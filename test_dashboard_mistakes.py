"""First-call report fixtures; no live database or forecasts are modified."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard_mistakes import build_mistake_report


class MistakeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.sqlite"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript("""
          CREATE TABLE markets(ticker TEXT PRIMARY KEY, metadata TEXT, result TEXT);
          CREATE TABLE predictions(id INTEGER PRIMARY KEY,ticker TEXT,source TEXT,
            observed REAL,completed REAL,close_ts REAL,p_yes REAL,detail TEXT,
            result TEXT,correct INTEGER,brier REAL);
        """)

    def market(self, ticker, result="no", metadata=None):
        if metadata is None:
            metadata = {"status": "finalized", "result": result, "expiration_value": "99.50", "floor_strike": 999}
        self.db.execute("INSERT INTO markets VALUES(?,?,?)", (ticker, json.dumps(metadata), result))
        self.db.commit()

    def forecast(self, ident, ticker, probability=.8, observed=1000, close=1900, source="volatility-proxy-v1", detail=None):
        if detail is None:
            detail = {"target": 100, "spot": {"price": 101}}
        self.db.execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (ident, ticker, source, observed, observed, close, probability,
                         json.dumps(detail), "yes", 1, 0))
        self.db.commit()

    def test_first_miss_survives_later_correct_and_uses_official_market_result(self):
        self.market("A")
        self.forecast(1, "A")
        self.forecast(2, "A", probability=.1, observed=1100, detail={"target": 200, "spot": {"price": 200}})
        report = build_mistake_report(self.path, now=2000)
        self.assertEqual(report["summary"]["wrong"], 1)
        row = report["mistakes"][0]
        self.assertEqual(row["forecast_id"], 1)
        self.assertEqual(row["actual"], "no")
        self.assertEqual((row["target"], row["proxy"], row["official_settlement"], row["gap"]), (100, 101, 99.5, -.5))
        self.assertEqual(row["p_yes"], .8)
        self.assertAlmostEqual(row["p_no"], .2)

    def test_equal_timestamp_uses_smaller_id(self):
        self.market("A")
        self.forecast(8, "A", probability=.1)
        self.forecast(3, "A", probability=.9)
        self.assertEqual(build_mistake_report(self.path)["mistakes"][0]["forecast_id"], 3)

    def test_numbering_includes_correct_pending_and_sorts_mistakes_by_close(self):
        self.market("A", "yes")
        self.market("B", "")
        self.market("C")
        self.market("D")
        for ident, ticker in enumerate(("A", "B", "C", "D"), 1):
            self.forecast(ident, ticker, observed=1000 + 100 * ident, close=2000 + 100 * ident)
        report = build_mistake_report(self.path)
        self.assertEqual([(r["ticker"], r["market_number"]) for r in report["mistakes"]], [("D", 4), ("C", 3)])
        self.assertEqual([(r["ticker"], r["market_number"]) for r in report["market_numbers"]],
                         [("A", 1), ("B", 2), ("C", 3), ("D", 4)])
        self.assertEqual(report["market_numbers"][1],
                         {"ticker": "B", "market_number": 2, "observed": 1200, "close_ts": 2200})
        self.assertEqual((report["summary"]["correct"], report["summary"]["wrong"], report["summary"]["pending"]), (1, 2, 1))
        self.forecast(10, "D", probability=.1, observed=1500, close=2400)
        self.assertEqual(build_mistake_report(self.path)["mistakes"], report["mistakes"])
        self.assertEqual(build_mistake_report(self.path)["market_numbers"], report["market_numbers"])

    def test_other_sources_do_not_replace_v1_and_pending_has_no_mistake(self):
        self.market("A")
        self.market("B", "")
        self.forecast(1, "A", probability=.1, observed=900, source="market-mid-v1")
        self.forecast(2, "A")
        self.forecast(3, "B")
        report = build_mistake_report(self.path)
        self.assertEqual(report["summary"]["markets_forecast"], 2)
        self.assertEqual([r["forecast_id"] for r in report["mistakes"]], [2])

    def test_missing_numbers_remain_null_and_payout_is_not_settlement(self):
        self.market("A", metadata={"status": "finalized", "result": "no", "floor_strike": 99,
                                   "settlement_value_dollars": "0.0000"})
        self.forecast(1, "A", detail={})
        row = build_mistake_report(self.path)["mistakes"][0]
        for field in ("target", "proxy", "official_settlement", "gap"):
            self.assertIsNone(row[field])

    def test_malformed_optional_numbers_do_not_become_prices(self):
        self.market("A", metadata={"result": "no", "expiration_value": "NaN"})
        self.forecast(1, "A", detail={"target": True, "spot": {"price": "Infinity"}})
        row = build_mistake_report(self.path)["mistakes"][0]
        self.assertIsNone(row["official_settlement"])
        self.assertIsNone(row["target"])
        self.assertIsNone(row["proxy"])
        json.dumps(build_mistake_report(self.path), allow_nan=False)

    def test_invalid_first_kept_excluded_without_fallback(self):
        for index, invalid in enumerate((None, "NaN", "Infinity", 1.1), 1):
            ticker = "A" + str(index)
            self.market(ticker)
            self.forecast(index * 2, ticker, probability=invalid, observed=1000 + index)
            self.forecast(index * 2 + 1, ticker, probability=.8, observed=1100 + index)
        report = build_mistake_report(self.path)
        self.assertEqual(report["summary"]["excluded_invalid_first"], 4)
        self.assertEqual(report["summary"]["markets_forecast"], 4)
        self.assertEqual(report["mistakes"], [])
        self.assertEqual(len(report["excluded_invalid_first"]), 4)

    def test_invalid_timestamp_does_not_select_later_valid(self):
        self.market("A")
        self.forecast(1, "A", observed=None)
        self.forecast(2, "A")
        report = build_mistake_report(self.path)
        self.assertEqual(report["excluded_invalid_first"][0]["reason"], "invalid_first_timestamp")
        self.assertEqual(report["mistakes"], [])

    def test_invalid_slots_preserved_but_not_mapped(self):
        self.market("A")
        self.market("B", "yes")
        self.market("C", "")
        self.forecast(1, "A", probability="NaN", observed=1000)
        self.forecast(2, "B", observed=1100)
        self.forecast(3, "C", observed=1200)
        self.forecast(4, "A", probability=.8, observed=1300)
        report = build_mistake_report(self.path)
        self.assertEqual([(r["ticker"], r["market_number"]) for r in report["market_numbers"]],
                         [("B", 2), ("C", 3)])
        self.assertEqual(report["excluded_invalid_first"][0]["market_number"], 1)
        self.assertEqual(report["summary"]["correct"], 1)
        self.assertEqual(report["summary"]["pending"], 1)

    def test_nonfinal_or_inconsistent_official_labels_not_scored(self):
        self.market("A", metadata={"status": "open", "result": "no"})
        self.market("B", metadata={"status": "finalized", "result": "yes"})
        self.forecast(1, "A")
        self.forecast(2, "B")
        report = build_mistake_report(self.path)
        self.assertEqual(report["summary"]["official_label_issues"], 2)
        self.assertEqual(report["summary"]["pending"], 2)
        self.assertEqual(report["summary"]["settled"], 0)

    def test_threshold_equal_point_five_is_yes(self):
        self.market("A")
        self.forecast(1, "A", probability=.5)
        self.assertEqual(build_mistake_report(self.path)["mistakes"][0]["predicted"], "yes")

    def test_read_only_database_preserved_and_missing_path_not_created(self):
        self.market("A")
        self.forecast(1, "A")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        build_mistake_report(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 1)
        absent = self.path.with_name("absent.sqlite")
        with self.assertRaises(sqlite3.OperationalError):
            build_mistake_report(absent)
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
