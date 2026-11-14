"""Chia đôi Max SL vô cớ: RR phải đo tại điểm vào dự kiến (SL ± Max SL), không đo từ giá hiện tại."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

f = b.calc_effective_max_sl


def old_rule(entry_ref, sl, tp, max_sl):
    """Luật cũ (để chứng minh lỗi): đo từ giá hiện tại."""
    d, t = abs(entry_ref - sl), abs(tp - entry_ref)
    return round(max_sl / 2.0, 2) if t < 2.0 * d else max_sl


class TestEffectiveMaxSL(unittest.TestCase):
    def test_buy_sl_xa_hon_max_rr2_khong_chia_doi(self):
        # entry 2000, SL AI cách 8, Max SL 4, rr=2: điểm vào dự kiến = sl+4 = 1996, TP = 1996 + 4*2 = 2004
        sl, tp = 1992.0, 2004.0
        self.assertEqual(old_rule(2000.0, sl, tp, 4.0), 2.0)       # luật cũ: chia đôi vô cớ
        self.assertEqual(f("BUY", 2000.0, sl, tp, 4.0), (4.0, False))

    def test_sell_doi_xung(self):
        sl, tp = 2008.0, 1996.0   # điểm vào dự kiến 2004, TP = 2004 - 8
        self.assertEqual(old_rule(2000.0, sl, tp, 4.0), 2.0)
        self.assertEqual(f("SELL", 2000.0, sl, tp, 4.0), (4.0, False))

    def test_tp_that_su_thap_van_chia_doi(self):
        # TP chỉ cách điểm vào dự kiến 5 (< 2R của SL 4) -> vẫn chia đôi
        sl = 1992.0
        tp = 1996.0 + 5.0
        self.assertEqual(f("BUY", 2000.0, sl, tp, 4.0), (2.0, True))

    def test_sl_trong_max_khong_doi_cach_do(self):
        # SL cách 3 <= Max 4: đo tại giá hiện tại như cũ
        self.assertEqual(f("BUY", 2000.0, 1997.0, 2006.0, 4.0), (4.0, False))   # 6 >= 2*3
        self.assertEqual(f("BUY", 2000.0, 1997.0, 2005.0, 4.0), (2.0, True))    # 5 < 6

    def test_max_sl_khong_gioi_han(self):
        self.assertEqual(f("BUY", 2000.0, 1990.0, 2005.0, 0.0), (5.0, True))    # 0 = theo AI, TP < 2R -> chia đôi SL
        self.assertEqual(f("BUY", 2000.0, 1990.0, 2020.0, 0.0), (0.0, False))

    def test_action_khong_hop_le(self):
        self.assertEqual(f(None, 2000.0, 1990.0, 2020.0, 4.0), (4.0, False))


if __name__ == "__main__":
    unittest.main()
