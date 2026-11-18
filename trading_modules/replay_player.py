"""
trading_modules/replay_player.py
────────────────────────────────
Trình PHÁT LẠI kết quả backtest dạng nến (kiểu TradingView Replay) cho tab Backtest của app.

- Dữ liệu: nến 1 giây (player.npz do tools/live_replay.run_replay_job ghi), danh sách lệnh đã đóng, lịch sử SL/TP từng lệnh.
- Phát lại từ kết quả ĐÃ TÍNH SẴN (không chạy AI lúc phát) nên mượt: vẽ bằng Tk Canvas với bộ hình nến dùng lại,
  khoảng 60 khung hình/giây, nến đang chạy dựng dần từ dữ liệu 1 giây (không nhìn trước tương lai).
- Điều khiển: Phát/Tạm dừng, lùi/tới 1 nến, tới lệnh kế tiếp, tốc độ 1x..600x, khung M1/M5/M15, phóng to/thu nhỏ, kéo thanh thời gian.
"""
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import tkinter as tk
from tkinter import ttk

VN_TZ = timezone(timedelta(hours=7))
SPEEDS = [1, 2, 5, 10, 30, 60, 120, 300, 600]
TF_CHOICES = {"M1": 60, "M5": 300, "M15": 900}


# ───────────────────────── Hàm tính (không giao diện, có test) ─────────────────────────
def aggregate_bars(sec, o, h, l, c, tf: int) -> dict:
    """Gộp nến 1 giây thành nến khung tf giây. Trả: start (giây mở nến), first/last (chỉ số nến 1s), o/h/l/c, bar_of (nến của từng giây)."""
    sec = np.asarray(sec, dtype=np.int64)
    if len(sec) == 0:
        e = np.zeros(0)
        return {"start": e.astype(np.int64), "first": e.astype(np.int64), "last": e.astype(np.int64),
                "o": e, "h": e, "l": e, "c": e, "bar_of": e.astype(np.int64)}
    o, h, l, c = (np.asarray(x, dtype=np.float64) for x in (o, h, l, c))
    bid = sec // int(tf)
    starts = np.r_[0, np.nonzero(np.diff(bid))[0] + 1].astype(np.int64)
    ends = np.r_[starts[1:], len(sec)].astype(np.int64)
    return {"start": bid[starts] * int(tf), "first": starts, "last": ends - 1,
            "o": o[starts], "h": np.maximum.reduceat(h, starts), "l": np.minimum.reduceat(l, starts), "c": c[ends - 1],
            "bar_of": np.repeat(np.arange(len(starts), dtype=np.int64), ends - starts)}


def forming_bar(bars: dict, h, l, c, idx: int) -> tuple:
    """Nến đang chạy tại giây thứ idx: (chỉ số nến, o, h, l, c) chỉ dùng dữ liệu tới idx (không nhìn trước)."""
    b = int(bars["bar_of"][idx])
    f = int(bars["first"][b])
    return b, float(bars["o"][b]), float(np.max(h[f:idx + 1])), float(np.min(l[f:idx + 1])), float(c[idx])


def group_sltp(sltp: list) -> dict:
    """{ticket: [(ms, sl, tp), ...] sắp theo thời gian} từ danh sách (ms, ticket, sl, tp)."""
    out = {}
    for rec in sltp or []:
        ms, ticket, sl, tp = rec
        out.setdefault(int(ticket), []).append((int(ms), float(sl), float(tp)))
    for v in out.values():
        v.sort(key=lambda x: x[0])
    return out


def sltp_at(by_ticket: dict, ticket, t_ms: float):
    """SL/TP của lệnh tại thời điểm t_ms (bản ghi cuối cùng <= t_ms); None nếu chưa có."""
    cur = None
    for ms, sl, tp in by_ticket.get(int(ticket), []):
        if ms <= t_ms:
            cur = (sl, tp)
        else:
            break
    return cur


def floating_pnl(trade: dict, price: float, contract: float) -> float:
    d = 1.0 if str(trade.get("type")) == "BUY" else -1.0
    return (float(price) - float(trade["price_open"])) * d * float(trade.get("volume", 0.0)) * float(contract)


def vn_str(sec: float, fmt: str = "%d/%m %H:%M:%S") -> str:
    return datetime.fromtimestamp(float(sec), VN_TZ).strftime(fmt)


# ───────────────────────── Cửa sổ phát lại ─────────────────────────
class ReplayPlayer:
    BG = "#131722"
    GRID = "#232733"
    TEXT = "#b2b5be"
    UP = "#26a69a"
    DOWN = "#ef5350"
    PRICE = "#f0b90b"
    BUY = "#29b6f6"
    SELL = "#ffa726"
    MAX_BARS = 400

    def __init__(self, root, data: dict, trades: list, sltp: list, start_utc: float, end_utc: float,
                 contract: float = 100.0, title: str = "", tf: str = "M1", start_at: float = None):
        self.root = root
        self.sec = np.asarray(data.get("sec", []), dtype=np.int64)
        self.o = np.asarray(data.get("o", []), dtype=np.float64)
        self.h = np.asarray(data.get("h", []), dtype=np.float64)
        self.l = np.asarray(data.get("l", []), dtype=np.float64)
        self.c = np.asarray(data.get("c", []), dtype=np.float64)
        self.start_utc, self.end_utc = float(start_utc), float(end_utc)
        self.contract = float(contract)
        self.trades = sorted([dict(t) for t in (trades or [])], key=lambda t: t["open_ms"])
        for t in self.trades:
            t["_os"] = float(t["open_ms"]) / 1000.0
            t["_cs"] = float(t["close_ms"]) / 1000.0
        self.sltp = group_sltp(sltp)
        closed_sorted = sorted(self.trades, key=lambda t: t["_cs"])
        self._close_s = np.array([t["_cs"] for t in closed_sorted], dtype=np.float64)
        self._cum_pnl = np.cumsum([float(t["profit"]) for t in closed_sorted]) if closed_sorted else np.zeros(0)
        self.tf_name = tf if tf in TF_CHOICES else "M1"
        self.bars = aggregate_bars(self.sec, self.o, self.h, self.l, self.c, TF_CHOICES[self.tf_name])
        self.t = float(start_at) if start_at is not None else self.start_utc
        self.playing = False
        self.speed = 60
        self.n_visible = 90
        self._yr = None
        self._dirty = True
        self._last = time.perf_counter()
        self._frame_ms = 16
        self._render_cost = 0.0
        self._after_id = None
        self._slider_lock = False
        self._closed = False

        self.win = tk.Toplevel(root)
        self.win.title(title or "Phát lại Backtest")
        self.win.configure(bg=self.BG)
        self.win.geometry("1180x660")
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        top = tk.Frame(self.win, bg="#0e1118")
        top.pack(fill=tk.X)
        self.info_lbl = tk.Label(top, text="", bg="#0e1118", fg="#e0e3eb", font=("Consolas", 9), anchor="w", padx=8, pady=4)
        self.info_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.canvas = tk.Canvas(self.win, bg=self.BG, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        ctl = tk.Frame(self.win, bg="#0e1118", pady=4)
        ctl.pack(fill=tk.X)
        bstyle = dict(bg="#1e222d", fg="#e0e3eb", activebackground="#2a2e39", activeforeground="#ffffff",
                      relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=8, cursor="hand2")
        tk.Button(ctl, text="⏮", command=self.go_start, **bstyle).pack(side=tk.LEFT, padx=(8, 2))
        tk.Button(ctl, text="◀ 1 nến", command=self.step_back, **bstyle).pack(side=tk.LEFT, padx=2)
        self.play_btn = tk.Button(ctl, text="▶ Phát", command=self.toggle_play, width=9, **bstyle)
        self.play_btn.pack(side=tk.LEFT, padx=2)
        tk.Button(ctl, text="1 nến ▶", command=self.step_forward, **bstyle).pack(side=tk.LEFT, padx=2)
        tk.Button(ctl, text="Lệnh kế ⏭", command=self.next_trade, **bstyle).pack(side=tk.LEFT, padx=2)
        tk.Label(ctl, text="Tốc độ:", bg="#0e1118", fg=self.TEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 2))
        self.speed_var = tk.StringVar(value=f"{self.speed}x")
        sp = ttk.Combobox(ctl, textvariable=self.speed_var, values=[f"{s}x" for s in SPEEDS], width=5, state="readonly")
        sp.pack(side=tk.LEFT)
        sp.bind("<<ComboboxSelected>>", lambda e: self.set_speed(self.speed_var.get()))
        tk.Label(ctl, text="Khung:", bg="#0e1118", fg=self.TEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 2))
        self.tf_var = tk.StringVar(value=self.tf_name)
        tfc = ttk.Combobox(ctl, textvariable=self.tf_var, values=list(TF_CHOICES), width=4, state="readonly")
        tfc.pack(side=tk.LEFT)
        tfc.bind("<<ComboboxSelected>>", lambda e: self.set_tf(self.tf_var.get()))
        tk.Button(ctl, text="－", command=lambda: self.zoom(1.25), **bstyle).pack(side=tk.LEFT, padx=(10, 1))
        tk.Button(ctl, text="＋", command=lambda: self.zoom(0.8), **bstyle).pack(side=tk.LEFT, padx=1)
        self.pos_var = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(ctl, from_=0.0, to=1000.0, orient=tk.HORIZONTAL, variable=self.pos_var, command=self._on_slider)
        self.slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        # Bộ hình nến dùng lại (không tạo/xóa mỗi khung hình -> mượt)
        self._wicks = [self.canvas.create_line(0, 0, 0, 0, fill=self.UP, state="hidden") for _ in range(self.MAX_BARS + 1)]
        self._bodies = [self.canvas.create_rectangle(0, 0, 0, 0, fill=self.UP, outline=self.UP, state="hidden")
                        for _ in range(self.MAX_BARS + 1)]

        self.win.bind("<space>", lambda e: self.toggle_play())
        self.win.bind("<Right>", lambda e: self.step_forward())
        self.win.bind("<Left>", lambda e: self.step_back())
        self.canvas.bind("<MouseWheel>", lambda e: self.zoom(0.85 if e.delta > 0 else 1.18))
        self.canvas.bind("<Configure>", lambda e: self._mark())
        self._after_id = self.win.after(30, self._tick)

    # ── điều khiển ──
    def _mark(self):
        self._dirty = True

    def close(self):
        self._closed = True
        try:
            if self._after_id is not None:
                self.win.after_cancel(self._after_id)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def toggle_play(self):
        if self.t >= self.end_utc - 0.5:
            self.t = self.start_utc
        self.playing = not self.playing
        self._last = time.perf_counter()
        self.play_btn.config(text="⏸ Dừng" if self.playing else "▶ Phát")
        self._mark()

    def pause(self):
        self.playing = False
        self.play_btn.config(text="▶ Phát")
        self._mark()

    def set_speed(self, txt):
        try:
            self.speed = max(1, int(str(txt).lower().replace("x", "").strip()))
        except ValueError:
            pass
        self._mark()

    def set_tf(self, name):
        if name in TF_CHOICES and name != self.tf_name:
            self.tf_name = name
            self.bars = aggregate_bars(self.sec, self.o, self.h, self.l, self.c, TF_CHOICES[name])
            self._yr = None
            self._mark()

    def zoom(self, factor):
        self.n_visible = int(max(20, min(self.MAX_BARS, round(self.n_visible * factor))))
        self._yr = None
        self._mark()

    def seek(self, t: float, pause: bool = True):
        self.t = float(min(max(t, float(self.sec[0]) if len(self.sec) else self.start_utc), self.end_utc))
        if pause:
            self.pause()
        self._yr = None
        self._mark()

    def go_start(self):
        self.seek(self.start_utc)

    def _cur_bar(self):
        idx = int(np.searchsorted(self.sec, self.t, side="right")) - 1
        return int(self.bars["bar_of"][max(idx, 0)]) if len(self.sec) else 0

    def step_forward(self):
        if not len(self.sec):
            return
        b = self._cur_bar()
        tf = TF_CHOICES[self.tf_name]
        nb = min(b + 1, len(self.bars["start"]) - 1)
        self.seek(float(self.bars["start"][nb]) + tf - 0.001)

    def step_back(self):
        if not len(self.sec):
            return
        b = self._cur_bar()
        self.seek(float(self.bars["start"][max(b, 0)]) - 0.001)

    def next_trade(self):
        for t in self.trades:
            if t["_os"] > self.t + 0.5:
                self.seek(t["_os"] + 0.5)
                return

    def jump_to_trade(self, ticket):
        for t in self.trades:
            if int(t["ticket"]) == int(ticket):
                self.seek(t["_os"] + 0.5)
                return

    def _on_slider(self, _v):
        if self._slider_lock:
            return
        frac = float(self.pos_var.get()) / 1000.0
        self.seek(self.start_utc + frac * (self.end_utc - self.start_utc), pause=False)

    # ── vòng khung hình ──
    def _tick(self):
        if self._closed:
            return
        now = time.perf_counter()
        dt = min(now - self._last, 0.25)  # máy bận giật 1 nhịp: không nhảy cóc quá xa
        self._last = now
        if self.playing:
            self.t = min(self.end_utc, self.t + dt * self.speed)
            if self.t >= self.end_utc:
                self.pause()
            self._dirty = True
        if self._dirty:
            t0 = time.perf_counter()
            try:
                self.render()
            except tk.TclError:
                return
            cost = time.perf_counter() - t0
            self._render_cost = 0.9 * self._render_cost + 0.1 * cost
            self._frame_ms = 33 if self._render_cost > 0.012 else 16
        try:
            self._after_id = self.win.after(self._frame_ms, self._tick)
        except tk.TclError:
            pass

    def render(self):
        cv = self.canvas
        W, H = max(cv.winfo_width(), 200), max(cv.winfo_height(), 150)
        cv.delete("dyn")
        if not len(self.sec):
            cv.create_text(W / 2, H / 2, text="Không có dữ liệu giá để phát lại", fill=self.TEXT, tags="dyn")
            return
        idx = max(int(np.searchsorted(self.sec, self.t, side="right")) - 1, 0)
        b, fo, fh, fl, fc = forming_bar(self.bars, self.h, self.l, self.c, idx)
        n = self.n_visible
        first = max(0, b - n + 1)
        ks = np.arange(first, b + 1)
        bo, bh, bl, bc = (self.bars[k][first:b + 1].copy() for k in ("o", "h", "l", "c"))
        bo[-1], bh[-1], bl[-1], bc[-1] = fo, fh, fl, fc
        m = len(ks)
        t_ms = self.t * 1000.0

        open_tr = [t for t in self.trades if t["_os"] <= self.t < t["_cs"]]
        lo, hi = float(np.min(bl)), float(np.max(bh))
        for t in open_tr:
            st = sltp_at(self.sltp, t["ticket"], t_ms)
            for v in ((st or (0.0, 0.0)) + (float(t["price_open"]),)):
                if v and v > 0:
                    lo, hi = min(lo, v), max(hi, v)
        pad = max((hi - lo) * 0.08, 0.5)
        target = (lo - pad, hi + pad)
        if self._yr is None:
            self._yr = target
        else:
            a = 0.25
            self._yr = (self._yr[0] + (target[0] - self._yr[0]) * a, self._yr[1] + (target[1] - self._yr[1]) * a)
            if abs(self._yr[0] - target[0]) <= 1e-3 and abs(self._yr[1] - target[1]) <= 1e-3:
                self._yr = target
        easing = self._yr != target  # còn đang chuyển thang giá mượt -> vẽ tiếp khung sau
        y_lo, y_hi = self._yr
        x0, x1, y0, y1 = 6, W - 78, 8, H - 24
        span = max(y_hi - y_lo, 1e-6)

        def Y(p):
            return y1 - (p - y_lo) / span * (y1 - y0)

        slot = (x1 - x0) / float(n + 4)

        def X(rel):
            return x0 + (n - m + rel + 0.5) * slot

        # lưới + nhãn giá
        step = _nice_step(span / 6.0)
        g = np.ceil(y_lo / step) * step
        while g < y_hi:
            yy = Y(g)
            cv.create_line(x0, yy, x1, yy, fill=self.GRID, tags="dyn")
            cv.create_text(x1 + 6, yy, text=f"{g:.2f}", anchor="w", fill=self.TEXT, font=("Consolas", 8), tags="dyn")
            g += step
        # nhãn giờ
        every = max(1, m // 6)
        for j in range(0, m, every):
            if X(j) < x0 + 18:
                continue  # nhãn sát mép trái bị cắt chữ
            cv.create_text(X(j), y1 + 12, text=vn_str(self.bars["start"][ks[j]], "%H:%M"), fill=self.TEXT,
                           font=("Consolas", 8), tags="dyn")
            cv.create_line(X(j), y0, X(j), y1, fill=self.GRID, tags="dyn")

        cv.tag_lower("dyn")  # lưới nằm dưới nến; lệnh/giá vẽ sau nên nằm trên

        # nến (dùng lại hình, chỉ ẩn phần thừa so với khung trước)
        bw = max(1.0, slot * 0.36)
        for j in range(m):
            col = self.UP if bc[j] >= bo[j] else self.DOWN
            xc = X(j)
            cv.coords(self._wicks[j], xc, Y(bh[j]), xc, Y(bl[j]))
            top, bot = Y(max(bo[j], bc[j])), Y(min(bo[j], bc[j]))
            if bot - top < 1:
                bot = top + 1
            cv.coords(self._bodies[j], xc - bw, top, xc + bw, bot)
            cv.itemconfigure(self._wicks[j], fill=col, state="normal")
            cv.itemconfigure(self._bodies[j], fill=col, outline=col, state="normal")
        for j in range(m, getattr(self, "_shown", self.MAX_BARS + 1)):
            cv.itemconfigure(self._wicks[j], state="hidden")
            cv.itemconfigure(self._bodies[j], state="hidden")
        self._shown = m

        # lệnh
        starts = self.bars["start"]

        def rel_of(sec_v):
            k = int(np.searchsorted(starts, sec_v, side="right")) - 1
            return k - first

        for t in self.trades:
            if t["_os"] > self.t:
                break
            closed = t["_cs"] <= self.t
            ro = rel_of(t["_os"])
            rc = rel_of(t["_cs"]) if closed else None
            if closed and rc < 0:
                continue
            col = self.BUY if t["type"] == "BUY" else self.SELL
            yo = Y(float(t["price_open"]))
            if ro >= 0:
                xo = X(ro)
                d = 7 if t["type"] == "BUY" else -7
                cv.create_polygon(xo, yo, xo - 6, yo + d, xo + 6, yo + d, fill=col, outline="", tags="dyn")
            else:
                xo = x0
            if closed:
                xc, yc = X(rc), Y(float(t["price_close"]))
                pcol = self.UP if float(t["profit"]) >= 0 else self.DOWN
                cv.create_line(xo, yo, xc, yc, fill=pcol, dash=(3, 2), tags="dyn")
                cv.create_line(xc - 4, yc - 4, xc + 4, yc + 4, fill=pcol, width=2, tags="dyn")
                cv.create_line(xc - 4, yc + 4, xc + 4, yc - 4, fill=pcol, width=2, tags="dyn")
                cv.create_text(xc + 6, yc - 9, text=f"{float(t['profit']):+.2f}$", anchor="w", fill=pcol,
                               font=("Consolas", 8, "bold"), tags="dyn")
            else:
                st = sltp_at(self.sltp, t["ticket"], t_ms)
                cv.create_line(xo, yo, x1, yo, fill="#e0e3eb", dash=(1, 3), tags="dyn")
                if st:
                    sl, tp = st
                    if sl > 0:
                        cv.create_line(xo, Y(sl), x1, Y(sl), fill=self.DOWN, dash=(5, 3), tags="dyn")
                        cv.create_text(x1 - 2, Y(sl) - 2, text=f"SL {sl:.2f}", anchor="se", fill=self.DOWN, font=("Consolas", 8), tags="dyn")
                    if tp > 0:
                        cv.create_line(xo, Y(tp), x1, Y(tp), fill=self.UP, dash=(5, 3), tags="dyn")
                        cv.create_text(x1 - 2, Y(tp) - 2, text=f"TP {tp:.2f}", anchor="se", fill=self.UP, font=("Consolas", 8), tags="dyn")
                fp = floating_pnl(t, fc, self.contract)
                lx = max(xo, x0) + 10
                cv.create_text(lx, yo - 9, text=f"{t['type']} {float(t['volume']):.2f} @ {float(t['price_open']):.2f} | {fp:+.2f}$",
                               anchor="w", fill=self.UP if fp >= 0 else self.DOWN, font=("Consolas", 8, "bold"), tags="dyn")

        # giá hiện tại
        yp = Y(fc)
        cv.create_line(x0, yp, x1, yp, fill=self.PRICE, dash=(2, 2), tags="dyn")
        cv.create_rectangle(x1 + 1, yp - 8, W - 2, yp + 8, fill=self.PRICE, outline="", tags="dyn")
        cv.create_text(x1 + 6, yp, text=f"{fc:.2f}", anchor="w", fill="#000000", font=("Consolas", 8, "bold"), tags="dyn")

        # thông tin + thanh thời gian
        n_closed = int(np.searchsorted(self._close_s, self.t, side="right"))
        realized = float(self._cum_pnl[n_closed - 1]) if n_closed > 0 else 0.0
        flt = sum(floating_pnl(t, fc, self.contract) for t in open_tr)
        self.info_lbl.config(text=(f"⏱ {vn_str(self.t)}  |  Giá {fc:.2f}  |  Đang mở: {len(open_tr)} lệnh ({flt:+.2f}$)  |  "
                                   f"Đã đóng: {n_closed} lệnh, {realized:+.2f}$  |  {self.speed}x  |  {self.tf_name}"))
        frac = (self.t - self.start_utc) / max(self.end_utc - self.start_utc, 1.0)
        self._slider_lock = True
        try:
            self.pos_var.set(max(0.0, min(1000.0, frac * 1000.0)))
        finally:
            self._slider_lock = False
        self._dirty = self.playing or easing  # tạm dừng + thang giá đã ổn: không vẽ lại (đỡ CPU cho bot live)


def _nice_step(raw: float) -> float:
    if raw <= 0:
        return 1.0
    mag = 10 ** np.floor(np.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return float(m * mag)
    return float(10 * mag)
