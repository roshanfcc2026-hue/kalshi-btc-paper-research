"""Layer 4 cost model: Kalshi trading fee + order-book walking fill model. PAPER ONLY.

FEE FORMULA (UNVERIFIED AGAINST THE PRIMARY SOURCE):
  fee = ceil_to_cent(rate * C * P * (1 - P)),  C = contracts, P = price in dollars
  taker rate 0.07 (general markets); some series use other multipliers (e.g. 0.035 for S&P/Nasdaq);
  maker fees, where charged, are 25% of the taker rate (0.0175).
Official source: Kalshi fee schedule, https://kalshi.com/docs/kalshi-fee-schedule.pdf
  -- could NOT be fetched from the build sandbox (egress blocked). The formula above was taken from
  secondary sources (e.g. https://defirate.com/prediction-markets/fees/, https://allium.so/blog/kalshi-fees-why-the-cost-peaks-near-50-cents/)
  and matches bot.fee. Reports note that crypto 15-minute markets gained fees in early 2026; whether
  KXBTC15M uses a non-default multiplier is NOT confirmed. The user must check the official PDF and set
  TAKER_RATE / MAKER_RATE accordingly before relying on any P&L. Rounding is per order (aggregate), up to the cent.
"""
import math

TAKER_RATE = 0.07
MAKER_RATE = 0.0175
FEE_SOURCE_VERIFIED = False


def fee(fills, rate=TAKER_RATE):
    """fills = [(price, contracts)]; one order -> one aggregate fee, rounded UP to the cent."""
    raw = sum(rate * c * p * (1 - p) for p, c in fills)
    return math.ceil(raw * 100 - 1e-9) / 100 if raw > 0 else 0.0


def ask_levels(yes_bids, no_bids, side):
    """Kalshi books hold bids only. Buying YES lifts NO bids: YES ask = 1 - NO bid, size = that bid's size.
    Returns [(ask_price, size)] best (cheapest) first."""
    opposite = no_bids if side == 'yes' else yes_bids
    return sorted(((round(1 - p, 4), q) for p, q in opposite), key=lambda v: v[0])


def fill(levels, contracts, rate=TAKER_RATE):
    """Walk ask levels for a taker buy of `contracts`. If the order exceeds displayed size at the best
    price the remainder uses the next level, and so on. Unfillable remainder is reported, never assumed filled."""
    if contracts <= 0: raise ValueError('contracts must be positive')
    left, fills = contracts, []
    for p, q in levels:
        if left <= 0: break
        take = min(left, q)
        if take > 0: fills.append((p, take)); left -= take
    filled = contracts - left
    notional = sum(p * c for p, c in fills)
    f = fee(fills, rate)
    return dict(requested=contracts, filled=filled, unfilled=left, fills=fills,
                best_price=levels[0][0] if levels else None, best_size=levels[0][1] if levels else 0.0,
                avg_price=notional / filled if filled else None, notional=notional, fee=f,
                total_cost=notional + f, cost_per_contract=(notional + f) / filled if filled else None,
                levels_used=len(fills))


def expected_value(p_win, cost_per_contract):
    """EV per contract of a $1-payout binary bought at an all-in cost (price + fee share)."""
    return p_win - cost_per_contract
