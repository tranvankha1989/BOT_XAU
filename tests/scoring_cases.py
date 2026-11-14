"""Ca thử xác định cho chấm điểm kịch bản Kronos: tập kịch bản (OHLC + log_prob) với tỉ lệ tăng/giảm và độ phân tán khác nhau."""
import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close", "volume", "amount"]


def make_scenarios(rng, last_close, n, bull_ratio, spread, pred_len=6, same_lp=False):
    out = []
    ts = pd.date_range("2026-11-02 10:00", periods=pred_len, freq="5min", tz="UTC")
    for i in range(n):
        up = rng.random() < bull_ratio
        drift = (1 if up else -1) * rng.uniform(0.3, 3.0)
        c = last_close + np.cumsum(np.full(pred_len, drift / pred_len * 2)) + rng.normal(0, spread, pred_len)
        o = np.r_[last_close, c[:-1]] + rng.normal(0, 0.1, pred_len)
        h = np.maximum(o, c) + np.abs(rng.normal(0.4, 0.2, pred_len))
        l = np.minimum(o, c) - np.abs(rng.normal(0.4, 0.2, pred_len))
        arr = np.c_[o, h, l, c, np.full(pred_len, 100.0), np.full(pred_len, 1e5)].astype(np.float32)
        d = pd.DataFrame(arr, columns=COLS)
        d.insert(0, "timestamps", ts)
        d.attrs["log_prob"] = 0.0 if same_lp else float(rng.normal(-3.0, 0.8))
        out.append(d)
    return out


def make_cases():
    rng = np.random.default_rng(4242)
    cases = []
    k = 0
    for n in (8, 16):
        for ratio in (0.0, 0.1, 0.5, 0.8, 0.9, 1.0):
            for spread in (0.2, 1.0, 4.0):
              for rep in range(3):
                  last = float(2000 + rng.normal(0, 5))
                  atr = float(np.round(rng.uniform(2.0, 5.0), 3))
                  same = (k % 7 == 0)
                  macro = {"macro_dir": int(rng.integers(-1, 2)), "orderflow_dir": int(rng.integers(-1, 2)),
                           "wick_dir": int(rng.integers(-1, 2)), "structure_reason": "giả"}
                  cfg = {"consensus": float(rng.choice([60, 70, 75, 80, 90])), "min_move": float(rng.choice([0.5, 1.2, 2.0])),
                         "max_dispersion": float(rng.choice([0.0, 1.5, 2.5])), "sl_atr": 1.5, "sl_buffer": 0.5,
                         "use_confidence_gate": bool(k % 2), "trend_filter": bool(k % 3), "bt_use_live_structure": True,
                         "lookback": 100, "scenarios": n, "expected_move_k": 3.0}
                  cases.append(dict(name=f"n{n}/r{ratio}/s{spread}/{rep}#{k}", last=last, atr=atr, scen=make_scenarios(rng, last, n, ratio, spread, same_lp=same),
                                    macro=macro, cfg=cfg))
                  k += 1
    return cases
