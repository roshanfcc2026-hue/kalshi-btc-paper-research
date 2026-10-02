"""Chronological feedback evaluation. Never changes historical predictions."""
import json, math, shutil, time

def save_version(path,content):
    if path.exists():
        archive=path.parent/'model-history'; archive.mkdir(exist_ok=True)
        shutil.copy2(path,archive/(path.stem+'-'+str(time.time_ns())+'.json'))
    temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(content,indent=2)); temp.replace(path)

def available_before(rows,cutoff,embargo=60):
    return [r for r in rows if r.get('label_available_ts',r['ts'])<cutoff-embargo]

def walk_forward(rows,fit,predict):
    # Only the development portion is supplied. Final holdout remains separate.
    start=max(1,len(rows)//2); size=max(1,(len(rows)-start+2)//3); folds=[]
    for index in range(start,len(rows),size):
        test=rows[index:index+size]
        train=available_before(rows[:index],test[0]['ts'])
        if len(train)<20: continue
        w=fit(train); brier=market_brier=logloss=0
        for row in test:
            p=predict(w,row['x']); market=(row['ya']+1-row['na'])/2
            brier+=(p-row['y'])**2; market_brier+=(market-row['y'])**2
            logloss-=math.log(max(1e-12,p if row['y'] else 1-p))
        folds.append(dict(training_markets=len(train),validation_markets=len(test),
                          train_labels_available_through=max(r.get('label_available_ts',r['ts']) for r in train),
                          validation_start=test[0]['ts'],brier=brier/len(test),market_mid_brier=market_brier/len(test),log_loss=logloss/len(test)))
    return {'kind':'expanding chronological folds; 60-second label embargo','folds':folds,
            'all_folds_beat_market':bool(folds) and all(f['brier']<f['market_mid_brier'] for f in folds)}

def feedback(evaluation,required,book_count,live_count):
    q=evaluation.get('volatility-proxy-v1',{}); market=evaluation.get('market-mid-v1',{})
    return dict(updated_at=time.time(),first_forecast_summary=q,market_benchmark=market,
        book_training_markets=book_count,live_training_markets=live_count,required_training_markets=required,
        training_status='enough data to evaluate candidate' if live_count>=required else 'collecting; insufficient settled markets',
        live_model_approved=False,notes=[
          'Hundreds of intrawindow snapshots do not count as hundreds of independent outcomes.',
          'Mistakes are retained. Later forecasts never replace a first forecast in first-call scoring.',
          'Near-50% misses are uncertain calls, not evidence of a repeatable market pattern.',
          'Lower Brier and log loss measure probability quality; hit rate alone does not establish profits.',
          'The fixed proxy formula has not learned until a separate model is trained and tested.',
          'Compare opening, middle and late forecast cohorts separately.',
          'No 100% accuracy, complete-data access or superiority to professional traders is established.'])
