"""Ổ khóa thông số + nút 'Lưu cấu hình mặc định' (chỉ giao diện / mặc định khôi phục)."""
import json
import os
import sys
import tempfile
import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b  # noqa: E402


class TestParamsLock(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"Không có Tk: {e}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(b, "SETTINGS_DIR", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)

        app = object.__new__(b.KronosDesktopApp)
        self.vars = {
            "rr": tk.StringVar(master=self.root, value="2"),
            "auto_be": tk.BooleanVar(master=self.root, value=True),
            "be_trigger_r": tk.StringVar(master=self.root, value="1.0"),
            "magic_number": tk.StringVar(master=self.root, value="777"),
        }
        rows = {}
        for k, v in self.vars.items():
            fr = tk.Frame(self.root)
            if isinstance(v, tk.BooleanVar):
                w = tk.Checkbutton(fr, variable=v)
            else:
                w = tk.Entry(fr, textvariable=v)
            tk.Label(fr, text=k).pack()
            rows[str(v)] = (fr, w)
        app.__dict__.update(
            root=self.root, _param_rows=rows, _param_syncs=[], _params_locked=True,
            _get_settings_vars_map=lambda: self.vars, active_profile_key="XAUUSD",
            log=mock.Mock(), show_toast=mock.Mock(), params_lock_btn=tk.Button(self.root),
            _validate_menu_settings=lambda: [], hf_token_var=tk.StringVar(master=self.root, value=""),
        )
        self.app = app
        self.rows = rows

    def test_lock_and_unlock(self):
        a = self.app
        a._bind_param_children(self.vars["auto_be"], [self.vars["be_trigger_r"]])
        a._apply_params_lock()
        for _row, w in self.rows.values():
            self.assertEqual(str(w.cget("state")), "disabled")
        self.assertEqual(a.params_lock_btn.cget("text"), "🔒 ĐANG KHÓA")
        # đặt giá trị bằng code khi đang khóa vẫn được
        self.vars["rr"].set("3")
        self.assertEqual(self.vars["rr"].get(), "3")
        a.toggle_params_lock()
        self.assertFalse(a._params_locked)
        for k in ("rr", "auto_be", "be_trigger_r"):
            self.assertEqual(str(self.rows[str(self.vars[k])][1].cget("state")), "normal", k)
        self.assertEqual(a.params_lock_btn.cget("text"), "🔓 ĐÃ MỞ KHÓA")
        # cha tắt -> con vẫn mờ sau khi mở khóa
        self.vars["auto_be"].set(False)
        self.assertEqual(str(self.rows[str(self.vars["be_trigger_r"])][1].cget("state")), "disabled")
        a.toggle_params_lock()
        self.assertTrue(a._params_locked)

    def test_reset_blocked_when_locked(self):
        a = self.app
        a.save_user_settings = mock.Mock()
        a.apply_live_settings_and_rescan = mock.Mock()
        self.vars["rr"].set("9")
        a.reset_section_settings(2)
        self.assertEqual(self.vars["rr"].get(), "9")
        a.save_user_settings.assert_not_called()

    def test_save_defaults_and_reset_uses_them(self):
        a = self.app
        a.save_user_settings = mock.Mock()
        a.apply_live_settings_and_rescan = mock.Mock()
        a.profile_settings = {"XAUUSD": {}}
        a._params_locked = True
        a.save_current_as_defaults()  # đang khóa -> không ghi file
        path = os.path.join(self.tmp.name, b.KronosDesktopApp.USER_DEFAULTS_FILE_NAME)
        self.assertFalse(os.path.exists(path))
        a._params_locked = False
        self.vars["rr"].set("2.5")
        with mock.patch.object(b.messagebox, "askyesno", return_value=True):
            a.save_current_as_defaults()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["XAUUSD"]["rr"], "2.5")
        self.assertNotIn("magic_number", data["XAUUSD"])  # khóa riêng máy không lưu
        d = a._get_effective_defaults("XAUUSD")
        self.assertEqual(d["rr"], "2.5")
        self.assertEqual(d["magic_number"], b.DEFAULT_PROFILE_SETTINGS["XAUUSD"]["magic_number"])
        # khôi phục Mục 2 -> về mặc định riêng (rr nằm ở Mục 2)
        self.vars["rr"].set("9")
        a.reset_section_settings(2)
        self.assertEqual(self.vars["rr"].get(), "2.5")

    def test_defaults_file_corrupt_falls_back(self):
        with open(os.path.join(self.tmp.name, b.KronosDesktopApp.USER_DEFAULTS_FILE_NAME), "w") as f:
            f.write("{hong")
        self.assertEqual(self.app._get_effective_defaults("XAUUSD"), dict(b.DEFAULT_PROFILE_SETTINGS["XAUUSD"]))

    def test_save_defaults_refused_when_declined(self):
        a = self.app
        a._params_locked = False
        with mock.patch.object(b.messagebox, "askyesno", return_value=False):
            a.save_current_as_defaults()
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, b.KronosDesktopApp.USER_DEFAULTS_FILE_NAME)))


if __name__ == "__main__":
    unittest.main()
