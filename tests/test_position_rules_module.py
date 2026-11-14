"""step_position == vòng lặp gốc đóng băng (chép nguyên văn từ _run_backtest_simulation chế độ nến) trên đường giá ngẫu nhiên."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from trading_modules.position_rules import step_position


def legacy_loop(signal, entry_price, sl_price, tp_price, one_r_dist, bars, auto_be, auto_trail, be_trigger_r, trail_dist_r, be_plus_price, max_hold_mins):
    """Bản GỐC đóng băng. bars: list (f_high, f_low, f_close, hold_mins). Trả (out_price, exit_reason, sl_price, be_moved, số thanh đã chạy)."""
    be_moved = False
    out_price = entry_price
    exit_reason = "Đóng cuối phiên"
    n = 0
    for (f_high, f_low, f_close, hold_mins) in bars:
        n += 1
        if signal == "BUY":
            fav_dist = f_high - entry_price
            if auto_be and not be_moved and fav_dist >= one_r_dist * be_trigger_r:
                sl_price = max(sl_price, entry_price + be_plus_price)
                be_moved = True
            if auto_trail and be_moved and fav_dist >= one_r_dist * trail_dist_r:
                trail_sl = f_high - (one_r_dist * trail_dist_r)
                sl_price = max(sl_price, trail_sl)
            if max_hold_mins > 0 and hold_mins >= max_hold_mins and not be_moved:
                out_price = f_close
                exit_reason = "Time-Stop (Hết giờ)"
                break
            if f_low <= sl_price:
                out_price = sl_price
                exit_reason = "Hòa vốn (BE+)" if (be_moved and sl_price >= entry_price) else "Cắt lỗ (SL)"
                break
            elif f_high >= tp_price:
                out_price = tp_price
                exit_reason = "Chốt lãi (TP)"
                break
        else:
            fav_dist = entry_price - f_low
            if auto_be and not be_moved and fav_dist >= one_r_dist * be_trigger_r:
                sl_price = min(sl_price, entry_price - be_plus_price)
                be_moved = True
            if auto_trail and be_moved and fav_dist >= one_r_dist * trail_dist_r:
                trail_sl = f_low + (one_r_dist * trail_dist_r)
                sl_price = min(sl_price, trail_sl)
            if max_hold_mins > 0 and hold_mins >= max_hold_mins and not be_moved:
                out_price = f_close
                exit_reason = "Time-Stop (Hết giờ)"
                break
            if f_high >= sl_price:
                out_price = sl_price
                exit_reason = "Hòa vốn (BE+)" if (be_moved and sl_price <= entry_price) else "Cắt lỗ (SL)"
                break
            elif f_low <= tp_price:
                out_price = tp_price
                exit_reason = "Chốt lãi (TP)"
                break
    return out_price, exit_reason, sl_price, be_moved, n


def new_loop(signal, entry_price, sl_price, tp_price, one_r_dist, bars, auto_be, auto_trail, be_trigger_r, trail_dist_r, be_plus_price, max_hold_mins):
    be_moved = False
    out_price = entry_price
    exit_reason = "Đóng cuối phiên"
    n = 0
    for (f_high, f_low, f_close, hold_mins) in bars:
        n += 1
        sl_price, be_moved, ex_price, ex_reason = step_position(
            signal, entry_price, sl_price, tp_price, one_r_dist, be_moved, f_high, f_low, f_close, hold_mins,
            auto_be, auto_trail, be_trigger_r, trail_dist_r, be_plus_price, max_hold_mins)
        if ex_price is not None:
            out_price, exit_reason = ex_price, ex_reason
            break
    return out_price, exit_reason, sl_price, be_moved, n


class TestPositionRules(unittest.TestCase):
    def test_giong_vong_lap_goc(self):
        rng = np.random.default_rng(2026)
        counts = {}
        for k in range(3000):
            side = "BUY" if k % 2 == 0 else "SELL"
            entry = 2000.0
            one_r = float(rng.uniform(0.5, 5.0))
            sl = entry - one_r if side == "BUY" else entry + one_r
            tp = entry + 2 * one_r if side == "BUY" else entry - 2 * one_r
            nb = int(rng.integers(1, 40))
            px = entry + np.cumsum(rng.normal(0, one_r * 0.35, nb))
            bars = []
            for i in range(nb):
                c = float(px[i])
                h = c + abs(float(rng.normal(0, one_r * 0.3)))
                l = c - abs(float(rng.normal(0, one_r * 0.3)))
                bars.append((h, l, c, float(i + 1)))
            params = dict(auto_be=bool(rng.integers(0, 2)), auto_trail=bool(rng.integers(0, 2)),
                          be_trigger_r=float(rng.choice([0.5, 1.0, 1.5])), trail_dist_r=float(rng.choice([0.35, 1.0, 2.0])),
                          be_plus_price=float(rng.choice([0.0, 0.2, 1.0])), max_hold_mins=float(rng.choice([0.0, 5.0, 20.0])))
            a = legacy_loop(side, entry, sl, tp, one_r, bars, **params)
            b = new_loop(side, entry, sl, tp, one_r, bars, **params)
            self.assertEqual(a, b, f"ca {k}")
            counts[a[1]] = counts.get(a[1], 0) + 1
        for reason in ("Chốt lãi (TP)", "Cắt lỗ (SL)", "Hòa vốn (BE+)", "Time-Stop (Hết giờ)"):
            self.assertGreater(counts.get(reason, 0), 20, reason)


if __name__ == "__main__":
    unittest.main()
