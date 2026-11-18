"""Test A38: Time-Stop quy đổi giờ server MT5 về UTC (_server_offset_sec). Toàn bộ dùng mock."""
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


class TestServerOffset(unittest.TestCase):
    def setUp(self):
        self.tick_mock = mock.Mock()
        self.m = types.SimpleNamespace(symbol_info_tick=self.tick_mock)
        p = mock.patch.object(b, "mt5", self.m)
        p.start()
        self.addCleanup(p.stop)
        self.app = object.__new__(b.KronosDesktopApp)
        self.app.__dict__.update(_mt5_lock=threading.RLock())

    def test_server_gmt3_offset_10800(self):
        now = time.time()
        self.tick_mock.return_value = SimpleNamespace(time=int(now) + 10800)
        self.assertEqual(self.app._server_offset_sec("XAUUSDm"), 10800.0)

    def test_server_gmt2_offset_7200(self):
        now = time.time()
        self.tick_mock.return_value = SimpleNamespace(time=int(now) + 7200 - 3)  # tick có độ trễ vài giây
        self.assertEqual(self.app._server_offset_sec("XAUUSDm"), 7200.0)

    def test_khong_co_tick_tra_0(self):
        self.tick_mock.return_value = None
        self.assertEqual(self.app._server_offset_sec("XAUUSDm"), 0.0)

    def test_loi_mt5_tra_0(self):
        self.tick_mock.side_effect = RuntimeError("boom")
        self.assertEqual(self.app._server_offset_sec("XAUUSDm"), 0.0)



if __name__ == "__main__":
    unittest.main()
