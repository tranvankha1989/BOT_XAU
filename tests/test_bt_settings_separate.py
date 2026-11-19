"""Cấu hình backtest TÁCH khỏi cấu hình bot live (mock, không Tk thật, không MT5)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

PK = "XAUUSD"


class V:
    """Biến giả thay tk.StringVar/BooleanVar."""
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


def make_app(live=None, sym="XAUUSDm", lot="0.02"):
    app = object.__new__(b.KronosDesktopApp)
    live_cfg = {"symbol": "XAUUSDm", "lot": "0.02", "max_hold_mins": "10", "auto_be": True, "consensus": "75",
                "hf_token": "SECRET", "telegram_bot_token": "SECRET2", "telegram_chat_id": "123",
                "trend_filter": False, "magic_number": "777", "tf": "M1", "analysis_tf": "M5"}
    live_cfg.update(live or {})
    app.__dict__.update(
        active_profile_key=PK, profile_settings={PK: dict(live_cfg), "BTCUSD": {}}, _loading_settings=False,
        bt_sym_var=V(sym), bt_lot_var=V(lot), _write_file_log=mock.Mock(),
        _get_settings_vars_map=lambda: {k: V(v) for k, v in live_cfg.items()},
    )
    return app


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(b, "SETTINGS_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def saved(self):
        with open(os.path.join(self.tmp, b.KronosDesktopApp.BT_SETTINGS_FILE_NAME), encoding="utf-8") as f:
            return json.load(f)


class TestBtSettings(Base):
    def test_first_use_copies_live_and_strips_secrets(self):
        app = make_app()
        cfg = app._get_bt_cfg(PK)
        self.assertEqual(cfg["max_hold_mins"], "10")
        for k in ("hf_token", "telegram_bot_token", "telegram_chat_id"):
            self.assertNotIn(k, cfg)
        data = self.saved()
        self.assertIn(PK, data)
        self.assertNotIn("hf_token", data[PK])
        self.assertNotIn("telegram_bot_token", data[PK])
        self.assertNotIn("lot", data[PK])  # Lot lấy theo thanh Backtest

    def test_edit_backtest_does_not_touch_live(self):
        app = make_app()
        app._get_bt_cfg(PK)
        app.bt_settings[PK]["max_hold_mins"] = "15"
        app.bt_settings[PK]["auto_be"] = False
        self.assertEqual(app._get_bt_cfg(PK)["max_hold_mins"], "15")
        self.assertEqual(app.profile_settings[PK]["max_hold_mins"], "10")
        self.assertIs(app.profile_settings[PK]["auto_be"], True)

    def test_symbol_lot_from_toolbar(self):
        app = make_app(lot="0.05")
        cfg = app._get_bt_cfg(PK)
        self.assertEqual(cfg["lot"], "0.05")
        self.assertEqual(cfg["symbol"], "XAUUSDm")

    def test_load_roundtrip(self):
        app = make_app()
        app._get_bt_cfg(PK)
        app.bt_settings[PK]["consensus"] = "80"
        app._save_bt_settings()
        app2 = make_app()
        self.assertEqual(app2._get_bt_cfg(PK)["consensus"], "80")

    def test_corrupt_file_falls_back_to_live(self):
        with open(os.path.join(self.tmp, b.KronosDesktopApp.BT_SETTINGS_FILE_NAME), "w", encoding="utf-8") as f:
            f.write("{hỏng")
        app = make_app()
        self.assertEqual(app._get_bt_cfg(PK)["consensus"], "75")

    def test_collect_changes(self):
        app = make_app(lot="0.05")
        app._get_bt_cfg(PK)
        self.assertEqual([c[0] for c in app._bt_collect_changes(PK)], ["lot"])
        app.bt_settings[PK]["max_hold_mins"] = "15.0"
        app.bt_settings[PK]["consensus"] = "75.0"     # cùng giá trị -> không tính
        app.bt_settings[PK]["auto_be"] = "True"       # cùng giá trị -> không tính
        app.bt_settings[PK]["trend_filter"] = True    # khóa ẩn -> không chép sang live
        keys = sorted(c[0] for c in app._bt_collect_changes(PK))
        self.assertEqual(keys, ["lot", "max_hold_mins"])

    def test_norm(self):
        n = b.KronosDesktopApp._bt_norm
        self.assertEqual(n(True), n("True"))
        self.assertEqual(n(False), n("false"))
        self.assertEqual(n("15"), n("15.0"))
        self.assertNotEqual(n("15"), n("10"))

    def test_section_map_shared(self):
        m = b.KronosDesktopApp.SETTINGS_SECTION_MAP
        self.assertEqual(sorted(m.keys()), [1, 2, 3, 4, 5])
        self.assertIn("max_hold_mins", m[3][1])
        self.assertIn("allow_opposite_entry", m[2][1])

    def test_param_spec_from_menu_meta(self):
        app = make_app()
        var = V("x")
        app._param_meta = {str(var): {"type": "coded", "label": "   Chế độ xử lý lệnh đang lỗ:", "code_labels": {"wait_be": "Chờ hồi về Entry"}}}
        spec = app._bt_param_spec("rev_loss_mode", var)
        self.assertEqual(spec["label"], "Chế độ xử lý lệnh đang lỗ")
        self.assertEqual(spec["type"], "coded")
        self.assertEqual(app._bt_show_val("rev_loss_mode", "wait_be", var), "Chờ hồi về Entry")


class TestBtSettingsFile(Base):
    """Nút 💾 Lưu ra file / 📂 Mở file trong bảng Thông số Backtest: giữ lại và nạp lại bộ thông số, không đụng bot live."""

    def _app(self):
        app = make_app(lot="0.03")
        app.show_toast = mock.Mock()
        app.log = mock.Mock()
        app._update_bt_tf_display = mock.Mock()
        app._refresh_bt_settings_window = mock.Mock()
        return app

    def test_export_then_import_roundtrip_without_touching_live(self):
        app = self._app()
        app._get_bt_cfg(PK)
        app.bt_settings[PK]["max_hold_mins"] = "15"
        path = os.path.join(self.tmp, "bt_saved.json")
        with mock.patch.object(b.filedialog, "asksaveasfilename", return_value=path):
            app._bt_export_settings_file(PK)
        with open(path, encoding="utf-8") as f:
            prof = json.load(f)["profiles"][PK]
        self.assertEqual(prof["max_hold_mins"], "15")
        self.assertEqual(prof["lot"], "0.03")  # file giữ cả Lot của thanh Backtest
        for k in ("hf_token", "telegram_bot_token", "telegram_chat_id"):
            self.assertNotIn(k, prof)  # không bao giờ ghi token ra file
        app.bt_settings[PK]["max_hold_mins"] = "30"
        app.bt_lot_var.set("0.10")
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=path),                 mock.patch.object(b.messagebox, "askyesno", return_value=True):
            app._bt_import_settings_file(PK)
        self.assertEqual(app._get_bt_cfg(PK)["max_hold_mins"], "15")  # về đúng bộ đã lưu
        self.assertEqual(app.bt_lot_var.get(), "0.03")
        self.assertEqual(self.saved()[PK]["max_hold_mins"], "15")
        self.assertEqual(app.profile_settings[PK]["max_hold_mins"], "10")  # bot live không đổi
        app._refresh_bt_settings_window.assert_called_once()

    def test_import_live_export_drops_secrets_and_hidden_keys(self):
        app = self._app()
        path = os.path.join(self.tmp, "live_export.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_active_tab": PK, "profiles": {PK: {"consensus": "80", "hf_token": "X", "telegram_bot_token": "Y",
                                                                "trend_filter": True, "magic_number": "1"}}}, f)
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=path),                 mock.patch.object(b.messagebox, "askyesno", return_value=True):
            app._bt_import_settings_file(PK)
        st = app.bt_settings[PK]
        self.assertEqual(st["consensus"], "80")
        for k in ("hf_token", "telegram_bot_token", "trend_filter", "magic_number"):
            self.assertNotIn(k, st)
        self.assertFalse(app._get_bt_cfg(PK).get("trend_filter"))  # lọc cấu trúc đã ẩn: vẫn tắt

    def test_import_wrong_profile_or_cancel_changes_nothing(self):
        app = self._app()
        app._get_bt_cfg(PK)
        before = dict(app.bt_settings[PK])
        path = os.path.join(self.tmp, "btc_only.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"profiles": {"BTCUSD": {"consensus": "90"}}}, f)
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=path):
            app._bt_import_settings_file(PK)
        self.assertEqual(app.bt_settings[PK], before)
        app.show_toast.assert_called()
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=""):
            app._bt_import_settings_file(PK)  # bấm Hủy
        with mock.patch.object(b.filedialog, "askopenfilename", return_value=os.path.join(self.tmp, "bt_x.json")),                 mock.patch.object(b.messagebox, "askyesno", return_value=False):
            with open(os.path.join(self.tmp, "bt_x.json"), "w", encoding="utf-8") as f:
                json.dump({"profiles": {PK: {"consensus": "99"}}}, f)
            app._bt_import_settings_file(PK)  # không đồng ý nạp
        self.assertEqual(app.bt_settings[PK], before)


if __name__ == "__main__":
    unittest.main()
