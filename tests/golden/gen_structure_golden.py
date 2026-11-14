"""Sinh golden cho cấu trúc thị trường TỪ HÀM GỐC (_analyze_scalping_micro_macro_context). Chạy 1 lần TRƯỚC khi tách module."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mt5_scalping_bot as b
from structure_cases import MODES, make_cases

out = {}
for mode in MODES:
    app = object.__new__(b.KronosDesktopApp)
    app.active_profile_key = "XAUUSD"
    app._get_profile_cfg = lambda pk, m=mode: {"structure_conflict_mode": m}
    for name, df_ana, df_sc, atr in make_cases():
        out[f"{mode}|{name}"] = app._analyze_scalping_micro_macro_context(df_ana, df_sc, atr)
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "structure_golden.json")
json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
print(len(out), "ca ->", path)
