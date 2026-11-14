"""Module Price Action: hàm mới và wrapper trong bot == golden sinh từ code gốc TRƯỚC khi tách (175 ca)."""
import json
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mt5_scalping_bot as b
from pa_cases import make_cases
from trading_modules.price_action import evaluate_price_action

GOLDEN = json.load(open(os.path.join(ROOT, "tests", "golden", "pa_golden.json"), encoding="utf-8"))


def run_wrapper(c):
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    app._get_profile_cfg = lambda pk: {"pa_fail_open": c["fail_open"], "pa_min_confirmations": str(c["req_conf"])}
    app.fetch_mt5_candles = lambda *a, **k: c["df"]
    tick = None if c["ask"] is None else SimpleNamespace(ask=c["ask"], bid=c["bid"])
    with mock.patch.object(b, "mt5", SimpleNamespace(symbol_info_tick=lambda s: tick)), mock.patch.object(b, "MT5_AVAILABLE", True):
        return list(app._check_price_action_entry_confirmation("XAUUSDm", c["action"], "M1", atr=c["atr"], profile_key="XAUUSD"))


class TestPriceActionModule(unittest.TestCase):
    def test_wrapper_giong_golden(self):
        cases = make_cases()
        self.assertEqual(len(cases), len(GOLDEN))
        for c in cases:
            self.assertEqual(run_wrapper(c), GOLDEN[c["name"]], c["name"])

    def test_ham_thuan_giong_golden_khi_du_lieu_hop_le(self):
        n = 0
        for c in make_cases():
            if c["df"] is None or len(c["df"]) < 2 or c["ask"] is None or "thieu_cot" in c["name"]:
                continue
            rc = max(1, min(3, int(c["req_conf"])))
            ok, msg = evaluate_price_action(c["action"], c["df"], c["ask"], c["bid"], c["atr"], "M1", rc)
            self.assertEqual([ok, msg], GOLDEN[c["name"]], c["name"])
            n += 1
        self.assertGreater(n, 150)

    def test_ham_thuan_nem_loi_khi_thieu_cot(self):
        c = next(x for x in make_cases() if x["name"] == "thieu_cot")
        with self.assertRaises(Exception):
            evaluate_price_action("BUY", c["df"], c["ask"], c["bid"], 1.0, "M1", 1)


if __name__ == "__main__":
    unittest.main()
