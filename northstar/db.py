from __future__ import annotations

import base64
import json
import os
import sqlite3
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_SECONDS = 12.0

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires_at ON auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS portfolio_state (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asset TEXT NOT NULL,
    type TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    shares REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    realized_pnl_override REAL,
    estimated INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    CONSTRAINT uq_trade_user_id UNIQUE(user_id, trade_id)
);
CREATE INDEX IF NOT EXISTS ix_trades_user_id ON trades(user_id);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user_id
    ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_expires_at
    ON password_reset_tokens(expires_at);
"""


class DatabaseError(RuntimeError):
    """Raised when a database request fails or returns an invalid response."""


@dataclass(frozen=True)
class Statement:
    sql: str
    params: Sequence[Any] = ()
    want_rows: bool = False


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    affected_rows: int = 0
    last_insert_rowid: int | None = None


class Database:
    def query(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        raise NotImplementedError

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        result = self.query(sql, params)
        return result.rows[0] if result.rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        raise NotImplementedError

    def transaction(self, statements: Iterable[Statement]) -> list[QueryResult]:
        raise NotImplementedError

    def initialize(self) -> None:
        raise NotImplementedError


class SQLiteDatabase(Database):
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _result(cursor: sqlite3.Cursor) -> QueryResult:
        rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
        rowid = cursor.lastrowid if cursor.lastrowid else None
        return QueryResult(rows=rows, affected_rows=max(cursor.rowcount, 0), last_insert_rowid=rowid)

    def query(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        try:
            with self._connect() as connection:
                return self._result(connection.execute(sql, tuple(params)))
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite query failed: {exc}") from exc

    def execute(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        try:
            with self._connect() as connection:
                cursor = connection.execute(sql, tuple(params))
                result = self._result(cursor)
                connection.commit()
                return result
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite write failed: {exc}") from exc

    def transaction(self, statements: Iterable[Statement]) -> list[QueryResult]:
        items = list(statements)
        if not items:
            return []
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                results: list[QueryResult] = []
                for item in items:
                    results.append(self._result(connection.execute(item.sql, tuple(item.params))))
                connection.commit()
                return results
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite transaction failed: {exc}") from exc

    def initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(SCHEMA_SQL)
                connection.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite schema initialization failed: {exc}") from exc


class TursoHttpDatabase(Database):
    def __init__(self, database_url: str, auth_token: str, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.endpoint = self._pipeline_url(database_url)
        self.auth_token = auth_token
        self.timeout = timeout

    @staticmethod
    def _pipeline_url(database_url: str) -> str:
        value = database_url.strip().rstrip("/")
        if value.startswith("libsql://"):
            value = "https://" + value.removeprefix("libsql://")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DatabaseError("TURSO_DATABASE_URL must start with libsql:// or https://")
        path = parsed.path.rstrip("/") + "/v2/pipeline"
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))

    @staticmethod
    def _encode_value(value: Any) -> dict[str, Any]:
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "integer", "value": "1" if value else "0"}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float):
            return {"type": "float", "value": value}
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
        return {"type": "text", "value": str(value)}

    @staticmethod
    def _decode_value(value: dict[str, Any]) -> Any:
        kind = value.get("type")
        if kind == "null":
            return None
        if kind == "integer":
            return int(value.get("value", 0))
        if kind == "float":
            return float(value.get("value", 0.0))
        if kind == "blob":
            return base64.b64decode(value.get("base64", ""))
        if kind == "text":
            return value.get("value", "")
        raise DatabaseError(f"Turso returned an unsupported value type: {kind!r}")

    def _statement(self, statement: Statement) -> dict[str, Any]:
        return {
            "sql": statement.sql,
            "args": [self._encode_value(value) for value in statement.params],
            "want_rows": statement.want_rows,
        }

    def _post(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps({"baton": None, "requests": requests}).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "northstar-portfolio/1.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise DatabaseError(f"Turso HTTP {exc.code}: {detail or exc.reason}") from exc
        except (URLError, socket.timeout, TimeoutError) as exc:
            raise DatabaseError(f"Turso request failed: {exc}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise DatabaseError(f"Turso returned an invalid response: {exc}") from exc

        results = payload.get("results")
        if not isinstance(results, list):
            raise DatabaseError("Turso response did not contain a results list")
        return payload

    @staticmethod
    def _stream_response(payload: dict[str, Any], index: int = 0) -> dict[str, Any]:
        results = payload.get("results", [])
        if index >= len(results):
            raise DatabaseError("Turso response was missing an expected result")
        item = results[index]
        if item.get("type") == "error":
            error = item.get("error") or {}
            raise DatabaseError(f"Turso query failed: {error.get('message', 'unknown error')}")
        response = item.get("response")
        if not isinstance(response, dict):
            raise DatabaseError("Turso response was missing a response object")
        return response

    def _parse_result(self, result: dict[str, Any]) -> QueryResult:
        columns = [column.get("name") or "" for column in result.get("cols", [])]
        rows: list[dict[str, Any]] = []
        for raw_row in result.get("rows", []):
            rows.append({name: self._decode_value(value) for name, value in zip(columns, raw_row, strict=False)})
        raw_rowid = result.get("last_insert_rowid")
        rowid = int(raw_rowid) if raw_rowid not in {None, ""} else None
        return QueryResult(
            rows=rows,
            affected_rows=int(result.get("affected_row_count", 0)),
            last_insert_rowid=rowid,
        )

    def query(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        payload = self._post(
            [
                {"type": "execute", "stmt": self._statement(Statement(sql, params, True))},
                {"type": "close"},
            ]
        )
        response = self._stream_response(payload)
        if response.get("type") != "execute":
            raise DatabaseError("Turso returned an unexpected query response")
        return self._parse_result(response.get("result") or {})

    def execute(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        payload = self._post(
            [
                {"type": "execute", "stmt": self._statement(Statement(sql, params, False))},
                {"type": "close"},
            ]
        )
        response = self._stream_response(payload)
        if response.get("type") != "execute":
            raise DatabaseError("Turso returned an unexpected write response")
        return self._parse_result(response.get("result") or {})

    def transaction(self, statements: Iterable[Statement]) -> list[QueryResult]:
        items = list(statements)
        if not items:
            return []

        steps: list[dict[str, Any]] = [
            {"stmt": self._statement(Statement("PRAGMA foreign_keys = ON"))},
            {
                "condition": {"type": "ok", "step": 0},
                "stmt": self._statement(Statement("BEGIN IMMEDIATE")),
            },
        ]
        data_step_indexes: list[int] = []
        previous_index = 1
        for item in items:
            step_index = len(steps)
            steps.append(
                {
                    "condition": {"type": "ok", "step": previous_index},
                    "stmt": self._statement(item),
                }
            )
            data_step_indexes.append(step_index)
            previous_index = step_index

        commit_index = len(steps)
        steps.append(
            {
                "condition": {"type": "ok", "step": previous_index},
                "stmt": self._statement(Statement("COMMIT")),
            }
        )
        steps.append(
            {
                "condition": {
                    "type": "not",
                    "cond": {"type": "ok", "step": commit_index},
                },
                "stmt": self._statement(Statement("ROLLBACK")),
            }
        )

        payload = self._post(
            [
                {"type": "batch", "batch": {"steps": steps}},
                {"type": "close"},
            ]
        )
        response = self._stream_response(payload)
        if response.get("type") != "batch":
            raise DatabaseError("Turso returned an unexpected transaction response")
        batch_result = response.get("result") or {}
        step_results = batch_result.get("step_results") or []
        step_errors = batch_result.get("step_errors") or []

        for index, error in enumerate(step_errors):
            if error is None:
                continue
            # A rollback can legitimately fail when BEGIN itself failed; the original
            # error is the useful one and will be found at an earlier index.
            if index == len(steps) - 1:
                continue
            raise DatabaseError(f"Turso transaction failed: {error.get('message', 'unknown error')}")

        if commit_index >= len(step_results) or step_results[commit_index] is None:
            raise DatabaseError("Turso transaction did not commit")

        results: list[QueryResult] = []
        for index in data_step_indexes:
            raw_result = step_results[index] if index < len(step_results) else None
            if raw_result is None:
                raise DatabaseError("Turso skipped a transaction statement")
            results.append(self._parse_result(raw_result))
        return results

    def initialize(self) -> None:
        payload = self._post(
            [
                {"type": "sequence", "sql": SCHEMA_SQL},
                {"type": "close"},
            ]
        )
        response = self._stream_response(payload)
        if response.get("type") != "sequence":
            raise DatabaseError("Turso returned an unexpected schema response")


@lru_cache(maxsize=1)
def get_database() -> Database:
    turso_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    if turso_url:
        if not turso_token:
            raise DatabaseError("TURSO_AUTH_TOKEN is required when TURSO_DATABASE_URL is set")
        timeout = float(os.getenv("DATABASE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        return TursoHttpDatabase(turso_url, turso_token, timeout=timeout)

    local_path = Path(os.getenv("NORTHSTAR_DB_PATH", ROOT / "data" / "northstar.db"))
    return SQLiteDatabase(local_path)


def initialize_database() -> None:
    get_database().initialize()


def using_turso() -> bool:
    return bool(os.getenv("TURSO_DATABASE_URL", "").strip())
