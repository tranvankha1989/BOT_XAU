"""Trình phát lại backtest (trading_modules/replay_player.py): hàm tính + dựng cửa sổ Tk thật (bỏ qua nếu máy không có màn hình)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trading_modules import replay_player as rp


def series(n=600, t0=1_762_732_800):
    sec = np.arange(t0, t0 + n, dtype=np.int64)
    c = 4100 + np.sin(np.arange(n) / 20.0)
    o = np.r_[c[0], c[:-1]]
    return sec, o, np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05, c


class TestCalc(unittest.TestCase):
    def test_aggregate_m1(self):
        sec, o, h, l, c = series(600)
        b = rp.aggregate_bars(sec, o, h, l, c, 60)
        self.assertEqual(len(b["start"]), 10)
        self.assertAlmostEqual(b["h"][0], h[:60].max())
        self.assertAlmostEqual(b["l"][3], l[180:240].min())
        self.assertAlmostEqual(b["c"][9], c[-1])
        self.assertEqual(int(b["bar_of"][61]), 1)

    def test_aggregate_gap(self):
        sec = np.array([0, 1, 2, 125, 126], dtype=np.int64)  # thiếu giây (không có tick) vẫn gộp đúng nến
        v = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        b = rp.aggregate_bars(sec, v, v, v, v, 60)
        self.assertEqual(list(b["start"]), [0, 120])
        self.assertEqual(list(b["c"]), [3.0, 5.0])

    def test_forming_bar_no_lookahead(self):
        sec, o, h, l, c = series(120)
        b = rp.aggregate_bars(sec, o, h, l, c, 60)
        k, fo, fh, fl, fc = rp.forming_bar(b, h, l, c, 70)
        self.assertEqual(k, 1)
        self.assertAlmostEqual(fh, h[60:71].max())
        self.assertAlmostEqual(fc, c[70])
        self.assertLessEqual(fh, b["h"][1])

    def test_sltp_at(self):
        g = rp.group_sltp([(2000, 7, 1.0, 9.0), (1000, 7, 0.5, 9.0), (1500, 8, 2.0, 3.0)])
        self.assertIsNone(rp.sltp_at(g, 7, 999))
        self.assertEqual(rp.sltp_at(g, 7, 1500), (0.5, 9.0))
        self.assertEqual(rp.sltp_at(g, 7, 2500), (1.0, 9.0))

    def test_floating_pnl(self):
        self.assertAlmostEqual(rp.floating_pnl({"type": "BUY", "price_open": 100.0, "volume": 0.02}, 101.0, 100.0), 2.0)
        self.assertAlmostEqual(rp.floating_pnl({"type": "SELL", "price_open": 100.0, "volume": 0.02}, 101.0, 100.0), -2.0)


class TestPlayerWindow(unittest.TestCase):
    def test_build_render_controls(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as e:  # máy không có màn hình
            self.skipTest(f"Không dựng được Tk: {e}")
        try:
            root.withdraw()
            sec, o, h, l, c = series(3600)
            t0 = float(sec[0])
            trades = [{"ticket": 11, "type": "BUY", "open_ms": (t0 + 600) * 1000, "close_ms": (t0 + 900) * 1000,
                       "price_open": float(c[600]), "price_close": float(c[900]), "volume": 0.02, "profit": 1.5}]
            sltp = [((t0 + 600) * 1000, 11, float(c[600]) - 2, float(c[600]) + 4)]
            p = rp.ReplayPlayer(root, {"sec": sec, "o": o, "h": h, "l": l, "c": c}, trades, sltp, t0 + 300, t0 + 3500)
            p.jump_to_trade(11)
            self.assertAlmostEqual(p.t, t0 + 600.5)
            p.render()
            p.step_forward()
            p.render()
            p.step_back()
            p.set_tf("M5")
            p.zoom(2.0)
            p.render()
            p.set_speed("600x")
            self.assertEqual(p.speed, 600)
            p.next_trade()  # không còn lệnh sau -> giữ nguyên
            p.close()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
