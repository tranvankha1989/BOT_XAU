"""Test tự kết nối lại MT5 khi khởi động lỗi (-6 Authorization failed). Mock toàn bộ, không gọi MT5 thật."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


def make_app():
    app = object.__new__(b.KronosDesktopApp)
    scheduled = []
    root = mock.Mock()
    root.after.side_effect = lambda ms, fn: scheduled.append((ms, fn))
    app.__dict__.update(_mt5_lock=threading.RLock(), root=root, mt5_status_lbl=mock.Mock(),
                        log_error=mock.Mock(), log=mock.Mock(), _algo_trading_on=lambda acc: True)
    return app, scheduled


class TestReconnect(unittest.TestCase):
    def test_loi_6_tu_thu_lai_roi_ket_noi(self):
        app, scheduled = make_app()
        acc = SimpleNamespace(login=1, trade_mode=2, balance=10.0, equity=10.0, server="S", currency="USD")
        fake = mock.Mock()
        fake.initialize.side_effect = [False, False, True]
        fake.last_error.return_value = (-6, "Terminal: Authorization failed")
        fake.account_info.return_value = acc
        with mock.patch.object(b, "mt5", fake), mock.patch.object(b, "MT5_AVAILABLE", True),                 mock.patch.object(b, "_running_mt5_terminal_paths", return_value=[]):
            self.assertFalse(app.check_mt5_connection())
            self.assertEqual(len(scheduled), 1)
            self.assertEqual(scheduled[0][0], 5000)
            self.assertIn("CHƯA ĐĂNG NHẬP", app.mt5_status_lbl.config.call_args.kwargs["text"])
            # Bấm nút thêm lần nữa trong lúc đang chờ -> không đặt lịch trùng
            self.assertFalse(app.check_mt5_connection())
            self.assertEqual(len(scheduled), 1)
            scheduled.pop(0)[1]()   # chạy lần thử lại -> thành công
        self.assertEqual(app._mt5_retry_count, 0)
        self.assertIn("THẬT", app.mt5_status_lbl.config.call_args.kwargs["text"])
        self.assertTrue(any("Tự kết nối lại thành công" in str(c.args[0]) for c in app.log.call_args_list))

    def test_dang_dong_app_khong_thu_lai(self):
        app, scheduled = make_app()
        app._app_closing = True
        fake = mock.Mock()
        fake.initialize.return_value = False
        fake.last_error.return_value = (-6, "x")
        with mock.patch.object(b, "mt5", fake), mock.patch.object(b, "MT5_AVAILABLE", True),                 mock.patch.object(b, "_running_mt5_terminal_paths", return_value=[]):
            app.check_mt5_connection()
        self.assertEqual(scheduled, [])


class TestFindTerminal(unittest.TestCase):
    def test_loi_6_gan_vao_mt5_dang_dang_nhap(self):
        app, scheduled = make_app()
        demo = SimpleNamespace(login=1, trade_mode=0, balance=1.0, equity=1.0, server="Trial", currency="USD")
        real = SimpleNamespace(login=2, trade_mode=2, balance=9.0, equity=9.0, server="Real", currency="USD")
        state = {"path": None}

        def init(path=None):
            state["path"] = path
            return path is not None

        fake = mock.Mock()
        fake.initialize.side_effect = init
        fake.last_error.return_value = (-6, "Terminal: Authorization failed")
        fake.account_info.side_effect = lambda: {"B": demo}.get(state["path"])  # A: không đăng nhập; B: đang đăng nhập DEMO
        fake.terminal_info.return_value = SimpleNamespace(path="B")
        with mock.patch.object(b, "mt5", fake), mock.patch.object(b, "MT5_AVAILABLE", True),                 mock.patch.object(b, "_running_mt5_terminal_paths", return_value=["A", "B"]):
            self.assertTrue(app.check_mt5_connection())
        self.assertEqual(state["path"], "B")
        self.assertEqual(scheduled, [])
        self.assertIn("DEMO", app.mt5_status_lbl.config.call_args.kwargs["text"])

    def test_khong_co_mt5_nao_van_thu_lai(self):
        app, scheduled = make_app()
        fake = mock.Mock()
        fake.initialize.return_value = False
        fake.last_error.return_value = (-6, "x")
        with mock.patch.object(b, "mt5", fake), mock.patch.object(b, "MT5_AVAILABLE", True),                 mock.patch.object(b, "_running_mt5_terminal_paths", return_value=[]):
            self.assertFalse(app.check_mt5_connection())
        self.assertEqual(len(scheduled), 1)
        self.assertTrue(any("không thấy terminal64.exe" in str(c.args[0]) for c in app.log.call_args_list))


if __name__ == "__main__":
    unittest.main()
