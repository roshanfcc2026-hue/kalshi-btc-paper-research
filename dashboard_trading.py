"""Timestamped ONE-contract paper scenarios; no account, orders or forecast writes."""
import hashlib
import json
import math
import sqlite3
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from paper_decision import Policy, decimal, epoch, kalshi_fee, price, validate_call

VERSION = "dashboard-paper-signals-v1"
SOURCE = "volatility-proxy-v1"
D = Decimal
POLICY = dict(margin=.03, quote_max_age_seconds=3, forecast_max_age_seconds=45,
              weak_direction_band=.1, quantity=1, fee_rate=.07, fee_multiplier=1,
              balance_precision="0.01", fee_status="UNVERIFIED multiplier-1 scenario",
              fee_rounding="model ceiling microdollar; BUY debit ceiling cent; SELL proceeds floor cent",
              execution="one aggregate hypothetical taker fill; no account or actual position")


def _decode(raw):
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Object required")
    return result


def _safe(value):
    try:
        result = float(decimal(value))
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def _skips(reason):
    return {name: dict(action="SKIP", side=None, quantity=1, reason=reason)
            for name in ("none", "yes", "no")}


def _best(levels):
    if not isinstance(levels, list):
        raise ValueError("Missing bid levels")
    bids = {}
    for level in levels:
        if not isinstance(level, list) or len(level) != 2:
            raise ValueError("Malformed bid level")
        bid, depth = price(level[0]), decimal(level[1])
        if depth < 0:
            raise ValueError("Negative depth")
        if depth:
            bids[bid] = bids.get(bid, D(0)) + depth
    if not bids:
        raise ValueError("Empty bid book")
    bid = max(bids)
    return bid, bids[bid]


def _sell(bid):
    # Signed-balance rounding: https://docs.kalshi.com/getting_started/fee_rounding
    # Fee schedule: https://kalshi.com/docs/kalshi-fee-schedule.pdf
    # Seller receives gross proceeds minus microdollar-ceiled model fee, then
    # that positive balance credit is floored to the assumed cent precision.
    model_fee = (D(".07") * bid * (1 - bid)).quantize(D(".000001"), rounding=ROUND_CEILING)
    net = (bid - model_fee).quantize(D(".01"), rounding=ROUND_FLOOR)
    return bid - net, net


def _values(p, quotes):
    result = {}
    for side, chance in (("yes", p), ("no", 1 - p)):
        ask, bid = quotes[side + "_ask"], quotes[side + "_bid"]
        buy_fee = kalshi_fee(ask, balance_precision="0.01")
        sell_fee, sell_net = _sell(bid)
        result[side] = dict(probability=chance, buy_fee=buy_fee,
                            buy_ev=chance - ask - buy_fee, sell_fee=sell_fee,
                            sell_net=sell_net, sell_edge=sell_net - chance)
    return result


def _scenarios(p, values):
    margin = D(".03")
    best = max(("yes", "no"), key=lambda side: values[side]["buy_ev"])
    reason = "weak_direction" if abs(p - D(".5")) < D(".1") else "insufficient_buy_edge"
    buy = abs(p - D(".5")) >= D(".1") and values[best]["buy_ev"] > margin
    result = {"none": dict(action="BUY_" + best.upper() if buy else "SKIP",
                           side=best if buy else None, quantity=1,
                           reason="net_buy_edge_exceeds_margin" if buy else reason)}
    for side in ("yes", "no"):
        sell = values[side]["sell_edge"] > margin
        result[side] = dict(action="SELL_" + side.upper() if sell else "HOLD",
                            side=side, quantity=1,
                            reason="net_sale_exceeds_expected_settlement" if sell else "sale_has_no_margin_over_hold")
    return result


def build_trading_report(db_path, now=None):
    explicit_now = now is not None
    now = time.time() if now is None else _safe(now)
    if now is None:
        raise ValueError("now must be a finite representable number")
    report = dict(version=VERSION, code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  generated_at=now, source=SOURCE, ticker=None, open_ts=None, close_ts=None,
                  observed=None, completed=None, quote_received=None, quote_age_seconds=None,
                  forecast_id=None, snapshot_id=None, first_call=None, forecasts=None, quotes=None,
                  values=None, snapshot_valid=False, current_quote_valid=False,
                  reason="missing_active_contract", scenarios=_skips("missing_active_contract"),
                  snapshot_scenarios=None, live_signal="SKIP", policy=POLICY.copy())
    connection = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        active = []
        for market in connection.execute("SELECT ticker,metadata,result FROM markets"):
            if not isinstance(market["ticker"], str) or not market["ticker"].startswith("KXBTC15M-"):
                continue
            try:
                meta = _decode(market["metadata"])
                opened, close = epoch(meta["open_time"]), epoch(meta["close_time"])
                if opened <= now < close:
                    active.append((market["ticker"], meta, opened, close, market["result"]))
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
        if len(active) != 1:
            if active:
                report["reason"] = "ambiguous_active_contract"
            report["scenarios"] = _skips(report["reason"])
            return report
        ticker, meta, opened, close, official = active[0]
        report.update(ticker=ticker, open_ts=opened, close_ts=close)
        first = connection.execute("SELECT id,observed,close_ts,p_yes FROM predictions WHERE ticker=? AND source=? ORDER BY observed,id LIMIT 1", (ticker, SOURCE)).fetchone()
        if first:
            first_p = _safe(first["p_yes"])
            if first_p is not None and not 0 <= first_p <= 1:
                first_p = None
            report["first_call"] = dict(forecast_id=first["id"], observed=_safe(first["observed"]),
                                        close_ts=_safe(first["close_ts"]), p_yes=first_p)
        # IDs are append sequence. A newest NULL/malformed observation must
        # fail closed, rather than sort below an older valid signal.
        row = connection.execute("SELECT * FROM predictions WHERE ticker=? AND source=? ORDER BY id DESC LIMIT 1", (ticker, SOURCE)).fetchone()
        if row is None:
            report["reason"] = "missing_forecast"
            report["scenarios"] = _skips(report["reason"])
            return report
        row = dict(row)
        report.update(forecast_id=row["id"], observed=_safe(row["observed"]), completed=_safe(row["completed"]))
        books = connection.execute("SELECT * FROM snapshots WHERE ticker=? AND received=? ORDER BY id", (ticker, row["observed"])).fetchall()
        book = dict(books[0]) if len(books) == 1 else None
    finally:
        connection.rollback()
        connection.close()

    def reject(reason):
        report.update(reason=reason, scenarios=_skips(reason))
        return report

    try:
        if not explicit_now:
            now = time.time()
            report["generated_at"] = now
        observed, completed, p = decimal(row["observed"]), decimal(row["completed"]), decimal(row["p_yes"])
        if not opened <= now < close:
            return reject("cycle_ended")
        if close - now <= 60:
            return reject("final_averaging_minute")
        if not observed <= completed <= decimal(now) or not 0 <= p <= 1:
            return reject("future_or_invalid_forecast")
        if decimal(now) - observed > 45:
            return reject("stale_forecast")
        # Saved public metadata must affirm active trading when it has a
        # status. Missing status remains supported by provenance-only fixtures.
        if official in ("yes", "no") or ("status" in meta and meta["status"] != "active"):
            return reject("contract_not_open")
        if meta.get("fee_waiver_expiration_time") is not None or meta.get("fee_waiver"):
            return reject("fee_waiver_requires_review")
        if ((meta.get("fee_type") is not None and meta["fee_type"] != "quadratic") or
                (meta.get("fee_multiplier") is not None and decimal(meta["fee_multiplier"]) != 1)):
            return reject("different_fee_scenario")
        if book is None:
            return reject("missing_or_ambiguous_exact_snapshot")
        report.update(snapshot_id=book["id"], quote_received=_safe(book["received"]))
        row.update(feature_snapshot=book["id"], decision_snapshot=book["id"])
        policy = Policy(margin=.03, balance_precision="0.01")
        error = validate_call(row, meta, {book["id"]: book}, policy, now)
        if error:
            return reject(error)
        detail = _decode(row["detail"])
        if decimal(detail["p_yes"]) != p or decimal(detail["close_ts"]) != decimal(row["close_ts"]):
            return reject("detail_forecast_mismatch")
        yes_ask, no_ask = price(book["yes_ask"]), price(book["no_ask"])
        if abs(price(detail["yes_ask"]) - yes_ask) > D("1e-12") or abs(price(detail["no_ask"]) - no_ask) > D("1e-12"):
            return reject("detail_quote_mismatch")
        raw = _decode(book["raw"])["orderbook_fp"]
        yes_bid, yes_depth = _best(raw["yes_dollars"])
        no_bid, no_depth = _best(raw["no_dollars"])
        if (yes_bid + no_bid > 1 or abs(yes_ask - (1 - no_bid)) > D("1e-12") or
                abs(no_ask - (1 - yes_bid)) > D("1e-12") or
                abs(decimal(book["yes_size"]) - no_depth) > D("1e-9") or
                abs(decimal(book["no_size"]) - yes_depth) > D("1e-9")):
            return reject("raw_book_quote_or_depth_mismatch")
        if min(yes_depth, no_depth) < 1:
            return reject("insufficient_one_contract_depth")
        quotes = dict(yes_ask=yes_ask, no_ask=no_ask, yes_bid=yes_bid, no_bid=no_bid,
                      yes_ask_depth=no_depth, no_ask_depth=yes_depth,
                      yes_bid_depth=yes_depth, no_bid_depth=no_depth)
        values = _values(p, quotes)
        snapshot_scenarios = _scenarios(p, values)
        if not explicit_now:
            now = time.time()
            report["generated_at"] = now
            if not opened <= now < close:
                return reject("cycle_ended")
            if close - now <= 60:
                return reject("final_averaging_minute")
            if decimal(now) - observed > 45:
                return reject("stale_forecast")
        age = decimal(now) - decimal(book["received"])
        current = 0 <= age <= 3
        rendered_quotes = {key: _safe(value) for key, value in quotes.items()}
        if any(value is None for value in rendered_quotes.values()):
            return reject("unrepresentable_book_number")
        report.update(snapshot_valid=True, current_quote_valid=current, quote_age_seconds=float(age),
                      forecasts=dict(p_yes=float(p), p_no=float(1-p)),
                      quotes=rendered_quotes,
                      values={side: {key: float(value) for key, value in item.items()} for side, item in values.items()},
                      snapshot_scenarios=snapshot_scenarios,
                      scenarios=snapshot_scenarios if current else _skips("stale_current_quote"),
                      reason="fresh_paper_scenarios" if current else "stale_current_quote")
        return report
    except (KeyError, ValueError, TypeError, OverflowError, ArithmeticError):
        return reject("malformed_forecast_or_book")
