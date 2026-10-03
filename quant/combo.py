"""Layer 3 signal combination: logit(p) = a + b*logit(p_market_mid) + sum(c_i * signal_i).

Ridge logistic regression fit on the TRAINING CHECKPOINT ONLY (first calls, one row per market).
Signals are standardized with training means/sds (stored in the model). Market-grouped bootstrap
(one row per market, so resampling rows = resampling markets) gives 95% intervals. Signals whose
interval includes zero are dropped and the model is refit, isotonic-calibrated on the same training
rows, and frozen as combo-v1 with code hashes. Frozen files are never overwritten.
CLI: python -m quant.combo quant.sqlite research.sqlite [--train-markets 200]
"""
import hashlib, json, random, sys, time
from pathlib import Path
from . import freeze, isotonic, logit as lg
from .signals import ALL
from .signals.common import logit as to_logit

DEFAULT_OUT = Path(__file__).resolve().parent / 'registry' / 'combo-v1.json'
CANDIDATES = ('s1_z', 's2_imb', 's3_r1', 's3_r3', 's3_r5', 's4_dev', 's5_imb', 's5_drift')
MIN_ROWS = 100


def complete(rows, features):
    return [r for r in rows if all(r['features'].get(f) is not None for f in features)]


def _design(rows, features, scale):
    return [[to_logit(r['p_market'])] + [(r['features'][f] - m) / sd for f, (m, sd) in zip(features, scale)] for r in rows]


def fit_once(rows, features, l2):
    scale = lg.standardize([[r['features'][f] for f in features] for r in rows]) if features else []
    X = _design(rows, features, scale)
    # penalize everything except the intercept; b (market) shrinks toward 0 too, kept small via l2
    return lg.fit(X, [r['y'] for r in rows], None, l2), scale


def bootstrap(rows, features, l2, n_boot=500, seed=11):
    rng = random.Random(seed); draws = []
    for _ in range(n_boot):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        if len({r['y'] for r in sample}) < 2: continue
        draws.append(fit_once(sample, features, l2)[0])
    names = ['intercept', 'b_market_logit'] + list(features); out = {}
    for j, name in enumerate(names):
        v = sorted(d[j] for d in draws)
        out[name] = (v[int(0.025 * (len(v) - 1))], v[int(0.975 * (len(v) - 1))])
    return out


def train(rows, features=CANDIDATES, l2=1.0, n_boot=500, seed=11):
    rows = complete(sorted(rows, key=lambda r: r['t']), features)
    tickers = [r['ticker'] for r in rows]
    if len(set(tickers)) != len(tickers): raise ValueError('one first call per market required')
    if len(rows) < MIN_ROWS: raise ValueError('need %d complete first-call markets; have %d' % (MIN_ROWS, len(rows)))
    w_full, _ = fit_once(rows, features, l2)
    ci_full = bootstrap(rows, features, l2, n_boot, seed)
    kept = [f for f in features if not (ci_full[f][0] <= 0 <= ci_full[f][1])]
    w, scale = fit_once(rows, kept, l2)
    ci = bootstrap(rows, kept, l2, n_boot, seed)
    raw = [lg.predict(w, x) for x in _design(rows, kept, scale)]
    y = [r['y'] for r in rows]
    steps = isotonic.fit(raw, y)
    cal = [isotonic.apply(steps, p) for p in raw]
    pm = [r['p_market'] for r in rows]
    mean = lambda ps: sum(lg.log_loss(p, t) for p, t in zip(ps, y)) / len(y)
    return dict(name='combo-v1', features=kept, dropped=[f for f in features if f not in kept], l2=l2,
                coefficients=dict(zip(['intercept', 'b_market_logit'] + kept, w)),
                ci95=ci, initial_fit=dict(coefficients=dict(zip(['intercept', 'b_market_logit'] + list(features), w_full)), ci95=ci_full),
                scale={f: s for f, s in zip(kept, scale)}, isotonic=steps,
                training=dict(markets=len(rows), first_ts=rows[0]['t'], last_ts=rows[-1]['t'],
                              tickers_sha256=hashlib.sha256('\n'.join(tickers).encode()).hexdigest(),
                              in_sample_log_loss=dict(market=mean(pm), raw=mean(raw), calibrated=mean(cal)),
                              reliability_raw=isotonic.reliability(raw, y), reliability_calibrated=isotonic.reliability(cal, y),
                              note='IN-SAMPLE on training data; not evidence of out-of-sample skill'))


def predict(model, p_market, features):
    x = [to_logit(p_market)] + [(features[f] - m) / sd for f, (m, sd) in model['scale'].items()]
    w = [model['coefficients'][k] for k in ['intercept', 'b_market_logit'] + model['features']]
    return isotonic.apply(model['isotonic'], lg.predict(w, x))


def code_hashes():
    import inspect, quant.combo as c
    h = lambda m: hashlib.sha256(Path(inspect.getfile(m)).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    return dict(combo=h(c), isotonic=h(isotonic), logit=h(lg), signals=freeze.entries(ALL))


def freeze_model(model, path=DEFAULT_OUT):
    path = Path(path)
    if path.exists(): raise freeze.FrozenMismatch('%s exists; frozen models are never overwritten' % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(model, frozen_at=time.time(), code=code_hashes()), indent=2, sort_keys=True) + '\n')


def verify_model(path=DEFAULT_OUT):
    doc = json.loads(Path(path).read_text())
    if doc['code'] != json.loads(json.dumps(code_hashes())):
        raise freeze.FrozenMismatch('combo code or signals changed since combo-v1 was frozen')
    return doc


if __name__ == '__main__':
    from . import dataset, store
    a = sys.argv[1:]; n = int(a[a.index('--train-markets') + 1]) if '--train-markets' in a else 200
    freeze.verify()
    rows = dataset.build_rows(store.connect(a[0]), dataset.outcomes_from_bot_db(a[1]))[:n]  # chronological training checkpoint
    m = train(rows); freeze_model(m)
    print(json.dumps({k: m[k] for k in ('features', 'dropped', 'coefficients', 'ci95')}, indent=2))
    print(json.dumps(m['training'], indent=2))
