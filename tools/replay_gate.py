"""
tools/replay_gate.py
────────────────────
Phát lại tick thật vào ĐÚNG hàm KronosDesktopApp._check_pending_pullback_order của bot (không sửa bot, không gọi MT5 thật,
không chạy bot): chỉ đọc CSV tick/nến và log hoạt động.

Cách làm
- Pending (vùng canh) dựng từ các dòng log "Canh Vùng Giá" (hướng, SL, TP, "Max" hiệu dụng, vùng Z, giờ VN = UTC+7).
- App dựng bằng object.__new__ + stub giao diện/log/khóa; mt5 thay bằng FakeMT5 đọc dữ liệu phát lại
  (tick hiện tại, nến đóng + nến đang hình thành dựng từ tick <= hiện tại, positions giả); execute_mt5_order được thay bằng
  hàm ghi nhận "vào lệnh giả"; time.time/time.monotonic thay bằng đồng hồ phát lại.
- Vòng phát lại mô phỏng vòng tick thật: mỗi --poll-ms (mặc định 1000) theo giờ dữ liệu, lấy tick cuối tại/trước thời điểm đó.

Ví dụ:
    python tools/replay_gate.py                      # kiểm chứng (kỳ vọng 0 vào lệnh giả)
    python tools/replay_gate.py --poll-ms 100
    python tools/replay_gate.py --no-halve-max-sl --tp-rr 2.0 --no-pa
    python tools/replay_gate.py --grid               # thử mọi tổ hợp 3 cờ nhị phân (CHỈ SAU KHI kiểm chứng đạt)

CẢNH BÁO: mẫu chỉ 17 tín hiệu trong MỘT đêm -> KHÔNG dùng để chọn luật giao dịch.
"""

import argparse
import glob
import os
import re
import sys
import types
import threading
import itertools
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np
import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import mt5_scalping_bot as bot_mod  # noqa: E402

PK = "XAUUSD"
SYM = "XAUUSDm"
MAGIC = 777
LOT = 0.01
OZ = LOT * 100.0           # 0.01 lot = 1 oz: 1 đô la giá = 1 đô la lãi/lỗ
VN_TZ = timezone(timedelta(hours=7))
M5_SCAN_GRACE_SEC = 90.0   # pending hết hạn nếu sau mốc M5 kế tiếp +90s vẫn không có sự kiện quét nào trong log

LOG_TS = r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\]"
SIGNAL_RE = re.compile(
    LOG_TS + r" 🎯 \[(\w+) - Canh Vùng Giá AI (\w+)( \[[^\]]*\])?\] AI báo (BUY|SELL) "
    r"\(SL: ([\d.]+), TP: ([\d.]+), cách SL ([\d.]+)\$ > Max ([\d.]+)\$\)\. .*?về vùng (<=|>=) ([\d.]+)"
)
LINE_TS_RE = re.compile(r"^" + LOG_TS)
CLEAR_MARKS = (f"[{PK} - {SYM}] Phân tích xong nến", f"[{PK} - Chặn Bắt", "KHỞI ĐỘNG HỆ THỐNG", "Đã đóng ứng dụng")


# ───────────────────────── Log → sự kiện ─────────────────────────
def log_ts_to_epoch(s: str) -> float:
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in s else "%Y-%m-%d %H:%M:%S"
    return datetime.strptime(s, fmt).replace(tzinfo=VN_TZ).timestamp()


def parse_log_events(lines, pk: str = PK) -> list:
    """Trả danh sách sự kiện đã sắp theo giờ: {"t": epoch UTC, "kind": "signal"|"clear", ...}.
    signal: dòng 🎯 "Canh Vùng Giá" của profile pk. clear: kết quả quét không tín hiệu / khởi động lại / đóng app."""
    events = []
    for ln in lines:
        m = SIGNAL_RE.search(ln)
        if m:
            if m.group(2) != pk:
                continue
            events.append({
                "t": log_ts_to_epoch(m.group(1)), "kind": "signal", "tf": m.group(3), "tight": bool(m.group(4)),
                "action": m.group(5), "sl": float(m.group(6)), "tp": float(m.group(7)), "sl_dist": float(m.group(8)),
                "cap": float(m.group(9)), "op": m.group(10), "z": float(m.group(11)),
            })
            continue
        if any(mark in ln for mark in CLEAR_MARKS):
            if ("[BTCUSD" in ln) and (f"[{pk}" not in ln):
                continue
            tm = LINE_TS_RE.match(ln)
            if tm:
                events.append({"t": log_ts_to_epoch(tm.group(1)), "kind": "clear"})
    events.sort(key=lambda e: (e["t"], 0 if e["kind"] == "clear" else 1))
    return events


def signal_windows(events: list) -> list:
    """Mỗi tín hiệu sống từ giờ log của nó đến sự kiện kế tiếp (tín hiệu mới thay thế / quét không tín hiệu xóa / khởi động lại);
    nếu không có sự kiện nào thì hết hạn sau mốc nến M5 kế tiếp + M5_SCAN_GRACE_SEC. Trả [(signal_event, t_end)]."""
    out = []
    for i, e in enumerate(events):
        if e["kind"] != "signal":
            continue
        nxt = events[i + 1]["t"] if i + 1 < len(events) else None
        boundary = (int(e["t"]) // 300 + 1) * 300
        cap_end = boundary + M5_SCAN_GRACE_SEC
        t_end = min(nxt, cap_end) if nxt is not None else cap_end
        out.append((e, max(t_end, e["t"])))
    return out


# ───────────────────────── Dữ liệu ─────────────────────────
class ReplayData:
    """Tick (mảng numpy theo time_msc UTC) + nến M1/M5 đã đóng (từ CSV; 'time' = epoch UTC)."""

    def __init__(self, ms, bid, ask, m1: dict, m5: dict):
        self.ms = np.asarray(ms, dtype=np.int64)
        self.bid = np.asarray(bid, dtype=float)
        self.ask = np.asarray(ask, dtype=float)
        self.candles = {60: m1, 300: m5}  # mỗi dict: time, open, high, low, close, tick_volume (mảng numpy)

    def tick_index(self, t_sec: float) -> int:
        return int(np.searchsorted(self.ms, int(round(t_sec * 1000.0)), side="right")) - 1


def _candle_arrays(df: pd.DataFrame) -> dict:
    return {k: df[k].to_numpy() for k in ("time", "open", "high", "low", "close", "tick_volume")}


def load_replay_data(data_dir: str) -> ReplayData:
    tick_files = sorted(glob.glob(os.path.join(data_dir, "ticks_*.csv")))
    m1_files = sorted(glob.glob(os.path.join(data_dir, "rates_m1_*.csv")))
    m5_files = sorted(glob.glob(os.path.join(data_dir, "rates_m5_*.csv")))
    if not (tick_files and m1_files and m5_files):
        raise FileNotFoundError(f"Thiếu ticks_*.csv / rates_m1_*.csv / rates_m5_*.csv trong {data_dir}")
    t = pd.read_csv(tick_files[-1], usecols=["time_msc", "bid", "ask"])
    return ReplayData(t["time_msc"].to_numpy(), t["bid"].to_numpy(), t["ask"].to_numpy(),
                      _candle_arrays(pd.read_csv(m1_files[-1])), _candle_arrays(pd.read_csv(m5_files[-1])))


def build_rates(data: ReplayData, tf_sec: int, now_s: float, count: int, start_pos: int) -> list:
    """Nến như copy_rates_from_pos(sym, tf, start_pos, count) tại thời điểm now_s (cũ -> mới):
    - nến ĐÃ ĐÓNG: chỉ khi time + tf <= now (nến chưa đóng KHÔNG lọt vào dữ liệu đóng);
    - nến ĐANG HÌNH THÀNH (start_pos=0): dựng chỉ từ tick <= now (bid), không lấy giá trị cuối cùng của file nến."""
    c = data.candles[tf_sec]
    n_closed = int(np.searchsorted(c["time"] + tf_sec, now_s, side="right"))  # số nến có time+tf <= now
    rows = [{"time": int(c["time"][i]), "open": float(c["open"][i]), "high": float(c["high"][i]),
             "low": float(c["low"][i]), "close": float(c["close"][i]), "tick_volume": int(c["tick_volume"][i]),
             "spread": 0, "real_volume": 0} for i in range(max(0, n_closed - count), n_closed)]
    if start_pos == 0:
        fs = int(now_s // tf_sec) * tf_sec
        i0 = int(np.searchsorted(data.ms, fs * 1000, side="left"))
        i1 = data.tick_index(now_s) + 1
        if i1 > i0:
            b = data.bid[i0:i1]
            rows.append({"time": fs, "open": float(b[0]), "high": float(b.max()), "low": float(b.min()),
                         "close": float(b[-1]), "tick_volume": int(i1 - i0), "spread": 0, "real_volume": 0})
    return rows[-count:] if count > 0 else rows


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now


class FakeMT5:
    """mt5 giả chỉ-đọc dữ liệu phát lại (đủ cho _check_pending_pullback_order, PA, _get_symbol_tolerances, _get_bot_positions)."""
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_H1 = 1, 5, 15, 16385
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    TIMEFRAME_SEC = {1: 60, 5: 300}

    def __init__(self, data: ReplayData, clock: Clock):
        self.data, self.clock = data, clock
        self.tick = None
        self.positions = []

    def initialize(self, *a, **k):
        return True

    def symbol_select(self, *a, **k):
        return True

    def symbol_info(self, symbol):
        return types.SimpleNamespace(digits=3, point=0.001, trade_stops_level=0, trade_contract_size=100.0,
                                     volume_min=0.01, volume_max=100.0, volume_step=0.01, filling_mode=2)

    def symbol_info_tick(self, symbol):
        return self.tick

    def positions_get(self, **kw):
        return list(self.positions)

    def last_error(self):
        return (1, "Success")

    def copy_rates_from_pos(self, symbol, tf, start_pos, count):
        return build_rates(self.data, self.TIMEFRAME_SEC.get(tf, 300), self.clock.now, int(count), int(start_pos))


# ───────────────────────── Pending + mô phỏng lệnh ─────────────────────────
def default_cfg(args) -> dict:
    cfg = dict(bot_mod.DEFAULT_PROFILE_SETTINGS[PK])
    cfg.update({"symbol": SYM, "tf": "M1", "analysis_tf": "M5", "max_sl_price": str(args.max_sl_base),
                "rr": str(args.tp_rr) if args.tp_rr else "0", "lot": str(LOT), "confirm_entry_pa": True,
                "pa_fail_open": False, "blocked_trading_windows": ""})
    if args.max_spread is not None:
        cfg["max_spread"] = str(args.max_spread)
    if args.max_pos is not None:
        cfg["max_pos"] = str(args.max_pos)
    return cfg


def build_pending(sig: dict, args, atr: float) -> dict:
    """Pending tương đương _run_analysis_worker (dòng ~11117): symbol, action, sl_price, manual_queued, zone_watch,
    effective_max_sl, sl_tightened_for_2r, target_tp_price, atr. (atr không có trong log -> suy ra, xem tài liệu.)"""
    tight = bool(sig["tight"])
    base = args.max_sl_base if args.max_sl is not None else (sig["cap"] * 2.0 if tight else sig["cap"])
    halve = tight and not args.no_halve_max_sl
    cap = round(base / 2.0, 2) if halve else base
    return {"symbol": SYM, "action": sig["action"], "sl_price": sig["sl"], "manual_queued": False, "zone_watch": True,
            "effective_max_sl": cap, "sl_tightened_for_2r": halve,
            "target_tp_price": None if args.tp_rr else sig["tp"], "atr": atr}


def simulate_outcome(data: ReplayData, idx: int, action: str, sl: float, tp: float):
    """Từ tick idx (lúc vào): SELL vào bằng bid, SL/TP chạm khi ASK >= sl / <= tp, đóng bằng ask tại tick đó;
    BUY vào bằng ask, chạm khi BID <= sl / >= tp, đóng bằng bid. Bỏ qua dời BE/trailing của bot. Trả dict kết quả."""
    n = len(data.ms)
    if action == "SELL":
        entry = float(data.bid[idx])
        px = data.ask[idx + 1:]
        hit_sl, hit_tp = px >= sl, px <= tp
    else:
        entry = float(data.ask[idx])
        px = data.bid[idx + 1:]
        hit_sl, hit_tp = px <= sl, px >= tp
    f_sl = int(np.argmax(hit_sl)) if hit_sl.any() else None
    f_tp = int(np.argmax(hit_tp)) if hit_tp.any() else None
    if f_sl is None and f_tp is None:
        j, reason = n - 1 - (idx + 1), "hết dữ liệu"
    elif f_tp is None or (f_sl is not None and f_sl <= f_tp):   # cùng tick: coi như SL trước (thận trọng)
        j, reason = f_sl, "SL"
    else:
        j, reason = f_tp, "TP"
    k = idx + 1 + j
    exit_px = float(data.ask[k]) if action == "SELL" else float(data.bid[k])
    pnl = ((entry - exit_px) if action == "SELL" else (exit_px - entry)) * OZ
    risk = abs(entry - sl) * OZ
    return {"entry": entry, "exit": exit_px, "reason": reason, "pnl": pnl, "r": (pnl / risk) if risk > 0 else 0.0,
            "exit_idx": k, "exit_ms": int(data.ms[k])}


# ───────────────────────── Vòng phát lại ─────────────────────────
def make_app(fake: FakeMT5, cfg: dict, clock: Clock, logs: list):
    app = object.__new__(bot_mod.KronosDesktopApp)
    ctx = {"pending_pullback_order": None}
    app.__dict__.update(
        _mt5_lock=threading.RLock(), _order_in_flight={}, active_profile_key=PK, profile_ctx={PK: ctx},
        profile_settings={PK: dict(cfg)}, pending_pullback_order=None, auto_loop_running=True, cached_tp=None,
        _last_order_candle_ts={}, _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk: False,
        _get_profile_cfg=lambda pk=None: dict(cfg), _get_profile_magic_number=lambda pk=None: MAGIC,
        _write_file_log=lambda *a, **k: None, log=lambda msg, *a, **k: logs.append((clock.now, str(msg))),
        log_error=lambda *a, **k: None,
    )
    return app


def run_replay(data: ReplayData, windows: list, args) -> dict:
    """Phát lại các cửa sổ tín hiệu; trả {"signals": [...], "fills": [...], "logs": [...]}."""
    clock, logs = Clock(), []
    fake = FakeMT5(data, clock)
    cfg = default_cfg(args)
    app = make_app(fake, cfg, clock, logs)
    ctx = app.profile_ctx[PK]
    gate_counts = Counter()
    fills = []
    state = {"fill": None}

    real_gate_log = bot_mod.KronosDesktopApp._pb_gate_log

    def counting_gate_log(pk, gate, *a, **k):
        gate_counts[gate] += 1
        return real_gate_log(app, pk, gate, *a, **k)
    app._pb_gate_log = counting_gate_log

    def fake_execute(symbol, action, lot, sl_price, tp_price, max_pos, profile_key=None):
        t = fake.tick
        idx = data.tick_index(clock.now)
        out = simulate_outcome(data, idx, action, float(sl_price), float(tp_price))
        rec = {"t": clock.now, "action": action, "bid": float(t.bid), "ask": float(t.ask), "sl": float(sl_price),
               "tp": float(tp_price), **out}
        state["fill"] = rec
        fake.positions.append(types.SimpleNamespace(
            ticket=len(fills) + 1, type=0 if action == "BUY" else 1, symbol=SYM, magic=MAGIC, volume=LOT,
            price_open=rec["entry"], sl=float(sl_price), tp=float(tp_price), profit=0.0, price_current=rec["entry"],
            _exit_ms=out["exit_ms"]))
        return True, f"[FAKE] vào lệnh giả {action} @ {rec['entry']:.3f}"
    app.execute_mt5_order = fake_execute
    if args.no_pa:
        app._check_price_action_entry_confirmation = lambda *a, **k: (True, "PA bỏ qua (--no-pa)")

    results = []
    patches = (mock.patch.object(bot_mod, "mt5", fake), mock.patch.object(bot_mod, "MT5_AVAILABLE", True),
               mock.patch.object(bot_mod, "is_trading_window_blocked", lambda *a, **k: (False, "")),
               mock.patch.object(bot_mod.time, "time", clock.time), mock.patch.object(bot_mod.time, "monotonic", clock.monotonic))
    for p in patches:
        p.start()
    try:
        tols = app._get_symbol_tolerances(SYM)
        for sig, t_end in windows:
            gate_counts.clear()
            n_logs0 = len(logs)
            clock.now = sig["t"]
            idx0 = data.tick_index(clock.now)
            atr = args.atr if args.atr else _atr_m5(app, fake, clock.now)
            pend = build_pending(sig, args, atr)
            ctx["pending_pullback_order"] = app.pending_pullback_order = pend
            cap, sl = pend["effective_max_sl"], sig["sl"]
            z = sl - cap if sig["action"] == "SELL" else sl + cap
            if sig["action"] == "SELL":
                lo, hi = z - tols["max_sl_tol"], sl - tols["sl_break_tol"]
            else:
                lo, hi = sl + tols["sl_break_tol"], z + tols["max_sl_tol"]
            calls, state["fill"], end_reason = 0, None, "hết hạn/thay thế theo log"
            step = max(args.poll_ms, 1) / 1000.0
            k = 1
            while True:
                T = sig["t"] + k * step
                if T >= t_end:
                    break
                k += 1
                clock.now = T
                i = data.tick_index(T)
                if i < 0:
                    continue
                fake.tick = types.SimpleNamespace(bid=float(data.bid[i]), ask=float(data.ask[i]), time=int(data.ms[i] // 1000))
                _update_positions(fake, data.ms[i], data.bid[i], data.ask[i])
                calls += 1
                app._check_pending_pullback_order(SYM, fake.tick, profile_key=PK)
                if state["fill"] is not None:
                    ctx["pending_pullback_order"] = app.pending_pullback_order = None
                    end_reason = "đã vào lệnh giả"
                    break
                if ctx["pending_pullback_order"] is None:
                    end_reason = "bot xóa pending (hủy/khóa/cooldown/khung giờ)"
                    break
            i_end = data.tick_index(min(clock.now, t_end))
            seg_bid, seg_ask = data.bid[idx0:i_end + 1], data.ask[idx0:i_end + 1]
            if sig["action"] == "SELL":
                gap = (z - float(seg_bid.max())) if len(seg_bid) else None
            else:
                gap = (float(seg_ask.min()) - z) if len(seg_ask) else None
            skip = {"chua_vao_vung"}
            top = [(g, c) for g, c in gate_counts.most_common() if g not in skip]
            results.append({
                "sig": sig, "z": z, "zone": (lo, hi), "cap": cap, "calls": calls, "gap_to_z": gap,
                "gates": dict(gate_counts), "top_gate": (top[0] if top else (("chua_vao_vung", gate_counts["chua_vao_vung"]) if gate_counts else None)),
                "fill": state["fill"], "end": end_reason, "diag": [m for t0, m in logs[n_logs0:] if m.startswith("🔎")],
            })
            if state["fill"] is not None:
                fills.append(state["fill"])
            fake.positions[:] = [x for x in fake.positions if getattr(x, "_exit_ms", 0) > clock.now * 1000.0]
    finally:
        for p in reversed(patches):
            p.stop()
    return {"signals": results, "fills": fills, "logs": logs}


def _update_positions(fake: FakeMT5, now_ms, bid, ask):
    fake.positions[:] = [x for x in fake.positions if getattr(x, "_exit_ms", 0) > now_ms]
    for x in fake.positions:
        x.price_current = float(bid if x.type == 0 else ask)
        x.profit = ((float(bid) - x.price_open) if x.type == 0 else (x.price_open - float(ask))) * OZ


def _atr_m5(app, fake, now_s) -> float:
    rows = fake.copy_rates_from_pos(SYM, 5, 1, 15)
    if len(rows) < 3:
        return 1.5
    try:
        return float(app.calculate_atr(pd.DataFrame(rows), 14))
    except Exception:
        return 1.5


# ───────────────────────── Báo cáo ─────────────────────────
def fmt_vn(t: float) -> str:
    return datetime.fromtimestamp(t, tz=VN_TZ).strftime("%d/%m %H:%M:%S")


def summarize(res: dict) -> dict:
    f = res["fills"]
    wins = sum(1 for x in f if x["pnl"] > 0)
    losses = sum(1 for x in f if x["pnl"] <= 0)
    return {"n": len(f), "win": wins, "loss": losses, "pnl": sum(x["pnl"] for x in f),
            "avg_r": (sum(x["r"] for x in f) / len(f)) if f else 0.0}


def print_signal_report(res: dict):
    print(f"{'giờ VN':<15}{'hướng':<6}{'Z':>9}  {'vùng kích hoạt':<19}{'gọi':>6}  {'cách Z (cao nhất bid/thấp nhất ask)':<36}{'cổng chặn nhiều nhất':<26}vào lệnh giả")
    for r in res["signals"]:
        s = r["sig"]
        gap = "n/a" if r["gap_to_z"] is None else (f"{r['gap_to_z']:+.2f}$ (chưa tới Z)" if r["gap_to_z"] > 0 else f"{-r['gap_to_z']:.2f}$ (đã vượt Z)")
        tg = f"{r['top_gate'][0]} x{r['top_gate'][1]}" if r["top_gate"] else "-"
        fl = r["fill"]
        fl_s = f"CÓ {fmt_vn(fl['t'])} bid={fl['bid']:.2f} ask={fl['ask']:.2f}" if fl else "không"
        print(f"{fmt_vn(s['t']):<15}{s['action']:<6}{r['z']:>9.2f}  [{r['zone'][0]:.2f}, {r['zone'][1]:.2f}]".ljust(52)
              + f"{r['calls']:>6}  {gap:<36}{tg:<26}{fl_s}")


def print_variants_table(rows: list):
    print(f"\n{'biến thể':<44}{'số lệnh':>8}{'thắng':>7}{'thua':>6}{'tổng $':>10}{'R TB':>8}")
    for name, sm in rows:
        print(f"{name:<44}{sm['n']:>8}{sm['win']:>7}{sm['loss']:>6}{sm['pnl']:>10.2f}{sm['avg_r']:>8.2f}")
    print("\n⚠️  CẢNH BÁO: mẫu chỉ 17 tín hiệu trong MỘT đêm — KHÔNG dùng để chọn luật giao dịch.")
    print("   Mô phỏng bỏ qua dời BE/trailing của bot; mỗi tín hiệu tối đa 1 lệnh giả; SL/TP đóng bằng giá tick (bid/ask) tại thời điểm chạm.")


def variant_name(args) -> str:
    parts = []
    if args.no_halve_max_sl:
        parts.append("no-halve")
    if args.tp_rr:
        parts.append(f"tp-rr{args.tp_rr:g}")
    if args.no_pa:
        parts.append("no-pa")
    if args.max_sl is not None:
        parts.append(f"max-sl{args.max_sl:g}")
    return "+".join(parts) if parts else "hiện tại (baseline)"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Phát lại tick vào _check_pending_pullback_order (chỉ đọc CSV và log)")
    appdata = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Kronos_MT5_Bot")
    ap.add_argument("--data-dir", default=os.path.join(appdata, "replay_data"))
    ap.add_argument("--log", action="append", default=None, help="File log hoạt động (lặp lại được); mặc định: các bot_activity_*.log phủ khoảng tick")
    ap.add_argument("--poll-ms", type=int, default=1000)
    ap.add_argument("--no-halve-max-sl", action="store_true", help="Trần SL của vùng dùng Max SL gốc (4.0) thay vì chia đôi khi dựng pending")
    ap.add_argument("--tp-rr", type=float, default=0.0, help="TP = giá vào ∓ R x khoảng cách SL (thay TP của AI); bot sàn RR tối thiểu 2.0")
    ap.add_argument("--max-sl", type=float, default=None, help="Max SL gốc (mặc định suy ra từ log: 4.0)")
    ap.add_argument("--no-pa", action="store_true", help="Bỏ qua xác nhận Price Action")
    ap.add_argument("--max-spread", type=float, default=None)
    ap.add_argument("--max-pos", type=int, default=None)
    ap.add_argument("--atr", type=float, default=0.0, help="ATR đưa vào pending cho PA (mặc định: ATR14 nến M5 đóng tại thời điểm tín hiệu)")
    ap.add_argument("--grid", action="store_true", help="Chạy mọi tổ hợp của 3 cờ nhị phân (chỉ sau khi baseline cho 0 lệnh)")
    args = ap.parse_args(argv)
    args.max_sl_base = args.max_sl if args.max_sl is not None else 4.0
    return args


def _log_files(args, data: ReplayData) -> list:
    if args.log:
        return args.log
    d0 = datetime.fromtimestamp(data.ms[0] / 1000.0, tz=VN_TZ).date()
    d1 = datetime.fromtimestamp(data.ms[-1] / 1000.0, tz=VN_TZ).date()
    base = os.path.join(os.path.dirname(args.data_dir), "logs")
    out, d = [], d0
    while d <= d1:
        f = os.path.join(base, f"bot_activity_{d.isoformat()}.log")
        if os.path.exists(f):
            out.append(f)
        d += timedelta(days=1)
    return out


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    data = load_replay_data(args.data_dir)
    lines = []
    for f in _log_files(args, data):
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            lines.extend(fh.read().splitlines())
    events = parse_log_events(lines)
    windows = [(s, e) for s, e in signal_windows(events)
               if data.ms[0] / 1000.0 <= s["t"] <= data.ms[-1] / 1000.0]
    n_sell = sum(1 for s, _ in windows if s["action"] == "SELL")
    print(f"[*] {len(data.ms):,} tick | {len(windows)} tín hiệu canh vùng {PK} ({n_sell} SELL, {len(windows) - n_sell} BUY) | poll {args.poll_ms}ms")

    # 1) KIỂM CHỨNG: luật hiện tại (mọi cờ biến thể tắt) phải cho 0 vào lệnh giả
    base_args = parse_args(["--poll-ms", str(args.poll_ms)] + (["--max-spread", str(args.max_spread)] if args.max_spread is not None else [])
                           + (["--max-pos", str(args.max_pos)] if args.max_pos is not None else []) + (["--atr", str(args.atr)] if args.atr else []))
    res0 = run_replay(data, windows, base_args)
    print("\n=== KIỂM CHỨNG: luật hiện tại ===")
    print_signal_report(res0)
    sm0 = summarize(res0)
    print(f"\nTổng vào lệnh giả (luật hiện tại): {sm0['n']}  (kỳ vọng 0)")
    if sm0["n"] != 0:
        f0 = next(r for r in res0["signals"] if r["fill"])
        s = f0["sig"]
        print(f"❌ KIỂM CHỨNG KHÔNG ĐẠT. Ca đầu tiên: {fmt_vn(s['t'])} {s['action']} Z={f0['z']:.2f} -> vào lệnh giả {fmt_vn(f0['fill']['t'])} "
              f"bid={f0['fill']['bid']:.2f} ask={f0['fill']['ask']:.2f}")
        print("   Điều kiện có thể khác bot thật (sai lệch của tool): spread/max_pos/khung giờ cấm/PA/atr, đồng hồ poll, hoặc pending dựng từ log. DỪNG, không thử biến thể.")
        return 3
    print("✅ Kiểm chứng đạt: 0 vào lệnh giả, khớp kết quả thật.")

    # 2) Biến thể
    rows = [("hiện tại (baseline)", sm0)]
    variants = []
    if args.grid:
        for nh, tp, npa in itertools.product((False, True), (0.0, 2.0), (False, True)):
            if not (nh or tp or npa):
                continue
            variants.append(parse_args(["--poll-ms", str(args.poll_ms)] + (["--no-halve-max-sl"] if nh else [])
                                       + (["--tp-rr", str(tp)] if tp else []) + (["--no-pa"] if npa else [])
                                       + (["--max-sl", str(args.max_sl)] if args.max_sl is not None else [])
                                       + (["--max-spread", str(args.max_spread)] if args.max_spread is not None else [])
                                       + (["--max-pos", str(args.max_pos)] if args.max_pos is not None else [])
                                       + (["--atr", str(args.atr)] if args.atr else [])))
    elif args.no_halve_max_sl or args.tp_rr or args.no_pa or args.max_sl is not None:
        variants.append(args)
    for v in variants:
        r = run_replay(data, windows, v)
        rows.append((variant_name(v), summarize(r)))
        if len(variants) == 1:
            print(f"\n=== Biến thể: {variant_name(v)} ===")
            print_signal_report(r)
    if len(rows) > 1:
        print_variants_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
