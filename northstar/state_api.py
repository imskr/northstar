from __future__ import annotations

import copy
import json
import math
import time

from flask import Blueprint, g, jsonify, request
from sqlalchemy import delete, select

from .auth import login_required
from .db import SessionLocal, using_turso
from .models import PortfolioState, Trade
from .security import iso, utcnow

bp = Blueprint("state_api", __name__, url_prefix="/api")
ASSETS = {"bcfp", "sec0", "emsm"}


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
    if asset not in ASSETS or trade_type not in {"buy", "sell"}:
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


def _trade_to_dict(t: Trade):
    out = {
        "id": t.trade_id,
        "asset": t.asset,
        "type": t.type,
        "date": t.trade_date,
        "shares": t.shares,
        "price": t.price,
        "fee": t.fee,
        "estimated": bool(t.estimated),
        "createdAt": t.created_at,
    }
    if t.realized_pnl_override is not None:
        out["realizedPnlOverride"] = t.realized_pnl_override
    return out


@bp.get("/state")
@login_required
def load_state():
    with SessionLocal() as db:
        row = db.get(PortfolioState, g.user.id)
        base = json.loads(row.state_json) if row and row.state_json else {}
        trades = db.scalars(
            select(Trade).where(Trade.user_id == g.user.id).order_by(Trade.trade_date, Trade.created_at)
        ).all()
        base["transactions"] = [_trade_to_dict(t) for t in trades]
        return jsonify(
            {
                "state": base,
                "revision": row.revision if row else 0,
                "updatedAt": row.updated_at if row else None,
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
        trades = [_clean_trade(t) for t in incoming.get("transactions", [])]
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if len(trades) > 20_000:
        return jsonify({"error": "Transaction limit exceeded."}), 400

    state_without_trades = copy.deepcopy(incoming)
    state_without_trades.pop("transactions", None)
    # Never persist obsolete provider secrets from older browser builds.
    market = state_without_trades.get("market")
    if isinstance(market, dict):
        for key in ("apiKey", "eodhdKey", "twelveKey"):
            market.pop(key, None)

    now = iso(utcnow())
    with SessionLocal() as db:
        row = db.get(PortfolioState, g.user.id)
        if not row:
            row = PortfolioState(user_id=g.user.id, state_json="{}", revision=0, updated_at=now)
            db.add(row)
        row.state_json = json.dumps(state_without_trades, separators=(",", ":"))
        row.revision = int(row.revision or 0) + 1
        row.updated_at = now

        db.execute(delete(Trade).where(Trade.user_id == g.user.id))
        for t in trades:
            db.add(
                Trade(
                    trade_id=t["id"],
                    user_id=g.user.id,
                    asset=t["asset"],
                    type=t["type"],
                    trade_date=t["date"],
                    shares=t["shares"],
                    price=t["price"],
                    fee=t["fee"],
                    realized_pnl_override=t["realizedPnlOverride"],
                    estimated=1 if t["estimated"] else 0,
                    created_at=t["createdAt"],
                )
            )
        db.commit()
        return jsonify({"ok": True, "revision": row.revision, "updatedAt": now})
