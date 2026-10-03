import unittest
from quant import costs
import bot


class FeeTests(unittest.TestCase):
    def test_known_values_and_rounding_up(self):
        self.assertEqual(costs.fee([(0.5, 1)]), 0.02)       # 0.0175 -> 0.02
        self.assertEqual(costs.fee([(0.5, 100)]), 1.75)
        self.assertEqual(costs.fee([(0.3, 10)]), costs.fee([(0.7, 10)]))  # symmetric
        self.assertEqual(costs.fee([(0.99, 1)]), 0.01)
        self.assertEqual(costs.fee([]), 0.0)
    def test_matches_existing_bot_fee_for_one_contract(self):
        for p in (0.01, 0.1, 0.33, 0.5, 0.77, 0.99):
            self.assertAlmostEqual(costs.fee([(p, 1)]), bot.fee(p, 0.07))
    def test_aggregate_rounding_is_cheaper_than_per_level(self):
        f = [(0.5, 1), (0.51, 1)]
        self.assertLessEqual(costs.fee(f), costs.fee(f[:1]) + costs.fee(f[1:]))
    def test_maker_rate_is_quarter_and_flag_unverified(self):
        self.assertAlmostEqual(costs.MAKER_RATE, costs.TAKER_RATE / 4); self.assertFalse(costs.FEE_SOURCE_VERIFIED)


class FillTests(unittest.TestCase):
    def test_asks_derived_from_opposite_bids(self):
        yes, no = [(0.45, 3), (0.44, 5)], [(0.52, 2), (0.50, 4)]
        self.assertEqual(costs.ask_levels(yes, no, 'yes'), [(0.48, 2), (0.50, 4)])
        self.assertEqual(costs.ask_levels(yes, no, 'no'), [(0.55, 3), (0.56, 5)])
    def test_walks_to_next_level_when_size_exceeds_display(self):
        r = costs.fill([(0.48, 2), (0.50, 4)], 5)
        self.assertEqual(r['fills'], [(0.48, 2), (0.50, 3)]); self.assertEqual(r['unfilled'], 0)
        self.assertAlmostEqual(r['notional'], 2.46); self.assertEqual(r['best_size'], 2); self.assertEqual(r['levels_used'], 2)
        self.assertAlmostEqual(r['total_cost'], 2.46 + costs.fee(r['fills']))
    def test_insufficient_depth_reports_unfilled(self):
        r = costs.fill([(0.48, 2)], 5); self.assertEqual((r['filled'], r['unfilled']), (2, 3))
        r = costs.fill([], 1); self.assertEqual(r['filled'], 0); self.assertIsNone(r['avg_price'])
    def test_ev(self):
        r = costs.fill([(0.40, 10)], 1); self.assertAlmostEqual(costs.expected_value(0.5, r['cost_per_contract']), 0.5 - 0.42)
        with self.assertRaises(ValueError): costs.fill([(0.4, 1)], 0)


if __name__ == '__main__':
    unittest.main()
