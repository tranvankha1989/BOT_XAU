"""
trading_modules/ai_selfcheck.py
Tự chấm điểm AI (khóa ai_selfcheck_n, mặc định TẮT; người dùng duyệt 24/11/2026).

Mỗi tín hiệu BUY/SELL của AI được ghi lại (kể cả khi bị chặn, để còn chấm tiếp lúc đang nghỉ). Sau `wait_candles` nến
khung phân tích, so giá đóng nến lúc đó với giá lúc ra tín hiệu: giá đi đúng hướng AI báo = "đúng".
Xét `n` kết quả gần nhất (bỏ kết quả cũ hơn `max_age_hours`): đúng < `min_pct`% -> tạm nghỉ vào lệnh mới.
Chưa đủ `n` kết quả (mới mở app, sau đêm/cuối tuần) -> giao dịch bình thường.

Bằng chứng (log + trace máy 2, vàng M5, 18-23/11/2026): sau giai đoạn AI đúng, tín hiệu kế tiếp đúng ~6/10;
sau giai đoạn AI sai chỉ ~4.5/10. Mẫu ~5 ngày, chưa chắc chắn: cần chạy thử DEMO.

Hàm thuần: không Tk, không MT5. Thời gian là mốc nến (giờ server, đổi ra giây) để bộ phát lại chạy giống live.
"""

import pandas as pd

DEFAULT_WAIT_CANDLES = 3
DEFAULT_MIN_PCT = 50.0
DEFAULT_MAX_AGE_HOURS = 3.0
_KEEP_RESULTS = 60


def new_state() -> dict:
    return {"pending": [], "results": []}


def _sec(candle_time) -> int:
    return int(pd.Timestamp(candle_time).value // 10 ** 9)


def clean_state(raw) -> dict:
    """Đọc trạng thái (từ file JSON): bỏ phần tử sai dạng."""
    st = new_state()
    if not isinstance(raw, dict):
        return st
    for p in raw.get("pending", []) if isinstance(raw.get("pending"), list) else []:
        try:
            st["pending"].append({"t": int(p["t"]), "dir": 1 if int(p["dir"]) > 0 else -1, "price": float(p["price"])})
        except (KeyError, TypeError, ValueError):
            continue
    for r in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
        try:
            st["results"].append({"t": int(r["t"]), "ok": bool(r["ok"])})
        except (KeyError, TypeError, ValueError):
            continue
    return st


def selfcheck_step(state: dict, candle_time, close: float, action, tf_minutes: float, n: int,
                   wait_candles: int = DEFAULT_WAIT_CANDLES, min_pct: float = DEFAULT_MIN_PCT,
                   max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> dict:
    """Gọi 1 lần mỗi lần AI phân tích xong 1 nến (sửa `state` tại chỗ).
    candle_time: mốc nến khung phân tích vừa đóng; close: giá đóng nến đó; action: 'BUY'/'SELL'/None của lần này.
    Trả {"paused", "correct", "total", "changed"}; trạng thái nghỉ tính từ các kết quả ĐÃ CÓ (không gồm tín hiệu lần này).
    Gọi lại cùng 1 nến (phân tích lại do đổi cài đặt...) không ghi trùng."""
    now = _sec(candle_time)
    wait_sec = max(int(wait_candles), 1) * max(float(tf_minutes), 1.0) * 60.0
    changed = False

    # 1. Chấm các tín hiệu đã đủ số nến chờ; đến quá muộn (> 2 lần thời gian chờ: app tắt, mất dữ liệu) thì bỏ, không chấm
    keep = []
    for p in state.get("pending", []):
        age = now - p["t"]
        if age < wait_sec:
            keep.append(p)
            continue
        changed = True
        if age <= 2 * wait_sec:
            state.setdefault("results", []).append({"t": p["t"], "ok": p["dir"] * (float(close) - p["price"]) > 0})
    state["pending"] = keep

    # 2. Bỏ kết quả cũ hơn max_age_hours (tính theo mốc tín hiệu)
    cutoff = now - float(max_age_hours) * 3600.0
    results = [r for r in state.get("results", []) if r["t"] >= cutoff]
    if len(results) != len(state.get("results", [])):
        changed = True
    state["results"] = results[-_KEEP_RESULTS:]

    # 3. Quyết định tạm nghỉ
    recent = state["results"][-n:] if n > 0 else []
    correct = sum(1 for r in recent if r["ok"])
    paused = n > 0 and len(recent) >= n and correct * 100.0 < float(min_pct) * n

    # 4. Ghi tín hiệu lần này để chấm sau (không ghi trùng cùng nến)
    if action in ("BUY", "SELL") and not any(p["t"] == now for p in state["pending"]) \
            and not any(r["t"] == now for r in state["results"]):
        state["pending"].append({"t": now, "dir": 1 if action == "BUY" else -1, "price": float(close)})
        changed = True

    return {"paused": paused, "correct": correct, "total": len(recent), "changed": changed}
