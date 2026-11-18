"""Lọc cấu trúc BOS/CHoCH (trend_filter) đã ẩn khỏi giao diện: mặc định TẮT, nạp cấu hình luôn đặt TẮT."""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


class TestTrendFilterHidden(unittest.TestCase):
    def test_default_off_both_profiles(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["trend_filter"], False)

    def test_load_forces_off(self):
        src = inspect.getsource(b.KronosDesktopApp.load_user_settings)
        self.assertIn('pcfg["trend_filter"] = False', src)

    def test_no_ui_widget(self):
        src = inspect.getsource(b.KronosDesktopApp)
        self.assertNotIn('help_key="trend_filter"', src)
        self.assertNotIn('help_key="structure_conflict_mode"', src)


if __name__ == "__main__":
    unittest.main()
