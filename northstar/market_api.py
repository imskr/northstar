from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request

from .auth import login_required
from .etf_catalog import catalog_stats, resolve_symbol, search_catalog
from .market_provider import EUROPEAN_EXCHANGES, is_supported_symbol, normalize, normalize_symbol

bp = Blueprint("market_api", __name__, url_prefix="/api")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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
        return jsonify(
            {"error": "A maximum of 24 selected symbols can be synced at once."}
        ), 400
    invalid = [symbol for symbol in symbols if not is_supported_symbol(symbol)]
    if invalid:
        return jsonify(
            {"error": "Unsupported European exchange symbol.", "symbols": invalid}
        ), 400

    range_ = request.args.get("range", "5d")
    if range_ not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y"}:
        range_ = "5d"
    fresh = _truthy(request.args.get("fresh"))

    data: dict[str, dict] = {}
    errors: dict[str, str] = {}
    # Keep upstream traffic deliberately small. A fresh manual sync bypasses our
    # application cache, so a larger burst would make provider throttling worse.
    with ThreadPoolExecutor(max_workers=1 if fresh else min(2, len(symbols))) as pool:
        futures = {
            pool.submit(normalize, symbol, range_, force=fresh): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                data[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = str(exc)

    if not data:
        return jsonify(
            {
                "error": (
                    "A fresh market-price request could not be completed. "
                    "Your saved prices were left unchanged."
                    if fresh
                    else "Selected prices could not be loaded."
                ),
                "freshRequested": fresh,
                "errors": errors,
            }
        ), 503

    return jsonify(
        {
            "provider": "Selected European listings · EUR-normalised",
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "freshRequested": fresh,
            "data": data,
            "errors": errors,
        }
    )


@bp.get("/market/search")
@login_required
def market_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify(
            {
                "results": [],
                "exchanges": EUROPEAN_EXCHANGES,
                "catalog": catalog_stats(),
            }
        )
    try:
        results = search_catalog(
            query,
            exchange_suffix=request.args.get("exchange", ""),
            limit=min(request.args.get("limit", default=16, type=int) or 16, 30),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(
        {
            "results": results,
            "exchanges": EUROPEAN_EXCHANGES,
            "catalog": catalog_stats(),
        }
    )


@bp.get("/market/catalog/resolve")
@login_required
def market_catalog_resolve():
    try:
        result = resolve_symbol(request.args.get("symbol", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"result": result})
