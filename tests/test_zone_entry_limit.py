"""Đường Vùng canh = mức vào THẬT (_zone_entry_limit, chỉ hiển thị): qua cả cổng SL tối đa, RR >= 2R tới TP của vùng,
RR thật tối thiểu (nếu bật); BUY trừ spread vì biểu đồ vẽ theo Bid. Không gọi MT5."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


def make_app(cfg):
    app = b.KronosDesktopApp.__new__(b.KronosDesktopApp)
    app.active_profile_key = "BTCUSD"
    app._get_profile_cfg = lambda pk=None: dict(cfg)
    return app


# Số liệu ảnh 20/11 (BTC): SL AI gốc 58806.75, SL thật 58746.75 (Giá an toàn 60), Max SL 160, TP 59200.41, spread 6.40
P_BUY = {"symbol": "BTCUSDm", "action": "BUY", "sl_ai_price": 58806.75, "sl_price": 58746.75,
         "effective_max_sl": 160.0, "target_tp_price": 59200.41}
TICK = types.SimpleNamespace(bid=58853.89, ask=58860.29)


class TestZoneEntryLimit(unittest.TestCase):
    def test_buy_tran_that_theo_rr_tru_spread(self):
        z = make_app({"rr": 2.0, "max_sl_price": 160.0})._zone_entry_limit(P_BUY, "BTCUSD", TICK)
        self.assertTrue(z["ok"])
        self.assertEqual(z["why"], "RR≥2R")
        self.assertAlmostEqual(z["gate"], 58937.97, places=2)  # thấp hơn đường cũ 58966.75 (chỉ xét SL tối đa)
        self.assertAlmostEqual(z["line"], 58937.97 - 6.40, places=2)
        rr_at_gate = (P_BUY["target_tp_price"] - z["gate"]) / (z["gate"] - P_BUY["sl_ai_price"])
        self.assertGreaterEqual(rr_at_gate, 2.0 - 0.005 / (z["gate"] - P_BUY["sl_ai_price"]))  # đúng tại mức này cổng RR còn qua

    def test_min_real_rr_tinh_theo_sl_that(self):
        z = make_app({"rr": 2.0, "min_real_rr": 2.0})._zone_entry_limit(P_BUY, "BTCUSD", TICK)
        self.assertEqual(z["why"], "RR thật≥2R")
        self.assertAlmostEqual(z["gate"], 58897.97, places=2)  # = giá đóng nến: RR thật đúng 2R

    def test_min_real_rr_qua_cao_khong_vao_duoc(self):
        z = make_app({"rr": 2.0, "min_real_rr": 10.0})._zone_entry_limit(P_BUY, "BTCUSD", TICK)
        self.assertFalse(z["ok"])

    def test_sell_khong_tp_chi_sl_toi_da_cong_dung_sai(self):
        p = {"symbol": "XAUUSDm", "action": "SELL", "sl_ai_price": 3735.53, "effective_max_sl": 4.0}
        z = make_app({"rr": 2.0})._zone_entry_limit(p, "XAUUSD", types.SimpleNamespace(bid=3730.0, ask=3730.2))
        self.assertAlmostEqual(z["gate"], 3735.53 - 4.05, places=2)
        self.assertAlmostEqual(z["line"], z["gate"], places=2)  # SELL đo bằng Bid: không trừ spread
        self.assertEqual(z["why"], "SL≤4.00$")

    def test_khong_tick_dung_spread_da_luu(self):
        app = make_app({"rr": 2.0})
        app._zone_spread = {"BTCUSD": 10.0}
        z = app._zone_entry_limit(P_BUY, "BTCUSD")
        self.assertAlmostEqual(z["line"], z["gate"] - 10.0, places=2)

    def test_replay_dai_vung_canh_dung_muc_that(self):
        import json
        import shutil
        import tempfile
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import live_replay as lr
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "events.jsonl")
        app = make_app({"rr": 2.0})
        app.profile_ctx = {"BTCUSD": {"pending_pullback_order": dict(P_BUY)}}
        broker = types.SimpleNamespace(positions=[], sltp_log=[], closed=[], symbol_info_tick=lambda s: TICK)
        es = lr.EventStream(path, broker, app, "BTCUSD", [], "BTCUSDm")
        es._zone(1000.0)
        es.f.close()
        with open(path, encoding="utf-8") as f:
            ev = [json.loads(x) for x in f if x.strip()]
        zone = next(e for e in ev if e["type"] == "zone")
        self.assertAlmostEqual(zone["hi"], 58937.97 - 6.40, places=2)

    def test_du_lieu_sai_tra_none(self):
        self.assertIsNone(make_app({})._zone_entry_limit({"action": "BUY"}, "BTCUSD", TICK))


if __name__ == "__main__":
    unittest.main()
