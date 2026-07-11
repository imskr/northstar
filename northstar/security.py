from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

PASSWORD_N = 2**14
PASSWORD_R = 8
PASSWORD_P = 1
SESSION_DAYS = 30


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    pepper = os.getenv("PASSWORD_PEPPER", "").encode()
    digest = hashlib.scrypt(
        password.encode() + pepper,
        salt=salt,
        n=PASSWORD_N,
        r=PASSWORD_R,
        p=PASSWORD_P,
        dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        PASSWORD_N,
        PASSWORD_R,
        PASSWORD_P,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        def pad(value: str) -> str:
            return value + "=" * (-len(value) % 4)

        salt = base64.urlsafe_b64decode(pad(salt_b64))
        expected = base64.urlsafe_b64decode(pad(digest_b64))
        pepper = os.getenv("PASSWORD_PEPPER", "").encode()
        actual = hashlib.scrypt(
            password.encode() + pepper,
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def new_session_token() -> tuple[str, str, str]:
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = iso(utcnow() + timedelta(days=SESSION_DAYS))
    return raw, token_hash, expires


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
