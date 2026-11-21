"""Telegram 19/11/2026: tin gọn 2 dòng, nút chỉ ở tin mới nhất, nút '🧠 Phân tích AI', báo đóng lệnh nhanh
(không ghi bộ đệm của luật giao dịch), lãi ngày tính từ cùng danh sách lệnh đã đóng. Toàn bộ mock, không mạng/MT5."""
import os
import queue
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def make_app(**kw):
    app = object.__new__(b.KronosDesktopApp)
    app.__dict__.update(
        root=types.SimpleNamespace(after=lambda ms, fn=None: fn() if fn else None),
        start_auto_scan=mock.Mock(), stop_auto_scan=mock.Mock(),
        _telegram_api_call=mock.Mock(),
        _get_telegram_inline_keyboard=mock.Mock(return_value={"inline_keyboard": [[{"text": "x", "callback_data": "tg_stats"}]]}),
        _generate_telegram_stats_report=mock.Mock(return_value="report"),
        _tg_kb_lock=threading.Lock(), _tg_last_kb_msg_id=None,
        _telegram_queue=queue.Queue(), _telegram_sent_deal_tickets=set(),
        telegram_enabled_var=_Var(True), telegram_notify_open_var=_Var(True), telegram_notify_close_var=_Var(True),
        profile_ctx={"XAUUSD": {}, "BTCUSD": {}},
    )
    app.__dict__.update(kw)
    return app


class TestKeyboard(unittest.TestCase):
    def test_hang_dau_co_bao_cao_va_phan_tich_ai(self):
        app = make_app(profile_ctx={"XAUUSD": {"auto_loop_running": True}, "BTCUSD": {}})
        del app.__dict__["_get_telegram_inline_keyboard"]  # dùng hàm thật
        kb = app._get_telegram_inline_keyboard()["inline_keyboard"]
        self.assertEqual([x["callback_data"] for x in kb[0]], ["tg_stats", "tg_ai"])
        self.assertEqual(kb[1][0]["text"], "🟡 VÀNG: ⏸ DỪNG")


class TestSendWithKeyboard(unittest.TestCase):
    def _api(self):
        ids = iter([101, 102, 103])

        def call(token, method, data=None, timeout=15):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": next(ids)}}
            return {"ok": True}
        return mock.Mock(side_effect=call)

    def test_go_nut_o_tin_truoc(self):
        app = make_app(_telegram_api_call=self._api())
        app._telegram_send_with_keyboard("T", "1", "tin 1")
        self.assertEqual(app._telegram_api_call.call_count, 1)
        app._telegram_send_with_keyboard("T", "1", "tin 2")
        calls = app._telegram_api_call.call_args_list
        self.assertEqual(calls[1][0][1], "sendMessage")
        self.assertIn("inline_keyboard", calls[1][0][2]["reply_markup"])
        self.assertEqual(calls[2][0][1], "editMessageReplyMarkup")
        self.assertEqual(calls[2][0][2]["message_id"], 101)
        self.assertEqual(calls[2][0][2]["reply_markup"], {"inline_keyboard": []})
        self.assertEqual(app._tg_last_kb_msg_id, 102)

    def test_gui_loi_khong_go_nut_cu(self):
        app = make_app(_telegram_api_call=mock.Mock(return_value=None), _tg_last_kb_msg_id=55)
        self.assertIsNone(app._telegram_send_with_keyboard("T", "1", "tin"))
        self.assertEqual(app._telegram_api_call.call_count, 1)
        self.assertEqual(app._tg_last_kb_msg_id, 55)


OFF2 = "VÀNG · AUTO TẮT\nBTC · AUTO TẮT"  # cả 2 mã AUTO TẮT: chỉ gửi chữ


class TestAiButton(unittest.TestCase):
    def test_bam_nut_ai(self):
        app = make_app(_telegram_send_with_keyboard=mock.Mock())
        app._handle_telegram_callback({"id": "cq1", "data": "tg_ai", "message": {"message_id": 7}}, "1", "T")
        self.assertEqual(app._telegram_api_call.call_args[0][1], "answerCallbackQuery")
        app._telegram_send_with_keyboard.assert_called_once_with("T", "1", OFF2)

    def test_lenh_go_ai(self):
        for t in ("/ai", "phân tích", "Phân tích AI"):
            app = make_app(_telegram_send_with_keyboard=mock.Mock())
            app._handle_telegram_command(t, "1", "T")
            app._telegram_send_with_keyboard.assert_called_once_with("T", "1", OFF2)
            app.start_auto_scan.assert_not_called()
            app.stop_auto_scan.assert_not_called()

    def test_noi_dung_phan_tich(self):
        ctx = {
            "_tg_ai_info": {"ts": "13:10:03", "tf": "M5"}, "auto_loop_running": True,
            "_sig_title": "🔴 TÍN HIỆU [XAUUSD]: SELL XAUUSDm — Đồng thuận 91%",
            "_last_detail_prefix": "Giá đóng nến: 3732.37  |  Dự báo 6 nến: 3730.09 (-2.28$)\nGợi ý AI ->  Entry: 3732.37   |   SL: 3738.03",
            "pending_pullback_order": {"zone_watch": True, "action": "SELL", "sl_ai_price": 3735.53, "effective_max_sl": 4.0},
        }
        app = make_app(profile_ctx={"XAUUSD": ctx, "BTCUSD": {}})
        rep = app._generate_telegram_ai_report()
        self.assertIn("🧠 VÀNG · AI M5 lúc 13:10:03 · AUTO BẬT", rep)
        self.assertIn("Đồng thuận 91%", rep)
        self.assertIn("Giá đóng nến: 3732.37 | Dự báo 6 nến: 3730.09 (-2.28$)", rep)
        self.assertIn("⏳ Đang canh vùng SELL ≥ 3731.53", rep)
        self.assertNotIn("BTC", rep)

    def test_chua_co_phan_tich(self):
        self.assertIn("Chưa có kết quả", make_app()._generate_telegram_ai_report())


class TestCompactMessages(unittest.TestCase):
    def test_tin_vao_lenh(self):
        app = make_app(profile_ctx={"XAUUSD": {"cached_consensus": "91"}, "BTCUSD": {}})
        app._telegram_notify_order_opened(4182479748, "XAUUSDm", "BUY", 0.02, 3733.14, 3729.00, 3748.50, "XAUUSD")
        msg, markup = app._telegram_queue.get_nowait()
        l1, l2 = msg.split("\n")
        self.assertTrue(l1.startswith("🚀 VÀNG BUY 0.02 @ 3733.14 · "))
        self.assertEqual(l2, "SL 3729.00 (-4.14) · TP 3748.50 (+15.36 · 3.7R) · AI 91% · #4182479748")
        self.assertIsNone(markup)

    def test_tin_vao_lenh_chua_co_dong_thuan(self):
        app = make_app()
        app._telegram_notify_order_opened(5, "XAUUSDm", "SELL", 0.02, 3734.24, 3738.03, 3718.0, "XAUUSD")
        msg, _ = app._telegram_queue.get_nowait()
        self.assertNotIn("AI", msg.split("\n")[1])

    def test_tin_dong_lenh(self):
        app = make_app(_get_today_deals_stats=mock.Mock(return_value=(99.0, 0, None)))
        cd = {"category": "WIN", "net_pnl": 0.70, "r_ratio_str": "+0.08R", "duration": "10s", "status": "ĐÃ CHỐT TP (13:10:11)",
              "open_price": 3733.14, "close_price": 3733.49, "symbol": "XAUUSDm", "type": "BUY", "ticket": 4182479748}
        app._telegram_notify_order_closed(cd, day_pnl=4.31)
        msg, markup = app._telegram_queue.get_nowait()
        self.assertEqual(msg, "🟢 VÀNG BUY +0.70$ (+0.08R) · CHỐT TP · 13:10:11\n"
                              "3733.14 → 3733.49 · giữ 10s · Ngày +4.31$ · #4182479748")
        self.assertIsNone(markup)
        app._get_today_deals_stats.assert_not_called()

    def test_tin_dong_lenh_ly_do_khong_co_gio(self):
        app = make_app()
        cd = {"category": "LOSS", "net_pnl": -7.58, "status": "Đã đóng", "open_price": 1, "close_price": 2,
              "symbol": "XAUUSDm", "type": "SELL", "ticket": 9}
        app._telegram_notify_order_closed(cd, day_pnl=-7.58)
        msg, _ = app._telegram_queue.get_nowait()
        self.assertTrue(msg.startswith("🔴 VÀNG SELL -7.58$ · Đã đóng\n"))


class TestPlainTextAndChart(unittest.TestCase):
    """19/11/2026: tin Telegram chỉ chữ (giữ chấm 🟢/🔴) + nút Phân tích AI gửi kèm ảnh biểu đồ."""

    def test_bo_icon_giu_cham_xanh_do(self):
        f = b.KronosDesktopApp._tg_plain_text
        self.assertEqual(f("🧠 VÀNG · AI M5 lúc 19:00:03 · AUTO BẬT"), "VÀNG · AI M5 lúc 19:00:03 · AUTO BẬT")
        self.assertEqual(f("🟢 TÍN HIỆU [XAUUSD]: BUY"), "🟢 TÍN HIỆU [XAUUSD]: BUY")
        self.assertEqual(f("🔴 VÀNG SELL -7.58$"), "🔴 VÀNG SELL -7.58$")
        self.assertEqual(f("⏳ Đang canh vùng BUY ≤ 3718.79"), "Đang canh vùng BUY ≤ 3718.79")
        self.assertEqual(f("🟡 VÀNG: ⏸ DỪNG"), "VÀNG: DỪNG")
        self.assertEqual(f("🟠 BTC: ▶️ BẬT"), "BTC: BẬT")
        self.assertEqual(f("⏸️ TẠM DỪNG"), "TẠM DỪNG")
        self.assertEqual(f("3733.14 → 3733.49 · giữ 10s"), "3733.14 → 3733.49 · giữ 10s")
        self.assertEqual(f("📊 [X] BÁO CÁO\n   • Lãi ngày: +1$\n────"), "[X] BÁO CÁO\n   • Lãi ngày: +1$\n────")
        self.assertEqual(f("🧠"), "🧠")  # bỏ xong rỗng -> giữ nguyên

    def test_payload_api_bo_icon_ca_nut(self):
        app = make_app()
        del app.__dict__["_telegram_api_call"]
        sent = {}

        class _Resp:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return b'{"ok": true}'

        def fake_open(req, context=None, timeout=None):
            import json
            sent.update(json.loads(req.data.decode("utf-8")))
            return _Resp()
        data = {"chat_id": "1", "text": "🧠 VÀNG\n🟢 TÍN HIỆU",
                "reply_markup": {"inline_keyboard": [[{"text": "📊 Báo cáo", "callback_data": "tg_stats"}]]}}
        with mock.patch.object(b.urllib.request, "urlopen", side_effect=fake_open):
            app._telegram_api_call("T", "sendMessage", data)
        self.assertEqual(sent["text"], "VÀNG\n🟢 TÍN HIỆU")
        self.assertEqual(sent["reply_markup"]["inline_keyboard"][0][0]["text"], "Báo cáo")
        self.assertEqual(data["text"], "🧠 VÀNG\n🟢 TÍN HIỆU")  # không sửa dict gốc

    def test_nut_ai_gui_1_tin_anh_kem_chu_va_nut(self):
        app = make_app(_tg_capture_chart_png=mock.Mock(return_value=b"PNG"), active_profile_key="XAUUSD",
                       profile_ctx={"XAUUSD": {"auto_loop_running": True}, "BTCUSD": {}},
                       _telegram_send_photo=mock.Mock(return_value={"ok": True, "result": {"message_id": 50}}),
                       _telegram_send_with_keyboard=mock.Mock(),
                       _tg_ai_brief=mock.Mock(return_value="R"))
        app._handle_telegram_callback({"id": "cq1", "data": "tg_ai", "message": {"message_id": 7}}, "1", "T")
        args, kw = app._telegram_send_photo.call_args
        self.assertEqual(args, ("T", "1", b"PNG", "R\nBTC · AUTO TẮT"))  # mã tắt: chỉ 1 dòng, không ảnh
        self.assertIn("inline_keyboard", kw["reply_markup"])
        app._telegram_send_with_keyboard.assert_not_called()
        self.assertEqual(app._tg_last_kb_msg_id, 50)

    def test_gui_anh_loi_thi_gui_chu(self):
        order = []
        app = make_app(_tg_capture_chart_png=mock.Mock(return_value=b"PNG"), active_profile_key="XAUUSD",
                       profile_ctx={"XAUUSD": {"auto_loop_running": True}, "BTCUSD": {}},
                       _telegram_send_photo=mock.Mock(side_effect=lambda *a, **k: order.append("photo")),
                       _telegram_send_with_keyboard=mock.Mock(side_effect=lambda *a, **k: order.append("text")),
                       _tg_ai_brief=mock.Mock(return_value="R"))
        app._telegram_send_ai_report("T", "1")
        self.assertEqual(order, ["photo", "text"])

    def test_2_ma_bat_moi_ma_1_anh_nut_o_tin_cuoi(self):
        app = make_app(_tg_capture_chart_png=mock.Mock(return_value=b"XAU"), active_profile_key="XAUUSD",
                       _tg_render_profile_png=mock.Mock(return_value=b"BTC"),
                       profile_ctx={"XAUUSD": {"auto_loop_running": True}, "BTCUSD": {"auto_loop_running": True}},
                       _telegram_send_photo=mock.Mock(return_value={"ok": True, "result": {"message_id": 9}}),
                       _telegram_send_with_keyboard=mock.Mock(),
                       _tg_ai_brief=mock.Mock(side_effect=lambda pk: f"cap {pk}"))
        app._telegram_send_ai_report("T", "1")
        calls = app._telegram_send_photo.call_args_list
        self.assertEqual([c[0][2:] for c in calls], [(b"XAU", "cap XAUUSD"), (b"BTC", "cap BTCUSD")])
        self.assertIsNone(calls[0][1]["reply_markup"])  # nút chỉ ở tin cuối
        self.assertIn("inline_keyboard", calls[1][1]["reply_markup"])
        app._tg_render_profile_png.assert_called_once_with("BTCUSD")  # mã chạy ngầm: vẽ ảnh riêng

    def test_ca_2_ma_tat_chi_gui_chu(self):
        app = make_app(_tg_capture_chart_png=mock.Mock(return_value=b"PNG"), active_profile_key="XAUUSD",
                       _telegram_send_photo=mock.Mock(), _telegram_send_with_keyboard=mock.Mock())
        app._telegram_send_ai_report("T", "1")
        app._telegram_send_photo.assert_not_called()
        app._telegram_send_with_keyboard.assert_called_once_with("T", "1", "VÀNG · AUTO TẮT\nBTC · AUTO TẮT")

    def test_chu_gon_2_dong(self):
        pend = {"zone_watch": True, "action": "BUY", "symbol": "BTCUSDm"}
        ctx = {"auto_loop_running": True, "_tg_ai_info": {"ts": "08:14:05", "tf": "M5"}, "cached_action": "BUY",
               "cached_consensus": "81", "pending_pullback_order": pend}
        pos = types.SimpleNamespace(type=b.mt5.POSITION_TYPE_BUY, profit=12.3)
        app = make_app(profile_ctx={"XAUUSD": {}, "BTCUSD": ctx}, active_profile_key="XAUUSD",
                       _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk: False,
                       _zone_entry_limit=lambda p, pk: {"line": 58931.57, "ok": True},
                       _get_profile_cfg=lambda pk: {"symbol": "BTCUSDm"},
                       _get_bot_positions=lambda s, profile_key=None: [pos],
                       _get_today_closed_deals=lambda s, profile_key=None: [{"net_pnl": -9.72}, {"net_pnl": -6.51}, {"net_pnl": 5.76}])
        self.assertEqual(app._tg_ai_brief("BTCUSD"),
                         "BTC 08:14 · AUTO BẬT · BUY 81% · canh <= 58931.57\nLệnh: 1 BUY +12.30$ · Hôm nay: -10.47$ (3 lệnh)")
        app._get_bot_positions = lambda s, profile_key=None: []
        app._get_today_closed_deals = lambda s, profile_key=None: []
        ctx.update(cached_action=None, cached_consensus="73", pending_pullback_order=None)
        self.assertEqual(app._tg_ai_brief("BTCUSD"), "BTC 08:14 · AUTO BẬT · ĐỨNG NGOÀI\nLệnh: không có · Hôm nay: chưa có lệnh")

    def test_ve_anh_gon_cho_ma_chay_ngam(self):
        import pandas as pd
        from trading_modules.tg_chart import render_chart_png
        df = pd.DataFrame({"timestamps": pd.date_range("2026-11-21 00:00", periods=60, freq="min"),
                           "open": [100 + i * 0.1 for i in range(60)], "high": [100.5 + i * 0.1 for i in range(60)],
                           "low": [99.5 + i * 0.1 for i in range(60)], "close": [100.2 + i * 0.1 for i in range(60)]})
        png = render_chart_png(df, "BTC BTCUSDm (M1)", [{"price": 101.0, "color": "#ffd54f", "label": "Vùng canh BUY <= 101.00"}],
                               last_price=106.0)
        self.assertEqual(png[1:4], b"PNG")

    def test_chup_khung_scalp_roi_tra_lai_khung_cu(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(figsize=(3, 2), dpi=50)
        FigureCanvasAgg(fig)
        fig.add_subplot(111).plot([1, 2])
        seen = []
        views = {"XAUUSD": "M5"}
        app = make_app(fig=fig, active_profile_key="XAUUSD", chart_view_tf_by_profile=views,
                       tf_var=_Var("M1"), user_zoomed=True, saved_xlim=(1, 2), saved_ylim=(3, 4),
                       _request_canvas_redraw=lambda full=True: None)
        app._refresh_realtime_chart = lambda force_full_redraw=False: seen.append((views["XAUUSD"], app.user_zoomed))
        png = app._tg_capture_chart_png()
        self.assertEqual(png[1:4], b"PNG")
        self.assertEqual(seen, [("M1", False), ("M5", True)])  # vẽ M1 để chụp, rồi trả lại M5 + thu phóng cũ
        self.assertEqual((views["XAUUSD"], app.saved_xlim, app.saved_ylim), ("M5", (1, 2), (3, 4)))

    def test_chup_loi_van_gui_chu(self):
        app = make_app(_tg_capture_chart_png=mock.Mock(side_effect=RuntimeError("x")), _telegram_send_photo=mock.Mock(),
                       active_profile_key="XAUUSD", profile_ctx={"XAUUSD": {"auto_loop_running": True}, "BTCUSD": {}},
                       _telegram_send_with_keyboard=mock.Mock(), _tg_ai_brief=mock.Mock(return_value="R"))
        app._telegram_send_ai_report("T", "1")
        app._telegram_send_photo.assert_not_called()
        app._telegram_send_with_keyboard.assert_called_once_with("T", "1", "R\nBTC · AUTO TẮT")

    def test_chup_bieu_do_that_tu_luong_nen(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(figsize=(3, 2), dpi=50)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        ax.plot([1, 2, 3])
        vline = ax.axvline(1.5)
        calls = []

        def after(ms, fn=None):  # giả luồng Tk chính: chạy ở luồng khác
            threading.Thread(target=fn).start()
        app = make_app(root=types.SimpleNamespace(after=after), fig=fig, cross_vline=vline,
                       _request_canvas_redraw=lambda full=True: calls.append(full))
        out = []
        t = threading.Thread(target=lambda: out.append(app._tg_capture_chart_png(timeout=10)))
        t.start()
        t.join(15)
        self.assertEqual(out[0][1:4], b"PNG")
        self.assertTrue(vline.get_visible())  # khôi phục chữ thập
        self.assertEqual(calls, [True])

    def test_gui_anh_multipart(self):
        app = make_app()
        seen = {}

        class _Resp:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return b'{"ok": true}'

        def fake_open(req, context=None, timeout=None):
            seen["url"] = req.full_url
            seen["ct"] = req.get_header("Content-type")
            seen["body"] = req.data
            return _Resp()
        with mock.patch.object(b.urllib.request, "urlopen", side_effect=fake_open):
            res = app._telegram_send_photo("T", "1", b"PNGDATA", "🧠 Biểu đồ VÀNG")
        self.assertEqual(res, {"ok": True})
        self.assertTrue(seen["url"].endswith("/sendPhoto"))
        self.assertIn("multipart/form-data; boundary=", seen["ct"])
        self.assertIn(b"PNGDATA", seen["body"])
        self.assertIn("Biểu đồ VÀNG".encode("utf-8"), seen["body"])
        self.assertNotIn("🧠".encode("utf-8"), seen["body"])


class TestFreshClosedDeals(unittest.TestCase):
    def test_doc_nhanh_khi_lenh_bien_mat(self):
        fresh = [{"ticket": 111, "net_pnl": -7.58}]
        app = make_app(_get_closed_deals_by_range=mock.Mock(return_value=fresh))
        pos = [types.SimpleNamespace(ticket=111)]
        clock = {"t": 1000.0}
        with mock.patch.object(b.time, "monotonic", side_effect=lambda: clock["t"]):
            self.assertEqual(app._telegram_fresh_closed_deals("XAUUSD", "XAUUSDm", pos, []), [])
            app._get_closed_deals_by_range.assert_not_called()
            # lệnh biến mất, danh sách đã đóng (bộ đệm) chưa có -> đọc mới, không ghi bộ đệm dùng chung
            clock["t"] = 1001.0
            self.assertEqual(app._telegram_fresh_closed_deals("XAUUSD", "XAUUSDm", [], []), fresh)
            kw = app._get_closed_deals_by_range.call_args[1]
            self.assertEqual((kw["use_cache"], kw["store_cache"], kw["profile_key"]), (False, False, "XAUUSD"))
            # cùng giây: không đọc lại
            clock["t"] = 1001.5
            self.assertEqual(app._telegram_fresh_closed_deals("XAUUSD", "XAUUSDm", [], []), [])
            self.assertEqual(app._get_closed_deals_by_range.call_count, 1)
            # đã gửi tin -> thôi chờ
            app._telegram_sent_deal_tickets.add(111)
            clock["t"] = 1003.0
            self.assertEqual(app._telegram_fresh_closed_deals("XAUUSD", "XAUUSDm", [], []), [])
            self.assertEqual(app._get_closed_deals_by_range.call_count, 1)

    def test_het_20_giay_thi_thoi(self):
        app = make_app(_get_closed_deals_by_range=mock.Mock(return_value=[]))
        clock = {"t": 0.0}
        with mock.patch.object(b.time, "monotonic", side_effect=lambda: clock["t"]):
            app._telegram_fresh_closed_deals("XAUUSD", "S", [types.SimpleNamespace(ticket=5)], [])
            clock["t"] = 1.0
            app._telegram_fresh_closed_deals("XAUUSD", "S", [], [])
            clock["t"] = 30.0
            app._telegram_fresh_closed_deals("XAUUSD", "S", [], [])
        self.assertEqual(app._get_closed_deals_by_range.call_count, 1)


class TestStoreCacheFlag(unittest.TestCase):
    def _app(self):
        return make_app(_get_profile_magic_number=lambda pk: 1, _resolve_profile_by_symbol=lambda s: "XAUUSD",
                        _mt5_lock=threading.RLock(), _cached_closed_deals_map={})

    def test_khong_ghi_bo_dem_khi_store_cache_false(self):
        fake_mt5 = types.SimpleNamespace(history_deals_get=lambda *a, **k: (), history_orders_get=lambda *a, **k: ())
        with mock.patch.object(b, "MT5_AVAILABLE", True), mock.patch.object(b, "mt5", fake_mt5):
            app = self._app()
            app._get_closed_deals_by_range(symbol="XAUUSDm", use_cache=False, profile_key="XAUUSD", store_cache=False)
            self.assertEqual(app._cached_closed_deals_map, {})
            app._get_closed_deals_by_range(symbol="XAUUSDm", use_cache=False, profile_key="XAUUSD")
            self.assertEqual(len(app._cached_closed_deals_map), 1)


if __name__ == "__main__":
    unittest.main()
