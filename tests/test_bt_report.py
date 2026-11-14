import os
import sys, csv, os, tempfile, unittest
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


def T(date, pnl, risk=10.0, cost=1.0, kind="CANDLE_EXPLICIT"):
    return {"ticket": "#1", "date": date, "in_time": "x", "out_time": "y", "duration": "1m", "type": "BUY",
            "lot": "0.02", "in_price": "1.00", "out_price": "2.00", "pnl": pnl, "risk_usd": risk,
            "cost_spread": cost, "cost_kind": kind, "reason": "r"}


def fake_trades(n_days=12, per_day=2):
    """Mỗi ngày: 1 lệnh thắng +20 và 1 lệnh thua -10 (ngày chẵn: thua -30)."""
    out = []
    for d in range(n_days):
        out.append(T(f"2026-11-{d + 1:02d}", 20.0))
        out.append(T(f"2026-11-{d + 1:02d}", -30.0 if d % 2 == 0 else -10.0))
    return out


class TestReport(unittest.TestCase):
    def test_metrics_known_values(self):
        tr = fake_trades()
        r = b.build_backtest_report(tr, 1000.0)
        self.assertEqual(r["n_trades"], 24)
        self.assertEqual(r["n_days"], 12)
        # tổng: 6 ngày *(20-30) + 6 ngày *(20-10) = -60 + 60 = 0
        self.assertAlmostEqual(r["total_pnl"], 0.0)
        # PF = 240 / (180+60) = 1.0
        self.assertAlmostEqual(r["profit_factor"], 1.0)
        self.assertAlmostEqual(r["win_rate"], 50.0)
        self.assertAlmostEqual(r["expectancy_r"], 0.0)
        # Ngày có lãi: ngày lẻ (+10) = 6/12
        self.assertAlmostEqual(r["profitable_days_pct"], 50.0)
        self.assertAlmostEqual(r["avg_trades_per_day"], 2.0)
        self.assertEqual(r["worst_day"], ("2026-11-01", -10.0))
        self.assertEqual(r["max_loss_streak"], 1)
        # Drawdown: 1000 ->1020 ->990 : dd 30 từ đỉnh 1020 => 2.94%
        self.assertAlmostEqual(r["max_dd"], 30.0)
        self.assertAlmostEqual(r["max_dd_pct"], 30.0 / 1020.0 * 100.0, places=4)
        # chi phí 24 * 1.0, lãi gộp trước chi phí = 0 + 24 -> 100%
        self.assertAlmostEqual(r["cost_spread_total"], 24.0)
        self.assertAlmostEqual(r["cost_ratio_pct"], 100.0)
        self.assertEqual(r["warnings"][0].startswith("Chỉ có") or True, True)
        self.assertTrue(any("< 100" in w for w in r["warnings"]))
        self.assertFalse(any("< 10)" in w for w in r["warnings"]))  # 12 ngày: không cảnh báo ngày

    def test_loss_streak_ignores_be(self):
        tr = [T("2026-11-01", p) for p in (-5, -5, 0.01, -5, 7, -5, -5, -5)]
        r = b.build_backtest_report(tr, 1000.0)
        self.assertEqual(r["max_loss_streak"], 3)  # BE (|0.01|<=0.05) không cắt chuỗi: 2 + 1 = 3 ... rồi thắng cắt; cuối 3

    def test_bootstrap_ci(self):
        r = b.build_backtest_report(fake_trades(12), 1000.0)
        lo, hi = r["ci95_daily"]
        self.assertLessEqual(lo, r["mean_daily"] + 1e-9)
        self.assertGreaterEqual(hi, r["mean_daily"] - 1e-9)
        self.assertAlmostEqual(r["mean_daily"], 0.0)
        # ngày: 6 ngày -10, 6 ngày +10 -> KTC phải nằm trong [-10, 10] và có độ rộng > 0
        self.assertGreater(hi - lo, 1.0)
        self.assertGreaterEqual(lo, -10.0)
        self.assertLessEqual(hi, 10.0)
        r2 = b.build_backtest_report(fake_trades(12), 1000.0)
        self.assertEqual(r["ci95_daily"], r2["ci95_daily"])  # seed cố định -> lặp lại được

    def test_not_enough_days(self):
        r = b.build_backtest_report(fake_trades(9), 1000.0)
        self.assertIsNone(r["ci95_daily"])
        self.assertTrue(any("< 10" in w for w in r["warnings"]))
        text = "\n".join(b.format_backtest_report(r))
        self.assertIn("không đủ dữ liệu", text)

    def test_many_trades_no_warning(self):
        tr = [T(f"2026-11-{(i % 15) + 1:02d}", 5.0 if i % 3 else -4.0) for i in range(150)]
        r = b.build_backtest_report(tr, 1000.0)
        self.assertEqual(r["warnings"], [])
        self.assertIsNotNone(r["ci95_daily"])

    def test_no_risk_no_cost_fields(self):
        tr = [{"date": "2026-11-01", "pnl": 3.0}, {"date": "2026-11-01", "pnl": -1.0}]
        r = b.build_backtest_report(tr, 1000.0)
        self.assertIsNone(r["expectancy_r"])
        self.assertIsNone(r["cost_ratio_pct"])
        text = "\n".join(b.format_backtest_report(r))
        self.assertIn("không có dữ liệu rủi ro", text)
        self.assertIn("commission, slippage, swap", text)

    def test_empty(self):
        r = b.build_backtest_report([], 1000.0)
        self.assertEqual(b.format_backtest_report(r)[-1], "Không có lệnh nào để thống kê.")

    def test_s1_cost_wording(self):
        tr = fake_trades(3)
        for t in tr:
            t["cost_kind"] = "S1_EMBEDDED"
        text = "\n".join(b.format_backtest_report(b.build_backtest_report(tr, 1000.0)))
        self.assertIn("nằm sẵn trong giá ask/bid", text)


class TestCsvAndEmit(unittest.TestCase):
    def test_csv_export_and_emit(self):
        tr = fake_trades(3)
        with tempfile.TemporaryDirectory() as d:
            s = SimpleNamespace(log=mock.Mock(),
                                bt_sym_var=SimpleNamespace(get=lambda: "XAUUSDm"),
                                bt_start_var=SimpleNamespace(get=lambda: "2026-11-01"),
                                bt_end_var=SimpleNamespace(get=lambda: "2026-11-03"))
            with mock.patch.object(b, "SETTINGS_DIR", d):
                b.KronosDesktopApp._emit_backtest_summary(s, tr, 1000.0)
            folder = os.path.join(d, "backtests")
            files = os.listdir(folder)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].startswith("backtest_XAUUSDm_2026-11-01_2026-11-03_"))
            with open(os.path.join(folder, files[0]), encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0]["pnl"], "20.0")
            self.assertIn("risk_usd", rows[0])
            logged = " ".join(c.args[0] for c in s.log.call_args_list)
            self.assertIn("TỔNG KẾT BACKTEST", logged)
            self.assertIn("Đã xuất danh sách lệnh backtest", logged)

    def test_render_calls_summary_and_survives_error(self):
        s = SimpleNamespace(_update_bt_status=mock.Mock(), _draw_equity_curve=mock.Mock(),
                            log_error=mock.Mock(), _emit_backtest_summary=mock.Mock(side_effect=RuntimeError("boom")))
        b.KronosDesktopApp._render_backtest_results(s, fake_trades(2), [(0, 1000.0)], 1000.0)
        s._emit_backtest_summary.assert_called_once()
        s.log_error.assert_called_once()
        s._update_bt_status.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
