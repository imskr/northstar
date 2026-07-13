from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .market_provider import EUROPEAN_EXCHANGES, exchange_for_symbol, is_supported_symbol, normalize_symbol

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "etf_catalog.json"
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict, ...]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    instruments = payload.get("instruments") or []
    result: list[dict] = []
    seen: set[str] = set()
    for raw in instruments:
        symbol = normalize_symbol(raw.get("symbol"))
        if not is_supported_symbol(symbol) or symbol in seen:
            continue
        suffix, exchange = exchange_for_symbol(symbol)
        item = {
            "symbol": symbol,
            "ticker": str(raw.get("ticker") or symbol.split(".")[0]).upper(),
            "name": str(raw.get("name") or symbol),
            "isin": str(raw.get("isin") or "").upper(),
            "exchange": str(raw.get("exchange") or exchange),
            "exchangeSuffix": str(raw.get("exchangeSuffix") or suffix),
            "nativeCurrency": str(raw.get("nativeCurrency") or ""),
            "issuer": str(raw.get("issuer") or ""),
            "assetClass": str(raw.get("assetClass") or "ETF"),
        }
        seen.add(symbol)
        result.append(item)
    return tuple(result)


def catalog_stats() -> dict:
    instruments = _catalog()
    return {
        "instruments": len(instruments),
        "exchanges": len({item["exchangeSuffix"] for item in instruments}),
        "supportedExchanges": len(EUROPEAN_EXCHANGES),
    }


def resolve_symbol(value: str) -> dict:
    symbol = normalize_symbol(value)
    for item in _catalog():
        if item["symbol"] == symbol:
            return dict(item)
    if not is_supported_symbol(symbol):
        raise ValueError("Enter a symbol with a supported European exchange suffix, for example VWCE.DE or VUSA.L.")
    suffix, exchange = exchange_for_symbol(symbol)
    ticker = symbol.rsplit(suffix, 1)[0] if suffix else symbol.split(".")[0]
    return {
        "symbol": symbol,
        "ticker": ticker,
        "name": f"{ticker} · custom European ETF listing",
        "isin": "",
        "exchange": exchange,
        "exchangeSuffix": suffix,
        "nativeCurrency": "",
        "issuer": "",
        "assetClass": "ETF",
        "custom": True,
    }


def search_catalog(query: str, exchange_suffix: str = "", limit: int = 16) -> list[dict]:
    query = str(query or "").strip()
    exchange_suffix = normalize_symbol(exchange_suffix)
    if exchange_suffix and exchange_suffix not in EUROPEAN_EXCHANGES:
        raise ValueError("Unsupported exchange filter.")
    if len(query) < 2:
        return []

    needle = query.casefold()
    compact = re.sub(r"[^a-z0-9]", "", needle)
    scored: list[tuple[int, str, dict]] = []
    for item in _catalog():
        if exchange_suffix and item["exchangeSuffix"] != exchange_suffix:
            continue
        fields = [item["symbol"], item["ticker"], item["name"], item["isin"], item["issuer"], item["assetClass"]]
        folded = [str(value or "").casefold() for value in fields]
        normalized = [re.sub(r"[^a-z0-9]", "", value) for value in folded]
        score = None
        if needle == folded[0] or needle == folded[1] or needle == folded[3]:
            score = 0
        elif any(value.startswith(needle) for value in folded if value):
            score = 1
        elif compact and any(value.startswith(compact) for value in normalized if value):
            score = 2
        elif any(needle in value for value in folded if value):
            score = 3
        elif compact and any(compact in value for value in normalized if value):
            score = 4
        if score is not None:
            scored.append((score, item["symbol"], item))

    scored.sort(key=lambda row: (row[0], row[1]))
    results = [dict(row[2]) for row in scored[: max(1, min(int(limit), 30))]]
    if not results:
        candidate = normalize_symbol(query)
        if is_supported_symbol(candidate):
            resolved = resolve_symbol(candidate)
            if not exchange_suffix or resolved["exchangeSuffix"] == exchange_suffix:
                results.append(resolved)
    return results
