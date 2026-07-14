from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

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

TWELVE_EXCHANGES = {
    ".DE": "XETR",
    ".F": "XFRA",
    ".L": "XLON",
    ".PA": "XPAR",
    ".AS": "XAMS",
    ".BR": "XBRU",
    ".LS": "XLIS",
    ".MI": "XMIL",
    ".MC": "XMAD",
    ".SW": "XSWX",
    ".VI": "XWBO",
    ".IR": "XDUB",
    ".ST": "XSTO",
    ".CO": "XCSE",
    ".HE": "XHEL",
    ".OL": "XOSL",
    ".WA": "XWAR",
}

SUFFIX_CURRENCIES = {
    ".DE": "EUR", ".F": "EUR", ".BE": "EUR", ".DU": "EUR",
    ".HA": "EUR", ".HM": "EUR", ".MU": "EUR", ".SG": "EUR",
    ".TG": "EUR", ".LU": "EUR", ".PA": "EUR", ".AS": "EUR",
    ".BR": "EUR", ".LS": "EUR", ".MI": "EUR", ".MC": "EUR",
    ".VI": "EUR", ".IR": "EUR", ".L": "GBP", ".SW": "CHF",
    ".ST": "SEK", ".CO": "DKK", ".HE": "EUR", ".OL": "NOK",
    ".IC": "ISK", ".WA": "PLN", ".PR": "CZK", ".BD": "HUF",
    ".AT": "EUR", ".IS": "TRY", ".TL": "EUR", ".RG": "EUR",
    ".VS": "EUR", ".RO": "RON",
}

EUROPEAN_SUFFIXES = tuple(sorted(EUROPEAN_EXCHANGES, key=len, reverse=True))
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{1,30}$")
RANGES = {"5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y"}
RANGE_DAYS = {"5d": 14, "1mo": 45, "3mo": 120, "6mo": 220, "1y": 400, "2y": 800, "3y": 1200, "5y": 2000}

_CACHE: dict[tuple[str, ...], tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0

QUOTE_CACHE_SECONDS = max(15, int(os.getenv("MARKET_QUOTE_CACHE_SECONDS", "300")))
HISTORY_CACHE_SECONDS = max(300, int(os.getenv("MARKET_HISTORY_CACHE_SECONDS", "21600")))
STALE_CACHE_SECONDS = max(HISTORY_CACHE_SECONDS, int(os.getenv("MARKET_STALE_CACHE_SECONDS", "172800")))
MIN_REQUEST_INTERVAL = max(0.15, float(os.getenv("MARKET_MIN_REQUEST_INTERVAL", "0.45")))


class MarketRateLimited(RuntimeError):
    def __init__(self, retry_after: int | None = None):
        self.retry_after = retry_after
        detail = f" Retry in about {retry_after} seconds." if retry_after else " Retry in a few minutes."
        super().__init__("The market-data provider is temporarily rate-limited." + detail)


class MarketEntitlementError(RuntimeError):
    pass


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


def real_time_configured() -> bool:
    return bool(os.getenv("TWELVE_DATA_API_KEY", "").strip())


def _retry_after(headers) -> int | None:
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(raw)
            return max(1, int((target - datetime.now(target.tzinfo or UTC)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


def _throttle() -> None:
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        now = time.monotonic()
        wait = MIN_REQUEST_INTERVAL - (now - _LAST_REQUEST_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST_AT = time.monotonic()


def _request(url: str, *, accept: str, timeout: float = 10.0, fresh: bool = False) -> bytes:
    _throttle()
    if fresh:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}northstar_ts={time.time_ns()}"
    headers = {
        "Accept": accept,
        "Accept-Language": "en-GB,en;q=0.9",
        "User-Agent": "Northstar/24 (+personal portfolio dashboard)",
    }
    if fresh:
        headers.update({"Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"})
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 429:
            raise MarketRateLimited(_retry_after(exc.headers)) from exc
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Market service returned HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the market service: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Market service timed out.") from exc


def _fetch_json(url: str, *, timeout: float = 10.0, fresh: bool = False) -> dict:
    try:
        return json.loads(_request(url, accept="application/json,text/plain,*/*", timeout=timeout, fresh=fresh).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Market service returned invalid JSON.") from exc


def _fetch_text(url: str, *, timeout: float = 10.0, fresh: bool = False) -> str:
    return _request(url, accept="text/csv,text/plain,*/*", timeout=timeout, fresh=fresh).decode("utf-8", "replace")


def _cached(
    key: tuple[str, ...],
    loader,
    *,
    ttl: int,
    force: bool = False,
    allow_stale: bool = True,
) -> tuple[dict, str]:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if not force and hit and now - hit[0] < ttl:
        return hit[1], "cache"
    try:
        value = loader()
    except (MarketRateLimited, MarketEntitlementError, RuntimeError):
        if allow_stale and hit and now - hit[0] < STALE_CACHE_SECONDS:
            return hit[1], "stale"
        raise
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
    return value, "refreshed"


def _symbol_parts(symbol: str) -> tuple[str, str, str]:
    suffix, exchange_name = exchange_for_symbol(symbol)
    if not suffix:
        raise ValueError("Unsupported European exchange symbol.")
    return symbol[: -len(suffix)], suffix, exchange_name


def _twelve_error(data: dict) -> None:
    if str(data.get("status") or "").lower() != "error" and not data.get("code"):
        return
    message = str(data.get("message") or "Twelve Data rejected the request.")
    code = int(data.get("code") or 0)
    lowered = message.lower()
    if code == 429 or "credit" in lowered or "rate limit" in lowered:
        raise MarketRateLimited()
    if any(word in lowered for word in ("subscription", "plan", "not available", "not authorized", "permission")):
        raise MarketEntitlementError(message)
    raise RuntimeError(message)


def _twelve_params(symbol: str) -> dict[str, str]:
    ticker, suffix, _ = _symbol_parts(symbol)
    exchange = TWELVE_EXCHANGES.get(suffix)
    if not exchange:
        raise RuntimeError(f"Twelve Data exchange mapping is not configured for {suffix}.")
    return {"symbol": ticker, "exchange": exchange}


def _twelve_quote(symbol: str) -> dict:
    key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TWELVE_DATA_API_KEY is not configured.")
    params = {**_twelve_params(symbol), "apikey": key}
    data = _fetch_json(f"https://api.twelvedata.com/quote?{urlencode(params)}", fresh=True)
    _twelve_error(data)
    price = _finite(data.get("close")) or _finite(data.get("price"))
    if not price or price <= 0:
        raise RuntimeError(f"Twelve Data returned no usable quote for {symbol}.")
    previous = _finite(data.get("previous_close"))
    timestamp = data.get("timestamp")
    if timestamp is None:
        raw = str(data.get("datetime") or "")
        try:
            timestamp = int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except (ValueError, TypeError):
            timestamp = int(datetime.now(UTC).timestamp())
    return {
        "provider": "Twelve Data",
        "realtime": True,
        "delayed": False,
        "name": data.get("name") or symbol.split(".")[0],
        "currency": str(data.get("currency") or SUFFIX_CURRENCIES.get(_symbol_parts(symbol)[1], "EUR")),
        "price": price,
        "previous": previous,
        "timestamp": int(timestamp),
        "market_state": "REGULAR" if data.get("is_market_open") else "CLOSED",
        "history": [],
    }


def _twelve_history(symbol: str, range_: str) -> dict:
    key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TWELVE_DATA_API_KEY is not configured.")
    outputsize = min(5000, max(20, RANGE_DAYS.get(range_, 400)))
    params = {
        **_twelve_params(symbol),
        "interval": "1day",
        "outputsize": str(outputsize),
        "order": "ASC",
        "timezone": "Europe/Berlin",
        "apikey": key,
    }
    data = _fetch_json(f"https://api.twelvedata.com/time_series?{urlencode(params)}")
    _twelve_error(data)
    rows = []
    for row in data.get("values") or []:
        close = _finite(row.get("close"))
        day = str(row.get("datetime") or "")[:10]
        if close and close > 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            rows.append({"date": day, "close": close})
    rows.sort(key=lambda item: item["date"])
    if len(rows) < 2:
        raise RuntimeError(f"Twelve Data returned insufficient history for {symbol}.")
    meta = data.get("meta") or {}
    return {
        "provider": "Twelve Data",
        "realtime": False,
        "delayed": False,
        "name": meta.get("instrument_name") or symbol.split(".")[0],
        "currency": str(meta.get("currency") or SUFFIX_CURRENCIES.get(_symbol_parts(symbol)[1], "EUR")),
        "price": rows[-1]["close"],
        "previous": rows[-2]["close"],
        "timestamp": int(datetime.fromisoformat(rows[-1]["date"]).replace(tzinfo=UTC).timestamp()),
        "market_state": None,
        "history": rows,
    }


def _stooq_symbol(symbol: str) -> str:
    return symbol.lower()


def _stooq_quote(symbol: str) -> dict:
    encoded = quote(_stooq_symbol(symbol), safe=".-")
    text = _fetch_text(f"https://stooq.com/q/l/?s={encoded}&f=sd2t2ohlcvn&h&e=csv", fresh=True)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise RuntimeError(f"Stooq returned no quote for {symbol}.")
    row = rows[0]
    price = _finite(row.get("Close"))
    if not price or price <= 0:
        raise RuntimeError(f"Stooq returned no usable quote for {symbol}.")
    day = str(row.get("Date") or "").strip()
    clock = str(row.get("Time") or "").strip()
    timestamp = int(datetime.now(UTC).timestamp())
    for raw, fmt in ((f"{day} {clock}".strip(), "%Y-%m-%d %H:%M:%S"), (day, "%Y-%m-%d")):
        if not raw:
            continue
        try:
            timestamp = int(datetime.strptime(raw, fmt).replace(tzinfo=UTC).timestamp())
            break
        except ValueError:
            pass
    _, suffix, _ = _symbol_parts(symbol)
    return {
        "provider": "Stooq",
        "realtime": False,
        "delayed": True,
        "name": row.get("Name") or symbol.split(".")[0],
        "currency": SUFFIX_CURRENCIES.get(suffix, "EUR"),
        "price": price,
        "previous": None,
        "timestamp": timestamp,
        "market_state": None,
        "history": [{"date": datetime.fromtimestamp(timestamp, UTC).date().isoformat(), "close": price}],
    }


def _stooq_history(symbol: str, range_: str) -> dict:
    days = RANGE_DAYS.get(range_, 400)
    end = date.today()
    start = end - timedelta(days=days)
    params = urlencode({"s": _stooq_symbol(symbol), "d1": start.strftime("%Y%m%d"), "d2": end.strftime("%Y%m%d"), "i": "d"})
    text = _fetch_text(f"https://stooq.com/q/d/l/?{params}")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        close = _finite(row.get("Close"))
        day = str(row.get("Date") or "")[:10]
        if close and close > 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            rows.append({"date": day, "close": close})
    rows.sort(key=lambda item: item["date"])
    if len(rows) < 2:
        raise RuntimeError(f"Stooq returned insufficient history for {symbol}.")
    _, suffix, _ = _symbol_parts(symbol)
    return {
        "provider": "Stooq",
        "realtime": False,
        "delayed": True,
        "name": symbol.split(".")[0],
        "currency": SUFFIX_CURRENCIES.get(suffix, "EUR"),
        "price": rows[-1]["close"],
        "previous": rows[-2]["close"],
        "timestamp": int(datetime.fromisoformat(rows[-1]["date"]).replace(tzinfo=UTC).timestamp()),
        "market_state": None,
        "history": rows,
    }


def _yahoo_history(symbol: str, range_: str) -> dict:
    encoded = quote(symbol, safe="")
    params = urlencode({"range": range_, "interval": "1d", "events": "div,splits", "includePrePost": "false"})
    data = _fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{params}")
    chart = data.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(chart["error"].get("description") or str(chart["error"]))
    result = (chart.get("result") or [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo returned no history for {symbol}.")
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators") or {}).get("quote") or []
    closes = (quotes[0] if quotes else {}).get("close") or []
    rows = []
    for timestamp, value in zip(timestamps, closes):
        close = _finite(value)
        if close and close > 0:
            rows.append({"date": datetime.fromtimestamp(int(timestamp), UTC).date().isoformat(), "close": close})
    if len(rows) < 2:
        raise RuntimeError(f"Yahoo returned insufficient history for {symbol}.")
    return {
        "provider": "Yahoo Finance",
        "realtime": False,
        "delayed": True,
        "name": meta.get("longName") or meta.get("shortName") or symbol.split(".")[0],
        "currency": str(meta.get("currency") or SUFFIX_CURRENCIES.get(_symbol_parts(symbol)[1], "EUR")),
        "price": _finite(meta.get("regularMarketPrice")) or rows[-1]["close"],
        "previous": _finite(meta.get("chartPreviousClose")) or rows[-2]["close"],
        "timestamp": int(meta.get("regularMarketTime") or datetime.now(UTC).timestamp()),
        "market_state": meta.get("marketState"),
        "history": rows,
    }


def _load_quote(symbol: str) -> dict:
    errors: list[str] = []
    if real_time_configured():
        try:
            return _twelve_quote(symbol)
        except (MarketRateLimited, MarketEntitlementError, RuntimeError) as exc:
            errors.append(f"Twelve Data: {exc}")
    try:
        return _stooq_quote(symbol)
    except (MarketRateLimited, RuntimeError) as exc:
        errors.append(f"Stooq: {exc}")
    try:
        history = _yahoo_history(symbol, "5d")
        history["history"] = history["history"][-2:]
        return history
    except (MarketRateLimited, RuntimeError) as exc:
        errors.append(f"Yahoo: {exc}")
    raise RuntimeError(" | ".join(errors) or f"No quote provider returned {symbol}.")


def _load_history(symbol: str, range_: str) -> dict:
    errors: list[str] = []
    if real_time_configured():
        try:
            return _twelve_history(symbol, range_)
        except (MarketRateLimited, MarketEntitlementError, RuntimeError) as exc:
            errors.append(f"Twelve Data: {exc}")
    try:
        return _stooq_history(symbol, range_)
    except (MarketRateLimited, RuntimeError) as exc:
        errors.append(f"Stooq: {exc}")
    try:
        return _yahoo_history(symbol, range_)
    except (MarketRateLimited, RuntimeError) as exc:
        errors.append(f"Yahoo: {exc}")
    raise RuntimeError(" | ".join(errors) or f"No history provider returned {symbol}.")


def _currency_parts(raw_currency: str | None) -> tuple[str, float]:
    raw = str(raw_currency or "EUR").strip()
    if raw in {"GBp", "GBX", "GBx", "GBPENCE"}:
        return "GBP", 0.01
    return raw.upper(), 1.0


def _fx_to_eur(currency: str) -> tuple[float, str]:
    currency = currency.upper()
    if currency == "EUR":
        return 1.0, "native"
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise RuntimeError(f"Unsupported quote currency: {currency}")

    def loader() -> dict:
        if real_time_configured():
            key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
            data = _fetch_json(f"https://api.twelvedata.com/exchange_rate?{urlencode({'symbol': currency + '/EUR', 'apikey': key})}")
            _twelve_error(data)
            rate = _finite(data.get("rate"))
            if rate and rate > 0:
                return {"rate": rate, "provider": "Twelve Data"}
        pair = f"{currency}EUR=X"
        data = _yahoo_history(pair, "5d")
        return {"rate": data["price"], "provider": data["provider"]}

    payload, status = _cached(("fx", currency), loader, ttl=HISTORY_CACHE_SECONDS, allow_stale=True)
    return float(payload["rate"]), status


def normalize(
    symbol: str,
    range_: str = "5d",
    *,
    force: bool = False,
    include_history: bool | None = None,
) -> dict:
    symbol = normalize_symbol(symbol)
    if not is_supported_symbol(symbol):
        raise ValueError("Unsupported European exchange symbol.")
    if range_ not in RANGES:
        range_ = "5d"
    if include_history is None:
        include_history = range_ != "5d"

    kind = "history" if include_history else "quote"
    ttl = HISTORY_CACHE_SECONDS if include_history else QUOTE_CACHE_SECONDS
    payload, cache_status = _cached(
        (kind, symbol, range_),
        lambda: _load_history(symbol, range_) if include_history else _load_quote(symbol),
        ttl=ttl,
        force=force and not include_history,
        allow_stale=not force or include_history,
    )

    native_currency, unit_scale = _currency_parts(payload.get("currency"))
    fx, fx_status = _fx_to_eur(native_currency)
    multiplier = unit_scale * fx
    history = [
        {"date": row["date"], "close": float(row["close"]) * multiplier}
        for row in payload.get("history") or []
        if _finite(row.get("close")) and _finite(row.get("close")) > 0
    ]
    native_price = _finite(payload.get("price"))
    if native_price is None:
        if not history:
            raise RuntimeError(f"{symbol} returned no usable market price.")
        price_eur = history[-1]["close"]
        native_price = price_eur / multiplier
    else:
        price_eur = native_price * multiplier
    previous_native = _finite(payload.get("previous"))
    previous_eur = previous_native * multiplier if previous_native is not None else (history[-2]["close"] if len(history) > 1 else None)
    timestamp = int(payload.get("timestamp") or datetime.now(UTC).timestamp())
    suffix, exchange_name = exchange_for_symbol(symbol)
    provider = str(payload.get("provider") or "Market provider")
    delayed = bool(payload.get("delayed", True))
    stale = cache_status == "stale" or fx_status == "stale"
    source_bits = [provider, exchange_name, "EUR-normalised"]
    source_bits.append("real-time entitlement" if payload.get("realtime") else "latest available / may be delayed")
    if stale:
        source_bits.append("stale fallback")
    return {
        "symbol": symbol,
        "ticker": symbol.split(".")[0],
        "name": payload.get("name") or symbol.split(".")[0],
        "exchange": exchange_name,
        "exchangeSuffix": suffix,
        "currency": "EUR",
        "nativeCurrency": native_currency,
        "nativePrice": native_price * unit_scale,
        "fxToEur": fx,
        "price": price_eur,
        "lastTrade": price_eur,
        "previousClose": previous_eur,
        "marketTime": datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z"),
        "marketState": payload.get("market_state"),
        "provider": provider,
        "source": " · ".join(source_bits),
        "realtime": bool(payload.get("realtime")),
        "delayed": delayed,
        "stale": stale,
        "cacheStatus": cache_status,
        "fresh": cache_status == "refreshed",
        "history": history,
    }
