"""Test pa_wait_bar_close: sau khi PA chặn vì nến lực (rơi tự do / dựng cột), không vào lại trong cùng cây nến.
Toàn bộ mock, không gọi MT5."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b
from test_pb_diag import Base, make_app, SYM, PK

MOMO = (False, "Nến M5 đang dựng cột tăng mạnh (Thân xanh đặc, chưa có lực xả)")
OK = (True, "ok")


def tk(bid, t, spread=0.20):
    return SimpleNamespace(bid=bid, ask=round(bid + spread, 2), time=t)


class TestPaWaitBar(Base):
    def run_seq(self, cfg_over, seq):
        app = make_app(cfg_over)
        app._check_price_action_entry_confirmation = mock.Mock(side_effect=[r for r, _ in seq])
        for _, t in seq:
            app._check_pending_pullback_order(SYM, tk(3886.90, t), profile_key=PK)
        return app

    def test_cung_nen_bi_chan(self):
        # Nến M5 bắt đầu 3000; chặn lúc 3010, PA qua lúc 3014 (cùng nến) -> không vào
        app = self.run_seq({}, [(MOMO, 3010), (OK, 3014)])
        app.execute_mt5_order.assert_not_called()
        self.assertIsNotNone(app.profile_ctx[PK]["pending_pullback_order"])  # vẫn giữ canh vùng

    def test_nen_moi_duoc_vao(self):
        app = self.run_seq({}, [(MOMO, 3010), (OK, 3014), (OK, 3300)])
        app.execute_mt5_order.assert_called_once()

    def test_tat_cong_tac_vao_ngay(self):
        app = self.run_seq({"pa_wait_bar_close": False}, [(MOMO, 3010), (OK, 3014)])
        app.execute_mt5_order.assert_called_once()

    def test_chan_ly_do_khac_khong_giu(self):
        # Chặn vì "chưa đủ điều kiện" (không phải nến lực) -> PA qua thì vào ngay như cũ
        app = self.run_seq({}, [((False, "Chưa đủ 1 điều kiện PA"), 3010), (OK, 3014)])
        app.execute_mt5_order.assert_called_once()

    def test_tick_khong_co_time(self):
        app = make_app()
        app._check_price_action_entry_confirmation = mock.Mock(side_effect=[MOMO, OK])
        for _ in range(2):
            app._check_pending_pullback_order(SYM, SimpleNamespace(bid=3886.90, ask=3887.10), profile_key=PK)
        app.execute_mt5_order.assert_called_once()


if __name__ == "__main__":
    unittest.main()
