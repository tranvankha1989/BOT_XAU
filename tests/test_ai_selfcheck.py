"""Tự chấm điểm AI (ai_selfcheck_n, mặc định TẮT, người dùng duyệt 24/11/2026):
AI gần đây đúng < mức tối thiểu trong N tín hiệu -> tạm nghỉ vào lệnh mới; vẫn chấm tiếp, đúng lại thì vào lệnh."""
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mt5_scalping_bot as b  # noqa: E402
from trading_modules import ai_selfcheck as sc  # noqa: E402
from trading_modules.param_rules import validate_param_values  # noqa: E402
import test_phase1 as tp  # noqa: E402

T0 = pd.Timestamp("2026-11-23 12:00")


def at(minutes):
    return T0 + pd.Timedelta(minutes=minutes)


class TestSelfcheckStep(unittest.TestCase):
    def test_cham_sau_du_so_nen(self):
        st = sc.new_state()
        sc.selfcheck_step(st, at(0), 100.0, "SELL", 5, 8)
        self.assertEqual(len(st["pending"]), 1)
        sc.selfcheck_step(st, at(10), 99.0, None, 5, 8)   # mới 2 nến: chưa chấm
        self.assertEqual(st["results"], [])
        sc.selfcheck_step(st, at(15), 99.0, None, 5, 8)   # đủ 3 nến M5: SELL và giá giảm -> đúng
        self.assertEqual([r["ok"] for r in st["results"]], [True])
        self.assertEqual(st["pending"], [])

    def test_buy_gia_giam_la_sai_va_bang_gia_la_sai(self):
        st = sc.new_state()
        sc.selfcheck_step(st, at(0), 100.0, "BUY", 5, 8)
        sc.selfcheck_step(st, at(5), 100.0, "BUY", 5, 8)
        sc.selfcheck_step(st, at(15), 99.0, None, 5, 8)
        sc.selfcheck_step(st, at(20), 100.0, None, 5, 8)
        self.assertEqual([r["ok"] for r in st["results"]], [False, False])

    def test_den_qua_muon_thi_bo_khong_cham(self):
        st = sc.new_state()
        sc.selfcheck_step(st, at(0), 100.0, "BUY", 5, 8)
        r = sc.selfcheck_step(st, at(45), 120.0, None, 5, 8)  # 9 nến sau (> 2 lần 3 nến): app tắt -> bỏ
        self.assertEqual(st["results"], [])
        self.assertEqual(st["pending"], [])
        self.assertTrue(r["changed"])

    def _fill(self, st, oks, end_min=0):
        for i, ok in enumerate(oks):
            st["results"].append({"t": sc._sec(at(end_min - 5 * (len(oks) - i))), "ok": ok})

    def test_tam_nghi_khi_dung_it_hon_nua(self):
        st = sc.new_state()
        self._fill(st, [True, True, True, False, False, False, False, False])
        r = sc.selfcheck_step(st, at(0), 100.0, None, 5, 8)
        self.assertTrue(r["paused"])
        self.assertEqual((r["correct"], r["total"]), (3, 8))

    def test_dung_dung_mot_nua_thi_khong_nghi(self):
        st = sc.new_state()
        self._fill(st, [True] * 4 + [False] * 4)
        self.assertFalse(sc.selfcheck_step(st, at(0), 100.0, None, 5, 8)["paused"])

    def test_chua_du_ket_qua_thi_giao_dich_binh_thuong(self):
        st = sc.new_state()
        self._fill(st, [False] * 7)
        self.assertFalse(sc.selfcheck_step(st, at(0), 100.0, None, 5, 8)["paused"])

    def test_n_bang_0_khong_bao_gio_nghi_nhung_van_ghi(self):
        st = sc.new_state()
        self._fill(st, [False] * 10)
        r = sc.selfcheck_step(st, at(0), 100.0, "BUY", 5, 0)
        self.assertFalse(r["paused"])
        self.assertEqual(len(st["pending"]), 1)

    def test_ket_qua_cu_hon_3_gio_bi_bo(self):
        st = sc.new_state()
        self._fill(st, [False] * 8, end_min=-200)  # tất cả cũ hơn 3 giờ
        r = sc.selfcheck_step(st, at(0), 100.0, None, 5, 8)
        self.assertFalse(r["paused"])
        self.assertEqual(st["results"], [])

    def test_phan_tich_lai_cung_nen_khong_ghi_trung(self):
        st = sc.new_state()
        sc.selfcheck_step(st, at(0), 100.0, "BUY", 5, 8)
        r = sc.selfcheck_step(st, at(0), 100.0, "BUY", 5, 8)
        self.assertEqual(len(st["pending"]), 1)
        self.assertFalse(r["changed"])

    def test_dang_nghi_van_cham_va_tu_chay_lai(self):
        st = sc.new_state()
        self._fill(st, [False] * 8)
        self.assertTrue(sc.selfcheck_step(st, at(0), 100.0, "BUY", 5, 8)["paused"])
        for k in range(1, 6):  # tín hiệu "ảo" lúc nghỉ, giá tăng đều -> BUY đúng
            r = sc.selfcheck_step(st, at(5 * k), 100.0 + k, "BUY", 5, 8)
        self.assertGreaterEqual(r["correct"], 1)
        for k in range(6, 12):
            r = sc.selfcheck_step(st, at(5 * k), 100.0 + k, "BUY", 5, 8)
        self.assertFalse(r["paused"])

    def test_clean_state_bo_phan_tu_hong(self):
        st = sc.clean_state({"pending": [{"t": 1, "dir": -1, "price": "2.5"}, {"x": 1}],
                             "results": [{"t": 2, "ok": 1}, "rac"]})
        self.assertEqual(st, {"pending": [{"t": 1, "dir": -1, "price": 2.5}], "results": [{"t": 2, "ok": True}]})
        self.assertEqual(sc.clean_state(None), sc.new_state())


def _app(persist=False):
    app = object.__new__(b.KronosDesktopApp)
    app.profile_ctx = {"XAUUSD": {}, "BTCUSD": {}}
    app.log = mock.Mock()
    app.log_error = mock.Mock()
    app._write_file_log = mock.Mock()
    if persist:
        app._ai_selfcheck_persist = True
    return app


class TestSelfcheckApp(unittest.TestCase):
    def test_cau_hinh_mac_dinh_tat_ca_hai_tab(self):
        for pk in ("XAUUSD", "BTCUSD"):
            d = b.DEFAULT_PROFILE_SETTINGS[pk]
            self.assertEqual((d["ai_selfcheck_n"], d["ai_selfcheck_candles"], d["ai_selfcheck_min_pct"]), ("0", "3", "50"))
        keys = b.KronosDesktopApp.SETTINGS_SECTION_MAP[3][1]
        for k in ("ai_selfcheck_n", "ai_selfcheck_candles", "ai_selfcheck_min_pct"):
            self.assertIn(k, keys)
            self.assertIn(k, b.PARAM_HELP_GUIDE)

    def test_luat_o_so(self):
        base = {"ai_selfcheck_n": "8", "ai_selfcheck_candles": "3", "ai_selfcheck_min_pct": "50"}
        self.assertEqual(validate_param_values(base), [])
        bad = dict(base, ai_selfcheck_n="-1")
        self.assertTrue(any("ai_selfcheck_n" in i["keys"] for i in validate_param_values(bad)))
        bad = dict(base, ai_selfcheck_candles="0")
        self.assertTrue(any("ai_selfcheck_candles" in i["keys"] for i in validate_param_values(bad)))
        bad = dict(base, ai_selfcheck_min_pct="120")
        self.assertTrue(any("ai_selfcheck_min_pct" in i["keys"] for i in validate_param_values(bad)))
        off = dict(base, ai_selfcheck_n="0", ai_selfcheck_candles="0")  # cha tắt: không kiểm con
        self.assertEqual(validate_param_values(off), [])

    def test_tat_thi_khong_nghi_va_khong_ghi_file(self):
        app = _app()
        with mock.patch.object(b, "SETTINGS_DIR", tempfile.mkdtemp()) as d:
            r = app._ai_selfcheck_update("XAUUSD", {"ai_selfcheck_n": "0"}, at(0), 100.0, "BUY", "M5")
            self.assertFalse(r["paused"])
            self.assertFalse(os.path.exists(os.path.join(d, "ai_selfcheck_state.json")))
        self.assertEqual(len(app.profile_ctx["XAUUSD"]["_ai_selfcheck"]["pending"]), 1)  # vẫn chấm để bật lên có sẵn điểm

    def test_log_khi_bat_dau_nghi_va_khi_chay_lai(self):
        app = _app()
        cfg = {"ai_selfcheck_n": "4", "ai_selfcheck_candles": "1", "ai_selfcheck_min_pct": "50"}
        st = app._ai_selfcheck_state("XAUUSD")
        for i in range(4):
            st["results"].append({"t": sc._sec(at(-5 * (4 - i))), "ok": False})
        r = app._ai_selfcheck_update("XAUUSD", cfg, at(0), 100.0, "BUY", "M5")
        self.assertTrue(r["paused"])
        self.assertIn("Tạm nghỉ vào lệnh mới", app.log.call_args[0][0])
        self.assertIn("0/4", app.profile_ctx["XAUUSD"]["_ai_selfcheck_msg"])
        app.log.reset_mock()
        app._ai_selfcheck_update("XAUUSD", cfg, at(0), 100.0, "BUY", "M5")  # cùng trạng thái: không log lại
        app.log.assert_not_called()
        for k in range(1, 4):
            r = app._ai_selfcheck_update("XAUUSD", cfg, at(5 * k), 100.0 + k, "BUY", "M5")
        self.assertFalse(r["paused"])
        self.assertIn("AI đúng trở lại", app.log.call_args[0][0])
        self.assertEqual(app.profile_ctx["XAUUSD"]["_ai_selfcheck_msg"], "")

    def test_app_that_luu_va_nap_lai_sau_khi_mo_lai(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(b, "SETTINGS_DIR", d):
            app = _app(persist=True)
            app._ai_selfcheck_update("XAUUSD", {"ai_selfcheck_n": "8"}, at(0), 100.0, "SELL", "M5")
            with open(os.path.join(d, "ai_selfcheck_state.json"), encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["XAUUSD"]["pending"]), 1)
            app2 = _app(persist=True)
            app2._ai_selfcheck_update("XAUUSD", {"ai_selfcheck_n": "8"}, at(15), 99.0, None, "M5")
            self.assertEqual([r["ok"] for r in app2.profile_ctx["XAUUSD"]["_ai_selfcheck"]["results"]], [True])

    def test_bo_phat_lai_khong_doc_file_cua_may(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "ai_selfcheck_state.json"), "w", encoding="utf-8") as f:
            json.dump({"XAUUSD": {"pending": [], "results": [{"t": sc._sec(at(-5)), "ok": False}] * 8}}, f)
        with mock.patch.object(b, "SETTINGS_DIR", d):
            app = _app(persist=False)  # object.__new__ như live_replay
            r = app._ai_selfcheck_update("XAUUSD", {"ai_selfcheck_n": "8"}, at(0), 100.0, None, "M5")
        self.assertFalse(r["paused"])


class TestSelfcheckGateInAnalysis(tp.Base):
    """Chạy trọn _run_analysis_worker (mock) tới chuỗi quyết định vào lệnh."""
    LAST_CLOSE = tp.TestAnalysisNoSignal.LAST_CLOSE
    df = tp.TestAnalysisNoSignal.df
    scenarios_for = tp.TestAnalysisNoSignal.scenarios_for

    def run_full(self, cfg_extra, prefill_wrong):
        df = self.df()
        ctx = {"is_analyzing": False, "pending_pullback_order": None, "auto_loop_running": True}
        if prefill_wrong:
            t_close = df["timestamps"].iloc[-2]  # nến phân tích vừa đóng
            st = sc.new_state()
            for i in range(8):
                st["results"].append({"t": sc._sec(t_close) - 300 * (12 - i), "ok": False})
            ctx["_ai_selfcheck"] = st
        cfg = {"symbol": tp.SYM, "tf": "M5", "analysis_tf": "M5", "lookback": 450, "pred_len": 6, "scenarios": 8,
               "consensus": 75.0, "max_dispersion": 2.5, "min_move": 1.2, "max_sl_price": 5.0, "rr": 2.0,
               "auto_rev_close": True, "use_confidence_gate": False, "expected_move_k": 3.0,
               "trend_filter": False, "magic_number": tp.MAGIC, "confirm_entry_pa": False}
        cfg.update(cfg_extra)
        self.m.symbol_info_tick.return_value = tp.SimpleNamespace(ask=2000.1, bid=2000.0)
        execute = mock.Mock(return_value=(True, "đã khớp"))
        app = tp.make_app(
            active_profile_key=tp.PK, profile_ctx={tp.PK: ctx}, is_analyzing=False, _is_initial_loading=False,
            auto_loop_running=True, _last_order_candle_ts={},
            _ai_scan_lock=threading.Lock(), _ui_after=mock.Mock(), _set_loading_stage=mock.Mock(),
            _get_profile_cfg=lambda pk: cfg, _get_ai_scan_config_sig=lambda pk: ("sig",),
            fetch_mt5_candles=lambda *a, **k: df.copy(),
            _get_symbol_tolerances=lambda *a, **k: {"min_atr_sl_floor": 0.5, "min_tp_floor": 1.0, "is_btc": False,
                                                    "eps_equal": 0.05, "sl_break_tol": 0.0, "max_sl_tol": 0.0},
            _compute_adaptive_sampling_params=lambda *a, **k: (0.6, 0.8, "x"),
            _analyze_scalping_micro_macro_context=lambda *a, **k: {
                "macro_dir": 0, "orderflow_dir": 0, "wick_dir": 0, "summary_tag": "", "structure_reason": ""},
            get_local_predictor=lambda m: object(),
            _fast_predict_scenarios=self.scenarios_for([3.0] * 8),
            _get_bot_positions=mock.Mock(return_value=[]),
            _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk, action=None: False,
            _get_live_bot_status_text=lambda pk: "", _zone_entry_limit=lambda *a, **k: None,
            execute_mt5_order=execute,
            _cleanup_memory_and_trim_workingset=mock.Mock())
        app._run_analysis_worker(profile_key=tp.PK, reason="TEST")
        logs = " | ".join(str(c.args[0]) for c in app.log.call_args_list)
        err_ctx = [str(c.args[0]) for c in app.log_error.call_args_list]
        self.assertFalse([e for e in err_ctx if "SELFCHECK" in e], err_ctx)
        return app, ctx, execute, logs

    def test_ai_dang_sai_thi_khong_vao_lenh(self):
        app, ctx, execute, logs = self.run_full({"ai_selfcheck_n": "8"}, prefill_wrong=True)
        execute.assert_not_called()
        self.assertIsNone(ctx["pending_pullback_order"])
        self.assertIn("Tự chấm điểm AI", logs)
        self.assertTrue(ctx.get("_ai_selfcheck_paused"))
        self.assertEqual(len(ctx["_ai_selfcheck"]["pending"]), 1)  # tín hiệu bị chặn vẫn được ghi để chấm

    def test_tat_tinh_nang_thi_vao_lenh_nhu_cu(self):
        app, ctx, execute, logs = self.run_full({"ai_selfcheck_n": "0"}, prefill_wrong=True)
        execute.assert_called_once()
        self.assertNotIn("Bỏ qua tín hiệu", logs)

    def test_bat_nhung_ai_dang_dung_thi_vao_lenh(self):
        app, ctx, execute, logs = self.run_full({"ai_selfcheck_n": "8"}, prefill_wrong=False)
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
