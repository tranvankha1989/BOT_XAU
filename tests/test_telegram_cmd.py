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


class TestOwnerOnlyControl(unittest.TestCase):
    """19/11/2026: chỉ chủ bot (ô 'ID người điều khiển') được bật/tắt; nhóm có nhiều bot; nút bấm trong nhóm."""
    GROUP = "-5282444977"
    OWNER = "111"

    def app(self, admins=OWNER):
        a = make_app()
        a.telegram_admin_ids_var = types.SimpleNamespace(get=lambda: admins)
        a._tg_bot_username = "bot_may1"
        a._telegram_send_with_keyboard = mock.Mock()
        return a

    def test_nhom_chi_chu_bot_tat_duoc(self):
        a = self.app()
        a._handle_telegram_command("/pause", self.GROUP, "T", from_id="222", in_group=True)
        a.stop_auto_scan.assert_not_called()
        self.assertIn("Chỉ chủ bot", a._telegram_api_call.call_args[0][2]["text"])
        a = self.app()
        a._handle_telegram_command("/pause", self.GROUP, "T", from_id=self.OWNER, in_group=True)
        self.assertEqual(a.stop_auto_scan.call_count, 2)

    def test_nguoi_khac_van_xem_bao_cao(self):
        a = self.app()
        a._handle_telegram_command("/stats", self.GROUP, "T", from_id="222", in_group=True)
        a._generate_telegram_stats_report.assert_called_once()

    def test_nhom_chua_dien_id_thi_khong_ai_bat_tat(self):
        a = self.app(admins="")
        a._handle_telegram_command("/resume", self.GROUP, "T", from_id=self.OWNER, in_group=True)
        a.start_auto_scan.assert_not_called()
        self.assertIn("Chưa điền", a._telegram_api_call.call_args[0][2]["text"])

    def test_chat_rieng_chua_dien_id_nhu_cu(self):
        a = self.app(admins="")
        a._handle_telegram_command("/resume", "555", "T", from_id="555")
        self.assertEqual(a.start_auto_scan.call_count, 2)

    def test_lenh_gui_bot_khac_bi_bo_qua(self):
        a = self.app()
        a._handle_telegram_command("/pause@bot_may2", self.GROUP, "T", from_id=self.OWNER, in_group=True)
        a.stop_auto_scan.assert_not_called()
        a._telegram_api_call.assert_not_called()
        a._handle_telegram_command("/pause@Bot_May1", self.GROUP, "T", from_id=self.OWNER, in_group=True)
        self.assertEqual(a.stop_auto_scan.call_count, 2)

    def test_nhom_khong_goi_y_khi_tin_thuong(self):
        a = self.app()
        a._handle_telegram_command("chào cả nhà", self.GROUP, "T", from_id="222", in_group=True)
        a._telegram_api_call.assert_not_called()

    def test_nut_bat_tat_trong_nhom(self):
        cq = {"id": "c", "data": "tg_stop_all", "from": {"id": 222}, "message": {"message_id": 5, "chat": {"id": int(self.GROUP)}}}
        a = self.app()
        a._handle_telegram_callback(cq, self.GROUP, "T")
        a.stop_auto_scan.assert_not_called()
        self.assertTrue(a._telegram_api_call.call_args[0][2]["show_alert"])
        cq["from"]["id"] = int(self.OWNER)
        a = self.app()
        with mock.patch.object(b.time, "sleep"):
            a._handle_telegram_callback(cq, self.GROUP, "T")
        self.assertEqual(a.stop_auto_scan.call_count, 2)

    def test_id_nhom_trong_thi_gui_rieng_nguoi_dieu_khien(self):
        a = self.app(admins="111, 333")
        a.telegram_chat_id_var = types.SimpleNamespace(get=lambda: "  ")
        self.assertEqual(a._tg_target_chat_id(), "111")
        a.telegram_chat_id_var = types.SimpleNamespace(get=lambda: self.GROUP)
        self.assertEqual(a._tg_target_chat_id(), self.GROUP)
        a = self.app(admins="")
        a.telegram_chat_id_var = types.SimpleNamespace(get=lambda: "")
        self.assertEqual(a._tg_target_chat_id(), "")

    def test_canh_bao_cau_hinh_cho_phep_trong_id_nhom(self):
        from trading_modules import param_rules as pr
        warn = [i for i in pr.validate_param_values({"telegram_enabled": True, "telegram_bot_token": "x", "telegram_chat_id": "",
                                                "telegram_admin_ids": "111"}) if "Telegram" in i["msg"]]
        self.assertEqual(warn, [])
        warn = [i for i in pr.validate_param_values({"telegram_enabled": True, "telegram_bot_token": "x", "telegram_chat_id": "",
                                                "telegram_admin_ids": ""}) if "Telegram" in i["msg"]]
        self.assertIn("ID nhóm chat hoặc ID người điều khiển", warn[0]["msg"])

    def test_quyen_nhan_tin(self):
        a = self.app()
        self.assertTrue(a._tg_sender_allowed(self.GROUP, "222", self.GROUP))       # tin trong nhóm đã cài
        self.assertTrue(a._tg_sender_allowed(self.GROUP, self.OWNER, self.OWNER))  # chủ bot nhắn riêng
        self.assertFalse(a._tg_sender_allowed(self.GROUP, "222", "222"))           # người lạ nhắn riêng
        self.assertFalse(a._tg_sender_allowed(self.GROUP, "222", "-999"))          # nhóm khác

    def test_polling_nhom_nguoi_la_khong_tat_duoc(self):
        a = self.app()
        a._app_closing = False
        a.telegram_enabled_var = types.SimpleNamespace(get=lambda: True)
        a.telegram_bot_token_var = types.SimpleNamespace(get=lambda: "T")
        a.telegram_chat_id_var = types.SimpleNamespace(get=lambda: self.GROUP)
        a.log = mock.Mock()
        g = int(self.GROUP)
        upd = {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": g}, "from": {"id": 222}, "text": "/pause"}},
            {"update_id": 2, "message": {"chat": {"id": g}, "from": {"id": 111}, "text": "/resume@bot_may1"}},
        ]}

        def api(token, method, payload=None, timeout=0):
            if method == "getMe":
                return {"ok": True, "result": {"username": "Bot_May1"}}
            if method == "getUpdates":
                a._app_closing = True
                return upd
            return {"ok": True}
        a._telegram_api_call = mock.Mock(side_effect=api)
        with mock.patch.object(b.time, "sleep", side_effect=lambda *_: setattr(a, "_app_closing", True)), \
                mock.patch.object(b.tk, "StringVar", mock.Mock()):
            a._telegram_polling_worker()
        a.stop_auto_scan.assert_not_called()
        self.assertEqual(a.start_auto_scan.call_count, 2)


if __name__ == "__main__":
    unittest.main()
