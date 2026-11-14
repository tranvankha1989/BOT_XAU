"""Sinh golden Price Action TỪ HÀM GỐC (_check_price_action_entry_confirmation). Chạy 1 lần TRƯỚC khi tách module."""
import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mt5_scalping_bot as b
from pa_cases import make_cases

out = {}
for c in make_cases():
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    app._get_profile_cfg = lambda pk, c=c: {"pa_fail_open": c["fail_open"], "pa_min_confirmations": str(c["req_conf"])}
    app.fetch_mt5_candles = lambda *a, c=c, **k: c["df"]
    tick = None if c["ask"] is None else SimpleNamespace(ask=c["ask"], bid=c["bid"])
    fake = SimpleNamespace(symbol_info_tick=lambda s, t=tick: t)
    with mock.patch.object(b, "mt5", fake), mock.patch.object(b, "MT5_AVAILABLE", True):
        out[c["name"]] = list(app._check_price_action_entry_confirmation("XAUUSDm", c["action"], "M1", atr=c["atr"], profile_key="XAUUSD"))
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pa_golden.json")
json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
import collections
print(len(out), "ca ->", path, collections.Counter(v[0] for v in out.values()))
