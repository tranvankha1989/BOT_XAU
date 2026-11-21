"""Ô Nhiệt độ AI (ai_temperature, Mục 4): 0 = tự động như cũ; > 0 thay T tự tính ở cả bot live và Backtest cũ."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b
from trading_modules import param_rules as pr

App = b.KronosDesktopApp


class TestAiTemperature(unittest.TestCase):
    def test_0_trong_sai_thi_tu_dong_nhu_cu(self):
        for v in (0, "0", "", None, "abc", "-1"):
            self.assertEqual(App._apply_ai_temperature({"ai_temperature": v}, 0.65, "M5-Scalp"), (0.65, "M5-Scalp"), v)
        self.assertEqual(App._apply_ai_temperature({}, 0.69, "M5-Range"), (0.69, "M5-Range"))  # file cấu hình cũ thiếu khóa

    def test_tu_dat_thay_t_va_ghi_nhan(self):
        self.assertEqual(App._apply_ai_temperature({"ai_temperature": "0.8"}, 0.65, "M5-Scalp"), (0.8, "M5-Scalp (tự đặt)"))
        self.assertEqual(App._apply_ai_temperature({"ai_temperature": "9"}, 0.65, "M5-Scalp")[0], 2.0)  # kẹp trên

    def test_ap_dung_o_ca_live_va_backtest(self):
        with open(os.path.join(ROOT, "mt5_scalping_bot.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count("dyn_t, regime_tag = self._apply_ai_temperature(cfg, dyn_t, regime_tag)"), 2)
        self.assertEqual(src.count("self._compute_adaptive_sampling_params("), 2)

    def test_mac_dinh_ca_2_profile_la_0(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertEqual(str(b.DEFAULT_PROFILE_SETTINGS[pk]["ai_temperature"]), "0")
        self.assertIn("ai_temperature", b.PARAM_HELP_GUIDE)
        self.assertIn("ai_temperature", App.SETTINGS_SECTION_MAP[4][1])

    def test_kiem_tra_o(self):
        def levels(v):
            return [i["level"] for i in pr.validate_param_values({"ai_temperature": v}) if "ai_temperature" in i["keys"]]
        self.assertEqual(levels("0"), [])
        self.assertEqual(levels("0.8"), [])
        self.assertEqual(levels("0.3"), ["warn"])
        self.assertEqual(levels("1.5"), ["warn"])
        self.assertIn("error", levels("3"))
        self.assertIn("error", levels("abc"))


if __name__ == "__main__":
    unittest.main()
