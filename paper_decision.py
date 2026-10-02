"""Versioned, read-only paper decision/replay layer. Never submits orders.

No forecast is generated or changed here. Historical replays are exploratory,
not forward validation. See README.md for execution/accounting assumptions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sqlite3
import time
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

VERSION = "paper-decision-v1"
ROOT = Path(__file__).resolve().parent
D = Decimal
ZERO, ONE = D(0), D(1)


def decimal(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Missing or boolean number")
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid number") from exc
    if not result.is_finite():
        raise ValueError("Non-finite number")
    return result


def price(value):
    p = decimal(value)
    if not ZERO < p < ONE:
        raise ValueError("Ask must be strictly between zero and one")
    # Recorded complements can contain IEEE-754 artifacts (e.g. .41000000000000003).
    # Normalize only a <=1e-12 artifact at the API's four-decimal price grid,
    # after rejecting invalid values. This is not probability/input clamping.
    grid = p.quantize(D("0.0001"))
    return grid if ZERO < grid < ONE and abs(grid - p) <= D("1e-12") else p


def ceil_grid(value, grid):
    return (value / grid).to_integral_value(rounding=ROUND_CEILING) * grid


def kalshi_fee(price_value, quantity=1, multiplier=1, balance_precision="0.0001"):
    """Dollar fee for one aggregate taker BUY fill; no settlement fee.

    Official schedule, effective 2026-07-07, checked 2026-10-01:
    https://kalshi.com/docs/kalshi-fee-schedule.pdf
    General quadratic model = .07 * multiplier * quantity * p * (1-p).
    Exact rounding: https://docs.kalshi.com/getting_started/fee_rounding
    Model fee rounds UP to $0.000001, then total purchase debit rounds UP to
    member precision ($0.0001 direct, $0.01 non-direct). A fresh single fill has
    no previous rounding accumulator/rebate. Multi-fill orders are unsupported.
    The multiplier is a scenario input: historical event overrides are not
    present in research.sqlite and must not be claimed verified by this helper.
    """
    p, q, mult, grid = price(price_value), decimal(quantity), decimal(multiplier), decimal(balance_precision)
    if q <= 0 or q != q.to_integral_value() or mult < 0:
        raise ValueError("Positive integer quantity and nonnegative fee multiplier required")
    if grid not in (D("0.0001"), D("0.01")):
        raise ValueError("Unsupported member balance precision")
    model_fee = ceil_grid(D("0.07") * mult * q * p * (ONE - p), D("0.000001"))
    return ceil_grid(q * p + model_fee, grid) - q * p


def trade_signal(p_model, yes_ask, no_ask, fee, margin=0.03):
    """Return BUY_YES / BUY_NO / SKIP using strict net EV > margin.

    fee is dollars per contract, either one scalar for both sides or a
    {'yes': ..., 'no': ...} mapping for asymmetric price-dependent fees.
    Invalid evidence fails closed. A signal is paper research, never approval.
    """
    try:
        p, ya, na, hurdle = decimal(p_model), price(yes_ask), price(no_ask), decimal(margin)
        fy = decimal(fee["yes"] if isinstance(fee, dict) else fee)
        fn = decimal(fee["no"] if isinstance(fee, dict) else fee)
        if not ZERO <= p <= ONE or min(fy, fn, hurdle) < 0 or ya + na < ONE:
            return "SKIP"
        ey, en = p - ya - fy, ONE - p - na - fn
        if max(ey, en) <= hurdle:
            return "SKIP"
        return "BUY_YES" if ey >= en else "BUY_NO"
    except (ValueError, KeyError, TypeError):
        return "SKIP"


@dataclass(frozen=True)
class Policy:
    paper_bankroll: float = 100.0
    margin: float = 0.03
    fee_multiplier: float = 1.0
    balance_precision: str = "0.0001"
    max_book_age_seconds: float = 3.0
    max_forecast_age_seconds: float = 45.0

    def __post_init__(self):
        if decimal(self.paper_bankroll) <= 0 or decimal(self.margin) < 0:
            raise ValueError("Positive bankroll and nonnegative margin required")
        if min(decimal(self.max_book_age_seconds), decimal(self.max_forecast_age_seconds)) < 0:
            raise ValueError("Negative freshness limit")
        kalshi_fee("0.5", multiplier=self.fee_multiplier, balance_precision=self.balance_precision)


def size_order(p_win, ask, depth, bankroll, cash, policy=Policy()):
    """Quarter Kelly; total debit <=1% of supplied bankroll AND free cash.

    Evaluate integer quantities with the actual aggregate order fee. The
    largest feasible quantity is chosen; no deeper-book sweep or partial fill.
    The replay passes a fixed initial bankroll, and never recycles payouts.
    """
    try:
        p, a, available, bank, free = map(decimal, (p_win, ask, depth, bankroll, cash))
        a = price(a)
        if not ZERO <= p <= ONE or min(available, bank, free) <= 0:
            return None
        budget = min(bank * D("0.01"), free)
        max_quantity = int(min(available, budget / a).to_integral_value(rounding=ROUND_FLOOR))
        for n in range(max_quantity, 0, -1):
            fee = kalshi_fee(a, n, policy.fee_multiplier, policy.balance_precision)
            cost = n * a + fee
            unit = cost / n
            ev = p - unit
            if unit >= ONE or ev <= decimal(policy.margin):
                continue
            kelly = D("0.25") * ev / (ONE - unit)
            if cost <= min(budget, bank * kelly):
                return dict(quantity=n, cost=cost, fee=fee, ev_per_contract=ev, kelly_fraction=kelly)
    except (ValueError, TypeError):
        return None
    return None


def digest(value):
    return hashlib.sha256(json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def json_safe(value):
    """Preserve invalid numeric evidence as rejectable text, never clamp/drop it."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def epoch(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed.timestamp()


def read_inputs(db_path):
    """One read-only transaction; rank FIRST before any eligibility filters.

    No immutable=1: the recorder may continue writing its database. Original
    forecast fields and snapshots are copied; labels get a separate digest.
    """
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = [dict(r) for r in db.execute("""SELECT id,ticker,source,observed,completed,close_ts,p_yes,detail
            FROM predictions ORDER BY observed,id""")]
        first = {}
        for row in rows:
            first.setdefault((row["source"], row["ticker"]), row)
        calls = list(first.values())
        selected_books = {}
        for row in calls:
            for field, exact in (("observed", True), ("completed", False)):
                operator = "=" if exact else "<="
                book = db.execute(f"""SELECT id,ticker,ts,received,yes_ask,no_ask,yes_size,no_size
                    FROM snapshots WHERE ticker=? AND received {operator} ?
                    ORDER BY received DESC,id DESC LIMIT 1""", (row["ticker"], row[field])).fetchone()
                key = "feature_snapshot" if exact else "decision_snapshot"
                row[key] = None if book is None else book["id"]
                if book is not None:
                    selected_books[book["id"]] = dict(book)
        markets = [dict(r) for r in db.execute("SELECT ticker,metadata,result FROM markets ORDER BY ticker")]
        revisions = [dict(r) for r in db.execute("SELECT * FROM outcome_revisions ORDER BY observed,ticker")]
        as_of = time.time()
        immutable = [{k: v for k, v in r.items() if k not in ("feature_snapshot", "decision_snapshot")} for r in rows]
        manifest = dict(predictions_count=len(rows), max_prediction_id=max((r["id"] for r in rows), default=0),
                        forecasts_sha256=digest(immutable), paired_snapshots_sha256=digest(list(selected_books.values())),
                        labels_sha256=digest(dict(markets=markets, revisions=revisions)))
        db.rollback()
    return json_safe(dict(schema_version=1, as_of=as_of, manifest=manifest, first_calls=calls,
                         snapshots=list(selected_books.values()), markets=markets, outcome_revisions=revisions))


def decode(value):
    obj = json.loads(value) if isinstance(value, str) else value
    if not isinstance(obj, dict):
        raise ValueError("Object required")
    return obj


def validate_call(row, metadata, books, policy, as_of):
    """Validate feature provenance independently of settlement outcomes."""
    try:
        if not row["ticker"].startswith("KXBTC15M-"):
            return "unsupported_market_series"
        observed, decision, close, p = [float(decimal(row[k])) for k in ("observed", "completed", "close_ts", "p_yes")]
        opened, official_close = epoch(metadata["open_time"]), epoch(metadata["close_time"])
        if not 0 <= p <= 1:
            return "invalid_probability"
        if not opened <= observed <= decision <= as_of or close != official_close or close - opened != 900:
            return "invalid_forecast_timestamps"
        if not 60 < close - decision <= 900:
            return "outside_supported_window"
        if decision - observed > policy.max_forecast_age_seconds:
            return "stale_forecast_at_decision"
        feature, execution = books.get(row.get("feature_snapshot")), books.get(row.get("decision_snapshot"))
        if feature is None or execution is None:
            return "missing_paired_snapshot"
        if feature["ticker"] != row["ticker"] or execution["ticker"] != row["ticker"]:
            return "snapshot_market_mismatch"
        if float(decimal(feature["received"])) != observed:
            return "feature_snapshot_time_mismatch"
        for book in (feature, execution):
            request, received = float(decimal(book["ts"])), float(decimal(book["received"]))
            if not opened <= request <= received <= decision or received - request > policy.max_book_age_seconds:
                return "invalid_or_slow_snapshot"
        if not 0 <= decision - float(decimal(execution["received"])) <= policy.max_book_age_seconds:
            return "stale_decision_quote"
        ya, na = price(execution["yes_ask"]), price(execution["no_ask"])
        if ya + na < ONE or min(decimal(execution["yes_size"]), decimal(execution["no_size"])) < 0:
            return "invalid_book"
        detail = decode(row["detail"])
        vector = detail.get("features", detail.get("book_features"))
        if not isinstance(vector, list) or not vector:
            return "missing_feature_vector"
        for value in vector:
            decimal(value)
        if row["source"] == "volatility-proxy-v1":
            spot = detail["spot"]
            tick, receipt, candle = [float(decimal(spot[k])) for k in ("tick_ts", "received", "last_closed_candle")]
            if not tick <= receipt <= observed or candle + 60 > observed or observed - tick > 30:
                return "future_or_stale_spot_features"
            if min(decimal(spot["price"]), decimal(spot["sigma_1m"]), decimal(detail["target"])) <= 0:
                return "invalid_spot_features"
            decimal(spot["momentum_5m"])
        elif row["source"] != "market-mid-v1":
            return "unsupported_source_provenance"
        return None
    except (KeyError, ValueError, TypeError, OverflowError):
        return "malformed_first_call"


def official_result(market, as_of):
    """Labels are evaluation-only. They NEVER influence trade selection/sizing."""
    try:
        meta = decode(market["metadata"])
        result = market["result"]
        if result not in ("yes", "no") or meta.get("result") != result or meta.get("status") != "finalized":
            return None
        close, settled = epoch(meta["close_time"]), epoch(meta["settlement_ts"])
        return result if close <= settled <= as_of else None
    except (KeyError, TypeError, ValueError):
        return None


def losses(p, y):
    # Endpoints use a stated 1e-12 log-loss scoring epsilon; no forecast is altered.
    clipped = min(1 - 1e-12, max(1e-12, p))
    return (p - y) ** 2, -(y * math.log(clipped) + (1 - y) * math.log1p(-clipped))


def fee_evidence_status(inputs, evidence, policy):
    """Describe exact public-API coverage, without inferring account membership.

    Nonempty change histories need a new reviewed event/time-aware fee resolver;
    they cannot silently be interpreted as the constant multiplier scenario.
    """
    status = dict(status="unverified_scenario", matched_events=0, required_events=0,
                  membership="assumed from balance_precision; no account accessed")
    if evidence is None:
        return status
    try:
        events = {decode(r["metadata"])["event_ticker"] for r in inputs["markets"]
                  if any(c["ticker"] == r["ticker"] for c in inputs["first_calls"])}
        status["required_events"] = len(events)
        results = evidence["results"]
        base = "https://external-api.kalshi.com/trade-api/v2"
        by_url = {r["url"]: r for r in results}
        series_row = by_url[base + "/series/KXBTC15M"]
        history_row = by_url[base + "/series/fee_changes?series_ticker=KXBTC15M&show_historical=true"]
        series = series_row["response"]["series"]
        if series_row["http_status"] != 200 or history_row["http_status"] != 200:
            return status
        if series["fee_type"] != "quadratic" or decimal(series["fee_multiplier"]) != decimal(policy.fee_multiplier):
            status["status"] = "different_fee_scenario"
            return status
        if history_row["response"]["series_fee_change_arr"]:
            status["status"] = "fee_changes_require_review"
            return status
        for event in events:
            row = by_url.get(base + "/events/fee_changes?event_ticker=" + event + "&limit=1000")
            if row is None or row.get("http_status") != 200:
                continue
            response = row["response"]
            if response["event_fee_changes"] or response["cursor"]:
                status["status"] = "fee_changes_require_review"
                return status
            status["matched_events"] += 1
        if status["matched_events"] == len(events) and events:
            status.update(status="api_checked_baseline_no_returned_changes", checked_at=evidence["checked_at"])
        return status
    except (KeyError, ValueError, TypeError):
        return status


def replay(inputs, policy=Policy(), fee_evidence=None):
    """Replay original first decisions; independent non-replenished bankroll per source.

    Fixed initial bankroll is the Kelly/cap base. Only unspent original cash can
    fund entries; ALL payouts remain unavailable throughout replay. Historical
    result-receipt/credit timestamps are absent, so none are invented to recycle
    capital. Realized P&L/drawdown are evaluated later using official labels.
    """
    books = {r["id"]: r for r in inputs["snapshots"]}
    markets = {r["ticker"]: r for r in inputs["markets"]}
    as_of = float(decimal(inputs["as_of"]))
    # Exported input can be replayed directly; still enforce global-first IDs.
    first = {}
    def first_key(r):
        try:
            return float(decimal(r["observed"])), r["id"]
        except (KeyError, ValueError):
            return -math.inf, r["id"]
    for row in sorted(inputs["first_calls"], key=first_key):
        first.setdefault((row["source"], row["ticker"]), row)
    by_source = {}
    for row in first.values():
        by_source.setdefault(row["source"], []).append(row)
    report = dict(policy_version=VERSION, implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  cohort="exploratory historical; not forward validation", as_of=as_of,
                  policy=asdict(policy), input_manifest=inputs.get("manifest", {}), inputs_sha256=digest(inputs),
                  live_orders_enabled=False, promotion_status="not evaluated; never auto-promote",
                  fee_evidence=fee_evidence_status(inputs, fee_evidence, policy),
                  assumptions=dict(fee_type="quadratic", fee_rate=0.07, fee_multiplier=policy.fee_multiplier,
                      member_precision=policy.balance_precision, historical_event_fee_overrides="see fee_evidence; uncovered history is an assumption",
                      fills="one aggregate taker fill at recorded ask, limited to visible top depth; no fill guarantee",
                      slippage=0, bankroll="fixed initial total per source; no deposits, compounding or payout recycling",
                      drawdown="settled-trade realized P&L curve ordered by close; pending excluded; not mark-to-market",
                      probabilities="original first calls; log-loss epsilon 1e-12"), sources={})
    for source, calls in sorted(by_source.items()):
        def decision_key(r):
            try:
                return float(decimal(r["completed"])), r["id"]
            except (KeyError, ValueError):
                return -math.inf, r["id"]
        cash = decimal(policy.paper_bankroll)
        bank = cash
        ledger, scored = [], []
        for row in sorted(calls, key=decision_key):
            entry = {k: row.get(k) for k in ("id", "ticker", "observed", "completed", "close_ts", "p_yes", "feature_snapshot", "decision_snapshot")}
            entry.update(signal="SKIP", reason="missing_market", quantity=0, cost=0.0, fee=0.0, cash_before=float(cash))
            market = markets.get(row["ticker"])
            try:
                meta = decode(market["metadata"]) if market else {}
                reason = validate_call(row, meta, books, policy, as_of) if market else "missing_market"
            except (ValueError, TypeError):
                reason = "malformed_market"
            if reason is not None:
                entry["reason"] = reason
                ledger.append(entry)
                continue
            book = books[row["decision_snapshot"]]
            p, ya, na = decimal(row["p_yes"]), price(book["yes_ask"]), price(book["no_ask"])
            mid = (ONE + ya - na) / 2
            entry.update(yes_ask=float(ya), no_ask=float(na), book_received=book["received"], paired_market_mid=float(mid))
            # Outcomes are deliberately NOT read until after all entry decisions.
            scored.append((row, mid))
            if report["fee_evidence"]["status"] in ("fee_changes_require_review", "different_fee_scenario"):
                entry["reason"] = report["fee_evidence"]["status"]
                ledger.append(entry)
                continue
            if meta.get("fee_waiver_expiration_time") is not None:
                entry["reason"] = "fee_waiver_requires_review"
                ledger.append(entry)
                continue
            yes = size_order(p, ya, book["yes_size"], bank, cash, policy)
            no = size_order(ONE - p, na, book["no_size"], bank, cash, policy)
            options = [("BUY_YES", yes), ("BUY_NO", no)]
            feasible = [(side, order) for side, order in options if order is not None]
            if not feasible:
                fees = dict(yes=kalshi_fee(ya, 1, policy.fee_multiplier, policy.balance_precision),
                            no=kalshi_fee(na, 1, policy.fee_multiplier, policy.balance_precision))
                entry["reason"] = "insufficient_net_edge" if trade_signal(p, ya, na, fees, policy.margin) == "SKIP" else "kelly_cash_cap_or_depth"
            else:
                side, order = max(feasible, key=lambda item: item[1]["ev_per_contract"])
                # Per-side fees must correspond to the proposed quantity, not an
                # independently rounded one-contract fee multiplied by quantity.
                fees = dict(yes=(yes["fee"] / yes["quantity"] if yes else kalshi_fee(ya, 1, policy.fee_multiplier, policy.balance_precision)),
                            no=(no["fee"] / no["quantity"] if no else kalshi_fee(na, 1, policy.fee_multiplier, policy.balance_precision)))
                signal = trade_signal(p, ya, na, fees, policy.margin)
                if signal != side:
                    raise AssertionError("Signal/sizing disagree")
                cash -= order["cost"]
                entry.update(signal=side, reason="positive_net_ev_paper_only", **{k: float(v) if isinstance(v, D) else v for k, v in order.items()})
            entry["cash_after"] = float(cash)
            ledger.append(entry)
        trades = [r for r in ledger if r["signal"] != "SKIP"]
        settled, pending = [], []
        for entry in trades:
            outcome = official_result(markets[entry["ticker"]], as_of)
            entry["official_result"] = outcome
            if outcome is None:
                pending.append(entry)
            else:
                won = (entry["signal"] == "BUY_YES") == (outcome == "yes")
                entry.update(won=won, payout=entry["quantity"] if won else 0)
                entry["pnl"] = float(decimal(entry["payout"]) - decimal(entry["cost"]))
                settled.append(entry)
        peak = curve = drawdown = ZERO
        for entry in sorted(settled, key=lambda r: (r["close_ts"], r["id"])):
            curve += decimal(entry["pnl"])
            peak = max(peak, curve)
            drawdown = max(drawdown, peak - curve)
        score_rows = []
        for row, midpoint in scored:
            result = official_result(markets[row["ticker"]], as_of)
            if result is None:
                continue
            y = int(result == "yes")
            brier, log_loss = losses(float(row["p_yes"]), y)
            mb, ml = losses(float(midpoint), y)
            score_rows.append(dict(ticker=row["ticker"], prediction_id=row["id"], brier=brier, log_loss=log_loss,
                                   midpoint_brier=mb, midpoint_log_loss=ml))
        def mean(key):
            return sum(r[key] for r in score_rows) / len(score_rows) if score_rows else None
        missing = [ticker for ticker, market in markets.items() if (source, ticker) not in first]
        pnl = sum((decimal(r["pnl"]) for r in settled), ZERO)
        spent = sum((decimal(r["cost"]) for r in trades), ZERO)
        payouts = sum((decimal(r["payout"]) for r in settled), ZERO)
        pending_cost = sum((decimal(r["cost"]) for r in pending), ZERO)
        assert cash == bank - spent and cash >= ZERO
        assert cash + payouts + pending_cost == bank + pnl
        report["sources"][source] = dict(first_calls=len(calls), trades=len(trades), settled_trades=len(settled),
            pending_trades=len(pending), skips=len(calls) - len(trades), skip_rate=(len(calls) - len(trades)) / len(calls),
            skip_reasons=dict(Counter(r["reason"] for r in ledger if r["signal"] == "SKIP")),
            discovered_markets_missing_first_call=missing, simulated_pnl=float(pnl),
            pnl_per_settled_trade=float(pnl / len(settled)) if settled else None,
            max_drawdown=float(drawdown), traded_win_rate=sum(r["won"] for r in settled) / len(settled) if settled else None,
            spent=float(spent), unspent_initial_cash=float(cash), nonrecycled_payouts=float(payouts),
            liquid_value_at_cutoff=float(cash + payouts), pending_cost=float(pending_cost),
            equity_at_cost=float(bank + pnl), worst_case_equity=float(cash + payouts),
            paired_scored_markets=len(score_rows), brier=mean("brier"), log_loss=mean("log_loss"),
            midpoint_brier=mean("midpoint_brier"), midpoint_log_loss=mean("midpoint_log_loss"),
            probability_scores=score_rows, decisions=ledger)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "research.sqlite")
    parser.add_argument("--inputs", type=Path, help="Replay a saved input export instead of reading SQLite")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--margin", type=float, default=0.03)
    parser.add_argument("--multiplier", type=float, default=1.0)
    parser.add_argument("--balance-precision", choices=("0.0001", "0.01"), default="0.0001")
    parser.add_argument("--fee-evidence", type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "paper-reports")
    args = parser.parse_args()
    policy = Policy(paper_bankroll=args.bankroll, margin=args.margin, fee_multiplier=args.multiplier,
                    balance_precision=args.balance_precision)
    inputs = json.loads(args.inputs.read_text(encoding="utf-8")) if args.inputs else read_inputs(args.db)
    fee_evidence = json.loads(args.fee_evidence.read_text(encoding="utf-8")) if args.fee_evidence else None
    result = replay(inputs, policy, fee_evidence)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = args.out_dir / (stamp + "-" + VERSION)
    destination.mkdir(parents=True, exist_ok=False)
    if args.fee_evidence:
        evidence = args.fee_evidence.read_bytes()
        (destination / "fee-evidence.json").write_bytes(evidence)
        result["fee_evidence_sha256"] = hashlib.sha256(evidence).hexdigest()
    for name, value in (("inputs.json", inputs), ("report.json", result)):
        (destination / name).write_text(json.dumps(json_safe(value), indent=2, allow_nan=False) + "\n", encoding="utf-8")
    summary = {source: {k: v for k, v in metrics.items() if k not in ("decisions", "probability_scores", "discovered_markets_missing_first_call")}
               for source, metrics in result["sources"].items()}
    print(json.dumps(dict(report=str(destination / "report.json"), sources=summary), indent=2))


if __name__ == "__main__":
    main()
