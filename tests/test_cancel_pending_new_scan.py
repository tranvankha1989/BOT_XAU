"""Test hủy lệnh chờ cũ khi nến mới bắt đầu phân tích lại."""
import os
import sys
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b  # noqa: E402


def _app(in_flight=False):
    app = object.__new__(b.KronosDesktopApp)
    app.active_profile_key = "XAUUSD"
    app.pending_pullback_order = {"action": "SELL"}
    app.profile_ctx = {"XAUUSD": {"pending_pullback_order": {"action": "SELL"}}}
    app._order_in_flight = {"XAUUSD": in_flight}
    app.log = mock.Mock()
    return app


class TestCancelPendingOnNewScan(unittest.TestCase):
    def test_huy_lenh_cho_cu(self):
        app = _app()
        app._cancel_pending_for_new_scan("XAUUSD")
        self.assertIsNone(app.pending_pullback_order)
        self.assertIsNone(app.profile_ctx["XAUUSD"]["pending_pullback_order"])
        app.log.assert_called_once()

    def test_khong_huy_khi_dang_gui_lenh(self):
        app = _app(in_flight=True)
        app._cancel_pending_for_new_scan("XAUUSD")
        self.assertIsNotNone(app.pending_pullback_order)

    def test_khong_co_lenh_cho_thi_khong_log(self):
        app = _app()
        app.pending_pullback_order = None
        app.profile_ctx["XAUUSD"]["pending_pullback_order"] = None
        app._cancel_pending_for_new_scan("XAUUSD")
        app.log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
