"""A03: nhánh không khớp/zone_watch không được ghi đè pending mới do luồng quét AI đặt trong lúc execute_mt5_order."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mt5_scalping_bot as b
import test_rr_gate as g

PK = g.PK


def scenario(zone_watch, ok, new_pending, msg="ok"):
    s, p, tick = g.mk(tp=104.0)
    p["manual_queued"] = False
    p["zone_watch"] = zone_watch
    newp = {"symbol": "XAUUSD", "action": "SELL", "sl_price": 110.0, "id": "moi"}

    def fake_exec(*a, **k):
        if new_pending:  # luồng quét AI đặt vùng mới trong lúc đang gửi lệnh
            s.profile_ctx[PK]["pending_pullback_order"] = newp
            s.pending_pullback_order = newp
        return (ok, msg)
    s.execute_mt5_order = mock.Mock(side_effect=fake_exec)
    g.run(s, tick)
    return s, p, newp


class TestA03(unittest.TestCase):
    def test_zone_watch_thanh_cong_khong_de_pending_moi(self):
        s, p, newp = scenario(True, True, True)
        self.assertIs(s.profile_ctx[PK]["pending_pullback_order"], newp)
        self.assertIs(s.pending_pullback_order, newp)

    def test_khong_khop_khong_de_pending_moi(self):
        s, p, newp = scenario(True, False, True, msg="vuong khoa")
        self.assertIs(s.profile_ctx[PK]["pending_pullback_order"], newp)
        self.assertIs(s.pending_pullback_order, newp)

    def test_khong_khop_khong_co_pending_moi_van_giu_vung_cu(self):
        s, p, newp = scenario(True, False, False, msg="vuong khoa")
        self.assertIs(s.profile_ctx[PK]["pending_pullback_order"], p)
        self.assertIs(s.pending_pullback_order, p)

    def test_zone_watch_thanh_cong_khong_co_pending_moi_duy_tri_vung(self):
        s, p, newp = scenario(True, True, False)
        got = s.profile_ctx[PK]["pending_pullback_order"]
        self.assertIsNotNone(got)
        self.assertEqual(got["sl_price"], p["sl_price"])
        self.assertIs(s.pending_pullback_order, got)

    def test_khong_zone_watch_thanh_cong_xoa_pending(self):
        s, p, newp = scenario(False, True, False)
        self.assertIsNone(s.profile_ctx[PK]["pending_pullback_order"])


if __name__ == "__main__":
    unittest.main()
