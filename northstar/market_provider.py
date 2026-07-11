#!/usr/bin/env python3
"""Northstar local server v13.

ETF current prices are read from Deutsche Börse's signed Server-Sent Events
quote stream for Xetra. Yahoo Finance is used for historical charts and EUR
benchmark histories only; it is never silently substituted for ETF valuation.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, urlencode, urljoin
from urllib.request import Request, urlopen
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import argparse, errno, hashlib, json, math, os, re, threading, time, webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent
INSTRUMENTS = {
    'BCFP.DE': {'isin':'IE000SB4G4I4', 'ticker':'BCFP'},
    'SEC0.DE': {'isin':'IE000I8KRLL9', 'ticker':'SEC0'},
    'EMSM.DE': {'isin':'IE00B3DWVS88', 'ticker':'EMSM'},
    'SXRV.DE': {'isin':'IE00B53SZB19', 'ticker':'SXRV', 'benchmark':True},
    'SXR8.DE': {'isin':'IE00B5BMR087', 'ticker':'SXR8', 'benchmark':True},
}
ALLOWED = set(INSTRUMENTS)
CACHE = {}
SALT_CACHE = {'value': None, 'at': 0}
SALT_LOCK = threading.Lock()
BERLIN = ZoneInfo('Europe/Berlin')


def finite(v):
    try:
        x = float(str(v).replace(',', '.'))
        return x if math.isfinite(x) else None
    except Exception:
        return None


def fetch_bytes(url, timeout=18, headers=None):
    base = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 NorthstarPortfolio/10.0',
        'Accept': '*/*',
        'Cache-Control': 'no-cache',
    }
    if headers:
        base.update(headers)
    req = Request(url, headers=base)
    with urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers


def fetch_text(url, timeout=18, headers=None):
    raw, _ = fetch_bytes(url, timeout=timeout, headers=headers)
    return raw.decode('utf-8', 'replace')


def _find_tracing_salt_unlocked(force=False):
    if not force and SALT_CACHE['value'] and time.time() - SALT_CACHE['at'] < 6 * 3600:
        return SALT_CACHE['value']
    errors = []
    homes = ('https://live.deutsche-boerse.com/', 'https://www.boerse-frankfurt.de/')
    salt_patterns = [
        r'\bsalt\s*:\s*["\']([A-Za-z0-9_-]{8,})["\']',
        r'["\']salt["\']\s*:\s*["\']([A-Za-z0-9_-]{8,})["\']',
        r'\b(?:traceSalt|tracingSalt)\s*[:=]\s*["\']([A-Za-z0-9_-]{8,})["\']',
    ]
    for home in homes:
        try:
            html = fetch_text(home, timeout=15, headers={'Accept':'text/html,application/xhtml+xml'})
            candidates = re.findall(r'<script[^>]+src=["\']([^"\']+\.js(?:\?[^"\']*)?)["\']', html, re.I)
            # Main bundle first, then the remaining script chunks.
            candidates.sort(key=lambda x: (0 if 'main' in x.lower() else 1, len(x)))
            for src in candidates[:20]:
                script_url = urljoin(home, src)
                try:
                    js = fetch_text(script_url, timeout=15, headers={'Referer':home, 'Accept':'application/javascript,*/*'})
                except Exception as exc:
                    errors.append(f'{script_url}: {exc}')
                    continue
                for pat in salt_patterns:
                    m = re.search(pat, js)
                    if m:
                        SALT_CACHE.update(value=m.group(1), at=time.time())
                        return m.group(1)
            # Some builds inline configuration in the HTML.
            for pat in salt_patterns:
                m = re.search(pat, html)
                if m:
                    SALT_CACHE.update(value=m.group(1), at=time.time())
                    return m.group(1)
            errors.append(f'{home}: tracing salt not found')
        except Exception as exc:
            errors.append(f'{home}: {exc}')
    raise RuntimeError('Could not initialise Deutsche Börse signed feed. ' + ' | '.join(errors[-4:]))


def find_tracing_salt(force=False):
    with SALT_LOCK:
        return _find_tracing_salt_unlocked(force=force)


def signed_headers(url, accept='text/event-stream'):
    salt = find_tracing_salt()
    now_utc = datetime.now(timezone.utc)
    client_date = now_utc.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    trace = hashlib.md5((client_date + url + salt).encode()).hexdigest()
    berlin = datetime.now(BERLIN).strftime('%Y%m%d%H%M')
    security = hashlib.md5(berlin.encode()).hexdigest()
    return {
        'Accept': accept,
        'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0',
        'Origin': 'https://live.deutsche-boerse.com',
        'Referer': 'https://live.deutsche-boerse.com/',
        'Client-Date': client_date,
        'X-Client-TraceId': trace,
        'X-Security': security,
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 NorthstarPortfolio/10.0',
    }


def _walk_find(obj, names):
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] not in (None, ''):
                return obj[name]
        for value in obj.values():
            hit = _walk_find(value, names)
            if hit not in (None, ''):
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = _walk_find(value, names)
            if hit not in (None, ''):
                return hit
    return None


def parse_iso_ts(value):
    if value in (None, ''):
        return 0.0
    try:
        s = str(value).strip().replace('Z', '+00:00')
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def parse_quote_obj(parsed):
    def get(names, numeric=False):
        value = _walk_find(parsed, names)
        return finite(value) if numeric else value
    return {
        'lastTrade': get(['lastPrice','price','last'], True),
        'bid': get(['bidLimit','bidPrice','bid'], True),
        'ask': get(['askLimit','askPrice','ask'], True),
        'change': get(['changeToPrevDayAbsolute','changeAbsolute','change'], True),
        'percentChange': get(['changeToPrevDayInPercent','changePercent','percentChange'], True),
        'spreadAbsolute': get(['spreadAbsolute'], True),
        'spreadPct': get(['spreadRelative','spreadPct'], True),
        'marketTime': get(['timestampLastPrice','timestamp','time']),
        'snapshotTime': get(['timestamp','timestampLastPrice','time']),
        'marketState': get(['tradingStatus','marketState','status']),
    }


def merge_quote(base, new):
    if base is None:
        return dict(new)
    out = dict(base)
    # Newer quote snapshots can update bid/ask without a new trade. Keep the most
    # recent fields while never replacing a newer last trade with an older one.
    base_trade_ts = parse_iso_ts(base.get('marketTime'))
    new_trade_ts = parse_iso_ts(new.get('marketTime'))
    if new.get('lastTrade') is not None and (new_trade_ts >= base_trade_ts or base.get('lastTrade') is None):
        for k in ('lastTrade','change','percentChange','marketTime'):
            if new.get(k) is not None:
                out[k] = new[k]
    base_snap_ts = parse_iso_ts(base.get('snapshotTime'))
    new_snap_ts = parse_iso_ts(new.get('snapshotTime'))
    if new_snap_ts >= base_snap_ts:
        for k in ('bid','ask','spreadAbsolute','spreadPct','snapshotTime','marketState'):
            if new.get(k) is not None:
                out[k] = new[k]
    return out


def read_sse_quote(url, timeout=5.5, settle_seconds=2.6):
    req = Request(url, headers=signed_headers(url))
    started = time.monotonic()
    first_valid_at = None
    best = None
    event_lines = []
    with urlopen(req, timeout=timeout) as response:
        while time.monotonic() - started < timeout:
            try:
                raw = response.readline()
            except Exception:
                if best is not None:
                    break
                raise
            if not raw:
                break
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            if line.startswith('data:'):
                event_lines.append(line[5:].lstrip())
            elif line == '' and event_lines:
                payload = '\n'.join(event_lines).strip()
                event_lines = []
                try:
                    obj = json.loads(payload)
                    quote_obj = parse_quote_obj(obj)
                    if quote_obj.get('lastTrade') is not None or quote_obj.get('bid') is not None or quote_obj.get('ask') is not None:
                        best = merge_quote(best, quote_obj)
                        if first_valid_at is None:
                            first_valid_at = time.monotonic()
                except Exception:
                    pass
            if first_valid_at is not None and time.monotonic() - first_valid_at >= settle_seconds:
                break
    if best and best.get('lastTrade') is not None:
        return best
    raise RuntimeError('Signed Xetra stream returned no executable quote snapshot.')


def fetch_xetra_quote(isin):
    key = ('xetra-sse-v13', isin)
    cached = CACHE.get(key)
    if cached and time.time() - cached[0] < 5:
        return cached[1]
    errors = []
    hosts = ('https://api.boerse-frankfurt.de', 'https://api.live.deutsche-boerse.com')
    endpoints = ('quote_box', 'price_information')
    for host in hosts:
        for endpoint in endpoints:
            url = f'{host}/v1/data/{endpoint}?' + urlencode({'isin':isin, 'mic':'XETR'})
            try:
                quote_data = read_sse_quote(url)
                quote_data['endpoint'] = endpoint
                quote_data['apiHost'] = host
                CACHE[key] = (time.time(), quote_data)
                return quote_data
            except Exception as exc:
                errors.append(f'{host.split("//",1)[-1]} {endpoint}: {exc}')
                # A stale salt causes signed requests to fail; refresh it once.
                if '401' in str(exc) or '403' in str(exc):
                    try:
                        find_tracing_salt(force=True)
                    except Exception:
                        pass
    raise RuntimeError('Official Xetra stream unavailable. ' + ' | '.join(errors[-4:]))


def fetch_chart(symbol, range_, interval):
    key = ('yahoo-history', symbol, range_, interval)
    cached = CACHE.get(key)
    ttl = 60 if interval == '1m' else 21600
    if cached and time.time() - cached[0] < ttl:
        return cached[1]
    u = (f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}'
         f'?range={quote(range_)}&interval={quote(interval)}&includePrePost=false'
         '&includeAdjustedClose=true&events=div%2Csplits')
    req = Request(u, headers={'User-Agent':'Mozilla/5.0 (compatible; NorthstarPortfolio/10.0)','Accept':'application/json'})
    with urlopen(req, timeout=18) as r:
        payload = json.load(r)
    result = payload['chart']['result'][0]
    CACHE[key] = (time.time(), result)
    return result


def last_valid(stamps, values):
    for i in range(min(len(stamps), len(values)) - 1, -1, -1):
        v = finite(values[i])
        if v is not None:
            return v, int(stamps[i])
    return None, None


def daily_history(result):
    stamps = result.get('timestamp', [])
    quotes = result.get('indicators', {}).get('quote', [{}])[0]
    adjusted = result.get('indicators', {}).get('adjclose', [{}])[0].get('adjclose', [])
    closes = quotes.get('close') or []
    history = []
    for i, ts in enumerate(stamps):
        raw = adjusted[i] if i < len(adjusted) and adjusted[i] is not None else (closes[i] if i < len(closes) else None)
        val = finite(raw)
        if val is not None:
            history.append({'date':time.strftime('%Y-%m-%d', time.gmtime(ts)), 'close':val})
    return history


def yahoo_snapshot(symbol, history_range):
    intraday = fetch_chart(symbol, '5d', '1m')
    meta = intraday.get('meta', {})
    stamps = intraday.get('timestamp', [])
    q = intraday.get('indicators', {}).get('quote', [{}])[0]
    last_trade, last_ts = last_valid(stamps, q.get('close') or [])
    regular = finite(meta.get('regularMarketPrice'))
    regular_ts = int(meta.get('regularMarketTime') or 0) or None
    if regular is not None and regular_ts and (not last_ts or regular_ts > last_ts):
        last_trade, last_ts = regular, regular_ts
    if history_range != '5d':
        history = daily_history(fetch_chart(symbol, history_range, '1d'))
    else:
        history = []
        vals = q.get('close') or []
        for i, ts in enumerate(stamps):
            val = finite(vals[i] if i < len(vals) else None)
            if val is not None:
                history.append({'date':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts)), 'close':val})
    return {'meta':meta, 'lastTrade':last_trade, 'lastTs':last_ts, 'history':history}


def normalize(symbol, history_range):
    info = INSTRUMENTS[symbol]
    yahoo = yahoo_snapshot(symbol, history_range)
    meta, history = yahoo['meta'], yahoo['history']

    if info.get('benchmark'):
        price = yahoo['lastTrade']
        previous = finite(meta.get('chartPreviousClose') or meta.get('previousClose'))
        ts = yahoo['lastTs'] or int(meta.get('regularMarketTime') or time.time())
        return {
            'symbol':symbol, 'price':price, 'lastTrade':price, 'bid':None, 'ask':None, 'mid':None,
            'spreadPct':None, 'previousClose':previous,
            'change':price-previous if price is not None and previous else None,
            'percentChange':(price/previous-1)*100 if price is not None and previous else None,
            'currency':meta.get('currency','EUR'), 'exchange':meta.get('fullExchangeName') or 'XETRA',
            'marketState':meta.get('marketState'),
            'marketTime':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts)),
            'source':'Yahoo Finance · benchmark history only', 'priceType':'last trade', 'history':history
        }

    official = fetch_xetra_quote(info['isin'])
    last, bid, ask = official.get('lastTrade'), official.get('bid'), official.get('ask')
    mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid and bid > 0 else None
    spread = ((ask - bid) / mid * 100) if mid else official.get('spreadPct')
    change = official.get('change')
    previous = (last - change) if last is not None and change is not None else None
    pct = official.get('percentChange')
    return {
        'symbol':symbol, 'isin':info['isin'], 'price':last, 'lastTrade':last, 'bid':bid, 'ask':ask, 'mid':mid,
        'spreadPct':spread, 'previousClose':previous, 'change':change,
        'percentChange':pct if pct is not None else ((last/previous-1)*100 if last and previous else None),
        'currency':'EUR', 'exchange':'XETRA', 'marketState':official.get('marketState'),
        'marketTime':official.get('marketTime') or official.get('snapshotTime'),
        'quoteTime':official.get('snapshotTime'),
        'source':'Deutsche Börse · signed Xetra live stream', 'priceType':'last trade',
        'history':history, 'quoteEndpoint':official.get('endpoint'), 'apiHost':official.get('apiHost')
    }


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        raw = super().translate_path(path)
        rel = os.path.relpath(raw, os.getcwd())
        return str(ROOT / rel)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != '/api/market':
            return super().do_GET()
        q = parse_qs(parsed.query)
        symbols = list(dict.fromkeys(s.strip().upper() for s in q.get('symbols',[''])[0].split(',') if s.strip()))
        if not symbols or any(s not in ALLOWED for s in symbols):
            return self.send_json({'error':'Invalid or unsupported symbols.'}, 400)
        range_ = q.get('range',['5d'])[0]
        if range_ not in {'5d','1mo','3mo','6mo','1y','2y','3y','5y'}:
            range_ = '5d'
        data, errors = {}, {}
        with ThreadPoolExecutor(max_workers=min(5, len(symbols))) as pool:
            futures = {pool.submit(normalize, symbol, range_): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    data[symbol] = future.result()
                except Exception as exc:
                    errors[symbol] = str(exc)
        if not data:
            return self.send_json({'error':'All market-data requests failed.', 'errors':errors}, 502)
        return self.send_json({
            'provider':'Deutsche Börse signed Xetra stream + Yahoo history',
            'updatedAt':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
            'data':data, 'errors':errors
        })

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_server(host='127.0.0.1', preferred_port=8000):
    candidates = list(range(preferred_port, preferred_port + 50)) + [0]
    last_error = None
    for port in candidates:
        try:
            server = ThreadingHTTPServer((host, port), Handler)
            return server, int(server.server_address[1])
        except OSError as exc:
            last_error = exc
            if exc.errno in (errno.EADDRINUSE, 48, 98):
                continue
            raise
    raise last_error or RuntimeError('No free local port was found.')


def main():
    parser = argparse.ArgumentParser(description='Run Northstar locally.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000, help='Preferred port. Northstar automatically uses the next free port.')
    parser.add_argument('--open', action='store_true', dest='open_browser')
    args = parser.parse_args()
    os.chdir(ROOT)
    server, port = build_server(args.host, args.port)
    url = f'http://localhost:{port}'
    if port != args.port:
        print(f'Port {args.port} is already in use. Northstar selected port {port}.')
    print(f'Northstar v14 running at {url}')
    print('ETF valuation: Deutsche Börse signed Xetra quote stream. Charts: Yahoo history only.')
    print('Keep this Terminal window open. Press Control-C to stop Northstar.')
    if args.open_browser:
        threading.Timer(0.45, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping Northstar…')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
