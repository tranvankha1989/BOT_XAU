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
import sys
import threading
import time
import types
from datetime import datetime

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
    args = ap.parse_args(argv)

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

    if not mt5.initialize():
        print(f"[ERROR] mt5.initialize() thất bại: {mt5.last_error()}")
        return 1

    log_lines, toasts = [], []
    t0 = time.perf_counter()
    prev_mt5 = bot_mod.mt5
    bot_mod.mt5 = ReadOnlyMT5(mt5)  # từ đây mọi lời gọi mt5 trong bot chỉ đọc
    try:
        app = build_app(pk, cfg, symbol, args.start, args.end, args.precision, not args.no_ai,
                        args.capital, args.lot, log_lines, toasts)
        app._run_backtest_simulation()
        trades = list(app._bt_trades_cache or [])
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

    report = bot_mod.build_backtest_report(trades, args.capital)
    cal_days = (d1 - d0).days + 1 if d1 >= d0 else (d0 - d1).days + 1
    print("-" * 65)
    print(f"Chế độ chạy thực tế  : {mode}")
    print(f"Số ngày giao dịch    : {report.get('n_days', 0)} (trong {cal_days} ngày lịch)")
    print(f"Số lệnh              : {report['n_trades']}")
    print(f"Thời gian chạy       : {elapsed:.1f} giây")
    print("-" * 65)
    for line in bot_mod.format_backtest_report(report):
        print(line)

    if trades:
        fname = f"backtest_{symbol}_{args.start}_{args.end}_{datetime.now().strftime('%H%M%S')}.csv"
        path = bot_mod.export_backtest_csv(trades, os.path.join(out_dir, fname))
        print(f"💾 Đã xuất CSV: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
