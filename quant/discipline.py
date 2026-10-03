"""Layer 8 research discipline.

walk_forward     train on markets 1..N, test the next block, roll forward; EVERY block reported.
grouped_bootstrap 95% CI of a mean over MARKETS (resample whole markets, never individual rows).
decay            each signal's coefficient over rolling 7-day windows; flags sign flips / >50% shrinkage.
promotion        a source is a "promotion candidate" only with >=500 NEW forward markets (after its
                 freeze time), log-loss-vs-market 95% CI entirely below 0 AND paper P&L after fees with
                 95% CI entirely above 0. This function NEVER promotes anything; it only reports.
"""
import json, math, random
from . import combo, isotonic, logit as lg

PROMOTION_MIN_MARKETS = 500


def _ll(p, y): return lg.log_loss(p, y)


def walk_forward(rows, features, initial=200, block=100, l2=1.0):
    rows = combo.complete(sorted(rows, key=lambda r: r['t']), features); out = []
    n = initial
    while n + 1 <= len(rows):
        train, test = rows[:n], rows[n:n + block]
        if len({r['y'] for r in train}) < 2: n += block; continue
        w, scale = combo.fit_once(train, features, l2)
        steps = isotonic.fit([lg.predict(w, x) for x in combo._design(train, features, scale)], [r['y'] for r in train])
        pred = [isotonic.apply(steps, lg.predict(w, x)) for x in combo._design(test, features, scale)]
        diffs = [_ll(p, r['y']) - _ll(r['p_market'], r['y']) for p, r in zip(pred, test)]
        out.append(dict(train_markets=n, test_markets=len(test), test_first_ts=test[0]['t'], test_last_ts=test[-1]['t'],
                        ll_model=sum(_ll(p, r['y']) for p, r in zip(pred, test)) / len(test),
                        ll_market=sum(_ll(r['p_market'], r['y']) for r in test) / len(test),
                        ll_diff=sum(diffs) / len(diffs), ll_diff_ci95=grouped_bootstrap({r['ticker']: [d] for r, d in zip(test, diffs)})))
        n += block
    return dict(blocks=out, blocks_beating_market=sum(b['ll_diff'] < 0 for b in out), total_blocks=len(out),
                note='Every block is listed; judge consistency, not only the average.')


def grouped_bootstrap(values_by_market, n_boot=2000, seed=17, stat='mean'):
    """values_by_market: {ticker: [values]} -> per-market total; CI of the mean (or sum) over markets."""
    totals = [sum(v) for v in values_by_market.values()]
    if len(totals) < 2: return None
    rng = random.Random(seed); k = len(totals); draws = []
    for _ in range(n_boot):
        s = sum(totals[rng.randrange(k)] for _ in range(k)); draws.append(s / k if stat == 'mean' else s)
    draws.sort()
    return (draws[int(0.025 * (n_boot - 1))], draws[int(0.975 * (n_boot - 1))])


def decay(rows, features, window_days=7, step_days=1, l2=1.0, min_rows=50):
    rows = combo.complete(sorted(rows, key=lambda r: r['t']), features)
    if not rows: return dict(windows=[], flags=[])
    W, S = window_days * 86400, step_days * 86400; start = rows[0]['t']; windows = []
    while start + W <= rows[-1]['t'] + S:
        g = [r for r in rows if start <= r['t'] < start + W]
        if len(g) >= min_rows and len({r['y'] for r in g}) == 2:
            w, _ = combo.fit_once(g, features, l2)
            windows.append(dict(start=start, end=start + W, markets=len(g), coefficients=dict(zip(features, w[2:]))))
        start += S
    flags = []
    if len(windows) >= 2:
        for f in features:
            first, last = windows[0]['coefficients'][f], windows[-1]['coefficients'][f]
            if first * last < 0: flags.append(dict(feature=f, kind='sign flip', first=first, last=last))
            elif abs(last) < 0.5 * abs(first): flags.append(dict(feature=f, kind='decayed >50%', first=first, last=last))
    return dict(windows=windows, flags=flags)


def promotion(db, source, frozen_at):
    """Report only. Forward markets: first calls made strictly after the source was frozen."""
    rows = db.execute('''SELECT f.ticker,f.p_yes,f.p_market,r.result FROM forecasts f JOIN market_results r USING(ticker)
                         WHERE f.source=? AND f.t>?''', (source, frozen_at)).fetchall()
    ll = {t: [_ll(p, res == 'yes') - _ll(pm, res == 'yes')] for t, p, pm, res in rows}
    pnl = {t: [0.0] for t in ll}  # markets the source skipped count as 0 P&L
    for t, v in db.execute('SELECT ticker,pnl FROM paper_positions WHERE source=? AND pnl IS NOT NULL AND opened>?', (source, frozen_at)):
        if t in pnl: pnl[t].append(v)
    ll_ci, pnl_ci = grouped_bootstrap(ll), grouped_bootstrap(pnl, stat='sum')
    reasons = []
    if len(ll) < PROMOTION_MIN_MARKETS: reasons.append('only %d forward markets (need %d)' % (len(ll), PROMOTION_MIN_MARKETS))
    if ll_ci is None or ll_ci[1] >= 0: reasons.append('log-loss vs market CI not entirely below 0: %s' % (ll_ci,))
    if pnl_ci is None or pnl_ci[0] <= 0: reasons.append('paper P&L CI not entirely above 0: %s' % (pnl_ci,))
    return dict(source=source, forward_markets=len(ll), ll_diff_mean=sum(v[0] for v in ll.values()) / len(ll) if ll else None,
                ll_diff_ci95=ll_ci, pnl_total=sum(sum(v) for v in pnl.values()), pnl_total_ci95=pnl_ci,
                status='promotion candidate' if not reasons else 'not a candidate', reasons=reasons,
                auto_promoted=False, note='Never auto-promoted. A candidate still requires your manual review; no profit is implied.')


if __name__ == '__main__':
    # python -m quant.discipline quant.sqlite research.sqlite
    import sys
    from pathlib import Path
    from . import dataset, monitor, store
    qdb = store.connect(sys.argv[1]); monitor.setup(qdb)
    rows = dataset.build_rows(qdb, dataset.outcomes_from_bot_db(sys.argv[2]))
    out = dict(markets=len(rows))
    if Path(combo.DEFAULT_OUT).exists():
        m = combo.verify_model(); feats = m['features']
        out['walk_forward'] = walk_forward(rows, feats); out['decay'] = decay(rows, feats)
        out['promotion'] = promotion(qdb, 'combo-v1', m['frozen_at'])
    else:
        out['status'] = 'combo-v1 not frozen yet; run python -m quant.combo first'
    print(json.dumps(out, indent=2, default=str))
