"""P53-C: test mock cho cache kịch bản AI thô của backtest (tools/bt_ai_cache.py). Không MT5, không nạp model thật."""
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mt5_scalping_bot as b
from tools.bt_ai_cache import ScenarioCache, predictor_fingerprint

N_ANA, N_SC = 120, 80
COLS = ["open", "high", "low", "close", "volume", "amount"]


class FakePredictor:
    def __init__(self, w=1.0):
        torch.manual_seed(0)
        self.model = torch.nn.Linear(3, 2)
        self.tokenizer = torch.nn.Linear(2, 2)
        with torch.no_grad():
            self.model.weight.add_(w - 1.0)
        self.device = "cpu"
        self.max_context = 512


def mk_df(n, bump=0.0):
    ts = pd.date_range("2026-11-13", periods=n, freq="5min", tz="UTC")
    c = 100.0 + np.arange(n) * 0.01 + bump
    return pd.DataFrame({"timestamps": ts, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0})


def make_app(counter):
    app = object.__new__(b.KronosDesktopApp)
    app._analyze_scalping_micro_macro_context = lambda *a, **k: {"macro_dir": 0, "structure_reason": "giả"}
    app._compute_adaptive_sampling_params = lambda *a, **k: (1.0, 0.9, "test")

    def fake_predict(predictor, df_in, x_timestamp, y_timestamp, pred_len, num_scenarios, T, top_k, top_p):
        counter["n"] += 1
        last_c = float(df_in["close"].iloc[-1])
        rng = np.random.default_rng(7)
        out = []
        for i in range(num_scenarios):
            arr = (last_c + np.linspace(1, 6, pred_len)[:, None] + rng.normal(0, 0.1, (pred_len, 6))).astype(np.float32)
            d = pd.DataFrame(arr, columns=COLS)
            d["high"] = d["high"] + 1.0
            d["low"] = d["low"] - 1.0
            d.insert(0, "timestamps", y_timestamp.values)
            d.attrs["log_prob"] = float(-1.0 - 0.1 * i)
            out.append(d)
        return out
    app._fast_predict_scenarios = fake_predict
    return app


CFG = {"consensus": 75, "min_move": 1.2, "max_dispersion": 2.5, "sl_atr": 1.5, "sl_buffer": 0.5,
       "trend_filter": True, "bt_use_live_structure": True, "lookback": 100, "scenarios": 8}


def run_eval(app, predictor, df_ana, df_sc):
    return app._evaluate_bt_ai_candle(predictor, df_ana, 100, dict(CFG), {}, "M5", False, 1.0,
                                      df_rates_scalp=df_sc, idx_scalp=75, scalp_tf_str="M5")


def same(tc, a, b):
    """So từng trường kết quả (có thể chứa DataFrame)."""
    tc.assertEqual(a is None, b is None)
    if a is None:
        return
    tc.assertEqual(set(a.keys()), set(b.keys()))
    for k in a:
        if isinstance(a[k], pd.DataFrame):
            pd.testing.assert_frame_equal(a[k], b[k])
        else:
            tc.assertEqual(a[k], b[k], k)


class TestScenarioCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pred = FakePredictor()
        self.df_ana, self.df_sc = mk_df(N_ANA), mk_df(N_SC)

    def test_khong_cache_hanh_vi_cu(self):
        c = {"n": 0}
        app = make_app(c)  # không có _bt_scenario_cache
        r1 = run_eval(app, self.pred, self.df_ana, self.df_sc)
        r2 = run_eval(app, self.pred, self.df_ana, self.df_sc)
        self.assertEqual(c["n"], 2)
        same(self, r1, r2)

    def test_lan_2_trung_cache_khong_goi_ai_va_ket_qua_giong_het(self):
        c0 = {"n": 0}
        ref = run_eval(make_app(c0), self.pred, self.df_ana, self.df_sc)  # chuẩn: không cache
        c = {"n": 0}
        app = make_app(c)
        app._bt_scenario_cache = ScenarioCache(self.tmp.name)
        r1 = run_eval(app, self.pred, self.df_ana, self.df_sc)
        r2 = run_eval(app, self.pred, self.df_ana, self.df_sc)
        self.assertEqual(c["n"], 1)                      # lần 2 không gọi AI
        self.assertEqual((app._bt_scenario_cache.hits, app._bt_scenario_cache.misses), (1, 1))
        self.assertIsNotNone(ref)
        same(self, r1, ref)
        same(self, r2, ref)                        # có cache == không cache, từng trường

    def test_scenarios_tu_cache_giong_het_ban_goc(self):
        c = {"n": 0}
        app = make_app(c)
        cache = ScenarioCache(self.tmp.name)
        sub = self.df_ana.iloc[:100]
        y_ts = pd.Series([sub["timestamps"].iloc[-1] + pd.Timedelta(minutes=5 * (k + 1)) for k in range(6)])
        orig = app._fast_predict_scenarios(self.pred, sub, None, y_ts, 6, 8, 1.0, 0, 0.9)
        key = cache.make_key(self.pred, sub, y_ts, 8, 1.0, 0.9)
        cache.put(key, orig)
        got = cache.get(key, y_ts)
        self.assertEqual(len(got), 8)
        for o, g in zip(orig, got):
            pd.testing.assert_frame_equal(o, g)
            self.assertEqual(o.attrs["log_prob"], g.attrs["log_prob"])

    def test_doi_du_lieu_nen_khoa_doi(self):
        cache = ScenarioCache(self.tmp.name)
        sub = self.df_ana.iloc[:100]
        y_ts = pd.Series(pd.date_range("2026-11-13 08:20", periods=6, freq="5min", tz="UTC"))
        k0 = cache.make_key(self.pred, sub, y_ts, 8, 1.0, 0.9)
        self.assertEqual(k0, cache.make_key(self.pred, sub, y_ts, 8, 1.0, 0.9))   # ổn định
        sub2 = sub.copy()
        sub2.loc[50, "close"] += 0.01                                              # đổi 1 nến giữa chuỗi
        self.assertNotEqual(k0, cache.make_key(self.pred, sub2, y_ts, 8, 1.0, 0.9))
        for args in ((9, 1.0, 0.9), (8, 1.1, 0.9), (8, 1.0, 0.8)):                # kịch bản / nhiệt độ / top_p
            self.assertNotEqual(k0, cache.make_key(self.pred, sub, y_ts, *args))

    def test_doi_trong_so_model_khoa_doi(self):
        sub = self.df_ana.iloc[:100]
        y_ts = pd.Series(pd.date_range("2026-11-13 08:20", periods=6, freq="5min", tz="UTC"))
        p2 = FakePredictor(w=1.5)
        self.assertNotEqual(predictor_fingerprint(self.pred), predictor_fingerprint(p2))
        k1 = ScenarioCache(self.tmp.name).make_key(self.pred, sub, y_ts, 8, 1.0, 0.9)
        k2 = ScenarioCache(self.tmp.name).make_key(p2, sub, y_ts, 8, 1.0, 0.9)
        self.assertNotEqual(k1, k2)

    def test_file_hong_coi_nhu_truot_va_chay_lai_ai(self):
        c = {"n": 0}
        app = make_app(c)
        app._bt_scenario_cache = ScenarioCache(self.tmp.name)
        ref = run_eval(app, self.pred, self.df_ana, self.df_sc)
        files = [f for f in os.listdir(self.tmp.name) if f.endswith(".npz")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.tmp.name, files[0]), "wb") as f:
            f.write(b"khong phai npz")
        r2 = run_eval(app, self.pred, self.df_ana, self.df_sc)
        self.assertEqual(c["n"], 2)                      # chạy lại AI
        self.assertEqual(app._bt_scenario_cache.read_errors, 1)
        same(self, r2, ref)
        r3 = run_eval(app, self.pred, self.df_ana, self.df_sc)   # đã ghi đè file tốt -> trúng
        self.assertEqual(c["n"], 2)
        same(self, r3, ref)

    def test_loi_ghi_cache_khong_lam_hong_backtest(self):
        c = {"n": 0}
        app = make_app(c)
        cache = ScenarioCache(self.tmp.name)
        cache.cache_dir = os.path.join(self.tmp.name, "khong_ton_tai", "x")   # ghi sẽ lỗi
        app._bt_scenario_cache = cache
        r = run_eval(app, self.pred, self.df_ana, self.df_sc)
        self.assertIsNotNone(r)
        self.assertEqual(cache.write_errors, 1)

    def test_cache_hong_ham_make_key_khong_lam_hong_backtest(self):
        class BadCache:
            def make_key(self, *a, **k):
                raise RuntimeError("hỏng")
        c = {"n": 0}
        app = make_app(c)
        app._bt_scenario_cache = BadCache()
        self.assertIsNotNone(run_eval(app, self.pred, self.df_ana, self.df_sc))
        self.assertEqual(c["n"], 1)


if __name__ == "__main__":
    unittest.main()
