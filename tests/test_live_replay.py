"""Kiểm thử sàn giả lập + đồng hồ của tools/live_replay.py (dữ liệu tổng hợp, không MT5)."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import live_replay as lr  # noqa: E402

T0 = 1_700_000_100  # bội số của 60 và 300? -> dùng mốc tròn bên dưới
T0 = (T0 // 900) * 900


def make_broker(prices, step_s=1.0, spread=2.0):
    """Tick mỗi step_s giây từ T0, bid = prices[i], ask = bid + spread. Nến M1/M5 dựng từ chính tick."""
    n = len(prices)
    ms = (T0 * 1000 + np.arange(n) * int(step_s * 1000)).astype(np.int64)
    bid = np.asarray(prices, dtype=float)
    ask = bid + spread
    rates = {}
    for tf in (60, 300, 900):
        rows = []
        t_end = T0 + int(n * step_s)
        for s in range(T0 - 50 * tf, t_end, tf):
            if s < T0:
                rows.append((s, 100.0, 100.0, 100.0, 100.0, 1, 0, 0))
                continue
            m = (ms >= s * 1000) & (ms < (s + tf) * 1000)
            if m.any():
                b = bid[m]
                rows.append((s, b[0], b.max(), b.min(), b[-1], int(m.sum()), 0, 0))
        rates[tf] = np.array(rows, dtype=lr.RATE_DTYPE)
    clock = lr.SimClock(T0)
    info = {"digits": 2, "point": 0.01, "trade_contract_size": 1.0, "volume_min": 0.01, "volume_max": 100.0,
            "volume_step": 0.01, "filling_mode": 2, "trade_stops_level": 0}
    return lr.FakeBroker("BTCUSDm", info, ms, bid, ask, rates, clock), clock


class TestFakeBroker(unittest.TestCase):
    def test_mo_lenh_buy_khop_ask_va_cham_sl_theo_bid(self):
        prices = [100.0] * 10 + [95.0, 89.0, 88.0] + [100.0] * 5
        b, clk = make_broker(prices)
        clk.now = T0 + 2
        r = b.order_send({"action": b.TRADE_ACTION_DEAL, "type": b.ORDER_TYPE_BUY, "volume": 0.02, "sl": 90.0, "tp": 120.0,
                          "magic": 7, "symbol": "BTCUSDm"})
        self.assertEqual(r.retcode, b.TRADE_RETCODE_DONE)
        self.assertAlmostEqual(r.price, 102.0)  # ask
        b.process_ticks(T0 + 2, T0 + 20)
        self.assertEqual(len(b.positions), 0)
        c = b.closed[0]
        self.assertEqual(c["reason"], "SL")
        self.assertAlmostEqual(c["price_close"], 89.0)  # tick đầu tiên bid <= 90
        self.assertAlmostEqual(c["profit"], round((89.0 - 102.0) * 0.02, 2))
        out = [d for d in b.deals if d.entry == b.DEAL_ENTRY_OUT]
        self.assertEqual(out[0].position_id, r.order)
        self.assertEqual(out[0].magic, 7)

    def test_sell_cham_tp_theo_ask(self):
        prices = [100.0] * 5 + [90.0, 85.0, 80.0]
        b, clk = make_broker(prices)
        clk.now = T0 + 1
        b.order_send({"action": b.TRADE_ACTION_DEAL, "type": b.ORDER_TYPE_SELL, "volume": 0.01, "sl": 110.0, "tp": 88.0, "magic": 1})
        b.process_ticks(T0 + 1, T0 + 10)
        self.assertEqual(b.closed[0]["reason"], "TP")
        self.assertAlmostEqual(b.closed[0]["price_close"], 87.0)  # ask = 85 + 2 là tick đầu <= 88

    def test_san_nghi_phien_tu_choi_moi_lenh_gui(self):
        # MT5 thật: sàn nghỉ phiên (vàng ~1 giờ/ngày, không có tick) -> từ chối mở/đóng/sửa SL-TP (10018 Market closed).
        # Trước đây sàn giả vẫn cho sửa SL/TP trong giờ nghỉ -> backtest khác live.
        b, clk = make_broker([100.0] * 10)
        clk.now = T0 + 1
        r = b.order_send({"action": b.TRADE_ACTION_DEAL, "type": b.ORDER_TYPE_BUY, "volume": 0.01, "sl": 90.0, "tp": 120.0, "magic": 1})
        self.assertEqual(r.retcode, b.TRADE_RETCODE_DONE)
        clk.now = T0 + 9 + 30  # tick cuối cách 30s: sàn vẫn mở
        ok = b.order_send({"action": b.TRADE_ACTION_SLTP, "position": r.order, "sl": 95.0, "tp": 120.0})
        self.assertEqual(ok.retcode, b.TRADE_RETCODE_DONE)
        clk.now = T0 + 9 + 3600  # 1 giờ không có tick: nghỉ phiên
        for req in ({"action": b.TRADE_ACTION_SLTP, "position": r.order, "sl": 99.0, "tp": 120.0},
                    {"action": b.TRADE_ACTION_DEAL, "position": r.order, "volume": 0.01, "type": b.ORDER_TYPE_SELL},
                    {"action": b.TRADE_ACTION_DEAL, "type": b.ORDER_TYPE_SELL, "volume": 0.01, "sl": 110.0, "tp": 80.0, "magic": 1}):
            res = b.order_send(req)
            self.assertEqual(res.retcode, 10018)
            self.assertIn("market closed", res.comment.lower())  # bot live nhận diện nghỉ phiên qua chữ này
        self.assertEqual(b.positions[0].sl, 95.0)  # SL/TP không bị sửa trong giờ nghỉ
        self.assertEqual(len(b.positions), 1)

    def test_sua_sltp_sai_phia_bi_tu_choi(self):
        b, clk = make_broker([100.0] * 10)
        clk.now = T0 + 1
        r = b.order_send({"action": b.TRADE_ACTION_DEAL, "type": b.ORDER_TYPE_BUY, "volume": 0.01, "sl": 90.0, "tp": 120.0, "magic": 1})
        bad = b.order_send({"action": b.TRADE_ACTION_SLTP, "position": r.order, "sl": 101.0, "tp": 120.0})
        self.assertEqual(bad.retcode, b.TRADE_RETCODE_INVALID_STOPS)
        ok = b.order_send({"action": b.TRADE_ACTION_SLTP, "position": r.order, "sl": 99.0, "tp": 120.0})
        self.assertEqual(ok.retcode, b.TRADE_RETCODE_DONE)
        self.assertEqual(b.positions_get(ticket=r.order)[0].sl, 99.0)

    def test_dong_lenh_chu_dong(self):
        b, clk = make_broker([100.0] * 5 + [110.0] * 5)
        clk.now = T0 + 1
        r = b.order_send({"action": b.TRADE_ACTION_DEAL, "type": b.ORDER_TYPE_BUY, "volume": 0.01, "sl": 90.0, "tp": 150.0, "magic": 1})
        clk.now = T0 + 7
        c = b.order_send({"action": b.TRADE_ACTION_DEAL, "position": r.order, "volume": 0.01, "type": b.ORDER_TYPE_SELL})
        self.assertEqual(c.retcode, b.TRADE_RETCODE_DONE)
        self.assertAlmostEqual(c.price, 110.0)
        self.assertEqual(b.closed[0]["reason"], "Bot đóng")
        self.assertEqual(len(b.history_deals_get(position=r.order)), 2)

    def test_nen_da_dong_khong_lo_tuong_lai_va_nen_dang_chay_tu_tick(self):
        prices = list(np.linspace(100, 160, 180))
        b, clk = make_broker(prices)
        clk.now = T0 + 90.5  # giữa nến M1 thứ 2
        closed = b.copy_rates_from_pos("BTCUSDm", b.TIMEFRAME_M1, 1, 5)
        self.assertEqual(int(closed["time"][-1]), T0)          # chỉ nến T0 đã đóng
        withf = b.copy_rates_from_pos("BTCUSDm", b.TIMEFRAME_M1, 0, 5)
        self.assertEqual(int(withf["time"][-1]), T0 + 60)       # nến đang chạy
        self.assertAlmostEqual(float(withf["close"][-1]), prices[90])  # tick cuối <= now
        self.assertLessEqual(float(withf["high"][-1]), prices[90])

    def test_gio_nghi_khong_co_nen_dang_chay_vi_tri_0_la_nen_cuoi(self):
        """Như MT5: không có tick trong chu kỳ hiện tại -> vị trí 0 là nến cuối cùng (không có nến đang chạy)."""
        b, clk = make_broker([100.0 + i * 0.1 for i in range(100)])  # tick tới T0+99
        clk.now = T0 + 150.0  # chu kỳ T0+120 chưa có tick
        p0 = b.copy_rates_from_pos("BTCUSDm", b.TIMEFRAME_M1, 0, 3)
        p1 = b.copy_rates_from_pos("BTCUSDm", b.TIMEFRAME_M1, 1, 3)
        self.assertEqual(int(p0["time"][-1]), T0 + 60)
        self.assertEqual(int(p1["time"][-1]), T0)
        self.assertEqual(len(p1), 3)

    def test_tick_va_dong_ho(self):
        b, clk = make_broker([100.0, 101.0, 102.0])
        clk.now = T0 + 1.5
        t = b.symbol_info_tick("BTCUSDm")
        self.assertEqual(t.bid, 101.0)
        self.assertEqual(t.time, T0 + 1)
        ft = lr.FakeTimeModule(clk)
        self.assertEqual(ft.time(), T0 + 1.5)
        FD = lr.make_fake_datetime(clk)
        self.assertEqual(int(FD.now(tz=lr.VN_TZ).timestamp()), T0 + 1)

    def test_thread_dong_bo(self):
        out = []
        lr.FakeThreadingModule().Thread(target=lambda x: out.append(x), args=(5,), daemon=True).start()
        self.assertEqual(out, [5])

    def test_set_bool_doi_dung_kieu(self):
        import run_backtest as rb
        d = {"trend_filter": True, "confirm_entry_pa": False, "tf": "M5"}
        cfg = {}
        rb.apply_set_overrides(cfg, d, ["trend_filter=False", "confirm_entry_pa=on", "tf=M1"])
        self.assertIs(cfg["trend_filter"], False)   # bot đọc bool(...) -> phải là bool thật, không phải chuỗi "False"
        self.assertIs(cfg["confirm_entry_pa"], True)
        self.assertEqual(cfg["tf"], "M1")
        with self.assertRaises(ValueError):
            rb.apply_set_overrides({}, d, ["trend_filter=tat"])

    def test_summarize(self):
        s = lr.summarize([{"profit": 2.0}, {"profit": -1.0}, {"profit": -1.0}, {"profit": 3.0}])
        self.assertEqual(s["n"], 4)
        self.assertAlmostEqual(s["net"], 3.0)
        self.assertAlmostEqual(s["pf"], 2.5)
        self.assertEqual(s["max_dd"], -2.0)
        self.assertEqual(s["max_loss_streak"], 2)


if __name__ == "__main__":
    unittest.main()
