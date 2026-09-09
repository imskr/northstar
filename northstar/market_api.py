from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request

from .auth import login_required
from .catalog import catalog_stats, list_catalog, resolve_symbol, search_catalog
from .market_provider import (
    EUROPEAN_EXCHANGES,
    is_supported_symbol,
    normalize,
    normalize_symbol,
)

bp = Blueprint("market_api", __name__, url_prefix="/api")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _fetch_many(
    symbols: list[str], range_: str, *, include_history: bool, fresh: bool
) -> tuple[dict[str, dict], dict[str, str]]:
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
    symbols = list(
        dict.fromkeys(
            normalize_symbol(value) for value in request.args.get("symbols", "").split(",") if value.strip()
        )
    )
    if not symbols:
        return jsonify({"error": "Add at least one symbol."}), 400
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

    data, errors = _fetch_many(
        symbols,
        range_,
        include_history=include_history,
        fresh=fresh,
    )

    if not data:
        message = (
            "No current price provider returned a usable quote. Open Settings → Technical diagnostics for the exact provider response."
            if not include_history
            else "Historical prices could not be loaded. Existing chart history was left unchanged."
        )
        return jsonify(
            {
                "error": message,
                "freshRequested": fresh,
                "mode": mode,
                "errors": errors,
            }
        ), 503

    providers = sorted({str(item.get("provider") or "Market provider") for item in data.values()})
    warnings: list[str] = []
    if errors:
        warnings.append(f"{len(errors)} symbol request(s) could not be refreshed.")

    if errors:
        diagnostic = " | ".join(f"{symbol}: {message}" for symbol, message in errors.items())
    else:
        diagnostic = "Yahoo Finance supplied the latest available quote."

    return jsonify(
        {
            "provider": " + ".join(providers),
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "freshRequested": fresh,
            "mode": mode,
            "realtime": False,
            "warnings": warnings,
            "diagnostic": diagnostic,
            "data": data,
            "errors": errors,
        }
    )


@bp.get("/market/status")
@login_required
def market_status():
    symbol = normalize_symbol(request.args.get("symbol", ""))
    if not symbol:
        return jsonify({"error": "Add a symbol to test."}), 400
    if not is_supported_symbol(symbol):
        return jsonify({"error": "Unsupported European exchange symbol."}), 400

    result: dict = {"ok": False}
    try:
        quote = normalize(symbol, "5d", force=True, include_history=False)
        result = {
            "ok": True,
            "provider": quote.get("provider"),
            "price": quote.get("price"),
            "marketTime": quote.get("marketTime"),
            "delayed": bool(quote.get("delayed")),
            "source": quote.get("source"),
            "providerErrors": quote.get("providerErrors") or [],
        }
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}

    ok = bool(result.get("ok"))
    return jsonify(
        {
            "ok": ok,
            "symbol": symbol,
            "message": (
                "Yahoo Finance returned a usable quote."
                if ok
                else "Neither Yahoo Finance nor the Stooq fallback returned a usable quote."
            ),
            "quote": result,
        }
    ), 200 if ok else 503


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


@bp.get("/market/catalog")
@login_required
def market_catalog():
    return jsonify({"instruments": list_catalog()})


@bp.get("/market/catalog/resolve")
@login_required
def market_catalog_resolve():
    try:
        result = resolve_symbol(request.args.get("symbol", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"result": result})
