"""Telegram: ghi lý do lỗi khi gọi API (chỉ chẩn đoán, không đổi luật). Mock urlopen, không gọi mạng thật."""
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b
from test_phase1 import make_app

TOKEN = "123456:ABCDEF_secret"


def http_err(code, desc):
    body = io.BytesIO(('{"ok": false, "error_code": %d, "description": "%s"}' % (code, desc)).encode("utf-8"))
    return urllib.error.HTTPError("https://api.telegram.org/bot" + TOKEN + "/sendMessage", code, "x", {}, body)


class TestTgErrorLog(unittest.TestCase):
    def _call(self, exc):
        app = make_app(_tg_strip_icons_payload=lambda d: d)
        with mock.patch.object(b.urllib.request, "urlopen", side_effect=exc):
            res = app._telegram_api_call(TOKEN, "sendMessage", {"chat_id": "1", "text": "x"})
        self.assertIsNone(res)
        logged = " ".join(str(c.args[0]) for c in app.log.call_args_list)
        self.assertNotIn(TOKEN, logged)
        self.assertNotIn(TOKEN, app._tg_last_error)
        return app, logged

    def test_mat_mang(self):
        app, logged = self._call(urllib.error.URLError("[Errno 11001] getaddrinfo failed"))
        self.assertIn("không kết nối được máy chủ Telegram", logged)
        self.assertIn("getaddrinfo", app._tg_last_error)

    def test_het_thoi_gian(self):
        _, logged = self._call(TimeoutError("timed out"))
        self.assertIn("hết thời gian chờ", logged)

    def test_sai_token_va_409(self):
        _, logged = self._call(http_err(401, "Unauthorized"))
        self.assertIn("HTTP 401 (sai token): Unauthorized", logged)
        _, logged = self._call(http_err(409, "Conflict: terminated by other getUpdates request"))
        self.assertIn("máy khác đang nghe cùng token", logged)

    def test_gioi_han_tan_suat(self):
        app = make_app(_tg_strip_icons_payload=lambda d: d)
        with mock.patch.object(b.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            for _ in range(5):
                app._telegram_api_call(TOKEN, "getUpdates", {})
        self.assertEqual(app.log.call_count, 1)

    def test_phan_biet_chua_gui_va_tra_loi_cham(self):
        # Hết giờ lúc kết nối (urllib bọc URLError) -> chưa gửi đi; hết giờ lúc chờ trả lời -> đã gửi, Telegram chậm
        app, _ = self._call(urllib.error.URLError(TimeoutError("timed out")))
        self.assertEqual(app._tg_tls_get("kind"), "not_sent")
        self.assertIn("chưa gửi đi", app._tg_tls_get("reason"))
        app, _ = self._call(TimeoutError("timed out"))
        self.assertEqual(app._tg_tls_get("kind"), "sent_slow")
        self.assertIn("đừng bấm gửi lại", app._tg_last_error)
        app = make_app(_tg_strip_icons_payload=lambda d: d)
        with mock.patch.object(b.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            app._telegram_api_call(TOKEN, "getUpdates", {})
        self.assertNotIn("đừng bấm", app._tg_last_error)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TestTgResend(unittest.TestCase):
    def _run_dispatcher(self, kinds):
        """kinds: loại lỗi từng lần gửi; "" = gửi thành công. Trả về số lần gọi gửi."""
        import queue
        app = make_app(_tg_target_chat_id=lambda: "1", telegram_bot_token_var=mock.Mock(get=lambda: TOKEN))
        app._telegram_queue = queue.Queue()
        app._telegram_queue.put(("tin", None))
        app._telegram_queue.put(None)
        seq = list(kinds)

        def fake_send(*a, **k):
            kind = seq.pop(0) if seq else ""
            app._tg_tls_set(kind, kind)
            return {"ok": True} if not kind else None

        app._telegram_send_with_keyboard = mock.Mock(side_effect=fake_send)
        clk = FakeClock()
        with mock.patch.object(b.tk, "StringVar"), mock.patch.object(b.time, "sleep", clk.sleep), \
                mock.patch.object(b.time, "monotonic", clk.monotonic):
            app._telegram_dispatcher_loop()
        return app._telegram_send_with_keyboard.call_count, clk.t

    def test_loi_ket_noi_thi_gui_lai(self):
        n, waited = self._run_dispatcher(["not_sent", "not_sent", ""])
        self.assertEqual(n, 3)
        self.assertEqual(waited, 5 + 15)

    def test_tra_loi_cham_khong_gui_lai(self):
        n, _ = self._run_dispatcher(["sent_slow"])
        self.assertEqual(n, 1)

    def test_bo_tin_sau_10_phut(self):
        n, waited = self._run_dispatcher(["not_sent"] * 50)
        self.assertLessEqual(waited, 600)
        self.assertLess(n, 50)

    def _ai_report(self, kind):
        app = make_app(_tg_ai_brief=lambda pk: "bao cao", _tg_capture_chart_png=lambda: b"png",
                       active_profile_key="XAUUSD", profile_ctx={"XAUUSD": {"auto_loop_running": True}, "BTCUSD": {}},
                       _get_telegram_inline_keyboard=lambda: {}, _tg_strip_icons_payload=lambda d: d)
        app._telegram_send_photo = mock.Mock(side_effect=lambda *a, **k: app._tg_tls_set(kind, kind))
        app._telegram_send_with_keyboard = mock.Mock()
        app._telegram_send_ai_report(TOKEN, "1")
        return app._telegram_send_with_keyboard.call_count

    def test_anh_tra_loi_cham_khong_gui_them_chu(self):
        self.assertEqual(self._ai_report("sent_slow"), 0)
        self.assertEqual(self._ai_report("not_sent"), 1)


if __name__ == "__main__":
    unittest.main()
