import unittest
from quant import asof, store as qstore, kalshi_book, dataset


def seed(db):
    for ts, p in ((90, 100.0), (100, 101.0), (110, 999.0)):  # ts=110 is in the future at t=100
        db.execute('INSERT INTO spot_proxy(ts,price,n_used,n_total,status) VALUES(?,?,4,4,"ok")', (ts, p))
        db.execute('INSERT INTO exchange_ticks(exchange,exch_ts,recv_ts,price) VALUES("a",?,?,?)', (ts - 1, ts, p))
        db.execute('INSERT INTO exchange_books(exchange,exch_ts,recv_ts,side,level,price,size) VALUES("a",NULL,?,"bid",0,?,?)', (ts, p, ts))
    kalshi_book.store(db, 'M', 50, 0.1, 50 + 800, 100.0, [(0.4, 1)], [(0.5, 1)])     # 800s to close: first call
    kalshi_book.store(db, 'M', 100, 0.1, 850, 100.0, [(0.45, 1)], [(0.5, 1)])
    kalshi_book.store(db, 'M', 105, 0.1, 850, 100.0, [(0.9, 1)], [(0.05, 1)])         # future at t=100
    kalshi_book.store(db, 'LATE', 100, 0.1, 130, 100.0, [(0.4, 1)], [(0.5, 1)])      # 30s to close: never eligible


class AsOfTests(unittest.TestCase):
    def test_no_lookahead_anywhere(self):
        db = qstore.connect(':memory:'); seed(db)
        s = asof.snapshot(db, 'M', 100)
        self.assertEqual(s['proxy'][-1], (100, 101.0)); self.assertEqual(s['ticks']['a'][-1], (100, 101.0))
        self.assertEqual(s['books']['a']['recv_ts'], 100); self.assertEqual(s['kalshi'][-1]['yes'], [(0.45, 1)])
        self.assertTrue(all(r[0] <= 100 for r in s['proxy']))
    def test_no_snapshot_before_any_kalshi_data(self):
        db = qstore.connect(':memory:'); seed(db); self.assertIsNone(asof.snapshot(db, 'M', 10))
    def test_first_call_times(self):
        db = qstore.connect(':memory:'); seed(db)
        self.assertEqual(asof.first_call_times(db), {'M': 50})
    def test_build_rows_one_per_market_and_readonly_outcomes(self):
        import sqlite3, tempfile, os
        db = qstore.connect(':memory:'); seed(db)
        rows = dataset.build_rows(db, {'M': 'yes'})
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['t'], 50); self.assertAlmostEqual(rows[0]['p_market'], 0.45)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'research.sqlite'); b = sqlite3.connect(p)
            b.execute('CREATE TABLE markets(ticker TEXT, metadata TEXT, result TEXT)'); b.execute("INSERT INTO markets VALUES('M','{}','yes'),('N','{}','')"); b.commit(); b.close()
            self.assertEqual(dataset.outcomes_from_bot_db(p), {'M': 'yes'})
            before = os.path.getmtime(p); dataset.outcomes_from_bot_db(p); self.assertEqual(os.path.getmtime(p), before)


if __name__ == '__main__':
    unittest.main()
