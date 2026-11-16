"""Bộ lọc cấu trúc xét lại trước khi khớp lệnh canh vùng + conflict_mode theo đúng profile + module cấu trúc báo lỗi."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mt5_scalping_bot as b
import test_rr_gate as g
from trading_modules.structure import analyze_structure_context

App = b.KronosDesktopApp


def mk_self(macro_dir=-1, trend_filter=True, fetch_exc=None):
    df = pd.DataFrame({"high": [2.0] * 20, "low": [1.0] * 20, "open": [1.5] * 20, "close": [1.5] * 20, "volume": [1.0] * 20})
    fetch = mock.Mock(side_effect=fetch_exc) if fetch_exc else mock.Mock(return_value=df)
    s = SimpleNamespace(
        fetch_mt5_candles=fetch, calculate_atr=lambda d, n: 1.0, log=mock.Mock(),
        _analyze_scalping_micro_macro_context=mock.Mock(return_value={"macro_dir": macro_dir, "structure_reason": "Giam LH+LL"}))
    cfg = {"trend_filter": trend_filter, "tf": "M5", "analysis_tf": "M15"}
    return s, cfg, {}


class TestPbStructureBlocks(unittest.TestCase):
    def test_tat_loc_khong_doc_nen(self):
        s, cfg, ctx = mk_self(trend_filter=False)
        self.assertEqual(App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "BUY", cfg, ctx), (False, ""))
        s.fetch_mt5_candles.assert_not_called()

    def test_buy_bi_chan_khi_cau_truc_giam(self):
        s, cfg, ctx = mk_self(macro_dir=-1)
        blocked, reason = App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "BUY", cfg, ctx)
        self.assertTrue(blocked)
        self.assertIn("Giam", reason)
        self.assertEqual(s._analyze_scalping_micro_macro_context.call_args.kwargs.get("profile_key"), "BTCUSD")

    def test_sell_khong_bi_chan_khi_cau_truc_giam(self):
        s, cfg, ctx = mk_self(macro_dir=-1)
        self.assertFalse(App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "SELL", cfg, ctx)[0])

    def test_sell_bi_chan_khi_cau_truc_tang(self):
        s, cfg, ctx = mk_self(macro_dir=1)
        self.assertTrue(App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "SELL", cfg, ctx)[0])

    def test_can_bang_khong_chan(self):
        s, cfg, ctx = mk_self(macro_dir=0)
        self.assertFalse(App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "BUY", cfg, ctx)[0])

    def test_cache_5_giay(self):
        s, cfg, ctx = mk_self(macro_dir=-1)
        with mock.patch.object(b.time, "monotonic", return_value=100.0):
            App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "BUY", cfg, ctx)
        n1 = s.fetch_mt5_candles.call_count
        with mock.patch.object(b.time, "monotonic", return_value=104.0):
            App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "BUY", cfg, ctx)
        self.assertEqual(s.fetch_mt5_candles.call_count, n1)
        with mock.patch.object(b.time, "monotonic", return_value=106.0):
            App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "BUY", cfg, ctx)
        self.assertGreater(s.fetch_mt5_candles.call_count, n1)

    def test_loi_doc_nen_khong_chan_va_co_log(self):
        s, cfg, ctx = mk_self(fetch_exc=RuntimeError("mat ket noi"))
        self.assertEqual(App._pb_structure_blocks(s, "BTCUSD", "BTCUSDm", "BUY", cfg, ctx), (False, ""))
        self.assertEqual(s.log.call_count, 1)


class TestGateTrongCanhVung(unittest.TestCase):
    def test_cau_truc_nguoc_chieu_khong_khop_giu_pending(self):
        s, p, tick = g.mk(tp=104.0)
        s._pb_structure_blocks = lambda *a, **k: (True, "Cau truc Giam")
        g.run(s, tick)
        s.execute_mt5_order.assert_not_called()
        self.assertIs(s.profile_ctx[g.PK]["pending_pullback_order"], p)

    def test_cau_truc_thuan_chieu_van_khop(self):
        s, p, tick = g.mk(tp=104.0)
        g.run(s, tick)
        s.execute_mt5_order.assert_called_once()


class TestConflictModeTheoProfile(unittest.TestCase):
    def test_doc_cau_hinh_cua_profile_truyen_vao(self):
        cfgs = {"XAUUSD": {"structure_conflict_mode": "neutral"}, "BTCUSD": {"structure_conflict_mode": "analysis"}}
        s = SimpleNamespace(active_profile_key="XAUUSD", _get_profile_cfg=lambda pk: cfgs[pk], log=mock.Mock())
        with mock.patch.object(b, "analyze_structure_context", return_value={"macro_dir": 0}) as m:
            App._analyze_scalping_micro_macro_context(s, None, None, 1.0, profile_key="BTCUSD")
            self.assertEqual(m.call_args.args[3], "analysis")
            App._analyze_scalping_micro_macro_context(s, None, None, 1.0)
            self.assertEqual(m.call_args.args[3], "neutral")

    def test_loi_module_cau_truc_co_log(self):
        s = SimpleNamespace(active_profile_key="XAUUSD", _get_profile_cfg=lambda pk: {}, log=mock.Mock())
        with mock.patch.object(b, "analyze_structure_context", return_value={"macro_dir": 0, "error": "ValueError: x"}):
            App._analyze_scalping_micro_macro_context(s, None, None, 1.0)
        self.assertEqual(s.log.call_count, 1)


class TestModuleCauTrucBaoLoi(unittest.TestCase):
    def test_du_lieu_sai_tra_truong_error(self):
        df_bad = pd.DataFrame({"high": [1.0] * 20})  # thiếu cột low/close/open
        res = analyze_structure_context(df_bad, df_bad, 1.0, "analysis")
        self.assertEqual(res["macro_dir"], 0)
        self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main()
