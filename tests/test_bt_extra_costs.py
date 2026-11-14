import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import run_backtest as rb


class TestExtraCosts(unittest.TestCase):
    def test_khong_chi_phi_tra_nguyen_ban(self):
        t = [{"pnl": 1.0, "lot": 0.02}]
        self.assertIs(rb.apply_extra_costs(t, 0.0, 0.0), t)

    def test_tru_commission_va_truot_gia(self):
        t = [{"pnl": 10.0, "lot": 0.02}, {"pnl": -5.0, "lot": 0.02}]
        r = rb.apply_extra_costs(t, commission_per_lot=7.0, slippage_price=0.05, contract_size=100.0)
        # extra = 7*0.02 + 0.05*0.02*100 = 0.14 + 0.10 = 0.24
        self.assertEqual([x["pnl"] for x in r], [9.76, -5.24])
        self.assertEqual(r[0]["cost_extra"], 0.24)
        self.assertEqual(t[0]["pnl"], 10.0)  # bản gốc không đổi


if __name__ == "__main__":
    unittest.main()
