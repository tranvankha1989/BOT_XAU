"""Xuất / Nhập file cấu hình KHÔNG chép thông số riêng từng máy (Magic, đường dẫn MT5, số luồng AI, toàn bộ Mục 5 Telegram)
và trạng thái riêng máy (tab đang mở, chiều cao bảng dưới, bot đang chạy tự động). Mock, không Tk thật, không MT5."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

A = b.KronosDesktopApp
TG_A = {"telegram_enabled": True, "telegram_bot_token": "TOKEN_A", "telegram_chat_id": "CHAT_A",
        "telegram_notify_open": True, "telegram_notify_close": False}
TG_B = {"telegram_enabled": False, "telegram_bot_token": "TOKEN_B", "telegram_chat_id": "CHAT_B",
        "telegram_notify_open": False, "telegram_notify_close": True}
LOCAL = {
    "rr": "2", "magic_number": "111", "mt5_path": "C:/MT5_A/terminal64.exe", "ai_threads": "2", **TG_A,
    "last_active_tab": "XAUUSD", "bottom_panel_height": 240, "auto_running_profiles": ["XAUUSD"],
    "profiles": {
        "XAUUSD": {"rr": "2", "magic_number": "111", "mt5_path": "C:/MT5_A/terminal64.exe", "ai_threads": "2", **TG_A},
        "BTCUSD": {"rr": "2", "magic_number": "112", "mt5_path": "C:/MT5_A/terminal64.exe", "ai_threads": "2", **TG_A},
    },
}
OTHER = {
    "rr": "3", "magic_number": "999", "mt5_path": "D:/Other/terminal64.exe", "ai_threads": "6", **TG_B,
    "last_active_tab": "BTCUSD", "bottom_panel_height": 400, "auto_running_profiles": ["XAUUSD", "BTCUSD"],
    "profiles": {
        "XAUUSD": {"rr": "3", "be_trigger_r": "1.2", "magic_number": "999", "mt5_path": "D:/Other/terminal64.exe", "ai_threads": "6", **TG_B},
        "BTCUSD": {"rr": "3", "magic_number": "998", "mt5_path": "D:/Other/terminal64.exe", "ai_threads": "6", **TG_B},
    },
}
MACHINE = set(A.MACHINE_LOCAL_KEYS) | set(A.MACHINE_LOCAL_STATE_KEYS)


class TestPure(unittest.TestCase):
    def test_section5_telegram_is_machine_local(self):
        self.assertTrue(set(A.SETTINGS_SECTION_MAP[5][1]) <= set(A.MACHINE_LOCAL_KEYS))
        self.assertTrue({"magic_number", "mt5_path", "ai_threads"} <= set(A.MACHINE_LOCAL_KEYS))

    def test_strip_removes_machine_keys_without_mutating(self):
        src = copy.deepcopy(OTHER)
        out = A._strip_machine_local(src)
        self.assertEqual(src, OTHER)
        self.assertFalse(MACHINE & set(out))
        for p in out["profiles"].values():
            self.assertFalse(set(A.MACHINE_LOCAL_KEYS) & set(p))
        self.assertEqual(out["profiles"]["XAUUSD"]["be_trigger_r"], "1.2")
        self.assertEqual(out["rr"], "3")

    def test_merge_keeps_this_machine(self):
        out = A._merge_machine_local(copy.deepcopy(OTHER), copy.deepcopy(LOCAL))
        self.assertEqual(out["profiles"]["XAUUSD"]["magic_number"], "111")
        self.assertEqual(out["profiles"]["BTCUSD"]["magic_number"], "112")
        self.assertEqual(out["profiles"]["XAUUSD"]["mt5_path"], "C:/MT5_A/terminal64.exe")
        self.assertEqual(out["profiles"]["BTCUSD"]["ai_threads"], "2")
        self.assertEqual(out["profiles"]["XAUUSD"]["rr"], "3")  # thông số chiến lược lấy theo file
        self.assertEqual((out["last_active_tab"], out["bottom_panel_height"], out["auto_running_profiles"]),
                         ("XAUUSD", 240, ["XAUUSD"]))
        # máy mới chưa có cài đặt -> bỏ khóa (nạp dùng mặc định), không lấy giá trị máy khác
        fresh = A._merge_machine_local(copy.deepcopy(OTHER), {})
        self.assertFalse(MACHINE & set(fresh))
        self.assertNotIn("magic_number", fresh["profiles"]["XAUUSD"])
        # file lạ: profiles không phải dict -> không lỗi
        self.assertEqual(A._merge_machine_local({"profiles": [1]}, LOCAL)["profiles"], [1])


class TestExportImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sf = os.path.join(self.tmp, "user_settings.json")
        for name, val in (("SETTINGS_DIR", self.tmp), ("SETTINGS_FILE", self.sf)):
            p = mock.patch.object(b, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.app = object.__new__(A)
        self.app.__dict__.update(
            root=None, active_profile_key="XAUUSD", profile_settings={}, log=mock.Mock(), log_error=mock.Mock(),
            show_toast=mock.Mock(), save_user_settings=mock.Mock(return_value=True),
            load_user_settings=mock.Mock(), apply_live_settings_and_rescan=mock.Mock(),
        )

    def write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_export_only_active_tab(self):
        self.write(self.sf, LOCAL)
        for pk, tag in (("XAUUSD", "XAU"), ("BTCUSD", "BTC")):
            self.app.active_profile_key = pk
            out = os.path.join(self.tmp, f"xuat_{tag}.json")
            with mock.patch.object(b.filedialog, "asksaveasfilename", return_value=out) as dlg:
                self.app.export_settings_file()
            self.assertTrue(dlg.call_args[1]["initialfile"].startswith(f"{tag}_settings_"))
            data = self.read(out)
            self.assertEqual(list(data["profiles"]), [pk])  # chỉ tab đang mở
            self.assertEqual(data["profile"], pk)
            self.assertFalse(set(A.PROFILE_IO_EXCLUDED) & set(data["profiles"][pk]))  # không Magic/MT5/Telegram/HF token
            self.assertEqual(data["profiles"][pk]["rr"], "2")
        self.assertEqual(self.read(self.sf), LOCAL)  # cài đặt máy này không đổi

    def test_import_dung_tab_chi_doi_tab_do(self):
        self.write(self.sf, LOCAL)
        self.app.active_profile_key = "BTCUSD"
        src = os.path.join(self.tmp, "BTC_settings_x.json")
        self.write(src, {"profile": "BTCUSD", "profiles": {"BTCUSD": {"rr": "3", "magic_number": "998", **TG_B}}})
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=src):
            self.app.import_settings_file()
        data = self.read(self.sf)
        self.assertEqual(data["profiles"]["BTCUSD"]["rr"], "3")
        self.assertEqual(data["profiles"]["BTCUSD"]["magic_number"], "112")  # Magic giữ của máy này
        self.assertEqual({k: data["profiles"]["BTCUSD"][k] for k in TG_A}, TG_A)  # Telegram giữ của máy này
        self.assertEqual(data["profiles"]["XAUUSD"], LOCAL["profiles"]["XAUUSD"])  # tab Vàng không đổi
        self.app.load_user_settings.assert_called_once()
        self.app.apply_live_settings_and_rescan.assert_called_once_with(profile_key="BTCUSD", source_label="Nhập File Cấu Hình")
        self.assertFalse(self.app.show_toast.call_args[1].get("is_error", False))

    def test_import_sai_tab_bao_loi_khong_doi_gi(self):
        self.write(self.sf, LOCAL)
        self.app.active_profile_key = "XAUUSD"
        src = os.path.join(self.tmp, "BTC_settings_x.json")
        self.write(src, {"profile": "BTCUSD", "profiles": {"BTCUSD": {"rr": "3"}}})
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=src), mock.patch.object(b.messagebox, "showerror") as err:
            self.app.import_settings_file()
        err.assert_called_once()
        self.assertIn("Sai cấu hình", err.call_args[0][1])
        self.assertTrue(self.app.show_toast.call_args[1].get("is_error"))
        self.assertEqual(self.read(self.sf), LOCAL)
        self.app.load_user_settings.assert_not_called()
        self.app.apply_live_settings_and_rescan.assert_not_called()

    def test_import_file_cu_2_tab_chi_lay_tab_dang_mo(self):
        self.write(self.sf, LOCAL)
        src = os.path.join(self.tmp, "may_khac.json")
        self.write(src, OTHER)
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=src):
            self.app.import_settings_file()  # đang mở tab Vàng
        data = self.read(self.sf)
        self.assertEqual(data["profiles"]["XAUUSD"]["rr"], "3")
        self.assertEqual(data["profiles"]["XAUUSD"]["be_trigger_r"], "1.2")
        self.assertEqual(data["profiles"]["XAUUSD"]["magic_number"], "111")
        self.assertEqual(data["profiles"]["BTCUSD"], LOCAL["profiles"]["BTCUSD"])  # tab BTC không đổi
        self.assertEqual((data["magic_number"], data["last_active_tab"], data["auto_running_profiles"]), ("111", "XAUUSD", ["XAUUSD"]))

    def test_import_on_fresh_machine_uses_defaults(self):
        src = os.path.join(self.tmp, "may_khac.json")
        self.write(src, OTHER)
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=src):
            self.app.import_settings_file()
        data = self.read(self.sf)
        self.assertFalse(MACHINE & set(data))
        self.assertNotIn("magic_number", data["profiles"]["XAUUSD"])
        self.assertEqual(data["profiles"]["XAUUSD"]["rr"], "3")
        self.assertNotIn("BTCUSD", data["profiles"])  # không chép tab BTC của máy khác


if __name__ == "__main__":
    unittest.main()
