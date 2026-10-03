"""Standalone predictive value of each signal on the TRAINING set (first calls only).

For each feature:
  corr_outcome  - Pearson correlation of feature with outcome (0/1)
  corr_residual - correlation with (outcome - market midpoint): information the market lacks
  walk-forward log-loss improvement over the market midpoint: model logit(p)=logit(mid)+a+c*feature,
  fit on earlier blocks only, scored on the next block (no look-ahead). Positive = better than market.
For probability signals, also the raw log loss of the signal's own probability vs the midpoint.
"""
import math
from . import freeze, logit as lg
from .signals import ALL
from .signals.common import logit as to_logit

MIN_ROWS = 50


def _corr(a, b):
    n = len(a); ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a); vb = sum((x - mb) ** 2 for x in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def first_calls_only(rows):
    tickers = [r['ticker'] for r in rows]
    if len(set(tickers)) != len(tickers):
        raise ValueError('more than one call per market; score first calls only')
    return sorted(rows, key=lambda r: r['t'])


def evaluate_feature(rows, name, prob=False, blocks=5, l2=1.0):
    rows = [r for r in first_calls_only(rows) if r['features'].get(name) is not None]
    if len(rows) < MIN_ROWS:
        return dict(feature=name, n=len(rows), status='insufficient (need %d)' % MIN_ROWS)
    x = [r['features'][name] for r in rows]; y = [r['y'] for r in rows]
    out = dict(feature=name, n=len(rows), status='ok', corr_outcome=_corr(x, y),
               corr_residual=_corr(x, [r['y'] - r['p_market'] for r in rows]))
    size = len(rows) // blocks; per_block = []
    for i in range(1, blocks):
        train, test = rows[:i * size], rows[i * size:(i + 1) * size if i < blocks - 1 else len(rows)]
        (m, sd), = lg.standardize([[r['features'][name]] for r in train])
        w = lg.fit([[(r['features'][name] - m) / sd] for r in train], [r['y'] for r in train],
                   [to_logit(r['p_market']) for r in train], l2)
        ll_m = sum(lg.log_loss(r['p_market'], r['y']) for r in test) / len(test)
        ll_s = sum(lg.log_loss(lg.predict(w, [(r['features'][name] - m) / sd], to_logit(r['p_market'])), r['y']) for r in test) / len(test)
        per_block.append(dict(train=len(train), test=len(test), ll_market=ll_m, ll_model=ll_s, improvement=ll_m - ll_s))
    out['blocks'] = per_block
    total = sum(b['test'] for b in per_block)
    out['improvement'] = sum(b['improvement'] * b['test'] for b in per_block) / total
    if prob:
        out['ll_market_all'] = sum(lg.log_loss(r['p_market'], r['y']) for r in rows) / len(rows)
        out['ll_signal_prob_all'] = sum(lg.log_loss(r['features'][name], r['y']) for r in rows) / len(rows)
    return out


def evaluate_all(rows, signals=ALL, registry=freeze.DEFAULT_REGISTRY):
    freeze.verify(registry, signals)  # refuse to evaluate unfrozen or edited code
    out = {}
    for s in signals:
        out[s.NAME] = dict(version=s.VERSION, reason=s.REASON,
                           features=[evaluate_feature(rows, f, prob=(f == s.PROB_FEATURE)) for f in s.FEATURES])
    return out


if __name__ == '__main__':
    # python -m quant.evaluate_signal quant.sqlite research.sqlite   (research.sqlite opened read-only)
    import json, sys
    from . import dataset, store
    qdb = store.connect(sys.argv[1]); rows = dataset.build_rows(qdb, dataset.outcomes_from_bot_db(sys.argv[2]))
    print(json.dumps(dict(first_call_markets=len(rows), signals=evaluate_all(rows)), indent=2))
