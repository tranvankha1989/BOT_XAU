"""
tools/bt_ai_cache.py
────────────────────
Cache kịch bản AI THÔ (đầu ra của _fast_predict_scenarios) cho backtest dòng lệnh.

- Chỉ run_backtest.py gắn cache này vào app (app._bt_scenario_cache). Bot live KHÔNG dùng (thuộc tính không tồn tại -> None).
- Khóa cache = hash của: phiên bản định dạng, vân tay model (trọng số model + tokenizer), thiết bị, max_context,
  toàn bộ nến đầu vào (OHLCV + thời gian), thời gian nến dự báo, số kịch bản, độ dài dự báo, nhiệt độ, top_p, top_k.
  Đổi bất cứ thứ gì trong đó -> khóa khác -> chạy lại AI (không bao giờ dùng nhầm).
- Mỗi khóa một file .npz, ghi atomic (file tạm rồi os.replace). File hỏng/đọc lỗi = coi như không có (chạy lại AI).
- Không gọi MT5, không đọc/ghi gì ngoài thư mục cache.
"""

import hashlib
import os
import threading

import numpy as np
import pandas as pd

CACHE_FORMAT_VERSION = 1
_COLS = ["open", "high", "low", "close", "volume", "amount"]


def predictor_fingerprint(predictor) -> str:
    """Vân tay trọng số model + tokenizer (sha256 theo tên và byte từng tensor) + thiết bị + max_context."""
    h = hashlib.sha256()
    for part in (predictor.model, predictor.tokenizer):
        sd = part.state_dict()
        for name in sorted(sd.keys()):
            t = sd[name].detach().cpu().contiguous()
            h.update(name.encode("utf-8"))
            h.update(str(t.dtype).encode("utf-8"))
            h.update(str(tuple(t.shape)).encode("utf-8"))
            h.update(t.numpy().tobytes())
    h.update(str(getattr(predictor, "device", "")).encode("utf-8"))
    h.update(str(getattr(predictor, "max_context", "")).encode("utf-8"))
    return h.hexdigest()


class ScenarioCache:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._fp = None
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.write_errors = 0
        self.read_errors = 0

    def _model_fp(self, predictor) -> str:
        if self._fp is None:
            self._fp = predictor_fingerprint(predictor)
        return self._fp

    def make_key(self, predictor, sub_df, y_ts, num_scenarios, temperature, top_p, pred_len=6, top_k=0) -> str:
        h = hashlib.sha256()
        h.update(f"v{CACHE_FORMAT_VERSION}|{self._model_fp(predictor)}".encode("utf-8"))
        h.update(f"|n={int(num_scenarios)}|L={int(pred_len)}|T={float(temperature)!r}|p={float(top_p)!r}|k={int(top_k)}".encode("utf-8"))
        ohlcv = sub_df[["open", "high", "low", "close", "volume"]].to_numpy(dtype=np.float64)
        h.update(np.ascontiguousarray(ohlcv).tobytes())
        h.update(np.ascontiguousarray(pd.to_datetime(sub_df["timestamps"]).to_numpy("datetime64[ns]").astype(np.int64)).tobytes())
        h.update(np.ascontiguousarray(pd.to_datetime(pd.Series(y_ts)).to_numpy("datetime64[ns]").astype(np.int64)).tobytes())
        return h.hexdigest()

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key + ".npz")

    def get(self, key: str, y_ts):
        """Trả list DataFrame y hệt _fast_predict_scenarios, hoặc None nếu chưa có / file hỏng."""
        path = self._path(key)
        if not os.path.exists(path):
            with self._lock:
                self.misses += 1
            return None
        try:
            with np.load(path, allow_pickle=False) as z:
                vals = z["vals"]
                lps = z["log_probs"]
            ts_vals = pd.Series(y_ts).values
            out = []
            for b in range(vals.shape[0]):
                p_df = pd.DataFrame(vals[b].copy(), columns=_COLS)
                p_df.insert(0, "timestamps", ts_vals)
                p_df.attrs["log_prob"] = float(lps[b])
                out.append(p_df)
            with self._lock:
                self.hits += 1
            return out
        except Exception:
            with self._lock:
                self.read_errors += 1
                self.misses += 1
            return None

    def put(self, key: str, scenarios) -> None:
        try:
            vals = np.stack([s[_COLS].to_numpy() for s in scenarios])
            lps = np.array([float(s.attrs.get("log_prob", 0.0)) for s in scenarios], dtype=np.float64)
            path = self._path(key)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                np.savez(f, vals=vals, log_probs=lps)
            os.replace(tmp, path)
        except Exception:
            with self._lock:
                self.write_errors += 1

    def summary(self) -> str:
        tot = self.hits + self.misses
        return (f"AI cache: {self.hits} trúng / {self.misses} trượt (tổng {tot}) | lỗi đọc {self.read_errors}, lỗi ghi {self.write_errors} | {self.cache_dir}")
