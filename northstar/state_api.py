from __future__ import annotations

import copy
import json
import math
import re
import time

from flask import Blueprint, g, jsonify, request

from .auth import login_required
from .db import Statement, get_database, using_turso
from .security import iso, utcnow

bp = Blueprint("state_api", __name__, url_prefix="/api")
# Dynamic ETF asset IDs look like "etf_vwce_de" (prefix + lowercased symbol with underscores).
# Legacy IDs ("bcfp", "sec0", "emsm") are also lowercase alphanumeric.
# We validate the general shape rather than an exhaustive allow-list.
_ASSET_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}$")


def _finite(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _clean_trade(raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid transaction entry.")
    asset = str(raw.get("asset", "")).lower()
    trade_type = str(raw.get("type", "")).lower()
    shares = _finite(raw.get("shares"))
    price = _finite(raw.get("price"))
    fee = _finite(raw.get("fee"), 0.0)
    date = str(raw.get("date", ""))[:10]
    if not _ASSET_RE.match(asset) or trade_type not in {"buy", "sell"}:
        raise ValueError("Invalid transaction asset or type.")
    if not shares or shares <= 0 or not price or price <= 0 or fee is None or fee < 0:
        raise ValueError("Transaction shares, price, or fee is invalid.")
    if len(date) != 10:
        raise ValueError("Transaction date is invalid.")
    override = _finite(raw.get("realizedPnlOverride"))
    return {
        "id": str(raw.get("id") or f"trade-{int(time.time() * 1000)}")[:80],
        "asset": asset,
        "type": trade_type,
        "date": date,
        "shares": shares,
        "price": price,
        "fee": fee,
        "realizedPnlOverride": override,
        "estimated": bool(raw.get("estimated", False)),
        "createdAt": int(_finite(raw.get("createdAt"), int(time.time() * 1000))),
    }


def _trade_to_dict(row):
    output = {
        "id": row["trade_id"],
        "asset": row["asset"],
        "type": row["type"],
        "date": row["trade_date"],
        "shares": row["shares"],
        "price": row["price"],
        "fee": row["fee"],
        "estimated": bool(row["estimated"]),
        "createdAt": row["created_at"],
    }
    if row["realized_pnl_override"] is not None:
        output["realizedPnlOverride"] = row["realized_pnl_override"]
    return output


@bp.get("/state")
@login_required
def load_state():
    database = get_database()
    row = database.query_one(
        "SELECT state_json, revision, updated_at FROM portfolio_state WHERE user_id = ?",
        (g.user.id,),
    )
    base = json.loads(row["state_json"]) if row and row["state_json"] else {}
    trades = database.query(
        """
        SELECT trade_id, asset, type, trade_date, shares, price, fee,
               realized_pnl_override, estimated, created_at
        FROM trades
        WHERE user_id = ?
        ORDER BY trade_date, created_at
        """,
        (g.user.id,),
    ).rows
    base["transactions"] = [_trade_to_dict(trade) for trade in trades]
    return jsonify(
        {
            "state": base,
            "revision": row["revision"] if row else 0,
            "updatedAt": row["updated_at"] if row else None,
            "storage": "turso" if using_turso() else "sqlite",
        }
    )


@bp.put("/state")
@login_required
def save_state():
    body = request.get_json(silent=True) or {}
    incoming = body.get("state")
    if not isinstance(incoming, dict):
        return jsonify({"error": "A portfolio state object is required."}), 400
    if len(json.dumps(incoming)) > 3_000_000:
        return jsonify({"error": "Portfolio state is too large."}), 413
    try:
        trades = [_clean_trade(trade) for trade in incoming.get("transactions", [])]
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if len(trades) > 20_000:
        return jsonify({"error": "Transaction limit exceeded."}), 400

    state_without_trades = copy.deepcopy(incoming)
    state_without_trades.pop("transactions", None)
    market = state_without_trades.get("market")
    if isinstance(market, dict):
        for key in ("apiKey", "eodhdKey", "twelveKey"):
            market.pop(key, None)

    now = iso(utcnow())
    statements = [
        Statement(
            """
            INSERT INTO portfolio_state (user_id, state_json, revision, updated_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json = excluded.state_json,
                revision = portfolio_state.revision + 1,
                updated_at = excluded.updated_at
            RETURNING revision
            """,
            (g.user.id, json.dumps(state_without_trades, separators=(",", ":")), now),
            want_rows=True,
        ),
        Statement("DELETE FROM trades WHERE user_id = ?", (g.user.id,)),
    ]
    statements.extend(
        Statement(
            """
            INSERT INTO trades
                (trade_id, user_id, asset, type, trade_date, shares, price, fee,
                 realized_pnl_override, estimated, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["id"],
                g.user.id,
                trade["asset"],
                trade["type"],
                trade["date"],
                trade["shares"],
                trade["price"],
                trade["fee"],
                trade["realizedPnlOverride"],
                1 if trade["estimated"] else 0,
                trade["createdAt"],
            ),
        )
        for trade in trades
    )
    results = get_database().transaction(statements)
    revision = int(results[0].rows[0]["revision"])
    return jsonify({"ok": True, "revision": revision, "updatedAt": now})
