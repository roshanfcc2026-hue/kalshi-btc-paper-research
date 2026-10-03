import unittest
from quant import risk, store as qstore

DAY = 86400.0 * 100
DEEP_NO = [(0.55, 1000)]  # YES ask 0.45, deep


def db():
    d = qstore.connect(':memory:'); risk.setup(d); return d


class SizingTests(unittest.TestCase):
    def test_kelly(self):
        self.assertAlmostEqual(risk.kelly(0.6, 0.5), 0.2); self.assertEqual(risk.kelly(0.4, 0.5), 0.0)
    def test_skip_when_edge_below_margin_after_fees(self):
        self.assertEqual(risk.size(0.48, [(0.40, 100)], DEEP_NO, 1000, 30)['action'], 'skip')  # 0.48-0.45-fee < .03
    def test_quarter_kelly_capped_at_one_percent(self):
        d = risk.size(0.80, [(0.40, 100)], DEEP_NO, 1000, 30)
        self.assertEqual(d['action'], 'yes'); self.assertLessEqual(d['fill']['total_cost'], 10.0)  # 1% of 1000
        small = risk.size(0.53, [(0.40, 100)], DEEP_NO, 1000, 30)  # kelly ~0.13 -> quarter 3.3% -> still cap? cost check
        self.assertLessEqual(small['fill']['total_cost'], 0.25 * small['kelly'] * 1000 + 1e-9)
    def test_picks_no_side(self):
        self.assertEqual(risk.size(0.20, [(0.40, 100)], DEEP_NO, 1000, 30)['action'], 'no')  # NO ask .60, p_no .80
    def test_thin_book_shrinks_size_to_keep_ev(self):
        d = risk.size(0.80, [], [(0.55, 3), (0.20, 100)], 1000, 30)  # second level is ask .80: no edge
        self.assertEqual(d['contracts'], 3)


class LimitTests(unittest.TestCase):
    def test_open_risk_cap_and_one_trade_per_market(self):
        d = db()
        for i in range(5): risk.decide(d, 'M%d' % i, 'combo-v1', 0.8, [], DEEP_NO, DAY)
        self.assertLessEqual(risk.open_risk(d), 30.0 + 1e-9)
        self.assertEqual(risk.decide(d, 'M0', 'combo-v1', 0.8, [], DEEP_NO, DAY)['reason'], 'already traded this market')
    def test_daily_loss_halts_until_manual_reset(self):
        d = db()
        d.execute("INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened,result,pnl,settled) VALUES('L','s','yes',1,50,1,?, 'no',-51,?)", (DAY, DAY + 10))
        self.assertEqual(risk.check_limits(d, risk.DEFAULTS, DAY + 20), 'daily loss limit')
        self.assertTrue(risk.decide(d, 'N', 's', 0.9, [], DEEP_NO, DAY + 86400 * 2)['reason'].startswith('halted'))  # still halted next days
        risk.reset(d, 'tester', DAY + 86400 * 2)
        self.assertFalse(risk.halted(d)[0])
        self.assertEqual([r[0] for r in d.execute('SELECT kind FROM risk_events ORDER BY id')], ['halt', 'manual_reset'])
    def test_weekly_loss_limit(self):
        d = db()
        for k in range(4):  # 4 days x -26 = -104 (~10.4%), each day under 5%
            d.execute("INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened,result,pnl,settled) VALUES(?,'s','yes',1,26,0,?,'no',-26,?)", ('W%d' % k, DAY + k * 86400, DAY + k * 86400))
        self.assertEqual(risk.check_limits(d, risk.DEFAULTS, DAY + 3 * 86400 + 5), 'weekly loss limit')
    def test_settlement_pnl_and_requires_official_result(self):
        d = db(); r = risk.decide(d, 'S', 's', 0.8, [], DEEP_NO, DAY)
        risk.settle(d, 'S', 'yes', DAY + 900)
        n, cost, fee, pnl = d.execute('SELECT contracts,cost,fee,pnl FROM paper_positions').fetchone()
        self.assertAlmostEqual(pnl, n - cost - fee); self.assertGreater(pnl, 0)
        with self.assertRaises(ValueError): risk.settle(d, 'S', 'void', DAY)


if __name__ == '__main__':
    unittest.main()
