"""Test A31: lệnh Telegram chỉ khớp NGUYÊN tin nhắn. Toàn bộ mock, không token thật, không gọi mạng/MT5."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


def make_app():
    app = object.__new__(b.KronosDesktopApp)
    app.__dict__.update(
        root=types.SimpleNamespace(after=lambda ms, fn=None: fn() if fn else None),
        start_auto_scan=mock.Mock(), stop_auto_scan=mock.Mock(),
        _telegram_api_call=mock.Mock(),
        _get_telegram_inline_keyboard=mock.Mock(return_value={}),
        _generate_telegram_stats_report=mock.Mock(return_value="report"),
    )
    return app


class TestTelegramCommand(unittest.TestCase):
    def run_cmd(self, text):
        app = make_app()
        app._handle_telegram_command(text, "1", "TOKEN_GIA")
        return app

    def assert_action(self, app, run=0, stop=0, stats=0):
        self.assertEqual(app.start_auto_scan.call_count, run)
        self.assertEqual(app.stop_auto_scan.call_count, stop)
        self.assertEqual(app._generate_telegram_stats_report.call_count, stats)

    def test_lenh_bat(self):
        for t in ("/resume", "/run", "/start_bot", "bật bot", "chạy"):
            self.assert_action(self.run_cmd(t), run=2)

    def test_lenh_tat(self):
        for t in ("/pause", "/stop", "tạm dừng"):
            self.assert_action(self.run_cmd(t), stop=2)

    def test_lenh_thong_ke(self):
        for t in ("/stats", "/status", "thống kê"):
            self.assert_action(self.run_cmd(t), stats=1)

    def test_menu(self):
        for t in ("/start", "/help", "/menu"):
            app = self.run_cmd(t)
            self.assert_action(app)
            self.assertEqual(app._telegram_api_call.call_count, 2)

    def test_cau_thuong_khong_kich_hoat(self):
        for t in ("sao bot không chạy?", "chạy đi", "bật bot lên", "tạm dừng giúp tôi", "cho xem thống kê hôm nay",
                  "/resume now", "đừng chạy", "chạy bot"):
            app = self.run_cmd(t)
            self.assert_action(app)
            # chỉ trả một dòng gợi ý, không kèm bàn phím
            self.assertEqual(app._telegram_api_call.call_count, 1)
            self.assertIn("Lệnh hợp lệ", app._telegram_api_call.call_args[0][2]["text"])

    def test_hoa_thuong_khoang_trang_va_ten_bot(self):
        self.assert_action(self.run_cmd("  CHẠY \n"), run=2)
        self.assert_action(self.run_cmd("/Resume@MyBot"), run=2)
        self.assert_action(self.run_cmd("/PAUSE"), stop=2)
        self.assert_action(self.run_cmd(" Thống Kê "), stats=1)

    def test_chat_id_la_bi_bo_qua(self):
        app = make_app()
        app._app_closing = False
        app.telegram_enabled_var = types.SimpleNamespace(get=lambda: True)
        app.telegram_bot_token_var = types.SimpleNamespace(get=lambda: "TOKEN_GIA")
        app.telegram_chat_id_var = types.SimpleNamespace(get=lambda: "1")
        app.log = mock.Mock()
        upd = {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/resume"}},    # chat lạ
            {"update_id": 2, "message": {"chat": {"id": 1}, "text": "/pause"}},       # đúng chat
        ]}

        def api(token, method, payload=None, timeout=0):
            if method == "getUpdates":
                app._app_closing = True  # chạy đúng 1 vòng
                return upd
            return {"ok": True}
        app._telegram_api_call = mock.Mock(side_effect=api)
        with mock.patch.object(b.time, "sleep", side_effect=lambda *_: setattr(app, "_app_closing", True)), \
                mock.patch.object(b.tk, "StringVar", mock.Mock()):
            app._telegram_polling_worker()
        self.assertEqual(app.start_auto_scan.call_count, 0)
        self.assertEqual(app.stop_auto_scan.call_count, 2)


if __name__ == "__main__":
    unittest.main()
