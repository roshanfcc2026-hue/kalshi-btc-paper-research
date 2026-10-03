"""Shared pure helpers for signals. Everything here is deterministic and look-ahead free:
callers pass series already filtered to timestamps <= the forecast time."""
import math
from bisect import bisect_right


def logit(p):
    p = min(1 - 1e-9, max(1e-9, p))
    return math.log(p / (1 - p))


def sigmoid(z):
    z = max(-35.0, min(35.0, z))
    return 1 / (1 + math.exp(-z))


def price_at(series, when, tolerance):
    """Last price at or before `when`, if it is no older than `tolerance` seconds. series=[(ts,price)] sorted."""
    i = bisect_right([s[0] for s in series], when) - 1
    if i >= 0 and when - series[i][0] <= tolerance:
        return series[i][1]
    return None


def latest(series, t, max_age):
    return price_at(series, t, max_age)


def minute_closes(series, t, count=61, tolerance=30.0):
    """Prices at consecutive whole-minute boundaries <= t, oldest->newest, longest contiguous run ending at the
    most recent boundary. Boundary price = last observation within `tolerance` seconds before it."""
    end = math.floor(t / 60) * 60
    prices = []
    for k in range(count):
        p = price_at(series, end - 60 * k, tolerance)
        if p is None:
            break
        prices.append(p)
    return prices[::-1]


def ewma_sigma(returns, lam=0.94):
    var = returns[0] ** 2
    for r in returns[1:]:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var)


def _betacf(a, b, x):
    tiny, qab, qap, qam = 1e-300, a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny); h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + aa * d; d = d if abs(d) > tiny else tiny
            c = 1 + aa / c; c = c if abs(c) > tiny else tiny
            d = 1 / d; delta = d * c; h *= delta
        if abs(delta - 1) < 3e-14:
            break
    return h


def betainc(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def t_cdf(x, df):
    """Student-t CDF (pure Python, via the regularized incomplete beta function)."""
    if x == 0: return 0.5
    tail = 0.5 * betainc(df / 2, 0.5, df / (df + x * x))
    return 1 - tail if x > 0 else tail


def market_mid(kalshi_snapshot):
    """YES midpoint from a Kalshi snapshot: (best yes bid + (1 - best no bid)) / 2. None if a side is empty."""
    if not kalshi_snapshot or not kalshi_snapshot['yes'] or not kalshi_snapshot['no']:
        return None
    return (kalshi_snapshot['yes'][0][0] + 1 - kalshi_snapshot['no'][0][0]) / 2


def bps(ratio):
    return math.log(ratio) * 1e4
