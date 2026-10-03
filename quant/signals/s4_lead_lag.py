"""S4 cross-exchange lead-lag: where the fastest-moving exchange sits relative to the proxy (bps)."""
import math
from .common import price_at, latest, bps

NAME, VERSION = 's4_lead_lag', 'v1'
REASON = 'Price discovery happens first on whichever venue gets the informed flow; the median proxy lags it.'
PARAMS = dict(window=30.0, max_age=10.0, tolerance=15.0, min_exchanges=3)
FEATURES, PROB_FEATURE = ('s4_dev',), None


def compute(snap, params=PARAMS):
    t = snap['t']; proxy = latest(snap['proxy'], t, params['max_age'])
    moves = []
    for ex in sorted(snap['ticks']):
        series = snap['ticks'][ex]
        now, then = latest(series, t, params['max_age']), price_at(series, t - params['window'], params['tolerance'])
        if now is not None and then is not None:
            moves.append((abs(math.log(now / then)), ex, now))
    if proxy is None or len(moves) < params['min_exchanges']:
        return None
    _, _, fast = max(moves, key=lambda m: m[0])  # ties -> alphabetical first
    return {'s4_dev': bps(fast / proxy)}
