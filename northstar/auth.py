from __future__ import annotations

import os
import re
import time
import uuid
from collections import defaultdict, deque
from functools import wraps

from flask import Blueprint, g, jsonify, request
from sqlalchemy import delete, select

from .db import SessionLocal
from .models import AuthSession, PortfolioState, User
from .security import hash_password, iso, new_session_token, token_hash, utcnow, verify_password

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
COOKIE_NAME = "northstar_session"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _client_key() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _rate_limited() -> bool:
    key = _client_key()
    now = time.time()
    q = _LOGIN_ATTEMPTS[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 12:
        return True
    q.append(now)
    return False


def _secure_cookie() -> bool:
    return os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"} or request.is_secure


def _set_session_cookie(response, raw_token: str):
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=30 * 24 * 3600,
        httponly=True,
        secure=_secure_cookie(),
        samesite="Lax",
        path="/",
    )


def _clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/", samesite="Lax")


def _current_user():
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    hashed = token_hash(raw)
    now = iso(utcnow())
    with SessionLocal() as db:
        row = db.execute(
            select(User, AuthSession)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(AuthSession.token_hash == hashed, AuthSession.expires_at > now)
        ).first()
        return row[0] if row else None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user:
            return _json_error("Authentication required.", 401)
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


def _issue_session(db, user: User):
    raw, hashed, expires = new_session_token()
    db.add(
        AuthSession(
            token_hash=hashed,
            user_id=user.id,
            created_at=iso(utcnow()),
            expires_at=expires,
            user_agent=(request.headers.get("User-Agent") or "")[:500],
        )
    )
    return raw


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

    now = iso(utcnow())
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            return _json_error("An account with that email already exists.", 409)
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            display_name=name,
            password_hash=hash_password(password),
            created_at=now,
        )
        db.add(user)
        db.add(PortfolioState(user_id=user.id, state_json="{}", revision=0, updated_at=now))
        raw = _issue_session(db, user)
        db.commit()

    response = jsonify({"user": {"id": user.id, "email": user.email, "name": user.display_name}})
    _set_session_cookie(response, raw)
    return response, 201


@bp.post("/login")
def login():
    if _rate_limited():
        return _json_error("Too many authentication attempts. Try again in a minute.", 429)
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()[:320]
    password = str(body.get("password", ""))

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user or not verify_password(password, user.password_hash):
            return _json_error("Email or password is incorrect.", 401)
        raw = _issue_session(db, user)
        db.commit()

    response = jsonify({"user": {"id": user.id, "email": user.email, "name": user.display_name}})
    _set_session_cookie(response, raw)
    return response


@bp.post("/logout")
def logout():
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        with SessionLocal() as db:
            db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(raw)))
            db.commit()
    response = jsonify({"ok": True})
    _clear_session_cookie(response)
    return response


@bp.get("/me")
def me():
    user = _current_user()
    if not user:
        return _json_error("Not authenticated.", 401)
    return jsonify({"user": {"id": user.id, "email": user.email, "name": user.display_name}})
