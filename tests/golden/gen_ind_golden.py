"""Sinh golden ATR + tham số lấy mẫu AI TỪ HÀM GỐC (calculate_atr, _compute_adaptive_sampling_params). Chạy 1 lần TRƯỚC khi tách."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mt5_scalping_bot as b
from ind_cases import atr_cases, sampling_cases

app = object.__new__(b.KronosDesktopApp)
out = {"atr": {}, "sampling": {}}
for name, df, period in atr_cases():
    out["atr"][name] = app.calculate_atr(df, period)
for name, df, tf in sampling_cases():
    out["sampling"][name] = list(app._compute_adaptive_sampling_params(df, tf))
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ind_golden.json")
json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
import collections
print(len(out["atr"]), len(out["sampling"]), collections.Counter(v[2].split("-")[1] for v in out["sampling"].values()))
