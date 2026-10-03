"""Build one FIRST-CALL row per market: features from every signal at the market's first eligible
forecast time, the Kalshi midpoint at that same time, and the official outcome.
Outcomes are read READ-ONLY from the bot's research.sqlite (never written)."""
import sqlite3
from pathlib import Path
from . import asof
from .signals import ALL
from .signals.common import market_mid


def outcomes_from_bot_db(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        return {t: r for t, r in db.execute("SELECT ticker,result FROM markets WHERE result IN ('yes','no')")}
    finally:
        db.close()


def build_rows(qdb, outcomes, signals=ALL):
    rows = []
    for ticker, t in sorted(asof.first_call_times(qdb).items(), key=lambda kv: kv[1]):
        if ticker not in outcomes:
            continue
        snap = asof.snapshot(qdb, ticker, t)
        pm = market_mid(snap['kalshi'][-1]) if snap and snap['kalshi'] else None
        if pm is None or not 0 < pm < 1:
            continue
        feats = {}
        for s in signals:
            v = s.compute(snap)
            for name in s.FEATURES:
                feats[name] = v.get(name) if v else None
        rows.append(dict(ticker=ticker, t=t, p_market=pm, y=int(outcomes[ticker] == 'yes'), features=feats))
    return rows
