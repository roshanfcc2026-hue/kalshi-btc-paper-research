"""S2 exchange order-book imbalance, top-5 levels, averaged across fresh exchanges."""
NAME, VERSION = 's2_exchange_imbalance', 'v1'
REASON = 'More resting bid than ask depth means buyers absorb sell flow more easily, a known short-horizon price-pressure effect.'
PARAMS = dict(levels=5, max_age=10.0, min_exchanges=2)
FEATURES, PROB_FEATURE = ('s2_imb',), None


def compute(snap, params=PARAMS):
    vals = []
    for book in snap['books'].values():
        if snap['t'] - book['recv_ts'] > params['max_age']:
            continue
        b = sum(s for _, s in book['bids'][:params['levels']]); a = sum(s for _, s in book['asks'][:params['levels']])
        if b + a > 0:
            vals.append((b - a) / (b + a))
    return {'s2_imb': sum(vals) / len(vals)} if len(vals) >= params['min_exchanges'] else None
