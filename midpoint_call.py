"""Forward-only, immutable midpoint calls; no network, orders, or source DB writes.

This is a separately registered selection policy over the unchanged v1 model.
The first currently fresh valid observation after the scheduled midpoint is used,
including a permanent weak-signal SKIP. It does not search for a stronger call.
"""
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from dashboard_trading import build_trading_report
from paper_decision import official_result, losses

ROOT = Path(__file__).resolve().parent
SOURCE = "midpoint-lock-v1"
POLICY = dict(cycle_seconds=900, lock_after_seconds=450, deadline_seconds=510,
              quote_max_age_seconds=3, yes_threshold=.6, no_threshold=.4,
              selection="first currently fresh eligible recorded observation; weak call permanently SKIP",
              fee_status="UNVERIFIED multiplier-1 paper scenario", live_signal="SKIP",
              execution="forecast observation only; no paper bankroll or order placement")
DEPENDENCIES = ("midpoint_call.py", "dashboard_trading.py", "paper_decision.py", "live.py", "bot.py")


def hashes(root=ROOT):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in DEPENDENCIES}


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def register(directory, now, dependency_hashes=None):
    """Never overwrite registration or silently start a trial with changed code."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "registry.json"
    expected = hashes() if dependency_hashes is None else dependency_hashes
    if path.exists():
        registry = json.loads(path.read_text(encoding="utf-8"))
        if registry["source"] != SOURCE or registry["hashes"] != expected or registry["policy"] != POLICY:
            raise RuntimeError("Frozen midpoint registration/hash mismatch; review required")
        return registry
    opened = int(now // 900) * 900
    # Registration must precede the scheduled decision. Never backfill a call
    # for a market whose decision point already passed before registration.
    first = opened if now < opened + 450 else opened + 900
    registry = dict(source=SOURCE, registered_at=now, start_open_ts=first,
                    hashes=expected, policy=POLICY.copy(), validated=False)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(registry, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    return registry


def read_ledger(path):
    """Reject partial/tampered logs rather than silently dropping decisions."""
    events, previous = [], ""
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        digest = event.pop("sha256")
        raw = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if event.get("previous_sha256") != previous or hashlib.sha256(raw.encode()).hexdigest() != digest:
            raise RuntimeError("Midpoint ledger hash mismatch; review required")
        event["sha256"] = digest
        events.append(event)
        previous = digest
    return events


def append_event(path, events, kind, value):
    event = dict(kind=kind, value=value, previous_sha256=events[-1]["sha256"] if events else "")
    raw = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
    event["sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    events.append(event)


def decisions(events):
    result, settlements = {}, {}
    for event in events:
        value = event["value"]
        if event["kind"] == "decision":
            if value["open_ts"] in result:
                raise RuntimeError("Duplicate immutable midpoint decision")
            result[value["open_ts"]] = value.copy()
        elif event["kind"] == "settlement":
            if value["open_ts"] in settlements:
                raise RuntimeError("Duplicate midpoint settlement")
            settlements[value["open_ts"]] = value
        else:
            raise RuntimeError("Unknown midpoint event type")
    for opened, settled in settlements.items():
        if opened not in result:
            raise RuntimeError("Settlement without midpoint decision")
        result[opened].update(settled)
    return result


def missing_decision(opened, now):
    return dict(source=SOURCE, open_ts=opened, close_ts=opened+900,
                lock_after=opened+450, deadline=opened+510, locked_at=now,
                direction="SKIP", reason="missed_capture_window", ticker=None,
                p_yes=None, p_no=None, p_market=None, observed=None,
                forecast_id=None, snapshot_id=None, quotes=None, values=None,
                paper_scenario=None, live_signal="SKIP", validated=False)


def eligible(report, opened, now):
    """Guard timing again, independently of UI helper validity checks."""
    try:
        numbers = [now, report["observed"], report["completed"], report["quote_received"],
                   report["forecasts"]["p_yes"], report["quotes"]["yes_bid"], report["quotes"]["yes_ask"]]
        if any(not math.isfinite(float(value)) for value in numbers):
            return False
        p = report["forecasts"]["p_yes"]
        return (opened+450 <= now < opened+510 and
                report["open_ts"] == opened and report["close_ts"] == opened+900 and
                report["snapshot_valid"] and report["current_quote_valid"] and
                opened+450 <= report["observed"] <= report["completed"] <= now and
                report["quote_received"] == report["observed"] and
                0 <= now-report["quote_received"] <= 3 and 0 <= p <= 1 and
                0 <= report["quotes"]["yes_bid"] <= report["quotes"]["yes_ask"] <= 1)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def capture(report, opened, now):
    if not eligible(report, opened, now):
        return None
    p = report["forecasts"]["p_yes"]
    direction = "UP / YES" if p >= .6 else "DOWN / NO" if p <= .4 else "SKIP"
    result = missing_decision(opened, now)
    result.update(ticker=report["ticker"], direction=direction,
                  reason="fixed_midpoint_call" if direction != "SKIP" else "weak_at_fixed_midpoint",
                  p_yes=p, p_no=1-p, p_market=(report["quotes"]["yes_bid"]+report["quotes"]["yes_ask"])/2,
                  observed=report["observed"], completed=report["completed"],
                  quote_received=report["quote_received"], forecast_id=report["forecast_id"],
                  snapshot_id=report["snapshot_id"], quotes=report["quotes"], values=report["values"],
                  paper_scenario=report["scenarios"]["none"])
    return result


def evaluate(ledger, scheduled):
    called = [x for x in ledger if x["direction"] != "SKIP"]
    settled = [x for x in called if x.get("result") in ("yes", "no")]
    scores = [(losses(x["p_yes"], int(x["result"] == "yes")),
               losses(x["p_market"], int(x["result"] == "yes"))) for x in settled]
    def avg(source, metric):
        return sum(x[source][metric] for x in scores)/len(scores) if scores else None
    correct = sum((x["direction"] == "UP / YES") == (x["result"] == "yes") for x in settled)
    return dict(scheduled=scheduled, decisions=len(ledger), calls=len(called),
                skips=len(ledger)-len(called), settled_calls=len(settled), correct=correct,
                wrong=len(settled)-correct, coverage=len(called)/len(ledger) if ledger else None,
                model=dict(brier=avg(0, 0), log_loss=avg(0, 1)),
                market=dict(brier=avg(1, 0), log_loss=avg(1, 1)),
                comparison="same locked directional calls only; abstentions counted in coverage",
                promotion_candidate=False, live_signal="SKIP")


def read_markets(db_path):
    with contextlib.closing(sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return {row["ticker"]: dict(row) for row in connection.execute("SELECT ticker,metadata,result FROM markets")}


def tick(directory, registry, db_path, now=None, report_builder=build_trading_report):
    explicit_now = now is not None
    now = time.time() if now is None else now
    path = Path(directory)/"ledger.jsonl"
    events = read_ledger(path)
    saved = decisions(events)
    current = int(now//900)*900
    scheduled = list(range(registry["start_open_ts"], current+1, 900))
    for opened in scheduled:
        if opened in saved:
            continue
        value = None
        if now >= opened+510:
            value = missing_decision(opened, now)
        elif opened+450 <= now:
            report = report_builder(db_path, now=now) if explicit_now else report_builder(db_path)
            # A slow DB read must never turn into a late/backfilled decision.
            now = now if explicit_now else time.time()
            value = missing_decision(opened, now) if now >= opened+510 else capture(report, opened, now)
        if value is not None:
            append_event(path, events, "decision", value)
            saved[opened] = value
    markets = read_markets(db_path)
    for opened, entry in saved.items():
        if entry.get("result") is not None or not entry["ticker"]:
            continue
        result = official_result(markets.get(entry["ticker"], {}), now)
        if result is not None:
            append_event(path, events, "settlement", dict(open_ts=opened, result=result, settlement_seen=now,
                         correct=None if entry["direction"] == "SKIP" else (entry["direction"] == "UP / YES") == (result == "yes")))
    ledger = sorted(decisions(events).values(), key=lambda row: row["open_ts"])
    current = int(now//900)*900
    active_decision = next((entry for entry in ledger if entry["open_ts"] == current), None)
    state = ("locked" if active_decision else "not_registered" if current < registry["start_open_ts"]
             else "waiting" if now < current+450 else "collecting")
    report = dict(source=SOURCE, generated_at=now, status="running", registry=registry,
                  active=dict(open_ts=current, close_ts=current+900, lock_after=current+450,
                              deadline=current+510, state=state, decision=active_decision),
                  evaluation=evaluate(ledger, len(scheduled)), ledger=ledger,
                  health=dict(pid=os.getpid(), poll_seconds=1, source_db="read_only", last_success=now),
                  live_signal="SKIP", validated=False)
    atomic_json(Path(directory)/"report.json", report)
    return report


@contextlib.contextmanager
def singleton(path):
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("monitor", "once"))
    parser.add_argument("--db", type=Path, default=ROOT/"research.sqlite")
    parser.add_argument("--directory", type=Path, default=ROOT/SOURCE)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with singleton(args.directory/"monitor.lock"):
        if (ROOT/"STOP").exists() or (args.directory/"STOP").exists():
            raise SystemExit("Collection intentionally stopped; no registration or restart")
        registry = register(args.directory, time.time())
        recorder = dict(pid=os.getpid(), source=SOURCE, started_at=time.time(), status="running")
        atomic_json(args.directory/"recorder.json", recorder)
        try:
            while True:
                if (ROOT/"STOP").exists() or (args.directory/"STOP").exists():
                    recorder["status"] = "intentionally_stopped"
                    break
                # Changed dependencies require a new reviewed version; do not
                # continue a frozen experiment using silently altered helpers.
                register(args.directory, time.time())
                tick(args.directory, registry, args.db)
                recorder["last_success"] = time.time()
                atomic_json(args.directory/"recorder.json", recorder)
                if args.command == "once":
                    recorder["status"] = "once_complete"
                    break
                time.sleep(1)
        except BaseException:
            recorder["status"] = "failed"
            raise
        finally:
            recorder["stopped_at"] = time.time()
            atomic_json(args.directory/"recorder.json", recorder)
            report_path = args.directory/"report.json"
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["status"] = recorder["status"]
                report["health"]["stopped_at"] = recorder["stopped_at"]
                atomic_json(report_path, report)


if __name__ == "__main__":
    main()
