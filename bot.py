"""Public-data research bot. No credentials and no live order submission."""
import argparse, datetime as dt, json, math, os, random, sqlite3, time
import urllib.request, urllib.parse
from pathlib import Path
import live
import research

ROOT = Path(__file__).resolve().parent
BASE = 'https://external-api.kalshi.com/trade-api/v2'

def get(path, **params):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'KalshiResearch/0.1'}), timeout=15) as r:
                return json.load(r)
        except Exception:
            if attempt == 2: raise
            time.sleep(2 ** attempt)

def connect(path):
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS markets(ticker TEXT PRIMARY KEY, metadata TEXT, result TEXT);
    CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, ticker TEXT, ts REAL, received REAL,
      yes_ask REAL, no_ask REAL, yes_size REAL, no_size REAL, features TEXT, raw TEXT);
    CREATE TABLE IF NOT EXISTS positions(ticker TEXT PRIMARY KEY, side TEXT, price REAL, fee REAL,
      opened REAL, result TEXT, pnl REAL);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, ticker TEXT, decision TEXT);
    CREATE INDEX IF NOT EXISTS snapshots_market_time ON snapshots(ticker,ts);
    ''')
    live.setup(db)
    return db

def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

def quotes(book):
    b = book['orderbook_fp']
    yes = [(float(p), float(q)) for p,q in b.get('yes_dollars',[]) if float(q)>0]
    no = [(float(p), float(q)) for p,q in b.get('no_dollars',[]) if float(q)>0]
    if not yes or not no: raise ValueError('Empty book')
    yb, ys = max(yes); nb, ns = max(no)
    if not (0 < yb < 1 and 0 < nb < 1 and yb+nb <= 1):
        raise ValueError('Invalid or crossed book')
    return yb, 1-nb, nb, 1-yb, ys, ns

def features(m, book, now):
    yb, ya, nb, na, ys, ns = quotes(book)
    remaining = stamp(m['close_time'])-now
    if not 0 < remaining <= 900: raise ValueError('Outside 15-minute window')
    # Book-only baseline. The contract settlement index must be added before live use.
    mid=(yb+ya)/2
    return [1., (mid-.5)*2, (ya-yb)*10, (ys-ns)/(ys+ns), remaining/900], (ya,na,ns,ys)

def probability(w,x):
    z=max(-30,min(30,sum(a*b for a,b in zip(w,x))))
    return 1/(1+math.exp(-z))

def fit(rows):
    dimensions=len(rows[0]['x'])
    w=[0.]*dimensions
    for _ in range(150):
        grad=[0.]*dimensions
        for row in rows:
            p=probability(w,row['x'])
            for j in range(dimensions): grad[j]+=(p-row['y'])*row['x'][j]
        for j in range(dimensions): w[j]-=.2*(grad[j]/len(rows)+(.01*w[j] if j else 0))
    return w

def fee(price, rate):
    # Conservative cent rounding for a one-contract taker fill.
    return math.ceil((rate*price*(1-price)-1e-12)*100)/100

def choose(p, ya, na, config):
    candidates=[('yes',ya,p),('no',na,1-p)]
    side,price,chance=max(candidates,key=lambda v:v[2]-v[1]-fee(v[1],config['fee_rate']))
    edge=chance-price-fee(price,config['fee_rate'])-config['slippage']
    return (side,price,edge) if edge>=config['minimum_edge'] else ('skip',0,edge)

def settle(db):
    tickers=[ticker for ticker,metadata,result in db.execute('SELECT ticker,metadata,result FROM markets')
             if not result or json.loads(metadata).get('status') not in ('finalized','settled')]
    # Bounded batch: old unresolved markets are retained for future retries.
    for ticker in tickers[:100]:
        try:
            m=get('/markets/'+urllib.parse.quote(ticker,safe=''))['market']
            result=m.get('result','')
            if result not in ('yes','no'): continue
            db.execute('UPDATE markets SET result=?,metadata=? WHERE ticker=?',(result,json.dumps(m),ticker))
            pos=db.execute('SELECT side,price,fee FROM positions WHERE ticker=? AND result IS NULL',(ticker,)).fetchone()
            if pos:
                pnl=float(pos[0]==result)-pos[1]-pos[2]
                db.execute('UPDATE positions SET result=?,pnl=? WHERE ticker=?',(result,pnl,ticker))
        except Exception as e: print('Settlement deferred:',ticker,str(e))
    db.commit()
    live.score(db)

def risk(db,c,ticker,cost):
    if db.execute('SELECT 1 FROM positions WHERE ticker=?',(ticker,)).fetchone(): return 'already traded'
    exposure=db.execute('SELECT COALESCE(SUM(price+fee),0) FROM positions WHERE result IS NULL').fetchone()[0]
    if exposure+cost>c['max_open_exposure']: return 'exposure limit'
    today=dt.datetime.now(dt.timezone.utc).date().isoformat()
    daily=db.execute("SELECT COALESCE(SUM(pnl),0) FROM positions WHERE date(opened,'unixepoch')=?",(today,)).fetchone()[0]
    if daily <= -c['daily_loss_limit']: return 'daily loss limit'
    total=db.execute('SELECT COALESCE(SUM(pnl),0) FROM positions').fetchone()[0]
    if c['paper_bankroll']+total-exposure<cost: return 'insufficient paper cash'
    return None

def collect(db,c,paper=False):
    settle(db)
    model=json.loads((ROOT/'model.json').read_text()) if (ROOT/'model.json').exists() else None
    live_model=json.loads((ROOT/'live-model.json').read_text()) if (ROOT/'live-model.json').exists() else None
    challenger=json.loads((ROOT/'live-candidate.json').read_text()) if (ROOT/'live-candidate.json').exists() else None
    evidence=None; spot_error=None
    if c.get('live_predictions',True):
        try: evidence=live.spot(c)
        except Exception as e: spot_error=str(e); print('Live forecast unavailable:',spot_error)
    count=0
    for series in c['series']:
        cursor=''
        while True:
            data=get('/markets',series_ticker=series,status='open',limit=100,cursor=cursor)
            for m in data['markets']:
                ticker=m['ticker']; now=time.time()
                db.execute('INSERT INTO markets VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE SET metadata=excluded.metadata',
                           (ticker,json.dumps(m),m.get('result','')))
                try:
                    book=get('/markets/'+urllib.parse.quote(ticker,safe='')+'/orderbook',depth=10)
                    received=time.time(); x,(ya,na,ys,ns)=features(m,book,received)
                    if received-now>c['max_request_seconds']: raise ValueError('Slow order book request; forecast skipped')
                    db.execute('INSERT INTO snapshots(ticker,ts,received,yes_ask,no_ask,yes_size,no_size,features,raw) VALUES(?,?,?,?,?,?,?,?,?)',
                        (ticker,now,received,ya,na,ys,ns,json.dumps(x),json.dumps(book)))
                    count+=1
                    decision={'action':'observe','reason':'No validated model'}
                    if evidence:
                        try:
                            forecast=live.estimate(m,x,evidence,received,c)
                            forecast.update(yes_ask=ya,no_ask=na)
                            market_p=(ya+1-na)/2
                            live.write_prediction(db,ticker,'volatility-proxy-v1',received,received,forecast['close_ts'],forecast['p_yes'],forecast)
                            live.write_prediction(db,ticker,'market-mid-v1',received,received,forecast['close_ts'],market_p,{'book_features':x})
                            if live_model and live_model.get('feature_schema')==live.SCHEMA and live_model['trained_at']<received:
                                lp=probability(live_model['weights'],forecast['features'])
                                live.write_prediction(db,ticker,'supervised-live:'+str(live_model['trained_at']),received,received,forecast['close_ts'],lp,
                                    dict(forecast,model_trained_at=live_model['trained_at'],calibrated=False))
                            if challenger and challenger.get('feature_schema')==live.SCHEMA and challenger['trained_at']<received:
                                cp=probability(challenger['weights'],forecast['features'])
                                live.write_prediction(db,ticker,'supervised-challenger:'+str(challenger['trained_at']),received,received,forecast['close_ts'],cp,
                                    dict(forecast,model_trained_at=challenger['trained_at'],calibrated=False,role='forward research challenger; not active strategy'))
                            decision.update(p_yes=forecast['p_yes'],forecast_source='volatility-proxy-v1',forecast_status='experimental',
                                            analyst=live.review(db,m,forecast,market_p,c))
                        except Exception as e: decision['forecast_unavailable']=str(e)
                    elif spot_error: decision['forecast_unavailable']=spot_error
                    if paper and model and model.get('approved_for_paper'):
                        p=probability(model['weights'],x); side,price,edge=choose(p,ya,na,c)
                        decision={'action':side,'p_yes':p,'edge':edge}
                        reason=risk(db,c,ticker,price+fee(price,c['fee_rate']))
                        if side!='skip':
                            reason=reason or ('fees/rules not reviewed' if not c['rules_and_fees_reviewed'] else None)
                            reason=reason or ('stale request' if received-now>c['max_request_seconds'] else None)
                            reason=reason or ('book expired during analysis' if time.time()-received>c['max_request_seconds'] else None)
                            reason=reason or ('insufficient displayed depth' if (ys if side=='yes' else ns)<1 else None)
                            if reason: decision.update(action='skip',reason=reason)
                            else:
                                fill=min(.9999,price+c['slippage'])
                                db.execute('INSERT INTO positions VALUES(?,?,?,?,?,NULL,NULL)',(ticker,side,fill,fee(fill,c['fee_rate']),received))
                                decision['fill_assumption']='one contract at observed ask plus slippage; not guaranteed executable'
                    db.execute('INSERT INTO audit(ts,ticker,decision) VALUES(?,?,?)',(received,ticker,json.dumps(decision)))
                    print(ticker,json.dumps(decision))
                except Exception as e: print('Skipped:',ticker,str(e))
            db.commit()
            cursor=data.get('cursor','')
            if not cursor: break
    print('Recorded snapshots:',count)

def dataset(db):
    # One observation per market; chronological market split prevents repeated-window leakage.
    rows=[]
    for ticker,result,metadata in db.execute("SELECT ticker,result,metadata FROM markets WHERE result IN ('yes','no')"):
        r=db.execute('SELECT ts,features,yes_ask,no_ask FROM snapshots WHERE ticker=? ORDER BY ts LIMIT 1',(ticker,)).fetchone()
        if r:
            meta=json.loads(metadata); available=meta.get('settlement_ts') or meta.get('expected_expiration_time')
            label_ts=stamp(available) if available else (stamp(meta['close_time'])+300 if meta.get('close_time') else r[0])
            rows.append(dict(ticker=ticker,ts=r[0],x=json.loads(r[1]),ya=r[2],na=r[3],y=int(result=='yes'),label_available_ts=label_ts))
    return sorted(rows,key=lambda r:r['ts'])

def metrics(rows,w,c):
    pnl=[]; brier=0; market_brier=0
    for r in rows:
        p=probability(w,r['x']); brier+=(p-r['y'])**2
        market_brier+=((r['ya']+1-r['na'])/2-r['y'])**2
        side,price,_=choose(p,r['ya'],r['na'],c)
        if side!='skip':
            fill=min(.9999,price+c['slippage'])
            pnl.append(float(r['y']==(side=='yes'))-fill-fee(fill,c['fee_rate']))
    mean=sum(pnl)/len(pnl) if pnl else 0
    se=math.sqrt(sum((v-mean)**2 for v in pnl)/(len(pnl)-1)/len(pnl)) if len(pnl)>1 else 1
    return dict(markets=len(rows),brier=brier/len(rows),market_mid_brier=market_brier/len(rows),
                trades=len(pnl),net_pnl=sum(pnl),mean_pnl=mean,mean_pnl_lower_95=mean-1.96*se)

def train(db,c):
    rows=dataset(db)
    if len(rows)<c['min_training_markets']: raise ValueError(f"Need {c['min_training_markets']} settled markets with pre-close snapshots; have {len(rows)}")
    split=int(len(rows)*.7); development=research.available_before(rows[:split],rows[split]['ts'])
    if len(development)<20: raise ValueError('Insufficient training labels available before holdout')
    forward=research.walk_forward(development,fit,probability)
    w=fit(development); report=metrics(rows[split:],w,c)
    # Validation is diagnostic only; independent forward paper evidence is still required for live.
    approved=forward['all_folds_beat_market'] and report['trades']>=30 and report['mean_pnl_lower_95']>0 and report['brier']<report['market_mid_brier']
    model=dict(weights=w,trained_at=time.time(),trained_through=development[-1]['ts'],approved_for_paper=approved,validation=report,walk_forward=forward)
    research.save_version(ROOT/'model.json',model)
    print(json.dumps(model,indent=2))

def train_live(db,c):
    live.score(db); rows=live.training_rows(db)
    if len(rows)<c['min_training_markets']:
        raise ValueError(f"Need {c['min_training_markets']} settled markets with live forecast evidence; have {len(rows)}")
    split=int(len(rows)*.7); development=research.available_before(rows[:split],rows[split]['ts'])
    if len(development)<20: raise ValueError('Insufficient labels available before holdout')
    w=fit(development); validation=metrics(rows[split:],w,c)
    model=dict(weights=w,feature_schema=live.SCHEMA,trained_at=time.time(),trained_through=development[-1]['ts'],
               validation=validation,walk_forward=research.walk_forward(development,fit,probability),
               approved_for_paper=False,notes='Research challenger; recorded forward separately; active forecast unchanged')
    research.save_version(ROOT/'live-candidate.json',model); print(json.dumps(model,indent=2))

def state(r):
    return str((int(r['x'][1]*5),int(r['x'][2]*2),int(r['x'][3]*2),min(2,int(r['x'][4]*3))))

def train_rl(db,c):
    rows=dataset(db)
    if len(rows)<c['min_training_markets']: raise ValueError('Insufficient settled data for RL experiment')
    split=int(len(rows)*.7); development=research.available_before(rows[:split],rows[split]['ts'])
    if len(development)<20: raise ValueError('Insufficient labels available before RL validation')
    table={}; counts={}; rng=random.Random(7)
    # Terminal, single-entry contextual bandit: gamma=0. This is not a multi-step execution agent.
    for _ in range(20):
        for r in development:
            s=state(r); q=table.setdefault(s,[0.,0.,0.]); n=counts.setdefault(s,[0,0,0])
            action=rng.randrange(3) if rng.random()<.2 else max(range(3),key=lambda a:q[a])
            reward=0.
            if action:
                price=min(.9999,(r['ya'] if action==1 else r['na'])+c['slippage'])
                reward=float(r['y']==(action==1))-price-fee(price,c['fee_rate'])
            n[action]+=1; q[action]+=(reward-q[action])/n[action]
    rewards=[]
    for r in rows[split:]:
        q=table.get(state(r),[0.,0.,0.]); a=max(range(3),key=lambda k:q[k])
        if a:
            price=min(.9999,(r['ya'] if a==1 else r['na'])+c['slippage'])
            rewards.append(float(r['y']==(a==1))-price-fee(price,c['fee_rate']))
    output=dict(kind='experimental terminal contextual bandit',q=table,validation_trades=len(rewards),validation_pnl=sum(rewards),deployable=False)
    research.save_version(ROOT/'rl-experiment.json',output)
    print(json.dumps({k:v for k,v in output.items() if k!='q'},indent=2))

def report(db,c):
    live.score(db)
    output={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['markets','snapshots','positions']}
    output['settled_training_markets']=len(dataset(db))
    output['settled_paper_pnl']=db.execute('SELECT COALESCE(SUM(pnl),0) FROM positions').fetchone()[0]
    output['updated_at']=time.time()
    output['forecast_evaluation']=live.evaluation(db)
    output['learning']=research.feedback(output['forecast_evaluation'],c['min_training_markets'],output['settled_training_markets'],len(live.training_rows(db)))
    (ROOT/'learning-report.json').write_text(json.dumps(output['learning'],indent=2))
    output['recent_predictions']=live.latest(db)
    output['recent_decisions']=[dict(ticker=r[0],decision=json.loads(r[1])) for r in db.execute('SELECT ticker,decision FROM audit ORDER BY id DESC LIMIT 10')]
    target=ROOT/'status.json'; temporary=ROOT/'status.tmp'
    temporary.write_text(json.dumps(output,indent=2)); temporary.replace(target)
    print(json.dumps({k:v for k,v in output.items() if k!='recent_predictions'},indent=2))

def main():
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['collect','monitor','paper','train','train-live','train-rl','report']); p.add_argument('--cycles',type=int,default=1)
    p.add_argument('--db',default=str(ROOT/'research.sqlite')); args=p.parse_args()
    c=json.loads((ROOT/'config.json').read_text()); db=connect(args.db)
    collector_lock=None
    try:
        if args.command in ['collect','monitor','paper']:
            # OS lock is released on exit/crash. All collector invocations use the same DB-specific lock.
            import msvcrt
            collector_lock=open(str(args.db)+'.collector.lock','a+b')
            collector_lock.seek(0); collector_lock.write(b'0'); collector_lock.flush(); collector_lock.seek(0)
            try: msvcrt.locking(collector_lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: raise RuntimeError('A collector is already running for this database')
            (ROOT/'recorder.json').write_text(json.dumps(dict(pid=os.getpid(),started_at=time.time(),command=args.command)))
            i=0
            while args.cycles==0 or i<args.cycles:
                if (ROOT/'STOP').exists(): print('Stop requested'); break
                try: collect(db,c,args.command=='paper'); report(db,c)
                except Exception as e:
                    db.rollback(); print('Collector cycle failed:',str(e),flush=True)
                    if args.cycles==1: raise
                i+=1
                if args.cycles==0 or i<args.cycles: time.sleep(c['poll_seconds'])
        elif args.command=='train': train(db,c)
        elif args.command=='train-live': train_live(db,c)
        elif args.command=='train-rl': train_rl(db,c)
        else: report(db,c)
    finally:
        if collector_lock: collector_lock.close()
        db.close()

if __name__=='__main__': main()
