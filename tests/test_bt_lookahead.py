import os
import sys, unittest, threading
from types import SimpleNamespace
from unittest import mock
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

T0, TF = 1_700_000_100, 300
N = 100
def make_rates():
    dt = np.dtype([("time","i8"),("open","f8"),("high","f8"),("low","f8"),("close","f8"),
                   ("tick_volume","i8"),("spread","i4"),("real_volume","i8")])
    a = np.zeros(N, dtype=dt)
    a["time"] = T0 + np.arange(N) * TF
    px = 2000 + np.arange(N) * 0.1
    a["open"], a["high"], a["low"], a["close"] = px, px + 1, px - 1, px + 0.05
    a["tick_volume"] = 100
    return a
def make_ticks():
    dt = np.dtype([("time","i8"),("bid","f8"),("ask","f8")])
    secs = []
    for bar in range(55, 75):
        base = T0 + bar * TF
        secs += [base, base + 60, base + 120, base + 200]
    t = np.zeros(len(secs), dtype=dt); t["time"] = secs; t["bid"] = 2005.0; t["ask"] = 2005.3
    return t

def run(precision):
    rates = make_rates()
    calls = []
    def fake_eval(predictor, df_ana, idx_ana, cfg, def_cfg, ana_tf, is_btc, atr, df_rates_scalp=None, idx_scalp=0, scalp_tf_str="M5", cur_sec=None):
        calls.append((idx_scalp, cur_sec, df_rates_scalp["time"].values.copy()))
        return None
    var = lambda v: SimpleNamespace(get=lambda: v)
    s = SimpleNamespace(
        bt_sym_var=var("XAUUSDm"), bt_start_var=var("2023-11-14"), bt_end_var=var("2023-11-15"),
        bt_data_precision_var=var(precision), bt_use_ai_var=var(1), bt_capital_var=var("1000"), bt_lot_var=var("0.02"),
        _get_profile_cfg=lambda pk: {"analysis_tf": "M5", "tf": "M5"},
        root=SimpleNamespace(after=lambda *a, **k: None), _bt_cancel_flag=False, is_analyzing=False,
        profile_ctx={}, _mt5_lock=threading.RLock(), log=mock.Mock(),
        get_local_predictor=lambda m: object(),
        _fetch_or_load_ticks=lambda *a, **k: make_ticks(),
        _resample_ticks_to_s1=lambda t: b.KronosDesktopApp._resample_ticks_to_s1(None, t),
        _evaluate_bt_ai_candle=fake_eval)
    with mock.patch.object(b, "mt5") as m, mock.patch.object(b, "MT5_AVAILABLE", True), mock.patch.object(b.time, "sleep"):
        m.copy_rates_range.return_value = rates
        b.KronosDesktopApp._run_backtest_simulation(s)
    return calls

class TestLookahead(unittest.TestCase):
    def check(self, calls):
        self.assertTrue(calls, "AI không được gọi")
        for idx_scalp, cur_sec, times in calls:
            last_incl_open = int(times[idx_scalp - 1])
            self.assertLessEqual(last_incl_open + TF, cur_sec, f"nến scalp chưa đóng lọt vào dữ liệu (open={last_incl_open}, cur={cur_sec})")
    def test_s1_mode(self):
        self.check(run("S1"))
    def test_candle_mode(self):
        self.check(run("M5"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
