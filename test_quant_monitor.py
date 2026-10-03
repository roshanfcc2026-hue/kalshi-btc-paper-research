import datetime as dt, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from quant import monitor, risk, store as qstore, kalshi_book as kb, proxy

UTC = dt.timezone.utc


def db():
    d = qstore.connect(':memory:'); monitor.setup(d); return d


def fresh_feeds(d, now):
    proxy.store(d, dict(ts=now - 1, price=100.0, n_used=3, n_total=4, status='ok', inputs=[]))
    kb.store(d, 'M', now - 1, 0.1, now + 600, 100, [(0.4, 1)], [(0.5, 1)])


class HeartbeatTests(unittest.TestCase):
    def setUp(self): self._p = patch.object(monitor, 'ALERT_LOG', Path(tempfile.mkdtemp()) / 'a.log'); self._p.start()
    def tearDown(self): self._p.stop()
    def test_alert_only_after_two_minutes(self):
        d = db(); monitor.beat(d, 'collector', 1000)
        self.assertEqual(monitor.check_heartbeats(d, 1110), []); self.assertEqual(monitor.check_heartbeats(d, 1121), ['collector'])
        self.assertEqual(d.execute('SELECT kind FROM alerts').fetchone()[0], 'heartbeat'); self.assertTrue(monitor.ALERT_LOG.exists())
    def test_never_beat_alerts(self):
        self.assertEqual(monitor.check_heartbeats(db(), 5), ['collector'])


class KillSwitchTests(unittest.TestCase):
    def setUp(self): self._p = patch.object(monitor, 'ALERT_LOG', Path(tempfile.mkdtemp()) / 'a.log'); self._p.start()
    def tearDown(self): self._p.stop()
    def test_more_than_three_consecutive_errors_halts(self):
        d = db()
        for i in range(3): monitor.record_cycle(d, False, i)
        self.assertFalse(risk.halted(d)[0]); monitor.record_cycle(d, False, 4)
        self.assertTrue(risk.halted(d)[0]); self.assertIn('consecutive', risk.halted(d)[1])
    def test_success_resets_streak(self):
        d = db()
        for ok in (False, False, False, True, False, False, False): monitor.record_cycle(d, ok, 1)
        self.assertFalse(risk.halted(d)[0])
    def test_feed_failure_halts_with_startup_grace(self):
        d = db(); now = 5000.0
        self.assertIsNone(monitor.after_cycle(d, {'errors': []}, now, started=now - 10))  # grace
        self.assertIn('data-feed failure', monitor.after_cycle(d, {'errors': []}, now, started=now - 100))
    def test_healthy_cycle_not_halted_and_single_market_error_ignored(self):
        d = db(); now = 5000.0; fresh_feeds(d, now)
        for _ in range(6): self.assertIsNone(monitor.after_cycle(d, {'errors': [('kalshi:M', 'x')]}, now))
    def test_loss_breach_halts_through_monitor(self):
        d = db(); now = 86400.0 * 50 + 100; fresh_feeds(d, now)
        d.execute("INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened,result,pnl,settled) VALUES('L','s','yes',1,60,0,?,'no',-60,?)", (now, now))
        self.assertEqual(monitor.after_cycle(d, {'errors': []}, now), 'daily loss limit')


class PacificAndScorecardTests(unittest.TestCase):
    def ts(self, *a): return dt.datetime(*a, tzinfo=UTC).timestamp()
    def test_dst_boundaries(self):
        self.assertEqual(monitor.pacific_offset_hours(self.ts(2026, 3, 8, 9, 59)), -8)
        self.assertEqual(monitor.pacific_offset_hours(self.ts(2026, 3, 8, 10, 0)), -7)
        self.assertEqual(monitor.pacific_offset_hours(self.ts(2026, 11, 1, 8, 59)), -7)
        self.assertEqual(monitor.pacific_offset_hours(self.ts(2026, 11, 1, 9, 0)), -8)
        self.assertEqual(monitor.pacific(self.ts(2026, 7, 1, 14, 0)).hour, 7)
    def test_due_once_at_seven_pacific(self):
        d = db(); before = self.ts(2026, 7, 1, 13, 59); at = self.ts(2026, 7, 1, 14, 0)
        self.assertIsNone(monitor.scorecard_due(d, before)); self.assertEqual(monitor.scorecard_due(d, at), '2026-07-01')
        with tempfile.TemporaryDirectory() as f:
            self.assertIsNotNone(monitor.run_scorecard_if_due(d, at, Path(f)))
            self.assertIsNone(monitor.run_scorecard_if_due(d, at + 3600, Path(f)))
            self.assertTrue((Path(f) / 'scorecard-2026-07-01.json').exists())
    def test_scorecard_contents(self):
        d = db(); now = self.ts(2026, 7, 1, 14, 0)
        for t, src, p, pm in (('A', 'combo-v1', 0.8, 0.6), ('A', 'market-mid', 0.6, 0.6), ('B', 'combo-v1', 0.3, 0.4)):
            d.execute('INSERT INTO forecasts VALUES(?,?,?,?,?)', (t, src, now - 5000, p, pm))
        d.execute("INSERT INTO market_results VALUES('A','yes',?),('B','no',?)", (now - 100, now - 100))
        d.execute("INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened,result,pnl,settled) VALUES('A','combo-v1','yes',5,2,0.1,?,'yes',2.9,?)", (now - 900, now - 100))
        d.execute("INSERT INTO paper_decisions(ts,ticker,source,action) VALUES(?,'A','combo-v1','yes'),(?,'B','combo-v1','skip')", (now - 900, now - 900))
        s = monitor.scorecard(d, now)
        c = s['sources']['combo-v1']; self.assertEqual(c['markets'], 2); self.assertLess(c['log_loss'], c['market_log_loss'])
        self.assertEqual((s['paper']['trades'], s['paper']['skips']), (1, 1)); self.assertAlmostEqual(s['paper']['pnl_24h'], 2.9)
        json.dumps(s)


if __name__ == '__main__':
    unittest.main()
