"""Test mã lý do đóng lệnh ghi vào comment MT5 (KR_<mã>)."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b  # noqa: E402


def _run_close(reason):
    app = object.__new__(b.KronosDesktopApp)
    app._mt5_lock = threading.RLock()
    sent = {}

    def fake_send(req, symbol, context_tag="", max_retries=3):
        sent.update(req)
        return True, None, ""

    app._send_mt5_order_with_retry = fake_send
    fake_mt5 = mock.MagicMock()
    fake_mt5.symbol_info_tick.return_value = SimpleNamespace(bid=3800.0, ask=3800.2)
    fake_mt5.symbol_info.return_value = SimpleNamespace(digits=2, point=0.01)
    fake_mt5.POSITION_TYPE_BUY = 0
    pos = SimpleNamespace(ticket=7, symbol="XAUUSDm", volume=0.02, type=0, magic=20260501, profit=1.0)
    with mock.patch.object(b, "mt5", fake_mt5):
        ok, _ = app._close_single_pos(pos, reason) if reason is not None else app._close_single_pos(pos)
    return ok, sent


class TestCloseReason(unittest.TestCase):
    def test_co_ma_ly_do(self):
        ok, req = _run_close("TIME")
        self.assertTrue(ok)
        self.assertEqual(req["comment"], "KR_TIME")

    def test_khong_ma_giu_comment_cu(self):
        ok, req = _run_close(None)
        self.assertEqual(req["comment"], "AI_Close")

    def test_comment_khong_qua_31_ky_tu(self):
        _, req = _run_close("X" * 50)
        self.assertLessEqual(len(req["comment"]), 31)


if __name__ == "__main__":
    unittest.main()
