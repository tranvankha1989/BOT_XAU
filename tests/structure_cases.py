"""Bộ ca thử xác định (deterministic) cho module cấu trúc thị trường: nến giả lập đủ kiểu (xu hướng, đi ngang, chữ V, giật...)."""
import numpy as np
import pandas as pd

MODES = ("neutral", "analysis", "scalp")


def _mk(n, kind, rng, base=2000.0, step_min=5):
    t = np.arange(n, dtype=float)
    if kind == "up":
        mid = base + 0.15 * t + rng.normal(0, 0.4, n)
    elif kind == "down":
        mid = base - 0.15 * t + rng.normal(0, 0.4, n)
    elif kind == "range":
        mid = base + 1.5 * np.sin(t / 5.0) + rng.normal(0, 0.3, n)
    elif kind == "v":
        mid = base + np.abs(t - n / 2) * 0.12 + rng.normal(0, 0.3, n)
    elif kind == "invv":
        mid = base - np.abs(t - n / 2) * 0.12 + rng.normal(0, 0.3, n)
    elif kind == "crash":
        mid = base - np.cumsum(np.abs(rng.normal(0.35, 0.2, n)))
    elif kind == "rally":
        mid = base + np.cumsum(np.abs(rng.normal(0.35, 0.2, n)))
    else:  # walk
        mid = base + np.cumsum(rng.normal(0, 0.6, n))
    spread = np.abs(rng.normal(0.6, 0.25, n)) + 0.05
    op = mid + rng.normal(0, 0.25, n)
    cl = mid + rng.normal(0, 0.25, n)
    hi = np.maximum(op, cl) + spread * rng.random(n)
    lo = np.minimum(op, cl) - spread * rng.random(n)
    vol = rng.integers(20, 400, n).astype(float)
    ts = pd.date_range("2026-11-02", periods=n, freq=f"{step_min}min", tz="UTC")
    return pd.DataFrame({"timestamps": ts, "open": op, "high": hi, "low": lo, "close": cl, "volume": vol})


def make_cases():
    """Trả list (tên, df_ana, df_scalp, atr). Cố định theo seed -> kết quả golden tái lập được."""
    kinds = ["up", "down", "range", "v", "invv", "crash", "rally", "walk"]
    cases = []
    rng = np.random.default_rng(20261114)
    k = 0
    for ka in kinds:
        for ks in kinds:
            n_ana = int(rng.integers(60, 200))
            n_sc = int(rng.integers(20, 80))
            atr = float(np.round(rng.uniform(0.6, 4.0), 3))
            cases.append((f"{ka}/{ks}#{k}", _mk(n_ana, ka, rng), _mk(n_sc, ks, rng, step_min=1), atr))
            k += 1
    # ca biên: dữ liệu ngắn / thiếu
    short = _mk(10, "up", rng)
    cases.append(("scalp_ngan", _mk(120, "down", rng), short, 1.5))
    cases.append(("scalp_none", _mk(120, "up", rng), None, 1.5))
    cases.append(("ca_hai_ngan", short, short.copy(), 1.5))
    cases.append(("ana_none_scalp_dai", None, _mk(60, "walk", rng, step_min=1), 2.0))
    return cases
