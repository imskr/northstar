from __future__ import annotations

import csv
import http.cookiejar
import io
import json
import math
import os
import re
import threading
import time
import urllib.request
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

# EODHD (EOD Historical Data) exchange codes — demo key, no auth required, no IP restrictions.
# Works reliably from cloud servers and covers all major European exchanges.
EOHHD_EXCHANGES: dict[str, str] = {
    ".DE": "XETRA",
    ".F": "F",
    ".L": "LSE",
    ".PA": "PA",
    ".AS": "AS",
    ".BR": "BR",
    ".LS": "LB",
    ".MI": "MI",
    ".MC": "MC",
    ".SW": "SW",
    ".VI": "VI",
    ".IR": "IRGX",
    ".ST": "STO",
    ".CO": "CO",
    ".HE": "HEX",
    ".OL": "OL",
    ".WA": "WAR",
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

# A modern browser UA is required — Stooq and Yahoo block custom bot agents from cloud IPs.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_CACHE: dict[tuple[str, ...], tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0

# Thread-local storage for a per-request Twelve Data key supplied by the user.
# The /api/market endpoint sets this so _twelve_key() picks it up without
# changing all function signatures.
_request_td_key: threading.local = threading.local()

# Yahoo Finance requires a session cookie + crumb since 2024.
# We use a proper http.cookiejar opener so cookies are handled transparently.
_yf_jar = http.cookiejar.CookieJar()
_yf_opener: urllib.request.OpenerDirector | None = None
_yf_opener_lock = threading.Lock()
_yf_crumb: str = ""
_yf_crumb_at: float = 0.0
_yf_crumb_lock = threading.Lock()

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
    user_key = getattr(_request_td_key, "value", "").strip()
    return bool(user_key or os.getenv("TWELVE_DATA_API_KEY", "").strip())


def set_request_td_key(key: str) -> None:
    """Set the Twelve Data key for the current request thread."""
    _request_td_key.value = (key or "").strip()


def clear_request_td_key() -> None:
    _request_td_key.value = ""


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


def _request(
    url: str,
    *,
    accept: str,
    timeout: float = 10.0,
    fresh: bool = False,
    extra_headers: dict | None = None,
) -> bytes:
    _throttle()
    if fresh:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}northstar_ts={time.time_ns()}"
    headers: dict[str, str] = {
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": _BROWSER_UA,
    }
    if fresh:
        headers.update({"Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"})
    if extra_headers:
        headers.update(extra_headers)
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 429:
            raise MarketRateLimited(_retry_after(exc.headers)) from exc
        body = exc.read().decode("utf-8", "replace")[:300]
        detail = body
        try:
            parsed_body = json.loads(body)
        except (ValueError, TypeError):
            parsed_body = None
        if isinstance(parsed_body, dict) and parsed_body.get("message"):
            detail = str(parsed_body["message"])
        raise RuntimeError(f"Market service returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the market service: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Market service timed out.") from exc


def _yf_get_opener() -> urllib.request.OpenerDirector:
    """Return (creating if needed) the module-level Yahoo Finance opener."""
    global _yf_opener
    with _yf_opener_lock:
        if _yf_opener is None:
            _yf_opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(_yf_jar)
            )
    return _yf_opener


def _yf_refresh_crumb() -> str:
    """Visit finance.yahoo.com to establish session cookies, then fetch a crumb."""
    opener = _yf_get_opener()
    # Establish session cookies via Yahoo Finance's landing page.
    for init_url in ["https://finance.yahoo.com/", "https://yahoo.com/"]:
        try:
            req = urllib.request.Request(
                init_url,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
            )
            with opener.open(req, timeout=10):
                pass
            break
        except Exception:
            continue
    # Retrieve the crumb using the now-established cookies.
    for host in ("query2", "query1"):
        try:
            req = urllib.request.Request(
                f"https://{host}.finance.yahoo.com/v1/test/getcrumb",
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept": "*/*",
                    "Referer": "https://finance.yahoo.com/",
                },
            )
            with opener.open(req, timeout=8) as r:
                crumb = r.read().decode("utf-8").strip()
                if crumb and 3 < len(crumb) < 60 and not crumb.startswith("<"):
                    return crumb
        except Exception:
            continue
    return ""


def _yf_get_crumb() -> str:
    """Return a cached Yahoo Finance crumb, refreshing if older than 50 minutes."""
    global _yf_crumb, _yf_crumb_at
    with _yf_crumb_lock:
        if _yf_crumb and time.monotonic() - _yf_crumb_at < 3000:
            return _yf_crumb
    crumb = _yf_refresh_crumb()
    if crumb:
        with _yf_crumb_lock:
            _yf_crumb = crumb
            _yf_crumb_at = time.monotonic()
    return crumb


def _fetch_json_yf(url: str) -> dict:
    """Fetch JSON from Yahoo Finance using the managed cookie jar + crumb."""
    crumb = _yf_get_crumb()
    if crumb:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}crumb={quote(crumb, safe='')}"
    opener = _yf_get_opener()
    _throttle()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://finance.yahoo.com/",
        },
    )
    try:
        with opener.open(req, timeout=12) as r:
            return json.loads(r.read().decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Yahoo Finance returned invalid JSON.") from exc
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise MarketRateLimited(_retry_after(exc.headers)) from exc
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Yahoo Finance returned HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Yahoo Finance: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise RuntimeError("Yahoo Finance request timed out.") from exc


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


def _twelve_identifier(symbol: str) -> str:
    ticker, suffix, _ = _symbol_parts(symbol)
    exchange = TWELVE_EXCHANGES.get(suffix)
    if not exchange:
        raise RuntimeError(f"Twelve Data exchange mapping is not configured for {suffix}.")
    # Twelve Data's canonical exchange-specific identifier is SYMBOL:MIC.
    return f"{ticker}:{exchange}"


def _twelve_key() -> str:
    # User-supplied key (via /api/market?twkey=…) takes precedence over env var.
    key = getattr(_request_td_key, "value", "").strip() or os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "No Twelve Data API key found. Add a free key at twelvedata.com, "
            "then enter it in Northstar Settings → Twelve Data API key."
        )
    return key


def _twelve_error_message(data: dict) -> str | None:
    if not isinstance(data, dict):
        return "Twelve Data returned an unexpected response."
    if str(data.get("status") or "").lower() != "error" and not data.get("code"):
        return None
    return str(data.get("message") or "Twelve Data rejected the request.")


def _twelve_batch(endpoint: str, symbols: list[str], *, fresh: bool = False, **params) -> tuple[dict[str, dict], dict[str, str]]:
    if not symbols:
        return {}, {}
    identifiers = {_twelve_identifier(symbol): symbol for symbol in symbols}
    query = {
        "symbol": ",".join(identifiers),
        **{key: str(value) for key, value in params.items() if value is not None},
        "apikey": _twelve_key(),
    }
    try:
        raw = _fetch_json(f"https://api.twelvedata.com/{endpoint}?{urlencode(query)}", fresh=fresh)
    except (MarketRateLimited, RuntimeError):
        # Twelve Data's multi-symbol quote/time_series endpoints reject the ENTIRE
        # request with a single HTTP error when even one symbol isn't entitled on
        # the caller's plan (common for European ETFs/Xetra on the free tier).
        # Re-issue one symbol at a time so symbols Twelve Data *can* serve still
        # get a real-time price instead of being dragged down with the bad one.
        if len(symbols) == 1:
            raise
        results: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for symbol in symbols:
            try:
                sub_results, sub_errors = _twelve_batch(endpoint, [symbol], fresh=fresh, **params)
            except (MarketRateLimited, RuntimeError) as sub_exc:
                errors[symbol] = str(sub_exc)
                continue
            results.update(sub_results)
            errors.update(sub_errors)
        return results, errors
    # A single-symbol response is the payload itself; batch responses are keyed by SYMBOL:MIC.
    if len(symbols) == 1 and (_twelve_error_message(raw) is not None or endpoint == "quote" and ("close" in raw or "price" in raw) or endpoint == "time_series" and ("values" in raw or "meta" in raw)):
        raw = {next(iter(identifiers)): raw}
    if not isinstance(raw, dict):
        return {}, {symbol: "Twelve Data returned an unexpected batch response." for symbol in symbols}

    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for identifier, symbol in identifiers.items():
        item = raw.get(identifier)
        if item is None:
            # Some responses use the ticker only even when an exchange-qualified identifier was requested.
            ticker = identifier.split(":", 1)[0]
            item = raw.get(ticker)
        if isinstance(item, dict) and isinstance(item.get("data"), dict):
            item = item["data"]
        if not isinstance(item, dict):
            errors[symbol] = f"Twelve Data did not return {identifier}."
            continue
        message = _twelve_error_message(item)
        if message:
            errors[symbol] = message
            continue
        results[symbol] = item
    return results, errors


def _twelve_timestamp(data: dict) -> int:
    timestamp = data.get("timestamp")
    if timestamp is not None:
        try:
            return int(timestamp)
        except (TypeError, ValueError):
            pass
    raw = str(data.get("datetime") or "")
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return int(datetime.now(UTC).timestamp())


def _twelve_quote_payload(symbol: str, data: dict) -> dict:
    message = _twelve_error_message(data)
    if message:
        _twelve_error(data)
    price = _finite(data.get("close")) or _finite(data.get("price"))
    if not price or price <= 0:
        raise RuntimeError(f"Twelve Data returned no usable quote for {_twelve_identifier(symbol)}.")
    previous = _finite(data.get("previous_close"))
    market_open = data.get("is_market_open")
    if isinstance(market_open, str):
        market_open = market_open.strip().lower() in {"1", "true", "yes", "open"}
    # Twelve Data marks XETR as EOD-delayed. Do not present an accepted
    # Xetra response as live merely because it came from the quote endpoint.
    is_xetra_eod = _symbol_parts(symbol)[1] == ".DE"
    return {
        "provider": "Twelve Data",
        "realtime": not is_xetra_eod,
        "delayed": is_xetra_eod,
        "name": data.get("name") or symbol.split(".")[0],
        "currency": str(data.get("currency") or SUFFIX_CURRENCIES.get(_symbol_parts(symbol)[1], "EUR")),
        "price": price,
        "previous": previous,
        "timestamp": _twelve_timestamp(data),
        "market_state": "REGULAR" if market_open else "CLOSED",
        "history": [],
    }


def _twelve_history_payload(symbol: str, data: dict) -> dict:
    message = _twelve_error_message(data)
    if message:
        _twelve_error(data)
    rows = []
    for row in data.get("values") or []:
        close = _finite(row.get("close"))
        day = str(row.get("datetime") or "")[:10]
        if close and close > 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            rows.append({"date": day, "close": close})
    rows.sort(key=lambda item: item["date"])
    if len(rows) < 2:
        raise RuntimeError(f"Twelve Data returned insufficient history for {_twelve_identifier(symbol)}.")
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


def _twelve_quote(symbol: str) -> dict:
    payloads, errors = _twelve_batch("quote", [symbol], fresh=True)
    if symbol in errors:
        message = errors[symbol]
        lowered = message.lower()
        if "credit" in lowered or "rate limit" in lowered:
            raise MarketRateLimited()
        if any(word in lowered for word in ("subscription", "plan", "not available", "not authorized", "permission", "access")):
            raise MarketEntitlementError(message)
        raise RuntimeError(message)
    return _twelve_quote_payload(symbol, payloads[symbol])


def _twelve_history(symbol: str, range_: str) -> dict:
    outputsize = min(5000, max(20, RANGE_DAYS.get(range_, 400)))
    payloads, errors = _twelve_batch(
        "time_series",
        [symbol],
        interval="1day",
        outputsize=outputsize,
        order="ASC",
        timezone="Europe/Berlin",
    )
    if symbol in errors:
        message = errors[symbol]
        lowered = message.lower()
        if "credit" in lowered or "rate limit" in lowered:
            raise MarketRateLimited()
        if any(word in lowered for word in ("subscription", "plan", "not available", "not authorized", "permission", "access")):
            raise MarketEntitlementError(message)
        raise RuntimeError(message)
    return _twelve_history_payload(symbol, payloads[symbol])


def _twelve_quote_payloads(symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    raw, errors = _twelve_batch("quote", symbols, fresh=True)
    parsed: dict[str, dict] = {}
    for symbol, item in raw.items():
        try:
            parsed[symbol] = _twelve_quote_payload(symbol, item)
        except (MarketRateLimited, MarketEntitlementError, RuntimeError) as exc:
            errors[symbol] = str(exc)
    return parsed, errors


def _twelve_history_payloads(symbols: list[str], range_: str) -> tuple[dict[str, dict], dict[str, str]]:
    outputsize = min(5000, max(20, RANGE_DAYS.get(range_, 400)))
    raw, errors = _twelve_batch(
        "time_series",
        symbols,
        interval="1day",
        outputsize=outputsize,
        order="ASC",
        timezone="Europe/Berlin",
    )
    parsed: dict[str, dict] = {}
    for symbol, item in raw.items():
        try:
            parsed[symbol] = _twelve_history_payload(symbol, item)
        except (MarketRateLimited, MarketEntitlementError, RuntimeError) as exc:
            errors[symbol] = str(exc)
    return parsed, errors

# ── EODHD (free demo key, no IP restrictions) ────────────────────────────────

def _eodhd_symbol(symbol: str) -> str | None:
    """Map e.g. VWCE.DE → VWCE.XETRA for the EODHD API. Returns None if no mapping."""
    upper = symbol.upper()
    for suffix, exchange in EOHHD_EXCHANGES.items():
        if upper.endswith(suffix):
            return f"{upper[:-len(suffix)]}.{exchange}"
    return None


def _eodhd_quote(symbol: str) -> dict:
    eodhd_sym = _eodhd_symbol(symbol)
    if not eodhd_sym:
        raise RuntimeError(f"EODHD: no exchange mapping for {symbol}.")
    url = f"https://eodhd.com/api/real-time/{quote(eodhd_sym, safe='.')}?api_token=demo&fmt=json"
    data = _fetch_json(url, timeout=12)
    # EODHD may return a list for batch calls; unwrap if needed.
    if isinstance(data, list):
        data = data[0] if data else {}
    price = _finite(data.get("close")) or _finite(data.get("open"))
    if not price or price <= 0:
        raise RuntimeError(f"EODHD returned no usable price for {symbol}.")
    _, suffix, _ = _symbol_parts(symbol)
    prev = _finite(data.get("previousClose"))
    ts = int(data.get("timestamp") or datetime.now(UTC).timestamp())
    return {
        "provider": "EODHD",
        "realtime": False,
        "delayed": True,
        "name": symbol.split(".")[0],
        "currency": SUFFIX_CURRENCIES.get(suffix, "EUR"),
        "price": price,
        "previous": prev,
        "timestamp": ts,
        "market_state": None,
        "history": [],
    }


def _eodhd_history(symbol: str, range_: str) -> dict:
    eodhd_sym = _eodhd_symbol(symbol)
    if not eodhd_sym:
        raise RuntimeError(f"EODHD: no exchange mapping for {symbol}.")
    days = RANGE_DAYS.get(range_, 400)
    end = date.today()
    start = end - timedelta(days=days)
    url = (
        f"https://eodhd.com/api/eod/{quote(eodhd_sym, safe='.')}?"
        f"api_token=demo&period=d&from={start.isoformat()}&to={end.isoformat()}&fmt=json"
    )
    raw = _fetch_json(url, timeout=15)
    if not isinstance(raw, list):
        raise RuntimeError(f"EODHD returned unexpected format for {symbol}.")
    rows: list[dict] = []
    for item in raw:
        close = _finite(item.get("adjusted_close") or item.get("close"))
        day = str(item.get("date") or "")[:10]
        if close and close > 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            rows.append({"date": day, "close": close})
    rows.sort(key=lambda x: x["date"])
    if len(rows) < 2:
        raise RuntimeError(f"EODHD returned insufficient history for {symbol}.")
    _, suffix, _ = _symbol_parts(symbol)
    return {
        "provider": "EODHD",
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


# ── Stooq ─────────────────────────────────────────────────────────────────────

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
    # Use crumb-authenticated helper; fall back to query2 host if query1 fails.
    try:
        data = _fetch_json_yf(f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{params}")
    except RuntimeError:
        data = _fetch_json_yf(f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}?{params}")
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


def _load_quote(symbol: str, *, prefer_realtime: bool = True) -> dict:
    errors: list[str] = []

    if prefer_realtime and real_time_configured():
        try:
            return _twelve_quote(symbol)
        except (MarketRateLimited, MarketEntitlementError, RuntimeError) as exc:
            errors.append(f"Twelve Data: {exc}")

    # Keep an explicitly configured EODHD account as an optional fallback.
    eodhd_token = os.getenv("EODHD_API_TOKEN", "").strip()
    if eodhd_token and eodhd_token.lower() != "demo":
        try:
            payload = _eodhd_quote(symbol)
            payload["provider_errors"] = errors.copy()
            return payload
        except (MarketRateLimited, RuntimeError) as exc:
            errors.append(f"EODHD: {exc}")

    # Stooq has direct coverage for the Xetra listings used by Northstar and is
    # less fragile on Render than Yahoo's cookie/crumb flow. It remains a delayed,
    # best-effort source, so Yahoo is retained as an independent fallback.
    suffix = exchange_for_symbol(symbol)[0]
    if suffix == ".DE":
        loaders = (
            ("Stooq", lambda: _stooq_quote(symbol)),
            ("Stooq history", lambda: _stooq_history(symbol, "5d")),
            ("Yahoo", lambda: _yahoo_history(symbol, "5d")),
        )
    else:
        loaders = (
            ("Yahoo", lambda: _yahoo_history(symbol, "5d")),
            ("Stooq", lambda: _stooq_quote(symbol)),
            ("Stooq history", lambda: _stooq_history(symbol, "5d")),
        )

    for provider_name, loader in loaders:
        try:
            payload = loader()
            if provider_name == "Yahoo":
                payload["history"] = payload.get("history", [])[-2:]
            payload["provider_errors"] = errors.copy()
            return payload
        except (MarketRateLimited, RuntimeError) as exc:
            errors.append(f"{provider_name}: {exc}")

    raise RuntimeError(" | ".join(errors) or f"No quote provider returned {symbol}.")

def _load_history(symbol: str, range_: str, *, prefer_realtime: bool = True) -> dict:
    errors: list[str] = []

    if prefer_realtime and real_time_configured():
        try:
            return _twelve_history(symbol, range_)
        except (MarketRateLimited, MarketEntitlementError, RuntimeError) as exc:
            errors.append(f"Twelve Data: {exc}")

    eodhd_token = os.getenv("EODHD_API_TOKEN", "").strip()
    if eodhd_token and eodhd_token.lower() != "demo":
        try:
            payload = _eodhd_history(symbol, range_)
            payload["provider_errors"] = errors.copy()
            return payload
        except (MarketRateLimited, RuntimeError) as exc:
            errors.append(f"EODHD: {exc}")

    suffix = exchange_for_symbol(symbol)[0]
    loaders = (
        (("Stooq", lambda: _stooq_history(symbol, range_)), ("Yahoo", lambda: _yahoo_history(symbol, range_)))
        if suffix == ".DE"
        else (("Yahoo", lambda: _yahoo_history(symbol, range_)), ("Stooq", lambda: _stooq_history(symbol, range_)))
    )
    for provider_name, loader in loaders:
        try:
            payload = loader()
            payload["provider_errors"] = errors.copy()
            return payload
        except (MarketRateLimited, RuntimeError) as exc:
            errors.append(f"{provider_name}: {exc}")

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


def _normalize_payload(symbol: str, payload: dict, cache_status: str) -> dict:
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
    provider_errors = [str(item) for item in payload.get("provider_errors") or [] if str(item).strip()]
    source_bits = [provider, exchange_name, "EUR-normalised"]
    source_bits.append("real-time" if payload.get("realtime") else "latest available / may be delayed")
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
        "providerErrors": provider_errors,
        "history": history,
    }


def normalize(
    symbol: str,
    range_: str = "5d",
    *,
    force: bool = False,
    include_history: bool | None = None,
    prefer_realtime: bool = True,
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
        (kind, symbol, range_, "live" if prefer_realtime else "fallback"),
        lambda: _load_history(symbol, range_, prefer_realtime=prefer_realtime) if include_history else _load_quote(symbol, prefer_realtime=prefer_realtime),
        ttl=ttl,
        force=force and not include_history,
        allow_stale=not force or include_history,
    )
    return _normalize_payload(symbol, payload, cache_status)


def normalize_quote_batch(symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    normalized = [normalize_symbol(symbol) for symbol in symbols]
    raw, errors = _twelve_quote_payloads(normalized)
    data: dict[str, dict] = {}
    for symbol, payload in raw.items():
        try:
            result = _normalize_payload(symbol, payload, "refreshed")
            with _CACHE_LOCK:
                _CACHE[("quote", symbol, "5d", "live")] = (time.monotonic(), payload)
            data[symbol] = result
        except RuntimeError as exc:
            errors[symbol] = str(exc)
    return data, errors


def normalize_history_batch(symbols: list[str], range_: str) -> tuple[dict[str, dict], dict[str, str]]:
    normalized = [normalize_symbol(symbol) for symbol in symbols]
    raw, errors = _twelve_history_payloads(normalized, range_)
    data: dict[str, dict] = {}
    for symbol, payload in raw.items():
        try:
            result = _normalize_payload(symbol, payload, "refreshed")
            with _CACHE_LOCK:
                _CACHE[("history", symbol, range_, "live")] = (time.monotonic(), payload)
            data[symbol] = result
        except RuntimeError as exc:
            errors[symbol] = str(exc)
    return data, errors


def twelve_diagnostics(symbol: str) -> dict:
    symbol = normalize_symbol(symbol)
    configured = real_time_configured()
    result = {"configured": configured, "symbol": symbol}
    if not configured:
        result.update({"ok": False, "message": "TWELVE_DATA_API_KEY is not visible to the running Render service. Save the variable and redeploy."})
        return result
    try:
        payload = _twelve_quote(symbol)
    except (MarketRateLimited, MarketEntitlementError, RuntimeError) as exc:
        result.update({"ok": False, "identifier": _twelve_identifier(symbol), "message": str(exc)})
        return result
    result.update({
        "ok": True,
        "identifier": _twelve_identifier(symbol),
        "provider": payload.get("provider"),
        "price": payload.get("price"),
        "currency": payload.get("currency"),
        "marketState": payload.get("market_state"),
    })
    return result
