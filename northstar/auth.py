from __future__ import annotations

import os
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

from .db import DatabaseError, Statement, get_database
from .security import hash_password, iso, new_session_token, token_hash, utcnow, verify_password

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
COOKIE_NAME = "northstar_session"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    display_name: str


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _client_key() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _rate_limited() -> bool:
    key = _client_key()
    now = time.time()
    attempts = _LOGIN_ATTEMPTS[key]
    while attempts and now - attempts[0] > 60:
        attempts.popleft()
    if len(attempts) >= 12:
        return True
    attempts.append(now)
    return False


def _secure_cookie() -> bool:
    return os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"} or request.is_secure


def _set_session_cookie(response, raw_token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=30 * 24 * 3600,
        httponly=True,
        secure=_secure_cookie(),
        samesite="Lax",
        path="/",
    )


def _clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", samesite="Lax")


def _current_user() -> CurrentUser | None:
    raw_token = request.cookies.get(COOKIE_NAME)
    if not raw_token:
        return None
    row = get_database().query_one(
        """
        SELECT users.id, users.email, users.display_name
        FROM users
        JOIN auth_sessions ON auth_sessions.user_id = users.id
        WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?
        LIMIT 1
        """,
        (token_hash(raw_token), iso(utcnow())),
    )
    if not row:
        return None
    return CurrentUser(id=row["id"], email=row["email"], display_name=row["display_name"])


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user:
            return _json_error("Authentication required.", 401)
        g.user = user
        return function(*args, **kwargs)

    return wrapper


def _session_statement(user_id: str, hashed_token: str, expires_at: str) -> Statement:
    return Statement(
        """
        INSERT INTO auth_sessions
            (token_hash, user_id, created_at, expires_at, user_agent)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            hashed_token,
            user_id,
            iso(utcnow()),
            expires_at,
            (request.headers.get("User-Agent") or "")[:500],
        ),
    )


@bp.post("/register")
def register():
    if os.getenv("ALLOW_REGISTRATION", "true").lower() not in {"1", "true", "yes"}:
        return _json_error("Registration is disabled on this deployment.", 403)
    if _rate_limited():
        return _json_error("Too many authentication attempts. Try again in a minute.", 429)

    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()[:120]
    email = str(body.get("email", "")).strip().lower()[:320]
    password = str(body.get("password", ""))
    if len(name) < 2:
        return _json_error("Enter your name.", 400)
    if not EMAIL_RE.match(email):
        return _json_error("Enter a valid email address.", 400)
    if len(password) < 10 or len(password) > 256:
        return _json_error("Use a password between 10 and 256 characters.", 400)

    database = get_database()
    if database.query_one("SELECT 1 AS found FROM users WHERE email = ? LIMIT 1", (email,)):
        return _json_error("An account with that email already exists.", 409)

    now = iso(utcnow())
    user_id = str(uuid.uuid4())
    raw_token, hashed_token, expires_at = new_session_token()
    try:
        database.transaction(
            [
                Statement(
                    """
                    INSERT INTO users (id, email, display_name, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, email, name, hash_password(password), now),
                ),
                Statement(
                    """
                    INSERT INTO portfolio_state (user_id, state_json, revision, updated_at)
                    VALUES (?, '{}', 0, ?)
                    """,
                    (user_id, now),
                ),
                _session_statement(user_id, hashed_token, expires_at),
            ]
        )
    except DatabaseError as exc:
        if "unique" in str(exc).lower() or "constraint" in str(exc).lower():
            return _json_error("An account with that email already exists.", 409)
        current_app.logger.exception("Account creation failed")
        raise

    response = jsonify({"user": {"id": user_id, "email": email, "name": name}})
    _set_session_cookie(response, raw_token)
    return response, 201


@bp.post("/login")
def login():
    if _rate_limited():
        return _json_error("Too many authentication attempts. Try again in a minute.", 429)

    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()[:320]
    password = str(body.get("password", ""))
    row = get_database().query_one(
        "SELECT id, email, display_name, password_hash FROM users WHERE email = ? LIMIT 1",
        (email,),
    )
    if not row or not verify_password(password, row["password_hash"]):
        return _json_error("Email or password is incorrect.", 401)

    raw_token, hashed_token, expires_at = new_session_token()
    session_statement = _session_statement(row["id"], hashed_token, expires_at)
    get_database().execute(session_statement.sql, session_statement.params)
    response = jsonify(
        {"user": {"id": row["id"], "email": row["email"], "name": row["display_name"]}}
    )
    _set_session_cookie(response, raw_token)
    return response


@bp.post("/logout")
def logout():
    raw_token = request.cookies.get(COOKIE_NAME)
    if raw_token:
        get_database().execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(raw_token),))
    response = jsonify({"ok": True})
    _clear_session_cookie(response)
    return response


@bp.get("/me")
def me():
    user = _current_user()
    if not user:
        return _json_error("Not authenticated.", 401)
    return jsonify({"user": {"id": user.id, "email": user.email, "name": user.display_name}})
