"""
trading_modules/tick_resample.py
Module GHÉP TICK -> NẾN 1 GIÂY (S1) cho backtest chế độ tick. Hàm thuần NumPy; tách nguyên văn từ KronosDesktopApp._resample_ticks_to_s1.
"""

import numpy as np


def resample_ticks_to_s1(ticks):
    """Chuyển đổi mảng tick MT5 thành các thanh nến 1 giây (S1) siêu tốc bằng NumPy:
    Mỗi giây trả về tuple (time_sec, o_b, h_b, l_b, c_b, h_a, l_a, c_a, spread)
    """
    if ticks is None or len(ticks) == 0:
        return []

    times = ticks["time"]
    bids = ticks["bid"]
    asks = ticks["ask"]

    unique_times, idx_start = np.unique(times, return_index=True)
    idx_end = np.append(idx_start[1:], len(ticks))

    opens_bid = bids[idx_start]
    highs_bid = np.maximum.reduceat(bids, idx_start)
    lows_bid = np.minimum.reduceat(bids, idx_start)
    closes_bid = bids[idx_end - 1]

    highs_ask = np.maximum.reduceat(asks, idx_start)
    lows_ask = np.minimum.reduceat(asks, idx_start)
    closes_ask = asks[idx_end - 1]

    s1_bars = []
    for i in range(len(unique_times)):
        t_sec = int(unique_times[i])
        o_b = float(opens_bid[i])
        h_b = float(highs_bid[i])
        l_b = float(lows_bid[i])
        c_b = float(closes_bid[i])
        h_a = float(highs_ask[i])
        l_a = float(lows_ask[i])
        c_a = float(closes_ask[i])
        spread = max(c_a - c_b, 0.0)
        s1_bars.append((t_sec, o_b, h_b, l_b, c_b, h_a, l_a, c_a, spread))

    return s1_bars
