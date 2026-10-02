"""Remaining-time lock regressions: synthetic SQLite only, no live writes."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import remaining_calls as r
import test_dashboard_trading


class RemainingCallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_dashboard_trading.TradingReportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = Path(self.fixture.tmp.name) / "remaining"
        self.opened = self.fixture.open
        self.registry = r.register(self.directory, self.opened + 300, {"fixture": "a"})

    def tick(self, elapsed, probability=.7, ident=None, observed=None):
        now = self.opened + elapsed
        if ident is not None:
            self.fixture.add(ident=ident, probability=probability,
                             observed=now if observed is None else self.opened + observed)
        return r.tick(self.directory, self.registry, self.fixture.path, now=now)

    def decision(self, report, minute="7"):
        return report["active"]["decisions"][minute]["decision"]

    def test_before_cutoff_waits_and_preserves_source_db(self):
        self.fixture.add(observed=self.opened + 460)
        before = hashlib.sha256(self.fixture.path.read_bytes()).hexdigest()
        report = self.tick(479)
        self.assertEqual(report["active"]["decisions"]["7"]["state"], "waiting")
        self.assertEqual(report["active"]["decisions"]["7"]["cutoff"], self.opened + 480)
        self.assertEqual(report["active"]["decisions"]["4"]["cutoff"], self.opened + 660)
        self.assertEqual(report["ledger"], [])
        self.assertEqual(report["evaluation"]["7"]["scheduled"], 0)
        self.assertEqual(before, hashlib.sha256(self.fixture.path.read_bytes()).hexdigest())
        self.assertEqual(self.fixture.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 1)

    def test_latest_pre_cutoff_seen_candidate_wins_and_never_flips(self):
        self.tick(460, .8, ident=1)
        self.tick(479, .3, ident=2)
        first = self.tick(480)
        locked = self.decision(first)
        self.assertEqual(locked["direction"], "DOWN / NO")
        self.assertEqual(locked["forecast_id"], 2)
        self.assertEqual(locked["seen_at"], self.opened + 479)
        self.assertEqual(locked["locked_at"], self.opened + 480)
        self.assertAlmostEqual(locked["p_market"], .495)
        second = self.tick(485, .99, ident=3)
        self.assertEqual(locked, self.decision(second))
        self.assertEqual(r.register(self.directory, self.opened + 600, {"fixture": "a"}), self.registry)
        self.assertEqual(locked, self.decision(self.tick(600)))

    def test_two_independent_cutoffs_keep_opposite_decisions(self):
        self.tick(479, .8, ident=1)
        seven = self.decision(self.tick(481))
        self.tick(659, .2, ident=2)
        report = self.tick(661)
        self.assertEqual(self.decision(report), seven)
        self.assertEqual(self.decision(report, "4")["direction"], "DOWN / NO")
        self.assertEqual(len(report["ledger"]), 2)
        for minute in ("7", "4"):
            self.assertEqual(report["evaluation"][minute]["calls"], 1)
            self.assertEqual(report["evaluation"][minute]["scheduled"], 1)

    def test_after_cutoff_better_forecast_not_used_for_earlier_call(self):
        self.tick(478, .7, ident=1)
        report = self.tick(481, .1, ident=2)
        self.assertEqual(self.decision(report)["p_yes"], .7)
        self.assertEqual(self.decision(report)["forecast_id"], 1)
        self.assertEqual(self.decision(report)["seen_at"], self.opened + 478)

    def test_db_forecast_with_old_observation_but_first_seen_late_cannot_backfill(self):
        report = self.tick(481, .9, ident=1, observed=479)
        self.assertEqual(self.decision(report)["reason"], "missed_capture_window")
        self.assertIsNone(self.decision(report)["p_yes"])
        self.assertEqual(self.decision(self.tick(485))["reason"], "missed_capture_window")

    def test_weak_latest_call_permanently_skips_stronger_later_call(self):
        self.tick(470, .8, ident=1)
        self.tick(479, .59, ident=2)
        first = self.decision(self.tick(480))
        self.assertEqual(first["reason"], "weak_at_fixed_cutoff")
        self.assertEqual(first["direction"], "SKIP")
        self.assertEqual(first["p_yes"], .59)
        second = self.tick(482, .99, ident=3)
        self.assertEqual(first, self.decision(second))
        self.assertEqual(second["evaluation"]["7"]["weak_skips"], 1)

    def test_snapshot_valid_direction_does_not_require_three_second_book(self):
        self.tick(470, .7, ident=1, observed=440)
        locked = self.decision(self.tick(480))
        self.assertEqual(locked["direction"], "UP / YES")
        self.assertEqual(locked["observed"], self.opened + 440)
        self.assertEqual(locked["live_signal"], "SKIP")
        self.assertNotIn("paper_scenario", locked)

    def test_forty_five_second_age_at_cutoff_inclusive(self):
        self.tick(470, .7, ident=1, observed=435)
        self.assertEqual(self.decision(self.tick(480))["direction"], "UP / YES")

    def test_cache_stale_at_cutoff_permanently_missing(self):
        self.tick(470, .7, ident=1, observed=434.99)
        locked = self.decision(self.tick(480))
        self.assertEqual(locked["reason"], "missed_capture_window")
        self.assertIsNone(locked["forecast_id"])

    def test_restart_with_cached_receipt_within_grace_works(self):
        self.tick(479, .7, ident=1)
        loaded = r.register(self.directory, self.opened + 495, {"fixture": "a"})
        report = r.tick(self.directory, loaded, self.fixture.path, now=self.opened + 495)
        self.assertEqual(self.decision(report)["direction"], "UP / YES")
        self.assertEqual(self.decision(report)["seen_at"], self.opened + 479)

    def test_resume_after_fifteen_seconds_skips_even_with_good_cache(self):
        self.tick(479, .9, ident=1)
        report = self.tick(495.001)
        self.assertEqual(self.decision(report)["reason"], "missed_capture_window")
        self.assertIsNone(self.decision(report)["p_yes"])

    def test_repeated_poll_deduplicates_candidate_and_keeps_first_seen(self):
        self.tick(470, .7, ident=1)
        self.tick(471)
        self.tick(479)
        candidates = [event for event in r.read_ledger(self.directory / "ledger.jsonl") if event["kind"] == "candidate"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["value"]["seen_at"], self.opened + 470)
        self.assertEqual(self.decision(self.tick(480))["seen_at"], self.opened + 470)

    def test_thresholds_inclusive_and_at_cutoff_observation_allowed(self):
        for index, (p, direction) in enumerate(((.6, "UP / YES"), (.4, "DOWN / NO"), (.5, "SKIP"))):
            with self.subTest(p=p):
                folder = Path(self.fixture.tmp.name) / ("threshold-" + str(index))
                registry = r.register(folder, self.opened + 400, {"fixture": "a"})
                self.fixture.add(ident=index + 1, probability=p, observed=self.opened + 480)
                # Exact-paired snapshot uniqueness is required by the shared helper.
                self.fixture.db.execute("DELETE FROM snapshots WHERE id<?", (index + 1,))
                self.fixture.db.commit()
                report = r.tick(folder, registry, self.fixture.path, now=self.opened + 480)
                self.assertEqual(self.decision(report)["direction"], direction)
                self.assertEqual(self.decision(report)["p_yes"], p)

    def test_registration_between_cutoffs_enrolls_four_only(self):
        folder = Path(self.fixture.tmp.name) / "late"
        registry = r.register(folder, self.opened + 500, {"fixture": "a"})
        self.assertEqual(registry["start_open_ts_by_minutes"], {"7": self.opened + 900, "4": self.opened})
        report = r.tick(folder, registry, self.fixture.path, now=self.opened + 501)
        self.assertEqual(report["active"]["decisions"]["7"]["state"], "not_registered")
        self.assertEqual(report["active"]["decisions"]["4"]["state"], "waiting")
        self.assertEqual(report["evaluation"]["7"]["decisions"], 0)

    def test_registration_exact_cutoff_does_not_enroll_that_cutoff(self):
        for minute, offset in r.CUTOFFS.items():
            folder = Path(self.fixture.tmp.name) / ("registration-" + minute)
            registry = r.register(folder, self.opened + offset, {"fixture": "a"})
            self.assertEqual(registry["start_open_ts_by_minutes"][minute], self.opened + 900)

    def test_missing_cycles_count_per_eligible_cutoff(self):
        report = self.tick(2800)
        self.assertEqual(len(report["ledger"]), 6)
        for minute in ("7", "4"):
            evaluation = report["evaluation"][minute]
            self.assertEqual(evaluation["scheduled"], 3)
            self.assertEqual(evaluation["missing_captures"], 3)
            self.assertEqual(evaluation["skips"], 3)
            self.assertEqual(report["active"]["decisions"][minute]["state"], "waiting")
        self.assertTrue(all(entry["p_yes"] is None for entry in report["ledger"]))

    def test_read_finishing_after_cutoff_cannot_retroactively_capture(self):
        self.fixture.add(observed=self.opened + 479)
        snapshot = r.build_trading_report(self.fixture.path, now=self.opened + 479)
        with mock.patch("remaining_calls.time.time", side_effect=[self.opened + 479.9, self.opened + 480.1]):
            report = r.tick(self.directory, self.registry, self.fixture.path,
                            report_builder=lambda _db: snapshot)
        self.assertEqual(self.decision(report)["reason"], "missed_capture_window")
        candidates = [event["value"] for event in r.read_ledger(self.directory / "ledger.jsonl") if event["kind"] == "candidate"]
        self.assertEqual(candidates[0]["seen_at"], self.opened + 480.1)

    def test_read_finishing_after_final_cutoff_not_cached(self):
        self.fixture.add(observed=self.opened + 659)
        snapshot = r.build_trading_report(self.fixture.path, now=self.opened + 659)
        with mock.patch("remaining_calls.time.time", side_effect=[self.opened + 659.9, self.opened + 660.1]):
            report = r.tick(self.directory, self.registry, self.fixture.path,
                            report_builder=lambda _db: snapshot)
        self.assertEqual(self.decision(report, "4")["reason"], "missed_capture_window")
        self.assertFalse(any(event["kind"] == "candidate" for event in r.read_ledger(self.directory / "ledger.jsonl")))

    def test_invalid_and_future_candidate_fail_closed(self):
        self.fixture.add(observed=self.opened + 470)
        valid = r.build_trading_report(self.fixture.path, now=self.opened + 470)
        self.assertIsNotNone(r.candidate(valid, self.opened, self.opened + 470))
        for field, value in (("observed", self.opened + 471), ("completed", self.opened + 471),
                             ("quote_received", self.opened + 469), ("snapshot_valid", False),
                             ("open_ts", self.opened - 900), ("close_ts", self.opened + 800),
                             ("forecast_id", None), ("snapshot_id", None), ("ticker", "OTHER")):
            snapshot = copy.deepcopy(valid)
            snapshot[field] = value
            with self.subTest(field=field):
                self.assertIsNone(r.candidate(snapshot, self.opened, self.opened + 470))
        for value in (float("nan"), float("inf"), -1, 2, True):
            snapshot = copy.deepcopy(valid)
            snapshot["forecasts"]["p_yes"] = value
            self.assertIsNone(r.candidate(snapshot, self.opened, self.opened + 470))

    def test_official_finalization_and_per_cutoff_paired_scoring(self):
        self.tick(479, .8, ident=1)
        self.tick(480)
        self.tick(659, .2, ident=2)
        self.tick(660)
        self.fixture.db.execute("UPDATE markets SET result='no'")
        self.fixture.db.commit()
        pending = self.tick(950)
        self.assertEqual(pending["evaluation"]["7"]["settled_calls"], 0)
        self.fixture.meta.update(status="finalized", result="no", settlement_ts=self.fixture.iso(self.opened + 920))
        self.fixture.db.execute("UPDATE markets SET metadata=?", (json.dumps(self.fixture.meta),))
        self.fixture.db.commit()
        previous = (self.directory / "ledger.jsonl").read_bytes()
        report = self.tick(950)
        seven, four = report["evaluation"]["7"], report["evaluation"]["4"]
        self.assertEqual(seven["wrong"], 1)
        self.assertEqual(four["correct"], 1)
        self.assertAlmostEqual(seven["model"]["brier"], .64)
        self.assertAlmostEqual(four["model"]["brier"], .04)
        self.assertAlmostEqual(four["market"]["brier"], .495 ** 2)
        self.assertAlmostEqual(four["model"]["log_loss"], r.losses(.2, 0)[1])
        self.assertTrue((self.directory / "ledger.jsonl").read_bytes().startswith(previous))
        previous = (self.directory / "ledger.jsonl").read_bytes()
        self.tick(960)
        self.assertEqual((self.directory / "ledger.jsonl").read_bytes(), previous)

    def test_skips_excluded_from_both_scoring_cohorts(self):
        skipped = r.missing_decision(self.opened, "7", self.opened + 480)
        skipped.update(result="yes", p_yes=.59, p_market=.49, reason="weak_at_fixed_cutoff")
        report = r.evaluate([skipped], 1)
        self.assertEqual(report["coverage"], 0)
        self.assertEqual(report["weak_skips"], 1)
        self.assertEqual(report["settled_calls"], 0)
        self.assertIsNone(report["model"]["brier"])
        self.assertIsNone(report["market"]["brier"])

    def test_registry_and_ledger_hash_guards_and_duplicate_decisions(self):
        self.assertRaises(RuntimeError, r.register, self.directory, self.opened + 400, {"fixture": "b"})
        self.tick(479, .7, ident=1)
        self.tick(480)
        events = r.read_ledger(self.directory / "ledger.jsonl")
        locked = next(event for event in events if event["kind"] == "decision")
        self.assertRaises(RuntimeError, r.decisions, events + [locked])
        path = self.directory / "ledger.jsonl"
        path.write_text(path.read_text().replace('"p_yes": 0.7', '"p_yes": 0.8'))
        self.assertRaises(RuntimeError, r.read_ledger, path)

    def test_stop_file_prevents_registration_and_launch(self):
        folder = Path(self.fixture.tmp.name) / "stopped"
        folder.mkdir()
        (folder / "STOP").touch()
        with mock.patch("sys.argv", ["remaining_calls.py", "once", "--directory", str(folder), "--db", str(self.fixture.path)]):
            self.assertRaises(SystemExit, r.main)
        self.assertFalse((folder / "registry.json").exists())

    def test_os_singleton_rejects_second_monitor(self):
        with r.singleton(self.directory / "monitor.lock"):
            with self.assertRaises(OSError):
                with r.singleton(self.directory / "monitor.lock"):
                    self.fail("Second monitor acquired singleton lock")


if __name__ == "__main__":
    unittest.main()
