"""Cửa sổ không viền (thanh tiêu đề tự vẽ) phải có WS_MINIMIZEBOX | WS_SYSMENU để bấm icon Taskbar lần 2 thì thu nhỏ."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b


class TestTaskbarMinimize(unittest.TestCase):
    def test_them_co_thu_nho(self):
        app = object.__new__(b.KronosDesktopApp)
        app.root = types.SimpleNamespace(overrideredirect=lambda v: None, update_idletasks=lambda: None, winfo_id=lambda: 11)
        styles = {-16: 0x80000000, -20: 0x00000080}  # WS_POPUP; WS_EX_TOOLWINDOW
        u = mock.Mock()
        u.GetParent.return_value = 22
        u.GetWindowLongW.side_effect = lambda h, idx: styles[idx]
        with mock.patch.object(b.ctypes, "windll", types.SimpleNamespace(user32=u)):
            app._apply_dark_titlebar()
        u.SetWindowLongW.assert_any_call(22, -16, 0x80000000 | 0x00020000 | 0x00080000)
        u.SetWindowLongW.assert_any_call(22, -20, 0x00040000)  # bỏ TOOLWINDOW, thêm APPWINDOW (giữ icon Taskbar)
        u.SetWindowPos.assert_called_once()


if __name__ == "__main__":
    unittest.main()
