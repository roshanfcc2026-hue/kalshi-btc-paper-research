import unittest
from quant import runner, monitor, store as qstore, kalshi_book as kb, proxy


class RunnerTests(unittest.TestCase):
    def test_records_benchmark_without_combo_and_never_trades(self):
        d = qstore.connect(':memory:'); monitor.setup(d)
        kb.store(d, 'M', 1000, 0.1, 1600, 100, [(0.4, 5)], [(0.5, 5)])
        runner.step(d, 1005, {}, None)
        (src, p), = d.execute('SELECT source,p_yes FROM forecasts').fetchall(); self.assertEqual(src, 'market-mid'); self.assertAlmostEqual(p, 0.45)
        self.assertEqual(d.execute('SELECT COUNT(*) FROM paper_decisions').fetchone()[0], 0)
        runner.step(d, 1010, {}, None); self.assertEqual(d.execute('SELECT COUNT(*) FROM forecasts').fetchone()[0], 1)  # first call only
    def test_missed_first_call_not_backfilled_and_settles_after_close(self):
        d = qstore.connect(':memory:'); monitor.setup(d)
        kb.store(d, 'M', 1000, 0.1, 1600, 100, [(0.4, 5)], [(0.5, 5)])
        runner.step(d, 1100, {'M': 'yes'}, None)
        self.assertEqual(d.execute('SELECT COUNT(*) FROM forecasts').fetchone()[0], 0)
        self.assertEqual(d.execute('SELECT COUNT(*) FROM market_results').fetchone()[0], 0)  # not closed yet
        runner.step(d, 1700, {'M': 'yes'}, None)
        self.assertEqual(d.execute('SELECT result FROM market_results').fetchone()[0], 'yes')


if __name__ == '__main__':
    unittest.main()
