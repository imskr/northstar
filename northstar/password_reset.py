from __future__ import annotations

import os
import secrets
import smtplib
import time
from collections import defaultdict, deque
from datetime import timedelta
from email.message import EmailMessage

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import ForeignKey, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import SessionLocal
from .models import AuthSession, Base, User
from .security import hash_password, iso, token_hash, utcnow

bp = Blueprint("password_reset", __name__, url_prefix="/api/auth")
RESET_TOKEN_MINUTES = 30
_RESET_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    used_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


def _client_key() -> str:
    return request.headers.get(
        "X-Forwarded-For", request.remote_addr or "unknown"
    ).split(",")[0].strip()


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
    response = {
        "ok": True,
        "message": "If that email exists, a password reset link has been created.",
    }

    if not email:
        return jsonify(response)

    reset_url: str | None = None
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            now = utcnow()
            raw_token = secrets.token_urlsafe(32)
            db.execute(
                delete(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                )
            )
            db.add(
                PasswordResetToken(
                    token_hash=token_hash(raw_token),
                    user_id=user.id,
                    created_at=iso(now),
                    expires_at=iso(now + timedelta(minutes=RESET_TOKEN_MINUTES)),
                    used_at=None,
                )
            )
            db.commit()
            reset_url = f"{_base_url()}/?reset={raw_token}"

    if reset_url:
        delivered = False
        try:
            delivered = _send_reset_email(email, reset_url)
        except Exception:
            current_app.logger.exception("Password reset email delivery failed")

        if not delivered:
            current_app.logger.warning(
                "Password reset requested for %s: %s", email, reset_url
            )

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
    with SessionLocal() as db:
        reset = db.scalar(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash(raw_token),
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        if not reset:
            return jsonify({"error": "This reset link is invalid or has expired."}), 400

        user = db.get(User, reset.user_id)
        if not user:
            return jsonify({"error": "This reset link is invalid or has expired."}), 400

        user.password_hash = hash_password(password)
        reset.used_at = now
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        db.commit()

    return jsonify(
        {"ok": True, "message": "Password updated. Sign in with your new password."}
    )
