"""Forward-only calls at seven and four minutes remaining; observation only.

Each cutoff locks the latest valid forecast this process actually saw at or
before that cutoff. Candidate receipts are persisted before any decision. A
restart never substitutes a forecast first seen after a cutoff. The source DB,
original first calls, midpoint calls, bankrolls and orders are never modified.
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
from paper_decision import losses, official_result

ROOT = Path(__file__).resolve().parent
SOURCE = "remaining-lock-v1"
CUTOFFS = {"7": 480, "4": 660}
POLICY = dict(cycle_seconds=900, cutoff_after_seconds=CUTOFFS,
              capture_grace_seconds=15, forecast_max_age_seconds=45,
              yes_threshold=.6, no_threshold=.4,
              selection="latest valid estimate seen at or before each cutoff; weak call permanently SKIP",
              execution="directional forecast observation only; no bankroll or order placement",
              live_signal="SKIP")
DEPENDENCIES = ("remaining_calls.py", "dashboard_trading.py", "paper_decision.py", "live.py", "bot.py")


def hashes(root=ROOT):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in DEPENDENCIES}


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def finite_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def register(directory, now, dependency_hashes=None):
    """Freeze code/policy and independently enroll future 7/4-minute cutoffs."""
    if not finite_number(now):
        raise ValueError("Registration time must be finite")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "registry.json"
    expected = hashes() if dependency_hashes is None else dependency_hashes
    if path.exists():
        registry = json.loads(path.read_text(encoding="utf-8"))
        if (registry["source"] != SOURCE or registry["hashes"] != expected
                or registry["policy"] != POLICY):
            raise RuntimeError("Frozen remaining-call registration/hash mismatch; review required")
        return registry
    opened = int(now // 900) * 900
    first = {minute: opened if now < opened + offset else opened + 900
             for minute, offset in CUTOFFS.items()}
    registry = dict(source=SOURCE, registered_at=now,
                    start_open_ts_by_minutes=first, hashes=expected,
                    policy=POLICY.copy(), validated=False)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(registry, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    return registry


def read_ledger(path):
    """Reject partial or changed audit events instead of dropping evidence."""
    events, previous = [], ""
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        digest = event.pop("sha256")
        raw = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if event.get("previous_sha256") != previous or hashlib.sha256(raw.encode()).hexdigest() != digest:
            raise RuntimeError("Remaining-call ledger hash mismatch; review required")
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


def candidate_key(value):
    return (value["open_ts"], value["ticker"], value["forecast_id"], value["snapshot_id"])


def decisions(events):
    result, settlements, seen = {}, {}, set()
    for event in events:
        value = event["value"]
        if event["kind"] == "candidate":
            key = candidate_key(value)
            if key in seen:
                raise RuntimeError("Duplicate remaining-call candidate receipt")
            seen.add(key)
            continue
        key = (value["open_ts"], value["remaining_minutes"])
        if value["remaining_minutes"] not in CUTOFFS:
            raise RuntimeError("Unknown remaining-call cutoff")
        if event["kind"] == "decision":
            if key in result:
                raise RuntimeError("Duplicate immutable remaining-call decision")
            result[key] = value.copy()
        elif event["kind"] == "settlement":
            if key in settlements:
                raise RuntimeError("Duplicate remaining-call settlement")
            settlements[key] = value
        else:
            raise RuntimeError("Unknown remaining-call event type")
    for key, settled in settlements.items():
        if key not in result:
            raise RuntimeError("Settlement without remaining-call decision")
        result[key].update(settled)
    return result


def candidate(report, opened, seen_at):
    """Validate directional evidence; execution-book freshness is not required."""
    try:
        p = report["forecasts"]["p_yes"]
        numbers = [seen_at, report["observed"], report["completed"], report["quote_received"],
                   p, report["quotes"]["yes_bid"], report["quotes"]["yes_ask"]]
        if any(not finite_number(value) for value in numbers):
            return None
        if not (report["snapshot_valid"] is True and report["open_ts"] == opened
                and report["close_ts"] == opened + 900
                and opened <= report["observed"] <= report["completed"] <= seen_at < opened + 900
                and report["quote_received"] == report["observed"]
                and seen_at - report["observed"] <= 45 and 0 <= p <= 1
                and 0 <= report["quotes"]["yes_bid"] <= report["quotes"]["yes_ask"] <= 1
                and isinstance(report["ticker"], str) and report["ticker"].startswith("KXBTC15M-")
                and type(report["forecast_id"]) is int and report["forecast_id"] > 0
                and type(report["snapshot_id"]) is int and report["snapshot_id"] > 0):
            return None
        # A JSON round trip detaches nested quotes from a caller's mutable report
        # and refuses non-finite values anywhere in the retained evidence.
        value = dict(source=SOURCE, open_ts=opened, close_ts=opened + 900,
                     seen_at=seen_at, ticker=report["ticker"], p_yes=p, p_no=1-p,
                     p_market=(report["quotes"]["yes_bid"] + report["quotes"]["yes_ask"])/2,
                     observed=report["observed"], completed=report["completed"],
                     quote_received=report["quote_received"], forecast_id=report["forecast_id"],
                     snapshot_id=report["snapshot_id"], quotes=report["quotes"])
        return json.loads(json.dumps(value, allow_nan=False))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def missing_decision(opened, minute, now):
    cutoff = opened + CUTOFFS[minute]
    return dict(source=SOURCE, remaining_minutes=minute, open_ts=opened, close_ts=opened + 900,
                cutoff=cutoff, deadline=cutoff + 15, locked_at=now,
                direction="SKIP", reason="missed_capture_window", ticker=None,
                p_yes=None, p_no=None, p_market=None, observed=None, completed=None,
                seen_at=None, quote_received=None, forecast_id=None, snapshot_id=None,
                quotes=None, candidate_sha256=None, live_signal="SKIP", validated=False)


def lock(events, opened, minute, now):
    """Use only persisted pre-cutoff receipts, even when the DB has a better call."""
    cutoff = opened + CUTOFFS[minute]
    if now < cutoff:
        return None
    value = missing_decision(opened, minute, now)
    if now > cutoff + 15:
        return value
    eligible = [event for event in events if event["kind"] == "candidate"
                and event["value"]["open_ts"] == opened
                and event["value"]["seen_at"] <= cutoff
                and 0 <= cutoff - event["value"]["observed"] <= 45]
    if not eligible:
        return value
    # Receipt order breaks equal-time ties. Deduplication preserves the original
    # first-seen timestamp when a forecast is polled repeatedly.
    selected = max(enumerate(eligible), key=lambda item: (item[1]["value"]["seen_at"], item[0]))[1]
    cached = selected["value"]
    p = cached["p_yes"]
    direction = "UP / YES" if p >= .6 else "DOWN / NO" if p <= .4 else "SKIP"
    value.update(cached)
    value.update(direction=direction, candidate_sha256=selected["sha256"],
                 reason="fixed_remaining_call" if direction != "SKIP" else "weak_at_fixed_cutoff")
    return value


def evaluate(ledger, scheduled):
    called = [entry for entry in ledger if entry["direction"] != "SKIP"]
    settled = [entry for entry in called if entry.get("result") in ("yes", "no")]
    scores = [(losses(entry["p_yes"], int(entry["result"] == "yes")),
               losses(entry["p_market"], int(entry["result"] == "yes"))) for entry in settled]
    def avg(source, metric):
        return sum(score[source][metric] for score in scores)/len(scores) if scores else None
    correct = sum((entry["direction"] == "UP / YES") == (entry["result"] == "yes") for entry in settled)
    return dict(scheduled=scheduled, decisions=len(ledger), calls=len(called),
                skips=len(ledger)-len(called),
                missing_captures=sum(entry["reason"] == "missed_capture_window" for entry in ledger),
                weak_skips=sum(entry["reason"] == "weak_at_fixed_cutoff" for entry in ledger),
                settled_calls=len(settled), pending_calls=len(called)-len(settled),
                correct=correct, wrong=len(settled)-correct,
                win_rate=correct/len(settled) if settled else None,
                coverage=len(called)/len(ledger) if ledger else None,
                model=dict(brier=avg(0, 0), log_loss=avg(0, 1)),
                market=dict(brier=avg(1, 0), log_loss=avg(1, 1)),
                comparison="same locked directional calls and their paired market midpoints; abstentions counted in coverage",
                promotion_candidate=False, live_signal="SKIP")


def read_markets(db_path):
    with contextlib.closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return {row["ticker"]: dict(row) for row in connection.execute("SELECT ticker,metadata,result FROM markets")}


def tick(directory, registry, db_path, now=None, report_builder=build_trading_report):
    explicit_now = now is not None
    now = time.time() if now is None else now
    if not finite_number(now):
        raise ValueError("Observation time must be finite")
    path = Path(directory) / "ledger.jsonl"
    events = read_ledger(path)
    saved = decisions(events)
    current = int(now // 900) * 900
    first = registry["start_open_ts_by_minutes"]
    # Poll until the final still-eligible cutoff. A report read that straddles
    # the cutoff is first seen only after it returns, never at query start.
    if any(current >= first[minute] and now <= current + offset
           and (current, minute) not in saved for minute, offset in CUTOFFS.items()):
        snapshot = report_builder(db_path, now=now) if explicit_now else report_builder(db_path)
        seen_at = now if explicit_now else time.time()
        value = candidate(snapshot, current, seen_at)
        if value is not None and any(current >= first[minute] and seen_at <= current + offset
                                     and (current, minute) not in saved for minute, offset in CUTOFFS.items()):
            old = {candidate_key(event["value"]) for event in events if event["kind"] == "candidate"}
            if candidate_key(value) not in old:
                append_event(path, events, "candidate", value)
        now = seen_at
    current = int(now // 900) * 900
    scheduled = {minute: list(range(first[minute], current + 1, 900)) for minute in CUTOFFS}
    for minute in CUTOFFS:
        for opened in scheduled[minute]:
            if (opened, minute) in saved:
                continue
            value = lock(events, opened, minute, now)
            if value is not None:
                append_event(path, events, "decision", value)
                saved[(opened, minute)] = value
    # Official finalized labels are read only after selection and never affect
    # a candidate or decision. Each cutoff settles independently.
    markets = read_markets(db_path)
    for (opened, minute), entry in saved.items():
        if entry.get("result") is not None or not entry["ticker"]:
            continue
        result = official_result(markets.get(entry["ticker"], {}), now)
        if result is not None:
            append_event(path, events, "settlement", dict(open_ts=opened, remaining_minutes=minute,
                         result=result, settlement_seen=now,
                         correct=None if entry["direction"] == "SKIP" else
                         (entry["direction"] == "UP / YES") == (result == "yes")))
    merged = decisions(events)
    ledger = sorted(merged.values(), key=lambda entry: (entry["open_ts"], entry["cutoff"]))
    active = {}
    for minute, offset in CUTOFFS.items():
        entry = merged.get((current, minute))
        state = "locked" if entry else "not_registered" if current < first[minute] else "waiting"
        active[minute] = dict(state=state, cutoff=current + offset, decision=entry)
    report = dict(source=SOURCE, generated_at=now, status="running", registry=registry,
                  active=dict(open_ts=current, close_ts=current + 900, decisions=active),
                  evaluation={minute: evaluate([entry for entry in ledger if entry["remaining_minutes"] == minute],
                              sum(opened + CUTOFFS[minute] <= now for opened in scheduled[minute])) for minute in CUTOFFS},
                  ledger=ledger, health=dict(pid=os.getpid(), poll_seconds=1, source_db="read_only", last_success=now),
                  live_signal="SKIP", validated=False)
    atomic_json(Path(directory) / "report.json", report)
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
    parser.add_argument("--db", type=Path, default=ROOT / "research.sqlite")
    parser.add_argument("--directory", type=Path, default=ROOT / SOURCE)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with singleton(args.directory / "monitor.lock"):
        if (ROOT / "STOP").exists() or (args.directory / "STOP").exists():
            raise SystemExit("Collection intentionally stopped; no registration or restart")
        registry = register(args.directory, time.time())
        recorder = dict(pid=os.getpid(), source=SOURCE, started_at=time.time(), status="running")
        atomic_json(args.directory / "recorder.json", recorder)
        try:
            while True:
                if (ROOT / "STOP").exists() or (args.directory / "STOP").exists():
                    recorder["status"] = "intentionally_stopped"
                    break
                register(args.directory, time.time())
                tick(args.directory, registry, args.db)
                recorder["last_success"] = time.time()
                atomic_json(args.directory / "recorder.json", recorder)
                if args.command == "once":
                    recorder["status"] = "once_complete"
                    break
                time.sleep(1)
        except BaseException:
            recorder["status"] = "failed"
            raise
        finally:
            recorder["stopped_at"] = time.time()
            atomic_json(args.directory / "recorder.json", recorder)
            report_path = args.directory / "report.json"
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["status"] = recorder["status"]
                report["health"]["stopped_at"] = recorder["stopped_at"]
                atomic_json(report_path, report)


if __name__ == "__main__":
    main()
