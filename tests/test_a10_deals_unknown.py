"""A10: không đọc được lịch sử deals (chưa có cache) -> chặn mở lệnh mới; đọc lại được thì mở khóa."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mt5_scalping_bot as b
import test_phase1 as t1

PK, SYM = "XAUUSD", "XAUUSDm"


def app():
    a = t1.make_app(
        profile_ctx={PK: {}}, active_profile_key=PK, _cached_deals_by_sym={},
        _resolve_profile_by_symbol=lambda s: PK, _get_profile_magic_number=lambda pk: t1.MAGIC,
        _resolve_position_magic=lambda pid, e=None: t1.MAGIC,
        _order_guard_lock=threading.Lock(), _order_in_flight={PK: False}, _last_order_open_mono={},
        _get_profile_cfg=lambda pk: {}, _is_daily_limit_locked=lambda pk=None: False,
        _is_cooldown_active=lambda pk=None: False)
    return a


class TestA10(t1.Base):
    def test_none_loi_khong_cache_dat_co_va_chan_lenh(self):
        self.m.history_deals_get.return_value = None
        self.m.last_error.return_value = (-1, "fail")
        a = app()
        self.assertEqual(a._get_today_deals_stats(symbol=SYM, profile_key=PK), (0.0, 0, None))
        self.assertTrue(a.profile_ctx[PK]["deals_unknown"])
        ok, msg = a.execute_mt5_order(SYM, "BUY", 0.01, 1990.0, 2010.0, 3, profile_key=PK)
        self.assertFalse(ok)
        self.assertIn("lịch sử", msg)
        self.m.order_send.assert_not_called()

    def test_none_ma_1_la_rong_khong_dat_co(self):
        self.m.history_deals_get.return_value = None
        self.m.last_error.return_value = (1, "Success")
        a = app()
        self.assertEqual(a._get_today_deals_stats(symbol=SYM, profile_key=PK), (0.0, 0, None))
        self.assertFalse(a.profile_ctx[PK].get("deals_unknown", False))

    def test_doc_lai_duoc_thi_mo_khoa(self):
        self.m.history_deals_get.return_value = None
        self.m.last_error.return_value = (-1, "fail")
        a = app()
        a._get_today_deals_stats(symbol=SYM, profile_key=PK)
        self.assertTrue(a.profile_ctx[PK]["deals_unknown"])
        self.m.history_deals_get.return_value = ()
        a._get_today_deals_stats(symbol=SYM, profile_key=PK)
        self.assertFalse(a.profile_ctx[PK]["deals_unknown"])

    def test_co_cache_cu_khong_chan(self):
        a = app()
        a._cached_deals_by_sym[f"{SYM}_{t1.MAGIC}"] = (-1000.0, (-5.0, 1, None))  # cache đã hết hạn 10s
        self.m.history_deals_get.return_value = None
        self.m.last_error.return_value = (-1, "fail")
        with mock.patch.object(b.time, "monotonic", return_value=100.0):
            self.assertEqual(a._get_today_deals_stats(symbol=SYM, profile_key=PK), (-5.0, 1, None))
        self.assertFalse(a.profile_ctx[PK].get("deals_unknown", False))


if __name__ == "__main__":
    unittest.main()
