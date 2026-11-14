"""
trading_modules/risk.py
Module RỦI RO / KẾ HOẠCH LỆNH: tính Max SL hiệu lực của vùng canh. Hàm thuần; tách nguyên văn từ mt5_scalping_bot.py.
"""


def calc_effective_max_sl(action: str, entry_ref: float, sl_price: float, tp_price: float, max_sl_price: float):
    """Max SL hiệu lực của vùng canh (live). Trả (effective_max_sl, da_chia_doi).
    Kiểm tra RR >= 2R tính tại điểm vào DỰ KIẾN (SL ± Max SL khi SL AI xa hơn Max SL), KHÔNG tính tại giá hiện tại:
    TP đã dựng từ điểm vào dự kiến nên đo từ giá hiện tại (xa hơn) luôn cho RR thấp giả -> chia đôi Max SL vô cớ, vùng canh quá sâu."""
    cur_sl_dist = abs(entry_ref - sl_price)
    if action not in ("BUY", "SELL") or cur_sl_dist <= 0.001:
        return max_sl_price, False
    ref = entry_ref
    if max_sl_price > 0.0 and cur_sl_dist > max_sl_price:
        ref = (sl_price + max_sl_price) if action == "BUY" else (sl_price - max_sl_price)
    sl_d = abs(ref - sl_price)
    dist_tp = abs(tp_price - ref)
    if dist_tp < 2.0 * sl_d:
        base_sl_cap = max_sl_price if max_sl_price > 0.0 else cur_sl_dist
        return round(base_sl_cap / 2.0, 2), True
    return max_sl_price, False
