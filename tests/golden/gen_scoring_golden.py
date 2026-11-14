"""Sinh golden cho _evaluate_bt_ai_candle (toàn bộ chấm điểm + cổng tín hiệu) TỪ CODE GỐC. Chạy 1 lần TRƯỚC khi tách chấm điểm."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import numpy as np
import pandas as pd
import mt5_scalping_bot as b
from scoring_cases import make_cases


def mk(n, last):
    ts = pd.date_range("2026-11-02", periods=n, freq="5min", tz="UTC")
    c = np.full(n, last)
    return pd.DataFrame({"timestamps": ts, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0})


def run_case(c):
    n_ana = 120
    df_ana = mk(n_ana, c["last"])
    df_sc = mk(80, c["last"])
    app = object.__new__(b.KronosDesktopApp)
    app._analyze_scalping_micro_macro_context = lambda *a, **k: dict(c["macro"])
    app._compute_adaptive_sampling_params = lambda *a, **k: (0.65, 0.84, "t")
    # _fast_predict_scenarios giả: trả bản sao (copy() làm mất attrs nên gán lại log_prob)
    def fast(**k):
        out = []
        for s in c["scen"]:
            d = s.copy()
            d.attrs["log_prob"] = s.attrs["log_prob"]
            out.append(d)
        return out
    app._fast_predict_scenarios = fast
    res = app._evaluate_bt_ai_candle(object(), df_ana, 100, dict(c["cfg"]), {}, "M5", False, c["atr"], df_rates_scalp=df_sc, idx_scalp=75, scalp_tf_str="M5")
    if res is None:
        return None
    out = {}
    for k, v in res.items():
        out[k] = v.round(6).to_dict("list") if isinstance(v, pd.DataFrame) else v
    # timestamps -> chuỗi
    if "mean_df" in out:
        out["mean_df"]["timestamps"] = [str(x) for x in out["mean_df"]["timestamps"]]
    return out


if __name__ == "__main__":
    out = {c["name"]: run_case(c) for c in make_cases()}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scoring_golden.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True, default=float)
    import collections
    print(len(out), collections.Counter((v or {}).get("signal", "None") for v in out.values()))
