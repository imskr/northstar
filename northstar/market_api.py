from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request

from .auth import login_required
from .etf_catalog import catalog_stats, resolve_symbol, search_catalog
from .market_provider import (
    EUROPEAN_EXCHANGES,
    is_supported_symbol,
    normalize,
    normalize_symbol,
    real_time_configured,
)

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
        return jsonify({"error": "A maximum of 24 selected symbols can be synced at once."}), 400

    invalid = [symbol for symbol in symbols if not is_supported_symbol(symbol)]
    if invalid:
        return jsonify({"error": "Unsupported European exchange symbol.", "symbols": invalid}), 400

    range_ = request.args.get("range", "5d")
    if range_ not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y"}:
        range_ = "5d"
    mode = request.args.get("mode", "history" if range_ != "5d" else "quote").strip().lower()
    include_history = mode == "history"
    fresh = _truthy(request.args.get("fresh")) and not include_history

    data: dict[str, dict] = {}
    errors: dict[str, str] = {}
    workers = 1 if fresh else min(2, len(symbols))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                normalize,
                symbol,
                range_,
                force=fresh,
                include_history=include_history,
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                data[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = str(exc)

    if not data:
        message = (
            "A current market-price request could not be completed. Your saved prices were left unchanged."
            if not include_history
            else "Historical prices could not be loaded. Existing chart history was left unchanged."
        )
        return jsonify({"error": message, "freshRequested": fresh, "mode": mode, "errors": errors}), 503

    providers = sorted({str(item.get("provider") or "Market provider") for item in data.values()})
    realtime = bool(data) and all(bool(item.get("realtime")) for item in data.values())
    warnings: list[str] = []
    if not include_history and not realtime:
        warnings.append(
            "Latest available quotes were synced, but they may be delayed. Configure TWELVE_DATA_API_KEY with EU real-time entitlement for real-time European prices."
        )
    if errors:
        warnings.append(f"{len(errors)} symbol request(s) could not be refreshed.")

    return jsonify(
        {
            "provider": " + ".join(providers),
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "freshRequested": fresh,
            "mode": mode,
            "realtime": realtime,
            "realTimeConfigured": real_time_configured(),
            "warnings": warnings,
            "data": data,
            "errors": errors,
        }
    )


@bp.get("/market/search")
@login_required
def market_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"results": [], "exchanges": EUROPEAN_EXCHANGES, "catalog": catalog_stats()})
    try:
        results = search_catalog(
            query,
            exchange_suffix=request.args.get("exchange", ""),
            limit=min(request.args.get("limit", default=16, type=int) or 16, 30),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"results": results, "exchanges": EUROPEAN_EXCHANGES, "catalog": catalog_stats()})


@bp.get("/market/catalog/resolve")
@login_required
def market_catalog_resolve():
    try:
        result = resolve_symbol(request.args.get("symbol", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"result": result})
