"""
trading_modules/scenario_scoring.py
Module CHẤM ĐIỂM KỊCH BẢN KRONOS: đồng thuận quỹ đạo đa bước, trọng số Bayesian theo log-likelihood, đường giá trung bình có trọng số,
kịch bản đại diện (medoid), độ phân tán, độ tin cậy (cộng/trừ điểm theo cấu trúc, order-flow, râu nến).
Hàm THUẦN dùng CHUNG cho live (_run_analysis_worker) và backtest (_evaluate_bt_ai_candle); trước đây hai nơi chép lại cùng công thức.
Tách nguyên văn (hành vi giữ nguyên 100%, có test so với bản gốc đóng băng + golden của _evaluate_bt_ai_candle).
Module KHÔNG sửa kịch bản của Kronos, chỉ đọc.
"""

import numpy as np


def score_scenarios(scenarios, last_close: float, atr: float, macro_ctx: dict) -> dict:
    """scenarios: list DataFrame (cột close...; attrs["log_prob"]); last_close: giá đóng nến cuối; atr: ATR đo phân tán;
    macro_ctx: dict từ module cấu trúc (macro_dir, orderflow_dir, wick_dir).
    Trả dict: bull_count, bear_count, total, bull_pct, bear_pct, scen_weights, mean_closes, avg_end_close, expected_move,
    scenario_std, dispersion_r, best_idx, dir_pct, ai_dom_dir, confidence_pct."""
    last_c = last_close
    bull_cnt = 0
    bear_cnt = 0
    for s in scenarios:
        c_arr = s["close"].values.astype(float)
        end_d = float(c_arr[-1]) - last_c
        path_d = float(np.mean(c_arr)) - last_c
        eff_d = 0.70 * end_d + 0.30 * path_d
        if eff_d > 0.0:
            bull_cnt += 1
        elif eff_d < 0.0:
            bear_cnt += 1
    total_scen = len(scenarios)
    b_pct = (bull_cnt / total_scen) * 100.0
    s_pct = (bear_cnt / total_scen) * 100.0

    raw_lps = np.array([float(s.attrs.get("log_prob", 0.0)) for s in scenarios], dtype=float)
    if np.ptp(raw_lps) > 1e-6:
        lp_shifted = np.clip((raw_lps - np.max(raw_lps)) * 0.35, -4.0, 0.0)
        scen_weights = np.exp(lp_shifted)
        scen_weights = scen_weights / np.sum(scen_weights)
    else:
        scen_weights = np.ones(total_scen, dtype=float) / max(float(total_scen), 1.0)

    all_closes_mat = np.array([s["close"].values.astype(float) for s in scenarios], dtype=float)
    mean_closes = np.average(all_closes_mat, axis=0, weights=scen_weights)
    avg_end_close = float(mean_closes[-1])
    exp_m = avg_end_close - last_c

    end_closes = np.array([float(s["close"].iloc[-1]) for s in scenarios], dtype=float)
    scenario_std = float(np.sqrt(np.average((end_closes - avg_end_close) ** 2, weights=scen_weights)))
    dispersion_r = float(scenario_std / max(atr, 0.01))

    best_idx = int(np.argmin([
        float(np.mean(np.abs(s["close"].values.astype(float) - mean_closes))) / max(float(scen_weights[idx_s]) * total_scen, 0.35)
        for idx_s, s in enumerate(scenarios)
    ]))

    dir_pct = max(b_pct, s_pct)
    disp_penalty = max(0.0, min(0.45, (dispersion_r - 1.0) * 0.25)) if dispersion_r > 1.0 else 0.0
    base_conf = dir_pct * (1.0 - disp_penalty)
    ai_dom_dir = 1 if b_pct > s_pct else (-1 if s_pct > b_pct else (1 if avg_end_close >= last_c else -1))
    ctx_bonus = 0.0
    if macro_ctx.get("macro_dir", 0) != 0:
        ctx_bonus += 4.0 if macro_ctx.get("macro_dir", 0) == ai_dom_dir else -3.5
    if macro_ctx.get("orderflow_dir", 0) != 0:
        ctx_bonus += 3.5 if macro_ctx.get("orderflow_dir", 0) == ai_dom_dir else -2.5
    if macro_ctx.get("wick_dir", 0) == ai_dom_dir:
        ctx_bonus += 3.5
    confidence_pct = max(10.0, min(99.0, base_conf + ctx_bonus))
    return {
        "bull_count": bull_cnt, "bear_count": bear_cnt, "total": total_scen,
        "bull_pct": b_pct, "bear_pct": s_pct, "scen_weights": scen_weights, "mean_closes": mean_closes,
        "avg_end_close": avg_end_close, "expected_move": exp_m, "scenario_std": scenario_std, "dispersion_r": dispersion_r,
        "best_idx": best_idx, "dir_pct": dir_pct, "ai_dom_dir": ai_dom_dir, "confidence_pct": confidence_pct,
    }
