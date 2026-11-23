"""Test luật gỡ vốn (recovery_close): chỉ dùng lệnh đang lãi >= lỗ ròng đã chốt, chưa BE; có chống xử lý đúp.
recovery_mode: 'Đóng lệnh ngay' = đóng lệnh; mặc định 'Dời SL khóa lãi rồi gồng tiếp' = dời SL khóa phần lỗ + đệm BE+."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b  # noqa: E402


def _pos(ticket, profit, sl=3790.0, price_open=3800.0, price_current=None, ptype=0):
    if price_current is None:
        price_current = price_open + profit / 2.0 if ptype == 0 else price_open - profit / 2.0  # lot 0.02: 1 giá = 2$
    return SimpleNamespace(ticket=ticket, profit=profit, sl=sl, tp=3810.0 if ptype == 0 else 3790.0, price_open=price_open,
                           price_current=price_current, type=ptype, symbol="XAUUSDm")


CLOSE = {"recovery_mode": "Đóng lệnh ngay"}
LOCK = {"recovery_mode": "Dời SL khóa lãi rồi gồng tiếp", "be_plus_price": "0.2"}


def _app(realized, positions_cfg=None):
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    app.profile_ctx = {"XAUUSD": {}}
    app.log = mock.Mock()
    app._write_file_log = mock.Mock()
    app._write_trace = mock.Mock()
    app._get_today_deals_stats = mock.Mock(return_value=(realized, 1, None))
    app._get_symbol_tolerances = mock.Mock(return_value={"eps_equal": 0.05, "min_stop_dist": 0.5})
    app._dispatch_pos_action_async = mock.Mock()
    app._close_single_pos = mock.Mock(return_value=(True, "ok"))
    app._modify_position_sltp = mock.Mock(return_value=True)
    return app


class TestRecoveryClose(unittest.TestCase):
    def test_mac_dinh_tat(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["recovery_close"], False)

    def test_lo_8_89_lai_14_68_thi_dong_lenh_lai(self):
        app = _app(-8.89)
        win = _pos(1, 14.68)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [win], dict(CLOSE, debug_trace=False))
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
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 14.68), _pos(2, 15.0)], CLOSE)
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(2, 15.0)], CLOSE)
        self.assertEqual(app._dispatch_pos_action_async.call_count, 1)

    def test_loi_trong_ham_khong_ném_ra(self):
        app = _app(-8.89)
        app._get_today_deals_stats = mock.Mock(side_effect=RuntimeError("boom"))
        app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 14.68)], {})
        app._write_file_log.assert_called()


    def test_mac_dinh_la_doi_sl_khoa_lai(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertEqual(b.DEFAULT_PROFILE_SETTINGS[pk]["recovery_mode"], "Dời SL khóa lãi rồi gồng tiếp")
        self.assertIn("recovery_mode", b.PARAM_HELP_GUIDE)
        src = open(os.path.join(ROOT, "mt5_scalping_bot.py"), encoding="utf-8").read()
        self.assertIn('"recovery_mode": self.recovery_mode_var', src)
        self.assertIn("recovery_mode", b.KronosDesktopApp.SETTINGS_SECTION_MAP[3][1])
        self.assertIn("self._bind_param_children(self.recovery_close_var, [self.recovery_mode_var])", src)

    def test_buy_du_lai_thi_doi_sl_khoa_no_cong_dem(self):
        # Lỗ 8.89$, BUY 0.02 lot lãi 12$ (giá +6): SL khóa = Entry + 8.89/2 + 0.2 = 3804.645, cách giá 3806 > 0.5
        app = _app(-8.89)
        pos = _pos(1, 12.0)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [pos], LOCK)
        args = app._dispatch_pos_action_async.call_args[0]
        self.assertEqual(args[0], ("RECOVERY_LOCK", 1))
        self.assertIs(args[1], app._modify_position_sltp)
        self.assertIs(args[2], pos)
        self.assertAlmostEqual(args[3], 3804.645, places=6)
        self.assertEqual(args[4], 3810.0)
        app._close_single_pos.assert_not_called()

    def test_sell_du_lai_thi_doi_sl_khoa(self):
        app = _app(-8.89)
        pos = _pos(1, 12.0, sl=3810.0, ptype=1)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [pos], LOCK)
        args = app._dispatch_pos_action_async.call_args[0]
        self.assertEqual(args[0], ("RECOVERY_LOCK", 1))
        self.assertAlmostEqual(args[3], 3795.355, places=6)

    def test_lai_vua_du_chua_du_khoang_cach_thi_cho(self):
        # Lãi 10$ (giá +5): SL khóa 3804.645 > 3805 - 0.5 -> chưa dời, không đóng
        app = _app(-8.89)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 10.0)], LOCK)
        app._dispatch_pos_action_async.assert_not_called()
        app._close_single_pos.assert_not_called()

    def test_da_khoa_1_lenh_thi_khong_khoa_them(self):
        app = _app(-8.89)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 12.0)], LOCK)
        app.profile_ctx["XAUUSD"]["_recovery_last_ts"] = -1e9  # bỏ qua 20s chống đúp
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", side_effect=lambda p, eps=None: p.ticket == 1):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 13.0, sl=3804.65), _pos(2, 14.0)], LOCK)
        self.assertEqual(app._dispatch_pos_action_async.call_count, 1)

    def test_doi_sl_that_bai_thi_thu_lai_sau(self):
        app = _app(-8.89)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 12.0)], LOCK)
            app.profile_ctx["XAUUSD"]["_recovery_last_ts"] = -1e9
            app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 12.0)], LOCK)
        self.assertEqual(app._dispatch_pos_action_async.call_count, 2)

    def test_luong_nen_goi_truc_tiep_va_ghi_log(self):
        app = _app(-8.89)
        out = []
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False):
            t = threading.Thread(target=lambda: app._recovery_close_step("XAUUSD", "XAUUSDm", [_pos(1, 12.0)], LOCK))
            t.start()
            t.join()
        app._modify_position_sltp.assert_called_once()
        app._dispatch_pos_action_async.assert_not_called()
        self.assertIn("gồng tiếp", app.log.call_args[0][0])

if __name__ == "__main__":
    unittest.main()
