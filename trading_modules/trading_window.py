"""
trading_modules/trading_window.py
Module KHUNG GIỜ CẤM giao dịch (theo giờ VN, ngày cụ thể và lặp hàng tuần). Hàm thuần; tách nguyên văn từ mt5_scalping_bot.py.
"""

import re
from datetime import datetime, timedelta, timezone


def is_trading_window_blocked(now_utc7=None, cfg: dict = None) -> tuple:
    """
    Kiểm tra xem thời điểm hiện tại (múi giờ UTC+7) có nằm trong danh sách khung giờ cấm giao dịch không.
    Hỗ trợ các định dạng:
    - Cố định theo ngày: 'YYYY-MM-DD HH:MM-HH:MM' (VD: '2026-11-12 19:25-20:00')
    - Lặp hàng tuần: 'THU 19:25-20:00' (hoặc T2..T7, CN, MON..SUN)
    - Lặp hàng ngày: 'HH:MM-HH:MM' (VD: '04:00-06:00')
    Nhiều khung giờ phân tách nhau bằng dấu phẩy ',', chấm phẩy ';' hoặc xuống dòng.
    Trả về: (is_blocked: bool, reason: str)
    """
    if not cfg or not isinstance(cfg, dict):
        return False, ""
    raw_val = str(cfg.get("blocked_trading_windows", "")).strip()
    if not raw_val:
        return False, ""

    tz_utc7 = timezone(timedelta(hours=7))
    if now_utc7 is None:
        now_dt = datetime.now(tz_utc7)
    elif isinstance(now_utc7, datetime):
        if now_utc7.tzinfo is None:
            now_dt = now_utc7.replace(tzinfo=tz_utc7)
        else:
            now_dt = now_utc7.astimezone(tz_utc7)
    else:
        now_dt = datetime.now(tz_utc7)

    day_map = {
        "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
        "T2": 0, "T3": 1, "T4": 2, "T5": 3, "T6": 4, "T7": 5, "CN": 6,
    }

    items = [x.strip() for x in re.split(r"[,;\n]+", raw_val) if x.strip()]
    def _to_min(hm: str) -> int:
        h, mi = hm.split(":")
        return int(h) * 60 + int(mi)

    now_min = now_dt.hour * 60 + now_dt.minute
    now_floor = now_dt.replace(second=0, microsecond=0)
    now_weekday = now_dt.weekday()

    for item in items:
        try:
            # 1. Định dạng cố định theo ngày: YYYY-MM-DD HH:MM-HH:MM
            m_date = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", item)
            if m_date:
                d_str, t_start, t_end = m_date.groups()
                d_base = datetime.strptime(d_str, "%Y-%m-%d").replace(tzinfo=tz_utc7)
                m_start, m_end = _to_min(t_start), _to_min(t_end)
                dt_start = d_base + timedelta(minutes=m_start)
                dt_end = d_base + timedelta(minutes=m_end)
                if m_end < m_start:  # Qua nửa đêm sang ngày kế
                    dt_end += timedelta(days=1)
                if dt_start <= now_floor <= dt_end:
                    return True, f"Khung giờ cấm giao dịch: {item} (UTC+7)"
                continue

            # 2. Định dạng lặp hàng tuần: THU 19:25-20:00
            m_week = re.match(r"^([A-Za-z0-9]{2,3})\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", item)
            if m_week:
                d_code, t_start, t_end = m_week.groups()
                d_target = day_map.get(d_code.upper())
                if d_target is not None:
                    m_start, m_end = _to_min(t_start), _to_min(t_end)
                    if m_start <= m_end:
                        if now_weekday == d_target and m_start <= now_min <= m_end:
                            return True, f"Khung giờ cấm lặp hàng tuần: {item} (UTC+7)"
                    else:
                        # Qua đêm: [start, 24:00) của ngày d_target và [00:00, end] của ngày kế tiếp
                        in_start_day = (now_weekday == d_target and now_min >= m_start)
                        in_next_day = (now_weekday == (d_target + 1) % 7 and now_min <= m_end)
                        if in_start_day or in_next_day:
                            return True, f"Khung giờ cấm lặp hàng tuần: {item} (UTC+7)"
                continue

            # 3. Định dạng lặp hàng ngày: HH:MM-HH:MM
            m_daily = re.match(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", item)
            if m_daily:
                m_start, m_end = (_to_min(x) for x in m_daily.groups())
                if m_start <= m_end:
                    if m_start <= now_min <= m_end:
                        return True, f"Khung giờ cấm hàng ngày: {item} (UTC+7)"
                else:
                    if now_min >= m_start or now_min <= m_end:
                        return True, f"Khung giờ cấm hàng ngày: {item} (UTC+7)"
                continue
        except Exception:
            continue

    return False, ""
