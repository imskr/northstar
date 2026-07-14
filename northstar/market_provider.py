from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

EUROPEAN_EXCHANGES = {
    ".DE": "Xetra", ".F": "Frankfurt", ".BE": "Berlin Stock Exchange",
    ".DU": "Dusseldorf Stock Exchange", ".HA": "Hanover Stock Exchange",
    ".HM": "Hamburg Stock Exchange", ".MU": "Munich Stock Exchange / gettex",
    ".SG": "Stuttgart Stock Exchange", ".TG": "Tradegate Exchange",
    ".LU": "Luxembourg Stock Exchange", ".L": "London Stock Exchange",
    ".PA": "Euronext Paris", ".AS": "Euronext Amsterdam",
    ".BR": "Euronext Brussels", ".LS": "Euronext Lisbon",
    ".MI": "Borsa Italiana", ".MC": "Bolsa de Madrid",
    ".SW": "SIX Swiss Exchange", ".VI": "Vienna Stock Exchange",
    ".IR": "Euronext Dublin", ".ST": "Nasdaq Stockholm",
    ".CO": "Nasdaq Copenhagen", ".HE": "Nasdaq Helsinki",
    ".OL": "Oslo Bors", ".IC": "Nasdaq Iceland",
    ".WA": "Warsaw Stock Exchange", ".PR": "Prague Stock Exchange",
    ".BD": "Budapest Stock Exchange", ".AT": "Athens Exchange",
    ".IS": "Borsa Istanbul", ".TL": "Nasdaq Tallinn",
    ".RG": "Nasdaq Riga", ".VS": "Nasdaq Vilnius",
    ".RO": "Bucharest Stock Exchange",
}
EUROPEAN_SUFFIXES = tuple(sorted(EUROPEAN_EXCHANGES, key=len, reverse=True))
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{1,30}$")
RANGES = {"5d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y"}

_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
QUOTE_CACHE_SECONDS = max(60, int(os.getenv("MARKET_QUOTE_CACHE_SECONDS", "900")))
HISTORY_CACHE_SECONDS = max(300, int(os.getenv("MARKET_HISTORY_CACHE_SECONDS", "21600")))
STALE_CACHE_SECONDS = max(HISTORY_CACHE_SECONDS, int(os.getenv("MARKET_STALE_CACHE_SECONDS", "86400")))
MIN_REQUEST_INTERVAL = max(0.15, float(os.getenv("MARKET_MIN_REQUEST_INTERVAL", "0.55")))


class MarketRateLimited(RuntimeError):
    def __init__(self, retry_after: int | None = None):
        self.retry_after = retry_after
        detail = (
            f" Try again in about {retry_after} seconds."
            if retry_after
            else " Try again in a few minutes."
        )
        super().__init__("The market-data provider is temporarily rate-limited." + detail)


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


def _retry_after(headers) -> int | None:
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(raw)
            return max(
                1,
                int((target - datetime.now(target.tzinfo or UTC)).total_seconds()),
            )
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


def _fetch_json(url: str, *, timeout: float = 9.0, fresh: bool = False) -> dict:
    _throttle()
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-GB,en;q=0.9",
        "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/126 Safari/537.36 Northstar/22",
    }
    if fresh:
        headers.update(
            {
                "Cache-Control": "no-cache, no-store, max-age=0",
                "Pragma": "no-cache",
            }
        )
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise MarketRateLimited(_retry_after(exc.headers)) from exc
        body = exc.read().decode("utf-8", "replace")[:240]
        raise RuntimeError(
            f"Market service returned HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the market service: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Market service timed out.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Market service returned invalid JSON.") from exc
def _cached_payload(
    key: tuple[str, str],
    loader,
    *,
    ttl: int,
    force: bool = False,
    allow_stale: bool = True,
) -> tuple[dict, str]:
    """Return payload and cache status: cache, refreshed, or stale.

    A user-initiated refresh sets ``force=True``. It bypasses a fresh cache entry and,
    importantly, does not pretend a stale fallback was a successful refresh.
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if not force and hit and now - hit[0] < ttl:
            return hit[1], "cache"

    try:
        value = loader()
    except (MarketRateLimited, RuntimeError):
        if allow_stale:
            with _CACHE_LOCK:
                stale = _CACHE.get(key)
            if stale and now - stale[0] < STALE_CACHE_SECONDS:
                return stale[1], "stale"
        raise

    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
    return value, "refreshed"


def _chart_payload(
    symbol: str,
    range_: str,
    *,
    interval: str = "1d",
    fresh: bool = False,
) -> dict:
    encoded = quote(symbol, safe="")
    params = {
        "range": range_,
        "interval": interval,
        "events": "div,splits",
        "includePrePost": "false",
    }
    if fresh:
        params["northstar_ts"] = str(int(time.time() * 1000))
    query = urlencode(params)
    hosts = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
    if sum(ord(char) for char in symbol) % 2:
        hosts = tuple(reversed(hosts))
    errors: list[str] = []
    for host in hosts:
        try:
            data = _fetch_json(
                f"https://{host}/v8/finance/chart/{encoded}?{query}",
                fresh=fresh,
            )
            chart = data.get("chart") or {}
            if chart.get("error"):
                raise RuntimeError(
                    chart["error"].get("description") or str(chart["error"])
                )
            results = chart.get("result") or []
            if not results:
                raise RuntimeError("No quote was returned.")
            return results[0]
        except MarketRateLimited:
            raise
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(" | ".join(errors[-2:]) or "No quote was returned.")
def _currency_parts(raw_currency: str | None) -> tuple[str, float]:
    raw = str(raw_currency or "EUR").strip()
    if raw in {"GBp", "GBX", "GBx", "GBPENCE"}:
        return "GBP", 0.01
    return raw.upper(), 1.0


def _fx_to_eur(currency: str, *, force: bool = False) -> tuple[float, str]:
    currency = currency.upper()
    if currency == "EUR":
        return 1.0, "native"
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise RuntimeError(f"Unsupported quote currency: {currency}")
    pair = f"{currency}EUR=X"
    payload, status = _cached_payload(
        (pair, "5d"),
        lambda: _chart_payload(pair, "5d", fresh=force),
        ttl=HISTORY_CACHE_SECONDS,
        force=force,
        allow_stale=not force,
    )
    meta = payload.get("meta") or {}
    price = _finite(meta.get("regularMarketPrice"))
    if not price:
        closes = (
            ((payload.get("indicators") or {}).get("quote") or [{}])[0].get("close")
            or []
        )
        price = next(
            (_finite(value) for value in reversed(closes) if _finite(value)),
            None,
        )
    if not price or price <= 0:
        raise RuntimeError(f"Could not convert {currency} prices to EUR.")
    return price, status
def normalize(symbol: str, range_: str = "5d", *, force: bool = False) -> dict:
    symbol = normalize_symbol(symbol)
    if not is_supported_symbol(symbol):
        raise ValueError("Unsupported European exchange symbol.")
    if range_ not in RANGES:
        range_ = "5d"

    intraday_refresh = force and range_ == "5d"
    provider_range = "1d" if intraday_refresh else range_
    interval = "1m" if intraday_refresh else "1d"
    ttl = QUOTE_CACHE_SECONDS if range_ == "5d" else HISTORY_CACHE_SECONDS
    cache_key = (symbol, f"{provider_range}:{interval}")
    payload, quote_status = _cached_payload(
        cache_key,
        lambda: _chart_payload(
            symbol,
            provider_range,
            interval=interval,
            fresh=force,
        ),
        ttl=ttl,
        force=force,
        allow_stale=not force,
    )

    meta = payload.get("meta") or {}
    timestamps = payload.get("timestamp") or []
    quote_rows = (payload.get("indicators") or {}).get("quote") or []
    closes = (quote_rows[0] if quote_rows else {}).get("close") or []
    native_currency, unit_scale = _currency_parts(meta.get("currency"))
    fx, fx_status = _fx_to_eur(native_currency, force=force)
    multiplier = unit_scale * fx

    native_closes = [_finite(value) for value in closes]
    latest_native_close = next(
        (value for value in reversed(native_closes) if value and value > 0),
        None,
    )
    history_by_date: dict[str, float] = {}
    for timestamp, close in zip(timestamps, native_closes):
        if close is None or close <= 0:
            continue
        day = datetime.fromtimestamp(int(timestamp), UTC).date().isoformat()
        history_by_date[day] = close * multiplier
    history = [
        {"date": day, "close": value}
        for day, value in sorted(history_by_date.items())
    ]

    meta_price = _finite(meta.get("regularMarketPrice"))
    native_price = latest_native_close if force and latest_native_close else meta_price
    if native_price is None:
        native_price = latest_native_close
    if native_price is None and history:
        price_eur = history[-1]["close"]
        native_price = price_eur / multiplier
    elif native_price is not None:
        price_eur = native_price * multiplier
    else:
        raise RuntimeError(f"{symbol} returned no usable market price.")

    previous_native = _finite(meta.get("chartPreviousClose")) or _finite(
        meta.get("previousClose")
    )
    previous_eur = (
        previous_native * multiplier
        if previous_native is not None
        else (history[-2]["close"] if len(history) > 1 else None)
    )
    market_timestamp = timestamps[-1] if force and timestamps else meta.get("regularMarketTime")
    market_time = (
        datetime.fromtimestamp(int(market_timestamp), UTC)
        .isoformat()
        .replace("+00:00", "Z")
        if market_timestamp
        else datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    suffix, exchange_name = exchange_for_symbol(symbol)
    stale = quote_status == "stale" or fx_status == "stale"
    source_detail = "fresh upstream quote" if force else "cached market quote"
    return {
        "symbol": symbol,
        "ticker": symbol.split(".")[0],
        "name": meta.get("longName") or meta.get("shortName") or symbol.split(".")[0],
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
        "source": (
            f"European exchange data · {exchange_name} · EUR-normalised · {source_detail}"
            + (" · stale fallback" if stale else "")
        ),
        "stale": stale,
        "cacheStatus": quote_status,
        "fresh": force and quote_status == "refreshed",
        "history": history,
    }
