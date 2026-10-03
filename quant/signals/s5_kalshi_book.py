"""S5 Kalshi book imbalance (top 5) and YES-midpoint drift over the last 3 minutes."""
from .common import market_mid

NAME, VERSION = 's5_kalshi_book', 'v1'
REASON = 'Informed Kalshi traders show up as one-sided resting depth and as mid moves the slow-updating market has not finished.'
PARAMS = dict(levels=5, max_age=20.0, drift_s=180.0, drift_tolerance=60.0)
FEATURES, PROB_FEATURE = ('s5_imb', 's5_drift'), None


def compute(snap, params=PARAMS):
    k = snap['kalshi']
    if not k or snap['t'] - k[-1]['recv_ts'] > params['max_age']:
        return None
    cur = k[-1]; mid = market_mid(cur)
    y = sum(q for _, q in cur['yes'][:params['levels']]); n = sum(q for _, q in cur['no'][:params['levels']])
    target = snap['t'] - params['drift_s']
    old = [s for s in k if target - params['drift_tolerance'] <= s['recv_ts'] <= target]
    old_mid = market_mid(old[-1]) if old else None
    if mid is None or old_mid is None or y + n == 0:
        return None
    return {'s5_imb': (y - n) / (y + n), 's5_drift': mid - old_mid}
