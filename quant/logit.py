"""Ridge-regularized logistic regression with optional fixed offset (pure Python Newton/IRLS)."""
import math
from .signals.common import sigmoid


def _solve(A, b):
    n = len(b); M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c])); M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-15: raise ValueError('singular system')
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1): M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit(X, y, offset=None, l2=1.0, iters=100):
    """Returns [intercept, w1..wd]. The intercept is not penalized. X rows are feature lists."""
    n, d = len(X), len(X[0]) if X else 0
    off = offset or [0.0] * n
    Z = [[1.0] + list(r) for r in X]; w = [0.0] * (d + 1)
    for _ in range(iters):
        g = [0.0] * (d + 1); H = [[0.0] * (d + 1) for _ in range(d + 1)]
        for zi, yi, oi in zip(Z, y, off):
            p = sigmoid(oi + sum(a * b for a, b in zip(w, zi))); s = p * (1 - p)
            for j in range(d + 1):
                g[j] += (p - yi) * zi[j]
                for k in range(d + 1): H[j][k] += s * zi[j] * zi[k]
        for j in range(1, d + 1): g[j] += l2 * w[j]; H[j][j] += l2
        for j in range(d + 1): H[j][j] += 1e-9
        step = _solve(H, g); w = [a - b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-10: break
    return w


def predict(w, x, offset=0.0):
    return sigmoid(offset + w[0] + sum(a * b for a, b in zip(w[1:], x)))


def log_loss(p, y):
    p = min(1 - 1e-12, max(1e-12, p))
    return -math.log(p if y else 1 - p)


def standardize(train_cols):
    """Return (means, sds) for columns; sd floored to avoid division by zero."""
    out = []
    for col in zip(*train_cols):
        m = sum(col) / len(col); sd = math.sqrt(sum((v - m) ** 2 for v in col) / max(1, len(col) - 1))
        out.append((m, sd if sd > 1e-12 else 1.0))
    return out
