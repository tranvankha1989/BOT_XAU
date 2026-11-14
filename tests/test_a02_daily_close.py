"""Test A02: sau khi chạm ngưỡng lãi/lỗ ngày, kiểm tra kết quả đóng lệnh bot và thử lại. Toàn bộ mock."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

PK = "XAUUSD"
SYM = "XAUUSDm"
MAGIC = 111


def pos(ticket, magic):
    return SimpleNamespace(ticket=ticket, magic=magic, symbol=SYM, volume=0.01, profit=-1.0)


def make_app(closer):
    ctx = {"cooldown_until": None, "last_cooldown_deal_ticket": None, "fast_sl_cooldown_until": None,
           "last_fast_sl_deal_ticket": None, "daily_limit_locked_date": None, "daily_limit_reason": "",
           "daily_limit_locked_kind": None, "daily_limit_locked_threshold": 0.0, "pending_pullback_order": None,
           "cached_daily_pnl": 0.0, "consec_loss_count": 0}
    cfg = {"symbol": SYM, "daily_sl_usd": 10.0, "daily_tp_usd": 0.0, "max_consec_loss": 0, "cooldown_mins": 0,
           "fast_sl_cooldown_candles": 0, "analysis_tf": "M5"}
    app = object.__new__(b.KronosDesktopApp)
    app.__dict__.update(
        active_profile_key=PK, profile_ctx={PK: ctx}, auto_loop_running=False, _mt5_lock=threading.RLock(),
        _get_profile_cfg=lambda pk: dict(cfg), _get_tf_minutes=lambda s: 5,
        _get_today_deals_stats=lambda **k: (-50.0, 0, None),
        _get_profile_magic_number=lambda pk: MAGIC,
        _close_single_pos=closer, _cached_deals_by_sym={}, _cached_closed_deals_map={},
        log=mock.Mock(), show_toast=mock.Mock(), _write_file_log=mock.Mock(),
        daily_limit_locked_date=None, daily_limit_reason="", daily_limit_locked_kind=None, daily_limit_locked_threshold=0.0,
        pending_pullback_order=None, cooldown_until=None, fast_sl_cooldown_until=None,
        last_cooldown_deal_ticket=None, last_fast_sl_deal_ticket=None, cached_daily_pnl=0.0, consec_loss_count=0,
    )
    return app, ctx


class TestA02(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.open = [pos(1, MAGIC), pos(2, MAGIC), pos(9, 0)]  # 9 = lệnh tay (magic 0)
        self.fail_first = {1}
        self.closed = []
        self.attempts = {}
        m = SimpleNamespace(positions_get=lambda **k: list(self.open), last_error=lambda: (1, "ok"))
        for p in (mock.patch.object(b, "mt5", m), mock.patch.object(b, "MT5_AVAILABLE", True),
                  mock.patch.object(b.time, "monotonic", side_effect=lambda: self.clock[0])):
            p.start()
            self.addCleanup(p.stop)

    def closer(self, pos_):
        self.closed.append(pos_.ticket)
        self.attempts[pos_.ticket] = self.attempts.get(pos_.ticket, 0) + 1
        if pos_.ticket in self.fail_first and self.attempts[pos_.ticket] == 1:
            return False, "that bai"
        self.open = [x for x in self.open if x.ticket != pos_.ticket]
        return True, "ok"

    def run_once(self, app):
        app._check_daily_profit_loss_limits(-5.0, tick=None, profile_key=PK)

    def test_that_bai_roi_thanh_cong_va_khong_dong_lenh_tay(self):
        app, ctx = make_app(self.closer)
        self.run_once(app)  # chạm ngưỡng: #1 thất bại, #2 thành công
        self.assertTrue(ctx["daily_limit_pending_close"])
        self.assertEqual(sorted(self.closed), [1, 2])
        self.assertNotIn(9, self.closed)
        self.assertEqual(ctx["daily_limit_locked_kind"], "SL")  # điều kiện khóa như cũ
        # chưa đủ 5 giây -> không thử lại
        self.clock[0] += 4.0
        self.run_once(app)
        self.assertEqual(self.attempts[1], 1)
        # đủ 5 giây -> thử lại, #1 thành công, xóa cờ
        self.clock[0] += 1.5
        self.run_once(app)
        self.assertEqual(self.attempts[1], 2)
        self.assertFalse(ctx["daily_limit_pending_close"])
        self.assertEqual([x.ticket for x in self.open], [9])  # chỉ còn lệnh tay
        self.assertNotIn(9, self.closed)
        # hết lệnh bot: không còn thử lại
        self.clock[0] += 10.0
        n = len(self.closed)
        self.run_once(app)
        self.assertEqual(len(self.closed), n)

    def test_thu_lai_khong_qua_1_lan_moi_5_giay_va_log_15_giay(self):
        self.fail_first = {1}
        always_fail = lambda p_: (self.closed.append(p_.ticket) or (False, "x"))
        app, ctx = make_app(always_fail)
        self.open = [pos(1, MAGIC), pos(9, 0)]
        self.run_once(app)  # lần đầu (t=1000)
        self.assertEqual(self.closed.count(1), 1)
        for dt in (1, 1, 1, 1):  # t=1001..1004: chưa đủ 5s
            self.clock[0] += 1.0
            self.run_once(app)
        self.assertEqual(self.closed.count(1), 1)
        self.clock[0] = 1005.0
        self.run_once(app)
        self.assertEqual(self.closed.count(1), 2)
        for t in (1006.0, 1010.0, 1014.0):
            self.clock[0] = t
            self.run_once(app)
        self.assertEqual(self.closed.count(1), 3)  # t=1010
        warn = [c.args[0] for c in app.log.call_args_list if "chưa đóng được -> thử lại" in str(c.args[0])]
        self.assertLessEqual(len(warn), 1)  # log thử lại <= 1 lần / 15s (t=1005 .. 1014)
        self.assertNotIn(9, self.closed)

    def test_mo_khoa_thi_ngung_thu_lai(self):
        always_fail = lambda p_: (self.closed.append(p_.ticket) or (False, "x"))
        app, ctx = make_app(always_fail)
        self.open = [pos(1, MAGIC)]
        self.run_once(app)
        ctx["daily_limit_locked_date"] = "1999-01-01"  # sang ngày mới -> mở khóa
        app._get_today_deals_stats = lambda **k: (0.0, 0, None)  # PnL ngày mới không còn chạm ngưỡng
        self.clock[0] += 6.0
        n = len(self.closed)
        self.run_once(app)
        self.assertFalse(ctx.get("daily_limit_pending_close"))
        self.assertEqual(len(self.closed), n)


if __name__ == "__main__":
    unittest.main()
