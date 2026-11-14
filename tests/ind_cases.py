"""Ca thử xác định cho ATR và tham số lấy mẫu AI."""
import numpy as np
import pandas as pd


def _df(rng, n, vol_scale):
    px = 2000 + np.cumsum(rng.normal(0, 0.5, n))
    sc = np.asarray(vol_scale, dtype=float) if np.ndim(vol_scale) else np.full(n, float(vol_scale))
    op = px + rng.normal(0, 0.2, n) * sc
    cl = px + rng.normal(0, 0.2, n) * sc
    hi = np.maximum(op, cl) + np.abs(rng.normal(0.3, 0.2, n)) * sc
    lo = np.minimum(op, cl) - np.abs(rng.normal(0.3, 0.2, n)) * sc
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": cl, "volume": 100.0})


def atr_cases():
    rng = np.random.default_rng(31)
    out = []
    for k in range(30):
        n = int(rng.integers(8, 120))
        out.append((f"atr#{k}", _df(rng, n, float(rng.uniform(0.3, 4.0))), int(rng.choice([7, 14, 20]))))
    return out


def sampling_cases():
    rng = np.random.default_rng(32)
    out = []
    k = 0
    for tf in ["M1", "M5", "M15", "H1", "m5", " M5 ", "X9"]:
        for n in (12, 29, 30, 60, 200):
            for mode in ("flat", "burst", "calm"):
                if mode == "flat":
                    sc = 1.0
                elif mode == "burst":  # biến động bung ở 10 nến cuối
                    sc = np.r_[np.full(max(n - 10, 0), 0.5), np.full(min(n, 10), 5.0)]
                else:                  # biến động co lại ở 10 nến cuối
                    sc = np.r_[np.full(max(n - 10, 0), 4.0), np.full(min(n, 10), 0.3)]
                out.append((f"{tf.strip()}/{n}/{mode}#{k}", _df(rng, n, sc), tf))
                k += 1
    return out
