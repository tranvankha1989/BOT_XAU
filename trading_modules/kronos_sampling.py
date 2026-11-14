"""
trading_modules/kronos_sampling.py
Module THAM SỐ LẤY MẪU KRONOS: nhiệt độ + top-p thích ứng theo khung và chế độ biến động (ATR nhanh/ATR chậm).
Hàm thuần; tách nguyên văn từ KronosDesktopApp._compute_adaptive_sampling_params. LƯU Ý: kết quả đi vào đầu vào của Kronos
(khóa cache backtest có nhiệt độ/top_p) nên mọi thay đổi ở đây phải qua test golden.
"""

from trading_modules.indicators import calculate_atr


def compute_adaptive_sampling_params(df_ai, analysis_tf: str) -> tuple:
    """Tự động điều chỉnh Nhiệt độ suy luận (Temperature) và Nucleus Sampling (Top-P)
    tối ưu riêng cho Scalping M1/M5 và trạng thái biến động thực tế (Volatility Regime):
    - M1/M5: Hạ nhiệt độ xuống 0.58 - 0.65 để triệt tiêu nhiễu râu nến ngắn hạn, hội tụ vào xung lực dòng tiền.
    - Khi biến động bung mạnh (ATR ngắn hạn > 1.35x ATR dài hạn): Siết thêm T để khóa chặt xu hướng chính, tránh phân tán ảo.
    """
    tf_upper = str(analysis_tf).upper().strip()
    base_map = {
        "M1": (0.58, 0.80),
        "M5": (0.65, 0.84),
        "M15": (0.72, 0.86),
        "H1": (0.78, 0.88),
    }
    base_t, base_top_p = base_map.get(tf_upper, (0.65, 0.84))
    regime_tag = f"{tf_upper}-Scalp"

    try:
        if len(df_ai) >= 30:
            atr_fast = calculate_atr(df_ai.iloc[-10:], 7)
            atr_slow = calculate_atr(df_ai.iloc[-35:], 14)
            vol_ratio = atr_fast / max(atr_slow, 1e-5)
            if vol_ratio >= 1.35:
                base_t = max(0.48, round(base_t - 0.06, 2))
                base_top_p = max(0.75, round(base_top_p - 0.04, 2))
                regime_tag = f"{tf_upper}-Impulse"
            elif vol_ratio <= 0.75:
                base_t = min(0.82, round(base_t + 0.04, 2))
                base_top_p = min(0.90, round(base_top_p + 0.02, 2))
                regime_tag = f"{tf_upper}-Range"
    except Exception:
        pass
    return float(base_t), float(base_top_p), regime_tag
