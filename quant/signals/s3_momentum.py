"""S3 short-term momentum: 1, 3, 5-minute spot-proxy log returns in basis points."""
from .common import price_at, latest, bps

NAME, VERSION = 's3_momentum', 'v1'
REASON = 'Large orders are split over minutes and news diffuses gradually across venues, which can produce short continuation.'
PARAMS = dict(horizons=(1, 3, 5), tolerance=15.0)
FEATURES, PROB_FEATURE = ('s3_r1', 's3_r3', 's3_r5'), None


def compute(snap, params=PARAMS):
    now = latest(snap['proxy'], snap['t'], params['tolerance'])
    if now is None:
        return None
    out = {}
    for k in params['horizons']:
        then = price_at(snap['proxy'], snap['t'] - 60 * k, params['tolerance'])
        if then is None:
            return None
        out['s3_r%d' % k] = bps(now / then)
    return out
