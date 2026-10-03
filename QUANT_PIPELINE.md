# Quant research pipeline (PAPER ONLY)

No live orders, no credentials. Writes only `quant.sqlite`; reads `research.sqlite` read-only.

| Layer | Module | Purpose |
|---|---|---|
| 1 Data | `quant/feeds.py`, `proxy.py`, `kalshi_book.py`, `dq_report.py` | 4-exchange BTC feed, median spot proxy, Kalshi top-5 books, data-quality report |
| 2 Signals | `quant/signals/`, `asof.py`, `evaluate_signal.py`, `freeze.py` | S1-S5, look-ahead-free inputs, standalone value, code-hash freeze |
| 3 Combination | `quant/combo.py`, `isotonic.py`, `logit.py` | ridge logistic + bootstrap CIs + isotonic -> frozen `combo-v1` |
| 4 Costs | `quant/costs.py` | Kalshi fee (UNVERIFIED rate, see file) + depth-walking fills |
| 5 Risk | `quant/risk.py` | EV margin, quarter-Kelly 1% cap, 3%/5%/10% limits, manual-reset halt |
| 6 Execution | `quant/execution.py` | paper taker fills; separate OPTIMISTIC passive research |
| 7 Monitoring | `quant/monitor.py`, `runner.py` | heartbeat watchdog, kill switch, 7:00 AM Pacific scorecard |
| 8 Discipline | `quant/discipline.py` | walk-forward blocks, grouped bootstrap, decay, promotion rule (never auto) |

## Run (PowerShell, bot folder, each in its own window)
```powershell
py -3 -m unittest discover -s . -p "test_*.py"
py -3 -X utf8 bot.py monitor --cycles 0               # existing recorder: official results
py -3 -m quant.collector --cycles 0 --interval 5      # data + forecasts + paper decisions
py -3 -m quant.monitor                                # heartbeat watchdog
py -3 -m quant.dashboard                              # http://127.0.0.1:8766 (read-only)
```
## Research steps
```powershell
py -3 -m quant.dq_report quant.sqlite
py -3 -m quant.evaluate_signal quant.sqlite research.sqlite          # after >=50 settled markets
py -3 -m quant.combo quant.sqlite research.sqlite --train-markets 200 # ONCE; freezes combo-v1
py -3 -m quant.discipline quant.sqlite research.sqlite               # walk-forward, decay, promotion
py -3 -m quant.risk status
py -3 -m quant.risk reset --by "your name"                            # only after reviewing a halt
```
Scorecards are written to `scorecards/` daily after 7:00 AM Pacific while the collector runs.
Nothing trades until `combo-v1` is frozen. A "promotion candidate" is a prompt for human review,
not permission to trade, and no result here implies profitability.
