"""S1 volatility-distance: distance to target / EWMA volatility, Student-t probability.

RECONSTRUCTION: the user's `ewma-t-v2` (challenger_v2.py) was not available. This follows the
described method and the existing volatility-proxy-v1 horizon formula; replace if they differ.
"""
import math
from .common import minute_closes, ewma_sigma, t_cdf, latest

NAME, VERSION = 's1_vol_distance', 'ewma-t-recon-v1'
REASON = 'A binary pays if price ends above target; how many volatility-units away it is now is the core driver of that chance.'
PARAMS = dict(lam=0.94, df=5, min_returns=30, max_spot_age=15.0)
FEATURES, PROB_FEATURE = ('s1_z', 's1_p'), 's1_p'


def compute(snap, params=PARAMS):
    t, remain = snap['t'], snap['close_ts'] - snap['t']
    if not 60 < remain <= 900 or snap['strike'] <= 0:
        return None
    price = latest(snap['proxy'], t, params['max_spot_age'])
    closes = minute_closes(snap['proxy'], t)
    if price is None or len(closes) < params['min_returns'] + 1:
        return None
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    sigma = max(ewma_sigma(returns, params['lam']), 1e-6)
    # Variance of the final-minute average for remaining T>60s: sigma^2 * (T - 40)/60 (same as live.estimate).
    z = math.log(price / snap['strike']) / (sigma * math.sqrt((remain - 40) / 60))
    df = params['df']
    p = min(0.99, max(0.01, t_cdf(z * math.sqrt(df / (df - 2)), df)))  # unit-variance Student-t
    return {'s1_z': max(-4.0, min(4.0, z)), 's1_p': p}
