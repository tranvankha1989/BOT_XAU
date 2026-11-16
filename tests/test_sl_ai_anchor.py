"""Vung vao neo theo SL AI goc (khong cong Gia an toan); huy canh vung khi nen scalp dong cua vuot SL AI."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

f = b.calc_effective_max_sl_ai


class TestCalcEffectiveMaxSlAi(unittest.TestCase):
    def test_vi_du_nguoi_dung_chia_doi(self):
        # Entry AI 64000, SL AI 63800, TP AI 64300 -> RR 1.5 < 2 -> Max SL 150 chia doi = 75 -> vung vao 63800 + 75
        eff, tight = f("BUY", 64000.0, 63800.0, 64300.0, 150.0)
        self.assertEqual((eff, tight), (75.0, True))
        self.assertEqual(63800.0 + eff, 63875.0)

    def test_rr_du_2r_khong_chia_doi(self):
        self.assertEqual(f("BUY", 64000.0, 63800.0, 64400.0, 150.0), (150.0, False))

    def test_sell_doi_xung(self):
        self.assertEqual(f("SELL", 64000.0, 64200.0, 63700.0, 150.0), (75.0, True))

    def test_khong_gioi_han_va_action_sai(self):
        self.assertEqual(f("BUY", 64000.0, 63800.0, 64300.0, 0.0), (100.0, True))
        self.assertEqual(f(None, 64000.0, 63800.0, 64300.0, 150.0), (150.0, False))


class TestScalpClosedBeyondSlAi(unittest.TestCase):
    def _app(self, close_val):
        app = SimpleNamespace()
        app.fetch_mt5_candles = mock.Mock(return_value=pd.DataFrame({"close": [64100.0, close_val]}))
        return app

    def test_buy_dong_cua_duoi_sl_ai_thi_huy(self):
        app, ctx = self._app(63790.0), {}
        self.assertTrue(b.KronosDesktopApp._scalp_closed_beyond_sl_ai(app, "XAUUSDm", "BUY", 63800.0, "M5", ctx))

    def test_buy_rau_xuyen_nhung_dong_cua_tren_sl_ai_thi_giu(self):
        app, ctx = self._app(63810.0), {}
        self.assertFalse(b.KronosDesktopApp._scalp_closed_beyond_sl_ai(app, "XAUUSDm", "BUY", 63800.0, "M5", ctx))

    def test_sell_dong_cua_tren_sl_ai_thi_huy(self):
        app, ctx = self._app(64210.0), {}
        self.assertTrue(b.KronosDesktopApp._scalp_closed_beyond_sl_ai(app, "XAUUSDm", "SELL", 64200.0, "M5", ctx))

    def test_loi_doc_nen_khong_huy(self):
        app = SimpleNamespace(fetch_mt5_candles=mock.Mock(side_effect=RuntimeError("x")))
        self.assertFalse(b.KronosDesktopApp._scalp_closed_beyond_sl_ai(app, "XAUUSDm", "BUY", 63800.0, "M5", {}))


if __name__ == "__main__":
    unittest.main()
