"""Test P47: log chẩn đoán canh vùng (_pb_gate_log, _pb_heartbeat, _pb_note_exception) và thứ tự cổng không đổi.
Toàn bộ dùng mock, không gọi MT5/mạng."""
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

PK = "XAUUSD"
SYM = "XAUUSDm"
TOLS = {"sl_break_tol": 0.25, "max_sl_tol": 0.05, "eps_equal": 0.05}


def tk(bid, spread=0.20):
    return SimpleNamespace(bid=bid, ask=round(bid + spread, 2))


def make_app(cfg_over=None, positions=None):
    cfg = {"max_sl_price": 4.0, "max_spread": 0.35, "lot": 0.01, "max_pos": 3, "rr": 0.0, "confirm_entry_pa": True, "tf": "M5"}
    cfg.update(cfg_over or {})
    p = {"symbol": SYM, "action": "SELL", "sl_price": 3888.86, "effective_max_sl": 2.0, "sl_tightened_for_2r": True,
         "target_tp_price": 3878.49, "zone_watch": True, "atr": 1.5, "manual_queued": False}
    app = object.__new__(b.KronosDesktopApp)
    app.__dict__.update(
        _order_in_flight={}, active_profile_key=PK, profile_ctx={PK: {"pending_pullback_order": p}},
        pending_pullback_order=p, auto_loop_running=True, cached_tp=None, _last_order_candle_ts={},
        _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk=None, action=None: False,
        _get_profile_cfg=lambda pk: dict(cfg), _get_symbol_tolerances=lambda s, t=None: dict(TOLS),
        _get_bot_positions=lambda s, profile_key=None: list(positions or []), _is_pos_at_be=lambda pos, eps=None: False,
        execute_mt5_order=mock.Mock(return_value=(True, "ok")),
        _check_price_action_entry_confirmation=mock.Mock(return_value=(True, "ok")),
        log=mock.Mock(),
    )
    return app


def diag(app, gate=None):
    out = [c.args[0] for c in app.log.call_args_list if c.args and str(c.args[0]).startswith("🔎")]
    return [x for x in out if gate is None or f"cổng={gate}" in x]


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        for p in (mock.patch.object(b, "mt5", SimpleNamespace(POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1)),
                  mock.patch.object(b, "is_trading_window_blocked", return_value=(False, "")),
                  mock.patch.object(b.time, "monotonic", side_effect=lambda: self.clock[0])):
            p.start()
            self.addCleanup(p.stop)


class TestGateLog(Base):
    def test_spread_log_moi_10s(self):
        app = make_app({"max_spread": 0.10})  # spread 0.20 > 0.10, giá trong vùng (cách SL 1.96)
        for t in (100.0, 105.0, 109.9, 110.1, 115.0):
            self.clock[0] = t
            app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        lines = diag(app, "spread")
        self.assertEqual(len(lines), 2)  # t=100 và t=110.1
        self.assertIn("spread=0.20", lines[0])
        self.assertIn("max_spread=0.10", lines[0])
        self.assertIn("Max SL hiệu dụng=2.00", lines[0])
        app.execute_mt5_order.assert_not_called()

    def test_xa_vung_khong_log(self):
        app = make_app({"max_spread": 0.10})
        for t in (100.0, 111.0, 122.0):
            self.clock[0] = t
            app._check_pending_pullback_order(SYM, tk(3880.00), profile_key=PK)  # cách SL 8.86
        self.assertEqual(diag(app), [])

    def test_gan_vung_ngoai_chua_vao(self):
        app = make_app()
        app._check_pending_pullback_order(SYM, tk(3885.90), profile_key=PK)  # cách SL 2.96: ngoài vùng 2.05 nhưng <= 3.05
        self.assertEqual(len(diag(app, "chua_vao_vung")), 1)
        app.execute_mt5_order.assert_not_called()

    def test_gate_names(self):
        # max_pos
        pos = [SimpleNamespace(type=0, profit=1.0)]
        app = make_app({"max_pos": 1}, positions=pos)
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "max_pos_hoac_lenh_nguoc")), 1)
        self.assertIn("lệnh mở=1", diag(app)[0])
        # auto off
        app = make_app()
        app.auto_loop_running = False
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "auto_off")), 1)
        # order_in_flight
        app = make_app()
        app._order_in_flight[PK] = True
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "order_in_flight")), 1)
        # daily/cooldown (pending bị xóa như cũ)
        app = make_app()
        app._is_cooldown_active = lambda pk=None, action=None: True
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "daily_limit_cooldown")), 1)
        self.assertIsNone(app.profile_ctx[PK]["pending_pullback_order"])
        # price_action
        app = make_app()
        app._check_price_action_entry_confirmation = mock.Mock(return_value=(False, "chua du"))
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "price_action")), 1)
        app.execute_mt5_order.assert_not_called()
        # execute thất bại
        app = make_app()
        app.execute_mt5_order = mock.Mock(return_value=(False, "bi tu choi"))
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "execute")), 1)

    def test_rr_thuc(self):
        app = make_app()
        app.profile_ctx[PK]["pending_pullback_order"]["target_tp_price"] = 3886.00  # TP quá gần -> RR < 2
        app._check_pending_pullback_order(SYM, tk(3887.60), profile_key=PK)
        self.assertEqual(len(diag(app, "rr_thuc")), 1)
        app.execute_mt5_order.assert_not_called()

    def test_huy_do_cham_sl_co_bid_truoc_va_ms(self):
        app = make_app()
        self.clock[0] = 100.0
        app._check_pending_pullback_order(SYM, tk(3884.00), profile_key=PK)
        self.clock[0] = 100.8
        app._check_pending_pullback_order(SYM, tk(3892.44), profile_key=PK)  # nhảy qua cả vùng và SL
        txt = [c.args[0] for c in app.log.call_args_list if "Hủy Lệnh Chờ Hồi" in str(c.args[0])]
        self.assertEqual(len(txt), 1)
        self.assertIn("bid lần kiểm tra trước=3884.00", txt[0])
        self.assertIn("800ms", txt[0])
        self.assertIsNone(app.profile_ctx[PK]["pending_pullback_order"])


class TestOrderUnchanged(Base):
    def test_thu_tu_cong_va_hanh_vi_vao_lenh(self):
        # Vừa spread cao vừa đủ max_pos -> cổng max_pos chặn trước, spread/PA/execute không được chạm tới
        pos = [SimpleNamespace(type=0, profit=1.0)]
        app = make_app({"max_pos": 1, "max_spread": 0.10}, positions=pos)
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "max_pos_hoac_lenh_nguoc")), 1)
        self.assertEqual(diag(app, "spread"), [])
        app._check_price_action_entry_confirmation.assert_not_called()
        app.execute_mt5_order.assert_not_called()
        # Spread cao -> PA không được gọi
        app = make_app({"max_spread": 0.10})
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        app._check_price_action_entry_confirmation.assert_not_called()
        app.execute_mt5_order.assert_not_called()
        # Mọi cổng qua -> vào lệnh đúng 1 lần với SL/TP đã lưu
        app = make_app()
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        app.execute_mt5_order.assert_called_once_with(SYM, "SELL", 0.01, 3888.86, 3878.49, 3, profile_key=PK, sl_halved=True)
        self.assertEqual(diag(app), [])


class TestHeartbeat(Base):
    def test_heartbeat_60s_dem_M_K(self):
        app = make_app()
        for t in range(0, 60, 1):   # 60 lần kiểm tra (t=0..59), chưa đủ 60s
            self.clock[0] = float(t)
            app._pb_heartbeat(PK, SYM, tk(3884.00))
        self.assertEqual(diag(app), [])
        app.__dict__["_pb_cnt"]["busy"] = 3
        app._pb_note_exception(ValueError("loi gia"))
        app._pb_note_exception(ValueError("loi gia"))
        self.clock[0] = 60.5
        app._pb_heartbeat(PK, SYM, tk(3884.00))
        hb = [x for x in diag(app) if "đang canh" in x]
        self.assertEqual(len(hb), 1)
        self.assertIn("lần kiểm tra trong 60s=61", hb[0])
        self.assertIn("lần bị bỏ qua do khóa MT5 bận=3", hb[0])
        self.assertIn("ngoại lệ vòng tick=2", hb[0])
        self.assertIn("vùng kích hoạt=[3886.81, 3888.61]", hb[0])
        # 30s sau chưa tới nhịp kế
        self.clock[0] = 90.0
        app._pb_heartbeat(PK, SYM, tk(3884.00))
        self.assertEqual(len([x for x in diag(app) if "đang canh" in x]), 1)
        self.clock[0] = 121.0
        app._pb_heartbeat(PK, SYM, tk(3884.00))
        self.assertEqual(len([x for x in diag(app) if "đang canh" in x]), 2)

    def test_khong_pending_khong_heartbeat(self):
        app = make_app()
        app.profile_ctx[PK]["pending_pullback_order"] = None
        app.pending_pullback_order = None
        for t in (0.0, 61.0, 122.0):
            self.clock[0] = t
            app._pb_heartbeat(PK, SYM, tk(3884.00))
        self.assertEqual(diag(app), [])

    def test_exception_log_toi_da_1_lan_30s(self):
        app = make_app()
        self.clock[0] = 100.0
        app._pb_note_exception(RuntimeError("a"))
        self.clock[0] = 110.0
        app._pb_note_exception(RuntimeError("b"))
        self.clock[0] = 131.0
        app._pb_note_exception(RuntimeError("c"))
        self.assertEqual(len(diag(app)), 2)
        self.assertEqual(app.__dict__["_pb_cnt"]["exc"], 3)


if __name__ == "__main__":
    unittest.main()
