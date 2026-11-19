"""Lịch Lãi/Lỗ và thanh Lịch Sử (chỉ giao diện). Mock, không MT5; phần giao diện dùng Tk thật (ẩn cửa sổ)."""
import os
import sys
import tkinter as tk
import unittest
from datetime import date, datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b
from trading_modules import pnl_calendar as pc

DEALS = [
    {"date": "2026-11-13", "net_pnl": 30.0}, {"date": "2026-11-13", "net_pnl": 20.0},
    {"date": "2026-11-14", "net_pnl": -20.0}, {"date": "2026-11-15", "net_pnl": 0.0},
    {"date": "xx", "net_pnl": 5.0}, {"date": "2026-11-16", "net_pnl": "abc"},
]


class TestPure(unittest.TestCase):
    def test_daily_pnl(self):
        d = pc.daily_pnl(DEALS)
        self.assertEqual(set(d), {"2026-11-13", "2026-11-14", "2026-11-15"})  # dòng lỗi bị bỏ
        self.assertEqual(d["2026-11-13"], {"pnl": 50.0, "n": 2, "win": 2, "loss": 0})
        self.assertEqual(d["2026-11-14"]["loss"], 1)
        self.assertEqual(pc.daily_pnl(None), {})

    def test_format_color_summary(self):
        self.assertEqual(pc.fmt_money(50), "+50.00$")
        self.assertEqual(pc.fmt_money(-20.5), "-20.50$")
        self.assertEqual(pc.fmt_money(1234.4), "+1,234$")
        self.assertEqual(pc.pnl_color(5), pc.GREEN)
        self.assertEqual(pc.pnl_color(-5), pc.RED)
        self.assertEqual(pc.pnl_color(0), pc.GOLD)
        s = pc.month_summary(pc.daily_pnl(DEALS), 2026, 11)
        self.assertEqual(s, {"pnl": 30.0, "win_days": 1, "loss_days": 1, "trades": 4})
        self.assertEqual(pc.month_bounds(2026, 2), (date(2026, 2, 1), date(2026, 2, 28)))


class TkBase(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"Không có Tk: {e}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)


class TestDialog(TkBase):
    def make(self, start="2026-11-13", end="2026-11-14"):
        self.calls = []
        self.applied = []

        def fetch(a, b_):
            self.calls.append((a, b_))
            return pc.daily_pnl(DEALS) if a.startswith("2026-11") else {}
        dlg = pc.PnlCalendarDialog(self.root, start, end, fetch, lambda s, e: self.applied.append((s, e)),
                                   today=date(2026, 11, 19))
        self.addCleanup(dlg.close)
        return dlg

    @staticmethod
    def texts(cell):
        out = []
        stack = [cell]
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Label):
                out.append((w.cget("text"), w.cget("fg")))
            stack.extend(w.winfo_children())
        return out

    def test_cells_show_pnl_colors(self):
        dlg = self.make()
        self.assertEqual(self.calls, [("2026-11-01", "2026-11-30")])
        t13 = self.texts(dlg.cells[date(2026, 11, 13)][0])
        self.assertIn("+50.00$", [t for t, _ in t13])
        t14 = dict(self.texts(dlg.cells[date(2026, 11, 14)][0]))
        self.assertIn("-20.00$", t14)
        # ngày 14 là đầu/cuối khoảng chọn (nền vàng) -> chữ đen; ngày 16 không có lệnh -> không ghi tiền
        self.assertFalse(any("$" in t for t, _ in self.texts(dlg.cells[date(2026, 11, 16)][0])))
        dlg.click_day(date(2026, 11, 1))
        dlg.click_day(date(2026, 11, 2))
        t14 = dict(self.texts(dlg.cells[date(2026, 11, 14)][0]))
        self.assertEqual(t14["-20.00$"], pc.RED)
        t13 = dict(self.texts(dlg.cells[date(2026, 11, 13)][0]))
        self.assertEqual(t13["+50.00$"], pc.GREEN)
        self.assertIn("+30.00$", dlg.lbl_month_sum.cget("text"))

    def test_pick_range_and_apply(self):
        dlg = self.make()
        dlg.click_day(date(2026, 11, 18))
        self.assertIn("bấm ngày kết thúc", dlg.lbl_range.cget("text"))
        dlg.click_day(date(2026, 11, 10))  # bấm ngày trước -> tự đảo
        self.assertEqual((dlg.start, dlg.end), (date(2026, 11, 10), date(2026, 11, 18)))
        self.assertIn("+30.00$", dlg.lbl_range.cget("text"))  # tổng lãi/lỗ khoảng chọn
        dlg.apply()
        self.assertEqual(self.applied, [("2026-11-10", "2026-11-18")])

    def test_month_nav_cache_and_quick(self):
        dlg = self.make()
        dlg._shift_month(-1)
        self.assertEqual(dlg.lbl_month.cget("text"), "Tháng 10 / 2026")
        self.assertIn("chưa có lệnh", dlg.lbl_month_sum.cget("text"))
        dlg._shift_month(1)
        self.assertEqual(len(self.calls), 2)  # tháng 11 lấy từ bộ đệm
        dlg._q_last(7)
        self.assertEqual((dlg.start, dlg.end), (date(2026, 11, 12), date(2026, 11, 19)))
        dlg._q_prev_month()
        self.assertEqual((dlg.start, dlg.end), (date(2026, 10, 1), date(2026, 10, 31)))
        dlg._shift_month(-12)  # năm trước
        self.assertEqual((dlg.year, dlg.month), (2025, 10))

    def test_fetch_error_does_not_crash(self):
        dlg = pc.PnlCalendarDialog(self.root, "2026-11-13", "bad", lambda a, b_: 1 / 0, None, today=date(2026, 11, 19))
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.end, date(2026, 11, 13))
        self.assertIn("chưa có lệnh", dlg.lbl_month_sum.cget("text"))


class TestHistoryBar(TkBase):
    def make_app(self, sym="Tất cả", manual=False):
        app = object.__new__(b.KronosDesktopApp)
        app.__dict__.update(
            root=self.root,
            hist_start_var=tk.StringVar(master=self.root), hist_end_var=tk.StringVar(master=self.root),
            hist_symbol_var=tk.StringVar(master=self.root, value=sym),
            hist_include_manual_var=tk.BooleanVar(master=self.root, value=manual),
            hist_range_btn=tk.Button(self.root),
            _hist_quick_btns={k: tk.Button(self.root) for k in ("today", "yesterday", "7d", "30d", "month")},
        )
        return app

    def test_filter_args(self):
        self.assertEqual(self.make_app()._hist_filter_args(), (None, None, "BOT_ALL"))
        self.assertEqual(self.make_app("XAUUSD")._hist_filter_args(), ("XAUUSDm", "XAUUSD", "PROFILE"))
        self.assertEqual(self.make_app("BTCUSD", True)._hist_filter_args(), ("BTCUSDm", None, "ALL"))

    def test_range_button_and_active_chip(self):
        app = self.make_app()
        today = datetime.now(tz=b.VN_TZ).strftime("%Y-%m-%d")
        app.hist_start_var.set(today)
        app.hist_end_var.set(today)
        app._update_hist_range_btn()
        self.assertIn(datetime.strptime(today, "%Y-%m-%d").strftime("%d/%m/%Y"), app.hist_range_btn.cget("text"))
        self.assertEqual(app._hist_quick_btns["today"].cget("bg"), b.GOLD_ACCENT)
        self.assertNotEqual(app._hist_quick_btns["7d"].cget("bg"), b.GOLD_ACCENT)
        app.hist_start_var.set("2026-01-01")
        app._update_hist_range_btn()
        self.assertIn("01/01/2026  →", app.hist_range_btn.cget("text"))
        self.assertFalse(any(x.cget("bg") == b.GOLD_ACCENT for x in app._hist_quick_btns.values()))

    def test_fetch_daily_uses_same_filter_and_no_shared_cache(self):
        app = self.make_app("XAUUSD")
        app._get_closed_deals_by_range = mock.Mock(return_value=DEALS)
        d = app._hist_fetch_daily_pnl("2026-11-01", "2026-11-30")
        self.assertEqual(d["2026-11-13"]["pnl"], 50.0)
        kw = app._get_closed_deals_by_range.call_args.kwargs
        self.assertEqual((kw["symbol"], kw["profile_key"], kw["magic_scope"]), ("XAUUSDm", "XAUUSD", "PROFILE"))
        self.assertFalse(kw["store_cache"])  # không ghi bộ đệm dùng chung của luật SL nhanh/chuỗi thua


if __name__ == "__main__":
    unittest.main()
