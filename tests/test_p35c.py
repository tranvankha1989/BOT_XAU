"""Test A37 (_modify_position_sltp đọc lại vị thế, không nới SL) và A39 (_get_fresh_tick). Toàn bộ dùng mock."""
import os
import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

SYM = "XAUUSDm"


def pos(ticket=1, typ=0, sl=0.0, tp=0.0):
    return SimpleNamespace(ticket=ticket, symbol=SYM, type=typ, sl=sl, tp=tp, magic=777)


class TestModifySltp(unittest.TestCase):
    def setUp(self):
        self.m = types.SimpleNamespace(
            POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1, TRADE_ACTION_SLTP=2,
            symbol_info=mock.Mock(return_value=SimpleNamespace(digits=2)),
            symbol_info_tick=mock.Mock())
        p = mock.patch.object(b, "mt5", self.m)
        p.start()
        self.addCleanup(p.stop)
        self.app = object.__new__(b.KronosDesktopApp)
        self.app.__dict__.update(
            _mt5_lock=threading.RLock(), _modify_fail_until={}, _modify_fail_log_ts={},
            log=mock.Mock(), _write_file_log=mock.Mock(),
            _send_mt5_order_with_retry=mock.Mock(return_value=(True, None, "")))
        self.fresh = [pos(sl=2000.0, tp=2010.0)]
        self.app._positions_get_checked = mock.Mock(side_effect=lambda **k: self.fresh)

    def sent(self):
        return self.app._send_mt5_order_with_retry.call_args[0][0] if self.app._send_mt5_order_with_retry.called else None

    def test_khong_ghi_de_be(self):
        # snapshot cũ SL=1990, TP=2010; thực tế SL đã dời lên 2000 (BE). Gọi đổi TP -> giữ SL=2000
        self.fresh = [pos(sl=2000.0, tp=2010.0)]
        old = pos(sl=1990.0, tp=2010.0)
        self.assertTrue(self.app._modify_position_sltp(old, 1990.0, 2005.0))
        self.assertEqual((self.sent()["sl"], self.sent()["tp"]), (2000.0, 2005.0))

    def test_khong_noi_sl(self):
        self.fresh = [pos(typ=0, sl=2000.0, tp=2010.0)]
        old = pos(typ=0, sl=1990.0, tp=2010.0)
        self.assertFalse(self.app._modify_position_sltp(old, 1995.0, 2010.0))  # BUY: SL mới < SL hiện tại
        self.assertIsNone(self.sent())
        self.fresh = [pos(typ=1, sl=2000.0, tp=1990.0)]
        old = pos(typ=1, sl=2010.0, tp=1990.0)
        self.assertFalse(self.app._modify_position_sltp(old, 2005.0, 1990.0))  # SELL: SL mới > SL hiện tại
        self.assertIsNone(self.sent())

    def test_cho_phep_noi_khi_co_co(self):
        self.fresh = [pos(typ=0, sl=2000.0, tp=2010.0)]
        old = pos(typ=0, sl=2000.0, tp=2010.0)
        self.assertTrue(self.app._modify_position_sltp(old, 1995.0, 2010.0, allow_widen_sl=True))
        self.assertEqual(self.sent()["sl"], 1995.0)

    def test_siet_sl_van_gui(self):
        self.fresh = [pos(typ=0, sl=1990.0, tp=2010.0)]
        self.assertTrue(self.app._modify_position_sltp(pos(typ=0, sl=1990.0, tp=2010.0), 1998.0, 2010.0))
        self.assertEqual(self.sent()["sl"], 1998.0)

    def test_vi_the_da_dong(self):
        self.fresh = []
        self.assertFalse(self.app._modify_position_sltp(pos(sl=1990.0, tp=2010.0), 1998.0, 2010.0))
        self.assertIsNone(self.sent())
        self.app.log.assert_called()

    def test_loi_doc_vi_the(self):
        self.fresh = None
        self.assertFalse(self.app._modify_position_sltp(pos(sl=1990.0, tp=2010.0), 1998.0, 2010.0))
        self.assertIsNone(self.sent())

    def test_gia_tri_khong_doi_khong_gui(self):
        # snapshot khác nhưng vị thế thật đã có đúng giá trị muốn đặt
        self.fresh = [pos(sl=2000.0, tp=2010.0)]
        self.assertTrue(self.app._modify_position_sltp(pos(sl=1990.0, tp=2010.0), 2000.0, 2010.0))
        self.assertIsNone(self.sent())
        # trùng ngay snapshot
        self.assertTrue(self.app._modify_position_sltp(pos(sl=2000.0, tp=2010.0), 2000.0, 2010.0))
        self.assertIsNone(self.sent())


class TestFreshTick(unittest.TestCase):
    def setUp(self):
        self.m = types.SimpleNamespace(symbol_info_tick=mock.Mock())
        p = mock.patch.object(b, "mt5", self.m)
        p.start()
        self.addCleanup(p.stop)
        self.app = object.__new__(b.KronosDesktopApp)
        self.app.__dict__.update(_mt5_lock=threading.RLock())

    def tick(self, age, offset=3 * 3600):
        return SimpleNamespace(time=int(time.time()) + offset - age, bid=2000.0, ask=2000.3)

    def test_none(self):
        self.m.symbol_info_tick.return_value = None
        self.assertIsNone(self.app._get_fresh_tick(SYM))

    def test_moi(self):
        self.m.symbol_info_tick.return_value = self.tick(1)
        self.assertIsNotNone(self.app._get_fresh_tick(SYM))

    def test_cu(self):
        for off in (3 * 3600, 0, -5 * 3600):
            self.m.symbol_info_tick.return_value = self.tick(60, off)
            self.assertIsNone(self.app._get_fresh_tick(SYM))


if __name__ == "__main__":
    unittest.main()
