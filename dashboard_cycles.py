"""15-minute cycle report for the dashboard: read-only over research.sqlite (and quant.sqlite if present).

- current: the active KXBTC15M window (open/close) and when its first call was recorded
- last_24h: one tile per 15-minute window that closed in the last 24 hours (up to 96):
    correct | wrong | pending (no official result yet) | skipped (no first call recorded)
- by_hour: first-call accuracy and Brier by Pacific hour of the window's open (all settled history),
    plus paper P&L by hour from the quant pipeline when it exists.
Scores use FIRST calls only (one per market) and official results only.
"""
import json, math, sqlite3, time
from pathlib import Path
from quant.monitor import pacific

SOURCE = "volatility-proxy-v1"
WINDOW = 900


def _ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    return db


def _ts(s):
    import datetime as dt
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def build_cycles_report(db_path, now=None, quant_path=None):
    now = time.time() if now is None else now
    db = _ro(db_path)
    try:
        markets = {}
        for ticker, meta, result in db.execute("SELECT ticker,metadata,result FROM markets WHERE ticker LIKE 'KXBTC15M-%'"):
            try:
                m = json.loads(meta or "{}"); close = _ts(m["close_time"])
                opened = _ts(m["open_time"]) if m.get("open_time") else close - WINDOW
            except (KeyError, ValueError, TypeError):
                continue
            markets[ticker] = dict(ticker=ticker, open=opened, close=close, result=result if result in ("yes", "no") else None)
        first = {}
        for ticker, observed, p in db.execute("SELECT ticker,observed,p_yes FROM predictions WHERE source=? ORDER BY observed,id", (SOURCE,)):
            first.setdefault(ticker, (observed, p))
    finally:
        db.close()

    entries = {}
    if quant_path and Path(quant_path).exists():
        try:
            q = _ro(quant_path)
            try:
                for row in q.execute("SELECT ts,ticker,source,p_yes,action,reason,contracts,avg_price,fee FROM paper_decisions ORDER BY id"):
                    entries.setdefault(row[1], dict(zip(("ts", "ticker", "source", "p_yes", "action", "reason", "contracts", "avg_price", "fee"), row)))
            finally:
                q.close()
        except sqlite3.Error:
            pass
    current = None
    for m in markets.values():
        if m["open"] <= now < m["close"]:
            fc = first.get(m["ticker"])
            current = dict(ticker=m["ticker"], open=m["open"], close=m["close"], first_call_ts=fc[0] if fc else None,
                           first_call_p=fc[1] if fc else None, locks=[m["open"] + 480, m["open"] + 660], final_minute=m["close"] - 60,
                           entry=entries.get(m["ticker"]))

    tiles = []
    for m in sorted(markets.values(), key=lambda m: m["close"]):
        if not now - 86400 <= m["close"] <= now:
            continue
        fc = first.get(m["ticker"])
        if fc is None:
            state = "skipped"
        elif m["result"] is None:
            state = "pending"
        else:
            state = "correct" if (fc[1] >= 0.5) == (m["result"] == "yes") else "wrong"
        tiles.append(dict(ticker=m["ticker"], open=m["open"], close=m["close"], state=state,
                          p_yes=fc[1] if fc else None, result=m["result"]))

    hours = {h: dict(hour=h, n=0, correct=0, brier=0.0, paper_trades=0, paper_pnl=0.0) for h in range(24)}
    for t, (obs, p) in first.items():
        m = markets.get(t)
        if not m or m["result"] is None:
            continue
        h = hours[pacific(m["open"]).hour]; y = m["result"] == "yes"
        h["n"] += 1; h["correct"] += (p >= 0.5) == y; h["brier"] += (p - y) ** 2
    if quant_path and Path(quant_path).exists():
        try:
            q = _ro(quant_path)
            try:
                for ticker, pnl in q.execute("SELECT ticker,pnl FROM paper_positions WHERE pnl IS NOT NULL"):
                    m = markets.get(ticker)
                    if m:
                        h = hours[pacific(m["open"]).hour]; h["paper_trades"] += 1; h["paper_pnl"] += pnl
            finally:
                q.close()
        except sqlite3.Error:
            pass
    for h in hours.values():
        h["accuracy"] = h["correct"] / h["n"] if h["n"] else None
        h["brier"] = h["brier"] / h["n"] if h["n"] else None
        h["paper_pnl"] = round(h["paper_pnl"], 4)
    counts = {s: sum(t["state"] == s for t in tiles) for s in ("correct", "wrong", "pending", "skipped")}
    return dict(source=SOURCE, generated_at=now, current=current, last_24h=tiles, last_24h_counts=counts,
                by_hour=[hours[h] for h in range(24)], timezone="America/Los_Angeles (computed)")
