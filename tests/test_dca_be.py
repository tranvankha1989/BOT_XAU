"""Nhồi giá tốt hơn (DCA): lệnh đã dời BE không còn làm mốc so giá cho lệnh mới (mock, không MT5 thật)."""
import os
import sys
import threading
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_phase1 import Base, make_app, PK, SYM, MAGIC  # noqa: E402


def buy_pos(ticket, price_open):
    return SimpleNamespace(ticket=ticket, magic=MAGIC, type=0, volume=0.01, price_open=price_open, sl=0.0, tp=0.0)


class TestDcaIgnoresBe(Base):
    def app(self, be_tickets):
        res = SimpleNamespace(price=2000.3, order=55, deal=0)
        return make_app(
            _order_guard_lock=threading.Lock(), _order_in_flight={PK: False}, _last_order_open_mono={PK: -1e9},
            _last_order_candle_ts={}, active_profile_key=PK, profile_ctx={PK: {}},
            _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk=None, action=None: False,
            _get_profile_cfg=lambda pk: {"max_sl_price": 5.0, "magic_number": MAGIC,
                                         "entry_mode": "Nhồi giá tốt hơn (DCA)", "min_entry_spacing": "0.5"},
            _get_symbol_tolerances=lambda *a, **k: {"eps_equal": 0.05, "min_stop_dist": 0.5, "max_sl_tol": 0.0},
            _is_pos_at_be=lambda p, e=None: p.ticket in be_tickets,
            _send_mt5_order_with_retry=mock.Mock(return_value=(True, res, "")),
            _telegram_notify_order_opened=mock.Mock())

    def test_buy_above_unbe_position_blocked(self):
        # Lệnh BUY cũ 1998 chưa BE, giá hiện tại 2000.3 cao hơn -> DCA chặn như cũ
        self.m.positions_get.return_value = (buy_pos(1, 1998.0),)
        app = self.app(be_tickets=set())
        ok, msg = app.execute_mt5_order(SYM, "BUY", 0.01, 1998.8, 2005.0, 2, profile_key=PK)
        self.assertFalse(ok)
        self.assertIn("DCA", msg)
        app._send_mt5_order_with_retry.assert_not_called()

    def test_buy_above_be_position_allowed(self):
        # Lệnh BUY cũ 1998 đã dời BE -> không còn là mốc DCA, cho vào lệnh mới
        self.m.positions_get.return_value = (buy_pos(1, 1998.0),)
        app = self.app(be_tickets={1})
        ok, msg = app.execute_mt5_order(SYM, "BUY", 0.01, 1998.8, 2005.0, 2, profile_key=PK)
        self.assertTrue(ok, msg)
        app._send_mt5_order_with_retry.assert_called_once()

    def test_unbe_position_still_compared_when_other_is_be(self):
        # 1 lệnh đã BE (1998) + 1 lệnh chưa BE (1999): vẫn so với lệnh chưa BE -> chặn
        self.m.positions_get.return_value = (buy_pos(1, 1998.0), buy_pos(2, 1999.0))
        app = self.app(be_tickets={1})
        ok, msg = app.execute_mt5_order(SYM, "BUY", 0.01, 1998.8, 2005.0, 2, profile_key=PK)
        self.assertFalse(ok)
        self.assertIn("1999.00", msg)
