"""Test A11: lưu/nạp initial_r_cache (ghi atomic), dọn ticket không còn mở, file hỏng không văng ngoại lệ. Toàn bộ mock."""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

PROFILES = {"XAUUSD": {"symbol": "XAUUSDm"}, "BTCUSD": {"symbol": "BTCUSDm"}}


def make_app():
    app = object.__new__(b.KronosDesktopApp)
    app.__dict__.update(initial_r_cache={}, _initial_r_saved={}, profile_ctx={k: {} for k in PROFILES},
                        profile_settings={k: dict(v) for k, v in PROFILES.items()}, _write_file_log=mock.Mock())
    return app


def pos(t):
    return SimpleNamespace(ticket=t)


class TestA11(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = [1000.0]
        for p in (mock.patch.object(b, "SETTINGS_DIR", self.tmp.name),
                  mock.patch.object(b.time, "monotonic", side_effect=lambda: self.clock[0])):
            p.start()
            self.addCleanup(p.stop)
        self.path = os.path.join(self.tmp.name, "initial_r_cache.json")

    def test_ghi_atomic_va_nap_lai(self):
        app = make_app()
        app.initial_r_cache.update({111: 2.5, 222: 4.0})
        real_replace = os.replace
        seen = {}

        def spy(src, dst):
            seen["src"], seen["dst"] = src, dst
            with open(src, encoding="utf-8") as fh:  # .tmp đã đầy đủ và hợp lệ trước khi replace
                seen["tmp_json"] = json.load(fh)
            self.assertFalse(os.path.exists(dst))
            return real_replace(src, dst)
        with mock.patch.object(b.os, "replace", side_effect=spy):
            app._save_initial_r_cache_if_changed()
        self.assertEqual(seen["src"], self.path + ".tmp")
        self.assertEqual(seen["dst"], self.path)
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertEqual(seen["tmp_json"]["r"], {"111": 2.5, "222": 4.0})
        # nạp lại bằng instance mới
        app2 = make_app()
        app2._load_initial_r_cache()
        self.assertEqual(app2.initial_r_cache, {111: 2.5, 222: 4.0})

    def test_chi_ghi_khi_thay_doi(self):
        app = make_app()
        app.initial_r_cache[111] = 2.5
        with mock.patch.object(b.os, "replace", wraps=os.replace) as rp:
            for _ in range(5):
                app._save_initial_r_cache_if_changed()
            self.assertEqual(rp.call_count, 1)
            app.initial_r_cache[333] = 1.0
            app._save_initial_r_cache_if_changed()
            app._save_initial_r_cache_if_changed()
            self.assertEqual(rp.call_count, 2)

    def test_file_hong_khong_van_ngoai_le(self):
        for content in ("{not json", "[]", '{"r": 5}', '{"r": {"abc": 1, "7": "x", "8": -3, "9": 1e99, "10": 2.0}}', ""):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(content)
            app = make_app()
            app._load_initial_r_cache()  # không được ném ngoại lệ
            self.assertEqual(app.initial_r_cache, {10: 2.0} if "10" in content else {})
        os.remove(self.path)
        app = make_app()
        app._load_initial_r_cache()  # file thiếu
        self.assertEqual(app.initial_r_cache, {})

    def test_loi_ghi_khong_van_ngoai_le(self):
        app = make_app()
        app.initial_r_cache[1] = 1.0
        with mock.patch.object(b.os, "replace", side_effect=OSError("disk")):
            app._save_initial_r_cache_if_changed()
            app._save_initial_r_cache_if_changed()  # trong 30s backoff: không thử lại
        self.assertEqual(app._write_file_log.call_count, 1)

    def test_don_ticket_khong_con_mo(self):
        app = make_app()
        app.initial_r_cache.update({1: 2.0, 2: 3.0, 3: 4.0, 4: 5.0})  # 1,2 của XAU; 3 của BTC; 4 đã đóng
        # mới có XAU báo cáo -> chưa dọn (BTC chưa báo, tránh xóa nhầm lệnh BTC)
        app._sync_initial_r_cache("XAUUSD", [pos(1), pos(2)])
        self.clock[0] += 100.0
        app._sync_initial_r_cache("XAUUSD", [pos(1), pos(2)])
        self.assertEqual(set(app.initial_r_cache), {1, 2, 3, 4})
        # BTC báo cáo: ticket 4 bắt đầu bị tính vắng mặt, chưa đủ 30s -> còn
        app._sync_initial_r_cache("BTCUSD", [pos(3)])
        self.assertIn(4, app.initial_r_cache)
        self.clock[0] += 31.0
        app._sync_initial_r_cache("XAUUSD", [pos(1), pos(2)])
        self.assertEqual(set(app.initial_r_cache), {1, 2, 3})  # 4 bị dọn; lệnh của profile kia (3) giữ nguyên
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)["r"]), {"1", "2", "3"})

    def test_ticket_quay_lai_trong_30s_khong_bi_don(self):
        app = make_app()
        app.initial_r_cache[1] = 2.0
        app._sync_initial_r_cache("XAUUSD", [pos(1)])
        app._sync_initial_r_cache("BTCUSD", [])
        self.clock[0] += 20.0
        app._sync_initial_r_cache("XAUUSD", [])      # MT5 đọc lỗi tạm thời -> danh sách rỗng
        self.clock[0] += 5.0
        app._sync_initial_r_cache("XAUUSD", [pos(1)])  # đọc lại bình thường
        self.clock[0] += 60.0
        app._sync_initial_r_cache("XAUUSD", [pos(1)])
        self.assertIn(1, app.initial_r_cache)


if __name__ == "__main__":
    unittest.main()
