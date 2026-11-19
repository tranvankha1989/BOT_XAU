"""
trading_modules/replay_player.py
────────────────────────────────
Trình PHÁT LẠI backtest dạng nến (kiểu TradingView Replay) cho tab Backtest của app.

- Dữ liệu: nến 1 giây (player.npz do tools/live_replay.run_replay_job ghi), lệnh, lịch sử SL/TP từng lệnh,
  và (khi có) sự kiện trực tiếp events.jsonl: kết quả AI (dòng tín hiệu giống tab live + nến dự báo), vùng canh, nhật ký bot.
- Hai chế độ:
  + Cửa sổ riêng (Toplevel) hoặc NHÚNG vào khung có sẵn (parent=...) để thay biểu đồ live khi mở tab Backtest.
  + live=True: XEM TRỰC TIẾP trong lúc tiến trình backtest còn đang tính: con trỏ thời gian chạy theo tốc độ chọn
    nhưng không vượt mốc đã tính ("frontier"); tua nhanh hơn tốc độ AI thì tự chờ (giống video đang tải).
- Vẽ bằng Tk Canvas, bộ hình nến dùng lại, ~60 khung hình/giây, chỉ vẽ lại khi có thay đổi;
  nến đang chạy dựng dần từ dữ liệu 1 giây (không nhìn trước tương lai).
"""
import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import tkinter as tk
from tkinter import ttk

VN_TZ = timezone(timedelta(hours=7))
SPEEDS = [1, 2, 5, 10, 30, 60, 120, 300, 600]
SPEED_MAX = 10 ** 9           # "Tối đa": bám sát mốc đã tính
SPEED_MAX_LABEL = "Tối đa"
TF_CHOICES = {"M1": 60, "M5": 300, "M15": 900}
TF_SEC_ALL = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
LOG_KEEP = 400


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


def parse_speed(txt) -> int:
    s = str(txt).strip()
    if s == SPEED_MAX_LABEL:
        return SPEED_MAX
    try:
        return max(1, int(s.lower().replace("x", "").strip()))
    except ValueError:
        return 0


def active_index(times: np.ndarray, t: float) -> int:
    """Chỉ số sự kiện cuối cùng có thời điểm <= t (-1 nếu chưa có)."""
    return int(np.searchsorted(times, t, side="right")) - 1 if len(times) else -1


# ───────────────────────── Trình phát lại ─────────────────────────
class ReplayPlayer:
    BG = "#131722"
    PANEL = "#0e1118"
    GRID = "#232733"
    TEXT = "#b2b5be"
    UP = "#26a69a"
    DOWN = "#ef5350"
    PRICE = "#f0b90b"
    BUY = "#29b6f6"
    SELL = "#ffa726"
    PRED_UP = "#80cbc4"
    PRED_DOWN = "#ef9a9a"
    FAN = "#3b4556"
    MAX_BARS = 400

    def __init__(self, root, data: dict, trades: list, sltp: list, start_utc: float, end_utc: float,
                 contract: float = 100.0, title: str = "", tf: str = "M1", start_at: float = None,
                 parent=None, live: bool = False, speed: int = 60):
        self.root = root
        self.sec = np.asarray(data.get("sec", []), dtype=np.int64)
        self.o = np.asarray(data.get("o", []), dtype=np.float64)
        self.h = np.asarray(data.get("h", []), dtype=np.float64)
        self.l = np.asarray(data.get("l", []), dtype=np.float64)
        self.c = np.asarray(data.get("c", []), dtype=np.float64)
        self.start_utc, self.end_utc = float(start_utc), float(end_utc)
        self.contract = float(contract)
        self.embedded = parent is not None
        self.live = bool(live)
        self.finished = not self.live
        self.frontier = float(start_utc) if self.live else None  # mốc đã tính tới (None = đã có toàn bộ)
        self.waiting = False
        self._closed_recs = []
        self._open_recs = {}
        for t in trades or []:
            self._add_closed(dict(t))
        self._rebuild_trades()
        self.sltp = group_sltp(sltp)
        self.ai_events, self._ai_t = [], np.zeros(0)
        self.zones, self._zone_t = [], np.zeros(0)
        self.logs, self._log_t = [], np.zeros(0)
        self._log_shown = 0
        self._hdr_idx = None
        self.tf_name = tf if tf in TF_CHOICES else "M1"
        self.bars = aggregate_bars(self.sec, self.o, self.h, self.l, self.c, TF_CHOICES[self.tf_name])
        self.t = float(start_at) if start_at is not None else self.start_utc
        self.playing = False
        self.speed = int(speed)
        self.n_visible = 90
        self._yr = None
        self._dirty = True
        self._last = time.perf_counter()
        self._frame_ms = 16
        self._render_cost = 0.0
        self._after_id = None
        self._slider_lock = False
        self._closed = False

        if self.embedded:
            self.win = tk.Frame(parent, bg=self.BG)
            self.win.pack(fill=tk.BOTH, expand=True)
        else:
            self.win = tk.Toplevel(root)
            self.win.title(title or "Phát lại Backtest")
            self.win.configure(bg=self.BG)
            self.win.geometry("1180x660")
            self.win.protocol("WM_DELETE_WINDOW", self.close)

        # Dòng tín hiệu AI (giống đầu tab live) - hiện khi có kết quả AI
        self.hdr = tk.Frame(self.win, bg=self.PANEL, padx=10, pady=4)
        self.hdr_title = tk.Label(self.hdr, text="⏳ Chờ lần phân tích AI đầu tiên...", bg=self.PANEL, fg=self.PRICE,
                                  font=("Segoe UI", 11, "bold"), anchor="w")
        self.hdr_title.pack(fill=tk.X)
        self.hdr_detail = tk.Label(self.hdr, text="", bg=self.PANEL, fg="#e0e3eb", font=("Consolas", 8), justify=tk.LEFT, anchor="w")
        self.hdr_detail.pack(fill=tk.X, pady=(2, 0))
        if self.live:
            self.hdr.pack(fill=tk.X)

        top = tk.Frame(self.win, bg=self.PANEL)
        top.pack(fill=tk.X)
        self.info_lbl = tk.Label(top, text="", bg=self.PANEL, fg="#e0e3eb", font=("Consolas", 9), anchor="w", padx=8, pady=3)
        self.info_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Nhật ký bot chạy theo thời gian (khung nhúng)
        self.log_text = None
        if self.embedded:
            self.log_text = tk.Text(self.win, height=6, bg="#0b0e14", fg="#c9ccd4", font=("Consolas", 8), relief=tk.FLAT,
                                    wrap=tk.NONE, state=tk.DISABLED, padx=6)
            self.log_text.pack(side=tk.BOTTOM, fill=tk.X)

        ctl = tk.Frame(self.win, bg=self.PANEL, pady=4)
        ctl.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas = tk.Canvas(self.win, bg=self.BG, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        bstyle = dict(bg="#1e222d", fg="#e0e3eb", activebackground="#2a2e39", activeforeground="#ffffff",
                      relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=8, cursor="hand2")
        tk.Button(ctl, text="⏮", command=self.go_start, **bstyle).pack(side=tk.LEFT, padx=(8, 2))
        tk.Button(ctl, text="◀ 1 nến", command=self.step_back, **bstyle).pack(side=tk.LEFT, padx=2)
        self.play_btn = tk.Button(ctl, text="▶ Phát", command=self.toggle_play, width=9, **bstyle)
        self.play_btn.pack(side=tk.LEFT, padx=2)
        tk.Button(ctl, text="1 nến ▶", command=self.step_forward, **bstyle).pack(side=tk.LEFT, padx=2)
        tk.Button(ctl, text="Lệnh kế ⏭", command=self.next_trade, **bstyle).pack(side=tk.LEFT, padx=2)
        tk.Label(ctl, text="Tốc độ:", bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 2))
        self.speed_var = tk.StringVar(value=self._speed_label())
        sp = ttk.Combobox(ctl, textvariable=self.speed_var, values=[f"{s}x" for s in SPEEDS] + [SPEED_MAX_LABEL],
                          width=7, state="readonly")
        sp.pack(side=tk.LEFT)
        sp.bind("<<ComboboxSelected>>", lambda e: self.set_speed(self.speed_var.get()))
        tk.Label(ctl, text="Khung:", bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 2))
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

        key_target = self.canvas if self.embedded else self.win
        key_target.bind("<space>", lambda e: self.toggle_play())
        key_target.bind("<Right>", lambda e: self.step_forward())
        key_target.bind("<Left>", lambda e: self.step_back())
        if self.embedded:
            self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set())
        self.canvas.bind("<MouseWheel>", lambda e: self.zoom(0.85 if e.delta > 0 else 1.18))
        self.canvas.bind("<Configure>", lambda e: self._mark())
        self._after_id = self.win.after(30, self._tick)

    # ── dữ liệu lệnh / sự kiện ──
    def _add_closed(self, c: dict):
        c["_os"] = float(c["open_ms"]) / 1000.0
        c["_cs"] = float(c["close_ms"]) / 1000.0
        self._closed_recs.append(c)

    def _rebuild_trades(self):
        self.trades = sorted(self._closed_recs + list(self._open_recs.values()), key=lambda t: t["_os"])
        closed_sorted = sorted(self._closed_recs, key=lambda t: t["_cs"])
        self._closed_sorted = closed_sorted
        self._close_s = np.array([t["_cs"] for t in closed_sorted], dtype=np.float64)
        self._cum_pnl = np.cumsum([float(t["profit"]) for t in closed_sorted]) if closed_sorted else np.zeros(0)

    def add_events(self, evs: list):
        """Nhận sự kiện từ events.jsonl (open/sltp/close/ai/zone/log/t) - gọi được nhiều lần, theo thứ tự ghi."""
        ch_tr = ch_ai = ch_zone = ch_log = False
        for ev in evs or []:
            try:
                typ = ev.get("type")
                t = float(ev.get("t", 0.0) or 0.0)
                if self.live and not self.finished and self.frontier is not None and t > self.frontier:
                    self.frontier = t
                if typ == "open":
                    tk_ = int(ev["ticket"])
                    self._open_recs[tk_] = {"ticket": tk_, "type": ev.get("side", "BUY"), "open_ms": int(ev["open_ms"]),
                                            "price_open": float(ev["price"]), "volume": float(ev.get("vol", 0.0)),
                                            "close_ms": None, "profit": 0.0, "_os": float(ev["open_ms"]) / 1000.0, "_cs": math.inf}
                    ch_tr = True
                elif typ == "close":
                    c = {k: v for k, v in ev.items() if k not in ("type", "t", "side")}
                    c["type"] = str(ev.get("side") or "BUY")  # "type" của sự kiện là "close"; chiều lệnh nằm ở "side"
                    self._add_closed(c)
                    op = self._open_recs.get(int(c["ticket"]))
                    if op is not None:
                        rest = float(op["volume"]) - float(c.get("volume", op["volume"]))
                        if rest <= 1e-9:
                            del self._open_recs[int(c["ticket"])]
                        else:
                            op["volume"] = rest
                    ch_tr = True
                elif typ == "sltp":
                    self.sltp.setdefault(int(ev["ticket"]), []).append((int(round(t * 1000.0)), float(ev["sl"]), float(ev["tp"])))
                elif typ == "ai":
                    ev = dict(ev)
                    ev["_tf_sec"] = TF_SEC_ALL.get(str(ev.get("tf", "")).upper(), 300)
                    self.ai_events.append(ev)
                    ch_ai = True
                elif typ == "zone":
                    self.zones.append(ev)
                    ch_zone = True
                elif typ == "log":
                    self.logs.append((t, str(ev.get("msg", ""))))
                    ch_log = True
            except Exception:
                continue
        if ch_tr:
            self._rebuild_trades()
        if ch_ai:
            self.ai_events.sort(key=lambda e: float(e.get("t", 0.0)))
            self._ai_t = np.array([float(e.get("t", 0.0)) for e in self.ai_events])
            self._hdr_idx = None
        if ch_zone:
            self.zones.sort(key=lambda e: float(e.get("t", 0.0)))
            self._zone_t = np.array([float(e.get("t", 0.0)) for e in self.zones])
        if ch_log:
            self._log_t = np.array([x[0] for x in self.logs])
        if ch_ai and not self.hdr.winfo_ismapped():
            try:
                self.hdr.pack(fill=tk.X, before=self.info_lbl.master)
            except tk.TclError:
                pass
        self._mark()

    def set_frontier(self, t: float):
        if self.live and not self.finished and t is not None and (self.frontier is None or t > self.frontier):
            self.frontier = float(t)
            self._mark()

    def finish(self, end_utc: float = None):
        """Tiến trình đã tính xong (hoặc dừng): bỏ giới hạn mốc đã tính; end_utc mới khi bị dừng giữa chừng."""
        if end_utc is not None:
            self.end_utc = float(end_utc)
        self.finished = True
        self.frontier = None
        self.waiting = False
        self._mark()

    def n_closed_at_cursor(self) -> int:
        return int(np.searchsorted(self._close_s, self.t, side="right"))

    def closed_upto_cursor(self) -> list:
        return self._closed_sorted[:self.n_closed_at_cursor()]

    # ── điều khiển ──
    def _mark(self):
        self._dirty = True

    def _limit(self) -> float:
        return self.end_utc if self.frontier is None else min(self.end_utc, self.frontier)

    def _speed_label(self) -> str:
        return SPEED_MAX_LABEL if self.speed >= SPEED_MAX else f"{self.speed}x"

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

    def play(self):
        if not self.playing:
            self.toggle_play()

    def toggle_play(self):
        if self.finished and self.t >= self.end_utc - 0.5:
            self.t = self.start_utc
        self.playing = not self.playing
        self._last = time.perf_counter()
        self.play_btn.config(text="⏸ Dừng" if self.playing else "▶ Phát")
        self._mark()

    def pause(self):
        self.playing = False
        self.waiting = False
        self.play_btn.config(text="▶ Phát")
        self._mark()

    def set_speed(self, txt):
        v = parse_speed(txt)
        if v:
            self.speed = v
        try:
            self.speed_var.set(self._speed_label())
        except Exception:
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
        lo = float(self.sec[0]) if len(self.sec) else self.start_utc
        self.t = float(min(max(t, lo), self._limit()))
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
            if t["_os"] > self.t + 0.5 and t["_os"] <= self._limit():
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
            lim = self._limit()
            prev = self.t
            self.t = lim if self.speed >= SPEED_MAX else min(lim, self.t + dt * self.speed)
            self.waiting = (not self.finished) and self.t >= lim - 1e-6
            if self.finished and self.t >= self.end_utc:
                self.pause()
            if self.t != prev:
                self._dirty = True
        try:
            visible = bool(self.canvas.winfo_viewable())
        except tk.TclError:
            return
        if self._dirty and visible:
            t0 = time.perf_counter()
            try:
                self.render()
            except tk.TclError:
                return
            cost = time.perf_counter() - t0
            self._render_cost = 0.9 * self._render_cost + 0.1 * cost
            self._frame_ms = 33 if self._render_cost > 0.012 else 16
        try:
            self._after_id = self.win.after(self._frame_ms if visible else 120, self._tick)
        except tk.TclError:
            pass

    def _update_header(self, ai_idx: int):
        if ai_idx == self._hdr_idx:
            return
        self._hdr_idx = ai_idx
        if ai_idx < 0:
            self.hdr_title.config(text="⏳ Chờ lần phân tích AI đầu tiên...", fg=self.PRICE)
            self.hdr_detail.config(text="")
            return
        ev = self.ai_events[ai_idx]
        col = str(ev.get("color") or "") or self.PRICE
        try:
            self.hdr_title.config(text=f"[{vn_str(ev['t'], '%d/%m %H:%M')}] {ev.get('title', '')}", fg=col)
        except tk.TclError:
            self.hdr_title.config(text=str(ev.get("title", "")), fg=self.PRICE)
        self.hdr_detail.config(text=str(ev.get("detail", "")))

    def _update_logs(self):
        if self.log_text is None:
            return
        target = int(np.searchsorted(self._log_t, self.t, side="right")) if len(self._log_t) else 0
        if target == self._log_shown:
            return
        lt = self.log_text
        lt.config(state=tk.NORMAL)
        if target < self._log_shown or target - self._log_shown > LOG_KEEP:
            lt.delete("1.0", tk.END)
            lo = max(0, target - LOG_KEEP)
        else:
            lo = self._log_shown
        lines = "".join(f"[{vn_str(t, '%H:%M:%S')}] {m}\n" for t, m in self.logs[lo:target])
        lt.insert(tk.END, lines)
        n_lines = int(lt.index("end-1c").split(".")[0])
        if n_lines > LOG_KEEP + 50:
            lt.delete("1.0", f"{n_lines - LOG_KEEP}.0")
        lt.see(tk.END)
        lt.config(state=tk.DISABLED)
        self._log_shown = target

    def render(self):
        cv = self.canvas
        W, H = max(cv.winfo_width(), 200), max(cv.winfo_height(), 150)
        cv.delete("dyn")
        if not len(self.sec):
            cv.create_text(W / 2, H / 2, text="Không có dữ liệu giá để phát lại", fill=self.TEXT, tags="dyn")
            return
        idx = max(int(np.searchsorted(self.sec, self.t, side="right")) - 1, 0)
        b, fo, fh, fl, fc = forming_bar(self.bars, self.h, self.l, self.c, idx)
        tf = TF_CHOICES[self.tf_name]
        n = self.n_visible
        first = max(0, b - n + 1)
        ks = np.arange(first, b + 1)
        bo, bh, bl, bc = (self.bars[k][first:b + 1].copy() for k in ("o", "h", "l", "c"))
        bo[-1], bh[-1], bl[-1], bc[-1] = fo, fh, fl, fc
        m = len(ks)
        t_ms = self.t * 1000.0
        cur_start = float(self.bars["start"][b])

        ai_idx = active_index(self._ai_t, self.t)
        ai = self.ai_events[ai_idx] if ai_idx >= 0 else None
        z_idx = active_index(self._zone_t, self.t)
        zone = self.zones[z_idx] if z_idx >= 0 and not self.zones[z_idx].get("clear") else None
        pred = (ai.get("pred") or []) if ai else []
        ahead = 0
        if pred:
            horizon_end = float(pred[-1][0]) + float(ai["_tf_sec"])
            ahead = int(min(max(math.ceil((horizon_end - cur_start) / tf), 0), int(n * 0.5)))
        right_pad = max(4, ahead + 1)

        open_tr = [t for t in self.trades if t["_os"] <= self.t < t["_cs"]]
        lo, hi = float(np.min(bl)), float(np.max(bh))
        for t in open_tr:
            st = sltp_at(self.sltp, t["ticket"], t_ms)
            for v in ((st or (0.0, 0.0)) + (float(t["price_open"]),)):
                if v and v > 0:
                    lo, hi = min(lo, v), max(hi, v)
        for p in pred:
            lo, hi = min(lo, float(p[3])), max(hi, float(p[2]))
        if zone and zone.get("lo") is not None:
            lo, hi = min(lo, float(zone["lo"])), max(hi, float(zone["hi"]))
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

        slot = (x1 - x0) / float(n + right_pad)

        def X(rel):
            return x0 + (n - m + rel + 0.5) * slot

        def X_time(sec_v):
            """Thời điểm -> tọa độ x tính từ nến hiện tại (dùng cho nến dự báo ở tương lai)."""
            return X(m - 1) - slot / 2.0 + (float(sec_v) - cur_start) / tf * slot

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
        # vùng canh vào lệnh (nằm dưới nến)
        if zone and zone.get("lo") is not None:
            zx = max(x0, X_time(float(zone["t"])))
            zcol = self.BUY if zone.get("action") == "BUY" else self.SELL
            cv.create_rectangle(zx, Y(float(zone["hi"])), x1, Y(float(zone["lo"])), fill=zcol, outline="", stipple="gray12", tags="dyn")
            cv.create_text(zx + 4, Y(float(zone["hi"])) - 2, text=f"Vùng canh {zone.get('action', '')} "
                           f"[{float(zone['lo']):.2f} - {float(zone['hi']):.2f}]", anchor="sw", fill=zcol,
                           font=("Consolas", 8), tags="dyn")

        cv.tag_lower("dyn")  # lưới + vùng nằm dưới nến; lệnh/giá vẽ sau nên nằm trên

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

        # dự báo AI: chùm kịch bản + nến dự báo (viền đứt) + SL/TP đề xuất
        if ai:
            ptf = float(ai["_tf_sec"])
            fan = ai.get("fan") or []
            if pred and fan:
                xs = [X_time(float(p[0]) + ptf / 2.0) for p in pred]
                for path in fan:
                    if len(path) >= len(xs):
                        pts = []
                        for xv, yv in zip(xs, path):
                            pts.extend((xv, Y(float(yv))))
                        if len(pts) >= 4:
                            cv.create_line(*pts, fill=self.FAN, tags="dyn")
            for p in pred:
                ps, po, ph, pl, pc = (float(v) for v in p[:5])
                xa, xb = X_time(ps), X_time(ps + ptf)
                if xa > x1:
                    break
                xm, half = (xa + xb) / 2.0, max(1.0, (xb - xa) * 0.32)
                pcol = self.PRED_UP if pc >= po else self.PRED_DOWN
                cv.create_line(xm, Y(ph), xm, Y(pl), fill=pcol, dash=(2, 2), tags="dyn")
                cv.create_rectangle(xm - half, Y(max(po, pc)), xm + half, Y(min(po, pc)) + 1, outline=pcol, dash=(3, 2), tags="dyn")
            xa_ai = max(x0, X_time(float(ai["t"])))
            for key, lbl, col in (("sl", "SL AI", self.PRED_DOWN), ("tp", "TP AI", self.PRED_UP)):
                v = float(ai.get(key) or 0.0)
                if v > 0 and y_lo <= v <= y_hi:
                    cv.create_line(xa_ai, Y(v), x1, Y(v), fill=col, dash=(1, 4), tags="dyn")
                    cv.create_text(xa_ai + 2, Y(v) - 2, text=f"{lbl} {v:.2f}", anchor="sw", fill=col, font=("Consolas", 7), tags="dyn")

        # lệnh
        starts = self.bars["start"]

        def rel_of(sec_v):
            k = int(np.searchsorted(starts, sec_v, side="right")) - 1
            return k - first

        exit_lbl_y = {}  # nhiều lệnh đóng cùng nến (nhồi lệnh): xếp nhãn lãi/lỗ chồng lên nhau, không đè chữ
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
                ly = yc - 9
                key = int(round(xc))
                if key in exit_lbl_y and abs(exit_lbl_y[key] - ly) < 12:
                    ly = exit_lbl_y[key] - 12
                exit_lbl_y[key] = ly
                cv.create_text(xc + 6, ly, text=f"{float(t['profit']):+.2f}$", anchor="w", fill=pcol,
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

        # thông tin + thanh thời gian + dòng tín hiệu + nhật ký
        n_closed = self.n_closed_at_cursor()
        realized = float(self._cum_pnl[n_closed - 1]) if n_closed > 0 else 0.0
        flt = sum(floating_pnl(t, fc, self.contract) for t in open_tr)
        info = (f"⏱ {vn_str(self.t)}  |  Giá {fc:.2f}  |  Đang mở: {len(open_tr)} lệnh ({flt:+.2f}$)  |  "
                f"Đã đóng: {n_closed} lệnh, {realized:+.2f}$  |  {self._speed_label()}  |  {self.tf_name}")
        if self.live and not self.finished and self.frontier is not None:
            info += f"  |  🧠 đã tính tới {vn_str(self.frontier, '%d/%m %H:%M:%S')}"
            if self.waiting and self.playing:
                info += "  ⏳ chờ AI tính..."
        self.info_lbl.config(text=info)
        frac = (self.t - self.start_utc) / max(self.end_utc - self.start_utc, 1.0)
        self._slider_lock = True
        try:
            self.pos_var.set(max(0.0, min(1000.0, frac * 1000.0)))
        finally:
            self._slider_lock = False
        self._update_header(ai_idx)
        self._update_logs()
        self._dirty = easing  # chỉ vẽ lại khi thời gian/sự kiện đổi hoặc thang giá còn đang chuyển (đỡ CPU cho bot live)


def _nice_step(raw: float) -> float:
    if raw <= 0:
        return 1.0
    mag = 10 ** np.floor(np.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return float(m * mag)
    return float(10 * mag)
