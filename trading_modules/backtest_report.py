"""
trading_modules/backtest_report.py
Module BÁO CÁO BACKTEST: thống kê, định dạng, xuất CSV. Hàm thuần; tách nguyên văn từ mt5_scalping_bot.py.
"""

import csv
import os

import numpy as np

_BT_WIN_EPS = 0.05  # Ngưỡng $ phân loại thắng/thua/hòa vốn, đồng bộ với thẻ Scorecards


def build_backtest_report(trades: list, init_cap: float, n_boot: int = 2000, seed: int = 12345) -> dict:
    """Tính các chỉ số tổng kết backtest từ danh sách lệnh (chỉ đọc, không đổi logic vào/thoát lệnh)."""
    rep = {"n_trades": len(trades), "init_cap": float(init_cap), "daily": [], "warnings": []}
    if not trades:
        rep["warnings"].append("Không có lệnh nào.")
        return rep

    by_day = {}
    for t in trades:
        by_day.setdefault(str(t.get("date") or "N/A"), []).append(float(t["pnl"]))
    for d in sorted(by_day):
        pl = by_day[d]
        wins_d = sum(1 for x in pl if x > _BT_WIN_EPS)
        rep["daily"].append({"date": d, "n": len(pl), "pnl": round(sum(pl), 2), "win_rate": wins_d / len(pl) * 100.0})
    n_days = len(rep["daily"])
    rep["n_days"] = n_days

    pnls = [float(t["pnl"]) for t in trades]
    win_sum = sum(x for x in pnls if x > 0)
    loss_sum = abs(sum(x for x in pnls if x < 0))
    rep["total_pnl"] = round(sum(pnls), 2)
    rep["win_rate"] = sum(1 for x in pnls if x > _BT_WIN_EPS) / len(pnls) * 100.0
    rep["profit_factor"] = (win_sum / loss_sum) if loss_sum > 0 else (float("inf") if win_sum > 0 else 0.0)

    rs = [float(t["pnl"]) / float(t["risk_usd"]) for t in trades if float(t.get("risk_usd") or 0.0) > 0]
    rep["expectancy_r"] = (sum(rs) / len(rs)) if rs else None
    rep["expectancy_r_n"] = len(rs)

    eq = peak = float(init_cap)
    max_dd = max_dd_pct = 0.0
    for x in pnls:
        eq += x
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * 100.0) if peak > 0 else 0.0
    rep["max_dd"] = round(max_dd, 2)
    rep["max_dd_pct"] = max_dd_pct

    day_pnls = np.array([d["pnl"] for d in rep["daily"]], dtype=float)
    rep["profitable_days_pct"] = float(np.sum(day_pnls > 0)) / n_days * 100.0
    rep["avg_trades_per_day"] = len(trades) / n_days
    worst = min(rep["daily"], key=lambda d: d["pnl"])
    rep["worst_day"] = (worst["date"], worst["pnl"])

    streak = best = 0  # Lệnh hòa vốn (|pnl| <= eps) không cắt cũng không cộng chuỗi thua
    for x in pnls:
        if x < -_BT_WIN_EPS:
            streak += 1
            best = max(best, streak)
        elif x > _BT_WIN_EPS:
            streak = 0
    rep["max_loss_streak"] = best

    rep["mean_daily"] = float(day_pnls.mean())
    rep["ci95_daily"] = None
    if n_days >= 10:
        rng = np.random.default_rng(seed)
        means = day_pnls[rng.integers(0, n_days, size=(int(n_boot), n_days))].mean(axis=1)
        lo, hi = np.percentile(means, [2.5, 97.5])
        rep["ci95_daily"] = (float(lo), float(hi))

    kinds = sorted({str(t.get("cost_kind")) for t in trades if t.get("cost_kind")})
    costs = [float(t["cost_spread"]) for t in trades if t.get("cost_spread") is not None]
    rep["cost_kinds"] = kinds
    rep["cost_spread_total"] = round(sum(costs), 2)
    rep["cost_trades_known"] = len(costs)
    gross_before_cost = rep["total_pnl"] + rep["cost_spread_total"]
    rep["gross_before_cost"] = round(gross_before_cost, 2)
    rep["cost_ratio_pct"] = (rep["cost_spread_total"] / gross_before_cost * 100.0) if (costs and gross_before_cost > 0) else None

    if n_days < 10:
        rep["warnings"].append(f"Chỉ có {n_days} ngày giao dịch (< 10): kết luận thống kê không đáng tin.")
    if len(trades) < 100:
        rep["warnings"].append(f"Chỉ có {len(trades)} lệnh (< 100): mẫu quá nhỏ.")
    return rep


def format_backtest_report(rep: dict) -> list:
    """Chuyển báo cáo thành các dòng văn bản tiếng Việt để ghi log."""
    L = ["📊 ===== TỔNG KẾT BACKTEST ====="]
    if rep.get("n_trades", 0) == 0:
        return L + ["Không có lệnh nào để thống kê."]
    L.append("Ngày (theo ngày vào lệnh) | Số lệnh | Lãi/Lỗ ròng | Tỷ lệ thắng")
    for d in rep["daily"]:
        L.append(f"  {d['date']} | {d['n']:>3} | {d['pnl']:+9.2f} $ | {d['win_rate']:5.1f}%")
    pf = rep["profit_factor"]
    pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
    er = rep["expectancy_r"]
    er_s = f"{er:+.3f}R/lệnh (n={rep['expectancy_r_n']})" if er is not None else "không có dữ liệu rủi ro (R)"
    L.append(f"Tổng: {rep['n_trades']} lệnh / {rep['n_days']} ngày | Lãi ròng {rep['total_pnl']:+.2f} $ | Win rate {rep['win_rate']:.1f}%")
    L.append(f"Kỳ vọng: {er_s} | Profit factor: {pf_s}")
    L.append(f"Max drawdown: -{rep['max_dd']:.2f} $ ({rep['max_dd_pct']:.1f}%) | Chuỗi thua dài nhất: {rep['max_loss_streak']} lệnh")
    L.append(f"Ngày có lãi: {rep['profitable_days_pct']:.1f}% | Lệnh TB/ngày: {rep['avg_trades_per_day']:.1f} | Ngày tệ nhất: {rep['worst_day'][0]} ({rep['worst_day'][1]:+.2f} $)")
    ci = rep["ci95_daily"]
    if ci is None:
        L.append("KTC 95% lãi/lỗ TB mỗi ngày: không đủ dữ liệu (cần >= 10 ngày giao dịch)")
    else:
        L.append(f"KTC 95% lãi/lỗ TB mỗi ngày (bootstrap theo ngày, 2000 lần): [{ci[0]:+.2f}; {ci[1]:+.2f}] $ (TB {rep['mean_daily']:+.2f} $)")
    kinds = rep["cost_kinds"]
    spread_desc = []
    if "CANDLE_EXPLICIT" in kinds:
        spread_desc.append("spread cố định theo lot (chế độ nến) đã trừ vào lãi/lỗ")
    if "S1_EMBEDDED" in kinds:
        spread_desc.append("spread tick thực (chế độ S1) nằm sẵn trong giá ask/bid; số liệu chi phí chỉ là ước tính theo spread tại giây vào lệnh")
    L.append("Chi phí ĐÃ áp dụng: " + ("; ".join(spread_desc) if spread_desc else "không xác định được (lệnh thiếu dữ liệu chi phí)"))
    L.append("Chi phí KHÔNG được mô phỏng: commission, slippage, swap")
    if rep["cost_ratio_pct"] is not None:
        L.append(f"Tổng chi phí spread {rep['cost_spread_total']:.2f} $ so với lãi gộp trước chi phí {rep['gross_before_cost']:.2f} $ = {rep['cost_ratio_pct']:.1f}%")
    else:
        L.append(f"Tổng chi phí spread {rep['cost_spread_total']:.2f} $; lãi gộp trước chi phí {rep['gross_before_cost']:.2f} $ (không tính được tỷ lệ)")
    for w in rep["warnings"]:
        L.append(f"⚠️ CẢNH BÁO: {w}")
    return L


def export_backtest_csv(trades: list, path: str) -> str:
    """Xuất danh sách lệnh backtest ra CSV (UTF-8 BOM để Excel đọc đúng tiếng Việt)."""
    import csv
    cols = ["ticket", "date", "in_time", "out_time", "duration", "type", "lot", "in_price", "out_price",
            "pnl", "risk_usd", "cost_spread", "cost_kind", "reason"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow({c: t.get(c, "") for c in cols})
    return path
