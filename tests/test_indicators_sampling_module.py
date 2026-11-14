"""ATR và tham số lấy mẫu AI: hàm module và wrapper trong bot == golden sinh từ code gốc TRƯỚC khi tách."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mt5_scalping_bot as b
from ind_cases import atr_cases, sampling_cases
from trading_modules.indicators import calculate_atr
from trading_modules.kronos_sampling import compute_adaptive_sampling_params

G = json.load(open(os.path.join(ROOT, "tests", "golden", "ind_golden.json"), encoding="utf-8"))


class TestIndicators(unittest.TestCase):
    def test_atr_module_va_wrapper(self):
        app = object.__new__(b.KronosDesktopApp)
        cases = atr_cases()
        self.assertEqual(len(cases), len(G["atr"]))
        for name, df, period in cases:
            self.assertEqual(calculate_atr(df, period), G["atr"][name], name)
            self.assertEqual(app.calculate_atr(df, period), G["atr"][name], name)

    def test_sampling_module_va_wrapper(self):
        app = object.__new__(b.KronosDesktopApp)
        cases = sampling_cases()
        self.assertEqual(len(cases), len(G["sampling"]))
        for name, df, tf in cases:
            exp = G["sampling"][name]
            self.assertEqual(list(compute_adaptive_sampling_params(df, tf)), exp, name)
            self.assertEqual(list(app._compute_adaptive_sampling_params(df, tf)), exp, name)

    def test_ba_che_do_deu_xuat_hien(self):
        tags = {v[2].split("-")[1] for v in G["sampling"].values()}
        self.assertTrue({"Scalp", "Range", "Impulse"} <= tags)


if __name__ == "__main__":
    unittest.main()
