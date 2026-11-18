"""Nhận định cấu trúc giá bằng đỉnh/đáy xoay (swing), KHÔNG dùng AI.
Quy ước: đầu vào là các nến ĐÃ ĐÓNG (nến cuối cùng là nến vừa đóng). Đỉnh/đáy chỉ được xác nhận sau khi có đủ L nến bên phải.

Quy tắc chọn chiều:
- Xu hướng GIẢM (đỉnh hồi và đáy đều thấp dần):
  - SELL khi giá hồi lên chạm vùng kháng cự (đỉnh hồi gần nhất) rồi bị từ chối bằng nến giảm.
  - BUY khi đóng cửa vượt đỉnh hồi gần nhất + dung sai (đổi cấu trúc).
- Xu hướng TĂNG (đỉnh và đáy đều cao dần):
  - BUY khi giá hồi xuống chạm hỗ trợ (đáy hồi gần nhất) rồi giữ bằng nến tăng.
  - SELL khi đóng cửa thủng đáy hồi gần nhất - dung sai (đổi cấu trúc).
- Đi ngang / chưa đủ đỉnh đáy: không có tín hiệu (signal = None).
"""

import numpy as np


def _pivots(highs: np.ndarray, lows: np.ndarray, L: int):
    """Trả về (đỉnh xoay, đáy xoay) dạng [(chỉ số, giá)], chỉ gồm các điểm đã được xác nhận."""
    sh, sl = [], []
    n = len(highs)
    for i in range(L, n - L):
        left_h, right_h = highs[i - L:i], highs[i + 1:i + L + 1]
        if highs[i] > left_h.max() and highs[i] >= right_h.max():
            sh.append((i, float(highs[i])))
        left_l, right_l = lows[i - L:i], lows[i + 1:i + L + 1]
        if lows[i] < left_l.min() and lows[i] <= right_l.min():
            sl.append((i, float(lows[i])))
    return sh, sl


def analyze_swing_structure(opens, highs, lows, closes, atr: float, L: int = 2, tol_atr: float = 0.3) -> dict:
    """Nhận định cấu trúc trên chuỗi nến đã đóng. Trả dict:
    trend ('up'|'down'|'range'), signal ('BUY'|'SELL'|None), reason (lý do tiếng Việt),
    resistance (đỉnh xoay gần nhất), support (đáy xoay gần nhất), tol (dung sai giá)."""
    o = np.asarray(opens, dtype=float)
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    out = {"trend": "range", "signal": None, "reason": "", "resistance": None, "support": None, "tol": None}
    n = len(c)
    if n < 2 * L + 5 or not (atr and atr > 0):
        out["reason"] = "thiếu dữ liệu nến hoặc ATR"
        return out
    tol = max(float(atr) * tol_atr, 0.05)
    out["tol"] = round(tol, 4)
    sh, sl = _pivots(h, l, L)
    if len(sh) < 2 or len(sl) < 2:
        out["reason"] = "chưa đủ đỉnh/đáy xoay"
        return out
    h1, h2 = sh[-2][1], sh[-1][1]
    l1, l2 = sl[-2][1], sl[-1][1]
    out["resistance"], out["support"] = h2, l2
    if h2 < h1 and l2 < l1:
        out["trend"] = "down"
    elif h2 > h1 and l2 > l1:
        out["trend"] = "up"
    else:
        out["reason"] = "đi ngang (đỉnh/đáy không cùng chiều)"
        return out

    c_now, o_now = c[-1], o[-1]
    # Chỉ tính "chạm" trong ĐỢT HỒI hiện tại (sau đáy xoay gần nhất khi giảm / sau đỉnh xoay gần nhất khi tăng),
    # không tính lần chạm cũ đã qua từ lâu.
    if out["trend"] == "down":
        leg = h[sl[-1][0] + 1:]
        recent_hi = leg.max() if len(leg) else h[-1]
    else:
        leg = l[sh[-1][0] + 1:]
        recent_lo = leg.min() if len(leg) else l[-1]
    if out["trend"] == "down":
        if c_now > h2 + tol:
            out.update(signal="BUY", reason=f"đóng cửa {c_now:.2f} vượt đỉnh hồi {h2:.2f} -> đổi cấu trúc lên")
        elif recent_hi >= h2 - tol and c_now < o_now and c_now < h2 - tol:
            out.update(signal="SELL", reason=f"hồi về kháng cự {h2:.2f} rồi bị từ chối (nến giảm) -> tiếp tục sóng giảm")
        else:
            out["reason"] = "sóng giảm, giá chưa chạm kháng cự hoặc chưa có từ chối"
    else:
        if c_now < l2 - tol:
            out.update(signal="SELL", reason=f"đóng cửa {c_now:.2f} thủng đáy hồi {l2:.2f} -> đổi cấu trúc xuống")
        elif recent_lo <= l2 + tol and c_now > o_now and c_now > l2 + tol:
            out.update(signal="BUY", reason=f"hồi xuống hỗ trợ {l2:.2f} rồi giữ (nến tăng) -> tiếp tục sóng tăng")
        else:
            out["reason"] = "sóng tăng, giá chưa chạm hỗ trợ hoặc chưa có giữ giá"
    return out
