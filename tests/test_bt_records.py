import os
import sys, threading, unittest
from types import SimpleNamespace
from unittest import mock
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

T0, TF, N = 1_700_000_100, 300, 120


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


def run(precision):
    var = lambda v: SimpleNamespace(get=lambda: v)
    s = SimpleNamespace(
        bt_sym_var=var("XAUUSDm"), bt_start_var=var("2023-11-14"), bt_end_var=var("2023-11-15"),
        bt_data_precision_var=var(precision), bt_use_ai_var=var(0), bt_capital_var=var("1000"), bt_lot_var=var("0.02"),
        _get_profile_cfg=lambda pk: {"analysis_tf": "M5", "tf": "M5", "max_sl_price": 0.0, "max_hold_mins": 0},
        root=SimpleNamespace(after=lambda *a, **k: None), _bt_cancel_flag=False, is_analyzing=False,
        profile_ctx={}, _mt5_lock=threading.RLock(), log=mock.Mock(),
        _fetch_or_load_ticks=lambda *a, **k: ticks(),
        _resample_ticks_to_s1=lambda t: b.KronosDesktopApp._resample_ticks_to_s1(None, t),
        _format_duration=lambda x: str(x), _push_live_bt_trade=mock.Mock(),
        _bt_trades_cache=[], _bt_equity_points=[])
    with mock.patch.object(b, "mt5") as m, mock.patch.object(b, "MT5_AVAILABLE", True), mock.patch.object(b.time, "sleep"):
        m.copy_rates_range.return_value = rates()
        b.KronosDesktopApp._run_backtest_simulation(s)
    return s._bt_trades_cache


class TestRecords(unittest.TestCase):
    def check(self, trades, kind):
        self.assertTrue(trades, "không sinh lệnh nào")
        t = trades[0]
        self.assertRegex(t["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertGreater(t["risk_usd"], 0)
        self.assertEqual(t["cost_kind"], kind)
        self.assertGreaterEqual(t["cost_spread"], 0)
        rep = b.build_backtest_report(trades, 1000.0)
        self.assertEqual(rep["n_trades"], len(trades))
        self.assertIsNotNone(rep["expectancy_r"])

    def test_candle(self):
        self.check(run("M5"), "CANDLE_EXPLICIT")

    def test_s1(self):
        self.check(run("S1"), "S1_EMBEDDED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
