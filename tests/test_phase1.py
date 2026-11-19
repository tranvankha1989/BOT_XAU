"""Test hồi quy cho các sửa của Giai đoạn 1 (không gọi MT5 thật, toàn bộ dùng mock)."""
import os
import sys
import threading
import types
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

PK = "XAUUSD"
SYM = "XAUUSDm"
MAGIC = 777


def fake_mt5():
    """mt5 giả với hằng số thật để getattr(mt5, 'X', default) trả đúng giá trị."""
    return types.SimpleNamespace(
        TRADE_ACTION_DEAL=1, TRADE_ACTION_SLTP=2,
        TRADE_RETCODE_REQUOTE=10004, TRADE_RETCODE_BUSY=10007, TRADE_RETCODE_PRICE_CHANGED=10020,
        TRADE_RETCODE_PRICE_OFF=10021, TRADE_RETCODE_TIMEOUT=10012, TRADE_RETCODE_CONNECTION=10031,
        TRADE_RETCODE_INVALID_FILL=10030, TRADE_RETCODE_DONE=10009, TRADE_RETCODE_DONE_PARTIAL=10010,
        TRADE_RETCODE_PLACED=10008, TRADE_RETCODE_NO_CHANGES=10025, TRADE_RETCODE_MARKET_CLOSED=10017,
        ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1, ORDER_FILLING_RETURN=2,
        ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, ORDER_TIME_GTC=0,
        POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1, DEAL_ENTRY_IN=0,
        positions_get=mock.Mock(), last_error=mock.Mock(return_value=(1, "Success")),
        order_send=mock.Mock(), order_check=mock.Mock(return_value=SimpleNamespace(retcode=0, comment="")),
        symbol_info=mock.Mock(return_value=SimpleNamespace(
            digits=2, filling_mode=2, volume_min=0.01, volume_max=100.0, volume_step=0.01, point=0.01)),
        symbol_info_tick=mock.Mock(return_value=SimpleNamespace(ask=2000.3, bid=2000.0)),
        symbol_select=mock.Mock(return_value=True), initialize=mock.Mock(return_value=True),
        history_deals_get=mock.Mock(),
    )


def make_app(**over):
    """KronosDesktopApp không chạy __init__ (không cần Tk), gắn thuộc tính tối thiểu."""
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    app._write_file_log = mock.Mock()
    app.log_error = mock.Mock()
    app.log = mock.Mock()
    app._last_mt5_order_mono = 0.0
    app.__dict__.update(over)
    return app


def pos(ticket, magic=MAGIC, ptype=0, volume=0.01):
    return SimpleNamespace(ticket=ticket, magic=magic, type=ptype, volume=volume, price_open=2000.0, sl=0.0, tp=0.0)


class Base(unittest.TestCase):
    def setUp(self):
        self.m = fake_mt5()
        for p in (mock.patch.object(b, "mt5", self.m), mock.patch.object(b, "MT5_AVAILABLE", True),
                  mock.patch.object(b.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)


# ---------------------------------------------------------------- 1
class TestPositionsGetChecked(Base):
    def test_returns_list_when_positions(self):
        self.m.positions_get.return_value = (pos(1), pos(2))
        r = make_app()._positions_get_checked(symbol=SYM)
        self.assertEqual([p.ticket for p in r], [1, 2])
        self.m.positions_get.assert_called_once_with(symbol=SYM)

    def test_none_with_success_error_is_empty_list(self):
        self.m.positions_get.return_value = None
        self.m.last_error.return_value = (1, "Success")
        self.assertEqual(make_app()._positions_get_checked(symbol=SYM), [])

    def test_none_with_real_error_is_none(self):
        self.m.positions_get.return_value = None
        self.m.last_error.return_value = (-1, "fail")
        app = make_app()
        self.assertIsNone(app._positions_get_checked(symbol=SYM))
        app._write_file_log.assert_called_once()

    def test_exception_is_none(self):
        self.m.positions_get.side_effect = RuntimeError("boom")
        app = make_app()
        self.assertIsNone(app._positions_get_checked(symbol=SYM))
        app._write_file_log.assert_called_once()


# ---------------------------------------------------------------- 2
class TestGetBotPositions(Base):
    def app(self):
        return make_app(_get_profile_cfg=lambda pk: {"magic_number": MAGIC})

    def test_filters_by_profile_magic(self):
        self.m.positions_get.return_value = (pos(1, MAGIC), pos(2, 123), pos(3, 0), pos(4, MAGIC))
        r = self.app()._get_bot_positions(SYM, profile_key=PK)
        self.assertEqual([p.ticket for p in r], [1, 4])
        self.m.positions_get.assert_called_once_with(symbol=SYM)  # luôn truyền symbol

    def test_empty_symbol_does_not_call_mt5(self):
        app = self.app()
        for s in ("", "   ", None):
            self.assertEqual(app._get_bot_positions(s, profile_key=PK), [])
        self.m.positions_get.assert_not_called()

    def test_mt5_error_returns_empty(self):
        self.m.positions_get.return_value = None
        self.m.last_error.return_value = (-1, "fail")
        self.assertEqual(self.app()._get_bot_positions(SYM, profile_key=PK), [])


# ---------------------------------------------------------------- 3
class TestExecuteOrder(Base):
    def app(self):
        res = SimpleNamespace(price=2000.3, order=55, deal=0)
        return make_app(
            _order_guard_lock=threading.Lock(), _order_in_flight={PK: False}, _last_order_open_mono={PK: -1e9},
            _last_order_candle_ts={}, active_profile_key=PK, profile_ctx={PK: {}},
            _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk=None, action=None: False,
            _get_profile_cfg=lambda pk: {"max_sl_price": 5.0, "magic_number": MAGIC},
            _get_symbol_tolerances=lambda *a, **k: {"eps_equal": 0.05, "min_stop_dist": 0.5, "max_sl_tol": 0.0},
            _is_pos_at_be=lambda p, e=None: False,
            _send_mt5_order_with_retry=mock.Mock(return_value=(True, res, "")),
            _telegram_notify_order_opened=mock.Mock())

    def test_none_with_success_error_still_opens_first_order(self):
        self.m.positions_get.return_value = None
        self.m.last_error.return_value = (1, "Success")
        app = self.app()
        ok, msg = app.execute_mt5_order(SYM, "BUY", 0.01, 1998.0, 2005.0, 3, profile_key=PK)
        self.assertTrue(ok, msg)
        app._send_mt5_order_with_retry.assert_called_once()

    def test_none_with_real_error_rejects(self):
        self.m.positions_get.return_value = None
        self.m.last_error.return_value = (-1, "fail")
        app = self.app()
        ok, msg = app.execute_mt5_order(SYM, "BUY", 0.01, 1998.0, 2005.0, 3, profile_key=PK)
        self.assertFalse(ok)
        self.assertIn("Không đọc được danh sách lệnh", msg)
        app._send_mt5_order_with_retry.assert_not_called()
        self.assertFalse(app._order_in_flight[PK])  # khóa in-flight đã được nhả

    def test_lot_normalized_to_step(self):
        self.m.positions_get.return_value = ()
        app = self.app()
        ok, msg = app.execute_mt5_order(SYM, "BUY", 0.037, 1998.0, 2005.0, 3, profile_key=PK)
        self.assertTrue(ok, msg)
        req = app._send_mt5_order_with_retry.call_args[0][0]
        self.assertAlmostEqual(req["volume"], 0.03)
        self.assertEqual(req["magic"], MAGIC)

    def test_sl_wrong_side_rejected(self):
        self.m.positions_get.return_value = ()
        for action, sl in (("BUY", 2001.0), ("SELL", 1999.0)):
            app = self.app()
            ok, msg = app.execute_mt5_order(SYM, action, 0.01, sl, 1990.0 if action == "SELL" else 2010.0, 3, profile_key=PK)
            self.assertFalse(ok, action)
            self.assertIn("sai phía", msg)
            app._send_mt5_order_with_retry.assert_not_called()


# ---------------------------------------------------------------- 4
class TestSendRetry(Base):
    TIMEOUT = 10012

    def app(self):
        return make_app()

    def new_req(self):
        return {"action": 1, "symbol": SYM, "volume": 0.01, "type": 0, "price": 2000.3, "magic": MAGIC}

    def timeout(self):
        return SimpleNamespace(retcode=self.TIMEOUT, comment="timeout", order=0)

    def test_a_timeout_with_new_position_is_success(self):
        self.m.positions_get.side_effect = [(), (pos(5),)]
        self.m.order_send.return_value = self.timeout()
        ok, res, err = self.app()._send_mt5_order_with_retry(self.new_req(), SYM, "OPEN")
        self.assertTrue(ok)
        self.assertEqual(res.order, 5)
        self.assertNotEqual(res.order, 0)
        self.assertEqual(self.m.order_send.call_count, 1)

    def test_b_timeout_without_new_position_fails_no_resend(self):
        self.m.positions_get.side_effect = [(), ()]
        self.m.order_send.return_value = self.timeout()
        ok, res, err = self.app()._send_mt5_order_with_retry(self.new_req(), SYM, "OPEN")
        self.assertFalse(ok)
        self.assertEqual(self.m.order_send.call_count, 1)

    def test_c_identical_old_position_not_mistaken(self):
        old = pos(7)  # cùng magic/type/volume nhưng đã tồn tại trước khi gửi
        self.m.positions_get.side_effect = [(old,), (pos(7),)]
        self.m.order_send.return_value = self.timeout()
        ok, res, err = self.app()._send_mt5_order_with_retry(self.new_req(), SYM, "OPEN")
        self.assertFalse(ok)
        self.assertEqual(self.m.order_send.call_count, 1)

    def test_d_snapshot_error_then_timeout_fails_no_resend(self):
        self.m.positions_get.side_effect = [None, (pos(5),)]  # snapshot lỗi; nếu có gọi lần 2 sẽ "thấy" lệnh mới
        self.m.last_error.return_value = (-1, "fail")
        self.m.order_send.return_value = self.timeout()
        ok, res, err = self.app()._send_mt5_order_with_retry(self.new_req(), SYM, "OPEN")
        self.assertFalse(ok)
        self.assertEqual(self.m.order_send.call_count, 1)
        self.assertEqual(self.m.positions_get.call_count, 1)  # không dùng kết quả không đáng tin sau timeout

    def close_req(self):
        return {"action": 1, "symbol": SYM, "position": 99, "volume": 0.01, "type": 1, "price": 2000.0, "magic": MAGIC}

    def test_e1_close_empty_list_means_already_closed_no_send(self):
        self.m.positions_get.return_value = ()
        ok, res, err = self.app()._send_mt5_order_with_retry(self.close_req(), SYM, "CLOSE")
        self.assertTrue(ok)
        self.m.order_send.assert_not_called()

    def test_e2_close_mt5_error_still_sends(self):
        self.m.positions_get.return_value = None
        self.m.last_error.return_value = (-1, "fail")
        self.m.order_send.return_value = SimpleNamespace(retcode=10009, comment="done", order=1)
        ok, res, err = self.app()._send_mt5_order_with_retry(self.close_req(), SYM, "CLOSE")
        self.assertTrue(ok)
        self.assertEqual(self.m.order_send.call_count, 1)


# ---------------------------------------------------------------- 5
class TestResolvePositionMagic(Base):
    def deal(self, magic):
        return SimpleNamespace(entry=0, magic=magic)

    def test_manual_magic_zero_is_cached(self):
        self.m.history_deals_get.return_value = [self.deal(0)]
        app = make_app()
        self.assertEqual(app._resolve_position_magic(5), 0)
        self.assertEqual(app._resolve_position_magic(5), 0)
        self.assertEqual(self.m.history_deals_get.call_count, 1)

    def test_mt5_error_returns_none_with_30s_backoff(self):
        self.m.history_deals_get.return_value = None
        app = make_app()
        with mock.patch.object(b.time, "monotonic", return_value=100.0):
            self.assertIsNone(app._resolve_position_magic(5))
        self.assertEqual(self.m.history_deals_get.call_count, 1)
        with mock.patch.object(b.time, "monotonic", return_value=129.0):  # còn trong backoff
            self.assertIsNone(app._resolve_position_magic(5))
        self.assertEqual(self.m.history_deals_get.call_count, 1)
        self.m.history_deals_get.return_value = [self.deal(MAGIC)]
        with mock.patch.object(b.time, "monotonic", return_value=131.0):  # hết backoff
            self.assertEqual(app._resolve_position_magic(5), MAGIC)
        self.assertEqual(self.m.history_deals_get.call_count, 2)

    def test_exception_returns_none_with_backoff(self):
        self.m.history_deals_get.side_effect = RuntimeError("boom")
        app = make_app()
        with mock.patch.object(b.time, "monotonic", return_value=100.0):
            self.assertIsNone(app._resolve_position_magic(5))
            self.assertIsNone(app._resolve_position_magic(5))
        self.assertEqual(self.m.history_deals_get.call_count, 1)

    def test_entry_by_pos_used_without_calling_mt5(self):
        app = make_app()
        self.assertEqual(app._resolve_position_magic(5, {5: self.deal(MAGIC)}), MAGIC)
        self.m.history_deals_get.assert_not_called()


# ---------------------------------------------------------------- 6
class TestDailyLimits(unittest.TestCase):
    def build(self):
        ctx = {"cooldown_until": None, "daily_limit_locked_date": None, "daily_limit_reason": "",
               "daily_limit_locked_kind": None, "daily_limit_locked_threshold": 0.0,
               "last_cooldown_deal_ticket": None, "pending_pullback_order": {"x": 1},
               "cached_daily_pnl": 0.0, "consec_loss_count": 0, "auto_loop_running": False}
        cfg = {"symbol": SYM, "daily_tp_usd": 50.0, "daily_sl_usd": 0.0, "max_consec_loss": 0,
               "cooldown_mins": 30.0, "fast_sl_cooldown_candles": 0, "analysis_tf": "M15"}
        state = {"pnl": 60.0}
        app = make_app(
            active_profile_key=PK, profile_ctx={PK: ctx}, auto_loop_running=False,
            _get_profile_cfg=lambda pk: cfg,
            _get_today_deals_stats=lambda **kw: (state["pnl"], 0, None),
            _get_bot_positions=mock.Mock(return_value=[SimpleNamespace(ticket=1)]),
            _close_single_pos=mock.Mock(return_value=(True, "ok")),
            _cached_deals_by_sym={"k": 1}, _cached_closed_deals_map={"k": 1},
            show_toast=mock.Mock(), enqueue_ai_scan=mock.Mock())
        return app, ctx, cfg, state

    def test_lock_survives_pnl_dip_and_unlocks_only_on_threshold_change(self):
        app, ctx, cfg, state = self.build()
        today = datetime.now(tz=b.VN_TZ).strftime("%Y-%m-%d")

        app._check_daily_profit_loss_limits(0.0, profile_key=PK)  # PnL 60 >= TP 50 -> khóa + đóng lệnh
        self.assertEqual(ctx["daily_limit_locked_date"], today)
        self.assertEqual(ctx["daily_limit_locked_kind"], "TP")
        self.assertIsNone(ctx["pending_pullback_order"])
        app._close_single_pos.assert_called_once()
        self.assertEqual(app._cached_deals_by_sym, {})       # cache deals được xóa sau khi đóng lệnh
        self.assertEqual(app._cached_closed_deals_map, {})

        state["pnl"] = 20.0  # PnL dao động xuống dưới ngưỡng
        app._check_daily_profit_loss_limits(0.0, profile_key=PK)
        self.assertEqual(ctx["daily_limit_locked_date"], today)  # KHÔNG mở khóa
        app._close_single_pos.assert_called_once()

        state["pnl"] = 60.0  # đổi ngưỡng nhưng PnL vẫn còn chạm ngưỡng mới -> vẫn khóa
        cfg["daily_tp_usd"] = 30.0
        app._check_daily_profit_loss_limits(0.0, profile_key=PK)
        self.assertEqual(ctx["daily_limit_locked_date"], today)

        state["pnl"] = 20.0  # đổi ngưỡng và PnL không còn chạm -> mở khóa
        cfg["daily_tp_usd"] = 100.0
        app._check_daily_profit_loss_limits(0.0, profile_key=PK)
        self.assertIsNone(ctx["daily_limit_locked_date"])
        self.assertIsNone(ctx["daily_limit_locked_kind"])

    def test_sl_lock_also_clears_cache_and_closes(self):
        app, ctx, cfg, state = self.build()
        cfg["daily_tp_usd"] = 0.0
        cfg["daily_sl_usd"] = 40.0
        state["pnl"] = -45.0
        app._check_daily_profit_loss_limits(0.0, profile_key=PK)
        self.assertEqual(ctx["daily_limit_locked_kind"], "SL")
        app._close_single_pos.assert_called_once()
        self.assertEqual(app._cached_deals_by_sym, {})
        state["pnl"] = -10.0
        app._check_daily_profit_loss_limits(0.0, profile_key=PK)
        self.assertIsNotNone(ctx["daily_limit_locked_date"])


# ---------------------------------------------------------------- 7
class _Stop(Exception):
    """Sentinel: dừng _run_analysis_worker ngay sau nhánh không tín hiệu."""


class TestAnalysisNoSignal(Base):
    LAST_CLOSE = 2000.0

    def df(self):
        n = 470
        ts = pd.date_range("2026-01-05 00:00", periods=n, freq="5min")
        c = np.full(n, self.LAST_CLOSE)
        return pd.DataFrame({"timestamps": ts, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 100.0})

    def scenarios_for(self, ends):
        def fake(predictor=None, df_in=None, x_timestamp=None, y_timestamp=None, pred_len=6, **kw):
            out = []
            for e in ends:
                closes = np.linspace(self.LAST_CLOSE, self.LAST_CLOSE + e, pred_len + 1)[1:]
                d = pd.DataFrame({"timestamps": list(y_timestamp.values), "open": closes, "high": closes + 0.3,
                                  "low": closes - 0.3, "close": closes, "volume": 100.0, "amount": 1.0})
                d.attrs["log_prob"] = 0.0
                out.append(d)
            return out
        return fake

    def run_worker(self, ends, use_gate, spread=0.1, pending="PENDING"):
        self.m.symbol_info_tick.return_value = SimpleNamespace(ask=2000.0 + spread, bid=2000.0)
        df = self.df()
        ctx = {"is_analyzing": False, "pending_pullback_order": pending}
        cfg = {"symbol": SYM, "tf": "M5", "analysis_tf": "M5", "lookback": 450, "pred_len": 6, "scenarios": 8,
               "consensus": 75.0, "max_dispersion": 2.5, "min_move": 1.2, "max_sl_price": 5.0, "rr": 2.0,
               "auto_rev_close": True, "use_confidence_gate": use_gate, "expected_move_k": 3.0,
               "trend_filter": True, "magic_number": MAGIC}
        app = make_app(
            active_profile_key=PK, profile_ctx={PK: ctx}, is_analyzing=False, _is_initial_loading=False,
            _ai_scan_lock=threading.Lock(), _ui_after=mock.Mock(), _set_loading_stage=mock.Mock(),
            _get_profile_cfg=lambda pk: cfg, _get_ai_scan_config_sig=lambda pk: ("sig",),
            fetch_mt5_candles=lambda *a, **k: df.copy(),
            _get_symbol_tolerances=lambda *a, **k: {"min_atr_sl_floor": 0.5, "min_tp_floor": 1.0, "is_btc": False,
                                                    "eps_equal": 0.05, "sl_break_tol": 0.0, "max_sl_tol": 0.0},
            _compute_adaptive_sampling_params=lambda *a, **k: (0.6, 0.8, "x"),
            _analyze_scalping_micro_macro_context=lambda *a, **k: {
                "macro_dir": 0, "orderflow_dir": 0, "wick_dir": 0, "summary_tag": "", "structure_reason": ""},
            get_local_predictor=lambda m: object(),
            _fast_predict_scenarios=self.scenarios_for(ends),
            _get_bot_positions=mock.Mock(side_effect=_Stop()),  # dừng ngay sau nhánh không tín hiệu
            _cleanup_memory_and_trim_workingset=mock.Mock())
        app._run_analysis_worker(profile_key=PK, reason="TEST")
        return app, ctx

    def check(self, app, ctx, expect_title):
        # Chỉ được văng đúng sentinel; UnboundLocalError (hoặc lỗi khác) là hồi quy
        self.assertEqual(app.log_error.call_count, 1, app.log_error.call_args_list)
        err = app.log_error.call_args[0][1]
        self.assertIsInstance(err, _Stop, f"lỗi bất ngờ: {err!r}")
        self.assertNotIsInstance(err, UnboundLocalError)
        self.assertIsNone(ctx["pending_pullback_order"])
        self.assertIn(expect_title, ctx["_sig_title"])
        self.assertIsNone(ctx["cached_action"])

    def test_low_confidence_bull_bias(self):
        app, ctx = self.run_worker([3.0] * 5 + [-3.0] * 3, use_gate=True)
        self.check(app, ctx, "ĐỘ TIN CẬY AI THẤP")

    def test_low_confidence_bear_bias(self):
        app, ctx = self.run_worker([-3.0] * 5 + [3.0] * 3, use_gate=True)
        self.check(app, ctx, "ĐỘ TIN CẬY AI THẤP")

    def test_low_cost_margin_bull(self):
        # 7/8 kịch bản tăng, biên độ +1.69$ nằm giữa min_move (1.2) và ngưỡng bù phí 3*(1.0+0.5)=4.5
        app, ctx = self.run_worker([2.0] * 7 + [-0.5], use_gate=False, spread=1.0)
        self.check(app, ctx, "BIÊN ĐỘ BÙ PHÍ THẤP")

    def test_low_cost_margin_bear(self):
        app, ctx = self.run_worker([-2.0] * 7 + [0.5], use_gate=False, spread=1.0)
        self.check(app, ctx, "BIÊN ĐỘ BÙ PHÍ THẤP")

    def test_plain_no_consensus(self):
        app, ctx = self.run_worker([2.0] * 4 + [-2.0] * 4, use_gate=False)
        self.check(app, ctx, "ĐỨNG NGOÀI")
        self.assertNotIn("BIÊN ĐỘ NHỎ", ctx["_sig_title"])

    def test_small_move_bear_shows_reason(self):
        # 8/8 kịch bản giảm (100%) nhưng chỉ -1.0$ < min_move 1.2$: ghi rõ lý do đứng ngoài (19/11)
        app, ctx = self.run_worker([-1.0] * 8, use_gate=False)
        self.check(app, ctx, "ĐỨNG NGOÀI (BIÊN ĐỘ NHỎ)")
        self.assertIn("Giảm 100%", ctx["_sig_title"])
        self.assertIn("-1.00$ < Biên độ tối thiểu 1.20$", ctx["_sig_title"])

    def test_small_move_bull_shows_reason(self):
        app, ctx = self.run_worker([1.0] * 8, use_gate=False)
        self.check(app, ctx, "ĐỨNG NGOÀI (BIÊN ĐỘ NHỎ)")
        self.assertIn("Tăng 100%", ctx["_sig_title"])
        self.assertIn("+1.00$ < Biên độ tối thiểu 1.20$", ctx["_sig_title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
