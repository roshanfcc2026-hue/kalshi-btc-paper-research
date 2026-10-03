import unittest
from quant import feeds, store as qstore

R = 1000.0
CB_T = {'price': '65000.10', 'size': '0.01', 'time': '1970-01-01T00:16:39.500000Z', 'bid': '64999.9', 'ask': '65000.2'}
CB_B = {'bids': [['64999.9', '1.5', 3], ['64999.8', '2', 1], ['64999.0', '1', 1], ['64998', '1', 1], ['64997', '1', 1], ['64990', '9', 1]],
        'asks': [['65000.2', '0.5', 1], ['65000.3', '1', 1]]}
KR_TR = {'error': [], 'result': {'XXBTZUSD': [['65001.0', '0.02', 998.25, 'b', 'm', '', 1]], 'last': '1'}}
KR_DP = {'error': [], 'result': {'XXBTZUSD': {'asks': [['65001.5', '1.0', 999], ['65002', '2', 999]], 'bids': [['65000.5', '3.0', 999], ['65000', '1', 999]]}}}
BS_T = {'last': '65002.0', 'timestamp': '999', 'bid': '65001.0', 'ask': '65003.0'}
BS_B = {'microtimestamp': '999500000', 'timestamp': '999', 'bids': [['65001.0', '0.4']], 'asks': [['65003.0', '0.6']]}
GM_T = {'bid': '65000.0', 'ask': '65002.0', 'last': '65001.0', 'volume': {'BTC': '1', 'timestamp': 999000}}
GM_B = {'bids': [{'price': '65000.0', 'amount': '2', 'timestamp': '999'}], 'asks': [{'price': '65002.0', 'amount': '1', 'timestamp': '999'}]}


class FeedTests(unittest.TestCase):
    def test_coinbase_book_trimmed_to_five_sorted(self):
        t, b = feeds.parse_coinbase(CB_T, CB_B, R)
        self.assertEqual(t['price'], 65000.10); self.assertEqual(t['exch_ts'], 999.5); self.assertEqual(t['recv_ts'], R)
        self.assertEqual(len(b['bids']), 5); self.assertEqual(b['bids'][0], (64999.9, 1.5)); self.assertEqual(b['asks'][0][0], 65000.2)
    def test_kraken(self):
        t, b = feeds.parse_kraken(KR_TR, KR_DP, R)
        self.assertEqual((t['price'], t['exch_ts'], t['bid'], t['ask']), (65001.0, 998.25, 65000.5, 65001.5))
    def test_kraken_error_raises(self):
        with self.assertRaises(ValueError): feeds.parse_kraken({'error': ['EGeneral:Too many requests']}, KR_DP, R)
    def test_bitstamp_uses_microtimestamp(self):
        t, b = feeds.parse_bitstamp(BS_T, BS_B, R)
        self.assertEqual(t['exch_ts'], 999.0); self.assertEqual(b['exch_ts'], 999.5)
    def test_gemini_dict_levels(self):
        t, b = feeds.parse_gemini(GM_T, GM_B, R)
        self.assertEqual((t['price'], t['exch_ts']), (65001.0, 999.0)); self.assertEqual(b['bids'][0], (65000.0, 2.0))
    def test_bad_price_rejected(self):
        for bad in ('0', '-1', 'nan', 'inf'):
            with self.assertRaises(ValueError): feeds.parse_coinbase(dict(CB_T, price=bad), CB_B, R)
    def test_crossed_book_rejected_and_crossed_quote_dropped(self):
        with self.assertRaises(ValueError): feeds.parse_coinbase(CB_T, {'bids': [['10', '1']], 'asks': [['9', '1']]}, R)
        t, _ = feeds.parse_coinbase(dict(CB_T, bid='70000', ask='60000'), CB_B, R)
        self.assertIsNone(t['bid']); self.assertIsNone(t['ask'])
    def test_poll_stamps_receive_time_after_fetch_and_stores(self):
        clock = iter([R]); responses = {feeds.URLS['bitstamp'][0]: BS_T, feeds.URLS['bitstamp'][1]: BS_B}
        t, b = feeds.poll('bitstamp', responses.__getitem__, lambda: next(clock))
        db = qstore.connect(':memory:'); feeds.store(db, t, b); db.commit()
        self.assertEqual(db.execute('SELECT recv_ts,exch_ts FROM exchange_ticks').fetchone(), (R, 999.0))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM exchange_books').fetchone()[0], 2)
    def test_store_refuses_bot_database(self):
        with self.assertRaises(ValueError): qstore.connect('/x/research.sqlite')


if __name__ == '__main__':
    unittest.main()
