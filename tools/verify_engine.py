"""
tools/verify_engine.py
Kiểm chứng module lõi Kronos (trading_modules/kronos_engine.py) cho KẾT QUẢ GIỐNG HỆT bản gốc (KronosDesktopApp._fast_predict_scenarios)
trên MÔ HÌNH THẬT (CPU, không MT5, không gửi lệnh).
Cách dùng:
    python tools/verify_engine.py --old-root "D:/.../Kronos" --new-root "D:/.../Kronos_modules" [--threads 2] [--cases 4]
- old-root: thư mục chứa mt5_scalping_bot.py GỐC (hàm cũ) và model/weights.
- new-root: thư mục chứa trading_modules/kronos_engine.py (hàm mới).
"""
import argparse
import importlib.util
import os
import sys
import threading
import types

import numpy as np
import pandas as pd


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def mk_frame(rng, n, start="2026-11-02"):
    px = 2000 + np.cumsum(rng.normal(0, 0.6, n))
    op = px + rng.normal(0, 0.2, n)
    cl = px + rng.normal(0, 0.2, n)
    hi = np.maximum(op, cl) + np.abs(rng.normal(0.4, 0.2, n))
    lo = np.minimum(op, cl) - np.abs(rng.normal(0.4, 0.2, n))
    ts = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"timestamps": ts, "open": op, "high": hi, "low": lo, "close": cl, "volume": rng.integers(50, 500, n).astype(float)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-root", required=True)
    ap.add_argument("--new-root", required=True)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--cases", type=int, default=4)
    a = ap.parse_args()
    sys.path.insert(0, a.old_root)
    import mt5_scalping_bot as old
    old.torch.set_num_threads(a.threads)
    new_eng = load_module("kronos_engine_new", os.path.join(a.new_root, "trading_modules", "kronos_engine.py"))

    app = object.__new__(old.KronosDesktopApp)
    app.__dict__.update(_mt5_lock=threading.RLock(), _ai_inference_lock=threading.RLock(), local_cache={},
                        log=lambda *x, **k: None, log_error=lambda *x, **k: None)
    predictor = app.get_local_predictor("AI-base (102.3M - Mạnh nhất)")
    rng = np.random.default_rng(7)
    ok = True
    for k in range(a.cases):
        n = int(rng.choice([120, 300, 450]))
        df = mk_frame(rng, n)
        x_ts = df["timestamps"].copy().reset_index(drop=True)
        y_ts = pd.Series([x_ts.iloc[-1] + pd.Timedelta(minutes=5) * (i + 1) for i in range(6)])
        T, tp, ns = [(0.65, 0.84, 8), (0.58, 0.80, 16), (0.72, 0.86, 8), (0.65, 0.84, 16)][k % 4]
        s_old = app._fast_predict_scenarios(predictor=predictor, df_in=df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=6,
                                            num_scenarios=ns, T=T, top_k=0, top_p=tp)
        s_new = new_eng.fast_predict_scenarios(predictor, df, x_ts, y_ts, pred_len=6, num_scenarios=ns, T=T, top_k=0, top_p=tp,
                                               inference_lock=app._ai_inference_lock)
        same = len(s_old) == len(s_new) and all(
            o.equals(nn) and o.attrs.get("log_prob") == nn.attrs.get("log_prob") for o, nn in zip(s_old, s_new))
        print(f"ca {k}: n_nen={n} kich_ban={ns} T={T} top_p={tp} -> {'GIONG HET' if same else 'KHAC!'}")
        ok &= same
    print("KET QUA:", "ĐẠT (module == gốc)" if ok else "KHÔNG ĐẠT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
