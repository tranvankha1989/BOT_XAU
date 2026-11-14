"""Ca thử xác định cho ngưỡng theo mã (_get_symbol_tolerances) và ghép tick thành nến 1 giây."""
import numpy as np


def tol_cases():
    cases = []
    k = 0
    for sym in ("XAUUSDm", "BTCUSDm", "xauusd", "btcusd", "", None, "EURUSD"):
        for (bid, ask) in ((None, None), (2000.0, 2000.24), (65000.0, 65006.4), (100.0, 99.0), (0.0, 0.5)):
            for (stops, pt) in ((None, None), (0, 0.01), (30, 0.01), (500, 0.001), (None, 0)):
                for avail in (True, False):
                    cases.append(dict(name=f"{sym}/{bid}/{stops}/{pt}/{avail}#{k}", symbol=sym, bid=bid, ask=ask, stops=stops, point=pt, avail=avail,
                                      tick_arg=None))
                    k += 1
    return cases


def tick_cases():
    rng = np.random.default_rng(55)
    dt = np.dtype([("time", "i8"), ("bid", "f8"), ("ask", "f8")])
    out = [("rong", np.zeros(0, dtype=dt))]
    for k in range(25):
        n = int(rng.integers(1, 400))
        t = np.sort(rng.integers(1_700_000_000, 1_700_000_000 + int(rng.integers(1, 120)), n))
        bid = 2000 + np.cumsum(rng.normal(0, 0.1, n))
        ask = bid + np.abs(rng.normal(0.2, 0.05, n))
        arr = np.zeros(n, dtype=dt)
        arr["time"], arr["bid"], arr["ask"] = t, bid, ask
        out.append((f"t{k}", arr))
    return out
