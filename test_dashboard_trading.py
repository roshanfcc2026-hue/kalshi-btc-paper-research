"""Paper-only guidance fixtures. Never access live data, accounts or orders."""
import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard_trading import build_trading_report


class TradingReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "fixture.sqlite"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript("""
          CREATE TABLE markets(ticker TEXT PRIMARY KEY, metadata TEXT, result TEXT);
          CREATE TABLE predictions(id INTEGER PRIMARY KEY,ticker TEXT,source TEXT,
            observed REAL,completed REAL,close_ts REAL,p_yes REAL,detail TEXT,
            result TEXT,correct INTEGER,brier REAL);
          CREATE TABLE snapshots(id INTEGER PRIMARY KEY,ticker TEXT,ts REAL,received REAL,
            yes_ask REAL,no_ask REAL,yes_size REAL,no_size REAL,features TEXT,raw TEXT);
        """)
        self.ticker = "KXBTC15M-FIXTURE"
        self.open = 1800000000
        self.close = self.open + 900
        self.observed = self.open + 100
        self.meta = {"open_time": self.iso(self.open), "close_time": self.iso(self.close), "status": "active"}
        self.db.execute("INSERT INTO markets VALUES(?,?,?)", (self.ticker, json.dumps(self.meta), ""))
        self.db.commit()

    def iso(self, value):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat().replace("+00:00", "Z")

    def add(self, ident=1, probability=.8, yes_bid=.49, no_bid=.50, observed=None, depth_yes=10, depth_no=20, changes=None):
        observed = self.observed if observed is None else observed
        yes_ask, no_ask = 1-no_bid, 1-yes_bid
        detail = {"p_yes": probability, "close_ts": self.close, "yes_ask": yes_ask, "no_ask": no_ask,
                  "target": 100, "features": [1, 0, .1, 0, .8, .2],
                  "spot": {"price": 101, "tick_ts": observed-1, "received": observed-.5,
                           "last_closed_candle": int(observed//60)*60-60,
                           "sigma_1m": .001, "momentum_5m": .002}}
        if changes:
            detail.update(changes)
        raw = {"orderbook_fp": {"yes_dollars": [[str(yes_bid), str(depth_yes)]],
                                "no_dollars": [[str(no_bid), str(depth_no)]]}}
        self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (ident, self.ticker, observed-.2, observed, yes_ask, no_ask,
                         depth_no, depth_yes, json.dumps([1,0,.1,0,.8]), json.dumps(raw)))
        self.db.execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (ident, self.ticker, "volatility-proxy-v1", observed, observed,
                         self.close, probability, json.dumps(detail), None, None, None))
        self.db.commit()

    def report(self, age=0):
        return build_trading_report(self.path, now=self.observed + age)

    def mutate_detail(self, edit):
        detail = json.loads(self.db.execute("SELECT detail FROM predictions ORDER BY id DESC LIMIT 1").fetchone()[0])
        edit(detail)
        self.db.execute("UPDATE predictions SET detail=? WHERE id=(SELECT MAX(id) FROM predictions)", (json.dumps(detail),))
        self.db.commit()

    def assert_skips(self, report):
        self.assertEqual({item["action"] for item in report["scenarios"].values()}, {"SKIP"})
        self.assertEqual(report["live_signal"], "SKIP")

    def test_buy_yes_one_contract_and_exact_depth_mapping(self):
        self.add()
        report = self.report()
        self.assertTrue(report["snapshot_valid"])
        self.assertTrue(report["current_quote_valid"])
        self.assertEqual(report["scenarios"]["none"]["action"], "BUY_YES")
        self.assertEqual(report["quotes"]["yes_bid_depth"], 10)
        self.assertEqual(report["quotes"]["yes_ask_depth"], 20)
        self.assertEqual(report["quotes"]["no_bid_depth"], 20)
        self.assertEqual(report["quotes"]["no_ask_depth"], 10)
        self.assertAlmostEqual(report["values"]["yes"]["buy_fee"], .02)
        self.assertAlmostEqual(report["values"]["yes"]["buy_ev"], .28)
        self.assertEqual(report["live_signal"], "SKIP")

    def test_buy_no_can_have_positive_edge_despite_majority_yes(self):
        self.add(probability=.6, yes_bid=.70, no_bid=.29)
        report = self.report()
        self.assertGreater(report["forecasts"]["p_yes"], .5)
        self.assertEqual(report["scenarios"]["none"]["action"], "BUY_NO")

    def test_direction_flip_does_not_force_exit_or_new_entry(self):
        self.add(probability=.49, yes_bid=.40, no_bid=.59)
        report = self.report()
        self.assertEqual(report["scenarios"]["yes"]["action"], "HOLD")
        self.assertEqual(report["scenarios"]["none"]["reason"], "weak_direction")

    def test_sell_uses_same_side_bid_and_ignores_entry_coinflip_gate(self):
        self.add(probability=.49, yes_bid=.80, no_bid=.19)
        report = self.report()
        self.assertEqual(report["scenarios"]["none"]["action"], "SKIP")
        self.assertEqual(report["scenarios"]["yes"]["action"], "SELL_YES")
        self.assertEqual(report["values"]["yes"]["sell_net"], .78)
        self.assertAlmostEqual(report["values"]["yes"]["sell_edge"], .29)
        self.assertEqual(report["scenarios"]["no"]["action"], "HOLD")

    def test_sell_signed_cent_rounding_and_buy_cent_rounding(self):
        self.add(probability=.8, yes_bid=.055, no_bid=.94)
        report = self.report()
        self.assertTrue(report["snapshot_valid"])
        yes = report["values"]["yes"]
        self.assertAlmostEqual(yes["sell_fee"], .005)
        self.assertEqual(yes["sell_net"], .05)
        self.assertAlmostEqual(yes["buy_fee"], .01)
        self.assertAlmostEqual(yes["buy_ev"], .73)

    def test_strict_margin_for_buy_and_sell(self):
        self.add(probability=.63, yes_bid=.56, no_bid=.42)
        report = self.report()
        self.assertEqual(report["values"]["yes"]["buy_ev"], .03)
        self.assertEqual(report["scenarios"]["none"]["action"], "SKIP")
        self.db.execute("DELETE FROM predictions")
        self.db.execute("DELETE FROM snapshots")
        self.add(probability=.5, yes_bid=.55, no_bid=.44)
        report = self.report()
        self.assertEqual(report["values"]["yes"]["sell_edge"], .03)
        self.assertEqual(report["scenarios"]["yes"]["action"], "HOLD")

    def test_quote_age_and_forecast_age_boundaries(self):
        self.add()
        self.assertTrue(self.report(3)["current_quote_valid"])
        stale = self.report(3.001)
        self.assertTrue(stale["snapshot_valid"])
        self.assertFalse(stale["current_quote_valid"])
        self.assert_skips(stale)
        self.assertEqual(stale["snapshot_scenarios"]["none"]["action"], "BUY_YES")
        self.assertTrue(self.report(45)["snapshot_valid"])
        old = self.report(45.001)
        self.assertFalse(old["snapshot_valid"])
        self.assertIsNone(old["values"])
        self.assert_skips(old)

    def test_final_minute_and_future_observation_fail_closed(self):
        self.add(observed=self.close-80)
        final = build_trading_report(self.path, now=self.close-60)
        self.assertEqual(final["reason"], "final_averaging_minute")
        self.assert_skips(final)
        future = build_trading_report(self.path, now=self.close-81)
        self.assertEqual(future["reason"], "future_or_invalid_forecast")
        self.assert_skips(future)

    def test_detail_probability_close_asks_mismatch(self):
        self.add()
        for field, value in (("p_yes", .7), ("close_ts", self.close+1), ("yes_ask", .6)):
            detail = json.loads(self.db.execute("SELECT detail FROM predictions").fetchone()[0])
            original = detail[field]
            self.mutate_detail(lambda d, field=field, value=value: d.update({field:value}))
            report = self.report()
            self.assertFalse(report["snapshot_valid"])
            self.assert_skips(report)
            self.mutate_detail(lambda d, field=field, original=original: d.update({field:original}))

    def test_invalid_newest_has_no_earlier_rescue_and_first_call_unchanged(self):
        self.add(ident=1, probability=.8)
        self.add(ident=2, probability=.1, observed=self.observed+10)
        good = self.report(10)
        self.assertEqual(good["first_call"]["forecast_id"], 1)
        self.assertEqual(good["first_call"]["p_yes"], .8)
        self.assertEqual(good["forecasts"]["p_yes"], .1)
        self.db.execute("UPDATE predictions SET p_yes='NaN' WHERE id=2")
        self.db.commit()
        invalid = self.report(10)
        self.assertEqual(invalid["forecast_id"], 2)
        self.assertEqual(invalid["first_call"], good["first_call"])
        self.assertFalse(invalid["snapshot_valid"])
        self.assert_skips(invalid)

    def test_missing_exact_snapshot_or_bad_raw_book_no_substitute(self):
        self.add()
        self.db.execute("UPDATE snapshots SET received=received-.01")
        self.db.commit()
        self.assert_skips(self.report())
        self.db.execute("UPDATE snapshots SET received=?,raw='{}'", (self.observed,))
        self.db.commit()
        self.assertFalse(self.report()["snapshot_valid"])
        self.assert_skips(self.report())

    def test_invalid_spot_and_insufficient_depth_rejected(self):
        self.add(depth_yes=.99)
        self.assertEqual(self.report()["reason"], "insufficient_one_contract_depth")
        self.db.execute("DELETE FROM predictions")
        self.db.execute("DELETE FROM snapshots")
        self.add()
        self.mutate_detail(lambda d: d["spot"].update(tick_ts=self.observed+1))
        self.assert_skips(self.report())
        self.assertFalse(self.report()["snapshot_valid"])

    def test_known_fee_overrides_reject_unverified_scenario(self):
        self.add()
        for change in ({"fee_waiver_expiration_time":"2026-10-02T00:00:00Z"},
                       {"fee_type":"flat"}, {"fee_multiplier":2}):
            metadata = dict(self.meta, **change)
            self.db.execute("UPDATE markets SET metadata=?", (json.dumps(metadata),))
            self.db.commit()
            with self.subTest(change=change):
                self.assert_skips(self.report())
                self.assertFalse(self.report()["snapshot_valid"])

    def test_read_only_db_and_json_finite(self):
        self.add()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        report = self.report()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(len(report["code_sha256"]), 64)
        self.assertIn("UNVERIFIED", report["policy"]["fee_status"])
        json.dumps(report, allow_nan=False)

    def test_default_now_rechecks_current_quote_after_calculation(self):
        self.add()
        with mock.patch("dashboard_trading.time.time", side_effect=[self.observed+2.9, self.observed+2.95, self.observed+3.01]):
            report = build_trading_report(self.path)
        self.assertTrue(report["snapshot_valid"])
        self.assertFalse(report["current_quote_valid"])
        self.assert_skips(report)

    def test_invalid_now_rejected_and_invalid_first_probability_safe_json(self):
        self.add()
        for now in (True, "NaN", "1e999"):
            with self.subTest(now=now), self.assertRaises(ValueError):
                build_trading_report(self.path, now=now)
        self.db.execute("UPDATE predictions SET p_yes='1e999'")
        self.db.commit()
        report = self.report()
        self.assertIsNone(report["first_call"]["p_yes"])
        self.assert_skips(report)
        json.dumps(report, allow_nan=False)

    def test_latest_null_timestamp_not_rescued_by_older_valid_signal(self):
        self.add(ident=1)
        self.add(ident=2, observed=self.observed+1)
        self.db.execute("UPDATE predictions SET observed=NULL WHERE id=2")
        self.db.commit()
        report = self.report(1)
        self.assertEqual(report["forecast_id"], 2)
        self.assertFalse(report["snapshot_valid"])
        self.assert_skips(report)

    def test_first_call_out_of_range_probability_hidden(self):
        self.add()
        self.db.execute("UPDATE predictions SET p_yes=1.1")
        self.db.commit()
        report = self.report()
        self.assertIsNone(report["first_call"]["p_yes"])
        self.assert_skips(report)

    def test_provided_nonactive_status_rejects_without_old_signal_fallback(self):
        self.add(ident=1)
        self.add(ident=2, observed=self.observed+1)
        for status in ("initialized", "uninitialized", "suspended", "unknown",
                       "closed", "finalized", "settled", "", None):
            metadata = dict(self.meta, status=status)
            self.db.execute("UPDATE markets SET metadata=?", (json.dumps(metadata),))
            self.db.commit()
            with self.subTest(status=status):
                report = self.report(1)
                self.assertEqual(report["reason"], "contract_not_open")
                self.assertEqual(report["forecast_id"], 2)
                self.assertFalse(report["snapshot_valid"])
                self.assertFalse(report["current_quote_valid"])
                self.assert_skips(report)

    def test_missing_status_retains_provenance_fixture_support(self):
        self.add()
        metadata = {key:value for key,value in self.meta.items() if key != "status"}
        self.db.execute("UPDATE markets SET metadata=?", (json.dumps(metadata),))
        self.db.commit()
        self.assertTrue(self.report()["snapshot_valid"])


if __name__ == "__main__":
    unittest.main()
