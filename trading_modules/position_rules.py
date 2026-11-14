"""
trading_modules/position_rules.py
Module QUY TẮC QUẢN LÝ LỆNH MỞ (mô phỏng): dời BE, trailing, Time-Stop, chạm SL/TP trên MỘT thanh giá.
Hàm THUẦN dùng cho backtest (trước đây chép lại 4 lần: nến/tick x BUY/SELL). Tách nguyên văn, hành vi giữ nguyên 100%.
LƯU Ý: quản lý lệnh LIVE (_update_open_positions_and_manage) vẫn là bản riêng trong bot, chưa dùng module này.
"""


def step_position(side, entry_price, sl_price, tp_price, one_r_dist, be_moved,
                  bar_high, bar_low, bar_close, hold_mins,
                  auto_be, auto_trail, be_trigger_r, trail_dist_r, be_plus_price, max_hold_mins):
    """Xử lý một thanh giá cho lệnh đang mở. side: "BUY" | "SELL".
    bar_high/bar_low/bar_close: với BUY là giá BID, với SELL là giá ASK (nến MT5 dùng giá nến chung).
    Trả (sl_price_mới, be_moved_mới, giá_thoát | None, lý_do | None)."""
    if side == "BUY":
        fav_dist = bar_high - entry_price
        if auto_be and not be_moved and fav_dist >= one_r_dist * be_trigger_r:
            sl_price = max(sl_price, entry_price + be_plus_price)
            be_moved = True

        if auto_trail and be_moved and fav_dist >= one_r_dist * trail_dist_r:
            trail_sl = bar_high - (one_r_dist * trail_dist_r)
            sl_price = max(sl_price, trail_sl)

        if max_hold_mins > 0 and hold_mins >= max_hold_mins and not be_moved:
            return sl_price, be_moved, bar_close, "Time-Stop (Hết giờ)"

        if bar_low <= sl_price:
            return sl_price, be_moved, sl_price, ("Hòa vốn (BE+)" if (be_moved and sl_price >= entry_price) else "Cắt lỗ (SL)")
        elif bar_high >= tp_price:
            return sl_price, be_moved, tp_price, "Chốt lãi (TP)"
    else:
        fav_dist = entry_price - bar_low
        if auto_be and not be_moved and fav_dist >= one_r_dist * be_trigger_r:
            sl_price = min(sl_price, entry_price - be_plus_price)
            be_moved = True

        if auto_trail and be_moved and fav_dist >= one_r_dist * trail_dist_r:
            trail_sl = bar_low + (one_r_dist * trail_dist_r)
            sl_price = min(sl_price, trail_sl)

        if max_hold_mins > 0 and hold_mins >= max_hold_mins and not be_moved:
            return sl_price, be_moved, bar_close, "Time-Stop (Hết giờ)"

        if bar_high >= sl_price:
            return sl_price, be_moved, sl_price, ("Hòa vốn (BE+)" if (be_moved and sl_price <= entry_price) else "Cắt lỗ (SL)")
        elif bar_low <= tp_price:
            return sl_price, be_moved, tp_price, "Chốt lãi (TP)"
    return sl_price, be_moved, None, None
