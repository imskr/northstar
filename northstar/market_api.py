from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request

from .auth import login_required
from .market_provider import ALLOWED, normalize

bp = Blueprint("market_api", __name__, url_prefix="/api")


@bp.get("/market")
@login_required
def market():
    symbols = list(
        dict.fromkeys(
            s.strip().upper()
            for s in request.args.get("symbols", "").split(",")
            if s.strip()
        )
    )
    if not symbols or any(s not in ALLOWED for s in symbols):
        return jsonify({"error": "Invalid or unsupported symbols."}), 400
    range_ = request.args.get("range", "5d")
    if range_ not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y"}:
        range_ = "5d"

    data, errors = {}, {}
    with ThreadPoolExecutor(max_workers=min(5, len(symbols))) as pool:
        futures = {pool.submit(normalize, symbol, range_): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                data[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = str(exc)
    if not data:
        return jsonify({"error": "All market-data requests failed.", "errors": errors}), 502
    return jsonify(
        {
            "provider": "Deutsche Börse signed Xetra stream + Yahoo history",
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "data": data,
            "errors": errors,
        }
    )
