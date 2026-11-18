"""fast_sl_same_dir: tạm nghỉ SL nhanh chỉ chặn chiều vừa SL (mock, không MT5 thật)."""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

PK = "XAUUSD"


def make_app(fs_dir, cfg=None):
    app = object.__new__(b.KronosDesktopApp)
    ctx = {"cooldown_until": None, "basket_cooldown_until": None,
           "fast_sl_cooldown_until": datetime.now() + timedelta(minutes=10), "fast_sl_cooldown_dir": fs_dir}
    app.__dict__.update(active_profile_key="BTCUSD", profile_ctx={PK: ctx},
                        _get_profile_cfg=lambda pk: dict(cfg or {"analysis_tf": "M5"}))
    return app


class TestFastSlSameDir(unittest.TestCase):
    def test_off_blocks_both_directions(self):
        app = make_app(None)
        self.assertTrue(app._is_cooldown_active(PK, action="BUY"))
        self.assertTrue(app._is_cooldown_active(PK, action="SELL"))
        self.assertTrue(app._is_cooldown_active(PK))

    def test_on_blocks_only_same_direction(self):
        app = make_app("BUY")
        self.assertTrue(app._is_cooldown_active(PK, action="BUY"))
        self.assertFalse(app._is_cooldown_active(PK, action="SELL"))
        self.assertTrue(app._is_cooldown_active(PK))  # hiển thị trạng thái vẫn báo đang nghỉ
        self.assertIn("chiều BUY", app._get_cooldown_remaining_str(PK))

    def test_loss_streak_cooldown_still_blocks_opposite(self):
        app = make_app("BUY")
        app.profile_ctx[PK]["cooldown_until"] = datetime.now() + timedelta(minutes=5)
        self.assertTrue(app._is_cooldown_active(PK, action="SELL"))

    def test_expired_not_blocking(self):
        app = make_app("BUY")
        app.profile_ctx[PK]["fast_sl_cooldown_until"] = datetime.now() - timedelta(seconds=1)
        self.assertFalse(app._is_cooldown_active(PK, action="BUY"))

    def test_flag_parsing(self):
        app = make_app(None)
        for v, exp in ((True, True), ("True", True), ("1", True), (False, False), ("False", False), (None, False)):
            self.assertEqual(app._fast_sl_same_dir_on({"fast_sl_same_dir": v}), exp, v)
        self.assertFalse(app._fast_sl_same_dir_on({}))

    def test_default_off_both_profiles(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["fast_sl_same_dir"], False)
        self.assertIn("fast_sl_same_dir", b.PARAM_HELP_GUIDE)


if __name__ == "__main__":
    unittest.main()
