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

def read_positions(**kwargs):
    """Đọc positions_get an toàn. Trả (list, None) nếu đọc được (kể cả rỗng THẬT: None kèm last_error()[0]==1);
    trả (None, lỗi) nếu MT5 lỗi -> người gọi phải coi là KHÔNG XÁC ĐỊNH."""
    try:
        res = mt5.positions_get(**kwargs)
    except Exception as ex:
        return None, f"exception: {ex}"
    if res is not None:
        return list(res), None
    err = mt5.last_error()
    if isinstance(err, (tuple, list)) and len(err) > 0 and err[0] == 1:
        return [], None
    return None, err

def ticket_state(ticket):
    """'ALIVE' (còn), 'GONE' (mất thật), 'UNKNOWN' (MT5 lỗi, không kết luận được)."""
    pos, _ = read_positions(ticket=int(ticket))
    if pos is None:
        return "UNKNOWN"
    return "ALIVE" if len(pos) > 0 else "GONE"

def track(ticket):
    if ticket not in opened_tickets:
        opened_tickets.append(ticket)

def snapshot_symbol(symbol):
    """Chụp {ticket: position} của symbol; lỗi MT5 thì ném RuntimeError (KHÔNG XÁC ĐỊNH)."""
    pos, err = read_positions(symbol=symbol)
    if pos is None:
        raise RuntimeError(f"KHÔNG XÁC ĐỊNH: không đọc được positions {symbol} (last_error={err})")
    return {p.ticket: p for p in pos}

def foreign_bot_tickets(symbol, magic):
    """Ticket magic bot trên symbol KHÔNG thuộc script (None nếu MT5 lỗi)."""
    pos, _ = read_positions(symbol=symbol)
    if pos is None:
        return None
    return [p.ticket for p in pos if getattr(p, "magic", None) == magic and p.ticket not in opened_tickets]

def open_bot_tracked(app, symbol, profile_key, magic, **kwargs):
    """Gọi execute_mt5_order và ghi ticket MỚI (so tập ticket trước/sau) vào opened_tickets."""
    before = snapshot_symbol(symbol)
    ok, msg = app.execute_mt5_order(symbol=symbol, profile_key=profile_key, **kwargs)
    try:
        after = snapshot_symbol(symbol)
    except RuntimeError:
        time.sleep(1.0)
        after = snapshot_symbol(symbol)
    new = [t for t, p in after.items() if t not in before and getattr(p, "magic", None) == magic]
    for t in new:
        track(t)
    return ok, msg, new

def open_manual_tracked(symbol, volume, sl, tp, price):
    """Mở lệnh tay giả lập (magic=0), xác định ticket bằng so tập ticket trước/sau, và ghi để dọn."""
    before = snapshot_symbol(symbol)
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume, "type": mt5.ORDER_TYPE_BUY,
        "price": price, "sl": sl, "tp": tp, "deviation": 50, "magic": 0, "comment": "MANUAL_TEST",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode not in (10009, 10008):
        req["type_filling"] = mt5.ORDER_FILLING_FOK
        res = mt5.order_send(req)
    if res is None or res.retcode not in (10009, 10008):
        raise RuntimeError(f"Không mở được lệnh tay test (retcode={getattr(res, 'retcode', None)})")
    after = snapshot_symbol(symbol)
    cands = [t for t, p in after.items() if t not in before and getattr(p, "magic", None) == 0]
    if getattr(res, "order", 0) in cands:
        ticket = res.order
    elif len(cands) == 1:
        ticket = cands[0]
    else:
        raise RuntimeError(f"KHÔNG XÁC ĐỊNH: không xác định được ticket lệnh tay (ứng viên={cands})")
    track(ticket)
    return ticket

def safe_close_ticket(ticket, symbol):
    """Đóng dứt điểm một ticket cụ thể trên MT5. True chỉ khi đã đóng hoặc đã mất THẬT."""
    try:
        positions, _ = read_positions(ticket=int(ticket))
        if positions is None:
            return False  # lỗi MT5: không kết luận được
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
        "min_entry_spacing": 0.0,
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

    # 3. CHỐT AN TOÀN 3: Không được có lệnh bot nào (magic của profile) trên symbol trước khi chạy
    pre_pos, pre_err = read_positions(symbol=symbol)
    if pre_pos is None:
        print(f"KHÔNG XÁC ĐỊNH: không đọc được positions {symbol} (last_error={pre_err}), DỪNG")
        mt5.shutdown()
        sys.exit(1)
    existing_bot = [p.ticket for p in pre_pos if getattr(p, "magic", None) == bot_magic]
    if existing_bot:
        print(f"ĐANG CÓ LỆNH BOT, DỪNG (magic {bot_magic}, ticket {existing_bot}). Không gửi lệnh nào.")
        mt5.shutdown()
        sys.exit(1)

    app = setup_stub_app(symbol, profile_key=profile_key, magic=bot_magic)

    try:
        # =========================================================================
        # PHÉP THỬ T1: Mở lệnh đầu tiên với magic bot qua execute_mt5_order
        # =========================================================================
        print("[PHÉP THỬ T1] Kiểm tra execute_mt5_order khi chưa có vị thế bot...")
        t1_ticket = None
        try:
            fresh_tick = mt5.symbol_info_tick(symbol)
            entry_price = fresh_tick.ask
            sl_p = round(entry_price - sl_offset, digits)
            tp_p = round(entry_price + tp_offset, digits)
            ok1, msg1, new1 = open_bot_tracked(
                app, symbol, profile_key, bot_magic,
                action="BUY", lot=v_min, sl_price=sl_p, tp_price=tp_p, max_positions=2
            )
            if ok1 and len(new1) == 1:
                t1_ticket = new1[0]
                results["T1"] = (True, f"Mở lệnh bot thành công (Ticket #{t1_ticket})")
                print(f"  -> PASS: {results['T1'][1]}")
            elif ok1:
                results["T1"] = (False, f"KHÔNG XÁC ĐỊNH: lệnh báo thành công nhưng ticket mới = {new1}")
                print(f"  -> FAIL: {results['T1'][1]}")
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
        except Exception as ex:
            results["T1"] = (False, f"Lỗi ngoại lệ phép thử T1: {ex}")
            print(f"  -> FAIL: {results['T1'][1]}")

        # =========================================================================
        # PHÉP THỬ T2: Kiểm tra cô lập lệnh tay (magic = 0) vs lệnh Bot
        # =========================================================================
        print("\n[PHÉP THỬ T2] Kiểm tra cô lập lệnh tay (magic = 0)...")
        t2_manual_ticket = None
        try:
            if t1_ticket is None:
                raise RuntimeError("T1 không cung cấp ticket lệnh bot -> không thể chạy T2")
            unknown = []
            fails = []

            fresh_tick = mt5.symbol_info_tick(symbol)
            t2_manual_ticket = open_manual_tracked(
                symbol, v_min,
                round(fresh_tick.ask - sl_offset * 3, digits),
                round(fresh_tick.ask + tp_offset * 3, digits),
                fresh_tick.ask
            )
            print(f"  + Đã mở lệnh tay giả lập ticket #{t2_manual_ticket} (magic=0)")

            # Đợi qua debounce 3.0s của bot
            time.sleep(3.2)

            # 2a. _get_bot_positions phải THẤY lệnh bot T1 (chứng minh phép đo không rỗng do lỗi) và KHÔNG thấy lệnh tay
            bot_tickets = [p.ticket for p in app._get_bot_positions(symbol, profile_key=profile_key)]
            if t1_ticket not in bot_tickets:
                fails.append("lệnh bot T1 không có trong _get_bot_positions (phép đo không đáng tin)")
            if t2_manual_ticket in bot_tickets:
                fails.append("_get_bot_positions lẫn lệnh tay")

            # 2b-1. max_positions=1 khi đã có 1 lệnh bot chưa BE -> phải từ chối
            fresh_tick = mt5.symbol_info_tick(symbol)
            ok_limit, msg_limit, _ = open_bot_tracked(
                app, symbol, profile_key, bot_magic,
                action="BUY", lot=v_min,
                sl_price=round(fresh_tick.ask - sl_offset, digits),
                tp_price=round(fresh_tick.ask + tp_offset, digits),
                max_positions=1
            )
            if not (not ok_limit and "Bỏ qua nhồi lệnh mới" in msg_limit):
                fails.append("max_positions=1 không chặn đúng")

            # 2b-2. Lệnh tay KHÔNG được đếm vào max_pos: 1 bot (chưa BE) + 1 tay, max_positions=2, HƯỚNG NGƯỢC
            #       -> phải được mở. Nếu lệnh tay bị đếm (2 >= 2) thì sẽ bị chặn.
            alive_now = []
            for t in opened_tickets:
                st = ticket_state(t)
                if st == "UNKNOWN":
                    unknown.append(f"không xác định trạng thái #{t} trước phép thử đếm max_pos")
                elif st == "ALIVE":
                    alive_now.append(t)
            if len(alive_now) >= 3:
                fails.append(f"đã có {len(alive_now)} lệnh của script đang mở (giới hạn 3) -> bỏ qua phép thử đếm max_pos")
            else:
                time.sleep(3.2)
                fresh_tick = mt5.symbol_info_tick(symbol)
                ok_opp, msg_opp, new_opp = open_bot_tracked(
                    app, symbol, profile_key, bot_magic,
                    action="SELL", lot=v_min,
                    sl_price=round(fresh_tick.bid + sl_offset, digits),
                    tp_price=round(fresh_tick.bid - tp_offset * 1.5, digits),
                    max_positions=2
                )
                if not ok_opp:
                    if "Bỏ qua nhồi lệnh mới" in msg_opp:
                        fails.append("lệnh tay bị đếm vào max_pos (SELL ngược hướng bị chặn)")
                    else:
                        fails.append(f"SELL ngược hướng bị từ chối vì lý do khác: {msg_opp}")
                elif len(new_opp) != 1:
                    fails.append(f"SELL báo thành công nhưng ticket mới không xác định ({new_opp})")

            # 2c. _close_all_positions_for_profile: đóng lệnh bot nhưng lệnh tay VẪN CÒN
            #     (chỉ gọi khi không có lệnh bot lạ ngoài các ticket của script)
            foreign = foreign_bot_tickets(symbol, bot_magic)
            if foreign is None:
                unknown.append("không đọc được positions trước khi đóng hàng loạt")
            elif foreign:
                fails.append(f"có lệnh bot lạ {foreign} -> BỎ QUA đóng hàng loạt để không đóng nhầm")
            else:
                app._close_all_positions_for_profile(profile_key, symbol)
                time.sleep(0.5)

                st_manual = ticket_state(t2_manual_ticket)
                if st_manual == "UNKNOWN":
                    unknown.append(f"không xác định trạng thái lệnh tay #{t2_manual_ticket}")
                elif st_manual != "ALIVE":
                    fails.append("lệnh tay bị đóng nhầm")
                for t in [x for x in opened_tickets if x != t2_manual_ticket]:
                    st = ticket_state(t)
                    if st == "UNKNOWN":
                        unknown.append(f"không xác định trạng thái lệnh bot #{t}")
                    elif st == "ALIVE":
                        fails.append(f"lệnh bot #{t} chưa đóng")

            if unknown:
                results["T2"] = (False, "KHÔNG XÁC ĐỊNH: " + "; ".join(unknown + fails))
                print(f"  -> FAIL: {results['T2'][1]}")
            elif fails:
                results["T2"] = (False, "; ".join(fails))
                print(f"  -> FAIL: {results['T2'][1]}")
            else:
                results["T2"] = (True, "Lệnh tay độc lập hoàn toàn: bot không nhầm lẫn, không tính vào max_pos, không bị đóng nhầm")
                print(f"  -> PASS: {results['T2'][1]}")

        except Exception as ex:
            results["T2"] = (False, f"Lỗi ngoại lệ phép thử T2: {ex}")
            print(f"  -> FAIL: {results['T2'][1]}")

        # =========================================================================
        # PHÉP THỬ T3: Khóa lãi/lỗ ngày (_check_daily_profit_loss_limits)
        # =========================================================================
        print("\n[PHÉP THỬ T3] Kiểm tra khóa lãi/lỗ ngày và mở khóa khi đổi ngưỡng...")
        try:
            unknown = []
            fails = []
            # Đợi qua debounce 3.0s của bot
            time.sleep(3.2)

            # Daily limit sẽ đóng MỌI lệnh bot của symbol -> phải chắc chắn không có lệnh bot lạ
            foreign = foreign_bot_tickets(symbol, bot_magic)
            if foreign is None:
                raise RuntimeError("KHÔNG XÁC ĐỊNH: không đọc được positions trước T3")
            if foreign:
                raise RuntimeError(f"Có lệnh bot lạ {foreign}, BỎ QUA T3 để daily limit không đóng nhầm")

            # Mở lại 1 lệnh bot để có vị thế kiểm tra khóa SL
            fresh_tick = mt5.symbol_info_tick(symbol)
            ok_bot3, msg_bot3, new3 = open_bot_tracked(
                app, symbol, profile_key, bot_magic,
                action="BUY", lot=v_min,
                sl_price=round(fresh_tick.ask - sl_offset, digits),
                tp_price=round(fresh_tick.ask + tp_offset, digits),
                max_positions=2
            )
            if not ok_bot3:
                raise RuntimeError(f"Không mở được lệnh bot cho T3: {msg_bot3}")
            if len(new3) != 1:
                raise RuntimeError(f"KHÔNG XÁC ĐỊNH: lệnh bot T3 báo thành công nhưng ticket mới = {new3}")
            t3_ticket = new3[0]
            print(f"  + Đã mở lệnh bot ticket #{t3_ticket} để test Daily Limit")

            cfg3 = app.profile_cfg[profile_key]
            cfg3["daily_tp_usd"] = 0.0
            # total_daily_pnl = realized_pnl + floating_profit; cần total <= -daily_sl
            realized_today, _, _ = app._get_today_deals_stats(symbol=symbol, profile_key=profile_key)
            sl_threshold = 0.05
            cfg3["daily_sl_usd"] = sl_threshold
            # Giả lập floating_profit sao cho total_daily_pnl = -(sl_threshold + 0.10) (chắc chắn chạm SL ngày)
            test_floating = float(-(sl_threshold + 0.10) - realized_today)

            app._check_daily_profit_loss_limits(floating_profit=test_floating, profile_key=profile_key)

            # 3a. Khóa đã được thiết lập & lệnh bot T3 đã MẤT THẬT (đo theo ticket)
            locked_date = app.profile_ctx[profile_key].get("daily_limit_locked_date")
            is_locked = (locked_date == datetime.now(tz=bot_mod.VN_TZ).strftime("%Y-%m-%d"))
            st_t3 = ticket_state(t3_ticket)
            if st_t3 == "UNKNOWN":
                unknown.append(f"không xác định trạng thái lệnh bot #{t3_ticket} sau khi chạm SL ngày")

            # Lệnh tay (nếu còn từ T2) không được bị đóng nhầm
            manual_pos_alive_t3 = True
            if t2_manual_ticket:
                st_m = ticket_state(t2_manual_ticket)
                if st_m == "UNKNOWN":
                    unknown.append(f"không xác định trạng thái lệnh tay #{t2_manual_ticket}")
                manual_pos_alive_t3 = (st_m == "ALIVE")

            # 3b. PnL ngày phục hồi rõ ràng cao hơn -daily_sl (floating +1000$), daily_tp=0
            #     -> kỳ vọng VẪN KHÓA (chỉ đổi ngưỡng mới được mở khóa). Logic lỗi cũ sẽ mở khóa ở bước này.
            app._cached_deals_by_sym.clear()
            app._cached_closed_deals_map.clear()
            big_float = 1000.0
            realized_after, _, _ = app._get_today_deals_stats(symbol=symbol, profile_key=profile_key)
            premise_3b = (realized_after + big_float) > (-sl_threshold + 1.0)
            if not premise_3b:
                unknown.append(f"tiền đề 3b không thỏa (realized hôm nay {realized_after:.2f}$ quá âm)")
            app._check_daily_profit_loss_limits(floating_profit=big_float, profile_key=profile_key)
            still_locked_after_pos = app._is_daily_limit_locked(profile_key)

            # Đợi qua debounce 3.0s của bot trước khi thử execute_mt5_order
            time.sleep(3.2)

            # execute_mt5_order phải từ chối vì đã khóa
            fresh_tick = mt5.symbol_info_tick(symbol)
            ok_denied, msg_denied, _ = open_bot_tracked(
                app, symbol, profile_key, bot_magic,
                action="BUY", lot=v_min,
                sl_price=round(fresh_tick.ask - sl_offset, digits),
                tp_price=round(fresh_tick.ask + tp_offset, digits),
                max_positions=2
            )
            order_blocked_by_limit = (not ok_denied and "Đã tự động dừng giao dịch" in msg_denied)
            if not order_blocked_by_limit:
                print(f"  [DEBUG T3] ok_denied={ok_denied}, msg_denied={msg_denied}")

            # 3c. Đổi ngưỡng sang 1000$ -> kỳ vọng MỞ KHÓA
            cfg3["daily_sl_usd"] = 1000.0
            app._check_daily_profit_loss_limits(floating_profit=0.0, profile_key=profile_key)
            unlocked_ok = (not app._is_daily_limit_locked(profile_key))

            if not is_locked:
                fails.append("Không đặt khóa khi chạm SL ngày")
            if st_t3 == "ALIVE":
                fails.append("Lệnh bot không được đóng khi chạm SL ngày")
            if not manual_pos_alive_t3 and not any("lệnh tay" in u for u in unknown):
                fails.append("Lệnh tay bị đóng nhầm trong daily limit")
            if premise_3b and not still_locked_after_pos:
                fails.append("Bị mở khóa sớm khi PnL ngày phục hồi (+1000$) mà chưa đổi ngưỡng")
            if not order_blocked_by_limit:
                fails.append("execute_mt5_order không chặn khi đang khóa")
            if not unlocked_ok:
                fails.append("Không mở khóa sau khi đổi ngưỡng sang 1000$")

            if unknown:
                results["T3"] = (False, "KHÔNG XÁC ĐỊNH: " + "; ".join(unknown + fails))
                print(f"  -> FAIL: {results['T3'][1]}")
            elif fails:
                results["T3"] = (False, "; ".join(fails))
                print(f"  -> FAIL: {results['T3'][1]}")
            else:
                results["T3"] = (True, "Khóa lãi/lỗ ngày chuẩn xác: đóng lệnh bot, giữ lệnh tay, không mở khóa nhầm khi PnL phục hồi, mở khóa khi đổi ngưỡng")
                print(f"  -> PASS: {results['T3'][1]}")

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
                # Kiểm tra lại: chỉ coi là đã mất khi MT5 báo rỗng THẬT (lỗi MT5 = không xác định)
                st_c = ticket_state(t)
                if st_c == "GONE":
                    closed_tickets.append(t)
                    print(f"  [CLEANUP] Ticket #{t} đã được đóng trước đó.")
                elif st_c == "UNKNOWN":
                    print(f"  [CLEANUP WARNING] Không xác định được trạng thái Ticket #{t} (lỗi MT5)!")
                else:
                    print(f"  [CLEANUP WARNING] Chưa đóng được Ticket #{t}!")

        # Xác nhận không còn ticket nào của script tồn tại trên MT5
        all_remaining, rem_err = read_positions(symbol=symbol)
        if all_remaining is None:
            script_remaining = None
            print(f"  [CLEANUP UNKNOWN] Không đọc được positions {symbol} để xác nhận (last_error={rem_err}).")
        else:
            script_remaining = [p for p in all_remaining if p.ticket in opened_tickets]
        if script_remaining is None:
            pass
        elif not script_remaining:
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
