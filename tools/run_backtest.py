"""
tools/run_backtest.py
─────────────────────
Chạy backtest từ dòng lệnh, KHÔNG cần giao diện (Tkinter).

- Dựng KronosDesktopApp bằng object.__new__ (không chạy __init__), stub phần giao diện/log.
- Dùng module MetaTrader5 thật CHỈ để ĐỌC dữ liệu (nến/tick). mt5 được bọc proxy chỉ-đọc:
  mọi lời gọi order_send/order_check đều bị chặn bằng ngoại lệ -> tuyệt đối không gửi lệnh.
- Cấu hình: DEFAULT_PROFILE_SETTINGS của profile; nếu có user_settings.json thì CHỈ lấy các khóa chiến lược
  (loại hẳn hf_*, telegram_*, magic, symbol...), không in ra bất kỳ giá trị nhạy cảm nào.
- Gọi lại logic backtest sẵn có (_run_backtest_simulation) rồi dùng build_backtest_report /
  format_backtest_report / export_backtest_csv để in báo cáo và ghi CSV.

Ví dụ:
    python tools/run_backtest.py --profile XAUUSD --symbol XAUUSDm --start 2026-11-03 --end 2026-11-07
    python tools/run_backtest.py --profile BTCUSD --symbol BTCUSDm --start 2026-11-03 --end 2026-11-07 --precision S1
"""

import argparse
import json
import os
import random
import sys
import threading
import time
import types
from datetime import datetime, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    import MetaTrader5 as mt5
except ImportError:  # máy không có MetaTrader5: chỉ báo lỗi khi chạy thật
    mt5 = None

import mt5_scalping_bot as bot_mod

# Khóa KHÔNG được lấy từ user_settings.json (nhạy cảm hoặc không thuộc chiến lược)
EXCLUDED_KEYS = {"symbol", "magic_number", "engine", "realtime_chart", "hf_space", "hf_token"}
EXCLUDED_PREFIXES = ("telegram_",)


class ReadOnlyMT5:
    """Proxy bọc module MetaTrader5: cho đọc dữ liệu, chặn mọi hàm gửi/kiểm tra lệnh."""
    BLOCKED = {"order_send", "order_check"}

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name in ReadOnlyMT5.BLOCKED:
            raise RuntimeError(f"CHẶN: run_backtest chỉ đọc dữ liệu, không được gọi mt5.{name}")
        return getattr(self._inner, name)


def load_strategy_cfg(profile_key: str):
    """Trả (cfg, số khóa lấy từ user_settings.json, ghi chú). Không bao giờ trả/in token."""
    cfg = dict(bot_mod.DEFAULT_PROFILE_SETTINGS[profile_key])
    loaded = 0
    note = "dùng DEFAULT_PROFILE_SETTINGS (không có user_settings.json)"
    path = bot_mod.SETTINGS_FILE
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved = (data.get("profiles") or {}).get(profile_key, {})
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if k in cfg and k not in EXCLUDED_KEYS and not k.startswith(EXCLUDED_PREFIXES):
                        cfg[k] = v
                        loaded += 1
            note = f"đã nạp {loaded} khóa chiến lược từ user_settings.json (không đọc token)"
            del data  # không giữ nội dung file (có thể chứa token) lâu hơn cần thiết
        except Exception as ex:
            note = f"không đọc được user_settings.json ({type(ex).__name__}), dùng mặc định"
    return cfg, loaded, note


def cpu_core_counts():
    """Trả (lõi vật lý, lõi logic). Không có psutil thì vật lý = logic//2."""
    logical = os.cpu_count() or 2
    try:
        import psutil
        phys = psutil.cpu_count(logical=False) or max(1, logical // 2)
        logical = psutil.cpu_count(logical=True) or logical
    except Exception:
        phys = max(1, logical // 2)
    return max(1, phys), logical


def resolve_threads(requested, phys):
    """Chọn số luồng: mặc định = lõi vật lý; vượt lõi vật lý thì chặn lại. Trả (n, cảnh báo hoặc None)."""
    if requested is None:
        return phys, None
    if requested < 1:
        return 1, f"--threads {requested} không hợp lệ, dùng 1"
    if requested > phys:
        return phys, f"--threads {requested} vượt số lõi vật lý ({phys}), đã chặn xuống {phys}"
    return requested, None


def apply_threads(n: int):
    """Ghi đè giới hạn luồng của bot (bot đặt lúc import bằng torch.set_num_threads). Chỉ ảnh hưởng tiến trình backtest này."""
    bot_mod.torch.set_num_threads(n)
    os.environ["OMP_NUM_THREADS"] = os.environ["MKL_NUM_THREADS"] = str(n)


def set_seed(seed: int):
    """Đặt seed cho random, numpy, torch (CPU và CUDA nếu có) để lần chạy lặp lại được."""
    random.seed(seed)
    bot_mod.np.random.seed(seed)
    torch = bot_mod.torch
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def trade_entry_dt(t: dict):
    """Thời điểm vào lệnh (giờ VN, naive) = cột date (YYYY-MM-DD) + giờ trong in_time ("dd/mm HH:MM[:SS]").
    Trả None nếu bản ghi thiếu/không đọc được."""
    try:
        day = str(t["date"])
        hms = str(t["in_time"]).split(" ")[-1]
        if hms.count(":") == 1:
            hms += ":00"
        return datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def filter_trades_by_range(trades: list, start: str, end: str):
    """Giữ lệnh có thời điểm vào trong [start 00:00:00, end 23:59:59] (giờ VN).
    Trả (kept, dropped, unparsed): lệnh không xác định được thời điểm vào được GIỮ LẠI và đếm riêng."""
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    if d0 > d1:
        d0, d1 = d1, d0
    lo = d0
    hi = d1 + timedelta(hours=23, minutes=59, seconds=59)
    kept, dropped, unparsed = [], [], 0
    for t in trades:
        dt = trade_entry_dt(t)
        if dt is None:
            unparsed += 1
            kept.append(t)
        elif lo <= dt <= hi:
            kept.append(t)
        else:
            dropped.append(t)
    return kept, dropped, unparsed


def compute_extra_metrics(trades: list) -> dict:
    """Tổng lãi/lỗ, profit factor, kỳ vọng R theo hai cách (tính từ danh sách lệnh):
    (a) trung bình R của từng lệnh; (b) tổng lãi ròng / tổng risk_usd (chỉ các lệnh có risk_usd > 0)."""
    pnls = [float(t["pnl"]) for t in trades]
    win = sum(x for x in pnls if x > 0)
    loss = abs(sum(x for x in pnls if x < 0))
    risky = [t for t in trades if float(t.get("risk_usd") or 0.0) > 0]
    rs = [float(t["pnl"]) / float(t["risk_usd"]) for t in risky]
    total_risk = sum(float(t["risk_usd"]) for t in risky)
    risky_pnl = sum(float(t["pnl"]) for t in risky)
    r_a = (sum(rs) / len(rs)) if rs else None
    r_b = (risky_pnl / total_risk) if total_risk > 0 else None
    return {
        "total_pnl": sum(pnls),
        "profit_factor": (win / loss) if loss > 0 else (float("inf") if win > 0 else 0.0),
        "r_mean_per_trade": r_a,
        "r_total_over_risk": r_b,
        "n_risk_trades": len(risky),
        "r_sign_conflict": (r_a is not None and r_b is not None and r_a * r_b < 0),
    }


def format_extra_metrics(m: dict) -> list:
    pf = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    ra = f"{m['r_mean_per_trade']:+.3f}R" if m["r_mean_per_trade"] is not None else "n/a"
    rb_ = f"{m['r_total_over_risk']:+.3f}R" if m["r_total_over_risk"] is not None else "n/a"
    lines = [
        "📐 ===== THỐNG KÊ BỔ SUNG (tính từ danh sách lệnh) =====",
        f"Tổng lãi/lỗ: {m['total_pnl']:+.2f} $ | Profit factor: {pf}",
        f"Kỳ vọng R (a) trung bình R từng lệnh: {ra} | (b) tổng lãi ròng / tổng risk_usd: {rb_} (n={m['n_risk_trades']} lệnh có risk)",
    ]
    if m["r_sign_conflict"]:
        lines.append("⚠️ CẢNH BÁO: hai cách tính kỳ vọng R TRÁI DẤU (a và b) - kết quả phụ thuộc mạnh vào độ lớn risk từng lệnh, cần thận trọng khi kết luận.")
    return lines


def build_app(profile_key, cfg, symbol, start, end, precision, use_ai, capital, lot, log_lines, toasts):
    """Dựng KronosDesktopApp không giao diện, chỉ gắn thuộc tính _run_backtest_simulation cần."""
    var = lambda v: types.SimpleNamespace(get=lambda: v)
    app = object.__new__(bot_mod.KronosDesktopApp)
    prog = {"next": 0}

    def update_status(msg, pct):
        if pct >= prog["next"]:
            print(f"  [{int(pct):>3}%] {msg}")
            prog["next"] = int(pct) // 10 * 10 + 10

    app.__dict__.update(
        _mt5_lock=threading.RLock(), _ai_inference_lock=threading.RLock(), local_cache={},
        active_profile_key=profile_key, _loading_settings=False, is_analyzing=False, _bt_cancel_flag=False,
        profile_settings={profile_key: cfg}, profile_ctx={profile_key: {}},
        bt_sym_var=var(symbol), bt_start_var=var(start), bt_end_var=var(end),
        bt_data_precision_var=var("S1 (Tick 1 giây)" if precision == "S1" else "Nến MT5"),
        bt_use_ai_var=var(1 if use_ai else 0), bt_capital_var=var(str(capital)), bt_lot_var=var(str(lot)),
        _bt_trades_cache=[], _bt_equity_points=[],
        # Stub giao diện: root.after chạy ngay (không có vòng lặp Tk), UI/log được thu thập
        root=types.SimpleNamespace(after=lambda ms, fn=None, *a: fn(*a) if fn else None),
        log=lambda msg, *a, **k: log_lines.append(str(msg)),
        log_error=lambda *a, **k: log_lines.append("LỖI: " + " ".join(str(x) for x in a)),
        show_toast=lambda msg, is_error=False, duration_ms=0: toasts.append((str(msg), bool(is_error))),
        _write_file_log=lambda *a, **k: None,
        _update_bt_status=update_status,
        _push_live_bt_trade=lambda *a, **k: None,
        _render_backtest_results=lambda *a, **k: None,  # kết quả lấy từ _bt_trades_cache
    )
    return app


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chạy backtest Kronos từ dòng lệnh (chỉ đọc dữ liệu MT5)")
    ap.add_argument("--profile", required=True, choices=["XAUUSD", "BTCUSD"])
    ap.add_argument("--symbol", default=None, help="Mã MT5 (mặc định: symbol của profile)")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", default=None, help="Thư mục xuất CSV (mặc định SETTINGS_DIR\\backtests)")
    ap.add_argument("--precision", choices=["CANDLE", "S1"], default="CANDLE", help="CANDLE = nến MT5, S1 = tick 1 giây")
    ap.add_argument("--no-ai", action="store_true", help="Tắt Não AI Kronos, dùng thuật toán kỹ thuật")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--lot", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=1, help="Seed cho random/numpy/torch (mặc định 1)")
    ap.add_argument("--keep-warmup-trades", action="store_true",
                    help="Giữ cả lệnh vào ngoài [start, end] (khởi động/warm-up); mặc định các lệnh này bị loại khỏi thống kê")
    ap.add_argument("--threads", type=int, default=None,
                    help="Số luồng CPU cho torch (mặc định = số lõi vật lý, tối đa = số lõi vật lý)")
    args = ap.parse_args(argv)

    phys, logical = cpu_core_counts()
    n_threads, thr_warn = resolve_threads(args.threads, phys)
    if thr_warn:
        print(f"[WARN] {thr_warn}")
    apply_threads(n_threads)
    print(f"threads: {n_threads} (vật lý {phys}, logic {logical})")

    pk = args.profile
    cfg, _, cfg_note = load_strategy_cfg(pk)
    symbol = (args.symbol or str(cfg.get("symbol", "")) or bot_mod.DEFAULT_PROFILE_SETTINGS[pk]["symbol"]).strip()
    try:
        d0 = datetime.strptime(args.start, "%Y-%m-%d").date()
        d1 = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        print("[ERROR] --start/--end phải theo định dạng YYYY-MM-DD")
        return 2
    # Hàm backtest tự suy ra profile từ tên symbol ("BTC" trong tên -> BTCUSD) nên phải khớp với --profile
    if ("BTC" in symbol.upper()) != (pk == "BTCUSD"):
        print(f"[ERROR] --profile {pk} không khớp symbol {symbol} (logic backtest suy profile theo tên symbol)")
        return 2
    if mt5 is None:
        print("[ERROR] Chưa cài thư viện MetaTrader5")
        return 1

    out_dir = args.out or os.path.join(bot_mod.SETTINGS_DIR, "backtests")
    print("=" * 65)
    print("   KRONOS BACKTEST (DÒNG LỆNH, CHỈ ĐỌC DỮ LIỆU)")
    print("=" * 65)
    print(f"[*] Profile / Symbol : {pk} / {symbol}")
    print(f"[*] Khoảng ngày      : {args.start} -> {args.end}")
    print(f"[*] Chế độ yêu cầu   : {'Tick S1 (1 giây)' if args.precision == 'S1' else 'Nến MT5'} | Não AI: {'TẮT' if args.no_ai else 'BẬT'}")
    print(f"[*] Cấu hình         : {cfg_note}")
    print(f"[*] Seed             : {args.seed}")

    if not mt5.initialize():
        print(f"[ERROR] mt5.initialize() thất bại: {mt5.last_error()}")
        return 1

    log_lines, toasts = [], []
    set_seed(args.seed)
    t0 = time.perf_counter()
    prev_mt5 = bot_mod.mt5
    bot_mod.mt5 = ReadOnlyMT5(mt5)  # từ đây mọi lời gọi mt5 trong bot chỉ đọc
    try:
        app = build_app(pk, cfg, symbol, args.start, args.end, args.precision, not args.no_ai,
                        args.capital, args.lot, log_lines, toasts)
        app._run_backtest_simulation()
        all_trades = list(app._bt_trades_cache or [])
    finally:
        bot_mod.mt5 = prev_mt5
        try:
            mt5.shutdown()
        except Exception:
            pass
    elapsed = time.perf_counter() - t0

    errors = [m for m, is_err in toasts if is_err]
    if errors:
        for m in errors:
            print(f"[ERROR] {m}")
        return 2

    fell_back = any("fallback" in ln.lower() for ln in log_lines)
    if args.precision == "S1" and not fell_back:
        mode = "Tick S1 (1 giây)"
    elif args.precision == "S1":
        mode = "Nến MT5 (đã fallback từ Tick S1 vì thiếu tick)"
    else:
        mode = "Nến MT5"
    for ln in log_lines[:20]:
        print(f"  [log] {ln}")

    if args.keep_warmup_trades:
        trades, dropped, unparsed = all_trades, [], 0
    else:
        trades, dropped, unparsed = filter_trades_by_range(all_trades, args.start, args.end)
    dropped_pnl = sum(float(t["pnl"]) for t in dropped)

    report = bot_mod.build_backtest_report(trades, args.capital)
    cal_days = (d1 - d0).days + 1 if d1 >= d0 else (d0 - d1).days + 1
    print("-" * 65)
    print(f"Chế độ chạy thực tế  : {mode}")
    print(f"Số ngày giao dịch    : {report.get('n_days', 0)} (trong {cal_days} ngày lịch)")
    print(f"Số lệnh              : {report['n_trades']}")
    print(f"Seed                 : {args.seed}")
    if args.keep_warmup_trades:
        print("Lệnh warm-up         : ĐANG GIỮ (--keep-warmup-trades) - thống kê gồm cả lệnh vào ngoài [start, end]")
    else:
        print(f"Lệnh ngoài khoảng    : loại {len(dropped)} lệnh vào ngoài [{args.start} 00:00:00, {args.end} 23:59:59] giờ VN, "
              f"tổng lãi/lỗ nhóm này {dropped_pnl:+.2f} $ (KHÔNG tính vào thống kê chính)")
    if unparsed:
        print(f"⚠️ {unparsed} lệnh không xác định được thời điểm vào nên được giữ lại")
    print(f"Thời gian chạy       : {elapsed:.1f} giây")
    print("-" * 65)
    for line in bot_mod.format_backtest_report(report):
        print(line)
    if trades:
        for line in format_extra_metrics(compute_extra_metrics(trades)):
            print(line)

    if trades:
        fname = f"backtest_{symbol}_{args.start}_{args.end}_{datetime.now().strftime('%H%M%S')}.csv"
        path = bot_mod.export_backtest_csv(trades, os.path.join(out_dir, fname))
        print(f"💾 Đã xuất CSV: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
