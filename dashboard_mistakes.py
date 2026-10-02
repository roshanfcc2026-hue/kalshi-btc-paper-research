"""Read-only first-call mistake report. Does not change forecasts or outcomes."""
import json
import math
import sqlite3
import time
from pathlib import Path

SOURCE = "volatility-proxy-v1"


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _object(raw):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_mistake_report(db_path, now=None):
    """Select the first row before validation; later updates never replace it.

    markets.result is the official binary label. expiration_value is the
    official BTC index average; settlement_value_dollars is a contract payout.
    Optional metadata status/result, when supplied, must confirm the label.
    """
    generated = time.time() if now is None else _number(now)
    if generated is None:
        raise ValueError("now must be finite")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute("""
            WITH first_calls AS (
              SELECT p.*, ROW_NUMBER() OVER (
                PARTITION BY ticker,source ORDER BY observed,id
              ) AS row_number FROM predictions p WHERE source=?
            )
            SELECT p.id,p.ticker,p.observed,p.close_ts,p.p_yes,p.detail,
                   m.result AS official_result,m.metadata
              FROM first_calls p LEFT JOIN markets m ON m.ticker=p.ticker
             WHERE p.row_number=1 ORDER BY p.observed,p.id
        """, (SOURCE,)).fetchall()
    finally:
        connection.close()

    summary = dict(markets_forecast=len(rows), settled=0, correct=0, wrong=0,
                   pending=0, excluded_invalid_first=0, official_label_issues=0)
    mistakes, excluded, market_numbers = [], [], []
    for index, row in enumerate(rows, 1):
        detail, metadata = _object(row["detail"]), _object(row["metadata"])
        observed, close = _number(row["observed"]), _number(row["close_ts"])
        probability = _number(row["p_yes"])
        issue = None
        if observed is None or close is None or observed <= 0 or close <= observed:
            issue = "invalid_first_timestamp"
        elif probability is None or not 0 <= probability <= 1:
            issue = "invalid_first_probability"
        elif not isinstance(row["ticker"], str) or not row["ticker"]:
            issue = "invalid_first_ticker"
        if issue:
            summary["excluded_invalid_first"] += 1
            excluded.append(dict(market_number=index, forecast_id=row["id"],
                                 ticker=row["ticker"], reason=issue))
            continue

        market_numbers.append(dict(ticker=row["ticker"], market_number=index,
                                   observed=observed, close_ts=close))

        actual = row["official_result"]
        if actual not in ("yes", "no"):
            summary["pending"] += 1
            continue
        status, metadata_result = metadata.get("status"), metadata.get("result")
        if ((status is not None and status not in ("finalized", "settled")) or
                (metadata_result is not None and metadata_result != actual)):
            summary["pending"] += 1
            summary["official_label_issues"] += 1
            continue
        summary["settled"] += 1
        predicted = "yes" if probability >= .5 else "no"
        if predicted == actual:
            summary["correct"] += 1
            continue
        summary["wrong"] += 1
        target = _number(detail.get("target"), positive=True)
        settlement = _number(metadata.get("expiration_value"), positive=True)
        spot = detail.get("spot")
        proxy = _number(spot.get("price"), positive=True) if isinstance(spot, dict) else None
        gap = settlement - target if settlement is not None and target is not None else None
        if gap is not None and not math.isfinite(gap):
            gap = None
        mistakes.append(dict(market_number=index, forecast_id=row["id"], ticker=row["ticker"],
                             source=SOURCE, observed=observed, close_ts=close,
                             p_yes=probability, p_no=1 - probability, predicted=predicted,
                             actual=actual, target=target, official_settlement=settlement,
                             gap=gap, proxy=proxy))
    mistakes.sort(key=lambda row: (row["close_ts"], row["observed"], row["forecast_id"]), reverse=True)
    return dict(generated_at=generated, source=SOURCE, summary=summary, mistakes=mistakes,
                excluded_invalid_first=excluded, market_numbers=market_numbers,
                notes=["One original first forecast per market; later updates never replace it.",
                       "Target and Coinbase proxy are from that first forecast only.",
                       "Official settlement is Kalshi expiration_value, a BRTI average, not a Coinbase tick.",
                       "This report records forecast mistakes, not actual bets or monetary losses."])
