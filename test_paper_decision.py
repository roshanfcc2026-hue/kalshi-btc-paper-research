"""Independent decision/fee/sizing fixtures; no live data, orders or credentials."""
from decimal import Decimal as D
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from paper_decision import Policy, fee_evidence_status, json_safe, kalshi_fee, read_inputs, replay, size_order, trade_signal


class SignalTests(unittest.TestCase):
    def test_yes_and_no_executable_edge(self):
        self.assertEqual(trade_signal(.8,.6,.41,.02),'BUY_YES')
        self.assertEqual(trade_signal(.4,.7,.31,{'yes':.01,'no':.02}),'BUY_NO')

    def test_strict_margin_and_fee_inclusion(self):
        self.assertEqual(trade_signal(D('.60'),D('.55'),D('.46'),D('.02')),'SKIP')
        self.assertEqual(trade_signal(D('.6001'),D('.55'),D('.46'),D('.02')),'BUY_YES')
        self.assertEqual(trade_signal(.6,.57,.44,.02),'SKIP')
        self.assertEqual(trade_signal(.6,.6,.41,0),'SKIP')

    def test_invalid_inputs_fail_closed(self):
        for bad in [None,float('nan'),float('inf'),-0.1,1.1,'invalid']:
            with self.subTest(probability=bad):
                self.assertEqual(trade_signal(bad,.6,.41,.02),'SKIP')
        for bad in [None,float('nan'),float('inf'),-1,0,1,'invalid']:
            with self.subTest(ask=bad):
                self.assertEqual(trade_signal(.8,bad,.41,.02),'SKIP')
        for bad in [None,float('nan'),float('inf'),-.01,{}, {'yes':.01}, {'yes':.01,'no':None}]:
            with self.subTest(fee=bad):
                self.assertEqual(trade_signal(.8,.6,.41,bad),'SKIP')


class FeeTests(unittest.TestCase):
    def test_microdollar_then_total_balance_rounding(self):
        self.assertEqual(kalshi_fee(D('.5')),D('.0175'))
        self.assertEqual(kalshi_fee(D('.37')),D('.0164'))
        # Exchange worked example: align the TOTAL debit, not the fee alone.
        self.assertEqual(kalshi_fee(D('.055'),balance_precision='.01'),D('.005'))
        self.assertEqual(kalshi_fee(D('.5'),balance_precision='.01'),D('.02'))

    def test_aggregated_quantity_and_multiplier(self):
        self.assertEqual(kalshi_fee(D('.12'),7),D('.0518'))
        self.assertEqual(kalshi_fee(D('.12'),8),D('.0592'))
        self.assertEqual(kalshi_fee(D('.5'),multiplier=2),D('.0350'))

    def test_invalid_fee_arguments_raise_value_error(self):
        for bad in [None,float('nan'),float('inf'),-.1,1.1,'invalid']:
            with self.subTest(price=bad), self.assertRaises(ValueError):
                kalshi_fee(bad)
        for bad in [-1,1.5,float('nan'),None]:
            with self.subTest(quantity=bad), self.assertRaises(ValueError):
                kalshi_fee(.5,quantity=bad)
        for bad in [-1,float('nan'),None]:
            with self.subTest(multiplier=bad), self.assertRaises(ValueError):
                kalshi_fee(.5,multiplier=bad)
        for bad in ['0','-.01','invalid']:
            with self.subTest(precision=bad), self.assertRaises(ValueError):
                kalshi_fee(.5,balance_precision=bad)


class SizingTests(unittest.TestCase):
    def test_policy_is_immutable(self):
        with self.assertRaises(AttributeError):
            Policy().margin=D('0')

    def test_fees_inside_one_percent_cap(self):
        order=size_order(.9,.5,1000,100,100)
        self.assertIsNotNone(order)
        self.assertEqual(order['quantity'],1)
        self.assertEqual(order['fee'],D('.0175'))
        self.assertEqual(order['cost'],D('.5175'))
        self.assertLessEqual(order['cost'],D('1'))
        self.assertGreater(order['ev_per_contract'],D('.03'))
        self.assertGreater(order['kelly_fraction'],D('0'))
        self.assertLessEqual(order['kelly_fraction'],D('.25'))

    def test_exhaustive_quantity_uses_aggregated_fees(self):
        order=size_order(.8,.12,1000,100,100)
        self.assertEqual(order['quantity'],7)
        self.assertEqual(order['cost'],D('.8918'))
        self.assertEqual(order['fee'],D('.0518'))
        self.assertGreater(D('8')*D('.12')+kalshi_fee(D('.12'),8),D('1'))

    def test_free_cash_and_fractional_depth_limits(self):
        order=size_order(.8,.12,1000,100,D('.6'))
        self.assertEqual(order['quantity'],4)
        self.assertLessEqual(order['cost'],D('.6'))
        order=size_order(.8,.12,D('2.9'),100,100)
        self.assertEqual(order['quantity'],2)
        self.assertIsNone(size_order(.8,.12,D('.9'),100,100))
        self.assertIsNone(size_order(.8,.12,1000,100,0))

    def test_exact_margin_and_small_kelly_are_not_forced_orders(self):
        self.assertIsNone(size_order(D('.5475'),D('.5'),1000,100,100))
        # A positive tiny edge cannot force a whole contract beyond Kelly budget.
        self.assertIsNone(size_order(D('.518'),D('.5'),1000,100,100,Policy(margin=0)))

    def test_cost_at_or_above_one_cannot_flip_kelly_sign(self):
        self.assertIsNone(size_order(1,D('.9999'),1000,100,100))
        self.assertIsNone(size_order(.95,.5,1000,100,100,Policy(fee_multiplier=100)))

    def test_invalid_sizing_inputs_fail_closed(self):
        for position in range(5):
            for bad in [None,float('nan'),float('inf'),'invalid']:
                args=[.9,.5,100,100,100]
                args[position]=bad
                with self.subTest(position=position,value=bad):
                    self.assertIsNone(size_order(*args))


class ReplayTests(unittest.TestCase):
    def fixture(self, count=1):
        opened=1800000000
        inputs=dict(schema_version=1,as_of=opened+count*900+30,manifest={},
                    first_calls=[],snapshots=[],markets=[],outcome_revisions=[])
        iso=lambda t:dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()
        for i in range(count):
            start=opened+i*900;observed=start+20;identity=i+1;ticker=f'KXBTC15M-TEST{i}'
            detail=dict(features=[1.0],target=100,yes_ask=.5,no_ask=.51,
                        spot=dict(price=102,sigma_1m=.001,momentum_5m=0,
                                  tick_ts=observed-2,received=observed-1,last_closed_candle=start-60))
            inputs['first_calls'].append(dict(id=identity,ticker=ticker,source='volatility-proxy-v1',
                observed=observed,completed=observed,close_ts=start+900,p_yes=.9,
                detail=json.dumps(detail),feature_snapshot=identity,decision_snapshot=identity))
            inputs['snapshots'].append(dict(id=identity,ticker=ticker,ts=observed-.5,received=observed,
                yes_ask=.5,no_ask=.51,yes_size=100,no_size=100))
            metadata=dict(open_time=iso(start),close_time=iso(start+900),
                          settlement_ts=iso(start+906),result='yes',status='finalized')
            inputs['markets'].append(dict(ticker=ticker,metadata=json.dumps(metadata),result='yes'))
        return inputs

    def source(self,inputs):
        return replay(inputs)['sources']['volatility-proxy-v1']

    def test_later_valid_call_never_replaces_invalid_first(self):
        inputs=self.fixture();later=copy.deepcopy(inputs['first_calls'][0]);later['id']=2
        later['observed']+=1;later['completed']+=1
        inputs['first_calls'][0]['p_yes']=None
        inputs['first_calls'].append(later)
        result=self.source(inputs)
        self.assertEqual(result['first_calls'],1)
        self.assertEqual(result['trades'],0)
        self.assertEqual(result['decisions'][0]['id'],1)
        self.assertEqual(result['skip_reasons'],{'malformed_first_call':1})

    def test_delayed_decision_uses_completion_quote_not_original_quote(self):
        inputs=self.fixture();row=inputs['first_calls'][0];row['completed']+=10
        book=copy.deepcopy(inputs['snapshots'][0]);book.update(id=2,received=row['completed']-1,
                                                            ts=row['completed']-1.5,yes_ask=.9,no_ask=.11)
        inputs['snapshots'].append(book);row['decision_snapshot']=2
        result=self.source(inputs)
        self.assertEqual(result['trades'],0)
        self.assertEqual(result['decisions'][0]['yes_ask'],.9)
        self.assertEqual(result['decisions'][0]['book_received'],row['completed']-1)

    def test_future_decision_quote_and_missing_pair_are_skips(self):
        inputs=self.fixture();row=inputs['first_calls'][0]
        inputs['snapshots'][0]['received']=row['completed']+1
        result=self.source(inputs)
        self.assertEqual(result['trades'],0)
        inputs=self.fixture();inputs['first_calls'][0]['decision_snapshot']=None
        self.assertEqual(self.source(inputs)['skip_reasons'],{'missing_paired_snapshot':1})

    def test_labels_do_not_change_entries_or_recycle_payouts(self):
        inputs=self.fixture(3);wins=self.source(inputs)
        losing=copy.deepcopy(inputs)
        for market in losing['markets']:
            metadata=json.loads(market['metadata']);metadata['result']='no'
            market.update(result='no',metadata=json.dumps(metadata))
        losses=self.source(losing)
        selection=lambda r:[(x['signal'],x['quantity'],x['cost'],x['cash_after']) for x in r['decisions']]
        self.assertEqual(selection(wins),selection(losses))
        self.assertEqual(wins['trades'],3)
        self.assertAlmostEqual(wins['unspent_initial_cash'],100-wins['spent'])
        self.assertEqual(wins['nonrecycled_payouts'],3)
        self.assertEqual(losses['nonrecycled_payouts'],0)

    def test_unsettled_and_future_settlement_never_produce_realized_pnl(self):
        for state in ['pending','future']:
            with self.subTest(state=state):
                inputs=self.fixture();market=inputs['markets'][0];meta=json.loads(market['metadata'])
                if state=='pending':market['result']='';meta.update(result='',status='active')
                else:meta['settlement_ts']=dt.datetime.fromtimestamp(inputs['as_of']+1,dt.timezone.utc).isoformat()
                market['metadata']=json.dumps(meta);result=self.source(inputs)
                self.assertEqual(result['first_calls'],1)
                self.assertEqual(result['pending_trades'],1)
                self.assertEqual(result['simulated_pnl'],0)
                self.assertEqual(result['paired_scored_markets'],0)
                self.assertEqual(result['nonrecycled_payouts'],0)

    def test_sources_have_separate_initial_bankrolls(self):
        inputs=self.fixture();mid=copy.deepcopy(inputs['first_calls'][0])
        mid.update(id=2,source='market-mid-v1',p_yes=.495,detail=json.dumps({'book_features':[1.0]}))
        inputs['first_calls'].append(mid);report=replay(inputs)['sources']
        self.assertEqual(report['market-mid-v1']['unspent_initial_cash'],100)
        self.assertLess(report['volatility-proxy-v1']['unspent_initial_cash'],100)

    def test_final_minute_outside_window_and_stale_quote_reject(self):
        inputs=self.fixture();row=inputs['first_calls'][0]
        row['completed']=row['close_ts']-60
        self.assertEqual(self.source(inputs)['skip_reasons'],{'outside_supported_window':1})
        row['completed']=row['close_ts']+1
        self.assertEqual(self.source(inputs)['trades'],0)
        inputs=self.fixture();row=inputs['first_calls'][0];row['completed']=row['observed']+3.0001
        self.assertEqual(self.source(inputs)['skip_reasons'],{'stale_decision_quote':1})

    def test_future_spot_and_candle_reject(self):
        for future in ['tick','candle']:
            with self.subTest(future=future):
                inputs=self.fixture();row=inputs['first_calls'][0];detail=json.loads(row['detail'])
                if future=='tick':
                    detail['spot']['tick_ts']=row['observed']+1
                    detail['spot']['received']=row['observed']+1
                else:detail['spot']['last_closed_candle']=row['observed']
                row['detail']=json.dumps(detail)
                self.assertEqual(self.source(inputs)['skip_reasons'],{'future_or_stale_spot_features':1})

    def test_unknown_source_is_retained_skip(self):
        inputs=self.fixture();inputs['first_calls'][0]['source']='unknown-source'
        result=replay(inputs)['sources']['unknown-source']
        self.assertEqual(result['first_calls'],1)
        self.assertEqual(result['trades'],0)
        self.assertEqual(result['skip_reasons'],{'unsupported_source_provenance':1})

    def test_settled_only_win_rate_and_loss_win_loss_drawdown(self):
        inputs=self.fixture(4)
        for i in [0,2]:
            market=inputs['markets'][i];meta=json.loads(market['metadata'])
            meta['result']='no';market.update(result='no',metadata=json.dumps(meta))
        market=inputs['markets'][3];meta=json.loads(market['metadata'])
        meta.update(result='',status='active');market.update(result='',metadata=json.dumps(meta))
        result=self.source(inputs)
        self.assertEqual(result['settled_trades'],3)
        self.assertEqual(result['pending_trades'],1)
        self.assertAlmostEqual(result['traded_win_rate'],1/3)
        self.assertAlmostEqual(result['simulated_pnl'],-.5525)
        self.assertAlmostEqual(result['max_drawdown'],.5525)

    def test_nonfinite_first_values_stay_in_skip_denominator(self):
        for bad in [float('nan'),float('inf'),'NaN','Infinity']:
            with self.subTest(probability=bad):
                inputs=self.fixture();inputs['first_calls'][0]['p_yes']=bad
                result=self.source(inputs)
                self.assertEqual(result['first_calls'],1)
                self.assertEqual(result['skips'],1)
                self.assertEqual(result['skip_rate'],1)
                # CLI export wraps the result with json_safe; no invalid JSON numbers.
                json.dumps(json_safe(replay(inputs)),allow_nan=False)

    def test_json_safe_exports_nonfinite_as_explicit_strings(self):
        value=json_safe({'nan':float('nan'),'positive':float('inf'),'negative':-float('inf')})
        encoded=json.dumps(value,allow_nan=False)
        restored=json.loads(encoded)
        self.assertTrue(all(isinstance(item,str) for item in restored.values()))
        self.assertIn(restored['nan'].lower(),{'nan'})
        self.assertIn(restored['positive'].lower(),{'inf','infinity'})
        self.assertIn(restored['negative'].lower(),{'-inf','-infinity'})

    def test_nonnull_fee_waiver_skips_without_removing_probability_score(self):
        inputs=self.fixture();market=inputs['markets'][0];meta=json.loads(market['metadata'])
        meta['fee_waiver_expiration_time']=meta['settlement_ts']
        market['metadata']=json.dumps(meta);result=self.source(inputs)
        self.assertEqual(result['first_calls'],1)
        self.assertEqual(result['trades'],0)
        self.assertEqual(result['skip_reasons'],{'fee_waiver_requires_review':1})
        self.assertEqual(result['paired_scored_markets'],1)

    def test_readonly_freeze_preserves_original_db_and_no_future_quote(self):
        inputs=self.fixture();row=inputs['first_calls'][0]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fixture.sqlite'
            db=sqlite3.connect(path)
            db.executescript('CREATE TABLE predictions(id,ticker,source,observed,completed,close_ts,p_yes,detail);'
                'CREATE TABLE snapshots(id,ticker,ts,received,yes_ask,no_ask,yes_size,no_size);'
                'CREATE TABLE markets(ticker,metadata,result);'
                'CREATE TABLE outcome_revisions(ticker,previous_result,revised_result,observed);')
            fields=['id','ticker','source','observed','completed','close_ts','p_yes','detail']
            db.execute('INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?)',[row[k] for k in fields])
            book=inputs['snapshots'][0];fields=['id','ticker','ts','received','yes_ask','no_ask','yes_size','no_size']
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)',[book[k] for k in fields])
            future=dict(book,id=2,received=row['completed']+1,ts=row['completed']+.5)
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)',[future[k] for k in fields])
            market=inputs['markets'][0]
            db.execute('INSERT INTO markets VALUES(?,?,?)',[market[k] for k in ['ticker','metadata','result']])
            db.commit();db.close();before=hashlib.sha256(path.read_bytes()).hexdigest()
            exported=read_inputs(path)
            self.assertEqual(exported['first_calls'][0]['feature_snapshot'],1)
            self.assertEqual(exported['first_calls'][0]['decision_snapshot'],1)
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())


class FeeEvidenceTests(unittest.TestCase):
    fixture=ReplayTests.fixture
    def evidence_fixture(self):
        inputs=self.fixture()
        meta=json.loads(inputs['markets'][0]['metadata']);meta['event_ticker']='KXBTC15M-EVENT'
        inputs['markets'][0]['metadata']=json.dumps(meta)
        base='https://external-api.kalshi.com/trade-api/v2'
        evidence=dict(checked_at=inputs['as_of'],results=[
            dict(url=base+'/series/KXBTC15M',http_status=200,response={'series':{'fee_type':'quadratic','fee_multiplier':1}}),
            dict(url=base+'/series/fee_changes?series_ticker=KXBTC15M&show_historical=true',http_status=200,response={'series_fee_change_arr':[]}),
            dict(url=base+'/events/fee_changes?event_ticker=KXBTC15M-EVENT&limit=1000',http_status=200,response={'event_fee_changes':[],'cursor':''})])
        return inputs,evidence

    def test_complete_empty_api_history_and_membership_assumptions(self):
        inputs,evidence=self.evidence_fixture()
        for precision in ['0.0001','0.01']:
            with self.subTest(precision=precision):
                policy=Policy(balance_precision=precision)
                status=fee_evidence_status(inputs,evidence,policy)
                self.assertEqual(status['status'],'api_checked_baseline_no_returned_changes')
                self.assertEqual(status['matched_events'],1)
                self.assertIn('assumed',status['membership'])
                report=replay(inputs,policy,evidence)
                self.assertEqual(report['assumptions']['member_precision'],precision)
                self.assertEqual(report['sources']['volatility-proxy-v1']['trades'],1)

    def test_missing_event_or_http_failure_remains_unverified(self):
        inputs,evidence=self.evidence_fixture();evidence['results'].pop()
        self.assertEqual(fee_evidence_status(inputs,evidence,Policy())['status'],'unverified_scenario')
        inputs,evidence=self.evidence_fixture();evidence['results'][-1]['http_status']=503
        self.assertEqual(fee_evidence_status(inputs,evidence,Policy())['status'],'unverified_scenario')
        self.assertEqual(fee_evidence_status(inputs,None,Policy())['status'],'unverified_scenario')

    def test_changes_or_incomplete_pagination_require_review(self):
        for change in ['series','event','cursor']:
            with self.subTest(change=change):
                inputs,evidence=self.evidence_fixture()
                if change=='series':evidence['results'][1]['response']['series_fee_change_arr']=[{'change':'fixture'}]
                elif change=='event':evidence['results'][2]['response']['event_fee_changes']=[{'change':'fixture'}]
                else:evidence['results'][2]['response']['cursor']='next-page'
                status=fee_evidence_status(inputs,evidence,Policy())
                self.assertEqual(status['status'],'fee_changes_require_review')
                result=replay(inputs,fee_evidence=evidence)['sources']['volatility-proxy-v1']
                self.assertEqual(result['trades'],0)
                self.assertEqual(result['skip_reasons'],{'fee_changes_require_review':1})
                self.assertEqual(result['paired_scored_markets'],1)

    def test_mismatched_fee_scenario_skips_but_preserves_probability_score(self):
        inputs,evidence=self.evidence_fixture()
        evidence['results'][0]['response']['series']['fee_multiplier']=2
        self.assertEqual(fee_evidence_status(inputs,evidence,Policy())['status'],'different_fee_scenario')
        result=replay(inputs,fee_evidence=evidence)['sources']['volatility-proxy-v1']
        self.assertEqual(result['trades'],0)
        self.assertEqual(result['skip_reasons'],{'different_fee_scenario':1})
        self.assertEqual(result['paired_scored_markets'],1)


if __name__=='__main__':
    unittest.main()
