"""Public BTC-USD feeds: Coinbase, Kraken, Bitstamp, Gemini.

Each adapter returns (tick, book):
  tick = dict(exchange, exch_ts, recv_ts, price, bid, ask, bid_size, ask_size, size)
  book = dict(exchange, exch_ts, recv_ts, bids=[(price,size)...<=5], asks=[...])
`price` is the last trade price. `exch_ts` is the exchange's own timestamp (last trade
or ticker time, see each parser) and may be None; `recv_ts` is the local clock when the
response arrived and is always set. Network access is injected (`fetch`) for offline tests.
Response formats follow each exchange's public REST docs; they have NOT been verified live
from this sandbox (outbound exchange hosts are blocked here).
"""
import datetime as dt, json, math, time
import urllib.request

DEPTH = 5
EXCHANGES = ('coinbase', 'kraken', 'bitstamp', 'gemini')


def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'KalshiResearch/0.3', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _num(value, name, positive=True):
    x = float(value)
    if not math.isfinite(x) or (positive and x <= 0):
        raise ValueError(f'invalid {name}: {value!r}')
    return x


def _opt(value):
    try:
        return _num(value, 'optional')
    except (TypeError, ValueError):
        return None


def _iso(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def _levels(rows, reverse, depth=DEPTH):
    out = []
    for row in rows:
        price, size = (row['price'], row['amount']) if isinstance(row, dict) else (row[0], row[1])
        p, s = _opt(price), _opt(size)
        if p is not None and s is not None:
            out.append((p, s))
    out.sort(key=lambda v: v[0], reverse=reverse)
    return out[:depth]


def _tick(exchange, exch_ts, recv, price, bid=None, ask=None, bid_size=None, ask_size=None, size=None):
    if bid is not None and ask is not None and bid > ask:  # crossed/garbled quote: keep the trade, drop the quote
        bid = ask = bid_size = ask_size = None
    return dict(exchange=exchange, exch_ts=exch_ts, recv_ts=recv, price=_num(price, 'price'),
                bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size, size=size)


def _book(exchange, exch_ts, recv, bids, asks):
    bids, asks = _levels(bids, True), _levels(asks, False)
    if bids and asks and bids[0][0] > asks[0][0]:
        raise ValueError('crossed book')
    return dict(exchange=exchange, exch_ts=exch_ts, recv_ts=recv, bids=bids, asks=asks)


# ---- parsers (pure) -------------------------------------------------------------------
def parse_coinbase(ticker, book, recv):
    """Ticker `time` is the last trade time."""
    ts = _iso(ticker['time'])
    t = _tick('coinbase', ts, recv, ticker['price'], _opt(ticker.get('bid')), _opt(ticker.get('ask')),
              _opt(ticker.get('bid_size')), _opt(ticker.get('ask_size')), _opt(ticker.get('size')))
    return t, _book('coinbase', _iso(book['time']) if book.get('time') else None, recv, book['bids'], book['asks'])


def _kraken_result(raw):
    if raw.get('error'):
        raise ValueError('kraken error: %s' % raw['error'])
    key = next(k for k in raw['result'] if k != 'last')
    return raw['result'][key]


def parse_kraken(trades, depth, recv):
    """Trades row: [price, volume, time, side, type, misc, id]; `time` is last trade time."""
    last = _kraken_result(trades)[-1]
    d = _kraken_result(depth)
    book = _book('kraken', None, recv, d['bids'], d['asks'])
    bid, ask = (book['bids'][0] if book['bids'] else (None, None)), (book['asks'][0] if book['asks'] else (None, None))
    t = _tick('kraken', float(last[2]), recv, last[0], bid[0], ask[0], bid[1], ask[1], _opt(last[1]))
    return t, book


def parse_bitstamp(ticker, book, recv):
    """Ticker `timestamp` (seconds) is the ticker generation time."""
    t = _tick('bitstamp', float(ticker['timestamp']), recv, ticker['last'], _opt(ticker.get('bid')), _opt(ticker.get('ask')))
    ts = float(book['microtimestamp']) / 1e6 if book.get('microtimestamp') else (float(book['timestamp']) if book.get('timestamp') else None)
    return t, _book('bitstamp', ts, recv, book['bids'], book['asks'])


def parse_gemini(ticker, book, recv):
    """pubticker has no trade time; `volume.timestamp` (ms) is the ticker time."""
    ts = ticker.get('volume', {}).get('timestamp')
    t = _tick('gemini', float(ts) / 1000 if ts else None, recv, ticker['last'], _opt(ticker.get('bid')), _opt(ticker.get('ask')))
    return t, _book('gemini', None, recv, book['bids'], book['asks'])


# ---- polling (network injected) --------------------------------------------------------
URLS = {
    'coinbase': ('https://api.exchange.coinbase.com/products/BTC-USD/ticker',
                 'https://api.exchange.coinbase.com/products/BTC-USD/book?level=2'),
    'kraken': ('https://api.kraken.com/0/public/Trades?pair=XBTUSD&count=1',
               'https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=%d' % DEPTH),
    'bitstamp': ('https://www.bitstamp.net/api/v2/ticker/btcusd/', 'https://www.bitstamp.net/api/v2/order_book/btcusd/'),
    'gemini': ('https://api.gemini.com/v1/pubticker/btcusd',
               'https://api.gemini.com/v1/book/btcusd?limit_bids=%d&limit_asks=%d' % (DEPTH, DEPTH)),
}
PARSERS = dict(coinbase=parse_coinbase, kraken=parse_kraken, bitstamp=parse_bitstamp, gemini=parse_gemini)


def poll(exchange, fetch=fetch_json, clock=time.time):
    """Fetch one exchange. `recv_ts` is stamped after the responses arrive. Raises on any failure."""
    first, second = URLS[exchange]
    a, b = fetch(first), fetch(second)
    return PARSERS[exchange](a, b, clock())


def store(db, tick, book):
    db.execute('INSERT INTO exchange_ticks(exchange,exch_ts,recv_ts,price,bid,ask,bid_size,ask_size,size) VALUES(?,?,?,?,?,?,?,?,?)',
               (tick['exchange'], tick['exch_ts'], tick['recv_ts'], tick['price'], tick['bid'], tick['ask'],
                tick['bid_size'], tick['ask_size'], tick['size']))
    for side, rows in (('bid', book['bids']), ('ask', book['asks'])):
        for level, (p, s) in enumerate(rows):
            db.execute('INSERT INTO exchange_books(exchange,exch_ts,recv_ts,side,level,price,size) VALUES(?,?,?,?,?,?,?)',
                       (book['exchange'], book['exch_ts'], book['recv_ts'], side, level, p, s))
