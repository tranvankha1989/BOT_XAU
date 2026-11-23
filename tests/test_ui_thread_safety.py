"""Chống treo app (24/11/2026): luồng nền không được gọi Tk trực tiếp qua _ui_after.
Tkinter bắt luồng nền gọi root.after phải CHỜ luồng giao diện xử lý; nếu luồng nền đang giữ _mt5_lock trong khi
luồng giao diện chờ _mt5_lock (vòng tick 1 giây) thì hai bên chờ nhau mãi -> "Not responding" vĩnh viễn."""
import os
import queue
import sys
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b  # noqa: E402


class _FakeTkRoot:
    """Giả lập Tk có luồng: after() gọi từ luồng nền phải chờ luồng giao diện phục vụ (như _tkinter.Tkapp_ThreadSend)."""

    def __init__(self):
        self.serviced = threading.Event()
        self.calls = []

    def after(self, ms, fn=None, *args):
        self.calls.append((ms, fn, args))
        if threading.current_thread() is not threading.main_thread():
            self.serviced.wait(timeout=5.0)

    def report_callback_exception(self, *exc):
        pass


def _app(with_queue=True):
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    app._log_file_lock = threading.Lock()
    app._app_closing = False
    app.root = _FakeTkRoot()
    app._ui_hb_mono = None
    if with_queue:
        app._ui_call_queue = queue.SimpleQueue()
    app._write_file_log = mock.Mock()
    app._append_log = mock.Mock()
    return app


def _run_in_thread(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


class TestUiAfterQueue(unittest.TestCase):
    def test_luong_nen_chi_bo_vao_hang_doi(self):
        app = _app()
        got = []
        _run_in_thread(lambda: app._ui_after(0, got.append, 7)).join(2.0)
        self.assertEqual(app.root.calls, [])  # không gọi Tk từ luồng nền
        self.assertEqual(got, [])
        app._ui_queue_pump()  # luồng giao diện
        self.assertEqual(got, [7])
        self.assertEqual(app.root.calls[-1][0], 30)  # tự hẹn lần bơm sau
        self.assertIsNotNone(app._ui_hb_mono)

    def test_luong_nen_co_tre_thi_bom_hen_lai_bang_after(self):
        app = _app()
        fn = mock.Mock()
        _run_in_thread(lambda: app._ui_after(500, fn, "x")).join(2.0)
        app._ui_queue_pump()
        self.assertIn((500, fn, ("x",)), app.root.calls)
        fn.assert_not_called()

    def test_luong_giao_dien_chay_ngay(self):
        app = _app()
        fn = mock.Mock()
        app._ui_after(0, fn, 1)
        fn.assert_called_once_with(1)

    def test_loi_trong_ham_khong_lam_dung_bom(self):
        app = _app()
        got = []

        def boom():
            raise ValueError("x")

        def enqueue_both():
            app._ui_after(0, boom)
            app._ui_after(0, got.append, 1)

        _run_in_thread(enqueue_both).join(2.0)
        app._ui_queue_pump()
        self.assertEqual(got, [1])


class TestKhongKetKhoaMt5(unittest.TestCase):
    def _worker_holds_lock_and_logs(self, app, started):
        def _w():
            with app._mt5_lock:
                started.set()
                app.log("⚠️ [MODIFY_SLTP] thử")  # như _modify_position_sltp ghi log khi đang giữ _mt5_lock
        return _run_in_thread(_w)

    def test_luong_giao_dien_lay_duoc_khoa_khi_luong_nen_ghi_log(self):
        app = _app(with_queue=True)
        started = threading.Event()
        t = self._worker_holds_lock_and_logs(app, started)
        self.assertTrue(started.wait(2.0))
        got = app._mt5_lock.acquire(timeout=2.0)  # luồng giao diện chờ khóa như vòng tick
        try:
            self.assertTrue(got, "luồng giao diện không lấy được _mt5_lock -> treo")
        finally:
            if got:
                app._mt5_lock.release()
            app.root.serviced.set()
            t.join(2.0)

    def test_cach_cu_goi_root_after_tu_luong_nen_thi_ket(self):
        """Ghi lại nguyên nhân: không có hàng đợi (cách cũ) -> luồng nền kẹt trong root.after khi còn giữ khóa."""
        app = _app(with_queue=False)
        started = threading.Event()
        t = self._worker_holds_lock_and_logs(app, started)
        self.assertTrue(started.wait(2.0))
        got = app._mt5_lock.acquire(timeout=0.5)
        try:
            self.assertFalse(got)
        finally:
            if got:
                app._mt5_lock.release()
            app.root.serviced.set()
            t.join(2.0)


class TestTelegramMoLenh(unittest.TestCase):
    def test_luong_nen_khong_doc_o_tk(self):
        app = _app()
        app.telegram_enabled_var = mock.Mock()
        app.telegram_enabled_var.get.return_value = False
        app.telegram_notify_open_var = mock.Mock()
        app._telegram_queue = queue.SimpleQueue()
        args = (123, "XAUUSDm", "BUY", 0.02, 3600.0, 3595.0, 3610.0, "XAUUSD")
        _run_in_thread(lambda: app._telegram_notify_order_opened(*args)).join(2.0)
        app.telegram_enabled_var.get.assert_not_called()
        app._ui_queue_pump()
        app.telegram_enabled_var.get.assert_called_once()


class TestUiWatchdog(unittest.TestCase):
    def test_ghi_ngan_xep_mot_lan_va_bao_hoi_phuc(self):
        app = _app()
        clock = {"t": 1000.0, "n": 0}

        def fake_sleep(_s):
            clock["t"] += 2.0
            clock["n"] += 1
            if clock["n"] == 12:
                app._ui_hb_mono = clock["t"]  # giao diện chạy lại
            if clock["n"] >= 13:
                app._app_closing = True

        app._ui_hb_mono = 1000.5
        with mock.patch.object(b.time, "monotonic", side_effect=lambda: clock["t"]), \
                mock.patch.object(b.time, "sleep", side_effect=fake_sleep):
            app._ui_freeze_watchdog_loop()
        msgs = [c.args[0] for c in app._write_file_log.call_args_list]
        dumps = [m for m in msgs if "Giao diện không phản hồi" in m]
        self.assertEqual(len(dumps), 1)
        self.assertIn("LUỒNG GIAO DIỆN", dumps[0])
        self.assertIn("test_ghi_ngan_xep_mot_lan_va_bao_hoi_phuc", dumps[0])
        self.assertTrue(any("hoạt động lại" in m for m in msgs))

    def test_khong_bao_khi_may_ngu(self):
        app = _app()
        clock = {"t": 1000.0, "n": 0}

        def fake_sleep(_s):
            clock["n"] += 1
            clock["t"] += 3600.0 if clock["n"] == 1 else 2.0  # máy ngủ 1 giờ rồi thức dậy
            if clock["n"] == 2:
                app._ui_hb_mono = clock["t"]
            if clock["n"] >= 4:
                app._app_closing = True

        app._ui_hb_mono = 1000.0
        with mock.patch.object(b.time, "monotonic", side_effect=lambda: clock["t"]), \
                mock.patch.object(b.time, "sleep", side_effect=fake_sleep):
            app._ui_freeze_watchdog_loop()
        app._write_file_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
