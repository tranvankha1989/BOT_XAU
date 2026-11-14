"""Module cấu trúc thị trường: hàm mới == hàm gốc (golden sinh từ code gốc TRƯỚC khi tách) trên 204 ca, mọi chế độ xung đột."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mt5_scalping_bot as b
from structure_cases import MODES, make_cases
from trading_modules.structure import analyze_structure_context

GOLDEN = json.load(open(os.path.join(ROOT, "tests", "golden", "structure_golden.json"), encoding="utf-8"))


class TestStructureModule(unittest.TestCase):
    def test_ham_thuan_giong_golden(self):
        n = 0
        for mode in MODES:
            for name, df_ana, df_sc, atr in make_cases():
                got = analyze_structure_context(df_ana, df_sc, atr, mode)
                self.assertEqual(got, GOLDEN[f"{mode}|{name}"], f"{mode}|{name}")
                n += 1
        self.assertEqual(n, len(GOLDEN))

    def test_wrapper_trong_bot_giong_golden(self):
        for mode in MODES:
            app = object.__new__(b.KronosDesktopApp)
            app.active_profile_key = "XAUUSD"
            app._get_profile_cfg = lambda pk, m=mode: {"structure_conflict_mode": m}
            for name, df_ana, df_sc, atr in make_cases():
                self.assertEqual(app._analyze_scalping_micro_macro_context(df_ana, df_sc, atr), GOLDEN[f"{mode}|{name}"], f"{mode}|{name}")

    def test_wrapper_khi_cau_hinh_loi_dung_neutral(self):
        app = object.__new__(b.KronosDesktopApp)  # không có active_profile_key/_get_profile_cfg
        name, df_ana, df_sc, atr = make_cases()[0]
        self.assertEqual(app._analyze_scalping_micro_macro_context(df_ana, df_sc, atr), GOLDEN[f"neutral|{name}"])

    def test_gia_tri_la_nhu_cu(self):
        # chuỗi rỗng KHÔNG phải neutral/analysis: hành vi gốc coi như "scalp"; None -> neutral
        name, df_ana, df_sc, atr = next(c for c in make_cases() if c[0].startswith("up/down"))
        self.assertEqual(analyze_structure_context(df_ana, df_sc, atr, ""), analyze_structure_context(df_ana, df_sc, atr, "scalp"))
        self.assertEqual(analyze_structure_context(df_ana, df_sc, atr, None), analyze_structure_context(df_ana, df_sc, atr, "neutral"))
        self.assertEqual(analyze_structure_context(df_ana, df_sc, atr, " Analysis "), GOLDEN[f"analysis|{name}"])

    def test_du_lieu_rong_khong_no(self):
        r = analyze_structure_context(None, None, 1.0, "neutral")
        self.assertEqual(r["macro_dir"], 0)


if __name__ == "__main__":
    unittest.main()
