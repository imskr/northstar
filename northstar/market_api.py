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
    normalize_history_batch,
    normalize_quote_batch,
    normalize_symbol,
    real_time_configured,
    twelve_diagnostics,
)

bp = Blueprint("market_api", __name__, url_prefix="/api")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _fallback_many(symbols: list[str], range_: str, *, include_history: bool, fresh: bool) -> tuple[dict[str, dict], dict[str, str]]:
    data: dict[str, dict] = {}
    errors: dict[str, str] = {}
    workers = 1 if fresh else min(2, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                normalize,
                symbol,
                range_,
                force=fresh,
                include_history=include_history,
                prefer_realtime=False,
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                data[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = str(exc)
    return data, errors


@bp.get("/market")
@login_required
def market():
    symbols = list(dict.fromkeys(normalize_symbol(value) for value in request.args.get("symbols", "").split(",") if value.strip()))
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
    live_issues: dict[str, str] = {}
    final_errors: dict[str, str] = {}
    configured = real_time_configured()

    # One exchange-qualified Twelve Data batch request is faster and more reliable than
    # issuing one request per ETF from a shared Render egress IP.
    if configured:
        try:
            if include_history:
                data, live_issues = normalize_history_batch(symbols, range_)
            else:
                data, live_issues = normalize_quote_batch(symbols)
        except Exception as exc:
            live_issues = {symbol: str(exc) for symbol in symbols}

    remaining = [symbol for symbol in symbols if symbol not in data]
    if remaining:
        fallback_data, fallback_errors = _fallback_many(
            remaining,
            range_,
            include_history=include_history,
            fresh=fresh,
        )
        data.update(fallback_data)
        final_errors.update(fallback_errors)

    if not data:
        message = (
            "No current price provider returned a usable quote. Open Settings → Technical diagnostics for the exact provider response."
            if not include_history
            else "Historical prices could not be loaded. Existing chart history was left unchanged."
        )
        return jsonify({
            "error": message,
            "freshRequested": fresh,
            "mode": mode,
            "realTimeConfigured": configured,
            "liveIssues": live_issues,
            "errors": final_errors,
        }), 503

    providers = sorted({str(item.get("provider") or "Market provider") for item in data.values()})
    realtime = bool(data) and all(bool(item.get("realtime")) for item in data.values())
    warnings: list[str] = []
    if not configured and not include_history:
        warnings.append("Render cannot see TWELVE_DATA_API_KEY; delayed providers were used.")
    elif live_issues and not include_history:
        details = "; ".join(f"{symbol}: {message}" for symbol, message in live_issues.items())
        warnings.append(f"Twelve Data live feed was unavailable ({details}). A delayed fallback was used where possible.")
    elif not include_history and not realtime:
        warnings.append("Latest available quotes were synced, but the selected feed is delayed.")
    if final_errors:
        warnings.append(f"{len(final_errors)} symbol request(s) could not be refreshed.")

    diagnostic_parts = []
    if live_issues:
        diagnostic_parts.append("Twelve Data: " + " | ".join(f"{symbol}: {message}" for symbol, message in live_issues.items()))
    if final_errors:
        diagnostic_parts.append("Fallbacks: " + " | ".join(f"{symbol}: {message}" for symbol, message in final_errors.items()))
    if not diagnostic_parts:
        diagnostic_parts.append("Live provider request completed successfully." if realtime else "A delayed provider supplied the latest available quote.")

    return jsonify({
        "provider": " + ".join(providers),
        "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "freshRequested": fresh,
        "mode": mode,
        "realtime": realtime,
        "realTimeConfigured": configured,
        "warnings": warnings,
        "diagnostic": " ".join(diagnostic_parts),
        "data": data,
        "liveIssues": live_issues,
        "errors": final_errors,
    })


@bp.get("/market/status")
@login_required
def market_status():
    symbol = normalize_symbol(request.args.get("symbol", ""))
    if not symbol:
        return jsonify({"error": "Add a symbol to test."}), 400
    if not is_supported_symbol(symbol):
        return jsonify({"error": "Unsupported European exchange symbol."}), 400
    result = twelve_diagnostics(symbol)
    return jsonify(result), 200 if result.get("ok") else 503


@bp.get("/market/search")
@login_required
def market_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"results": [], "exchanges": EUROPEAN_EXCHANGES, "catalog": catalog_stats()})
    try:
        results = search_catalog(query, exchange_suffix=request.args.get("exchange", ""), limit=min(request.args.get("limit", default=16, type=int) or 16, 30))
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
