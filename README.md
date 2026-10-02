# BTC forecast research

A local Windows dashboard and public-data recorder for Kalshi BTC 15-minute
markets. It records experimental probabilities and evaluates them against
official outcomes. No live order execution is implemented. The dashboard's
real-money strategy remains **SKIP**.

This source-only copy contains no recorded market history, database, trained
models, credentials, frozen registrations or prior performance reports. All
results begin with evidence collected on your own machine.

## Requirements

- Windows and Python 3.12 or later. The collector uses Windows process locking.
- A modern browser. The dashboard binds to `127.0.0.1` only.
- Internet access to the public Kalshi and Coinbase APIs when recording.

The included core uses Python's standard library; no pip installation is needed.
No Kalshi account, trading key or OpenAI API key is needed for the default run.
Optional OpenAI reviews are disabled in the example configuration.
If Node.js is already installed, `node --test test_dashboard_ui.js` also checks
dashboard card mounting and navigation. Node.js is not needed to run the app.

## Start

Open PowerShell in this folder and initialize local files:

```powershell
py -3 -X utf8 init.py
py -3 -X utf8 -m unittest discover -s . -p 'test_*.py'
```

Initialization creates `config.json`, an empty `research.sqlite`, and initial
reports. Repeating initialization preserves existing settings and recorded data.
It does not contact an API, collect evidence, train a model or register a trial.

Run each of these commands in a separate PowerShell terminal:

```powershell
py -3 -X utf8 bot.py monitor --cycles 0
py -3 -X utf8 midpoint_call.py monitor
py -3 -X utf8 remaining_calls.py monitor
py -3 -X utf8 dashboard.py
```

Open [the local dashboard](http://127.0.0.1:8765). The main recorder collects
public evidence; the two call monitors read that database and save their own
decisions. The dashboard only displays data. Missing/stale evidence shows a
waiting or SKIP state. The first usable results require fresh forecasts and
official settlement; copying this source provides no historical results.

The optional launcher supports the same workflow:

```powershell
.\run.ps1 init
.\run.ps1 test
.\run.ps1 monitor
.\run.ps1 midpoint
.\run.ps1 remaining
.\run.ps1 dashboard
```

It finds `py` or `python` on PATH. Use `-Python 'D:\path\python.exe'` to select a
specific interpreter. Direct Python commands also work when PowerShell script
execution is restricted; changing system execution policy is unnecessary.

Keep the machine awake while recording. API failures and sleep can create gaps.
Press Ctrl+C in each terminal to stop its process. Creating a file named `STOP`
in this folder stops the collectors/call monitors; remove that file deliberately
before restarting. Stop the dashboard with Ctrl+C. Only one collector per
database and one monitor per call source can run at once.

## Forecasts and fixed calls

The volatility proxy uses public Coinbase BTC prices and recent completed
one-minute candles to estimate a Kalshi settlement probability. Coinbase is not
the official settlement index. Basis differences, jumps and volatility changes
are unmodeled. Forecasts are unsupported during the final averaging minute and
are hidden when stale or invalid. These probabilities remain **unvalidated**.

Original first calls stay separate from later snapshots and timing studies:

- `midpoint-lock-v1`: after 7 minutes 30 seconds from opening, save the first
  eligible fresh observation. The capture deadline is 8 minutes 30 seconds
  after opening. Its execution-book freshness requirement remains 3 seconds.
- `remaining-lock-v1`: independently lock at 7 minutes and 4 minutes remaining
  (8 and 11 minutes after opening). Use the latest valid estimate the monitor
  actually saw at or before each cutoff, no more than 45 seconds old at that
  cutoff. Receipts persist across restarts. An absent/stale receipt or a resume
  more than 15 seconds after the cutoff permanently records SKIP; later
  forecasts never backfill the decision.

Both timing sources call UP / YES at 60% YES or higher and DOWN / NO at 40% YES
or lower; intermediate probabilities permanently lock SKIP. Each call remains
fixed. Official finalized results and the market midpoint captured with that
call provide separate per-source accuracy, Brier and log-loss comparisons.
Correct forecasts do not establish trading profits.

The call monitors register only eligible future decision points when first
started. Their source and dependency hashes are frozen in their new local
registries. A later code change causes a hash mismatch; do not silently overwrite
registries or reuse old trial identity. Preserve the old experiment and use a
new reviewed version/directory when changing a policy.

The learning checkpoint is **200 distinct settled markets**, not 200 snapshots.
Reaching it means enough data to evaluate a candidate, not automatic training,
model promotion, proven predictive improvement or permission to trade. Candidate
training and historical comparisons can overfit; independent future evaluation
is still required.

## Historical paper replay

After collecting your own data, run:

```powershell
py -3 -X utf8 paper_decision.py
```

This writes a timestamped local report under `paper-reports/` using original
first calls. Its default $100 hypothetical bankroll is distinct from the core
configuration's $1,000 paper bookkeeping parameter. No real or simulated order
is submitted by the replay. The `monitor` command records observations; it does
not run the bot's separate legacy `paper` command. The default configuration
keeps `rules_and_fees_reviewed` false.

Fees and hypothetical fills are assumptions: a quadratic 0.07 taker fee,
multiplier-one scenario, aggregate-order rounding, recorded top-of-book ask and
visible depth. Event-specific overrides, account precision, real fills and
slippage are not verified merely by running the code. The replay uses the stated
fixed initial bankroll without replenishment or payout recycling. Consult the
report's policy and assumptions; these outputs are research, not promised returns.

## Local data and sharing

`config.example.json` is the distributed default. Your generated `config.json`,
SQLite database, reports, call ledgers, registrations, logs and model files stay
local. The `.gitignore` uses an explicit source-file allowlist to prevent newly
generated data from being accidentally committed. Review staged files before
sharing. No optional paid review is enabled by startup or initialization.

The source retains optional OpenAI review support. If you choose to enable it,
supply `OPENAI_API_KEY` and `OPENAI_MODEL` through your own process environment
and explicitly change the local configuration. Never commit credentials. API
usage is billed separately and is not provided by a Codex subscription.

This package excludes the original machine's automation/watchdog integration,
date-bound $400 day/overnight trials, allocation and quant-shadow experiments,
macro-calendar setup, historical timing backtests and optional scientific Python
environment. Their UI and saved reports are also omitted. The included workflow
is the core recorder, read-only dashboard, fixed-call studies and paper replay.
