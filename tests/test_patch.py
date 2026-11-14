import os
import sys, threading, unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

TZ = timezone(timedelta(hours=7))
def blocked(ts, win):
    return b.is_trading_window_blocked(datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=TZ), {"blocked_trading_windows": win})[0]

class TestWindow(unittest.TestCase):
    # 2026-11-12 = Thursday, 2026-11-13 = Friday
    def test_weekly_overnight(self):
        w = "THU 22:00-02:00"
        self.assertTrue(blocked("2026-11-12 22:00", w))
        self.assertTrue(blocked("2026-11-12 23:59", w))
        self.assertTrue(blocked("2026-11-13 00:00", w))
        self.assertTrue(blocked("2026-11-13 02:00", w))
        self.assertFalse(blocked("2026-11-13 02:01", w))
        self.assertFalse(blocked("2026-11-12 21:59", w))
        self.assertFalse(blocked("2026-11-12 01:00", w))  # sáng thứ Năm không chặn
        self.assertFalse(blocked("2026-11-14 01:00", w))
    def test_weekly_normal_and_minutes_not_string(self):
        self.assertTrue(blocked("2026-11-12 9:30", "THU 9:00-10:00"))   # "H:MM" không đệm 0
        self.assertFalse(blocked("2026-11-12 10:01", "THU 9:00-10:00"))
        self.assertFalse(blocked("2026-11-13 09:30", "THU 9:00-10:00"))
        self.assertTrue(blocked("2026-11-12 10:00", "T5 9:00-10:00"))
    def test_fixed_date(self):
        self.assertTrue(blocked("2026-11-12 19:30", "2026-11-12 19:25-20:00"))
        self.assertFalse(blocked("2026-11-12 20:01", "2026-11-12 19:25-20:00"))
        w = "2026-11-12 22:00-02:00"
        self.assertTrue(blocked("2026-11-12 23:00", w))
        self.assertTrue(blocked("2026-11-13 01:30", w))
        self.assertFalse(blocked("2026-11-13 02:01", w))
        self.assertFalse(blocked("2026-11-12 01:00", w))
        self.assertFalse(blocked("2026-11-14 01:00", w))
    def test_daily(self):
        self.assertTrue(blocked("2026-11-12 5:00", "4:00-6:00"))
        self.assertFalse(blocked("2026-11-12 6:01", "4:00-6:00"))
        self.assertTrue(blocked("2026-11-12 23:00", "22:00-02:00"))
        self.assertTrue(blocked("2026-11-13 01:00", "22:00-02:00"))
        self.assertFalse(blocked("2026-11-13 12:00", "22:00-02:00"))
    def test_multi(self):
        self.assertTrue(blocked("2026-11-12 05:00", "THU 22:00-02:00; 04:00-06:00"))

class TestModify(unittest.TestCase):
    def mk(self, ok):
        s = SimpleNamespace(_mt5_lock=threading.RLock(), _modify_fail_until={}, _modify_fail_log_ts={},
                            log=mock.Mock(), _send_mt5_order_with_retry=mock.Mock(return_value=(ok, None, "err")),
                            _positions_get_checked=lambda **k: [self.pos()])
        return s
    def pos(self): return SimpleNamespace(ticket=7, symbol="XAUUSD", sl=1.0, tp=2.0, type=0)
    def run_(self, s, now):
        with mock.patch.object(b, "mt5") as m, mock.patch.object(b.time, "monotonic", return_value=now):
            m.symbol_info.return_value = SimpleNamespace(digits=2); m.POSITION_TYPE_BUY = 0
            return b.KronosDesktopApp._modify_position_sltp(s, self.pos(), 1.5, 2.5)
    def test_fail_backoff_and_log_limit(self):
        s = self.mk(False)
        self.assertFalse(self.run_(s, 100.0))
        self.assertEqual(s._modify_fail_until[7], 115.0)
        self.assertEqual(s._send_mt5_order_with_retry.call_count, 1)
        self.assertFalse(self.run_(s, 105.0))            # trong thời gian chờ -> không gửi
        self.assertEqual(s._send_mt5_order_with_retry.call_count, 1)
        self.assertEqual(s.log.call_count, 1)
        self.assertFalse(self.run_(s, 116.0))            # hết chờ -> gửi lại, log lại (>=15s)
        self.assertEqual(s._send_mt5_order_with_retry.call_count, 2)
        self.assertEqual(s.log.call_count, 2)
    def test_success_clears(self):
        s = self.mk(True); s._modify_fail_until[7] = 50.0
        self.assertTrue(self.run_(s, 100.0))
        self.assertNotIn(7, s._modify_fail_until)

if __name__ == "__main__":
    unittest.main(verbosity=2)
