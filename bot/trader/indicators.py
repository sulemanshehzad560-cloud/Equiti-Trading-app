"""Plain-Python indicators. Every function returns a list the same length as its
input, with None where there is not yet enough data."""
import math


def sma(values, n):
    out, s = [None] * len(values), 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(values, n):
    out, k, prev = [None] * len(values), 2 / (n + 1), None
    for i, v in enumerate(values):
        if i == n - 1:
            prev = sum(values[:n]) / n
        elif i >= n:
            prev = v * k + prev * (1 - k)
        else:
            continue
        out[i] = prev
    return out


def true_range(highs, lows, closes):
    tr = []
    for i in range(len(closes)):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            pc = closes[i - 1]
            tr.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return tr


def _wilder(values, n, start):
    """Wilder smoothing (running average) of values[start:], seeded with a simple mean."""
    out = [None] * len(values)
    if len(values) < start + n:
        return out
    prev = sum(values[start:start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def atr(highs, lows, closes, n=14):
    return _wilder(true_range(highs, lows, closes), n, 0)


def rsi(closes, n=14):
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = _wilder(gains, n, 1), _wilder(losses, n, 1)
    out = [None] * len(closes)
    for i in range(len(closes)):
        if ag[i] is None:
            continue
        out[i] = 100.0 if al[i] == 0 else 100 - 100 / (1 + ag[i] / al[i])
    return out


def adx(highs, lows, closes, n=14):
    """Returns (adx, +DI, -DI)."""
    size = len(closes)
    pdm, mdm = [0.0] * size, [0.0] * size
    for i in range(1, size):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm[i] = up if up > down and up > 0 else 0.0
        mdm[i] = down if down > up and down > 0 else 0.0
    tr = true_range(highs, lows, closes)
    str_, sp, sm = _wilder(tr, n, 1), _wilder(pdm, n, 1), _wilder(mdm, n, 1)
    pdi, mdi, dx = [None] * size, [None] * size, [0.0] * size
    first_dx = None
    for i in range(size):
        if str_[i] is None or str_[i] == 0:
            continue
        pdi[i], mdi[i] = 100 * sp[i] / str_[i], 100 * sm[i] / str_[i]
        tot = pdi[i] + mdi[i]
        dx[i] = 0.0 if tot == 0 else 100 * abs(pdi[i] - mdi[i]) / tot
        if first_dx is None:
            first_dx = i
    adx_out = _wilder(dx, n, first_dx) if first_dx is not None else [None] * size
    return adx_out, pdi, mdi


def bollinger(values, n=20, k=2.0):
    """Returns (lower, middle, upper)."""
    mid = sma(values, n)
    lo, up = [None] * len(values), [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1:i + 1]
        sd = math.sqrt(sum((v - mid[i]) ** 2 for v in window) / n)
        lo[i], up[i] = mid[i] - k * sd, mid[i] + k * sd
    return lo, mid, up


def donchian(highs, lows, n=20):
    """Returns (lower, upper) channel over the last n bars including the current one."""
    lo, up = [None] * len(highs), [None] * len(highs)
    for i in range(n - 1, len(highs)):
        up[i] = max(highs[i - n + 1:i + 1])
        lo[i] = min(lows[i - n + 1:i + 1])
    return lo, up
