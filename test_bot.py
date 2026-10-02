import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import bot
import live
import research

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=bot.connect(':memory:')
        self.c=json.loads((bot.ROOT/'config.json').read_text())
    def tearDown(self): self.db.close()
    def test_bid_complements_and_depth(self):
        b={'orderbook_fp':{'yes_dollars':[['0.4','2'],['0.45','3']], 'no_dollars':[['0.5','7']]}}
        self.assertEqual(bot.quotes(b),(.45,.5,.5,.55,3.,7.))
    def test_empty_and_crossed_books(self):
        for b in [dict(yes_dollars=[],no_dollars=[]),dict(yes_dollars=[['0.6','1']],no_dollars=[['0.6','1']])]:
            with self.assertRaises(ValueError): bot.quotes({'orderbook_fp':b})
    def test_risk_cash_and_exposure(self):
        self.db.execute("INSERT INTO positions VALUES('a','yes',0.5,0.02,0,NULL,NULL)")
        self.assertEqual(bot.risk(self.db,self.c,'a',.52),'already traded')
        self.c['max_open_exposure']=.6
        self.assertEqual(bot.risk(self.db,self.c,'b',.52),'exposure limit')
    def test_fee_and_no_edge(self):
        self.assertEqual(bot.fee(.5,.07),.02)
        self.assertEqual(bot.choose(.5,.5,.5,self.c)[0],'skip')
    def test_official_settlement_and_idempotency(self):
        self.db.execute("INSERT INTO markets VALUES('a','{}','')")
        self.db.execute("INSERT INTO positions VALUES('a','yes',0.5,0.02,0,NULL,NULL)")
        with patch('bot.get',return_value={'market':{'result':'yes'}}): bot.settle(self.db); bot.settle(self.db)
        self.assertAlmostEqual(self.db.execute('SELECT pnl FROM positions').fetchone()[0],.48)
    def test_market_grouping_and_order(self):
        for t,ts,y in [('b',2,'no'),('a',1,'yes')]:
            self.db.execute('INSERT INTO markets VALUES(?,?,?)',(t,'{}',y))
            for offset in [0,1]:
                self.db.execute('INSERT INTO snapshots(ticker,ts,features,yes_ask,no_ask) VALUES(?,?,?,?,?)',(t,ts+offset,json.dumps([1,0,0,0,.5]),.5,.5))
        rows=bot.dataset(self.db)
        self.assertEqual([r['ticker'] for r in rows],['a','b'])
        self.assertEqual(len(rows),2)
    def test_training_gate_and_experimental_rl(self):
        with self.assertRaises(ValueError): bot.train(self.db,self.c)
        for i in range(220):
            t=str(i); y=int(i%2==0)
            self.db.execute('INSERT INTO markets VALUES(?,?,?)',(t,'{}','yes' if y else 'no'))
            x=[1,.2 if y else -.2,.2,0,.5]
            self.db.execute('INSERT INTO snapshots(ticker,ts,features,yes_ask,no_ask) VALUES(?,?,?,?,?)',(t,i*900,json.dumps(x),.52,.52))
        with tempfile.TemporaryDirectory() as tmp, patch('bot.ROOT',Path(tmp)):
            bot.train(self.db,self.c); bot.train_rl(self.db,self.c)
            self.assertEqual(json.loads((Path(tmp)/'model.json').read_text())['trained_through'],153*900)
            self.assertFalse(json.loads((Path(tmp)/'rl-experiment.json').read_text())['deployable'])

    def live_fixture(self,price=100):
        m={'ticker':'KXBTC15M-test','floor_strike':100,'close_time':'1970-01-01T00:15:00Z','strike_type':'greater_or_equal'}
        evidence={'price':price,'tick_ts':300,'received':300,'sigma_1m':.001,'momentum_5m':0,'last_closed_candle':240}
        return m,evidence
    def test_live_probability_and_final_minute(self):
        m,e=self.live_fixture()
        f=live.estimate(m,[1,0,0,0,.5],e,300,self.c)
        self.assertAlmostEqual(f['p_yes'],.5); self.assertEqual(len(f['features']),8)
        e['price']=100.1
        self.assertGreater(live.estimate(m,[1,0,0,0,.5],e,300,self.c)['p_yes'],.5)
        with self.assertRaises(ValueError): live.estimate(m,[1,0,0,0,.5],e,850,self.c)
    def test_live_stale_future_and_late_predictions(self):
        m,e=self.live_fixture(); e['tick_ts']=200
        with self.assertRaises(ValueError): live.estimate(m,[1,0,0,0,.5],e,300,self.c)
        with self.assertRaises(ValueError): live.write_prediction(self.db,'a','test',2,4,3,.5,{})
        with self.assertRaises(ValueError): live.write_prediction(self.db,'a','test',2,2,3,float('nan'),{})
    def test_invalid_feed_cannot_become_extreme_probability(self):
        for key,value in [('price',float('nan')),('price',float('inf')),('price',0),
                          ('sigma_1m',float('nan')),('sigma_1m',0),
                          ('momentum_5m',float('inf')),('tick_ts',301),
                          ('received',float('nan'))]:
            with self.subTest(key=key,value=value):
                m,e=self.live_fixture();e[key]=value
                with self.assertRaises(ValueError):live.estimate(m,[1,0,0,0,.5],e,300,self.c)
        m,e=self.live_fixture();m['floor_strike']=float('nan')
        with self.assertRaises(ValueError):live.estimate(m,[1,0,0,0,.5],e,300,self.c)
    def test_outcome_scoring_one_forecast_per_market(self):
        self.db.execute("INSERT INTO markets VALUES('a','{}','yes')")
        for t,p in [(1,.7),(2,.2)]: live.write_prediction(self.db,'a','test',t,t,3,p,{})
        live.score(self.db); live.score(self.db)
        result=live.evaluation(self.db)['test']
        self.assertEqual(result['settled_markets'],1); self.assertEqual(result['accuracy'],1)
        self.assertAlmostEqual(result['brier'],.09)
        self.assertEqual(result['correct'],1); self.assertEqual(result['wrong'],0)
    def test_mistake_ledger_and_label_embargo(self):
        self.db.execute("INSERT INTO markets VALUES('a','{}','yes')")
        live.write_prediction(self.db,'a','test',1,1,900,.49,{})
        live.write_prediction(self.db,'a','test',2,2,900,.99,{})
        live.score(self.db); summary=live.evaluation(self.db)['test']
        self.assertEqual(summary['wrong'],1)
        self.assertEqual(summary['mistakes'][0]['predicted'],'no')
        self.assertTrue(summary['mistakes'][0]['weak_direction'])
        self.assertEqual(summary['horizon_cohorts']['opening (10–15 min left)']['settled_markets'],1)
        rows=[{'ts':1,'label_available_ts':200},{'ts':2,'label_available_ts':10}]
        self.assertEqual(research.available_before(rows,100),[rows[1]])
    def test_model_versions_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.json'
            research.save_version(path,{'version':1}); research.save_version(path,{'version':2})
            old=list((Path(tmp)/'model-history').glob('*.json'))
            self.assertEqual(len(old),1); self.assertEqual(json.loads(old[0].read_text())['version'],1)
            self.assertEqual(json.loads(path.read_text())['version'],2)
    def test_learning_report_uses_configured_threshold(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bot.ROOT',Path(tmp)):
            bot.report(self.db,self.c)
            result=json.loads((Path(tmp)/'learning-report.json').read_text())
            self.assertEqual(result['required_training_markets'],200)
            self.assertEqual(result['live_training_markets'],0)
            self.assertFalse(result['live_model_approved'])
    def test_revised_outcome_preserves_forecast(self):
        self.db.execute("INSERT INTO markets VALUES('a','{}','no')")
        live.write_prediction(self.db,'a','test',1,1,3,.7,{})
        live.score(self.db)
        self.db.execute("UPDATE markets SET result='yes' WHERE ticker='a'")
        live.score(self.db); live.score(self.db)
        self.assertEqual(self.db.execute('SELECT p_yes,result,correct FROM predictions').fetchone(),(.7,'yes',1))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM outcome_revisions').fetchone()[0],1)
    def test_bad_candidate_does_not_replace_active_model(self):
        rows=[dict(ticker=str(i),ts=i*900,label_available_ts=i*900,x=[1,0,0,0,.5,0,0,1],ya=.52,na=.52,y=i%2) for i in range(220)]
        with tempfile.TemporaryDirectory() as tmp,patch('bot.ROOT',Path(tmp)),patch('live.training_rows',return_value=rows):
            active=Path(tmp)/'live-model.json'; active.write_text('{"unchanged":true}')
            bot.train_live(self.db,self.c)
            self.assertEqual(json.loads(active.read_text()),{'unchanged':True})
            self.assertFalse(json.loads((Path(tmp)/'live-candidate.json').read_text())['approved_for_paper'])
    def test_analyst_parsing_and_missing_configuration(self):
        valid={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(dict(p_yes=.6,stance='yes',summary='Experimental',limitations=[]))}]}]}
        self.assertEqual(live.parse_analyst(valid)['p_yes'],.6)
        valid['status']='incomplete'
        with self.assertRaises(ValueError): live.parse_analyst(valid)
        with patch.dict('os.environ',{},clear=True):
            self.assertIn('needs',live.review(self.db,{}, {},.5,{'openai_enabled':True})['status'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM analyst_calls').fetchone()[0],0)
    def test_candles_exclude_open_candle(self):
        candles=[[t,90,110,100,100+.01*t,1] for t in range(0,2460,60)]
        with patch('live.time.time',return_value=2401),patch('live.fetch',side_effect=[{'time':'1970-01-01T00:40:00Z','price':'100'},candles]):
            e=live.spot(self.c)
        self.assertEqual(e['closed_candles'],40)
        self.assertEqual(e['last_closed_candle'],2340)

if __name__=='__main__': unittest.main()
