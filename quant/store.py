"""Separate SQLite store for the quant pipeline. Never touches research.sqlite."""
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS exchange_ticks(id INTEGER PRIMARY KEY, exchange TEXT NOT NULL,
  exch_ts REAL, recv_ts REAL NOT NULL, price REAL NOT NULL, bid REAL, ask REAL,
  bid_size REAL, ask_size REAL, size REAL);
CREATE INDEX IF NOT EXISTS ticks_ex_time ON exchange_ticks(exchange,recv_ts);
CREATE TABLE IF NOT EXISTS exchange_books(id INTEGER PRIMARY KEY, exchange TEXT NOT NULL,
  exch_ts REAL, recv_ts REAL NOT NULL, side TEXT NOT NULL, level INTEGER NOT NULL,
  price REAL NOT NULL, size REAL NOT NULL);
CREATE INDEX IF NOT EXISTS books_ex_time ON exchange_books(exchange,recv_ts);
CREATE TABLE IF NOT EXISTS spot_proxy(id INTEGER PRIMARY KEY, ts REAL NOT NULL, price REAL,
  n_used INTEGER, n_total INTEGER, status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS proxy_time ON spot_proxy(ts);
CREATE TABLE IF NOT EXISTS proxy_inputs(id INTEGER PRIMARY KEY, ts REAL NOT NULL, exchange TEXT NOT NULL,
  price REAL, age_s REAL, status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS inputs_ex_time ON proxy_inputs(exchange,ts);
CREATE TABLE IF NOT EXISTS kalshi_snapshots(id INTEGER PRIMARY KEY, ticker TEXT NOT NULL,
  recv_ts REAL NOT NULL, request_s REAL, close_ts REAL, strike REAL);
CREATE INDEX IF NOT EXISTS kalshi_snap_market ON kalshi_snapshots(ticker,recv_ts);
CREATE TABLE IF NOT EXISTS kalshi_levels(snapshot_id INTEGER NOT NULL REFERENCES kalshi_snapshots(id),
  side TEXT NOT NULL, level INTEGER NOT NULL, price REAL NOT NULL, size REAL NOT NULL);
CREATE INDEX IF NOT EXISTS kalshi_levels_snap ON kalshi_levels(snapshot_id);
CREATE TABLE IF NOT EXISTS feed_errors(id INTEGER PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL, message TEXT);
'''


def connect(path):
    """Open (creating if needed) the quant database. Refuses the bot's own database file."""
    if str(path).replace('\\', '/').endswith('research.sqlite'):
        raise ValueError('quant pipeline must not write to research.sqlite')
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db
