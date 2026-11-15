"""nearest_swing_dist: SL dưới đáy swing gần nhất (BUY) / trên đỉnh swing gần nhất (SELL); không có swing -> None."""
import os, sys, unittest
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from trading_modules.structure import nearest_swing_dist


def mk(lows, highs):
    return pd.DataFrame({"low": lows, "high": highs})


class TestNearestSwing(unittest.TestCase):
    def test_buy_tim_day_swing_gan_nhat_duoi_gia(self):
        lows = [10, 9, 5, 9, 10, 9, 7, 9, 10, 11, 12]  # đáy swing 5 (i=2) và 7 (i=6)
        d = nearest_swing_dist(mk(lows, [x + 2 for x in lows]), "BUY", 12.0)
        self.assertAlmostEqual(d, 5.0)  # gần nhất = 7

    def test_sell_tim_dinh_swing_gan_nhat_tren_gia(self):
        highs = [10, 11, 15, 11, 10, 11, 13, 11, 10, 9, 8]
        d = nearest_swing_dist(mk([x - 2 for x in highs], highs), "SELL", 9.0)
        self.assertAlmostEqual(d, 4.0)  # đỉnh 13

    def test_khong_co_swing_tra_none(self):
        lows = [1, 2, 3, 4, 5, 6, 7, 8]
        self.assertIsNone(nearest_swing_dist(mk(lows, [x + 1 for x in lows]), "BUY", 20.0))
        self.assertIsNone(nearest_swing_dist(mk([1, 2], [2, 3]), "BUY", 5.0))
        self.assertIsNone(nearest_swing_dist(None, "BUY", 5.0))


if __name__ == "__main__":
    unittest.main()
