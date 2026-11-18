"""Test luật gỡ vốn (recovery_close): chỉ đóng lệnh đang lãi >= lỗ ròng đã chốt, chưa BE; có chống đóng đúp."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b  # noqa: E402


def _pos(ticket, profit, sl=3790.0, price_open=3800.0):
    return SimpleNamespace(ticket=ticket, profit=profit, sl=sl, tp=3810.0, price_open=price_open, type=0, symbol="XAUUSDm")


def _app(realized, positions_cfg=None):
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    app.profile_ctx = {"XAUUSD": {}}
    app.log = mock.Mock()
    app._write_file_log = mock.Mock()
    app._write_trace = mock.Mock()
    app._get_today_deals_stats = mock.Mock(return_value=(realized, 1, None))
    app._get_symbol_tolerances = mock.Mock(return_value={"eps_equal": 0.05})
    app._dispatch_pos_action_async = mock.Mock()
    app._close_single_pos = mock.Mock(return_value=(True, "ok"))
    return app


class TestRecoveryClose(unittest.TestCase):
    def test_mac_dinh_tat(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["recovery_close"], False)

    def test_lo_8_89_lai_14_68_thi_dong_lenh_lai(self):
        app = _app(-8.89)
        win = _pos(1, 14.68)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [win], {"debug_trace": False})
        app._dispatch_pos_action_async.assert_called_once()
        self.assertEqual(app._dispatch_pos_action_async.call_args[0][0], ("RECOVERY_CLOSE", 1))

    def test_lai_nho_hon_lo_thi_khong_dong(self):
        app = _app(-8.89)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 5.0)], {})
        app._dispatch_pos_action_async.assert_not_called()

    def test_lenh_da_be_khong_bi_dong(self):
        app = _app(-8.89)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=True):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 14.68)], {})
        app._dispatch_pos_action_async.assert_not_called()

    def test_khong_co_lo_thi_khong_lam_gi(self):
        app = _app(+3.0)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 14.68)], {})
        app._dispatch_pos_action_async.assert_not_called()

    def test_chong_dong_dup_trong_20s(self):
        app = _app(-8.89)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 14.68), _pos(2, 15.0)], {})
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(2, 15.0)], {})
        self.assertEqual(app._dispatch_pos_action_async.call_count, 1)

    def test_loi_trong_ham_khong_ném_ra(self):
        app = _app(-8.89)
        app._get_today_deals_stats = mock.Mock(side_effect=RuntimeError("boom"))
        app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 14.68)], {})
        app._write_file_log.assert_called()


if __name__ == "__main__":
    unittest.main()
