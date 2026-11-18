"""Tác vụ backtest code live cho app (tools/live_replay.run_replay_job) + phía app (lệnh khởi chạy, đọc tiến độ, đổ kết quả).
Toàn bộ mock: không MT5, không AI."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import live_replay as lr  # noqa: E402
import mt5_scalping_bot as b  # noqa: E402


def fake_sim_result():
    ms = np.arange(1_794_268_800_000, 1_794_268_800_000 + 7200_000, 500, dtype=np.int64)
    bid = 4100 + np.sin(np.arange(len(ms)) / 50.0)
    closed = [{"ticket": 100001, "type": "BUY", "open_ms": int(ms[100]), "close_ms": int(ms[900]), "price_open": 4100.5,
               "price_close": 4102.0, "sl0": 4098.0, "tp0": 4105.0, "sl_last": 4100.85, "volume": 0.02, "profit": 3.0,
               "reason": "Bot đóng", "comment": "KR_TIME"},
              {"ticket": 100003, "type": "SELL", "open_ms": int(ms[2000]), "close_ms": int(ms[2400]), "price_open": 4101.0,
               "price_close": 4103.0, "sl0": 4103.0, "tp0": 4097.0, "sl_last": 4103.0, "volume": 0.02, "profit": -4.0,
               "reason": "SL", "comment": "[sl 4103.00]"}]
    return {"closed": closed, "rejects": [], "logs": [(1_794_268_900.0, "log 1")], "errors": [], "n_analyses": 24,
            "cache": SimpleNamespace(hits=3, misses=21), "end_utc": 1_794_272_399.0, "contract": 100.0,
            "sltp": [(int(ms[100]), 100001, 4098.0, 4105.0), (int(ms[500]), 100001, 4100.85, 4105.0)],
            "ticks_ms": ms, "ticks_bid": bid}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for p in (mock.patch.object(b, "SETTINGS_DIR", self.tmp),):
            p.start()
            self.addCleanup(p.stop)

    def write_job(self, **over):
        job = {"profile": "XAUUSD", "symbol": "XAUUSDm", "cfg": {"lot": "0.02", "max_hold_mins": "10", "hf_token": "X"},
               "start": "2026-11-10", "end": "2026-11-10", "capital": 1000, "threads": 1, "seed": 1,
               "out_dir": os.path.join(self.tmp, "run_1")}
        job.update(over)
        os.makedirs(job["out_dir"], exist_ok=True)
        path = os.path.join(job["out_dir"], "job.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(job, f)
        return path, job["out_dir"]

    def events(self, out_dir):
        with open(os.path.join(out_dir, "progress.jsonl"), encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]


class TestRunJob(Base):
    def test_done_writes_result_player_log(self):
        path, out = self.write_job()
        seen = {}

        def fake_sim(bot_mod, pk, cfg, symbol, start_utc, end_utc, **kw):
            seen.update(cfg=dict(cfg), symbol=symbol, kw=kw)
            kw["progress"](50, start_utc + 100, 1, 12, None)
            return fake_sim_result()

        with mock.patch.object(lr, "simulate", side_effect=fake_sim):
            self.assertEqual(lr.run_replay_job(path), 0)
        self.assertEqual(seen["symbol"], "XAUUSDm")
        self.assertEqual(str(seen["cfg"]["max_hold_mins"]), "10")
        self.assertNotEqual(seen["cfg"].get("hf_token"), "X")  # token trong tác vụ không bao giờ vào cấu hình chạy (chỉ còn mặc định rỗng)
        self.assertEqual(seen["kw"]["progress_step"], 1)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
            res = json.load(f)
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["summary"]["n"], 2)
        self.assertEqual([c["reason_label"] for c in res["closed"]], ["Hết giờ giữ lệnh", "Cắt lỗ (SL)"])
        self.assertEqual(len(res["sltp"]), 2)
        with np.load(os.path.join(out, "player.npz")) as z:
            self.assertTrue(len(z["sec"]) > 0)
            self.assertTrue(np.all(z["h"] >= z["l"]))
        self.assertTrue(os.path.exists(os.path.join(out, "log.txt")))
        types_ = [e["type"] for e in self.events(out)]
        self.assertEqual(types_[0], "start")
        self.assertIn("progress", types_)
        self.assertEqual(types_[-1], "done")

    def test_cancel(self):
        path, out = self.write_job()

        def fake_sim(bot_mod, pk, cfg, symbol, start_utc, end_utc, **kw):
            open(os.path.join(out, "cancel"), "w").close()
            kw["progress"](10, start_utc, 0, 1, None)
            return fake_sim_result()

        with mock.patch.object(lr, "simulate", side_effect=fake_sim):
            self.assertEqual(lr.run_replay_job(path), 3)
        self.assertEqual(self.events(out)[-1]["type"], "cancelled")
        self.assertFalse(os.path.exists(os.path.join(out, "result.json")))

    def test_error_reported(self):
        path, out = self.write_job()
        with mock.patch.object(lr, "simulate", side_effect=RuntimeError("Không kết nối được MT5")):
            self.assertEqual(lr.run_replay_job(path), 1)
        ev = self.events(out)[-1]
        self.assertEqual(ev["type"], "error")
        self.assertIn("MT5", ev["msg"])

    def test_cli_job_flag(self):
        path, _out = self.write_job()
        with mock.patch.object(lr, "run_replay_job", return_value=0) as m:
            self.assertEqual(lr.main(["--job", path]), 0)
        m.assert_called_once_with(path)


class TestHelpers(unittest.TestCase):
    def test_player_data_ohlc_per_second(self):
        ms = np.array([1000, 1200, 1900, 2100, 2500, 9000], dtype=np.int64)
        bid = np.array([5.0, 7.0, 6.0, 4.0, 4.5, 9.0])
        d = lr.build_player_data(ms, bid, 1.0, 2.9, context_sec=0)
        self.assertEqual(list(d["sec"]), [1, 2])
        self.assertEqual(list(d["o"]), [5.0, 4.0])
        self.assertEqual(list(d["h"]), [7.0, 4.5])
        self.assertEqual(list(d["l"]), [5.0, 4.0])
        self.assertEqual(list(d["c"]), [6.0, 4.5])

    def test_attr_patch_restores(self):
        ns = SimpleNamespace(a=1)
        p = lr._AttrPatch(ns, "a", 2)
        p.start()
        self.assertEqual(ns.a, 2)
        p.stop()
        self.assertEqual(ns.a, 1)
        q = lr._AttrPatch(ns, "new_attr", 5)
        q.start()
        q.stop()
        self.assertFalse(hasattr(ns, "new_attr"))

    def test_reason_labels(self):
        self.assertEqual(lr.close_reason_label({"reason": "TP"}), "Chốt lãi (TP)")
        self.assertEqual(lr.close_reason_label({"reason": "Bot đóng", "comment": "KR_TIMEALL"}), "Hết giờ giữ lệnh (đóng tất)")
        self.assertEqual(lr.close_reason_label({"reason": "Đóng cuối kỳ", "comment": "AI_XAUUSD"}), "Đóng cuối kỳ")


class TestAppSide(Base):
    def test_command_source_mode(self):
        cmd, env = b.KronosDesktopApp._bt_build_replay_command("C:/x/job.json")
        self.assertEqual(cmd[-2:], ["--job", "C:/x/job.json"])
        self.assertTrue(cmd[2].endswith(os.path.join("tools", "live_replay.py")))
        self.assertNotIn("KRONOS_REPLAY_JOB", env)

    def test_command_frozen_mode(self):
        with mock.patch.object(b.sys, "frozen", True, create=True), mock.patch.object(b.sys, "executable", "C:/App/Trading AI PRO.exe"):
            cmd, env = b.KronosDesktopApp._bt_build_replay_command("C:/x/job.json")
        self.assertEqual(cmd, ["C:/App/Trading AI PRO.exe"])
        self.assertEqual(env["KRONOS_REPLAY_JOB"], "C:/x/job.json")

    def test_progress_reader_partial_lines(self):
        app = object.__new__(b.KronosDesktopApp)
        app._bt_run_dir = self.tmp
        app._bt_prog_pos = 0
        path = os.path.join(self.tmp, "progress.jsonl")
        with open(path, "wb") as f:
            f.write(json.dumps({"type": "start"}).encode() + b"\n" + b'{"type": "progr')
        self.assertEqual([e["type"] for e in app._bt_read_progress_events()], ["start"])
        with open(path, "ab") as f:
            f.write(b'ess", "pct": 5}\n')
        ev = app._bt_read_progress_events()
        self.assertEqual(ev, [{"type": "progress", "pct": 5}])
        self.assertEqual(app._bt_read_progress_events(), [])

    def test_show_result_maps_trades(self):
        app = object.__new__(b.KronosDesktopApp)
        app.__dict__.update(_render_backtest_results=mock.Mock(), _update_bt_status=mock.Mock(), log=mock.Mock(),
                            bt_tree=SimpleNamespace(bind=lambda *a, **k: None))
        res = fake_sim_result()
        for c in res["closed"]:
            c["reason_label"] = lr.close_reason_label(c)
        app._bt_show_live_result({"capital": 1000, "symbol": "XAUUSDm", "closed": res["closed"], "summary": lr.summarize(res["closed"]),
                                  "n_analyses": 24, "minutes": 1.0, "n_errors": 0}, self.tmp)
        trades, eq, cap = app._render_backtest_results.call_args[0]
        self.assertEqual([t["ticket"] for t in trades], ["#0001", "#0002"])
        self.assertEqual(trades[1]["reason"], "Cắt lỗ (SL)")
        self.assertEqual(trades[0]["mt5_ticket"], 100001)
        self.assertEqual(eq[-1], (2, 999.0))
        self.assertEqual(cap, 1000.0)

    def test_child_threads_safe(self):
        with mock.patch.object(b.os, "cpu_count", return_value=16):
            self.assertEqual(b.KronosDesktopApp._bt_child_threads(), 2)
        with mock.patch.object(b.os, "cpu_count", return_value=2):
            self.assertEqual(b.KronosDesktopApp._bt_child_threads(), 1)

    def test_packed_hook_noop_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KRONOS_REPLAY_JOB", None)
            self.assertIsNone(b._run_packed_replay_job_from_env())


if __name__ == "__main__":
    unittest.main()
