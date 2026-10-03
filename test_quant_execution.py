import unittest
from quant import execution as ex, kalshi_book as kb, risk, store as qstore

T = 10000.0


def db():
    d = qstore.connect(':memory:'); ex.setup(d); return d


class TakerTests(unittest.TestCase):
    def test_fills_at_recorded_ask_and_logs(self):
        d = db(); kb.store(d, 'M', T - 2, 0.1, T + 600, 100, [(0.40, 50)], [(0.55, 50)])
        r = ex.taker(d, 'M', 'combo-v1', 0.80, T)
        self.assertEqual(r['action'], 'yes'); self.assertEqual(r['fill']['fills'][0][0], 0.45)
        row = d.execute('SELECT action,avg_price,book_age FROM paper_decisions').fetchone()
        self.assertEqual((row[0], row[2]), ('yes', 2.0)); self.assertAlmostEqual(row[1], 0.45)
    def test_stale_or_future_book_not_used(self):
        d = db(); kb.store(d, 'M', T - 30, 0.1, T + 600, 100, [(0.40, 50)], [(0.55, 50)])
        kb.store(d, 'M', T + 1, 0.1, T + 600, 100, [(0.40, 50)], [(0.55, 50)])
        self.assertTrue(ex.taker(d, 'M', 's', 0.8, T)['reason'].startswith('no Kalshi book'))
        self.assertEqual(d.execute("SELECT action FROM paper_decisions").fetchone()[0], 'skip')
    def test_skip_is_logged(self):
        d = db(); kb.store(d, 'M', T - 1, 0.1, T + 600, 100, [(0.40, 50)], [(0.55, 50)])
        ex.taker(d, 'M', 's', 0.46, T)
        self.assertEqual(ex.report(d)['taker']['skips'], 1)


class PassiveTests(unittest.TestCase):
    def seed(self, later_no_bid):
        d = db(); kb.store(d, 'M', T - 1, 0.1, T + 600, 100, [(0.40, 50)], [(0.55, 50)])  # yes bid .40, yes ask .45
        kb.store(d, 'M', T + 60, 0.1, T + 600, 100, [(0.38, 50)], [(later_no_bid, 50)])
        return d
    def test_posts_inside_spread(self):
        d = self.seed(0.55); r = ex.post_passive(d, 'M', 's', 0.80, T)
        self.assertEqual((r['side'], r['limit']), ('yes', 0.41))
    def test_filled_only_when_later_quote_crosses(self):
        d = self.seed(0.59)  # later YES ask .41 == our limit -> crossed
        ex.post_passive(d, 'M', 's', 0.80, T); ex.resolve_passive(d, 'M', 'yes', T + 600)
        filled, pnl = d.execute('SELECT filled,pnl FROM passive_orders').fetchone()
        self.assertEqual(filled, 1); self.assertAlmostEqual(pnl, 1 - 0.41 - 0.01)
        d2 = self.seed(0.55); ex.post_passive(d2, 'M', 's', 0.80, T); ex.resolve_passive(d2, 'M', 'yes', T + 600)
        self.assertEqual(d2.execute('SELECT filled,pnl FROM passive_orders').fetchone(), (0, 0))
    def test_quotes_after_close_ignored_and_no_cross_posting(self):
        d = db(); kb.store(d, 'M', T - 1, 0.1, T + 600, 100, [(0.40, 50)], [(0.59, 50)])  # 1c spread: bid+1c would cross
        self.assertIsNone(ex.post_passive(d, 'M', 's', 0.9, T))
        d = self.seed(0.55); kb.store(d, 'M', T + 700, 0.1, T + 600, 100, [(0.1, 5)], [(0.9, 5)])
        ex.post_passive(d, 'M', 's', 0.8, T); ex.resolve_passive(d, 'M', 'yes', T + 600)
        self.assertEqual(d.execute('SELECT filled FROM passive_orders').fetchone()[0], 0)
    def test_report_separates_and_warns(self):
        d = self.seed(0.59); ex.post_passive(d, 'M', 's', 0.8, T); ex.resolve_passive(d, 'M', 'yes', T + 600)
        r = ex.report(d)['passive_research']; self.assertEqual(r['fill_rate'], 1.0); self.assertIn('OPTIMISTIC', r['warning'])


if __name__ == '__main__':
    unittest.main()
