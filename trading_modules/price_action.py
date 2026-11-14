"""
trading_modules/price_action.py
Module PRICE ACTION: xác nhận lực nến trước khi vào lệnh (chặn bắt dao rơi / đuổi nến dựng cột).
Hàm THUẦN: nhận nến + giá, không đụng MT5/cấu hình. Tách nguyên văn từ KronosDesktopApp._check_price_action_entry_confirmation
(hành vi giữ nguyên 100%, có test golden). Ném ngoại lệ khi dữ liệu sai; bên gọi tự xử lý (pa_fail_open).
"""


def evaluate_price_action(action: str, df_recent, ask: float, bid: float, atr, scalp_tf: str, req_conf: int) -> tuple:
    """Trả (đạt: bool, lý do: str). df_recent: các nến gần nhất, nến cuối là nến đang chạy; ask/bid: giá hiện tại."""
    cur_price = ask if action == "BUY" else bid
    spread = max(ask - bid, 0.05)

    forming = df_recent.iloc[-1]
    last_closed = df_recent.iloc[-2]

    o_f, h_f, l_f, c_f = float(forming["open"]), float(forming["high"]), float(forming["low"]), float(forming["close"])
    rng_f = max(h_f - l_f, 0.05)
    body_f = abs(c_f - o_f)

    o_c, h_c, l_c, c_c = float(last_closed["open"]), float(last_closed["high"]), float(last_closed["low"]), float(last_closed["close"])
    rng_c = max(h_c - l_c, 0.05)
    body_c = abs(c_c - o_c)

    min_low_5 = float(df_recent["low"].min())
    max_high_5 = float(df_recent["high"].max())

    eff_atr = float(atr) if (atr is not None and float(atr) > 0.0) else max(rng_c, 1.5)
    min_bounce = max(0.25, spread * 1.5, 0.15 * eff_atr)
    min_drop = max(0.25, spread * 1.5, 0.15 * eff_atr)

    if action == "BUY":
        # 1. KIỂM TRA NẾN RƠI TỰ DO (RUNAWAY MARUBOZU):
        lower_wick_f = min(o_f, c_f) - l_f
        lower_wick_c = min(o_c, c_c) - l_c

        is_falling_knife_forming = (c_f < o_f) and (body_f >= 0.70 * rng_f) and (lower_wick_f < 0.15 * rng_f) and (c_f <= l_f + 0.15 * rng_f)
        is_falling_knife_closed = (c_c < o_c) and (body_c >= 0.75 * rng_c) and (lower_wick_c < 0.15 * rng_c) and (c_f < c_c)

        if is_falling_knife_forming or is_falling_knife_closed:
            return False, f"Nến {scalp_tf} đang cắm đầu rơi tự do (Thân đỏ đặc, chưa có lực đỡ)"

        # 2. XÁC NHẬN LỰC ĐỠ (BUY REJECTION / ABSORPTION):
        has_wick_rejection = (lower_wick_f >= 0.25 * rng_f and lower_wick_f >= spread * 1.5) or (lower_wick_c >= 0.35 * rng_c)
        is_bullish_turnaround = (c_f > o_f and c_f > (l_f + rng_f * 0.40)) or (c_c > o_c and c_f >= c_c)
        has_tick_bounce = (cur_price - min_low_5) >= min_bounce

        confirmations = []
        if has_wick_rejection:
            confirmations.append("Rút râu dưới")
        if is_bullish_turnaround:
            confirmations.append("Nến xanh đảo chiều")
        if has_tick_bounce:
            confirmations.append(f"Bật nảy +{cur_price - min_low_5:.2f}$ từ đáy")

        if len(confirmations) >= req_conf:
            reason = " + ".join(confirmations)
            return True, f"Xác nhận lực đỡ ({reason})"

        detail = " + ".join(confirmations) if confirmations else f"Đang cách đáy chỉ {cur_price - min_low_5:.2f}$"
        return False, f"Chưa đủ {req_conf} điều kiện PA (Đạt {len(confirmations)}/{req_conf}: {detail})"

    else:  # SELL
        upper_wick_f = h_f - max(o_f, c_f)
        upper_wick_c = h_c - max(o_c, c_c)

        is_rocketing_forming = (c_f > o_f) and (body_f >= 0.70 * rng_f) and (upper_wick_f < 0.15 * rng_f) and (c_f >= h_f - 0.15 * rng_f)
        is_rocketing_closed = (c_c > o_c) and (body_c >= 0.75 * rng_c) and (upper_wick_c < 0.15 * rng_c) and (c_f > c_c)

        if is_rocketing_forming or is_rocketing_closed:
            return False, f"Nến {scalp_tf} đang dựng cột tăng mạnh (Thân xanh đặc, chưa có lực xả)"

        has_wick_rejection = (upper_wick_f >= 0.25 * rng_f and upper_wick_f >= spread * 1.5) or (upper_wick_c >= 0.35 * rng_c)
        is_bearish_turnaround = (c_f < o_f and c_f < (h_f - rng_f * 0.40)) or (c_c < o_c and c_f <= c_c)
        has_tick_drop = (max_high_5 - cur_price) >= min_drop

        confirmations = []
        if has_wick_rejection:
            confirmations.append("Rút râu trên")
        if is_bearish_turnaround:
            confirmations.append("Nến đỏ đảo chiều")
        if has_tick_drop:
            confirmations.append(f"Giảm -{max_high_5 - cur_price:.2f}$ từ đỉnh")

        if len(confirmations) >= req_conf:
            reason = " + ".join(confirmations)
            return True, f"Xác nhận lực xả ({reason})"

        detail = " + ".join(confirmations) if confirmations else f"Đang cách đỉnh chỉ {max_high_5 - cur_price:.2f}$"
        return False, f"Chưa đủ {req_conf} điều kiện PA (Đạt {len(confirmations)}/{req_conf}: {detail})"
