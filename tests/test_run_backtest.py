"""Test mock cho tools/run_backtest.py (không dùng MT5 thật, không gửi lệnh)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mt5_scalping_bot as b
import run_backtest as rb

T0, TF, N = 1_700_000_100, 300, 120
SECRET = "SECRET_TOKEN_12345"


def rates():
    dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                   ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
    a = np.zeros(N, dtype=dt)
    a["time"] = T0 + np.arange(N) * TF
    px = 2000 + np.arange(N) * 2.0
    a["open"], a["high"], a["low"], a["close"] = px, px + 0.5, px - 0.5, px + 0.4
    a["tick_volume"] = 100
    return a


def ticks():
    dt = np.dtype([("time", "i8"), ("bid", "f8"), ("ask", "f8")])
    secs = np.arange(T0 + 50 * TF, T0 + 110 * TF, 5)
    px = 2000 + (secs - T0) / TF * 2.0
    t = np.zeros(len(secs), dtype=dt)
    t["time"], t["bid"], t["ask"] = secs, px, px + 0.3
    return t


def fake_mt5():
    return SimpleNamespace(
        initialize=mock.Mock(return_value=True), shutdown=mock.Mock(), last_error=mock.Mock(return_value=(1, "ok")),
        copy_rates_range=mock.Mock(return_value=rates()), copy_ticks_range=mock.Mock(return_value=ticks()),
        COPY_TICKS_ALL=-1, TIMEFRAME_M1=1, TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_M30=30, TIMEFRAME_H1=60,
        order_send=mock.Mock(), order_check=mock.Mock())


class TestRunBacktest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = fake_mt5()
        self.settings = os.path.join(self.tmp.name, "user_settings.json")
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"profiles": {"XAUUSD": {
                "rr": "3.5", "tf": "M5", "analysis_tf": "M5", "max_sl_price": "0", "max_hold_mins": "0",
                "hf_token": SECRET, "telegram_bot_token": SECRET, "telegram_chat_id": SECRET, "magic_number": "999",
            }}}, f)
        prev_mt5 = b.mt5
        for p in (mock.patch.object(rb, "mt5", self.m), mock.patch.object(b, "MT5_AVAILABLE", True),
                  mock.patch.object(b, "SETTINGS_FILE", self.settings), mock.patch.object(b, "SETTINGS_DIR", self.tmp.name),
                  mock.patch.object(b.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)
        self.prev_mt5 = prev_mt5
        self.out = os.path.join(self.tmp.name, "out")

    def run_cli(self, *extra):
        argv = ["--profile", "XAUUSD", "--symbol", "XAUUSDm", "--start", "2023-11-14", "--end", "2023-11-15",
                "--no-ai", "--out", self.out, *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rb.run(argv)
        return code, buf.getvalue()

    def test_candle_mode_report_csv_and_no_orders(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertIn("Chế độ chạy thực tế  : Nến MT5", out)
        self.assertIn("Số ngày giao dịch", out)
        self.assertIn("Số lệnh", out)
        self.assertIn("Thời gian chạy", out)
        self.assertIn("TỔNG KẾT BACKTEST", out)
        files = os.listdir(self.out)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("backtest_XAUUSDm_2023-11-14_2023-11-15_"))
        self.m.order_send.assert_not_called()
        self.m.order_check.assert_not_called()
        self.m.initialize.assert_called_once()
        self.m.shutdown.assert_called_once()
        self.assertIs(b.mt5, self.prev_mt5)  # proxy chỉ-đọc đã được gỡ

    def test_s1_mode_reported(self):
        code, out = self.run_cli("--precision", "S1")
        self.assertEqual(code, 0, out)
        self.assertIn("Chế độ chạy thực tế  : Tick S1 (1 giây)", out)
        self.m.order_send.assert_not_called()

    def test_s1_fallback_to_candle_is_reported(self):
        self.m.copy_ticks_range.return_value = None
        code, out = self.run_cli("--precision", "S1")
        self.assertEqual(code, 0, out)
        self.assertIn("đã fallback từ Tick S1", out)

    def test_secrets_and_excluded_keys_never_loaded_or_printed(self):
        cfg, loaded, note = rb.load_strategy_cfg("XAUUSD")
        self.assertEqual(cfg["rr"], "3.5")  # khóa chiến lược được nạp
        self.assertNotEqual(cfg["hf_token"], SECRET)
        self.assertNotEqual(cfg["telegram_bot_token"], SECRET)
        self.assertNotEqual(cfg["telegram_chat_id"], SECRET)
        self.assertNotEqual(str(cfg["magic_number"]), "999")
        self.assertNotIn(SECRET, note)
        code, out = self.run_cli()
        self.assertNotIn(SECRET, out)

    def test_read_only_proxy_blocks_order_functions(self):
        proxy = rb.ReadOnlyMT5(self.m)
        with self.assertRaises(RuntimeError):
            proxy.order_send({})
        with self.assertRaises(RuntimeError):
            proxy.order_check({})
        self.assertEqual(proxy.TIMEFRAME_M5, 5)  # đọc dữ liệu vẫn dùng được
        self.m.order_send.assert_not_called()

    def test_profile_symbol_mismatch_rejected_without_mt5(self):
        argv = ["--profile", "BTCUSD", "--symbol", "XAUUSDm", "--start", "2023-11-14", "--end", "2023-11-15"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rb.run(argv)
        self.assertEqual(code, 2)
        self.m.initialize.assert_not_called()

    def test_bad_date_rejected(self):
        argv = ["--profile", "XAUUSD", "--start", "14/11/2023", "--end", "2023-11-15"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rb.run(argv), 2)
        self.m.initialize.assert_not_called()

    def test_no_candles_returns_error_code(self):
        self.m.copy_rates_range.return_value = None
        code, out = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("Không lấy được nến", out)
        self.assertIs(b.mt5, self.prev_mt5)

    def test_default_drops_out_of_range_trades(self):
        code, out = self.run_cli("--start", "2023-11-14", "--end", "2023-11-14")
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"Lệnh ngoài khoảng    : loại [1-9]\d* lệnh")
        self.assertIn("Số lệnh              : 0", out)
        self.assertIn("KHÔNG tính vào thống kê chính", out)
        self.assertFalse(os.path.isdir(self.out) and os.listdir(self.out))  # không có lệnh trong khoảng -> không ghi CSV

    def test_keep_warmup_trades_flag(self):
        code, out = self.run_cli("--start", "2023-11-14", "--end", "2023-11-14", "--keep-warmup-trades")
        self.assertEqual(code, 0, out)
        self.assertIn("ĐANG GIỮ (--keep-warmup-trades)", out)
        self.assertNotIn("Số lệnh              : 0", out)
        self.assertEqual(len(os.listdir(self.out)), 1)
        self.assertIn("THỐNG KÊ BỔ SUNG", out)

    def test_in_range_run_has_no_dropped_and_extra_metrics(self):
        code, out = self.run_cli()  # [2023-11-14, 2023-11-15] bao trọn dữ liệu
        self.assertEqual(code, 0, out)
        self.assertIn("loại 0 lệnh", out)
        self.assertIn("THỐNG KÊ BỔ SUNG", out)

    def test_seed_is_set_and_printed(self):
        with mock.patch.object(rb, "set_seed", wraps=rb.set_seed) as m_seed:
            code, out = self.run_cli("--seed", "5")
        self.assertEqual(code, 0, out)
        m_seed.assert_called_once_with(5)
        self.assertIn("Seed                 : 5", out)
        with mock.patch.object(rb, "set_seed", wraps=rb.set_seed) as m_seed:
            code, out = self.run_cli()
        m_seed.assert_called_once_with(1)  # mặc định 1


def T(date, in_time, pnl, risk=10.0):
    return {"date": date, "in_time": in_time, "pnl": pnl, "risk_usd": risk}


class TestFilterAndMetrics(unittest.TestCase):
    def test_filter_boundaries_vn_time(self):
        trades = [
            T("2023-11-13", "13/11 23:59:59", -1.0),   # 1 giây trước start -> loại
            T("2023-11-14", "14/11 00:00:00", 2.0),    # đúng biên start -> giữ
            T("2023-11-15", "15/11 23:59:59", 3.0),    # đúng biên end -> giữ
            T("2023-11-16", "16/11 00:00:00", -4.0),   # 1 giây sau end -> loại
            T("2023-11-15", "15/11 10:30", 5.0),       # định dạng nến (không có giây) -> giữ
            {"pnl": 7.0},                              # thiếu date/in_time -> giữ và đếm riêng
        ]
        kept, dropped, unparsed = rb.filter_trades_by_range(trades, "2023-11-14", "2023-11-15")
        self.assertEqual([t["pnl"] for t in kept], [2.0, 3.0, 5.0, 7.0])
        self.assertEqual([t["pnl"] for t in dropped], [-1.0, -4.0])
        self.assertEqual(unparsed, 1)
        # start > end được hoán đổi giống hàm backtest
        kept2, dropped2, _ = rb.filter_trades_by_range(trades, "2023-11-15", "2023-11-14")
        self.assertEqual(len(kept2), 4)
        self.assertEqual(len(dropped2), 2)

    def test_metrics_and_r_sign_conflict_warning(self):
        trades = [T("d", "x 00:00:00", 1.0, 0.1), T("d", "x 00:00:00", -5.0, 10.0), T("d", "x 00:00:00", -5.0, 10.0)]
        m = rb.compute_extra_metrics(trades)
        self.assertAlmostEqual(m["total_pnl"], -9.0)
        self.assertAlmostEqual(m["profit_factor"], 0.1)
        self.assertAlmostEqual(m["r_mean_per_trade"], (10.0 - 0.5 - 0.5) / 3.0)   # (a) dương
        self.assertAlmostEqual(m["r_total_over_risk"], -9.0 / 20.1)               # (b) âm
        self.assertTrue(m["r_sign_conflict"])
        text = "\n".join(rb.format_extra_metrics(m))
        self.assertIn("TRÁI DẤU", text)

    def test_metrics_no_conflict_no_warning(self):
        trades = [T("d", "x 00:00:00", 5.0, 10.0), T("d", "x 00:00:00", -2.0, 10.0)]
        m = rb.compute_extra_metrics(trades)
        self.assertFalse(m["r_sign_conflict"])
        self.assertAlmostEqual(m["r_mean_per_trade"], 0.15)
        self.assertAlmostEqual(m["r_total_over_risk"], 0.15)
        self.assertNotIn("TRÁI DẤU", "\n".join(rb.format_extra_metrics(m)))

    def test_metrics_without_risk_data(self):
        m = rb.compute_extra_metrics([{"pnl": 3.0}, {"pnl": -1.0}])
        self.assertIsNone(m["r_mean_per_trade"])
        self.assertIsNone(m["r_total_over_risk"])
        self.assertFalse(m["r_sign_conflict"])

    def test_set_seed_reproducible(self):
        import random as _random
        torch = b.torch
        rb.set_seed(7)
        a = (_random.random(), float(np.random.rand()), float(torch.rand(1)))
        rb.set_seed(7)
        c = (_random.random(), float(np.random.rand()), float(torch.rand(1)))
        rb.set_seed(8)
        d = (_random.random(), float(np.random.rand()), float(torch.rand(1)))
        self.assertEqual(a, c)
        self.assertNotEqual(a, d)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestThreadsOption(unittest.TestCase):
    def test_resolve_threads(self):
        import tools.run_backtest as rb
        self.assertEqual(rb.resolve_threads(None, 6), (6, None))
        self.assertEqual(rb.resolve_threads(4, 6), (4, None))
        n, w = rb.resolve_threads(99, 6)
        self.assertEqual(n, 6)
        self.assertIsNotNone(w)


class TestTrendFilterFlags(unittest.TestCase):
    """P50-C: --trend-filter / --structure-conflict-mode chỉ ảnh hưởng lần chạy, hiệu lực được báo cáo và ghi vào CSV."""

    def _eval(self, cfg, macro_dir=-1):
        import pandas as pd
        n_ana, n_sc = 120, 80
        ts = pd.date_range("2026-11-13", periods=n_ana, freq="5min", tz="UTC")

        def mk(n):
            c = 100.0 + np.arange(n) * 0.01
            return pd.DataFrame({"timestamps": ts[:n], "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0})
        df_ana, df_sc = mk(n_ana), mk(n_sc)
        last_c = float(df_ana["close"].iloc[100 - 1])

        def scen(*a, **k):  # 8 kịch bản cùng tăng mạnh (đồng thuận BUY 100%, biên độ dự báo +6$)
            out = []
            for _ in range(8):
                d = pd.DataFrame({"close": np.linspace(last_c + 1, last_c + 6, 6), "high": np.linspace(last_c + 1.5, last_c + 6.5, 6),
                                  "low": np.linspace(last_c + 0.5, last_c + 5.5, 6)})
                d.attrs["log_prob"] = -1.0
                out.append(d)
            return out
        app = object.__new__(b.KronosDesktopApp)
        app._analyze_scalping_micro_macro_context = lambda *a, **k: {"macro_dir": macro_dir, "structure_reason": "giả"}
        app._fast_predict_scenarios = scen
        app._compute_adaptive_sampling_params = lambda *a, **k: (1.0, 0.9, "test")
        return app._evaluate_bt_ai_candle(object(), df_ana, 100, cfg, {}, "M5", False, 1.0,
                                          df_rates_scalp=df_sc, idx_scalp=75, scalp_tf_str="M5")

    def base_cfg(self):
        return {"consensus": 75, "min_move": 1.2, "max_dispersion": 2.5, "sl_atr": 1.5, "sl_buffer": 0.5,
                "trend_filter": True, "bt_use_live_structure": True, "lookback": 100, "scenarios": 8}

    def test_mac_dinh_khong_doi_hanh_vi_cu_chan_buy_khi_macro_giam(self):
        cfg = self.base_cfg()
        before = dict(cfg)
        eff = rb.apply_flag_overrides(cfg, {}, None, None)          # không cờ
        self.assertEqual(cfg, before)                               # cfg không bị đụng
        self.assertTrue(eff["trend_filter"])
        self.assertEqual(eff["structure_conflict_mode"], "neutral")
        self.assertEqual(eff["trend_filter_src"], "cấu hình hiện hành")
        self.assertIsNone(self._eval(cfg, macro_dir=-1))            # macro_dir == -1 chặn BUY

    def test_co_on_chan_buy(self):
        cfg = self.base_cfg()
        cfg["trend_filter"] = False                                  # cấu hình đang tắt, cờ on bật lại
        eff = rb.apply_flag_overrides(cfg, {}, "on", None)
        self.assertTrue(eff["trend_filter"])
        self.assertEqual(eff["trend_filter_src"], "cờ --trend-filter")
        self.assertIsNone(self._eval(cfg, macro_dir=-1))

    def test_co_off_khong_con_chan_buy(self):
        cfg = self.base_cfg()
        eff = rb.apply_flag_overrides(cfg, {}, "off", None)
        self.assertFalse(eff["trend_filter"])
        res = self._eval(cfg, macro_dir=-1)
        self.assertIsNotNone(res)
        self.assertEqual(res["signal"], "BUY")
        self.assertEqual(res["macro_dir"], -1)

    def test_structure_conflict_mode_va_hieu_luc(self):
        cfg = self.base_cfg()
        cfg["structure_conflict_mode"] = "Scalp "
        self.assertEqual(rb.apply_flag_overrides(dict(cfg), {}, None, None)["structure_conflict_mode"], "scalp")  # theo cấu hình
        eff = rb.apply_flag_overrides(cfg, {}, None, "analysis")
        self.assertEqual(eff["structure_conflict_mode"], "analysis")
        self.assertEqual(cfg["structure_conflict_mode"], "analysis")
        lines = "\n".join(rb.format_effective_flags(eff))
        self.assertIn("trend_filter", lines)
        self.assertIn("analysis", lines)
        # trend_filter không có trong cfg -> lấy mặc định của profile (def_cfg), đúng như bot đọc
        self.assertFalse(rb.apply_flag_overrides({}, {"trend_filter": False}, None, None)["trend_filter"])
        self.assertTrue(rb.apply_flag_overrides({}, {}, None, None)["trend_filter"])

    def test_khong_ghi_user_settings_va_csv_co_gia_tri_hieu_luc(self):
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "user_settings.json")
            with open(settings, "w", encoding="utf-8") as f:
                f.write('{"profiles": {}}')
            mtime = os.path.getmtime(settings)
            cfg = self.base_cfg()
            eff = rb.apply_flag_overrides(cfg, {}, "off", "scalp")
            trades = [{"ticket": "#0001", "date": "2026-11-13", "in_time": "13/11 20:50", "out_time": "13/11 20:55", "duration": "5p",
                       "type": "BUY", "lot": "0.02", "in_price": "1.00", "out_price": "2.00", "pnl": 1.5, "risk_usd": 1.0,
                       "cost_spread": 0.1, "cost_kind": "CANDLE_EXPLICIT", "reason": "TP"}]
            path = rb.export_csv_with_params(trades, os.path.join(d, "out", "bt.csv"), eff)
            import csv
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["trend_filter"], "off")
            self.assertEqual(rows[0]["structure_conflict_mode"], "scalp")
            self.assertEqual(rows[0]["pnl"], "1.5")                  # cột cũ giữ nguyên
            self.assertEqual(os.path.getmtime(settings), mtime)      # không đụng user_settings.json

    def run_cli(self, *flags):
        cfg = dict(b.DEFAULT_PROFILE_SETTINGS["XAUUSD"])
        fake_mt5 = SimpleNamespace(initialize=lambda: False, last_error=lambda: (1, "x"))
        buf = io.StringIO()
        with mock.patch.object(rb, "load_strategy_cfg", return_value=(cfg, 0, "mock")), mock.patch.object(rb, "mt5", fake_mt5),                 mock.patch.object(rb, "apply_threads"), contextlib.redirect_stdout(buf):
            code = rb.run(["--profile", "XAUUSD", "--symbol", "XAUUSDm", "--start", "2026-11-12", "--end", "2026-11-12", *flags])
        return code, buf.getvalue(), cfg

    def test_cli_in_gia_tri_hieu_luc_o_dau_log(self):
        code, out, cfg = self.run_cli("--trend-filter", "off", "--structure-conflict-mode", "analysis")
        self.assertEqual(code, 1)  # mt5.initialize giả trả False -> dừng sau phần đầu log
        self.assertIn("trend_filter           : OFF (cờ --trend-filter)", out)
        self.assertIn("structure_conflict_mode: analysis (cờ --structure-conflict-mode)", out)
        self.assertFalse(cfg["trend_filter"])
        self.assertEqual(cfg["structure_conflict_mode"], "analysis")
        # mặc định: theo cấu hình hiện hành, không đổi cfg
        code, out, cfg = self.run_cli()
        self.assertIn("(cấu hình hiện hành)", out)
        self.assertEqual(cfg["structure_conflict_mode"], b.DEFAULT_PROFILE_SETTINGS["XAUUSD"]["structure_conflict_mode"])
