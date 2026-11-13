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


if __name__ == "__main__":
    unittest.main(verbosity=2)
