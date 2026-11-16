"""
trading_modules/structure.py
Module CẤU TRÚC THỊ TRƯỜNG (BOS/CHoCH/swing) + Order-flow proxy + Quét râu. Hàm THUẦN: chỉ nhận nến, không đụng MT5/giao diện/cấu hình.
Tách nguyên văn từ KronosDesktopApp._analyze_scalping_micro_macro_context (hành vi giữ nguyên 100%, có test golden).
"""

import numpy as np


def analyze_structure_context(df_ana, df_scalp, atr: float, conflict_mode: str = "neutral") -> dict:
    """
    Trích xuất Cấu Trúc Thị Trường Không Độ Trễ (Swing Highs/Lows, BOS & CHoCH) kết hợp xung lực nến đóng cửa & áp lực
    Order-Flow Volume Delta. conflict_mode ("neutral" | "analysis" | "scalp"): xử lý khi khung scalp và khung phân tích ngược chiều.
    Trả dict: macro_dir, orderflow_dir, wick_dir, summary_tag, structure_reason, break_type, timeframe_conflict, scalp_dir, ana_dir.
    """
    ctx_info = {
        "macro_dir": 0,       # +1: Cấu trúc Tăng, -1: Cấu trúc Giảm, 0: Đi ngang/Tích lũy/Xung đột
        "orderflow_dir": 0,   # +1: Mua chủ động, -1: Bán chủ động
        "wick_dir": 0,        # +1: Rút chân dưới (Buy Sweep), -1: Rút chân trên (Sell Sweep)
        "summary_tag": "Cân bằng",
        "structure_reason": "Cân bằng / Tích lũy",
        "break_type": "NONE", # "BOS" | "CHOCH" | "SWING" | "NONE"
        "timeframe_conflict": False,
        "scalp_dir": 0,
        "ana_dir": 0,
    }
    try:
        conflict_mode = "neutral" if conflict_mode is None else str(conflict_mode).strip().lower()

        def _extract_structure(df_in):
            if df_in is None or len(df_in) < 15:
                return 0, "Cân bằng", "NONE"
            h_v = df_in["high"].astype(float).values
            l_v = df_in["low"].astype(float).values
            c_v = df_in["close"].astype(float).values
            o_v = df_in["open"].astype(float).values
            n_b = len(df_in)

            p_h = []
            p_l = []
            for i in range(2, n_b - 2):
                if h_v[i] > h_v[i-1] and h_v[i] > h_v[i-2] and h_v[i] > h_v[i+1] and h_v[i] > h_v[i+2]:
                    p_h.append(h_v[i])
                if l_v[i] < l_v[i-1] and l_v[i] < l_v[i-2] and l_v[i] < l_v[i+1] and l_v[i] < l_v[i+2]:
                    p_l.append(l_v[i])

            cur_c = c_v[-1]
            dir_val = 0
            reas = "Cân bằng"
            b_type = "NONE"

            if len(p_h) >= 2 and len(p_l) >= 2:
                lh = p_h[-1] < p_h[-2]
                ll = p_l[-1] < p_l[-2]
                hh = p_h[-1] > p_h[-2]
                hl = p_l[-1] > p_l[-2]

                prev_trend = -1 if (lh and ll) else (1 if (hh and hl) else 0)

                if cur_c > p_h[-1]:
                    dir_val = 1
                    b_type = "CHOCH" if prev_trend == -1 else "BOS"
                    reas = f"{b_type} Tăng (Giá {cur_c:.2f} > Đỉnh {p_h[-1]:.2f})"
                elif cur_c < p_l[-1]:
                    dir_val = -1
                    b_type = "CHOCH" if prev_trend == 1 else "BOS"
                    reas = f"{b_type} Giảm (Giá {cur_c:.2f} < Đáy {p_l[-1]:.2f})"
                elif lh and ll:
                    dir_val = -1
                    b_type = "SWING"
                    reas = f"Cấu trúc Giảm LH+LL (Đỉnh {p_h[-1]:.2f} < {p_h[-2]:.2f})"
                elif hh and hl:
                    dir_val = 1
                    b_type = "SWING"
                    reas = f"Cấu trúc Tăng HH+HL (Đáy {p_l[-1]:.2f} > {p_l[-2]:.2f})"

            # Xung lực 10 nến gần nhất: KHÔNG được ghi đè BOS/CHoCH vô điều kiện
            # Chỉ dùng khi dir_val == 0 (chưa rõ cấu trúc) hoặc khi cùng chiều với cấu trúc
            recent_c = c_v[-10:]
            recent_o = o_v[-10:]
            red_c = int(np.sum(recent_c < recent_o))
            green_c = int(np.sum(recent_c > recent_o))
            local_min = float(np.min(l_v[-10:]))
            local_max = float(np.max(h_v[-10:]))

            if red_c >= 7 and cur_c <= local_min + (atr * 0.25):
                if dir_val == 0 or dir_val == -1:
                    dir_val = -1
                    reas = f"Đổ đèo tự do ({red_c}/10 nến đỏ sát đáy {local_min:.2f})"
                    if b_type == "NONE":
                        b_type = "SWING"
            elif green_c >= 7 and cur_c >= local_max - (atr * 0.25):
                if dir_val == 0 or dir_val == 1:
                    dir_val = 1
                    reas = f"Tăng dốc đứng ({green_c}/10 nến xanh sát đỉnh {local_max:.2f})"
                    if b_type == "NONE":
                        b_type = "SWING"

            return dir_val, reas, b_type

        # 1. Trích xuất cấu trúc không độ trễ trên cả 2 khung Scalping (M1/M5) và Phân tích (M15)
        s_dir, s_reas, s_type = _extract_structure(df_scalp)
        a_dir, a_reas, a_type = _extract_structure(df_ana)
        ctx_info["scalp_dir"] = s_dir
        ctx_info["ana_dir"] = a_dir

        # 2. Xử lý xung đột khung Scalp vs Phân tích M15 theo cấu hình structure_conflict_mode
        if s_dir != 0 and a_dir != 0 and (s_dir * a_dir == -1):
            ctx_info["timeframe_conflict"] = True
            if conflict_mode == "neutral":
                ctx_info["macro_dir"] = 0
                ctx_info["break_type"] = "NONE"
                ctx_info["structure_reason"] = f"Xung đột đa khung (Scalp {s_dir:+d} vs Phân tích {a_dir:+d}) -> Trung lập"
            elif conflict_mode == "analysis":
                ctx_info["macro_dir"] = a_dir
                ctx_info["break_type"] = a_type
                ctx_info["structure_reason"] = f"Xung đột đa khung (Ưu tiên Phân tích: {a_reas})"
            else:  # "scalp"
                ctx_info["macro_dir"] = s_dir
                ctx_info["break_type"] = s_type
                ctx_info["structure_reason"] = f"Xung đột đa khung (Ưu tiên Scalp: {s_reas})"
        else:
            ctx_info["timeframe_conflict"] = False
            if s_dir != 0:
                ctx_info["macro_dir"] = s_dir
                ctx_info["break_type"] = s_type
                ctx_info["structure_reason"] = s_reas
            elif a_dir != 0:
                ctx_info["macro_dir"] = a_dir
                ctx_info["break_type"] = a_type
                ctx_info["structure_reason"] = a_reas
            else:
                ctx_info["macro_dir"] = 0
                ctx_info["break_type"] = "NONE"
                ctx_info["structure_reason"] = "Cân bằng / Tích lũy"

        ref_df = df_scalp if (df_scalp is not None and len(df_scalp) >= 12) else df_ana
        recent = ref_df.iloc[-12:]
        h_v = recent["high"].astype(float).values
        l_v = recent["low"].astype(float).values
        o_v = recent["open"].astype(float).values
        c_v = recent["close"].astype(float).values
        v_v = recent["volume"].astype(float).values
        rng = np.maximum(h_v - l_v, 1e-5)

        # Volume Delta Proxy: Tỷ trọng đóng cửa kết hợp thân nến nhân với Tick Volume
        clv = ((c_v - l_v) - (h_v - c_v)) / rng
        body_pressure = (c_v - o_v) / rng
        signed_vol = v_v * (0.6 * clv + 0.4 * body_pressure)
        vol_sum = float(np.sum(v_v)) + 1e-5
        norm_delta = float(np.sum(signed_vol)) / vol_sum
        if norm_delta >= 0.12:
            ctx_info["orderflow_dir"] = 1
        elif norm_delta <= -0.12:
            ctx_info["orderflow_dir"] = -1

        # Phát hiện Quét râu thanh khoản (Liquidity Sweep / Pinbar Rejection) trên 2 nến gần nhất
        last_2 = recent.iloc[-2:]
        for _, row in last_2.iterrows():
            ro, rh, rl, rc = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            r_len = max(rh - rl, 1e-5)
            if r_len >= 0.45 * atr:
                lower_wick = min(ro, rc) - rl
                upper_wick = rh - max(ro, rc)
                if lower_wick >= 0.52 * r_len and lower_wick > upper_wick * 1.6:
                    ctx_info["wick_dir"] = 1
                elif upper_wick >= 0.52 * r_len and upper_wick > lower_wick * 1.6:
                    ctx_info["wick_dir"] = -1

        tags = []
        if ctx_info["macro_dir"] != 0:
            tags.append("CấuTrúc:Tăng" if ctx_info["macro_dir"] > 0 else "CấuTrúc:Giảm")
        if ctx_info["orderflow_dir"] != 0:
            tags.append("Vol:Mua" if ctx_info["orderflow_dir"] > 0 else "Vol:Bán")
        if ctx_info["wick_dir"] != 0:
            tags.append("RútChân:Dưới" if ctx_info["wick_dir"] > 0 else "RútChân:Trên")
        if tags:
            ctx_info["summary_tag"] = " | ".join(tags)
    except Exception:
        pass
    return ctx_info

def nearest_swing_dist(df, side: str, ref_price: float, lookback: int = 100):
    """Khoảng cách từ ref_price tới đáy swing gần nhất DƯỚI giá (side=BUY) hoặc đỉnh swing gần nhất TRÊN giá (side=SELL).
    Swing = fractal 2 nến mỗi bên, chỉ dùng nến đã đóng trong `lookback` nến cuối. Trả None nếu không có swing hợp lệ (bên gọi dùng SL cũ)."""
    try:
        if df is None or len(df) < 7:
            return None
        sub = df.iloc[-lookback:]
        h = sub["high"].astype(float).values
        l = sub["low"].astype(float).values
        best = None
        for i in range(2, len(sub) - 2):
            if side == "BUY":
                if l[i] < min(l[i-2], l[i-1], l[i+1], l[i+2]) and l[i] < ref_price:
                    d = ref_price - l[i]
                    best = d if best is None else min(best, d)
            else:
                if h[i] > max(h[i-2], h[i-1], h[i+1], h[i+2]) and h[i] > ref_price:
                    d = h[i] - ref_price
                    best = d if best is None else min(best, d)
        return best
    except Exception:
        return None


def heikin_ashi_colors(opens, highs, lows, closes):
    """Màu nến Heikin Ashi (+1 xanh, -1 đỏ, 0 doji) cho từng nến, chỉ dùng dữ liệu quá khứ (nhân quả)."""
    o = [float(x) for x in opens]; h = [float(x) for x in highs]
    l = [float(x) for x in lows]; c = [float(x) for x in closes]
    out = []
    ha_o = None
    for i in range(len(c)):
        ha_c = (o[i] + h[i] + l[i] + c[i]) / 4.0
        ha_o = (o[i] + c[i]) / 2.0 if ha_o is None else (ha_o + prev_c) / 2.0
        prev_c = ha_c
        out.append(1 if ha_c > ha_o else (-1 if ha_c < ha_o else 0))
    return out
