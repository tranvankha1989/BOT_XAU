"""Test engine live của tools/run_backtest.py (--engine live, mặc định): đổi lệnh + luồng báo cáo.
Không dùng MT5 thật: simulate() được mock bằng danh sách lệnh đóng giả."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mt5_scalping_bot as b  # noqa: E402
import run_backtest as rb  # noqa: E402
import live_replay as lr  # noqa: E402

VN = timezone(timedelta(hours=7))


def _ms(y, mo, d, h, mi, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=VN).timestamp() * 1000)


def closed_rec(open_ms, close_ms, typ="BUY", price_open=2000.0, price_close=2002.0, sl0=1996.0, profit=4.0, reason="TP"):
    return {"ticket": 1, "type": typ, "open_ms": open_ms, "close_ms": close_ms, "price_open": price_open,
            "price_close": price_close, "sl0": sl0, "tp0": 2008.0, "sl_last": sl0, "volume": 0.02,
            "profit": profit, "reason": reason}


class _FakeCache:
    hits, misses = 0, 0


class TestLiveClosedToTrades(unittest.TestCase):
    def test_chuyen_doi_ban_ghi_va_gio_vn(self):
        rows = rb.live_closed_to_trades([closed_rec(_ms(2026, 11, 17, 6, 21, 14), _ms(2026, 11, 17, 6, 26, 14),
                                                    reason="SL", profit=-2.0)], contract_size=100.0)
        r = rows[0]
        self.assertEqual(r["ticket"], "#0001")
        self.assertEqual(r["date"], "2026-11-17")
        self.assertEqual(r["in_time"], "17/11 06:21:14")
        self.assertEqual(r["out_time"], "17/11 06:26:14")
        self.assertEqual(r["duration"], "5p 00s")
        self.assertEqual(r["reason"], "Cắt lỗ (SL)")
        self.assertEqual(r["pnl"], -2.0)
        self.assertEqual(r["cost_kind"], "S1_EMBEDDED")
        # rủi ro = |giá vào - SL gốc| * lot * contract = 4 * 0.02 * 100 = 8
        self.assertEqual(r["risk_usd"], 8.0)

    def test_thieu_sl_goc_thi_risk_bang_0_va_giu_ly_do_la(self):
        rows = rb.live_closed_to_trades([closed_rec(_ms(2026, 11, 17, 9, 0), _ms(2026, 11, 17, 9, 3), sl0=0.0,
                                                    reason="Đóng cuối kỳ")])
        self.assertEqual(rows[0]["risk_usd"], 0.0)
        self.assertEqual(rows[0]["reason"], "Đóng cuối kỳ")

    def test_ma_lenh_tang_dan(self):
        recs = [closed_rec(_ms(2026, 11, 17, 9, 0), _ms(2026, 11, 17, 9, 1)) for _ in range(3)]
        self.assertEqual([r["ticket"] for r in rb.live_closed_to_trades(recs)], ["#0001", "#0002", "#0003"])


class TestRunEngineLive(unittest.TestCase):
    def _run(self, argv, closed=None, errors=None):
        cfg = dict(b.DEFAULT_PROFILE_SETTINGS["XAUUSD"])
        fake = {"closed": closed or [], "rejects": [], "logs": [], "errors": errors or [], "n_analyses": 3,
                "cache": _FakeCache(), "end_utc": 0.0, "contract": 100.0}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        buf = io.StringIO()
        with mock.patch.object(lr, "simulate", return_value=fake) as sim,                 mock.patch.object(rb, "load_strategy_cfg", return_value=(cfg, 0, "mock")),                 mock.patch.object(rb, "apply_threads"), contextlib.redirect_stdout(buf):
            code = rb.run(argv + ["--out", os.path.join(tmp.name, "out")])
        return code, buf.getvalue(), sim

    def test_mac_dinh_la_live_va_bao_cao_co_lenh(self):
        closed = [closed_rec(_ms(2026, 11, 17, 6, 21, 14), _ms(2026, 11, 17, 6, 26, 14))]
        code, out, sim = self._run(["--profile", "XAUUSD", "--symbol", "XAUUSDm", "--start", "2026-11-17",
                                    "--end", "2026-11-17", "--precision", "S1"], closed=closed)
        self.assertEqual(code, 0, out)
        sim.assert_called_once()
        self.assertIn("Engine           : LIVE", out)
        self.assertIn("Số lệnh              : 1", out)
        self.assertIn("Lỗi ghi nhận trong code bot: 0", out)

    def test_live_bao_loi_ghi_nhan_trong_code_bot(self):
        code, out, _ = self._run(["--profile", "XAUUSD", "--symbol", "XAUUSDm", "--start", "2026-11-17",
                                  "--end", "2026-11-17", "--precision", "S1"], errors=["loi thu"])
        self.assertEqual(code, 0, out)
        self.assertIn("Lỗi ghi nhận trong code bot: 1", out)
        self.assertIn("loi thu", out)

    def test_live_tu_choi_candle_va_no_ai(self):
        code, out, sim = self._run(["--profile", "XAUUSD", "--symbol", "XAUUSDm", "--start", "2026-11-17",
                                    "--end", "2026-11-17", "--precision", "CANDLE"])
        self.assertEqual(code, 2)
        sim.assert_not_called()
        code, out, sim = self._run(["--profile", "XAUUSD", "--symbol", "XAUUSDm", "--start", "2026-11-17",
                                    "--end", "2026-11-17", "--precision", "S1", "--no-ai"])
        self.assertEqual(code, 2)
        sim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
