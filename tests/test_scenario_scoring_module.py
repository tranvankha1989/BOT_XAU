"""Chấm điểm kịch bản Kronos: (1) _evaluate_bt_ai_candle sau khi tách == golden sinh từ code gốc; (2) score_scenarios == bản gốc đóng băng
của khối chấm điểm trong _run_analysis_worker (live) trên nhiều tập kịch bản ngẫu nhiên."""
import json
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "golden"))
import gen_scoring_golden as gg
from scoring_cases import make_cases, make_scenarios
from trading_modules.scenario_scoring import score_scenarios

GOLDEN = json.load(open(os.path.join(ROOT, "tests", "golden", "scoring_golden.json"), encoding="utf-8"))


def legacy_live_block(scenarios, last_close, atr, macro_ctx):
    """Bản GỐC đóng băng (chép nguyên văn từ _run_analysis_worker trước khi tách)."""
    bull_count = 0
    bear_count = 0
    for s in scenarios:
        c_arr = s["close"].values.astype(float)
        end_d = float(c_arr[-1]) - last_close
        path_d = float(np.mean(c_arr)) - last_close
        eff_d = 0.70 * end_d + 0.30 * path_d
        if eff_d > 0.0:
            bull_count += 1
        elif eff_d < 0.0:
            bear_count += 1
    total = len(scenarios)
    bull_pct = (bull_count / total) * 100.0
    bear_pct = (bear_count / total) * 100.0
    raw_lps = np.array([float(s.attrs.get("log_prob", 0.0)) for s in scenarios], dtype=float)
    if np.ptp(raw_lps) > 1e-6:
        lp_shifted = np.clip((raw_lps - np.max(raw_lps)) * 0.35, -4.0, 0.0)
        scen_weights = np.exp(lp_shifted)
        scen_weights = scen_weights / np.sum(scen_weights)
    else:
        scen_weights = np.ones(total, dtype=float) / max(float(total), 1.0)
    all_closes_mat = np.array([s["close"].values.astype(float) for s in scenarios], dtype=float)
    mean_closes = np.average(all_closes_mat, axis=0, weights=scen_weights)
    avg_end_close = float(mean_closes[-1])
    best_idx = int(np.argmin([
        float(np.mean(np.abs(s["close"].values.astype(float) - mean_closes))) / max(float(scen_weights[idx_s]) * total, 0.35)
        for idx_s, s in enumerate(scenarios)
    ]))
    end_closes = np.array([float(s["close"].iloc[-1]) for s in scenarios], dtype=float)
    scenario_std = float(np.sqrt(np.average((end_closes - avg_end_close) ** 2, weights=scen_weights)))
    dispersion_r = float(scenario_std / max(atr, 0.01))
    dir_pct = max(bull_pct, bear_pct)
    disp_penalty = max(0.0, min(0.45, (dispersion_r - 1.0) * 0.25)) if dispersion_r > 1.0 else 0.0
    base_conf = dir_pct * (1.0 - disp_penalty)
    ai_dom_dir = 1 if bull_pct > bear_pct else (-1 if bear_pct > bull_pct else (1 if avg_end_close >= last_close else -1))
    ctx_bonus = 0.0
    if macro_ctx["macro_dir"] != 0:
        ctx_bonus += 4.0 if macro_ctx["macro_dir"] == ai_dom_dir else -3.5
    if macro_ctx["orderflow_dir"] != 0:
        ctx_bonus += 3.5 if macro_ctx["orderflow_dir"] == ai_dom_dir else -2.5
    if macro_ctx["wick_dir"] == ai_dom_dir:
        ctx_bonus += 3.5
    confidence_pct = max(10.0, min(99.0, base_conf + ctx_bonus))
    return dict(bull_count=bull_count, bear_count=bear_count, total=total, bull_pct=bull_pct, bear_pct=bear_pct,
                avg_end_close=avg_end_close, scenario_std=scenario_std, dispersion_r=dispersion_r, best_idx=best_idx,
                dir_pct=dir_pct, ai_dom_dir=ai_dom_dir, confidence_pct=confidence_pct)


class TestScenarioScoring(unittest.TestCase):
    def test_evaluate_bt_ai_candle_giong_golden(self):
        cases = make_cases()
        self.assertEqual(len(cases), len(GOLDEN))
        n_sig = 0
        for c in cases:
            got = gg.run_case(c)
            exp = GOLDEN[c["name"]]
            self.assertEqual(json.loads(json.dumps(got, default=float)), exp, c["name"])
            n_sig += got is not None
        self.assertGreater(n_sig, 20)

    def test_score_scenarios_giong_ban_goc_live(self):
        rng = np.random.default_rng(99)
        for k in range(300):
            last = float(2000 + rng.normal(0, 10))
            n = int(rng.choice([4, 8, 16]))
            scen = make_scenarios(rng, last, n, float(rng.random()), float(rng.choice([0.2, 1.0, 5.0])), same_lp=(k % 9 == 0))
            macro = {"macro_dir": int(rng.integers(-1, 2)), "orderflow_dir": int(rng.integers(-1, 2)), "wick_dir": int(rng.integers(-1, 2))}
            atr = float(rng.uniform(0.3, 6.0))
            ref = legacy_live_block(scen, last, atr, macro)
            got = score_scenarios(scen, last, atr, macro)
            for key, v in ref.items():
                self.assertEqual(got[key], v, f"{k}/{key}")


if __name__ == "__main__":
    unittest.main()
