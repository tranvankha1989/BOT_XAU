"""Chia đôi khoảng cách nhồi khi siết 1/2 Max SL (halve_spacing_with_sl, mặc định BẬT). Mock, không MT5 thật."""
import os
import sys
import threading
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_phase1 import Base, make_app, PK, SYM, MAGIC  # noqa: E402
import mt5_scalping_bot as b  # noqa: E402


def buy_pos(ticket, price_open):
    return SimpleNamespace(ticket=ticket, magic=MAGIC, type=0, volume=0.01, price_open=price_open, sl=1999.0, tp=0.0)


class TestHalveSpacing(Base):
    def app(self, extra_cfg=None):
        res = SimpleNamespace(price=2000.3, order=55, deal=0)
        cfg = {"max_sl_price": 5.0, "magic_number": MAGIC, "min_entry_spacing": "1.0",
               "entry_mode": "Tự do theo AI (Cách >= khoảng cách tối thiểu)", "one_order_per_candle": False}
        cfg.update(extra_cfg or {})
        return make_app(
            _order_guard_lock=threading.Lock(), _order_in_flight={PK: False}, _last_order_open_mono={PK: -1e9},
            _last_order_candle_ts={}, active_profile_key=PK, profile_ctx={PK: {}},
            _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk=None, action=None: False,
            _get_profile_cfg=lambda pk: cfg,
            _get_symbol_tolerances=lambda *a, **k: {"eps_equal": 0.05, "min_stop_dist": 0.5, "max_sl_tol": 0.0},
            _is_pos_at_be=lambda p, e=None: False,
            _send_mt5_order_with_retry=mock.Mock(return_value=(True, res, "")),
            _telegram_notify_order_opened=mock.Mock())

    def _open(self, app, sl_halved):
        # Lệnh cũ 1999.6, giá mua hiện tại 2000.3 -> cách 0.7: < 1.0 (gốc) nhưng >= 0.5 (chia đôi)
        self.m.positions_get.return_value = (buy_pos(1, 1999.6),)
        return app.execute_mt5_order(SYM, "BUY", 0.01, 1998.3, 2005.0, 3, profile_key=PK, sl_halved=sl_halved)

    def test_khong_siet_sl_giu_khoang_cach(self):
        ok, msg = self._open(self.app(), sl_halved=False)
        self.assertFalse(ok)
        self.assertIn("tối thiểu 1.00", msg)

    def test_siet_sl_chia_doi_khoang_cach(self):
        app = self.app()
        ok, msg = self._open(app, sl_halved=True)
        self.assertTrue(ok, msg)
        app._send_mt5_order_with_retry.assert_called_once()

    def test_tat_o_thi_khong_chia_doi(self):
        ok, msg = self._open(self.app({"halve_spacing_with_sl": False}), sl_halved=True)
        self.assertFalse(ok)
        self.assertIn("tối thiểu 1.00", msg)

    def test_siet_sl_van_chan_khi_qua_sat(self):
        self.m.positions_get.return_value = (buy_pos(1, 2000.0),)  # cách 0.3 < 0.5
        ok, msg = self.app().execute_mt5_order(SYM, "BUY", 0.01, 1998.3, 2005.0, 3, profile_key=PK, sl_halved=True)
        self.assertFalse(ok)
        self.assertIn("đã chia đôi do siết SL", msg)

    def test_mac_dinh_bat_ca_hai_profile(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["halve_spacing_with_sl"], True)
        self.assertIn("halve_spacing_with_sl", b.PARAM_HELP_GUIDE)
