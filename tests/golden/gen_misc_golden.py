"""Sinh golden cho _get_symbol_tolerances và _resample_ticks_to_s1 TỪ CODE GỐC. Chạy 1 lần TRƯỚC khi tách."""
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
from misc_cases import tol_cases, tick_cases


def run_tol(c):
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    tick = None if c["bid"] is None else SimpleNamespace(bid=c["bid"], ask=c["ask"])
    info = None if c["stops"] is None else SimpleNamespace(trade_stops_level=c["stops"], point=c["point"])
    fake = SimpleNamespace(symbol_info_tick=lambda s: tick, symbol_info=lambda s: info)
    with mock.patch.object(b, "mt5", fake), mock.patch.object(b, "MT5_AVAILABLE", c["avail"]):
        return app._get_symbol_tolerances(c["symbol"], c["tick_arg"](tick) if callable(c.get("tick_arg")) else None)


if __name__ == "__main__":
    out = {"tol": {}, "ticks": {}}
    for c in tol_cases():
        out["tol"][c["name"]] = run_tol(c)
    app = object.__new__(b.KronosDesktopApp)
    for name, arr in tick_cases():
        out["ticks"][name] = [list(x) for x in app._resample_ticks_to_s1(arr)]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "misc_golden.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True, default=float)
    print(len(out["tol"]), len(out["ticks"]))
