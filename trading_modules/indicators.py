"""
trading_modules/indicators.py
Chỉ báo thuần: ATR (trung bình True Range của `period` nến cuối). Tách nguyên văn từ KronosDesktopApp.calculate_atr.
"""

import pandas as pd


def calculate_atr(df, period: int = 14) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return float(tr.iloc[-period:].mean())
