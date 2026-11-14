"""Ca thử xác định cho module Price Action: 5 nến gần nhất + bid/ask + ATR + số xác nhận tối thiểu."""
import numpy as np
import pandas as pd


def _candles(rng, n, style):
    base = 2000.0 + rng.normal(0, 3)
    rows = []
    px = base
    for i in range(n):
        o = px
        if style == "marubozu_down":
            h, l, c = o + 0.05, o - 1.4, o - 1.38
        elif style == "marubozu_up":
            h, l, c = o + 1.4, o - 0.05, o + 1.38
        elif style == "hammer":
            c = o + rng.normal(0, 0.1)
            h, l = max(o, c) + 0.05, min(o, c) - rng.uniform(0.6, 1.2)
        elif style == "shooting":
            c = o + rng.normal(0, 0.1)
            h, l = max(o, c) + rng.uniform(0.6, 1.2), min(o, c) - 0.05
        else:
            c = o + rng.normal(0, 0.5)
            h = max(o, c) + abs(rng.normal(0, 0.3))
            l = min(o, c) - abs(rng.normal(0, 0.3))
        rows.append((o, h, l, c))
        px = c
    a = np.array(rows)
    return pd.DataFrame({"timestamps": pd.date_range("2026-11-02", periods=n, freq="1min", tz="UTC"),
                         "open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": 100.0})


def make_cases():
    """Trả list dict(name, action, df, ask, bid, atr, req_conf, fail_open)."""
    rng = np.random.default_rng(777)
    styles = ["marubozu_down", "marubozu_up", "hammer", "shooting", "noise", "noise", "noise"]
    cases = []
    k = 0
    for style in styles:
        for action in ("BUY", "SELL"):
            for _ in range(12):
                df = _candles(rng, 5, style)
                last = float(df["close"].iloc[-1])
                spr = float(np.round(rng.uniform(0.02, 0.6), 2))
                bid = last + float(rng.normal(0, 0.4))
                atr = [None, 0.0, float(np.round(rng.uniform(0.5, 4.0), 3))][int(rng.integers(0, 3))]
                cases.append(dict(name=f"{style}/{action}#{k}", action=action, df=df, ask=bid + spr, bid=bid, atr=atr,
                                  req_conf=int(rng.integers(1, 4)), fail_open=bool(rng.integers(0, 2))))
                k += 1
    # ca biên
    df = _candles(rng, 5, "noise")
    cases.append(dict(name="df_none", action="BUY", df=None, ask=2000.3, bid=2000.0, atr=1.0, req_conf=1, fail_open=False))
    cases.append(dict(name="df_none_open", action="SELL", df=None, ask=2000.3, bid=2000.0, atr=1.0, req_conf=1, fail_open=True))
    cases.append(dict(name="df_1_nen", action="BUY", df=df.iloc[:1], ask=2000.3, bid=2000.0, atr=1.0, req_conf=1, fail_open=False))
    cases.append(dict(name="tick_none", action="BUY", df=df, ask=None, bid=None, atr=1.0, req_conf=1, fail_open=False))
    cases.append(dict(name="tick_none_open", action="SELL", df=df, ask=None, bid=None, atr=1.0, req_conf=2, fail_open=True))
    cases.append(dict(name="thieu_cot", action="BUY", df=df.drop(columns=["low"]), ask=2000.3, bid=2000.0, atr=1.0, req_conf=1, fail_open=False))
    cases.append(dict(name="thieu_cot_open", action="BUY", df=df.drop(columns=["low"]), ask=2000.3, bid=2000.0, atr=1.0, req_conf=1, fail_open=True))
    return cases
