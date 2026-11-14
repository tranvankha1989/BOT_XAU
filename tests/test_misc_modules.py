"""Ngưỡng theo mã và ghép tick S1: module + wrapper trong bot == golden sinh từ code gốc TRƯỚC khi tách."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "golden"))
import mt5_scalping_bot as b
import gen_misc_golden as gg
from misc_cases import tick_cases, tol_cases
from trading_modules.symbol_profile import symbol_tolerances
from trading_modules.tick_resample import resample_ticks_to_s1

G = json.load(open(os.path.join(ROOT, "tests", "golden", "misc_golden.json"), encoding="utf-8"))


class TestMisc(unittest.TestCase):
    def test_tolerances_wrapper_giong_golden(self):
        cases = tol_cases()
        self.assertEqual(len(cases), len(G["tol"]))
        for c in cases:
            self.assertEqual(gg.run_tol(c), G["tol"][c["name"]], c["name"])

    def test_tolerances_ham_thuan(self):
        r = symbol_tolerances("BTCUSDm", 6.4, 0.0)
        self.assertTrue(r["is_btc"])
        self.assertEqual(r["min_stop_dist"], max(0.0, 6.4 * 1.2, 8.0))
        r2 = symbol_tolerances("XAUUSDm", 0.24, 0.3)
        self.assertFalse(r2["is_btc"])
        self.assertEqual(r2["min_stop_dist"], max(0.3 * 1.2, 0.24 * 1.2, 0.50))

    def test_resample_giong_golden(self):
        app = object.__new__(b.KronosDesktopApp)
        cases = tick_cases()
        self.assertEqual(len(cases), len(G["ticks"]))
        for name, arr in cases:
            exp = G["ticks"][name]
            self.assertEqual([list(x) for x in resample_ticks_to_s1(arr)], exp, name)
            self.assertEqual([list(x) for x in app._resample_ticks_to_s1(arr)], exp, name)
        self.assertEqual(resample_ticks_to_s1(None), [])


if __name__ == "__main__":
    unittest.main()
