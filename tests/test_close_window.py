"""Test đóng app: không treo khi luồng khác giữ khóa MT5, luôn gọi os._exit và có chốt hẹn giờ. Mock toàn bộ."""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


def make_app():
    app = object.__new__(b.KronosDesktopApp)
    app.__dict__.update(
        _mt5_lock=threading.RLock(), profile_ctx={"XAUUSD": {}}, _auto_worker_token={},
        _ai_queue=mock.Mock(), root=mock.Mock(), save_user_settings=mock.Mock(side_effect=RuntimeError("x")),
        _write_file_log=mock.Mock(), _release_active_instance_lock=mock.Mock(), _cleanup_workspace_junk=mock.Mock(),
    )
    return app


class TestClose(unittest.TestCase):
    def test_khoa_mt5_bi_giu_van_thoat(self):
        app = make_app()
        held = threading.Event()
        release = threading.Event()

        def holder():
            with app._mt5_lock:
                held.set()
                release.wait(10)

        th = threading.Thread(target=holder, daemon=True)
        th.start()
        held.wait(2)
        fake_mt5 = mock.Mock()
        with mock.patch.object(b, "mt5", fake_mt5), mock.patch.object(b, "MT5_AVAILABLE", True), \
                mock.patch.object(b.os, "_exit") as ex, mock.patch.object(b.threading, "Timer") as tm:
            t0 = time.time()
            app._on_close_window()
            self.assertLess(time.time() - t0, 5.0)
            ex.assert_called_once_with(0)
            fake_mt5.shutdown.assert_not_called()  # không chiếm được khóa -> bỏ qua shutdown
            tm.assert_called_once()                 # chốt hẹn giờ tự thoát đã đặt
            app.root.withdraw.assert_called_once()
        release.set()

    def test_binh_thuong_goi_shutdown(self):
        app = make_app()
        fake_mt5 = mock.Mock()
        with mock.patch.object(b, "mt5", fake_mt5), mock.patch.object(b, "MT5_AVAILABLE", True), \
                mock.patch.object(b.os, "_exit") as ex, mock.patch.object(b.threading, "Timer"):
            app._on_close_window()
            fake_mt5.shutdown.assert_called_once()
            ex.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
