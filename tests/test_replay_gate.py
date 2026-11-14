"""Test tools/replay_gate.py bằng dữ liệu tổng hợp (không đọc CSV thật, không gọi MT5/mạng)."""
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import mt5_scalping_bot as b
import replay_gate as rg

BASE = 1_794_577_500  # epoch UTC (13:45:00 UTC ngày 13/11)


def candles(times, tf, o=100.0):
    n = len(times)
    t = np.asarray(times, dtype=np.int64)
    return {"time": t, "open": np.full(n, o), "high": np.full(n, o + 1.0), "low": np.full(n, o - 1.0),
            "close": np.full(n, o), "tick_volume": np.full(n, 10)}


def make_data(bid_fn, spread=0.2, n_sec=120, m1_times=(), m5_times=()):
    ms = (BASE + np.arange(n_sec)) * 1000
    bid = np.array([bid_fn(i) for i in range(n_sec)], dtype=float)
    return rg.ReplayData(ms, bid, bid + spread, candles(m1_times, 60), candles(m5_times, 300))


def sig(**kw):
    s = {"t": float(BASE), "kind": "signal", "tf": "M5", "tight": True, "action": "SELL", "sl": 100.0, "tp": 90.0,
         "sl_dist": 8.0, "cap": 2.0, "op": ">=", "z": 98.0}
    s.update(kw)
    return s


def args(*extra):
    return rg.parse_args(["--poll-ms", "1000", *extra])


SAMPLE = [
    "[2026-11-14 01:10:03.932] 🎯 [XAUUSD - Canh Vùng Giá AI M5 [Siết 1/2 Max SL do RR < 2R]] AI báo SELL (SL: 3888.86, TP: 3878.49, "
    "cách SL 5.92$ > Max 2.00$). Python đang canh giá M1/Tick về vùng >= 3886.86 để tự động vào lệnh!",
    "[2026-11-14 01:12:00.000] 🎯 [BTCUSD - Canh Vùng Giá AI M5] AI báo BUY (SL: 69496.13, TP: 69939.63, cách SL 252.26$ > Max 75.00$). "
    "Python đang canh giá M1/Tick về vùng <= 69571.13 để tự động vào lệnh!",
    "[2026-11-14 01:15:01.852] [XAUUSD - XAUUSDm] Phân tích xong nến M5 (2026-11-13 18:10:00): Chưa đủ điều kiện vào lệnh.",
]


class TestParse(unittest.TestCase):
    def test_pending_dung_tu_dong_log_mau(self):
        ev = rg.parse_log_events(SAMPLE)
        self.assertEqual([e["kind"] for e in ev], ["signal", "clear"])  # dòng BTC bị bỏ
        s = ev[0]
        self.assertEqual((s["action"], s["sl"], s["tp"], s["cap"], s["z"], s["op"], s["tight"]),
                         ("SELL", 3888.86, 3878.49, 2.00, 3886.86, ">=", True))
        # 01:10:03.932 giờ VN = 18:10:03.932 UTC
        self.assertAlmostEqual(s["t"], rg.log_ts_to_epoch("2026-11-14 01:10:03.932"))
        self.assertEqual(rg.fmt_vn(s["t"])[-8:], "01:10:03")
        p = rg.build_pending(s, args(), atr=1.5)
        for k in ("symbol", "action", "sl_price", "manual_queued", "zone_watch", "effective_max_sl", "sl_tightened_for_2r",
                  "target_tp_price", "atr"):
            self.assertIn(k, p)
        self.assertEqual((p["action"], p["sl_price"], p["effective_max_sl"], p["sl_tightened_for_2r"], p["target_tp_price"],
                          p["zone_watch"], p["manual_queued"]), ("SELL", 3888.86, 2.0, True, 3878.49, True, False))
        # biến thể: không chia đôi / TP theo RR
        p2 = rg.build_pending(s, args("--no-halve-max-sl", "--tp-rr", "2"), atr=1.5)
        self.assertEqual((p2["effective_max_sl"], p2["sl_tightened_for_2r"], p2["target_tp_price"]), (4.0, False, None))

    def test_cua_so_tin_hieu_het_han_o_su_kien_ke_tiep(self):
        ev = rg.parse_log_events(SAMPLE)
        (s, t_end), = rg.signal_windows(ev)
        self.assertAlmostEqual(t_end, ev[1]["t"])


class TestReplay(unittest.TestCase):
    def run_rep(self, data, a, t_end=BASE + 100, **sk):
        return rg.run_replay(data, [(sig(**sk), float(t_end))], a)

    def test_a_dong_ho_phat_lai_dieu_khien_gioi_han_tan_suat(self):
        # spread 0.50 > max_spread 0.35, bid 98.5 trong vùng (cách SL 1.5 <= 2.0): cổng spread chặn, log chẩn đoán 10s/lần theo ĐỒNG HỒ PHÁT LẠI
        data = make_data(lambda i: 98.5, spread=0.5, n_sec=60)
        res = self.run_rep(data, args("--no-pa"), t_end=BASE + 35)
        lines = [m for t, m in res["logs"] if m.startswith("🔎") and "cổng=spread" in m]
        self.assertEqual(len(lines), 4)  # t=+1s, +11s, +21s, +31s
        self.assertEqual(res["fills"], [])
        self.assertIn("spread=0.50", lines[0])
        self.assertIn("max_spread=0.35", lines[0])
        # nhanh hơn thời gian thật: chỉ chạy theo đồng hồ dữ liệu, không ngủ
        times = [t for t, m in res["logs"] if m.startswith("🔎")]
        self.assertEqual([round(x - BASE) for x in times], [1, 11, 21, 31])

    def test_c_bid_vao_vung_thi_vao_lenh_gia_khong_vao_thi_0(self):
        near = make_data(lambda i: 95.0 if i < 5 else 98.5, n_sec=60)   # sau 5 giây bid vào vùng (Z=98.0)
        res = self.run_rep(near, args("--no-pa"), t_end=BASE + 50)
        self.assertEqual(len(res["fills"]), 1)
        f = res["fills"][0]
        self.assertEqual(f["action"], "SELL")
        self.assertAlmostEqual(f["bid"], 98.5)
        self.assertAlmostEqual(f["ask"], 98.7)
        self.assertEqual(round(f["t"] - BASE), 5)
        far = make_data(lambda i: 95.0, n_sec=60)
        res0 = self.run_rep(far, args("--no-pa"), t_end=BASE + 50)
        self.assertEqual(res0["fills"], [])
        self.assertGreater(res0["signals"][0]["gap_to_z"], 0)  # bid cao nhất 95 < Z 98 -> chưa tới
        self.assertEqual(res0["signals"][0]["calls"], 49)

    def test_thu_tu_cong_khong_doi_pa_chan_truoc_execute(self):
        # có PA, nến M1 phẳng (không có lực xả) -> cổng price_action chặn, không vào lệnh
        m1 = [BASE - 60 * k for k in range(5, 0, -1)]
        data = make_data(lambda i: 98.5, n_sec=60, m1_times=m1)
        flat = data.candles[60]
        flat.update(open=np.full(5, 98.5), high=np.full(5, 98.5), low=np.full(5, 98.5), close=np.full(5, 98.5))  # nến phẳng: không rút râu/đảo chiều/giảm từ đỉnh
        res = self.run_rep(data, args(), t_end=BASE + 20)
        self.assertEqual(res["fills"], [])
        self.assertIn("price_action", res["signals"][0]["gates"])

    def test_d_mo_phong_ket_qua_dung_dau_va_dung_bid_ask(self):
        # SELL vào bằng bid=100.0 (ask=100.2). TP chạm khi ASK <= 97.0 -> đóng bằng ask 96.8 -> lãi (100-96.8)=3.2
        bid = [100.0, 99.0, 98.0, 96.6, 96.6]
        d = rg.ReplayData((BASE + np.arange(5)) * 1000, bid, np.array(bid) + 0.2, candles([], 60), candles([], 300))
        o = rg.simulate_outcome(d, 0, "SELL", sl=103.0, tp=97.0)
        self.assertEqual((o["reason"], round(o["entry"], 2), round(o["exit"], 2), round(o["pnl"], 2)), ("TP", 100.0, 96.8, 3.2))
        self.assertAlmostEqual(o["r"], 3.2 / 3.0)
        # SELL dính SL: ASK >= 103 -> đóng bằng ask 103.2 -> lỗ -3.2 (spread nằm trong ask)
        bid2 = [100.0, 101.0, 102.8, 102.8]
        d2 = rg.ReplayData((BASE + np.arange(4)) * 1000, bid2, np.array(bid2) + 0.2, candles([], 60), candles([], 300))
        o2 = rg.simulate_outcome(d2, 0, "SELL", sl=103.0, tp=95.0)
        self.assertEqual((o2["reason"], round(o2["exit"], 2), round(o2["pnl"], 2)), ("SL", 103.0, -3.0))
        # BUY vào bằng ask=100.2; TP khi BID >= 103 -> đóng bằng bid 103.1 -> lãi 2.9
        bid3 = [100.0, 101.0, 103.1, 103.1]
        d3 = rg.ReplayData((BASE + np.arange(4)) * 1000, bid3, np.array(bid3) + 0.2, candles([], 60), candles([], 300))
        o3 = rg.simulate_outcome(d3, 0, "BUY", sl=98.0, tp=103.0)
        self.assertEqual((o3["reason"], round(o3["entry"], 2), round(o3["exit"], 2), round(o3["pnl"], 2)), ("TP", 100.2, 103.1, 2.9))
        # BUY dính SL: BID <= 98 -> lỗ
        bid4 = [100.0, 99.0, 97.9, 97.9]
        d4 = rg.ReplayData((BASE + np.arange(4)) * 1000, bid4, np.array(bid4) + 0.2, candles([], 60), candles([], 300))
        o4 = rg.simulate_outcome(d4, 0, "BUY", sl=98.0, tp=105.0)
        self.assertEqual(o4["reason"], "SL")
        self.assertAlmostEqual(o4["pnl"], 97.9 - 100.2)
        self.assertLess(o4["r"], 0)

    def test_e_nen_chua_dong_khong_lot_vao_pa(self):
        m1 = [BASE - 120, BASE - 60, BASE, BASE + 60]   # nến BASE+60 mở SAU thời điểm "now"
        data = make_data(lambda i: 100.0 + i, n_sec=200, m1_times=m1)
        now = BASE + 30.5                                 # đang ở giây thứ 30 của phút BASE
        rows = rg.build_rates(data, 60, now, 5, 0)
        times = [r["time"] for r in rows]
        self.assertEqual(times, [BASE - 120, BASE - 60, BASE])              # BASE là nến ĐANG HÌNH THÀNH, không có BASE+60
        forming = rows[-1]
        self.assertEqual(forming["high"], 130.0)                            # chỉ tick <= now (bid tới 100+30), không phải giá trị cuối file
        self.assertEqual(forming["close"], 130.0)
        self.assertNotEqual(forming["high"], data.candles[60]["high"][2])   # không lấy high của file nến
        closed_only = rg.build_rates(data, 60, now, 5, 1)
        self.assertEqual([r["time"] for r in closed_only], [BASE - 120, BASE - 60])   # start_pos=1: chỉ nến đã đóng
        # đúng ngay tại biên: nến BASE đóng khi now >= BASE+60
        self.assertEqual([r["time"] for r in rg.build_rates(data, 60, BASE + 60, 5, 1)][-1], BASE)
        # qua hàm thật fetch_mt5_candles của bot (PA dùng): hàng cuối là nến đang hình thành dựng từ tick
        clock = rg.Clock()
        clock.now = now
        fake = rg.FakeMT5(data, clock)
        app = object.__new__(b.KronosDesktopApp)
        app._mt5_lock = __import__("threading").RLock()
        with mock.patch.object(b, "mt5", fake):
            df = app.fetch_mt5_candles("XAUUSDm", "M1", 5, include_forming=True)
        self.assertEqual(float(df["high"].iloc[-1]), 130.0)


if __name__ == "__main__":
    unittest.main()
