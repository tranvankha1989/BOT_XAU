"""
tools/demo_check.py
───────────────────
Kiểm tra an toàn các sửa đổi của Giai đoạn 1 trên MT5 THẬT (Tài khoản DEMO).
Độc lập, không import Tkinter, có chốt an toàn và khối dọn dẹp tự động.
"""

import sys
import os
import time
import argparse
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import MetaTrader5 as mt5
import mt5_scalping_bot as bot_mod

opened_tickets = []

def safe_close_ticket(ticket, symbol):
    """Đóng dứt điểm một ticket cụ thể trên MT5."""
    try:
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            return True
        pos = positions[0]
        tick = mt5.symbol_info_tick(symbol)
        sym_info = mt5.symbol_info(symbol)
        if not tick or not sym_info:
            return False
        digits = sym_info.digits
        is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
        close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "price": round(price, digits),
            "deviation": 50,
            "magic": getattr(pos, "magic", 0),
            "comment": "DEMO_CLEANUP",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res is not None and res.retcode in (10009, 10008):
            return True
        # Thử FOK nếu IOC từ chối
        req["type_filling"] = mt5.ORDER_FILLING_FOK
        res = mt5.order_send(req)
        return (res is not None and res.retcode in (10009, 10008))
    except Exception as ex:
        print(f"  [ERROR] Lỗi ngoại lệ khi đóng #{ticket}: {ex}")
        return False

def setup_stub_app(symbol, profile_key="XAUUSD", magic=20260501):
    """Tạo đối tượng KronosDesktopApp với các thuộc tính và stub cần thiết."""
    app = bot_mod.KronosDesktopApp.__new__(bot_mod.KronosDesktopApp)
    app._mt5_lock = bot_mod.threading.RLock()
    app._order_guard_lock = bot_mod.threading.Lock()
    app._last_mt5_order_mono = 0.0
    app._cached_pid_magic = {}
    app._pid_magic_backoff = {}
    app._logged_magic_fail_pids = set()
    app._cached_closed_deals_map = {}
    app._cached_deals_by_sym = {}
    app._last_drawn_chart_key = None
    app.active_profile_key = profile_key
    app.app_version = "1.3.7"
    app.daily_limit_locked_date = None
    app.daily_limit_reason = ""
    app.daily_limit_locked_kind = None
    app.daily_limit_locked_threshold = 0.0
    app.cooldown_until = None
    app.fast_sl_cooldown_until = None
    app.basket_cooldown_until = None
    app.auto_loop_running = False

    app._order_in_flight = {profile_key: False}
    app._last_order_open_mono = {profile_key: 0.0}
    app._last_order_candle_ts = {profile_key: None}
    app._loading_settings = False

    # Cấu hình profile
    cfg_data = {
        "symbol": symbol,
        "magic_number": magic,
        "max_positions": 2,
        "max_sl_price": 10.0,
        "one_order_per_candle": False,
        "entry_mode": "Bình thường (Độc lập)",
        "daily_tp_usd": 0.0,
        "daily_sl_usd": 0.0,
        "max_consec_loss": 0,
        "cooldown_mins": 0.0,
    }
    app.profile_settings = {
        profile_key: cfg_data
    }
    app.profile_cfg = {
        profile_key: cfg_data
    }
    app.profile_ctx = {
        profile_key: {
            "daily_limit_locked_date": None,
            "daily_limit_reason": "",
            "daily_limit_locked_kind": None,
            "daily_limit_locked_threshold": 0.0,
            "cooldown_until": None,
            "fast_sl_cooldown_until": None,
            "basket_cooldown_until": None,
            "cached_daily_pnl": 0.0,
            "consec_loss_count": 0,
            "pending_pullback_order": None,
            "last_fast_sl_deal_ticket": None,
            "last_cooldown_deal_ticket": None,
            "last_candle_time": None,
        }
    }

    # Stubs giao diện và thông báo
    app.log = lambda msg, *args, **kwargs: None
    app.log_error = lambda tag, ex=None, extra_info="", show_in_ui=False: None
    app.show_toast = lambda msg, is_error=False, duration_ms=2500: None
    app._ui_after = lambda ms, fn: fn()
    app._write_file_log = lambda msg, is_error=False: None
    app.enqueue_ai_scan = lambda *args, **kwargs: None
    app._update_open_positions_and_manage = lambda *args, **kwargs: None

    return app

def main():
    parser = argparse.ArgumentParser(description="Demo Check Stage 1 MT5")
    parser.add_argument("--symbol", default="XAUUSDm", help="Symbol MT5 cần kiểm tra (mặc định: XAUUSDm)")
    args = parser.parse_args()
    symbol = args.symbol

    results = {}
    print("=" * 65)
    print("   KIỂM TRA AN TOÀN GIAI ĐOẠN 1 TRÊN TÀI KHOẢN MT5 DEMO")
    print("=" * 65)

    # 1. CHỐT AN TOÀN 1: MT5 Initialize & Tài khoản DEMO
    if not mt5.initialize():
        print(f"[ERROR] mt5.initialize() thất bại: {mt5.last_error()}")
        sys.exit(1)

    acc = mt5.account_info()
    if not acc:
        print("KHÔNG PHẢI DEMO, DỪNG (Không đọc được account_info)")
        mt5.shutdown()
        sys.exit(1)

    if getattr(acc, "trade_mode", -1) != mt5.ACCOUNT_TRADE_MODE_DEMO:
        print("KHÔNG PHẢI DEMO, DỪNG")
        mt5.shutdown()
        sys.exit(1)

    print(f"[*] Trạng thái tài khoản : DEMO")
    print(f"[*] Máy chủ kết nối      : {getattr(acc, 'server', 'Unknown')}")
    print(f"[*] Mã giao dịch         : {symbol}")

    # 2. CHỐT AN TOÀN 2: Kiểm tra thị trường mở & Symbol khả dụng
    if not mt5.symbol_select(symbol, True):
        print(f"[ERROR] Không chọn được symbol {symbol} trên MT5.")
        mt5.shutdown()
        sys.exit(1)

    sym_info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not sym_info or not tick:
        print("THỊ TRƯỜNG ĐÓNG, KHÔNG THỂ KIỂM TRA (Không lấy được tick/sym_info)")
        mt5.shutdown()
        sys.exit(1)

    now_server = time.time()
    tick_age = abs(now_server - tick.time)
    # Exness đôi khi có tick time theo GMT
    trade_allowed = (getattr(sym_info, "trade_mode", 0) == mt5.SYMBOL_TRADE_MODE_FULL)
    if not trade_allowed:
        print("THỊ TRƯỜNG ĐÓNG, KHÔNG THỂ KIỂM TRA (trade_mode không cho phép giao dịch)")
        mt5.shutdown()
        sys.exit(1)

    if tick_age > 120 and tick.ask <= 0:
        print("THỊ TRƯỜNG ĐÓNG, KHÔNG THỂ KIỂM TRA (Tick quá cũ hoặc giá không hợp lệ)")
        mt5.shutdown()
        sys.exit(1)

    v_min = getattr(sym_info, "volume_min", 0.01) or 0.01
    digits = getattr(sym_info, "digits", 2)
    is_gold = "XAU" in symbol.upper()
    sl_offset = 5.0 if is_gold else 150.0
    tp_offset = 10.0 if is_gold else 300.0

    print(f"[*] Volume tối thiểu     : {v_min}")
    print(f"[*] Giá Ask/Bid hiện tại : {tick.ask} / {tick.bid}")
    print("-" * 65)

    profile_key = "XAUUSD" if is_gold else "BTCUSD"
    bot_magic = 20260501 if is_gold else 20260502
    app = setup_stub_app(symbol, profile_key=profile_key, magic=bot_magic)

    try:
        # =========================================================================
        # PHÉP THỬ T1: Mở lệnh đầu tiên với magic bot qua execute_mt5_order
        # =========================================================================
        print("[PHÉP THỬ T1] Kiểm tra execute_mt5_order khi chưa có vị thế bot...")
        fresh_tick = mt5.symbol_info_tick(symbol)
        entry_price = fresh_tick.ask
        sl_p = round(entry_price - sl_offset, digits)
        tp_p = round(entry_price + tp_offset, digits)

        ok1, msg1 = app.execute_mt5_order(
            symbol=symbol,
            action="BUY",
            lot=v_min,
            sl_price=sl_p,
            tp_price=tp_p,
            max_positions=2,
            profile_key=profile_key
        )

        t1_ticket = None
        if ok1:
            bot_positions = app._get_bot_positions(symbol, profile_key=profile_key)
            if bot_positions:
                t1_ticket = bot_positions[0].ticket
                opened_tickets.append(t1_ticket)
            results["T1"] = (True, f"Mở lệnh bot thành công (Ticket #{t1_ticket})")
            print(f"  -> PASS: {results['T1'][1]}")
        else:
            last_err = mt5.last_error()
            results["T1"] = (False, f"Bị từ chối: {msg1} | MT5 last_error: {last_err}")
            print(f"  -> FAIL: {results['T1'][1]}")
            # Đọc file log nếu có lỗi POSITIONS_GET_ERROR
            try:
                log_file = os.path.join(ROOT_DIR, "logs", f"mt5_{symbol}.log")
                if os.path.exists(log_file):
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()[-20:]
                        for line in lines:
                            if "[POSITIONS_GET_ERROR]" in line:
                                print(f"     [LOG FOUND] {line.strip()}")
            except Exception:
                pass

        # =========================================================================
        # PHÉP THỬ T2: Kiểm tra cô lập lệnh tay (magic = 0) vs lệnh Bot
        # =========================================================================
        print("\n[PHÉP THỬ T2] Kiểm tra cô lập lệnh tay (magic = 0)...")
        t2_manual_ticket = None
        try:
            fresh_tick = mt5.symbol_info_tick(symbol)
            manual_sl = round(fresh_tick.ask - sl_offset * 3, digits)
            manual_tp = round(fresh_tick.ask + tp_offset * 3, digits)
            req_manual = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": v_min,
                "type": mt5.ORDER_TYPE_BUY,
                "price": fresh_tick.ask,
                "sl": manual_sl,
                "tp": manual_tp,
                "deviation": 50,
                "magic": 0,
                "comment": "MANUAL_TEST",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res_manual = mt5.order_send(req_manual)
            if res_manual is None or res_manual.retcode not in (10009, 10008):
                req_manual["type_filling"] = mt5.ORDER_FILLING_FOK
                res_manual = mt5.order_send(req_manual)

            if res_manual is not None and res_manual.retcode in (10009, 10008):
                t2_manual_ticket = res_manual.order
                opened_tickets.append(t2_manual_ticket)
                print(f"  + Đã mở lệnh tay giả lập ticket #{t2_manual_ticket} (magic=0)")
            else:
                raise RuntimeError(f"Không mở được lệnh tay test (retcode={getattr(res_manual, 'retcode', None)})")

            # Đợi qua debounce 3.0s của bot
            time.sleep(3.2)

            # 2a. Kiểm tra _get_bot_positions không trả lệnh tay
            bot_pos_list = app._get_bot_positions(symbol, profile_key=profile_key)
            bot_tickets = [p.ticket for p in bot_pos_list]
            manual_in_bot = t2_manual_ticket in bot_tickets

            # 2b. Kiểm tra max_positions: đặt max_positions = 1 khi đang có 1 lệnh bot -> phải từ chối mở lệnh bot tiếp theo
            # Đồng thời xác nhận nếu chỉ có lệnh tay thì bot không tính lệnh tay vào max_positions
            fresh_tick = mt5.symbol_info_tick(symbol)
            ok_limit, msg_limit = app.execute_mt5_order(
                symbol=symbol,
                action="BUY",
                lot=v_min,
                sl_price=round(fresh_tick.ask - sl_offset, digits),
                tp_price=round(fresh_tick.ask + tp_offset, digits),
                max_positions=1,
                profile_key=profile_key
            )
            blocked_by_max_pos = (not ok_limit and "Bỏ qua nhồi lệnh mới" in msg_limit)

            # 2c. Gọi _close_all_positions_for_profile: đóng lệnh bot nhưng lệnh tay VẪN CÒN
            closed_bot_cnt = app._close_all_positions_for_profile(profile_key, symbol)
            time.sleep(0.5)

            # Kiểm tra trạng thái lệnh tay
            manual_pos_check = mt5.positions_get(ticket=int(t2_manual_ticket))
            manual_still_alive = (manual_pos_check is not None and len(manual_pos_check) == 1)

            # Kiểm tra trạng thái lệnh bot
            bot_pos_after = app._get_bot_positions(symbol, profile_key=profile_key)
            bot_all_closed = (len(bot_pos_after) == 0)

            if not manual_in_bot and blocked_by_max_pos and manual_still_alive and bot_all_closed:
                results["T2"] = (True, "Lệnh tay độc lập hoàn toàn: bot không nhầm lẫn, không tính vào max_pos, không bị đóng nhầm")
                print(f"  -> PASS: {results['T2'][1]}")
            else:
                fail_reasons = []
                if manual_in_bot:
                    fail_reasons.append("_get_bot_positions lẫn lệnh tay")
                if not blocked_by_max_pos:
                    fail_reasons.append("max_positions không chặn đúng")
                if not manual_still_alive:
                    fail_reasons.append("lệnh tay bị đóng nhầm")
                if not bot_all_closed:
                    fail_reasons.append("lệnh bot chưa đóng hết")
                results["T2"] = (False, "; ".join(fail_reasons))
                print(f"  -> FAIL: {results['T2'][1]}")

        except Exception as ex:
            results["T2"] = (False, f"Lỗi ngoại lệ phép thử T2: {ex}")
            print(f"  -> FAIL: {results['T2'][1]}")

        # =========================================================================
        # PHÉP THỬ T3: Khóa lãi/lỗ ngày (_check_daily_profit_loss_limits)
        # =========================================================================
        print("\n[PHÉP THỬ T3] Kiểm tra khóa lãi/lỗ ngày và mở khóa khi đổi ngưỡng...")
        try:
            # Đợi qua debounce 3.0s của bot
            time.sleep(3.2)

            # Mở lại 1 lệnh bot để có vị thế kiểm tra khóa SL
            fresh_tick = mt5.symbol_info_tick(symbol)
            ok_bot3, msg_bot3 = app.execute_mt5_order(
                symbol=symbol,
                action="BUY",
                lot=v_min,
                sl_price=round(fresh_tick.ask - sl_offset, digits),
                tp_price=round(fresh_tick.ask + tp_offset, digits),
                max_positions=2,
                profile_key=profile_key
            )
            t3_ticket = None
            if ok_bot3:
                bot_pos3 = app._get_bot_positions(symbol, profile_key=profile_key)
                if bot_pos3:
                    t3_ticket = bot_pos3[0].ticket
                    opened_tickets.append(t3_ticket)
                print(f"  + Đã mở lệnh bot ticket #{t3_ticket} để test Daily Limit")
            else:
                raise RuntimeError(f"Không mở được lệnh bot cho T3: {msg_bot3}")

            # Lấy realized_pnl hôm nay để tính ngưỡng chạm cắt lỗ ngày chính xác:
            # total_daily_pnl = realized_pnl + floating_profit
            # Ta cần total_daily_pnl <= -daily_sl => floating_profit <= -daily_sl - realized_pnl
            realized_today, _, _ = app._get_today_deals_stats(symbol=symbol, profile_key=profile_key)
            sl_threshold = 0.05
            app.profile_cfg[profile_key]["daily_sl_usd"] = sl_threshold
            # Giả lập floating_profit sao cho total_daily_pnl = -(sl_threshold + 0.10) (chắc chắn chạm SL ngày)
            test_floating = float(-(sl_threshold + 0.10) - realized_today)

            app._check_daily_profit_loss_limits(floating_profit=test_floating, profile_key=profile_key)

            # 3a. Kiểm tra khóa đã được thiết lập & lệnh bot bị đóng
            locked_date = app.profile_ctx[profile_key].get("daily_limit_locked_date")
            is_locked = (locked_date == datetime.now().strftime("%Y-%m-%d"))
            bot_pos_after_sl = app._get_bot_positions(symbol, profile_key=profile_key)
            bot_pos_closed_by_limit = (len(bot_pos_after_sl) == 0)

            # Kiểm tra lệnh tay nếu còn thì không bị đóng
            manual_pos_alive_t3 = True
            if t2_manual_ticket:
                m_chk = mt5.positions_get(ticket=int(t2_manual_ticket))
                manual_pos_alive_t3 = (m_chk is not None and len(m_chk) == 1)

            # 3b. Gọi lại với floating = 0 và cache đã xóa -> kỳ vọng VẪN KHÓA
            app._cached_deals_by_sym.clear()
            app._cached_closed_deals_map.clear()
            app._check_daily_profit_loss_limits(floating_profit=0.0, profile_key=profile_key)
            still_locked_after_zero = app._is_daily_limit_locked(profile_key)

            # Đợi qua debounce 3.0s của bot trước khi thử execute_mt5_order
            time.sleep(3.2)

            # execute_mt5_order phải từ chối vì đã khóa
            fresh_tick = mt5.symbol_info_tick(symbol)
            ok_denied, msg_denied = app.execute_mt5_order(
                symbol=symbol,
                action="BUY",
                lot=v_min,
                sl_price=round(fresh_tick.ask - sl_offset, digits),
                tp_price=round(fresh_tick.ask + tp_offset, digits),
                max_positions=2,
                profile_key=profile_key
            )
            order_blocked_by_limit = (not ok_denied and "Đã tự động dừng giao dịch" in msg_denied)
            if not order_blocked_by_limit:
                print(f"  [DEBUG T3] ok_denied={ok_denied}, msg_denied={msg_denied}")

            # 3c. Đổi ngưỡng sang 1000$ -> kỳ vọng MỞ KHÓA
            app.profile_cfg[profile_key]["daily_sl_usd"] = 1000.0
            app._check_daily_profit_loss_limits(floating_profit=0.0, profile_key=profile_key)
            unlocked_ok = (not app._is_daily_limit_locked(profile_key))

            if is_locked and bot_pos_closed_by_limit and manual_pos_alive_t3 and still_locked_after_zero and order_blocked_by_limit and unlocked_ok:
                results["T3"] = (True, "Khóa lãi/lỗ ngày chuẩn xác: đóng lệnh bot, giữ lệnh tay, không mở khóa nhầm khi PnL đổi, mở khóa khi đổi ngưỡng")
                print(f"  -> PASS: {results['T3'][1]}")
            else:
                fail_t3 = []
                if not is_locked:
                    fail_t3.append("Không đặt khóa khi chạm SL ngày")
                if not bot_pos_closed_by_limit:
                    fail_t3.append("Lệnh bot không được đóng khi chạm SL ngày")
                if not manual_pos_alive_t3:
                    fail_t3.append("Lệnh tay bị đóng nhầm trong daily limit")
                if not still_locked_after_zero:
                    fail_t3.append("Bị mở khóa sớm khi floating = 0")
                if not order_blocked_by_limit:
                    fail_t3.append("execute_mt5_order không chặn khi đang khóa")
                if not unlocked_ok:
                    fail_t3.append("Không mở khóa sau khi đổi ngưỡng sang 1000$")
                results["T3"] = (False, "; ".join(fail_t3))
                print(f"  -> FAIL: {results['T3'][1]}")

        except Exception as ex:
            results["T3"] = (False, f"Lỗi ngoại lệ phép thử T3: {ex}")
            print(f"  -> FAIL: {results['T3'][1]}")

    finally:
        # =========================================================================
        # KHỐI DỌN DẸP AN TOÀN TUYỆT ĐỐI: CHỈ ĐÓNG CÁC TICKET DO SCRIPT NÀY MỞ
        # =========================================================================
        print("\n" + "=" * 65)
        print("   DỌN DẸP TÀI KHOẢN (CHỈ ĐÓNG CÁC TICKET CỦA SCRIPT)")
        print("=" * 65)
        closed_tickets = []
        for t in list(set(opened_tickets)):
            ok_c = safe_close_ticket(t, symbol)
            if ok_c:
                closed_tickets.append(t)
                print(f"  [CLEANUP] Đã đóng thành công Ticket #{t}")
            else:
                # Kiểm tra lại xem có còn tồn tại không
                pos_remain = mt5.positions_get(ticket=int(t))
                if not pos_remain:
                    closed_tickets.append(t)
                    print(f"  [CLEANUP] Ticket #{t} đã được đóng trước đó.")
                else:
                    print(f"  [CLEANUP WARNING] Chưa đóng được Ticket #{t}!")

        # Xác nhận không còn ticket nào của script tồn tại trên MT5
        all_remaining = mt5.positions_get() or []
        script_remaining = [p for p in all_remaining if p.ticket in opened_tickets]
        if not script_remaining:
            print("  [CLEANUP SUCCESS] Xác nhận 100%: Toàn bộ ticket của script đã được dọn sạch.")
        else:
            print(f"  [CLEANUP ERROR] Còn lại {len(script_remaining)} ticket chưa đóng: {[p.ticket for p in script_remaining]}")

        mt5.shutdown()

    # In bảng tổng kết PASS / FAIL
    print("\n" + "=" * 65)
    print("   BẢNG TỔNG HỢP KẾT QUẢ KIỂM TRA (DEMO MT5)")
    print("=" * 65)
    for test_id, (p_res, note) in results.items():
        status_str = "PASS" if p_res else "FAIL"
        print(f"  [{status_str}] {test_id}: {note}")
    print(f"  - Các ticket đã mở : {opened_tickets}")
    print(f"  - Các ticket đã đóng: {closed_tickets}")
    print("=" * 65)

if __name__ == "__main__":
    main()
