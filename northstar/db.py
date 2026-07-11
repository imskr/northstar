from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import NoSuchModuleError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def get_engine():
    turso_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()

    if turso_url:
        if not turso_token:
            raise RuntimeError(
                "TURSO_DATABASE_URL is set but TURSO_AUTH_TOKEN is missing."
            )

        # Official remote-only Turso SQLAlchemy URL shape:
        # sqlite+libsql://<host>?secure=true
        url = turso_url if turso_url.startswith("sqlite+") else f"sqlite+{turso_url}"
        separator = "&" if "?" in url else "?"
        if "secure=" not in url:
            url = f"{url}{separator}secure=true"

        try:
            return create_engine(
                url,
                connect_args={"auth_token": turso_token},
                poolclass=NullPool,
                future=True,
            )
        except NoSuchModuleError as exc:
            raise RuntimeError(
                "Turso is configured, but sqlalchemy-libsql is not installed. "
                "Install deployment dependencies with: "
                "pip install -r requirements-turso.txt."
            ) from exc

    local_path = Path(os.getenv("NORTHSTAR_DB_PATH", ROOT / "data" / "northstar.db"))
    local_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{local_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        future=True,
    )


SessionLocal = sessionmaker(
    bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
)


def using_turso() -> bool:
    return bool(os.getenv("TURSO_DATABASE_URL", "").strip())
