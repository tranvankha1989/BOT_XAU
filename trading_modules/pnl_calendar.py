"""
trading_modules/pnl_calendar.py
Lịch lãi/lỗ theo ngày cho tab Lịch Sử (CHỈ giao diện, chỉ đọc số liệu; không đổi luật giao dịch).
- daily_pnl(deals): gộp lãi/lỗ ròng theo ngày đóng lệnh (giờ VN, khóa "date" của _get_closed_deals_by_range).
- PnlCalendarDialog: lịch tháng, mỗi ô ghi lãi (xanh) / lỗ (đỏ) của ngày; bấm 2 ngày để chọn khoảng Từ -> Đến.
"""

import calendar
from datetime import date, datetime, timedelta

import tkinter as tk

BG = "#10141d"
PANEL = "#19202c"
CELL = "#1c2433"
CELL_EMPTY = "#141a25"
RANGE_BG = "#2a3550"
EDGE_BG = "#f0b90b"
TEXT = "#eaecef"
MUTED = "#8b98a9"
GREEN = "#0ecb81"
RED = "#f6465d"
GOLD = "#f0b90b"
EPS = 0.005  # |lãi ngày| nhỏ hơn mức này coi như hòa


def parse_iso(s, default=None):
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def daily_pnl(deals) -> dict:
    """{"YYYY-MM-DD": {"pnl": tổng lãi ròng, "n": số lệnh, "win": số lệnh lãi, "loss": số lệnh lỗ}} theo ngày đóng lệnh."""
    out = {}
    for d in deals or []:
        day = str(d.get("date") or "").strip()
        if parse_iso(day) is None:
            continue
        try:
            pnl = float(d.get("net_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        rec = out.setdefault(day, {"pnl": 0.0, "n": 0, "win": 0, "loss": 0})
        rec["pnl"] += pnl
        rec["n"] += 1
        if pnl > EPS:
            rec["win"] += 1
        elif pnl < -EPS:
            rec["loss"] += 1
    for rec in out.values():
        rec["pnl"] = round(rec["pnl"], 2)
    return out


def fmt_money(x: float) -> str:
    """+50.0$ / -20.5$ (gọn để vừa ô lịch)."""
    return f"{x:+,.2f}$" if abs(x) < 100 else f"{x:+,.0f}$"


def pnl_color(x: float) -> str:
    return GREEN if x > EPS else (RED if x < -EPS else GOLD)


def month_summary(daily: dict, year: int, month: int) -> dict:
    pref = f"{year:04d}-{month:02d}-"
    days = [v for k, v in daily.items() if k.startswith(pref)]
    return {"pnl": round(sum(v["pnl"] for v in days), 2),
            "win_days": sum(1 for v in days if v["pnl"] > EPS),
            "loss_days": sum(1 for v in days if v["pnl"] < -EPS),
            "trades": sum(v["n"] for v in days)}


def month_bounds(year: int, month: int):
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


class PnlCalendarDialog:
    """Lịch chọn khoảng ngày kèm lãi/lỗ từng ngày.
    fetch_daily(start_iso, end_iso) -> dict như daily_pnl (gọi khi đổi tháng, có bộ đệm theo tháng; lỗi thì để trống).
    on_apply(start_iso, end_iso): gọi khi bấm "Áp dụng"."""

    def __init__(self, parent, start_iso, end_iso, fetch_daily, on_apply, today=None):
        self.today = today or date.today()
        self.fetch_daily = fetch_daily
        self.on_apply = on_apply
        self.start = parse_iso(start_iso, self.today)
        self.end = parse_iso(end_iso, self.start)
        if self.end < self.start:
            self.start, self.end = self.end, self.start
        self._picking_end = False  # đã bấm ngày bắt đầu, đang chờ bấm ngày kết thúc
        self.year, self.month = self.end.year, self.end.month
        self._cache = {}  # (năm, tháng) -> dict ngày

        self.win = tk.Toplevel(parent)
        self.win.title("Lịch Lãi/Lỗ - Chọn khoảng ngày")
        self.win.configure(bg=PANEL)
        self.win.resizable(False, False)
        try:
            self.win.transient(parent)
            self.win.grab_set()
        except tk.TclError:
            pass

        hdr = tk.Frame(self.win, bg=BG, padx=10, pady=8)
        hdr.pack(fill=tk.X)
        self._nav_btn(hdr, "◀", lambda: self._shift_month(-1)).pack(side=tk.LEFT)
        self.lbl_month = tk.Label(hdr, text="", bg=BG, fg=GOLD, font=("Segoe UI", 11, "bold"))
        self.lbl_month.pack(side=tk.LEFT, expand=True)
        self._nav_btn(hdr, "▶", lambda: self._shift_month(1)).pack(side=tk.RIGHT)

        self.lbl_month_sum = tk.Label(self.win, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"), pady=4)
        self.lbl_month_sum.pack(fill=tk.X)

        grid_wrap = tk.Frame(self.win, bg=PANEL, padx=8, pady=2)
        grid_wrap.pack()
        for i, name in enumerate(["T2", "T3", "T4", "T5", "T6", "T7", "CN"]):
            tk.Label(grid_wrap, text=name, bg=PANEL, fg=(RED if i == 6 else (GOLD if i == 5 else MUTED)),
                     font=("Segoe UI", 8, "bold")).grid(row=0, column=i, pady=(0, 3))
        self.grid = tk.Frame(grid_wrap, bg=PANEL)
        self.grid.grid(row=1, column=0, columnspan=7)
        self.cells = {}  # ngày -> khung ô (để test/kiểm tra)

        quick = tk.Frame(self.win, bg=PANEL, padx=8, pady=6)
        quick.pack(fill=tk.X)
        for txt, fn in (("Hôm nay", self._q_today), ("7 ngày", lambda: self._q_last(7)),
                        ("30 ngày", lambda: self._q_last(30)), ("Tháng này", self._q_this_month),
                        ("Tháng trước", self._q_prev_month)):
            tk.Button(quick, text=txt, bg="#232d3f", fg=TEXT, activebackground=GOLD, activeforeground="#000000",
                      relief=tk.FLAT, font=("Segoe UI", 8), padx=8, pady=2, cursor="hand2",
                      command=fn).pack(side=tk.LEFT, padx=(0, 4))

        foot = tk.Frame(self.win, bg=BG, padx=10, pady=8)
        foot.pack(fill=tk.X)
        self.lbl_range = tk.Label(foot, text="", bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold"), anchor="w", justify=tk.LEFT)
        self.lbl_range.pack(side=tk.LEFT)
        tk.Button(foot, text="Đóng", bg="#232d3f", fg=TEXT, relief=tk.FLAT, font=("Segoe UI", 9),
                  padx=10, pady=3, cursor="hand2", command=self.close).pack(side=tk.RIGHT)
        tk.Button(foot, text="✓ Áp dụng", bg="#0ecb81", fg="#000000", relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
                  padx=12, pady=3, cursor="hand2", command=self.apply).pack(side=tk.RIGHT, padx=(0, 6))

        self.render()
        self._center(parent)

    # ── điều khiển ─────────────────────────────────────────────
    @staticmethod
    def _nav_btn(parent, txt, cmd):
        return tk.Button(parent, text=txt, bg="#232d3f", fg=TEXT, relief=tk.FLAT, font=("Segoe UI", 10, "bold"),
                         padx=8, pady=1, cursor="hand2", command=cmd)

    def _center(self, parent):
        try:
            self.win.update_idletasks()
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.win.winfo_width()) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.win.winfo_height()) // 2)
            self.win.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _shift_month(self, step: int):
        m = self.month + step
        self.year += (m - 1) // 12
        self.month = (m - 1) % 12 + 1
        self.render()

    def _set_range(self, a: date, b: date, jump=True):
        self.start, self.end = (a, b) if a <= b else (b, a)
        self._picking_end = False
        if jump:
            self.year, self.month = self.end.year, self.end.month
        self.render()

    def _q_today(self):
        self._set_range(self.today, self.today)

    def _q_last(self, n: int):
        self._set_range(self.today - timedelta(days=n), self.today)  # giống nút "7 ngày"/"30 ngày" của tab Lịch Sử

    def _q_this_month(self):
        self._set_range(self.today.replace(day=1), self.today)

    def _q_prev_month(self):
        last = self.today.replace(day=1) - timedelta(days=1)
        self._set_range(last.replace(day=1), last)

    def click_day(self, d: date):
        """Bấm lần 1 = ngày bắt đầu, bấm lần 2 = ngày kết thúc (bấm ngày trước đó thì tự đảo)."""
        if not self._picking_end:
            self.start = self.end = d
            self._picking_end = True
        else:
            self.start, self.end = (self.start, d) if d >= self.start else (d, self.start)
            self._picking_end = False
        self.render()

    def apply(self):
        if self.on_apply:
            self.on_apply(self.start.strftime("%Y-%m-%d"), self.end.strftime("%Y-%m-%d"))
        self.close()

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # ── dữ liệu + vẽ ───────────────────────────────────────────
    def month_data(self, year: int, month: int) -> dict:
        key = (year, month)
        if key not in self._cache:
            a, b = month_bounds(year, month)
            try:
                self._cache[key] = dict(self.fetch_daily(a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")) or {})
            except Exception:
                self._cache[key] = {}
        return self._cache[key]

    def render(self):
        data = self.month_data(self.year, self.month)
        self.lbl_month.config(text=f"Tháng {self.month:02d} / {self.year}")
        ms = month_summary(data, self.year, self.month)
        if ms["trades"]:
            self.lbl_month_sum.config(
                text=f"Cả tháng: {fmt_money(ms['pnl'])}   •   {ms['win_days']} ngày lãi   •   {ms['loss_days']} ngày lỗ   •   {ms['trades']} lệnh",
                fg=pnl_color(ms["pnl"]))
        else:
            self.lbl_month_sum.config(text="Tháng này chưa có lệnh đóng", fg=MUTED)
        for w in self.grid.winfo_children():
            w.destroy()
        self.cells = {}
        for r, week in enumerate(calendar.monthcalendar(self.year, self.month)):
            for c, day_num in enumerate(week):
                if day_num == 0:
                    tk.Frame(self.grid, bg=PANEL, width=78, height=48).grid(row=r, column=c, padx=2, pady=2)
                    continue
                self._draw_cell(r, c, date(self.year, self.month, day_num), data)
        txt = f"Từ {self.start:%d/%m/%Y}  →  Đến {self.end:%d/%m/%Y}"
        if self._picking_end:
            txt += "   (bấm ngày kết thúc)"
        else:
            sel = self.range_total()
            if sel is not None:
                txt += f"   |   {fmt_money(sel)}"
        self.lbl_range.config(text=txt)

    def range_total(self):
        """Tổng lãi/lỗ của các ngày ĐÃ NẠP nằm trong khoảng chọn (None nếu chưa có số liệu ngày nào)."""
        tot, seen = 0.0, False
        for data in self._cache.values():
            for k, v in data.items():
                d = parse_iso(k)
                if d is not None and self.start <= d <= self.end:
                    tot += v["pnl"]
                    seen = True
        return round(tot, 2) if seen else None

    def _draw_cell(self, r, c, d: date, data: dict):
        rec = data.get(d.strftime("%Y-%m-%d"))
        in_range = self.start <= d <= self.end
        is_edge = d in (self.start, self.end)
        bg = EDGE_BG if is_edge else (RANGE_BG if in_range else (CELL if rec else CELL_EMPTY))
        fg_day = "#000000" if is_edge else (GREEN if d == self.today else (TEXT if rec else MUTED))
        cell = tk.Frame(self.grid, bg=bg, width=78, height=48, cursor="hand2",
                        highlightthickness=1, highlightbackground=(GREEN if d == self.today else bg))
        cell.grid(row=r, column=c, padx=2, pady=2)
        cell.grid_propagate(False)
        cell.pack_propagate(False)
        top = tk.Frame(cell, bg=bg)
        top.pack(fill=tk.X, padx=4, pady=(2, 0))
        tk.Label(top, text=str(d.day), bg=bg, fg=fg_day, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        parts = [cell, top]
        if rec:
            col = "#000000" if is_edge else pnl_color(rec["pnl"])
            tk.Label(top, text="●", bg=bg, fg=col, font=("Segoe UI", 7)).pack(side=tk.RIGHT)
            tk.Label(cell, text=fmt_money(rec["pnl"]), bg=bg, fg=col, font=("Consolas", 8, "bold")).pack(fill=tk.X)
            tk.Label(cell, text=f"{rec['n']} lệnh", bg=bg, fg=("#000000" if is_edge else MUTED),
                     font=("Segoe UI", 7)).pack(fill=tk.X)
        for w in parts + list(cell.winfo_children()) + list(top.winfo_children()):
            w.bind("<Button-1>", lambda _e, dd=d: self.click_day(dd))
        self.cells[d] = (cell, rec)
