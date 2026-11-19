"""Xem backtest TRỰC TIẾP (màn hình Backtest riêng ở khung trên):
- tools/live_replay.EventStream ghi sự kiện (lệnh, SL/TP, AI, vùng canh, nhật ký, mốc đã tính) ra events.jsonl;
- trading_modules/replay_player.ReplayPlayer nhận sự kiện, không tua quá mốc đã tính, nhúng được vào khung có sẵn;
- app: đổi khung trên live <-> Backtest, bảng lệnh chạy theo thời điểm đang phát, vẽ nhẹ không ghi "hoàn tất".
Toàn bộ mock: không MT5, không AI."""
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import live_replay as lr  # noqa: E402
import mt5_scalping_bot as b  # noqa: E402
from trading_modules import replay_player as rp  # noqa: E402

T0 = 1_794_268_800  # 10/11/2026 00:00 UTC


def series(n=3600, t0=T0):
    sec = np.arange(t0, t0 + n, dtype=np.int64)
    c = 4100 + np.sin(np.arange(n) / 30.0)
    o = np.r_[c[0], c[:-1]]
    return {"sec": sec, "o": o, "h": np.maximum(o, c) + 0.05, "l": np.minimum(o, c) - 0.05, "c": c}


def make_tk():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    return tk, root


class TestEventStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "events.jsonl")
        self.broker = types.SimpleNamespace(positions=[], sltp_log=[], closed=[], symbol_info_tick=lambda s: None)
        self.ctx = {"pending_pullback_order": None}
        self.app = types.SimpleNamespace(profile_ctx={"XAUUSD": self.ctx},
                                         _get_symbol_tolerances=lambda s, t=None: {"max_sl_tol": 0.05, "sl_break_tol": 0.25})
        self.logs = []
        self.es = lr.EventStream(self.path, self.broker, self.app, "XAUUSD", self.logs, "XAUUSDm")
        self.addCleanup(self.es.f.close)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_trade_zone_log_ai_events(self):
        ms_open = (T0 + 100) * 1000
        pos = types.SimpleNamespace(ticket=100001, type=1, time_msc=ms_open, price_open=4103.93, volume=0.02)
        self.broker.positions.append(pos)
        self.broker.sltp_log.append((ms_open, 100001, 4109.41, 4092.91))
        self.logs.append((T0 + 100.0, "ĐÃ KHỚP LỆNH #100001"))
        self.ctx["pending_pullback_order"] = {"action": "SELL", "sl_ai_price": 4106.91, "sl_price": 4109.41,
                                              "effective_max_sl": 3.0, "target_tp_price": 4092.91}
        self.es.step(T0 + 100.0, force_flush=True)
        mean_df = pd.DataFrame({"timestamps": pd.to_datetime([T0 + 300, T0 + 600], unit="s"),
                                "open": [4104.0, 4103.0], "high": [4105.0, 4104.0], "low": [4102.5, 4101.0], "close": [4103.0, 4101.5]})
        self.es.ai(T0 + 110.0, [mean_df, mean_df], mean_df, "[XAUUSD] SELL", "#ef5350", "Giá đóng nến...", "SELL", 4109.41, 4092.91, "M5")
        self.broker.positions.clear()
        self.broker.closed.append({"ticket": 100001, "type": "SELL", "open_ms": ms_open, "close_ms": ms_open + 60_000,
                                   "price_open": 4103.93, "price_close": 4103.60, "sl0": 4109.41, "tp0": 4092.91, "sl_last": 4103.60,
                                   "volume": 0.02, "profit": 0.66, "reason": "SL", "comment": "[sl 4103.60]"})
        self.ctx["pending_pullback_order"] = None
        self.es.close(T0 + 200.0)
        ev = self.read()
        types_ = [e["type"] for e in ev]
        for k in ("open", "sltp", "log", "zone", "t", "ai", "close"):
            self.assertIn(k, types_)
        self.assertEqual(types_.count("open"), 1)  # không ghi mở lệnh 2 lần
        zone = next(e for e in ev if e["type"] == "zone" and not e.get("clear"))
        self.assertAlmostEqual(zone["lo"], 4106.91 - 3.05, places=6)
        self.assertAlmostEqual(zone["hi"], 4106.91 - 0.25, places=6)
        self.assertTrue(any(e["type"] == "zone" and e.get("clear") for e in ev))
        ai = next(e for e in ev if e["type"] == "ai")
        self.assertEqual(ai["pred"][0][0], float(T0 + 300))  # giờ nến dự báo cùng gốc với giờ tick
        self.assertEqual(len(ai["fan"]), 2)
        close = next(e for e in ev if e["type"] == "close")
        self.assertEqual(close["reason_label"], "SL đã dời (có lãi)")
        self.assertEqual(close["side"], "SELL")  # chiều lệnh không bị "type": "close" đè mất
        self.assertEqual(ev[-1]["type"], "t")
        self.assertEqual(ev[-1]["t"], T0 + 200.0)

    def test_close_without_seen_open_writes_open_first(self):
        self.broker.closed.append({"ticket": 7, "type": "BUY", "open_ms": T0 * 1000, "close_ms": T0 * 1000 + 5000, "price_open": 1.0,
                                   "price_close": 2.0, "sl0": 0.5, "tp0": 3.0, "sl_last": 0.5, "volume": 0.01, "profit": 1.0,
                                   "reason": "TP", "comment": "[tp 2.00]"})
        self.es.close(T0 + 10.0)
        types_ = [e["type"] for e in self.read() if e["type"] in ("open", "close")]
        self.assertEqual(types_, ["open", "close"])

    def test_errors_never_raise(self):
        self.broker.positions.append(object())  # vật lạ: thiếu thuộc tính
        self.es.step(T0 + 1.0, force_flush=True)  # không được ném lỗi làm hỏng lượt backtest


class TestPlayerLive(unittest.TestCase):
    def setUp(self):
        try:
            self.tk, self.root = make_tk()
        except Exception as e:
            self.skipTest(f"Không dựng được Tk: {e}")
        self.addCleanup(self.root.destroy)

    def test_frontier_limits_cursor_and_events(self):
        frame = self.tk.Frame(self.root)
        frame.pack()
        p = rp.ReplayPlayer(self.root, series(), [], [], T0 + 60, T0 + 3500, parent=frame, live=True)
        self.assertIsNotNone(p.log_text)
        p.set_speed(rp.SPEED_MAX_LABEL)
        self.assertEqual(p.speed, rp.SPEED_MAX)
        self.assertEqual(p.speed_var.get(), rp.SPEED_MAX_LABEL)  # ô tốc độ khớp tốc độ thật
        p.add_events([{"type": "t", "t": T0 + 600}])
        p.play()
        p._last = time.perf_counter() - 0.2
        p._tick()
        self.assertAlmostEqual(p.t, T0 + 600)  # không vượt mốc đã tính
        self.assertTrue(p.waiting)
        ms_o = (T0 + 700) * 1000
        p.add_events([
            {"type": "open", "t": T0 + 700, "ticket": 5, "side": "BUY", "open_ms": ms_o, "price": 4100.2, "vol": 0.02},
            {"type": "sltp", "t": T0 + 700, "ticket": 5, "sl": 4098.0, "tp": 4104.0},
            {"type": "ai", "t": T0 + 650, "title": "[XAUUSD] BUY", "color": "#26a69a", "detail": "chi tiết", "action": "BUY",
             "sl": 4098.0, "tp": 4104.0, "tf": "M5", "pred": [[T0 + 900, 4100, 4101, 4099.5, 4100.8]], "fan": [[4100.5]]},
            {"type": "zone", "t": T0 + 660, "action": "BUY", "lo": 4099.0, "hi": 4100.5},
            {"type": "log", "t": T0 + 650, "msg": "AI báo BUY"},
            {"type": "t", "t": T0 + 1000},
        ])
        self.assertEqual(p.frontier, T0 + 1000)
        p.seek(T0 + 800)
        p.render()
        self.assertIn("BUY", p.hdr_title.cget("text"))
        self.assertIn("AI báo BUY", p.log_text.get("1.0", "end"))
        self.assertEqual(p.n_closed_at_cursor(), 0)
        p.add_events([{"type": "close", "t": T0 + 900, "ticket": 5, "side": "BUY", "open_ms": ms_o, "close_ms": (T0 + 900) * 1000,
                       "price_open": 4100.2, "price_close": 4104.0, "volume": 0.02, "profit": 7.6, "reason": "TP",
                       "reason_label": "Chốt lãi (TP)", "sl0": 4098.0}])
        self.assertNotIn(5, p._open_recs)
        p.seek(T0 + 950)
        self.assertEqual(p.n_closed_at_cursor(), 1)
        self.assertEqual(p.closed_upto_cursor()[0]["ticket"], 5)
        self.assertEqual(p.closed_upto_cursor()[0]["type"], "BUY")
        p.seek(T0 + 5000)  # kéo quá mốc đã tính -> dừng ở mốc
        self.assertLessEqual(p.t, T0 + 1000)
        p.finish()
        p.seek(T0 + 3400)
        self.assertAlmostEqual(p.t, T0 + 3400)
        p.render()
        p.close()

    def test_busy_ui_keeps_real_speed(self):
        # App bận (nhịp vẽ cách nhau 1 giây): 10x vẫn phải tiến ~10 giây biểu đồ, không bị chặn 0.25s/nhịp như trước
        p = rp.ReplayPlayer(self.root, series(), [], [], T0 + 60, T0 + 3500)
        p.set_speed("10x")
        p.play()
        t_before = p.t
        p._last = time.perf_counter() - 1.0
        p._tick()
        self.assertGreater(p.t - t_before, 9.0)
        p.close()

    def test_toplevel_mode_still_works(self):
        p = rp.ReplayPlayer(self.root, series(), [], [], T0 + 60, T0 + 3500)
        self.assertIsNone(p.log_text)
        self.assertFalse(p.live)
        p.render()
        p.close()


class TestAppWorkspace(unittest.TestCase):
    def setUp(self):
        try:
            self.tk, self.root = make_tk()
        except Exception as e:
            self.skipTest(f"Không dựng được Tk: {e}")
        self.addCleanup(self.root.destroy)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        tk = self.tk
        app = object.__new__(b.KronosDesktopApp)
        app.root = self.root
        app.right_panel = tk.Frame(self.root)
        app.right_panel.pack()
        app.live_sig_box = tk.Frame(app.right_panel)
        app.live_sig_box.pack(fill=tk.X)
        app.right_paned = tk.Frame(app.right_panel)
        app.right_paned.pack()
        app.chart_holder = tk.Frame(app.right_paned)
        app.chart_holder.pack()
        app.live_chart_frame = tk.Frame(app.chart_holder)
        app.live_chart_frame.pack()
        app._write_file_log = lambda *a, **k: None
        self.app = app

    def test_switch_live_and_backtest(self):
        app = self.app
        app._bt_set_workspace_visible(True)
        self.assertEqual(app.bt_ws_frame.winfo_manager(), "pack")
        self.assertEqual(app.live_chart_frame.winfo_manager(), "")
        self.assertEqual(app.live_sig_box.winfo_manager(), "")
        app._bt_set_workspace_visible(False)
        self.assertEqual(app.bt_ws_frame.winfo_manager(), "")
        self.assertEqual(app.live_chart_frame.winfo_manager(), "pack")
        self.assertEqual(app.live_sig_box.winfo_manager(), "pack")
        self.assertEqual(app.right_panel.pack_slaves()[0], app.live_sig_box)  # dòng tín hiệu live trở lại đúng chỗ (trên biểu đồ)
        self.assertFalse(app._bt_workspace_shown())
        app._bt_set_workspace_visible(True)
        self.assertTrue(app._bt_workspace_shown())  # vòng tick live dùng cờ này để không vẽ biểu đồ live đang ẩn

    def test_live_player_and_table_follow_cursor(self):
        app = self.app
        run_dir = self.tmp
        np.savez_compressed(os.path.join(run_dir, "player.npz"), **series())
        app._bt_run_dir = run_dir
        app._bt_live_meta = {"capital": 1000.0}
        rendered = []
        app._render_backtest_results = lambda tr, eq, cap, final=True: rendered.append((len(tr), final))
        app._format_duration = lambda s: f"{s}s"
        with mock.patch.object(app.root, "after", lambda *a, **k: None):
            app._bt_on_data_ready({"type": "data_ready", "start": T0 + 60, "end": T0 + 3500, "contract": 100.0, "tf": "M1"})
        p = app._bt_player
        self.assertTrue(p.live and p.playing)
        with open(os.path.join(run_dir, "events.jsonl"), "w", encoding="utf-8") as f:
            for ev in ({"type": "open", "t": T0 + 100, "ticket": 9, "side": "SELL", "open_ms": (T0 + 100) * 1000, "price": 4100.0, "vol": 0.02},
                       {"type": "close", "t": T0 + 200, "ticket": 9, "side": "SELL", "open_ms": (T0 + 100) * 1000,
                        "close_ms": (T0 + 200) * 1000, "price_open": 4100.0, "price_close": 4099.0, "volume": 0.02, "profit": 2.0,
                        "reason": "TP", "reason_label": "Chốt lãi (TP)", "sl0": 4102.0},
                       {"type": "t", "t": T0 + 400}):
                f.write(json.dumps(ev) + "\n")
            f.write('{"type": "log", "t": 1')  # dòng đang ghi dở: bỏ qua
        app._bt_feed_live_events()
        self.assertEqual(p.frontier, T0 + 400)
        p.seek(T0 + 150)
        app._bt_sync_table_to_cursor()
        self.assertEqual(rendered[-1], (0, False))  # chưa tới lúc đóng lệnh -> bảng trống, vẽ nhẹ
        p.seek(T0 + 300)
        app._bt_sync_table_to_cursor()
        self.assertEqual(rendered[-1], (1, False))
        self.assertEqual(app._bt_trades_cache[0]["reason"], "Chốt lãi (TP)")
        self.assertEqual(app._bt_trades_cache[0]["type"], "SELL")
        app._bt_close_ws_player()
        self.assertIsNone(app._bt_player)

    def test_render_results_light_mode_skips_status_and_report(self):
        app = self.app
        app._update_bt_status = mock.Mock()
        app._emit_backtest_summary = mock.Mock()
        app._draw_equity_curve = mock.Mock()
        trades = [{"ticket": "#0001", "in_time": "", "out_time": "", "duration": "", "type": "BUY", "lot": "0.02",
                   "in_price": "1", "out_price": "2", "pnl": 2.0, "reason": "TP"}]
        app._render_backtest_results(trades, [(0, 1000.0), (1, 1002.0)], 1000.0, final=False)
        app._update_bt_status.assert_not_called()
        app._emit_backtest_summary.assert_not_called()
        app._draw_equity_curve.assert_called_once()
        app._render_backtest_results(trades, [(0, 1000.0), (1, 1002.0)], 1000.0)
        app._update_bt_status.assert_called_once()
        app._emit_backtest_summary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
