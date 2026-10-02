"""New midpoint policy regressions; fixture DB only, no live files or network."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import midpoint_call as m
import test_dashboard_trading


class MidpointCallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_dashboard_trading.TradingReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = Path(self.fixture.tmp.name)/"trial"
        self.opened = self.fixture.open
        self.registry = m.register(self.directory, self.opened+300, {"fixture": "a"})

    def tick(self, elapsed, probability=.7, add=True, ident=1):
        now = self.opened+elapsed
        if add:
            self.fixture.add(ident=ident, probability=probability, observed=now)
        return m.tick(self.directory, self.registry, self.fixture.path, now=now)

    def test_no_pre_midpoint_and_source_db_unchanged(self):
        self.fixture.add(observed=self.opened+440)
        before = hashlib.sha256(self.fixture.path.read_bytes()).hexdigest()
        report = self.tick(449, add=False)
        self.assertEqual(report["active"]["state"], "waiting")
        self.assertEqual(report["ledger"], [])
        self.assertEqual(before, hashlib.sha256(self.fixture.path.read_bytes()).hexdigest())

    def test_first_midpoint_capture_permanent_across_flip_and_restart(self):
        first = self.tick(451, .7)
        locked = first["active"]["decision"]
        self.assertEqual(locked["direction"], "UP / YES")
        self.assertEqual(locked["live_signal"], "SKIP")
        self.assertAlmostEqual(locked["p_market"], .495)
        second = self.tick(470, .1, ident=2)
        self.assertEqual(locked, second["active"]["decision"])
        self.assertEqual(len(second["ledger"]), 1)
        self.assertEqual(second["evaluation"]["calls"], 1)
        self.assertEqual(m.register(self.directory, self.opened+800, {"fixture": "a"}), self.registry)

    def test_weak_first_call_permanently_skips_stronger_later_forecast(self):
        first = self.tick(450, .59)
        self.assertEqual(first["active"]["decision"]["reason"], "weak_at_fixed_midpoint")
        second = self.tick(460, .9, ident=2)
        self.assertEqual(second["active"]["decision"]["p_yes"], .59)
        self.assertEqual(second["evaluation"]["skips"], 1)

    def test_thresholds_are_inclusive_and_raw_probabilities_preserved(self):
        self.fixture.add(probability=.6, observed=self.opened+450)
        valid = m.build_trading_report(self.fixture.path, now=self.opened+450)
        for p, expected in ((.6, "UP / YES"), (.4, "DOWN / NO"), (.5, "SKIP")):
            report = copy.deepcopy(valid)
            report["forecasts"]["p_yes"] = p
            self.assertEqual(m.capture(report, self.opened, self.opened+450)["direction"], expected)

    def test_stale_future_expired_and_invalid_values_rejected(self):
        self.fixture.add(observed=self.opened+450)
        valid = m.build_trading_report(self.fixture.path, now=self.opened+450)
        for now in (self.opened+449, self.opened+453.01, self.opened+510, self.opened+900):
            with self.subTest(now=now):
                self.assertIsNone(m.capture(valid, self.opened, now))
        for field, value in (("observed", self.opened+460), ("completed", self.opened+460),
                             ("current_quote_valid", False), ("snapshot_valid", False),
                             ("close_ts", self.opened+800), ("quote_received", self.opened+451)):
            report = copy.deepcopy(valid)
            report[field] = value
            with self.subTest(field=field):
                self.assertIsNone(m.capture(report, self.opened, self.opened+450))
        for p in (float("nan"), float("inf"), -1, 2):
            report = copy.deepcopy(valid)
            report["forecasts"]["p_yes"] = p
            self.assertIsNone(m.capture(report, self.opened, self.opened+450))

    def test_previous_midpoint_quote_must_not_be_used(self):
        self.fixture.add(observed=self.opened+449.9)
        report = self.tick(451, add=False)
        self.assertEqual(report["ledger"], [])

    def test_recorded_stale_forecast_does_not_lock_and_deadline_skips(self):
        self.fixture.add(observed=self.opened+450)
        report = self.tick(455, add=False)
        self.assertEqual(report["ledger"], [])
        expired = self.tick(510, add=False)
        self.assertEqual(expired["ledger"][0]["reason"], "missed_capture_window")
        self.assertIsNone(expired["ledger"][0]["p_yes"])

    def test_missing_windows_on_resume_cannot_backfill(self):
        self.fixture.add(observed=self.opened+451)
        report = self.tick(900*3+100, add=False)
        self.assertEqual(report["evaluation"]["skips"], 3)
        self.assertEqual(report["evaluation"]["scheduled"], 4)
        self.assertEqual([x["open_ts"] for x in report["ledger"]],
                         [self.opened+i*900 for i in range(3)])
        self.assertEqual(report["active"]["open_ts"], self.opened+2700)
        self.assertIsNone(report["active"]["decision"])
        self.assertTrue(all(x["forecast_id"] is None for x in report["ledger"]))

    def test_registration_after_midpoint_waits_next_market(self):
        nextdir = Path(self.fixture.tmp.name)/"later"
        reg = m.register(nextdir, self.opened+451, {"fixture": "a"})
        self.assertEqual(reg["start_open_ts"], self.opened+900)
        report = m.tick(nextdir, reg, self.fixture.path, now=self.opened+460)
        self.assertEqual(report["active"]["state"], "not_registered")
        self.assertEqual(report["ledger"], [])

    def test_settlement_scores_paired_calls_only_and_official_finalized_required(self):
        self.tick(451, .7)
        before = (self.directory/"ledger.jsonl").read_bytes()
        self.fixture.db.execute("UPDATE markets SET result='no'")
        self.fixture.db.commit()
        pending = self.tick(950, add=False)
        self.assertEqual(pending["evaluation"]["settled_calls"], 0)
        self.fixture.meta.update(status="finalized", result="no", settlement_ts=self.fixture.iso(self.opened+920))
        self.fixture.db.execute("UPDATE markets SET metadata=?", (json.dumps(self.fixture.meta),))
        self.fixture.db.commit()
        after = self.tick(950, add=False)
        ev = after["evaluation"]
        self.assertEqual(ev["settled_calls"], 1)
        self.assertEqual(ev["wrong"], 1)
        self.assertAlmostEqual(ev["model"]["brier"], .49)
        self.assertAlmostEqual(ev["market"]["brier"], .495**2)
        self.assertAlmostEqual(ev["model"]["log_loss"], m.losses(.7, 0)[1])
        self.assertTrue((self.directory/"ledger.jsonl").read_bytes().startswith(before))
        size = (self.directory/"ledger.jsonl").stat().st_size
        self.tick(960, add=False)
        self.assertEqual(size, (self.directory/"ledger.jsonl").stat().st_size)

    def test_abstentions_excluded_from_both_scoring_cohorts(self):
        weak = m.missing_decision(self.opened, self.opened+451)
        weak.update(result="yes", p_yes=.55, p_market=.49)
        result = m.evaluate([weak], 1)
        self.assertEqual(result["coverage"], 0)
        self.assertEqual(result["settled_calls"], 0)
        self.assertIsNone(result["model"]["brier"])
        self.assertIsNone(result["market"]["brier"])

    def test_registration_hash_and_ledger_tamper_guard(self):
        self.assertRaises(RuntimeError, m.register, self.directory, self.opened+400, {"fixture": "b"})
        self.tick(451)
        path = self.directory/"ledger.jsonl"
        path.write_text(path.read_text().replace('"p_yes": 0.7', '"p_yes": 0.8'))
        self.assertRaises(RuntimeError, m.read_ledger, path)

    def test_stop_file_does_not_register_or_launch(self):
        directory = Path(self.fixture.tmp.name)/"stopped"
        directory.mkdir()
        (directory/"STOP").touch()
        with mock.patch("sys.argv", ["midpoint_call.py", "once", "--directory", str(directory), "--db", str(self.fixture.path)]):
            self.assertRaises(SystemExit, m.main)
        self.assertFalse((directory/"registry.json").exists())

    def test_concurrent_monitor_os_lock_rejected(self):
        with m.singleton(self.directory/"monitor.lock"):
            with self.assertRaises(OSError):
                with m.singleton(self.directory/"monitor.lock"):
                    self.fail("Second monitor acquired OS singleton lock")


if __name__ == "__main__":
    unittest.main()
