"""RR thật tối thiểu (min_real_rr): đo RR theo SL THẬT (SL AI + Giá an toàn) lúc vào lệnh. 0 = tắt (như cũ)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b
from trading_modules import param_rules as pr
from test_phase1 import Base, TestExecuteOrder, PK, SYM


class TestMinRealRR(Base):
    # SELL tại bid 2000.0: SL AI 2001.0 (cách 1.0), SL thật 2003.0 (cách 3.0, Giá an toàn 2.0), TP 1997.5 (cách 2.5)
    # -> RR theo SL AI = 2.5R (qua luật cũ), RR thật = 2.5 / 3.0 = 0.83R
    def _app(self, mrr):
        app = TestExecuteOrder.app(self)
        app._get_profile_cfg = lambda pk: {"max_sl_price": 5.0, "magic_number": 777, "min_real_rr": mrr}
        self.m.positions_get.return_value = ()
        return app

    def _run(self, mrr):
        app = self._app(mrr)
        ok, msg = app.execute_mt5_order(SYM, "SELL", 0.01, 2003.0, 1997.5, 3, profile_key=PK, sl_ai_price=2001.0)
        return app, ok, msg

    def test_tat_thi_vao_lenh_nhu_cu(self):
        for v in ("0", "", None):
            app, ok, msg = self._run(v)
            self.assertTrue(ok, msg)
            app._send_mt5_order_with_retry.assert_called_once()

    def test_rr_that_thap_hon_thi_chan(self):
        app, ok, msg = self._run("1.5")
        self.assertFalse(ok)
        self.assertIn("RR thật 0.83R < 1.50R", msg)
        app._send_mt5_order_with_retry.assert_not_called()
        self.assertFalse(app._order_in_flight[PK])  # khóa in-flight đã nhả

    def test_rr_that_dat_thi_vao(self):
        app, ok, msg = self._run("0.8")
        self.assertTrue(ok, msg)
        app._send_mt5_order_with_retry.assert_called_once()

    def test_mac_dinh_tat_ca_hai_profile(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertEqual(b.DEFAULT_PROFILE_SETTINGS[pk]["min_real_rr"], "0")
        self.assertIn("min_real_rr", b.KronosDesktopApp.SETTINGS_SECTION_MAP[2][1])
        self.assertIn("min_real_rr", b.PARAM_HELP_GUIDE)


class TestMinRealRRParamRules(unittest.TestCase):
    def _warns(self, cfg):
        return [i for i in pr.validate_param_values(cfg) if i["level"] == "warn" and "sl_buffer" in i["keys"]]

    def test_canh_bao_gia_an_toan(self):
        # BTC máy 2: Giá an toàn 150, SL tối đa 250, TP theo AI -> cảnh báo
        w = self._warns({"sl_buffer": "150", "max_sl_price": "250", "rr": "0"})
        self.assertIn("RR thật < 1", w[0]["msg"])
        # Bật RR thật tối thiểu hoặc R:R > 0 (TP tính từ SL thật, như vàng) -> không cảnh báo
        self.assertEqual(self._warns({"sl_buffer": "150", "max_sl_price": "250", "rr": "0", "min_real_rr": "1.5"}), [])
        self.assertEqual(self._warns({"sl_buffer": "2.5", "max_sl_price": "4", "rr": "2"}), [])
        # Giá an toàn nhỏ hơn 1/2 SL tối đa -> không cảnh báo
        self.assertEqual(self._warns({"sl_buffer": "30", "max_sl_price": "250", "rr": "0"}), [])

    def test_o_so_am_bao_loi(self):
        errs = [i for i in pr.validate_param_values({"min_real_rr": "-1"}) if i["level"] == "error"]
        self.assertTrue(errs)


if __name__ == "__main__":
    unittest.main()
