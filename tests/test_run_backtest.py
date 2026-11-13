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
