"""Test heikin_ashi_colors (thử nghiệm backtest exp_ha_trend / exp_ha_exit)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trading_modules.structure import heikin_ashi_colors


class TestHA(unittest.TestCase):
    def test_tang_deu_xanh(self):
        o = [100 + i for i in range(6)]
        c = [101 + i for i in range(6)]
        h = [x + 0.5 for x in c]
        l = [x - 0.5 for x in o]
        self.assertEqual(heikin_ashi_colors(o, h, l, c)[1:], [1] * 5)  # nến đầu râu đối xứng = doji

    def test_dao_chieu(self):
        o = [100, 101, 102, 103, 102, 100]
        c = [101, 102, 103, 102, 100, 98]
        h = [max(a, b) + 0.2 for a, b in zip(o, c)]
        l = [min(a, b) - 0.2 for a, b in zip(o, c)]
        col = heikin_ashi_colors(o, h, l, c)
        self.assertEqual(col[1:3], [1, 1])
        self.assertEqual(col[-2:], [-1, -1])

    def test_nhan_qua(self):
        # Thêm nến tương lai không làm đổi màu các nến cũ
        o = [100, 101, 102, 101]; c = [101, 102, 101, 100]
        h = [x + 1 for x in c]; l = [x - 1 for x in o]
        a = heikin_ashi_colors(o[:3], h[:3], l[:3], c[:3])
        b = heikin_ashi_colors(o, h, l, c)
        self.assertEqual(a, b[:3])


if __name__ == "__main__":
    unittest.main()
