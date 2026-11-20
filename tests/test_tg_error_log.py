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


if __name__ == "__main__":
    unittest.main()
