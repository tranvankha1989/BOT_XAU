"""Test cổng ngày và đủ ngữ cảnh của _run_backtest_simulation (mock MT5, không chạy backtest thật)."""
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

DAY = 86400
START_TS = int(datetime(2026, 11, 9, tzinfo=b.VN_TZ).timestamp())  # 2026-11-09 là THỨ HAI (giờ VN)
SERIES_FROM = START_TS - 28 * DAY
SERIES_TO = START_TS + 3 * DAY
LOOKBACK = 450


def price(epoch):
    """Giá tăng đều theo thời gian (+2 mỗi 5 phút), dùng chung cho nến và tick để vùng SL khớp giá tick."""
    return 2000.0 + (epoch - SERIES_FROM) / 300.0 * 2.0


def series(tf_min):
    """Nến chỉ có từ thứ Hai đến thứ Sáu (giờ VN), nghỉ thứ Bảy/Chủ nhật."""
    step = tf_min * 60
    ts = [e for e in range(SERIES_FROM, SERIES_TO, step)
          if datetime.fromtimestamp(e, b.VN_TZ).weekday() < 5]
    dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                   ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
    a = np.zeros(len(ts), dtype=dt)
    a["time"] = ts
    px = np.array([price(e) for e in ts])
    a["open"], a["high"], a["low"], a["close"] = px, px + 0.5, px - 0.5, px + 0.4
    a["tick_volume"] = 100
    return a


def tick_array():
    """Tick mỗi 5 giây từ start-2h đến start+6h (giống khoảng _fetch_or_load_ticks thật: lùi 2 giờ trước start)."""
    dt = np.dtype([("time", "i8"), ("bid", "f8"), ("ask", "f8")])
    secs = np.arange(START_TS - 7200, START_TS + 6 * 3600, 5)
    t = np.zeros(len(secs), dtype=dt)
    t["time"] = secs
    t["bid"] = [price(e) for e in secs]
    t["ask"] = t["bid"] + 0.3
    return t


def fake_mt5():
    m = SimpleNamespace(TIMEFRAME_M1=1, TIMEFRAME_M5=5, TIMEFRAME_M15=15, TIMEFRAME_M30=30, TIMEFRAME_H1=60)
    cache = {}
    calls = []

    def copy_rates_range(sym, tf, d_from, d_to):
        calls.append((tf, int(d_from.timestamp()), int(d_to.timestamp())))
        arr = cache.setdefault(tf, series(tf))
        lo, hi = d_from.timestamp(), d_to.timestamp()
        return arr[(arr["time"] >= lo) & (arr["time"] <= hi)]

    m.copy_rates_range = copy_rates_range
    m.range_calls = calls
    return m


def run_sim(precision, use_ai, ana_tf="M15", scalp_tf="M5"):
    ai_calls, logs = [], []

    def fake_eval(predictor, df_ana, idx_ana, cfg, def_cfg, ana_tf_str, is_btc, atr, df_rates_scalp=None,
                  idx_scalp=0, scalp_tf_str="M5", cur_sec=None):
        ai_calls.append({"idx_ana": idx_ana, "idx_scalp": idx_scalp, "cur_sec": cur_sec})
        return None

    var = lambda v: SimpleNamespace(get=lambda: v)
    app = object.__new__(b.KronosDesktopApp)
    app.__dict__.update(
        bt_sym_var=var("XAUUSDm"), bt_start_var=var("2026-11-09"), bt_end_var=var("2026-11-09"),
        bt_data_precision_var=var("S1 (Tick 1 giây)" if precision == "S1" else "Nến MT5"),
        bt_use_ai_var=var(1 if use_ai else 0), bt_capital_var=var("1000"), bt_lot_var=var("0.02"),
        _get_profile_cfg=lambda pk: {"analysis_tf": ana_tf, "tf": scalp_tf, "lookback": str(LOOKBACK),
                                     "max_sl_price": "0", "max_hold_mins": "0"},
        root=SimpleNamespace(after=lambda ms, fn=None, *a: fn(*a) if fn else None),
        log=lambda msg, *a, **k: logs.append(str(msg)), show_toast=lambda *a, **k: None,
        _update_bt_status=lambda *a, **k: None, _push_live_bt_trade=lambda *a, **k: None,
        _render_backtest_results=lambda *a, **k: None,
        _bt_cancel_flag=False, is_analyzing=False, profile_ctx={}, _mt5_lock=threading.RLock(),
        _bt_trades_cache=[], _bt_equity_points=[],
        _fetch_or_load_ticks=lambda *a, **k: tick_array(),
        get_local_predictor=lambda m: object(), _evaluate_bt_ai_candle=fake_eval)
    m = fake_mt5()
    # Thư mục cấu hình tạm: tab Backtest có cấu hình riêng (backtest_settings.json), không được ghi vào APPDATA thật
    with tempfile.TemporaryDirectory() as tmp_dir, mock.patch.object(b, "SETTINGS_DIR", tmp_dir):
        with mock.patch.object(b, "mt5", m), mock.patch.object(b, "MT5_AVAILABLE", True), mock.patch.object(b.time, "sleep"):
            app._run_backtest_simulation()
    return SimpleNamespace(trades=list(app._bt_trades_cache), ai_calls=ai_calls, logs=logs, range_calls=m.range_calls)


def entry_dt(t):
    hms = t["in_time"].split(" ")[-1]
    if hms.count(":") == 1:
        hms += ":00"
    return datetime.strptime(f"{t['date']} {hms}", "%Y-%m-%d %H:%M:%S")


START_LABEL = datetime(2026, 11, 9, 0, 0, 0)


class TestDayGate(unittest.TestCase):
    def check_no_trade_before_start(self, res):
        self.assertTrue(res.trades, "không có lệnh nào -> test không có ý nghĩa")
        early = [t for t in res.trades if entry_dt(t) < START_LABEL]
        self.assertEqual(early, [], f"có lệnh mở trước start: {[(t['date'], t['in_time']) for t in early[:3]]}")

    def test_a_no_trades_before_start_candle(self):
        self.check_no_trade_before_start(run_sim("M5", use_ai=False))

    def test_a_no_trades_before_start_s1(self):
        self.check_no_trade_before_start(run_sim("S1", use_ai=False))

    def check_ai_gate(self, res):
        self.assertTrue(res.ai_calls, "AI không được gọi lần nào")
        early = [c for c in res.ai_calls if c["cur_sec"] < START_TS]
        self.assertEqual(early, [], f"AI bị gọi cho {len(early)} nến/giây trước start")

    def test_b_ai_not_called_before_start_candle(self):
        self.check_ai_gate(run_sim("M5", use_ai=True))

    def test_b_ai_not_called_before_start_s1(self):
        self.check_ai_gate(run_sim("S1", use_ai=True))

    def check_context(self, res):
        first = res.ai_calls[0]
        self.assertGreaterEqual(first["cur_sec"], START_TS)
        self.assertGreaterEqual(first["idx_ana"], LOOKBACK,
                                f"nến đầu tiên sau start chỉ có {first['idx_ana']} nến phân tích (< lookback {LOOKBACK})")
        self.assertGreaterEqual(first["idx_scalp"], 60)

    def test_c_first_candle_after_monday_start_has_full_lookback_candle(self):
        self.check_context(run_sim("M5", use_ai=True))

    def test_c_first_candle_after_monday_start_has_full_lookback_s1(self):
        self.check_context(run_sim("S1", use_ai=True))

    def test_warmup_window_is_by_bars_not_fixed_72h(self):
        res = run_sim("M5", use_ai=False, ana_tf="M5", scalp_tf="M5")
        tf, q_from, q_to = res.range_calls[0]
        # (450+10) nến M5 = 138000s; x1.5 + 3 ngày = 466200s (không phải 72h = 259200s)
        self.assertEqual(START_TS - q_from, 466200)
        res2 = run_sim("M5", use_ai=False, ana_tf="M15", scalp_tf="M5")
        self.assertEqual(START_TS - res2.range_calls[0][1], 10 * DAY)  # M15: chạm trần 10 ngày lịch

    def test_warmup_capped_and_shortfall_warned(self):
        res = run_sim("M5", use_ai=False, ana_tf="H1", scalp_tf="M5")
        self.assertEqual(START_TS - res.range_calls[0][1], 10 * DAY)  # không vượt 10 ngày lịch
        self.assertTrue(any("chưa đủ" in m and "H1" in m for m in res.logs), res.logs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
