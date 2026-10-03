"""Isotonic calibration (pool-adjacent-violators) and reliability table. Pure Python."""
from bisect import bisect_right


def fit(p, y):
    """Returns sorted (x_threshold, calibrated_value) steps from training predictions p and outcomes y."""
    blocks = []  # [sum_y, count, max_x]
    for x, t in sorted(zip(p, y)):
        blocks.append([t, 1, x])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            s, c, mx = blocks.pop(); blocks[-1][0] += s; blocks[-1][1] += c; blocks[-1][2] = mx
    return [(b[2], b[0] / b[1]) for b in blocks]


def apply(steps, p, floor=0.01, ceil=0.99):
    """Step function: value of the first block whose upper x is >= p (last block beyond the range)."""
    xs = [s[0] for s in steps]; i = min(bisect_right(xs, p - 1e-15), len(steps) - 1)
    # bisect_right on p-eps gives first block with max_x >= p
    return min(ceil, max(floor, steps[i][1]))


def reliability(p, y, buckets=10):
    out = []
    for b in range(buckets):
        lo, hi = b / buckets, (b + 1) / buckets
        idx = [i for i, v in enumerate(p) if lo <= v < hi or (b == buckets - 1 and v == 1.0)]
        out.append(dict(bucket='%d-%d%%' % (lo * 100, hi * 100), n=len(idx),
                        mean_pred=sum(p[i] for i in idx) / len(idx) if idx else None,
                        observed=sum(y[i] for i in idx) / len(idx) if idx else None))
    return out
