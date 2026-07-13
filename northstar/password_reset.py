from __future__ import annotations

import os
import secrets
import smtplib
import time
from collections import defaultdict, deque
from datetime import timedelta
from email.message import EmailMessage

from flask import Blueprint, current_app, jsonify, request

from .db import Statement, get_database
from .security import hash_password, iso, token_hash, utcnow

bp = Blueprint("password_reset", __name__, url_prefix="/api/auth")
RESET_TOKEN_MINUTES = 30
_RESET_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


def _client_key() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _rate_limited() -> bool:
    key = _client_key()
    now = time.time()
    attempts = _RESET_ATTEMPTS[key]
    while attempts and now - attempts[0] > 60:
        attempts.popleft()
    if len(attempts) >= 6:
        return True
    attempts.append(now)
    return False


def _base_url() -> str:
    configured = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    return configured or request.url_root.rstrip("/")


def _send_reset_email(recipient: str, reset_url: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "Northstar <no-reply@localhost>")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
    message = EmailMessage()
    message["Subject"] = "Reset your Northstar password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your Northstar account.\n\n"
        f"Open this link within {RESET_TOKEN_MINUTES} minutes:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(host, port, timeout=15) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
    return True


@bp.post("/forgot-password")
def forgot_password():
    if _rate_limited():
        return jsonify({"error": "Too many reset attempts. Try again in a minute."}), 429
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()[:320]
    response = {"ok": True, "message": "If that email exists, a password reset link has been created."}
    if not email:
        return jsonify(response)

    user = get_database().query_one("SELECT id FROM users WHERE email = ? LIMIT 1", (email,))
    reset_url: str | None = None
    if user:
        now = utcnow()
        raw_token = secrets.token_urlsafe(32)
        get_database().transaction(
            [
                Statement(
                    "DELETE FROM password_reset_tokens WHERE user_id = ? AND used_at IS NULL",
                    (user["id"],),
                ),
                Statement(
                    """
                    INSERT INTO password_reset_tokens
                        (token_hash, user_id, created_at, expires_at, used_at)
                    VALUES (?, ?, ?, ?, NULL)
                    """,
                    (
                        token_hash(raw_token),
                        user["id"],
                        iso(now),
                        iso(now + timedelta(minutes=RESET_TOKEN_MINUTES)),
                    ),
                ),
            ]
        )
        reset_url = f"{_base_url()}/?reset={raw_token}"

    if reset_url:
        delivered = False
        try:
            delivered = _send_reset_email(email, reset_url)
        except Exception:
            current_app.logger.exception("Password reset email delivery failed")
        if not delivered:
            current_app.logger.warning("Password reset requested for %s: %s", email, reset_url)
        if os.getenv("PASSWORD_RESET_DEBUG", "").lower() in {"1", "true", "yes"}:
            response["debug_reset_url"] = reset_url
    return jsonify(response)


@bp.post("/reset-password")
def reset_password():
    if _rate_limited():
        return jsonify({"error": "Too many reset attempts. Try again in a minute."}), 429
    body = request.get_json(silent=True) or {}
    raw_token = str(body.get("token", "")).strip()
    password = str(body.get("password", ""))
    if not raw_token:
        return jsonify({"error": "Reset token is missing."}), 400
    if len(password) < 10 or len(password) > 256:
        return jsonify({"error": "Use a password between 10 and 256 characters."}), 400

    now = iso(utcnow())
    reset = get_database().query_one(
        """
        SELECT token_hash, user_id
        FROM password_reset_tokens
        WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
        LIMIT 1
        """,
        (token_hash(raw_token), now),
    )
    if not reset:
        return jsonify({"error": "This reset link is invalid or has expired."}), 400

    get_database().transaction(
        [
            Statement("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), reset["user_id"])),
            Statement("UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?", (now, reset["token_hash"])),
            Statement("DELETE FROM auth_sessions WHERE user_id = ?", (reset["user_id"],)),
        ]
    )
    return jsonify({"ok": True, "message": "Password updated. Sign in with your new password."})
