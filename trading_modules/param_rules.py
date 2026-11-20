"""
trading_modules/param_rules.py
Luật kiểm tra thông số người dùng nhập trên Menu và bảng Thông số Backtest.
CHỈ dùng cho giao diện (tô đỏ ô sai, tô cam ô cần lưu ý, chặn nút Lưu / Áp dụng): KHÔNG đổi luật giao dịch.
Hàm thuần, không Tk, không MT5.
"""

import re
from datetime import datetime

# Tên ngắn hiển thị trong thông báo
PARAM_NAMES = {
    "lot": "Khối lượng (Lot)",
    "max_pos": "Số lệnh tối đa",
    "magic_number": "Magic Number",
    "scenarios": "Số kịch bản AI",
    "consensus": "Độ đồng thuận tối thiểu (%)",
    "max_dispersion": "Độ phân tán tối đa",
    "min_move": "Biên độ tối thiểu",
    "expected_move_k": "Hệ số k biên độ bù phí",
    "max_spread": "Spread tối đa",
    "sl_atr": "Khoảng cách SL AI (x ATR14)",
    "sl_buffer": "Giá an toàn vào SL",
    "max_sl_price": "SL tối đa",
    "rr": "Tỷ lệ R:R",
    "min_real_rr": "RR thật tối thiểu",
    "min_entry_spacing": "Khoảng cách nhồi tối thiểu",
    "be_trigger_r": "Mức kích hoạt dời SL về Entry",
    "be_plus_price": "Giá lãi nhỏ cộng vào Entry (BE+)",
    "trail_dist_r": "Khoảng cách bám giá Trailing",
    "rev_consensus": "Đồng thuận đảo chiều - lệnh chưa BE (%)",
    "rev_be_consensus": "Đồng thuận đảo chiều - lệnh đã BE (%)",
    "rev_min_move": "Biên độ ngược chiều",
    "basket_tp_usd": "Chốt lời chùm lệnh ($)",
    "basket_cooldown_mins": "Nghỉ sau chốt chùm (phút)",
    "max_hold_mins": "Giữ lệnh tối đa (phút)",
    "daily_tp_usd": "Dừng khi lãi ngày ($)",
    "daily_sl_usd": "Dừng khi lỗ ngày ($)",
    "max_consec_loss": "Thua liên tiếp nghỉ (lệnh)",
    "cooldown_mins": "Thời gian tạm ngừng (phút)",
    "fast_sl_cooldown_candles": "Nghỉ nếu SL nhanh (nến)",
    "lookback": "Số nến lịch sử AI (Lookback)",
    "pred_len": "Số nến dự báo",
    "blocked_trading_windows": "Khung giờ cấm",
}

# Ô số: khóa -> (kiểu, cận dưới, được bằng cận dưới?, cận trên hoặc None). "int" = số nguyên (code bot đọc bằng int()).
NUMERIC_RULES = {
    "lot": ("float", 0.0, False, None),
    "max_pos": ("int", 1, True, None),
    "magic_number": ("int", 1, True, None),
    "scenarios": ("int", 1, True, None),
    "consensus": ("float", 50.0, True, 100.0),
    "max_dispersion": ("float", 0.0, True, None),
    "min_move": ("float", 0.0, True, None),
    "expected_move_k": ("float", 0.0, True, None),
    "max_spread": ("float", 0.0, False, None),
    "sl_atr": ("float", 0.0, True, None),
    "sl_buffer": ("float", 0.0, True, None),
    "max_sl_price": ("float", 0.0, True, None),
    "rr": ("float", 0.0, True, None),
    "min_real_rr": ("float", 0.0, True, None),
    "min_entry_spacing": ("float", 0.0, True, None),
    "be_trigger_r": ("float", 0.0, False, None),
    "be_plus_price": ("float", 0.0, True, None),
    "trail_dist_r": ("float", 0.0, False, None),
    "rev_consensus": ("float", 50.0, True, 100.0),
    "rev_be_consensus": ("float", 50.0, True, 100.0),
    "rev_min_move": ("float", 0.0, True, None),
    "basket_tp_usd": ("float", 0.0, True, None),
    "basket_cooldown_mins": ("float", 0.0, True, None),
    "max_hold_mins": ("float", 0.0, True, None),
    "daily_tp_usd": ("float", 0.0, True, None),
    "daily_sl_usd": ("float", 0.0, True, None),
    "max_consec_loss": ("int", 0, True, None),
    "cooldown_mins": ("float", 0.0, True, None),
    "fast_sl_cooldown_candles": ("float", 0.0, True, None),
    "lookback": ("int", 1, True, None),
    "pred_len": ("int", 1, True, None),
}

# Đặt 0 = Tắt luôn hợp lệ
ZERO_IS_OFF = frozenset({"rev_be_consensus"})

# Thông số con: cha tắt (bỏ tích, hoặc = 0 với cha là số) thì ô con bị khóa trên Menu -> không kiểm tra ô con
PARENT_OF = {
    "be_trigger_r": "auto_be",
    "trail_dist_r": "auto_trail",
    "rev_consensus": "auto_rev_close",
    "rev_be_consensus": "auto_rev_close",
    "rev_min_move": "auto_rev_close",
    "basket_cooldown_mins": "basket_tp_usd",
    "cooldown_mins": "max_consec_loss",
}
NUMERIC_PARENTS = frozenset({"basket_tp_usd", "max_consec_loss"})

# Hằng số lấy theo code bot live (đổi code đó thì sửa ở đây):
TRAIL_START_EXTRA_R = 0.3  # Trailing bắt đầu khi lãi >= Mức kích hoạt BE + 0.3R (_update_open_positions_and_manage)
FIXED_MIN_RR = 2.0  # Khớp lệnh canh vùng đòi RR thực tế >= max(R:R, 2.0) (_check_pending_pullback_order)

_RE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$")
_RE_WEEK = re.compile(r"^([A-Za-z0-9]{2,3})\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$")
_RE_DAILY = re.compile(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$")
_DAY_CODES = frozenset({"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN", "T2", "T3", "T4", "T5", "T6", "T7", "CN"})


def _issue(level: str, keys, msg: str) -> dict:
    return {"level": level, "keys": tuple(keys), "msg": msg}


def _to_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "bật")
    return bool(v)


def _is_on(cfg: dict, key: str) -> bool:
    """Cha đang bật? Giống Menu: ô số đang gõ dở (không đọc được) coi như bật; thiếu khóa coi như bật."""
    if key not in cfg:
        return True
    v = cfg.get(key)
    if key in NUMERIC_PARENTS:
        try:
            return float(str(v).strip()) > 0
        except (TypeError, ValueError):
            return True
    return _to_bool(v)


def _parse_number(raw, typ: str):
    """Đọc ô số như code bot (float()/int()). Trả (số, lỗi): lỗi là None, "empty", "nan", "int"."""
    if isinstance(raw, bool):
        return None, "nan"
    if isinstance(raw, (int, float)):
        x = float(raw)
        if x != x or x in (float("inf"), float("-inf")):
            return None, "nan"
        if typ == "int" and not x.is_integer():
            return None, "int"
        return x, None
    s = "" if raw is None else str(raw).strip()
    if not s:
        return None, "empty"
    if typ == "int":
        try:
            return float(int(s)), None
        except ValueError:
            try:
                float(s)
                return None, "int"
            except ValueError:
                return None, "nan"
    try:
        x = float(s)
    except ValueError:
        return None, "nan"
    if x != x or x in (float("inf"), float("-inf")):
        return None, "nan"
    return x, None


def _shown_value(raw) -> str:
    """Giá trị người dùng đang nhập, để ghi vào câu báo (cắt bớt nếu quá dài)."""
    s = "" if raw is None else str(raw).strip()
    return s if len(s) <= 20 else s[:20] + "…"


def _bound_text(lo, lo_incl: bool, hi) -> str:
    if hi is not None:
        return f"phải từ {lo:g} đến {hi:g}" if lo_incl else f"phải lớn hơn {lo:g} và không quá {hi:g}"
    if lo == 0:
        return "không được âm" if lo_incl else "phải lớn hơn 0"
    return f"phải từ {lo:g} trở lên" if lo_incl else f"phải lớn hơn {lo:g}"


def _hm_ok(hm: str) -> bool:
    h, m = (int(x) for x in hm.split(":"))
    return 0 <= m <= 59 and (0 <= h <= 23 or (h == 24 and m == 0))


def window_item_ok(item: str) -> bool:
    """1 khung giờ cấm đúng định dạng mà trading_window.is_trading_window_blocked đọc được."""
    m = _RE_DATE.match(item)
    if m:
        try:
            datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            return False
        return _hm_ok(m.group(2)) and _hm_ok(m.group(3))
    m = _RE_WEEK.match(item)
    if m:
        return m.group(1).upper() in _DAY_CODES and _hm_ok(m.group(2)) and _hm_ok(m.group(3))
    m = _RE_DAILY.match(item)
    if m:
        return _hm_ok(m.group(1)) and _hm_ok(m.group(2))
    return False


_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _conflict_issues(cfg: dict, vals: dict) -> list:
    """Ô chữ / ô chọn và xung đột giữa các ô (chỉ khi các khóa liên quan có trong cfg). Mỗi luật ghi rõ vì sao theo code bot."""
    out = []
    if "symbol" in cfg and not str(cfg.get("symbol") or "").strip():
        out.append(_issue("error", ("symbol",), "Mã giao dịch (Symbol): chưa nhập, bot sẽ bỏ qua profile này."))
    tf, atf = str(cfg.get("tf") or "").strip().upper(), str(cfg.get("analysis_tf") or "").strip().upper()
    if tf in _TF_MINUTES and atf in _TF_MINUTES and _TF_MINUTES[tf] > _TF_MINUTES[atf]:
        out.append(_issue("warn", ("tf", "analysis_tf"),
                          f"Khung vào lệnh {tf} lớn hơn Khung phân tích AI {atf}: thường đặt khung vào lệnh nhỏ hơn hoặc bằng "
                          f"(VD phân tích M5, vào lệnh M1)."))
    # Chuỗi thua: bot tắt tạm nghỉ khi số lệnh thua = 0 HOẶC thời gian nghỉ = 0 (_check_daily_profit_loss_limits)
    mcl, cdm = vals.get("max_consec_loss"), vals.get("cooldown_mins")
    if mcl is not None and mcl > 0 and cdm is not None and cdm == 0:
        out.append(_issue("warn", ("cooldown_mins", "max_consec_loss"),
                          f"Thua liên tiếp {mcl:g} lệnh nhưng Thời gian tạm ngừng = 0: bot sẽ KHÔNG tạm nghỉ (tính năng như tắt)."))
    # SL tối đa đo tới SL AI; SL thật = SL AI + Giá an toàn. TP theo AI (R:R = 0) không giãn theo Giá an toàn, SL tối đa có thể bị chia đôi
    # -> Giá an toàn >= 1/2 SL tối đa thì RR thật có thể < 1 (TP gần hơn SL thật). R:R > 0 (TP tính từ SL thật) hoặc bật RR thật tối thiểu thì không lo.
    buf, msl = vals.get("sl_buffer"), vals.get("max_sl_price")
    rr_v, mrr = vals.get("rr"), vals.get("min_real_rr")
    if (buf is not None and msl is not None and msl > 0 and buf >= msl / 2.0
            and not (rr_v is not None and rr_v > 0) and not (mrr is not None and mrr > 0)):
        out.append(_issue("warn", ("sl_buffer", "max_sl_price"),
                          f"Giá an toàn vào SL ({buf:g}) >= 1/2 SL tối đa ({msl:g}) khi TP theo AI: SL thật = SL AI + Giá an toàn, "
                          f"TP AI không giãn theo, lệnh có thể có TP gần hơn SL thật (RR thật < 1). "
                          f"Nên giảm Giá an toàn hoặc đặt RR thật tối thiểu (ví dụ 1.5)."))
    rc, rbc = vals.get("rev_consensus"), vals.get("rev_be_consensus")
    if _is_on(cfg, "auto_rev_close") and rc is not None and rbc is not None and 0 < rbc < rc:
        out.append(_issue("warn", ("rev_be_consensus", "rev_consensus"),
                          f"Đồng thuận đảo chiều lệnh ĐÃ BE ({rbc:g}%) thấp hơn lệnh CHƯA BE ({rc:g}%): lệnh đã an toàn lại bị "
                          f"đóng dễ hơn; thường đặt cao hơn hoặc bằng."))
    if "telegram_enabled" in cfg and _to_bool(cfg.get("telegram_enabled")):
        miss = [n for k, n in (("telegram_bot_token", "Bot Token"),) if k in cfg and not str(cfg.get(k) or "").strip()]
        # ID nhóm chat được để trống: khi đó bot gửi riêng cho 'ID người điều khiển' (19/11/2026); thiếu cả hai mới báo
        if ("telegram_chat_id" in cfg and not str(cfg.get("telegram_chat_id") or "").strip()
                and not str(cfg.get("telegram_admin_ids") or "").strip()):
            miss.append("ID nhóm chat hoặc ID người điều khiển")
        if miss:
            out.append(_issue("warn", ("telegram_enabled", "telegram_bot_token", "telegram_chat_id", "telegram_admin_ids"),
                              f"Đã bật Telegram nhưng chưa nhập {' và '.join(miss)}: bot sẽ không gửi/nhận tin."))
    if (str(cfg.get("engine") or "").startswith("Ưu tiên Hugging Face") and "hf_space" in cfg
            and not str(cfg.get("hf_space") or "").strip()):
        out.append(_issue("warn", ("engine", "hf_space"),
                          "Chọn chạy AI trên Hugging Face nhưng chưa nhập tên Space: bot tự chạy AI trên máy này."))
    return out


def validate_param_values(cfg: dict) -> list:
    """Kiểm tra bộ thông số (giá trị dạng chuỗi/bool như ô nhập). Trả danh sách vấn đề
    {"level": "error"|"warn", "keys": (khóa,...), "msg": câu báo}:
    - error: ô nhập sai hoặc mâu thuẫn làm tính năng không bao giờ chạy -> tô đỏ, chặn nút Lưu.
    - warn: hợp lệ nhưng dễ hiểu nhầm -> tô cam, không chặn."""
    cfg = cfg if isinstance(cfg, dict) else {}
    issues = []
    vals = {}
    for key, (typ, lo, lo_incl, hi) in NUMERIC_RULES.items():
        if key not in cfg:
            continue
        parent = PARENT_OF.get(key)
        if parent is not None and not _is_on(cfg, parent):
            continue
        name = PARAM_NAMES.get(key, key)
        x, err = _parse_number(cfg.get(key), typ)
        if err == "empty":
            issues.append(_issue("error", (key,), f"{name}: chưa nhập."))
            continue
        shown = _shown_value(cfg.get(key))
        if err == "int":
            issues.append(_issue("error", (key,), f"{name}: đang nhập '{shown}', phải là số nguyên (VD 2)."))
            continue
        if err is not None:
            issues.append(_issue("error", (key,), f"{name}: đang nhập '{shown}', phải là số (dùng dấu chấm, VD 1.5)."))
            continue
        if key in ZERO_IS_OFF and x == 0:
            vals[key] = x
            continue
        if x < lo or (x == lo and not lo_incl) or (hi is not None and x > hi):
            txt = _bound_text(lo, lo_incl, hi)
            if key in ZERO_IS_OFF:
                txt = "phải bằng 0 (tắt) hoặc " + txt[len("phải "):]
            issues.append(_issue("error", (key,), f"{name}: đang nhập {shown}, {txt}."))
            continue
        vals[key] = x

    rr = vals.get("rr")
    if rr is not None and rr > 0:
        be_on = _is_on(cfg, "auto_be")
        trail_on = _is_on(cfg, "auto_trail")
        be = vals.get("be_trigger_r") if be_on else None
        td = vals.get("trail_dist_r") if trail_on else None
        if be is not None and be >= rr:
            issues.append(_issue("error", ("be_trigger_r", "rr"),
                                 f"Mức kích hoạt dời SL về Entry ({be:g}R) phải nhỏ hơn Tỷ lệ R:R ({rr:g}): "
                                 f"nếu không, lệnh chạm TP trước khi kịp dời SL."))
        elif be is not None and trail_on and round(be + TRAIL_START_EXTRA_R, 6) >= rr:
            issues.append(_issue("error", ("be_trigger_r", "rr"),
                                 f"Trailing bắt đầu khi lãi đạt Mức kích hoạt + {TRAIL_START_EXTRA_R:g}R "
                                 f"({round(be + TRAIL_START_EXTRA_R, 6):g}R), phải nhỏ hơn Tỷ lệ R:R ({rr:g}): "
                                 f"nếu không, Trailing không bao giờ chạy."))
        if td is not None and td >= rr:
            issues.append(_issue("error", ("trail_dist_r", "rr"),
                                 f"Khoảng cách bám giá Trailing ({td:g}R) phải nhỏ hơn Tỷ lệ R:R ({rr:g}): "
                                 f"nếu không, Trailing không khóa được lãi trước TP."))
        if rr < FIXED_MIN_RR:
            issues.append(_issue("warn", ("rr",),
                                 f"Tỷ lệ R:R {rr:g} < {FIXED_MIN_RR:g}: lúc khớp lệnh bot vẫn đòi lãi ≥ {FIXED_MIN_RR:g}R "
                                 f"(luật cố định) nên ít lệnh hơn và TP thực tế không nhỏ như đặt."))

    issues.extend(_conflict_issues(cfg, vals))

    raw_bw = cfg.get("blocked_trading_windows")
    if raw_bw is not None and str(raw_bw).strip():
        items = [x.strip() for x in re.split(r"[,;\n]+", str(raw_bw)) if x.strip()]
        bad = [x for x in items if not window_item_ok(x)]
        if bad:
            issues.append(_issue("error", ("blocked_trading_windows",),
                                 f"{PARAM_NAMES['blocked_trading_windows']}: sai định dạng '{bad[0]}' "
                                 f"(VD: 04:00-06:00, THU 19:25-20:00, 2026-11-12 19:25-20:00)."))
    return issues
