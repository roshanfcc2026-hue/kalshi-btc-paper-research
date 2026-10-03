import unittest
from quant import dq_report, store as qstore

D1 = 86400.0 * 10  # 1970-01-11 UTC


class DQTests(unittest.TestCase):
    def test_gaps_statuses_errors_and_days(self):
        db = qstore.connect(':memory:')
        for ts in (D1, D1 + 5, D1 + 100, D1 + 105):
            db.execute('INSERT INTO exchange_ticks(exchange,exch_ts,recv_ts,price) VALUES("coinbase",?,?,100)', (ts, ts))
        db.execute('INSERT INTO exchange_ticks(exchange,exch_ts,recv_ts,price) VALUES("kraken",NULL,?,100)', (D1 + 86400 + 1,))
        for status in ('ok', 'stale', 'outlier', 'outlier', 'missing'):
            db.execute('INSERT INTO proxy_inputs(ts,exchange,status) VALUES(?,"coinbase",?)', (D1 + 1, status))
        db.execute('INSERT INTO spot_proxy(ts,price,n_used,n_total,status) VALUES(?,NULL,1,4,"insufficient")', (D1 + 1,))
        db.execute('INSERT INTO feed_errors(ts,source,message) VALUES(?,"kalshi:KX-1","x")', (D1 + 2,))
        db.commit()
        r = dq_report.report(db)
        c = r['exchanges']['1970-01-11']['coinbase']
        self.assertEqual((c['ticks'], c['gaps'], c['max_gap_s'], c['missing_s']), (4, 1, 95.0, 95.0))
        self.assertEqual((c['stale'], c['outlier'], c['missing']), (1, 2, 1))
        self.assertEqual(r['exchanges']['1970-01-11']['kalshi']['errors'], 1)
        self.assertEqual(r['exchanges']['1970-01-12']['kraken']['ticks'], 1)
        self.assertEqual(r['days']['1970-01-11']['proxy_insufficient'], 1)
    def test_empty_database(self):
        self.assertEqual(dq_report.report(qstore.connect(':memory:')), dict(gap_threshold_s=30.0, exchanges={}, days={}))


if __name__ == '__main__':
    unittest.main()
