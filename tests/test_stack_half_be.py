"""Test luật nhồi lệnh thoát nửa số lệnh giá xấu ở BE+ (stack_half_be, 19/11/2026):
n lệnh cùng chiều chưa BE (n >= 2) -> n//2 lệnh giá xấu nhất vào cơ chế 'chờ về Entry+ thoát'; chỉ chọn lại khi số lệnh tăng."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b  # noqa: E402

BUY = b.mt5.POSITION_TYPE_BUY if b.mt5 else 0
SELL = b.mt5.POSITION_TYPE_SELL if b.mt5 else 1


def _pos(ticket, price_open, typ=None, tp=3830.0, sl=3780.0):
    return SimpleNamespace(ticket=ticket, price_open=price_open, type=BUY if typ is None else typ, tp=tp, sl=sl,
                           profit=-1.0, symbol="XAUUSDm")


def _app():
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    app.profile_ctx = {"XAUUSD": {}}
    app.pending_be_exit_tickets = set()
    app.log = mock.Mock()
    app._write_file_log = mock.Mock()
    app._get_symbol_tolerances = mock.Mock(return_value={"eps_equal": 0.05})
    return app


CFG = {"stack_half_be": True, "be_plus_price": "0.2"}


class TestStackHalfBe(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def _step(self, app, positions):
        app._stack_half_be_step("XAUUSD", "XAUUSDm", positions, CFG)

    def test_mac_dinh_tat_va_du_6_buoc(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["stack_half_be"], False)
        self.assertIn("stack_half_be", b.PARAM_HELP_GUIDE)
        src = open(os.path.join(ROOT, "mt5_scalping_bot.py"), encoding="utf-8").read()
        self.assertIn('"stack_half_be": self.stack_half_be_var', src)
        self.assertIn('"recovery_close", "stack_half_be"]', src)
        self.assertIn('help_key="stack_half_be"', src)

    def test_3_lenh_buy_dca_thoat_lenh_gia_cao_nhat(self):
        app = _app()
        self._step(app, [_pos(1, 3800.0), _pos(2, 3797.0), _pos(3, 3794.0)])
        self.assertEqual(app.pending_be_exit_tickets, {1})

    def test_4_lenh_thoat_2_lenh_xau(self):
        app = _app()
        self._step(app, [_pos(1, 3800.0), _pos(2, 3797.0), _pos(3, 3794.0), _pos(4, 3791.0)])
        self.assertEqual(app.pending_be_exit_tickets, {1, 2})

    def test_sell_gia_thap_nhat_la_xau(self):
        app = _app()
        self._step(app, [_pos(1, 3800.0, SELL), _pos(2, 3803.0, SELL)])
        self.assertEqual(app.pending_be_exit_tickets, {1})

    def test_1_lenh_khong_lam_gi(self):
        app = _app()
        self._step(app, [_pos(1, 3800.0)])
        self.assertEqual(app.pending_be_exit_tickets, set())

    def test_tang_dan_tu_2_len_3_len_4(self):
        app = _app()
        p1, p2, p3, p4 = _pos(1, 3800.0), _pos(2, 3797.0), _pos(3, 3794.0), _pos(4, 3791.0)
        self._step(app, [p1, p2])
        self.assertEqual(app.pending_be_exit_tickets, {1})
        self._step(app, [p1, p2, p3])  # 3//2 = 1 -> đã đủ, không chọn thêm
        self.assertEqual(app.pending_be_exit_tickets, {1})
        self._step(app, [p1, p2, p3, p4])  # 4//2 = 2 -> thêm lệnh xấu tiếp theo
        self.assertEqual(app.pending_be_exit_tickets, {1, 2})

    def test_lenh_xau_dong_xong_khong_keo_lenh_khac(self):
        app = _app()
        p1, p2, p3 = _pos(1, 3800.0), _pos(2, 3797.0), _pos(3, 3794.0)
        self._step(app, [p1, p2, p3])
        app.pending_be_exit_tickets.discard(1)  # lệnh 1 đã thoát BE+
        self._step(app, [p2, p3])  # số lệnh giảm -> không chọn lại
        self._step(app, [p2, p3])
        self.assertEqual(app.pending_be_exit_tickets, set())

    def test_khoi_dong_lai_nhan_ra_tp_da_ve_be(self):
        app = _app()  # mất bộ nhớ: lệnh 1 đã có TP = Entry+0.2
        self._step(app, [_pos(1, 3800.0, tp=3800.2), _pos(2, 3797.0), _pos(3, 3794.0)])
        self.assertEqual(app.pending_be_exit_tickets, set())

    def test_lenh_da_be_khong_tinh(self):
        app = _app()
        p1, p2 = _pos(1, 3800.0), _pos(2, 3797.0)
        with mock.patch.object(b.KronosDesktopApp, "_is_pos_at_be", side_effect=lambda p, eps: p.ticket == 1):
            self._step(app, [p1, p2])
        self.assertEqual(app.pending_be_exit_tickets, set())

    def test_hai_chieu_tinh_rieng(self):
        app = _app()
        self._step(app, [_pos(1, 3800.0), _pos(2, 3797.0), _pos(3, 3800.0, SELL)])
        self.assertEqual(app.pending_be_exit_tickets, {1})


if __name__ == "__main__":
    unittest.main()
