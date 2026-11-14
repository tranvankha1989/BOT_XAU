"""
trading_modules/symbol_profile.py
Module NGƯỠNG THEO MÃ GIAO DỊCH: bộ ngưỡng đệm kỹ thuật co giãn theo mã (vàng vs BTC) và spread/stops level hiện tại.
Hàm THUẦN (spread, stops_dist do bên gọi đọc từ MT5). Tách nguyên văn từ KronosDesktopApp._get_symbol_tolerances.
"""


def symbol_tolerances(symbol, spread: float, stops_dist: float) -> dict:
    """Trả dict ngưỡng: is_btc, eps_equal, min_stop_dist, min_trail_step, min_manual_be_dist, sl_break_tol, max_sl_tol,
    min_candle_body, min_atr_sl_floor, min_tp_floor."""
    sym_up = str(symbol or "").upper()
    is_btc = "BTC" in sym_up
    if is_btc:
        return {
            "is_btc": True,
            "eps_equal": 1.50,
            "min_stop_dist": max(stops_dist * 1.2, spread * 1.2, 8.0),
            "min_trail_step": max(spread * 0.8, 5.0),
            "min_manual_be_dist": max(spread * 1.0, 6.0),
            "sl_break_tol": max(spread * 0.5, 3.0),
            "max_sl_tol": 1.0,
            "min_candle_body": 2.0,
            "min_atr_sl_floor": 20.0,
            "min_tp_floor": 30.0,
        }
    return {
        "is_btc": False,
        "eps_equal": 0.05,
        "min_stop_dist": max(stops_dist * 1.2, spread * 1.2, 0.50),
        "min_trail_step": 0.40,
        "min_manual_be_dist": 0.30,
        "sl_break_tol": 0.25,
        "max_sl_tol": 0.05,
        "min_candle_body": 0.05,
        "min_atr_sl_floor": 1.0,
        "min_tp_floor": 1.5,
    }
