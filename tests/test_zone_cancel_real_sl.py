"""Canh vùng (25/11): chỉ hủy vùng khi nến khung scalp ĐÓNG CỬA vượt SL THẬT (SL AI gốc + Giá an toàn).
Đóng qua SL AI gốc nhưng chưa qua SL thật: giữ vùng, chưa vào; giá quay lại vùng thì vào. Mock, không MT5 thật."""
import os
import sys
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_pb_diag import Base, make_app, diag, tk, PK, SYM  # noqa: E402

SL_REAL = 3888.86   # SL thật (SELL)
SL_AI = 3887.36     # SL AI gốc, Giá an toàn 1.50


def app_with_close(close_val):
    app = make_app()
    app.pending_pullback_order["sl_ai_price"] = SL_AI
    app.fetch_mt5_candles = mock.Mock(return_value=pd.DataFrame({"close": [3880.0, close_val]}))
    return app


class TestZoneCancelRealSl(Base):
    def test_dong_qua_sl_ai_chua_qua_sl_that_giu_vung_chua_vao(self):
        app = app_with_close(3888.00)
        app._check_pending_pullback_order(SYM, tk(3887.60), profile_key=PK)
        self.assertIsNotNone(app.profile_ctx[PK]["pending_pullback_order"])
        app.execute_mt5_order.assert_not_called()
        self.assertEqual(len(diag(app, "gia_duoi_sl_ai")), 1)

    def test_dong_qua_sl_ai_roi_gia_quay_lai_vung_thi_vao(self):
        app = app_with_close(3888.00)
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        app.execute_mt5_order.assert_called_once()

    def test_dong_qua_sl_that_thi_huy_vung(self):
        app = app_with_close(3889.00)
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertIsNone(app.profile_ctx[PK]["pending_pullback_order"])
        self.assertIsNone(app.pending_pullback_order)
        app.execute_mt5_order.assert_not_called()
        msgs = [str(c.args[0]) for c in app.log.call_args_list if c.args]
        self.assertTrue(any("SL thật" in m and f"{SL_REAL:.2f}" in m for m in msgs))


if __name__ == "__main__":
    import unittest
    unittest.main()
