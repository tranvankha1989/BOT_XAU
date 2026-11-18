"""Test nhận định cấu trúc đỉnh/đáy xoay (trading_modules/swing_structure.py)."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from trading_modules.swing_structure import analyze_swing_structure  # noqa: E402

# Mỗi nến: (high, low, open, close). Sóng giảm: đỉnh 110 -> 107, đáy 100 -> 97 (đỉnh/đáy xoay có L=2 mỗi bên)
DOWN_BARS = [
    (102, 100, 101, 101.5), (104, 101, 102, 103), (110, 103, 104, 108), (107, 102, 107, 103),
    (105, 101, 103, 102), (103, 100, 102, 101), (106, 101, 101, 105), (106, 103, 105, 104),
    (107, 104, 104, 105), (106, 102, 105, 103), (103, 97, 101, 99), (104, 99, 99, 103),
    (106, 100, 103, 105),
]


def _arrays(bars):
    a = np.array(bars, dtype=float)
    return a[:, 2], a[:, 0], a[:, 1], a[:, 3]  # opens, highs, lows, closes


def _mirror(bars):
    """Lật giá quanh 200: sóng giảm thành sóng tăng (kiểm tra đối xứng)."""
    return [(200 - lo, 200 - hi, 200 - op, 200 - cl) for (hi, lo, op, cl) in bars]


class TestSwingStructure(unittest.TestCase):
    def test_thieu_du_lieu(self):
        o, h, l, c = _arrays([(1, 0, 0.5, 0.6)] * 5)
        r = analyze_swing_structure(o, h, l, c, atr=1.0)
        self.assertIsNone(r["signal"])
        self.assertIn("thiếu", r["reason"])

    def test_atr_khong_hop_le(self):
        o, h, l, c = _arrays(DOWN_BARS)
        self.assertIsNone(analyze_swing_structure(o, h, l, c, atr=0.0)["signal"])

    def test_song_giam_hoi_ve_khang_cu_bi_tu_choi_thi_sell(self):
        bars = DOWN_BARS + [(106.9, 103, 103, 106.2), (106.9, 104, 106.5, 105)]
        o, h, l, c = _arrays(bars)
        r = analyze_swing_structure(o, h, l, c, atr=2.0)
        self.assertEqual(r["trend"], "down", r)
        self.assertEqual(r["resistance"], 107.0)
        self.assertEqual(r["signal"], "SELL", r["reason"])

    def test_song_giam_vuot_dinh_hoi_thi_buy_doi_cau_truc(self):
        bars = DOWN_BARS + [(106.9, 103, 103, 106.2), (115, 105, 106, 114.5)]
        o, h, l, c = _arrays(bars)
        r = analyze_swing_structure(o, h, l, c, atr=2.0)
        self.assertEqual(r["trend"], "down", r)
        self.assertEqual(r["signal"], "BUY", r["reason"])

    def test_song_giam_chua_hoi_thi_khong_lam_gi(self):
        # sau đáy 97 giá chỉ đi ngang dưới kháng cự 107, không có đợt hồi chạm vùng
        bars = DOWN_BARS[:11] + [(101, 98, 99, 100), (100, 98, 100, 99)]
        o, h, l, c = _arrays(bars)
        r = analyze_swing_structure(o, h, l, c, atr=2.0)
        self.assertEqual(r["trend"], "down", r)
        self.assertIsNone(r["signal"])

    def test_guong_song_tang_hoi_ve_ho_tro_giu_gia_thi_buy(self):
        bars = _mirror(DOWN_BARS + [(106.9, 103, 103, 106.2), (106.9, 104, 106.5, 105)])
        o, h, l, c = _arrays(bars)
        r = analyze_swing_structure(o, h, l, c, atr=2.0)
        self.assertEqual(r["trend"], "up", r)
        self.assertEqual(r["signal"], "BUY", r["reason"])

    def test_guong_song_tang_thung_day_thi_sell_doi_cau_truc(self):
        bars = _mirror(DOWN_BARS + [(106.9, 103, 103, 106.2), (115, 105, 106, 114.5)])
        o, h, l, c = _arrays(bars)
        r = analyze_swing_structure(o, h, l, c, atr=2.0)
        self.assertEqual(r["trend"], "up", r)
        self.assertEqual(r["signal"], "SELL", r["reason"])

    def test_di_ngang_khong_co_tin_hieu(self):
        pts = [(105, 100, 102, 103), (108, 102, 104, 107), (104, 98, 100, 99), (110, 103, 104, 109),
               (103, 99, 101, 100), (109, 101, 103, 107), (105, 99, 100, 101), (107, 100, 102, 104),
               (106, 101, 103, 105), (105, 100, 102, 103), (106, 101, 102, 104)]
        o, h, l, c = _arrays(pts)
        r = analyze_swing_structure(o, h, l, c, atr=2.0)
        self.assertEqual(r["trend"], "range", r)
        self.assertIsNone(r["signal"])


if __name__ == "__main__":
    unittest.main()
