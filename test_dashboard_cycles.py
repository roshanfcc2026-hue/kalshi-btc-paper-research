import datetime as dt, json, os, sqlite3, tempfile, unittest
import bot
from dashboard_cycles import build_cycles_report
from quant import store as qstore, risk

UTC = dt.timezone.utc
def iso(ts): return dt.datetime.fromtimestamp(ts, UTC).isoformat().replace('+00:00', 'Z')
NOW = dt.datetime(2026, 7, 1, 20, 7, tzinfo=UTC).timestamp()  # 1:07 PM PDT; current window opened 20:00 UTC


class CycleTests(unittest.TestCase):
    def make(self, d):
        db = bot.connect(os.path.join(d, 'research.sqlite'))
        def market(t, opened, result):
            db.execute('INSERT INTO markets VALUES(?,?,?)', (t, json.dumps(dict(open_time=iso(opened), close_time=iso(opened + 900))), result))
        def call(t, ts, p): db.execute("INSERT INTO predictions(ticker,source,observed,completed,close_ts,p_yes,detail) VALUES(?,?,?,?,?,?,'{}')", (t, 'volatility-proxy-v1', ts, ts, ts + 600, p))
        o = NOW - 420  # current window open
        market('KXBTC15M-CUR', o, ''); call('KXBTC15M-CUR', o + 30, .6)
        market('KXBTC15M-A', o - 900, 'yes'); call('KXBTC15M-A', o - 880, .7); call('KXBTC15M-A', o - 600, .1)  # later call ignored
        market('KXBTC15M-B', o - 1800, 'yes'); call('KXBTC15M-B', o - 1790, .3)
        market('KXBTC15M-C', o - 2700, '')  ; call('KXBTC15M-C', o - 2690, .5)
        market('KXBTC15M-D', o - 3600, 'no')                                        # never forecast
        market('KXBTC15M-OLD', o - 93600, 'no'); call('KXBTC15M-OLD', o - 93590, .2)  # >24h ago
        market('KXETH15M-X', o - 900, 'yes')                                         # other series ignored
        db.commit(); db.close()
        q = qstore.connect(os.path.join(d, 'quant.sqlite')); risk.setup(q)
        from quant import execution; execution.setup(q)
        q.execute("INSERT INTO paper_decisions(ts,ticker,source,p_yes,action,reason,contracts,avg_price,fee) VALUES(?,'KXBTC15M-CUR','combo-v1',.64,'yes',NULL,3,.47,.06)", (o + 31,))
        q.execute("INSERT INTO paper_positions(ticker,source,side,contracts,cost,fee,opened,result,pnl,settled) VALUES('KXBTC15M-A','combo-v1','yes',2,1,.04,0,'yes',.96,1)"); q.commit(); q.close()

    def test_report(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d)
            r = build_cycles_report(os.path.join(d, 'research.sqlite'), NOW, os.path.join(d, 'quant.sqlite'))
            self.assertEqual(r['current']['ticker'], 'KXBTC15M-CUR'); self.assertEqual(r['current']['first_call_ts'], NOW - 390)
            e = r['current']['entry']; self.assertEqual((e['action'], e['contracts'], e['avg_price'], e['ts']), ('yes', 3, .47, NOW - 389))
            self.assertEqual(r['current']['locks'], [NOW - 420 + 480, NOW - 420 + 660])
            self.assertEqual({t['ticker']: t['state'] for t in r['last_24h']},
                             {'KXBTC15M-A': 'correct', 'KXBTC15M-B': 'wrong', 'KXBTC15M-C': 'pending', 'KXBTC15M-D': 'skipped'})
            self.assertEqual(r['last_24h_counts'], dict(correct=1, wrong=1, pending=0 + 1, skipped=1))
            h = r['by_hour'][12]  # A and B opened 12:xx PM PDT
            self.assertEqual((h['n'], h['correct'], h['paper_trades']), (2, 1, 1)); self.assertAlmostEqual(h['accuracy'], .5)
            self.assertEqual(sum(x['n'] for x in r['by_hour']), 3)  # A, B, OLD settled with first calls
            json.dumps(r, allow_nan=False)

    def test_fresh_run_counts_only_windows_since_start(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d)
            r = build_cycles_report(os.path.join(d, 'research.sqlite'), NOW, since=NOW - 420 - 1800)  # from window B
            self.assertEqual({t['ticker'] for t in r['last_24h']}, {'KXBTC15M-A', 'KXBTC15M-B'})
            self.assertEqual((r['run']['settled'], r['run']['correct'], r['run']['cycle']), (2, 1, 2))
            self.assertIsNone(build_cycles_report(os.path.join(d, 'research.sqlite'), NOW)['run'])

    def test_does_not_modify_database(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d); p = os.path.join(d, 'research.sqlite'); before = os.path.getmtime(p)
            build_cycles_report(p, NOW); self.assertEqual(os.path.getmtime(p), before)


if __name__ == '__main__':
    unittest.main()
