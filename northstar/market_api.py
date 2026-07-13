from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request

from .auth import login_required
from .market_provider import (
    EUROPEAN_EXCHANGES,
    is_supported_symbol,
    normalize,
    normalize_symbol,
    search_etfs,
)

bp = Blueprint("market_api", __name__, url_prefix="/api")


@bp.get("/market")
@login_required
def market():
    symbols = list(
        dict.fromkeys(
            normalize_symbol(value)
            for value in request.args.get("symbols", "").split(",")
            if value.strip()
        )
    )
    if not symbols:
        return jsonify({"error": "Add at least one ETF symbol."}), 400
    if len(symbols) > 24:
        return jsonify({"error": "A maximum of 24 symbols can be requested at once."}), 400
    invalid = [symbol for symbol in symbols if not is_supported_symbol(symbol)]
    if invalid:
        return jsonify({"error": "Unsupported European exchange symbol.", "symbols": invalid}), 400

    range_ = request.args.get("range", "5d")
    if range_ not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y"}:
        range_ = "5d"

    data: dict[str, dict] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as pool:
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
            "provider": "European exchanges · EUR-normalised",
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "data": data,
            "errors": errors,
        }
    )


@bp.get("/market/search")
@login_required
def market_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"results": [], "exchanges": EUROPEAN_EXCHANGES})
    try:
        results = search_etfs(
            query,
            exchange_suffix=request.args.get("exchange", ""),
            limit=min(request.args.get("limit", default=16, type=int) or 16, 30),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"ETF search failed: {exc}"}), 502
    return jsonify({"results": results, "exchanges": EUROPEAN_EXCHANGES})
