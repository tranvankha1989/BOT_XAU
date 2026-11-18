"""Test debug_trace: ghi trace JSONL khi bật, lỗi ghi không ảnh hưởng, mặc định tắt."""
import json
import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mt5_scalping_bot as b  # noqa: E402


def _app(trace_dir):
    app = object.__new__(b.KronosDesktopApp)
    app._log_file_lock = threading.Lock()
    app._trace_dir = trace_dir
    return app


class TestDebugTrace(unittest.TestCase):
    def test_mac_dinh_tat(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["debug_trace"], False)

    def test_ghi_jsonl_co_ts_va_profile(self):
        with tempfile.TemporaryDirectory() as d:
            app = _app(d)
            app._trace_run = "replay"
            app._write_trace("XAUUSD", {"event": "analysis", "decision": {"action": None}})
            files = os.listdir(d)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].startswith("trace_") and files[0].endswith(".jsonl"))
            with open(os.path.join(d, files[0]), encoding="utf-8") as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["run"], "replay")
            self.assertEqual(rec["profile"], "XAUUSD")
            self.assertEqual(rec["event"], "analysis")
            self.assertIn("ts", rec)

    def test_gia_tri_khong_json_duoc_chuyen_chuoi(self):
        with tempfile.TemporaryDirectory() as d:
            app = _app(d)
            app._write_trace("XAUUSD", {"x": object()})
            with open(os.path.join(d, os.listdir(d)[0]), encoding="utf-8") as f:
                self.assertIn("object", json.loads(f.readline())["x"])

    def test_loi_ghi_bi_nuot(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = os.path.join(d, "khong_phai_thu_muc")
            open(blocker, "w").close()
            app = _app(blocker)  # đường dẫn là file -> makedirs/open lỗi, không được ném ra ngoài
            app._write_trace("XAUUSD", {"event": "analysis"})


if __name__ == "__main__":
    unittest.main()
