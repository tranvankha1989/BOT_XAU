"""Test sau cập nhật OTA: ẩn cửa sổ và khởi động lại thẳng, không dựng lại UI tại chỗ (chống chớp tắt). Mock toàn bộ."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


class TestUpdateRestart(unittest.TestCase):
    def test_restart_thang_khong_dung_lai_ui(self):
        app = object.__new__(b.KronosDesktopApp)
        app.__dict__.update(
            app_version="1.3.11", profile_ctx={"XAUUSD": {"auto_loop_running": True}, "BTCUSD": {}},
            auto_loop_running=True, active_profile_key="XAUUSD", _auto_worker_token={}, _mt5_lock=threading.RLock(),
            root=mock.Mock(), updater=mock.Mock(), save_user_settings=mock.Mock(), log=mock.Mock(),
            _release_active_instance_lock=mock.Mock(), _build_ui=mock.Mock(), _stop_version_badge_blink=mock.Mock(),
        )
        fake_mt5 = mock.Mock()
        with mock.patch.object(b, "mt5", fake_mt5), mock.patch.object(b, "MT5_AVAILABLE", True):
            app._on_update_hot_applied(True, "1.3.12")
        app.save_user_settings.assert_called_once()
        app.root.withdraw.assert_called_once()
        app.updater.restart_application.assert_called_once()
        app._build_ui.assert_not_called()
        app.root.winfo_children.assert_not_called()
        fake_mt5.shutdown.assert_called_once()
        self.assertFalse(app.profile_ctx["XAUUSD"]["auto_loop_running"])


if __name__ == "__main__":
    unittest.main()
