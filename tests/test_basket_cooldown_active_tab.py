"""Tạm nghỉ sau chốt chùm (basket_tp_usd + basket_cooldown_mins) phải có hiệu lực ở TAB ĐANG MỞ.
Lỗi cũ (rà soát backtest vs live 19/11/2026): luồng chốt chùm chỉ ghi ctx["basket_cooldown_until"] / ctx["is_basket_closing"],
vòng tick live gọi _sync_self_to_active_ctx() mỗi giây chép self.* (vẫn rỗng) đè lên ctx -> sau ~1 giây hết tạm nghỉ
(bot vào lệnh lại ngay) và mất khóa chống đóng trùng. Backtest chạy nhánh tab ngầm nên không bị -> backtest khác live.
Toàn bộ mock: không MT5, không Tk."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b  # noqa: E402


def make_app(active: bool):
    app = object.__new__(b.KronosDesktopApp)
    app.active_profile_key = "XAUUSD" if active else "BTCUSD"
    app._loading_settings = False
    app.profile_ctx = {"XAUUSD": app._create_empty_profile_ctx("XAUUSD"), "BTCUSD": app._create_empty_profile_ctx("BTCUSD")}
    for attr in b.PROFILE_STATE_ATTRS:
        setattr(app, attr, app.profile_ctx[app.active_profile_key].get(attr))
    app.log = mock.Mock()
    app.show_toast = mock.Mock()
    app.log_error = mock.Mock()
    app._close_all_positions_for_profile = mock.Mock(return_value=2)
    return app


class TestBasketCooldownActiveTab(unittest.TestCase):
    def test_cooldown_survives_sync_in_active_tab(self):
        for active in (True, False):
            app = make_app(active)
            app._execute_basket_tp_close("XAUUSD", "XAUUSDm", 52.0, 50.0, {"basket_cooldown_mins": "15"})
            self.assertTrue(app._is_cooldown_active("XAUUSD", "BUY"))
            for _ in range(3):  # 3 nhịp vòng tick live
                app._sync_self_to_active_ctx()
            self.assertTrue(app._is_cooldown_active("XAUUSD", "BUY"), f"active={active}: tạm nghỉ chốt chùm bị xóa sau đồng bộ")
            self.assertTrue(app._is_cooldown_active("XAUUSD", "SELL"))
            self.assertIn("chốt chùm", app._get_cooldown_remaining_str("XAUUSD"))

    def test_closing_lock_survives_sync_in_active_tab(self):
        app = make_app(True)
        ctx = app.profile_ctx["XAUUSD"]
        ctx["is_basket_closing"] = True  # vòng tick vừa đặt khóa và khởi chạy luồng chốt chùm
        app._sync_self_to_active_ctx()
        self.assertTrue(ctx["is_basket_closing"], "khóa chống đóng trùng bị xóa khi luồng chốt chùm còn đang chạy")

    def test_cooldown_zero_means_no_pause(self):
        app = make_app(True)
        app._execute_basket_tp_close("XAUUSD", "XAUUSDm", 52.0, 50.0, {"basket_cooldown_mins": "0"})
        app._sync_self_to_active_ctx()
        self.assertFalse(app._is_cooldown_active("XAUUSD", "BUY"))  # = 0: vào lại ngay như cài đặt
        self.assertFalse(app.profile_ctx["XAUUSD"]["is_basket_closing"])  # đóng xong mở khóa

    def test_cooldown_still_expires(self):
        app = make_app(True)
        app._execute_basket_tp_close("XAUUSD", "XAUUSDm", 52.0, 50.0, {"basket_cooldown_mins": "15"})
        app.profile_ctx["XAUUSD"]["basket_cooldown_until"] = datetime.now() - timedelta(seconds=1)  # đã quá 15 phút
        app._sync_self_to_active_ctx()
        self.assertFalse(app._is_cooldown_active("XAUUSD", "BUY"))

    def test_constant_contains_basket_state(self):
        for k in ("basket_cooldown_until", "is_basket_closing"):
            self.assertIn(k, b.AI_MANAGED_STATE_ATTRS)
            self.assertIn(k, b.PROFILE_STATE_ATTRS)


if __name__ == "__main__":
    unittest.main()
