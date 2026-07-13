from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

# Yahoo-style exchange suffixes for the principal European listing venues.
# The suffix is part of the market symbol, for example SXR8.DE or VUSA.L.
EUROPEAN_EXCHANGES = {
    ".DE": "Xetra",
    ".F": "Frankfurt",
    ".BE": "Berlin Stock Exchange",
    ".DU": "Dusseldorf Stock Exchange",
    ".HA": "Hanover Stock Exchange",
    ".HM": "Hamburg Stock Exchange",
    ".MU": "Munich Stock Exchange / gettex",
    ".SG": "Stuttgart Stock Exchange",
    ".TG": "Tradegate Exchange",
    ".LU": "Luxembourg Stock Exchange",
    ".L": "London Stock Exchange",
    ".PA": "Euronext Paris",
    ".AS": "Euronext Amsterdam",
    ".BR": "Euronext Brussels",
    ".LS": "Euronext Lisbon",
    ".MI": "Borsa Italiana",
    ".MC": "Bolsa de Madrid",
    ".SW": "SIX Swiss Exchange",
    ".VI": "Vienna Stock Exchange",
    ".IR": "Euronext Dublin",
    ".ST": "Nasdaq Stockholm",
    ".CO": "Nasdaq Copenhagen",
    ".HE": "Nasdaq Helsinki",
    ".OL": "Oslo Bors",
    ".IC": "Nasdaq Iceland",
    ".WA": "Warsaw Stock Exchange",
    ".PR": "Prague Stock Exchange",
    ".BD": "Budapest Stock Exchange",
    ".AT": "Athens Exchange",
    ".IS": "Borsa Istanbul",
    ".TL": "Nasdaq Tallinn",
    ".RG": "Nasdaq Riga",
    ".VS": "Nasdaq Vilnius",
    ".RO": "Bucharest Stock Exchange",
}

EUROPEAN_SUFFIXES = tuple(sorted(EUROPEAN_EXCHANGES, key=len, reverse=True))
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{1,30}$")
RANGES = {"5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y"}
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_SECONDS = 45


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def normalize_symbol(value: str) -> str:
    return str(value or "").strip().upper()


def exchange_for_symbol(symbol: str) -> tuple[str, str] | tuple[None, None]:
    normalized = normalize_symbol(symbol)
    for suffix in EUROPEAN_SUFFIXES:
        if normalized.endswith(suffix):
            return suffix, EUROPEAN_EXCHANGES[suffix]
    return None, None


def is_supported_symbol(symbol: str) -> bool:
    normalized = normalize_symbol(symbol)
    return bool(SYMBOL_RE.fullmatch(normalized) and exchange_for_symbol(normalized)[0])


def _fetch_json(url: str, *, timeout: float = 9.0) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-GB,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/126 Safari/537.36 Northstar/15"
            ),
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Market service returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the market service: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Market service timed out.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Market service returned invalid JSON.") from exc


def _cached_json(key: tuple[str, str], loader) -> dict:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = loader()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def _chart_payload(symbol: str, range_: str) -> dict:
    encoded = quote(symbol, safe="")
    params = urlencode(
        {
            "range": range_,
            "interval": "1d",
            "events": "div,splits",
            "includePrePost": "false",
        }
    )
    errors: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded}?{params}"
        try:
            data = _fetch_json(url)
            chart = data.get("chart") or {}
            if chart.get("error"):
                description = chart["error"].get("description") or str(chart["error"])
                raise RuntimeError(description)
            results = chart.get("result") or []
            if not results:
                raise RuntimeError("No quote was returned.")
            return results[0]
        except Exception as exc:  # retry the second public host
            errors.append(str(exc))
    raise RuntimeError(" | ".join(errors[-2:]) or "No quote was returned.")


def _currency_parts(raw_currency: str | None) -> tuple[str, float]:
    raw = str(raw_currency or "EUR").strip()
    if raw in {"GBp", "GBX", "GBx", "GBPENCE"}:
        # Yahoo commonly reports London-listed funds in pence.
        return "GBP", 0.01
    if raw.upper() == "GBP":
        return "GBP", 1.0
    return raw.upper(), 1.0


def _fx_to_eur(currency: str) -> float:
    currency = currency.upper()
    if currency == "EUR":
        return 1.0
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise RuntimeError(f"Unsupported quote currency: {currency}")
    pair = f"{currency}EUR=X"
    payload = _cached_json((pair, "5d"), lambda: _chart_payload(pair, "5d"))
    meta = payload.get("meta") or {}
    price = _finite(meta.get("regularMarketPrice"))
    if not price:
        closes = ((payload.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        price = next((_finite(value) for value in reversed(closes) if _finite(value)), None)
    if not price or price <= 0:
        raise RuntimeError(f"Could not convert {currency} prices to EUR.")
    return price


def normalize(symbol: str, range_: str = "5d") -> dict:
    symbol = normalize_symbol(symbol)
    if not is_supported_symbol(symbol):
        raise ValueError("Unsupported European exchange symbol.")
    if range_ not in RANGES:
        range_ = "5d"

    payload = _cached_json((symbol, range_), lambda: _chart_payload(symbol, range_))
    meta = payload.get("meta") or {}
    timestamps = payload.get("timestamp") or []
    quote_rows = (payload.get("indicators") or {}).get("quote") or []
    closes = (quote_rows[0] if quote_rows else {}).get("close") or []

    native_currency, unit_scale = _currency_parts(meta.get("currency"))
    fx = _fx_to_eur(native_currency)
    multiplier = unit_scale * fx

    history: list[dict] = []
    for timestamp, close in zip(timestamps, closes):
        value = _finite(close)
        if value is None or value <= 0:
            continue
        history.append(
            {
                "date": datetime.fromtimestamp(int(timestamp), UTC).date().isoformat(),
                "close": value * multiplier,
            }
        )

    native_price = _finite(meta.get("regularMarketPrice"))
    if native_price is None and history:
        price_eur = history[-1]["close"]
        native_price = price_eur / multiplier
    elif native_price is not None:
        price_eur = native_price * multiplier
    else:
        raise RuntimeError(f"{symbol} returned no usable market price.")

    previous_native = _finite(meta.get("chartPreviousClose")) or _finite(meta.get("previousClose"))
    if previous_native is not None:
        previous_eur = previous_native * multiplier
    elif len(history) > 1:
        previous_eur = history[-2]["close"]
    else:
        previous_eur = None

    market_timestamp = meta.get("regularMarketTime")
    market_time = (
        datetime.fromtimestamp(int(market_timestamp), UTC).isoformat().replace("+00:00", "Z")
        if market_timestamp
        else datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    suffix, exchange_name = exchange_for_symbol(symbol)
    long_name = meta.get("longName") or meta.get("shortName") or symbol.split(".")[0]

    return {
        "symbol": symbol,
        "ticker": symbol.split(".")[0],
        "name": long_name,
        "exchange": exchange_name,
        "exchangeSuffix": suffix,
        "currency": "EUR",
        "nativeCurrency": native_currency,
        "nativePrice": native_price * unit_scale,
        "fxToEur": fx,
        "price": price_eur,
        "lastTrade": price_eur,
        "previousClose": previous_eur,
        "marketTime": market_time,
        "marketState": meta.get("marketState"),
        "source": f"European exchange data · {exchange_name} · EUR-normalised",
        "history": history,
    }


def search_etfs(query: str, exchange_suffix: str = "", limit: int = 16) -> list[dict]:
    query = str(query or "").strip()
    if len(query) < 2:
        return []
    exchange_suffix = normalize_symbol(exchange_suffix)
    if exchange_suffix and exchange_suffix not in EUROPEAN_EXCHANGES:
        raise ValueError("Unsupported exchange filter.")

    params = urlencode(
        {
            "q": query[:80],
            "quotesCount": 50,
            "newsCount": 0,
            "enableFuzzyQuery": "true",
            "quotesQueryId": "tss_match_phrase_query",
        }
    )
    errors: list[str] = []
    data = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            data = _fetch_json(f"https://{host}/v1/finance/search?{params}")
            break
        except Exception as exc:
            errors.append(str(exc))
    if data is None:
        raise RuntimeError(" | ".join(errors[-2:]) or "ETF search failed.")

    results: list[dict] = []
    seen: set[str] = set()
    for item in data.get("quotes") or []:
        symbol = normalize_symbol(item.get("symbol"))
        suffix, exchange_name = exchange_for_symbol(symbol)
        quote_type = str(item.get("quoteType") or item.get("typeDisp") or "").upper()
        if quote_type != "ETF" or not suffix or not is_supported_symbol(symbol):
            continue
        if exchange_suffix and suffix != exchange_suffix:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        results.append(
            {
                "symbol": symbol,
                "ticker": symbol.split(".")[0],
                "name": item.get("longname") or item.get("shortname") or symbol,
                "exchange": exchange_name,
                "exchangeSuffix": suffix,
                "nativeCurrency": item.get("currency") or "",
            }
        )
        if len(results) >= max(1, min(int(limit), 30)):
            break
    return results
