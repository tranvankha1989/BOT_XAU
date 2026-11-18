"""
tools/live_replay.py
────────────────────
BỘ PHÁT LẠI (REPLAY) CHẠY CHÍNH CODE BOT LIVE TRÊN DỮ LIỆU TICK QUÁ KHỨ.

Khác tools/run_backtest.py (mô phỏng riêng, nhiều phần khác live): ở đây KHÔNG viết lại luật giao dịch.
Bộ phát lại gọi đúng các hàm mà bot live gọi, theo đúng nhịp:
  - vòng nến (_auto_loop_worker): mỗi 2 giây xét nến khung vào lệnh / khung phân tích vừa đóng;
    khi nến phân tích đóng -> chạy _run_analysis_worker (AI, lọc, Entry/SL/TP, vùng canh) sau độ trễ phân tích như live;
  - vòng tick (_realtime_tick_loop, nhánh chạy ngầm của profile): mỗi 1 giây
    _update_open_positions_and_manage -> _check_daily_profit_loss_limits -> _check_pending_pullback_order -> _pb_heartbeat.
Mọi thông số trong file cấu hình đều có hiệu lực vì code live tự đọc cấu hình.

Thay thế duy nhất là môi trường:
  - mt5 -> FakeBroker: tick + nến lịch sử thật (đọc 1 lần từ MT5, CHỈ ĐỌC), nến đang chạy dựng từ tick <= hiện tại,
    nhận lệnh/sửa SL-TP/đóng lệnh, khớp SL/TP theo TỪNG tick, lịch sử deals/orders như MT5;
  - time.time / time.monotonic / datetime.now -> đồng hồ phát lại; time.sleep -> bỏ qua;
  - luồng nền (threading.Thread) -> chạy đồng bộ; giao diện/Telegram -> bỏ qua.
Giới hạn (không tái tạo được): độ trễ mạng/khớp lệnh (lệnh khớp ở tick tại thời điểm quyết định), phí qua đêm,
từ chối lệnh của sàn thật. AI có yếu tố ngẫu nhiên: cố định seed + cache kịch bản để chạy lại ra cùng kết quả.

Ví dụ:
    python tools/live_replay.py --profile BTCUSD --config "Cấu Hình 1/1.json" --start 2026-11-10 --end 2026-11-14
"""

import argparse
import csv
import math
import os
import sys
import threading
import time as _real_time
import types
from datetime import datetime, timedelta, timezone

import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT_DIR, TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

VN_TZ = timezone(timedelta(hours=7))
UTC = timezone.utc
TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}
RATE_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                       ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])


# ───────────────────────── Đồng hồ ─────────────────────────
class SimClock:
    """Giờ phát lại = epoch UTC (giờ server Exness = UTC, khớp cách bot đổi d.time sang giờ VN)."""

    def __init__(self, now: float = 0.0):
        self.now = float(now)

    def time(self):
        return self.now

    def monotonic(self):
        return self.now


class FakeTimeModule:
    """Thay module time trong bot: time/monotonic theo đồng hồ phát lại, sleep bỏ qua, phần còn lại dùng time thật."""

    def __init__(self, clock: SimClock):
        self._clock = clock

    def time(self):
        return self._clock.now

    def monotonic(self):
        return self._clock.now

    def sleep(self, _s):
        return None

    def __getattr__(self, name):
        return getattr(_real_time, name)


def make_fake_datetime(clock: SimClock):
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock.now, tz) if tz is not None else datetime.fromtimestamp(clock.now)
    return FakeDateTime


class SyncThread:
    """threading.Thread chạy đồng bộ (đóng/sửa lệnh, chốt rổ... chạy ngay trong vòng phát lại)."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target, self._args, self._kwargs = target, tuple(args or ()), dict(kwargs or {})

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, *a, **k):
        return None

    def is_alive(self):
        return False


class FakeThreadingModule:
    def __init__(self):
        self.Thread = SyncThread

    def __getattr__(self, name):
        return getattr(threading, name)


class Dummy:
    """Widget giả: nhận mọi lời gọi/thuộc tính (cây lệnh, nhãn tổng kết...)."""

    def __getattr__(self, name):
        return Dummy()

    def __call__(self, *a, **k):
        return Dummy()

    def selection(self):
        return ()

    def get(self):
        return ""

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return False


# ───────────────────────── Sàn giả lập ─────────────────────────
class FakeBroker:
    """Thay module MetaTrader5 cho bot. Dữ liệu: ticks (time_msc UTC, bid, ask) + nến lịch sử theo khung (mảng RATE_DTYPE)."""

    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30, TIMEFRAME_H1 = 1, 5, 15, 30, 16385
    _TF_ENUM_SEC = {1: 60, 5: 300, 15: 900, 30: 1800, 16385: 3600}
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT = 0, 1, 2
    DEAL_REASON_EXPERT, DEAL_REASON_SL, DEAL_REASON_TP = 3, 4, 5
    COPY_TICKS_ALL = -1
    TRADE_RETCODE_DONE, TRADE_RETCODE_INVALID_STOPS, TRADE_RETCODE_INVALID_FILL = 10009, 10016, 10030
    TRADE_RETCODE_INVALID_VOLUME, TRADE_RETCODE_POSITION_CLOSED = 10014, 10036
    SYMBOL_FILLING_FOK, SYMBOL_FILLING_IOC = 1, 2

    def __init__(self, symbol: str, sym_info: dict, ticks_ms, bids, asks, rates_by_tf: dict, clock: SimClock,
                 capital: float = 1000.0):
        self.symbol = symbol
        self.sym_info = dict(sym_info)
        self.contract = float(self.sym_info.get("trade_contract_size", 1.0) or 1.0)
        self.digits = int(self.sym_info.get("digits", 2))
        self.ms = np.asarray(ticks_ms, dtype=np.int64)
        self.bid = np.asarray(bids, dtype=float)
        self.ask = np.asarray(asks, dtype=float)
        self.rates = rates_by_tf      # {tf_sec: structured array RATE_DTYPE, sắp theo time}
        self.clock = clock
        self.capital = float(capital)
        self.positions = []
        self.deals = []
        self.orders = []
        self.closed = []              # tổng kết từng lệnh đã đóng (báo cáo)
        self._next_id = 100000
        self.rejects = []

    # ── tiện ích ──
    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def _idx(self, t_sec: float) -> int:
        return int(np.searchsorted(self.ms, int(round(t_sec * 1000.0)), side="right")) - 1

    def _cur(self):
        i = self._idx(self.clock.now)
        if i < 0:
            return None
        return i, float(self.bid[i]), float(self.ask[i]), int(self.ms[i])

    def _profit(self, ptype, vol, p_open, p_close):
        d = (p_close - p_open) if ptype == self.POSITION_TYPE_BUY else (p_open - p_close)
        return round(d * vol * self.contract, 2)

    # ── API MT5 ──
    def initialize(self, *a, **k):
        return True

    def shutdown(self):
        return None

    def symbol_select(self, *a, **k):
        return True

    def last_error(self):
        return (1, "Success")

    def terminal_info(self):
        return types.SimpleNamespace(connected=True, trade_allowed=True, tradeapi_disabled=False)

    def account_info(self):
        realized = sum(float(d.profit) for d in self.deals)
        floating = sum(float(p.profit) for p in self.positions)
        bal = self.capital + realized
        return types.SimpleNamespace(login=0, trade_mode=0, balance=bal, equity=bal + floating, margin_free=bal + floating,
                                     currency="USD", trade_allowed=True, trade_expert=True, leverage=200)

    def symbol_info(self, symbol=None):
        return types.SimpleNamespace(**self.sym_info)

    def symbol_info_tick(self, symbol=None):
        c = self._cur()
        if c is None:
            return None
        _, b, a, ms = c
        return types.SimpleNamespace(time=ms // 1000, time_msc=ms, bid=b, ask=a, last=0.0, volume=0, flags=0)

    def _rates_until_now(self, tf_sec, count, start_pos):
        arr = self.rates.get(tf_sec)
        now = self.clock.now
        if arr is None or len(arr) == 0:
            return None
        n_closed = int(np.searchsorted(arr["time"] + tf_sec, now, side="right"))
        closed = arr[max(0, n_closed - count - start_pos - 1):n_closed]
        # Như MT5 thật: vị trí 0 = nến MỚI NHẤT đang tồn tại. Nến đang chạy chỉ tồn tại khi đã có tick trong chu kỳ;
        # chưa có tick (giờ nghỉ của vàng, cuối tuần, đầu nến chưa có giá) thì vị trí 0 là nến đã đóng gần nhất
        # (trước đây luôn coi vị trí 0 là nến đang chạy -> giờ nghỉ phát hiện "nến mới" lặp vô hạn).
        out = closed
        fs = int(now // tf_sec) * tf_sec
        i0 = int(np.searchsorted(self.ms, fs * 1000, side="left"))
        i1 = self._idx(now) + 1
        if i1 > i0:
            b = self.bid[i0:i1]
            forming = np.array([(fs, b[0], b.max(), b.min(), b[-1], i1 - i0, 0, 0)], dtype=RATE_DTYPE)
            out = np.concatenate([closed, forming])
        end = len(out) - start_pos
        return out[max(0, end - count):max(0, end)] if count > 0 else out[:max(0, end)]

    def copy_rates_from_pos(self, symbol, tf, start_pos, count):
        return self._rates_until_now(self._TF_ENUM_SEC.get(tf, 300), int(count), int(start_pos))

    def copy_rates_range(self, symbol, tf, date_from, date_to):
        tf_sec = self._TF_ENUM_SEC.get(tf, 300)
        arr = self.rates.get(tf_sec)
        if arr is None:
            return None
        t0, t1 = _ts(date_from), min(_ts(date_to), self.clock.now)
        sel = arr[(arr["time"] >= t0) & (arr["time"] + tf_sec <= t1 + 1e-9)]
        return sel

    def copy_ticks_range(self, *a, **k):
        return None

    def positions_get(self, symbol=None, ticket=None, group=None, **kw):
        res = list(self.positions)
        if symbol is not None:
            res = [p for p in res if p.symbol == symbol]
        if ticket is not None:
            res = [p for p in res if p.ticket == ticket]
        return tuple(res)

    def order_check(self, request):
        return types.SimpleNamespace(retcode=0, comment="Done", request=request)

    def order_calc_profit(self, otype, symbol, volume, p_open, p_close):
        return self._profit(self.POSITION_TYPE_BUY if otype == self.ORDER_TYPE_BUY else self.POSITION_TYPE_SELL,
                            float(volume), float(p_open), float(p_close))

    def _stops_ok(self, ptype, sl, tp, bid, ask):
        if ptype == self.POSITION_TYPE_BUY:
            return (sl == 0 or sl < bid) and (tp == 0 or tp > bid)
        return (sl == 0 or sl > ask) and (tp == 0 or tp < ask)

    def _result(self, retcode, comment, **kw):
        return types.SimpleNamespace(retcode=retcode, comment=comment, order=kw.get("order", 0), deal=kw.get("deal", 0),
                                     price=kw.get("price", 0.0), volume=kw.get("volume", 0.0), request_id=0)

    def order_send(self, req):
        c = self._cur()
        if c is None:
            return self._result(10018, "Market closed")
        _, bid, ask, ms = c
        act = req.get("action")
        if act == self.TRADE_ACTION_SLTP:
            pos = next((p for p in self.positions if p.ticket == req.get("position")), None)
            if pos is None:
                return self._result(self.TRADE_RETCODE_POSITION_CLOSED, "Position doesn't exist")
            sl, tp = float(req.get("sl", 0.0) or 0.0), float(req.get("tp", 0.0) or 0.0)
            if not self._stops_ok(pos.type, sl, tp, bid, ask):
                self.rejects.append((self.clock.now, "SLTP", pos.ticket, sl, tp, bid, ask))
                return self._result(self.TRADE_RETCODE_INVALID_STOPS, "Invalid stops")
            pos.sl, pos.tp, pos.time_update = sl, tp, ms // 1000
            return self._result(self.TRADE_RETCODE_DONE, "Request executed", order=self._new_id())
        if act != self.TRADE_ACTION_DEAL:
            return self._result(10013, "Invalid request")
        if req.get("position"):
            pos = next((p for p in self.positions if p.ticket == req.get("position")), None)
            if pos is None:
                return self._result(self.TRADE_RETCODE_POSITION_CLOSED, "Position doesn't exist")
            px = bid if pos.type == self.POSITION_TYPE_BUY else ask
            vol = min(float(req.get("volume", pos.volume)), pos.volume)
            deal = self._close(pos, px, ms, self.DEAL_REASON_EXPERT, vol, "bot")
            return self._result(self.TRADE_RETCODE_DONE, "Request executed", order=deal.order, deal=deal.ticket, price=px, volume=vol)
        otype = req.get("type")
        ptype = self.POSITION_TYPE_BUY if otype == self.ORDER_TYPE_BUY else self.POSITION_TYPE_SELL
        px = ask if ptype == self.POSITION_TYPE_BUY else bid
        sl, tp = float(req.get("sl", 0.0) or 0.0), float(req.get("tp", 0.0) or 0.0)
        if not self._stops_ok(ptype, sl, tp, bid, ask):
            self.rejects.append((self.clock.now, "OPEN", None, sl, tp, bid, ask))
            return self._result(self.TRADE_RETCODE_INVALID_STOPS, "Invalid stops")
        vol = float(req.get("volume", 0.0))
        oid = self._new_id()
        pos = types.SimpleNamespace(
            ticket=oid, identifier=oid, time=ms // 1000, time_msc=ms, time_update=ms // 1000, type=ptype,
            magic=int(req.get("magic", 0)), reason=self.DEAL_REASON_EXPERT, volume=vol, price_open=px, sl=sl, tp=tp,
            price_current=px, swap=0.0, profit=0.0, symbol=req.get("symbol", self.symbol), comment=str(req.get("comment", "")),
            external_id="", _sl0=sl, _tp0=tp)
        self.positions.append(pos)
        did = self._new_id()
        self.deals.append(self._deal(did, oid, ms, 0 if ptype == self.POSITION_TYPE_BUY else 1, self.DEAL_ENTRY_IN,
                                     pos.magic, oid, self.DEAL_REASON_EXPERT, vol, px, 0.0, pos.symbol, pos.comment))
        self.orders.append(self._order(oid, ms, otype, pos.magic, oid, vol, px, sl, tp, pos.symbol, pos.comment))
        return self._result(self.TRADE_RETCODE_DONE, "Request executed", order=oid, deal=did, price=px, volume=vol)

    def _deal(self, did, oid, ms, dtype, entry, magic, pid, reason, vol, px, profit, sym, comment):
        return types.SimpleNamespace(ticket=did, order=oid, time=ms // 1000, time_msc=ms, type=dtype, entry=entry, magic=magic,
                                     position_id=pid, reason=reason, volume=vol, price=px, commission=0.0, swap=0.0,
                                     profit=profit, fee=0.0, symbol=sym, comment=comment, external_id="")

    def _order(self, oid, ms, otype, magic, pid, vol, px, sl, tp, sym, comment):
        return types.SimpleNamespace(ticket=oid, time_setup=ms // 1000, time_setup_msc=ms, time_done=ms // 1000,
                                     time_done_msc=ms, type=otype, state=4, magic=magic, position_id=pid,
                                     volume_initial=vol, volume_current=0.0, price_open=px, sl=sl, tp=tp,
                                     price_current=px, symbol=sym, comment=comment, reason=self.DEAL_REASON_EXPERT)

    def _close(self, pos, px, ms, reason, vol, how):
        profit = self._profit(pos.type, vol, pos.price_open, px)
        oid, did = self._new_id(), self._new_id()
        dtype = 1 if pos.type == self.POSITION_TYPE_BUY else 0
        deal = self._deal(did, oid, ms, dtype, self.DEAL_ENTRY_OUT, pos.magic, pos.ticket, reason, vol, px, profit, pos.symbol, pos.comment)
        self.deals.append(deal)
        self.orders.append(self._order(oid, ms, 1 if pos.type == self.POSITION_TYPE_BUY else 0, pos.magic, pos.ticket,
                                       vol, px, 0.0, 0.0, pos.symbol, pos.comment))
        rsn = {self.DEAL_REASON_SL: "SL", self.DEAL_REASON_TP: "TP"}.get(reason, "Bot đóng")
        self.closed.append({"ticket": pos.ticket, "type": "BUY" if pos.type == self.POSITION_TYPE_BUY else "SELL",
                            "open_ms": pos.time_msc, "close_ms": ms, "price_open": pos.price_open, "price_close": px,
                            "sl0": pos._sl0, "tp0": pos._tp0, "sl_last": pos.sl, "volume": vol, "profit": profit,
                            "reason": rsn})
        if vol >= pos.volume - 1e-9:
            self.positions.remove(pos)
        else:
            pos.volume = round(pos.volume - vol, 8)
        return deal

    def history_deals_get(self, *args, **kw):
        if "position" in kw:
            return tuple(d for d in self.deals if d.position_id == kw["position"])
        if "ticket" in kw:
            return tuple(d for d in self.deals if d.ticket == kw["ticket"])
        if len(args) >= 2:
            t0, t1 = _ts(args[0]) - 86400, _ts(args[1]) + 86400
            return tuple(d for d in self.deals if t0 <= d.time <= t1)
        return tuple(self.deals)

    def history_orders_get(self, *args, **kw):
        if "position" in kw:
            return tuple(o for o in self.orders if o.position_id == kw["position"])
        if "ticket" in kw:
            return tuple(o for o in self.orders if o.ticket == kw["ticket"])
        if len(args) >= 2:
            t0, t1 = _ts(args[0]) - 86400, _ts(args[1]) + 86400
            return tuple(o for o in self.orders if t0 <= o.time_setup <= t1)
        return tuple(self.orders)

    # ── khớp SL/TP phía sàn ──
    def process_ticks(self, t_from: float, t_to: float):
        """Duyệt tick trong (t_from, t_to]: SL/TP chạm theo giá đóng (BUY: bid, SELL: ask), đóng tại giá tick chạm."""
        if not self.positions:
            return
        i0 = int(np.searchsorted(self.ms, int(round(t_from * 1000.0)), side="right"))
        i1 = int(np.searchsorted(self.ms, int(round(t_to * 1000.0)), side="right"))
        while self.positions and i0 < i1:
            best = None
            for p in self.positions:
                if p.type == self.POSITION_TYPE_BUY:
                    px = self.bid[i0:i1]
                    hit_sl = (px <= p.sl) if p.sl > 0 else np.zeros(len(px), bool)
                    hit_tp = (px >= p.tp) if p.tp > 0 else np.zeros(len(px), bool)
                else:
                    px = self.ask[i0:i1]
                    hit_sl = (px >= p.sl) if p.sl > 0 else np.zeros(len(px), bool)
                    hit_tp = (px <= p.tp) if p.tp > 0 else np.zeros(len(px), bool)
                hit = hit_sl | hit_tp
                if hit.any():
                    k = int(np.argmax(hit))
                    if best is None or k < best[0]:
                        best = (k, p, bool(hit_sl[k]))
            if best is None:
                break
            k, p, is_sl = best
            j = i0 + k
            px = float(self.bid[j]) if p.type == self.POSITION_TYPE_BUY else float(self.ask[j])
            self._close(p, px, int(self.ms[j]), self.DEAL_REASON_SL if is_sl else self.DEAL_REASON_TP, p.volume, "broker")
            i0 = j + 1

    def mark_to_market(self):
        c = self._cur()
        if c is None:
            return
        _, bid, ask, _ = c
        for p in self.positions:
            p.price_current = bid if p.type == self.POSITION_TYPE_BUY else ask
            p.profit = self._profit(p.type, p.volume, p.price_open, p.price_current)


def _ts(x) -> float:
    if isinstance(x, datetime):
        return x.timestamp()
    return float(x)


# ───────────────────────── Dữ liệu từ MT5 thật (CHỈ ĐỌC) ─────────────────────────
def load_market_data(symbol: str, start_utc: float, end_utc: float, warmup_days: int = 12, cache_dir: str = None):
    """Đọc tick + nến lịch sử từ MT5 thật (chỉ đọc), có cache npz. Trả (sym_info dict, ms, bid, ask, rates_by_tf)."""
    import MetaTrader5 as real_mt5
    key = f"{symbol}_{int(start_utc)}_{int(end_utc)}_{warmup_days}"
    path = os.path.join(cache_dir, f"replay_{key}.npz") if cache_dir else None
    if path and os.path.exists(path):
        z = np.load(path, allow_pickle=True)
        rates = {int(k[2:]): z[k] for k in z.files if k.startswith("r_")}
        return dict(z["sym_info"].item()), z["ms"], z["bid"], z["ask"], rates
    if not real_mt5.initialize():
        raise RuntimeError(f"Không kết nối được MT5 để đọc dữ liệu: {real_mt5.last_error()}")
    try:
        real_mt5.symbol_select(symbol, True)
        si = real_mt5.symbol_info(symbol)
        if si is None:
            raise RuntimeError(f"Không có mã {symbol} trên MT5")
        sym_info = {k: getattr(si, k) for k in ("digits", "point", "trade_stops_level", "trade_freeze_level",
                                                 "trade_contract_size", "volume_min", "volume_max", "volume_step",
                                                 "filling_mode", "name", "spread") if hasattr(si, k)}
        t0 = datetime.fromtimestamp(start_utc - warmup_days * 86400, UTC)
        t1 = datetime.fromtimestamp(end_utc + 3600, UTC)
        rates = {}
        enums = {60: real_mt5.TIMEFRAME_M1, 300: real_mt5.TIMEFRAME_M5, 900: real_mt5.TIMEFRAME_M15,
                 1800: real_mt5.TIMEFRAME_M30, 3600: real_mt5.TIMEFRAME_H1}
        for sec, en in enums.items():
            r = real_mt5.copy_rates_range(symbol, en, t0, t1)
            rates[sec] = np.array([tuple(x[n] for n in RATE_DTYPE.names) for x in r], dtype=RATE_DTYPE) if r is not None else np.zeros(0, RATE_DTYPE)
        ms_l, bid_l, ask_l = [], [], []
        day = start_utc - 86400  # đệm 1 ngày tick trước start (nến đang chạy đầu kỳ)
        while day < end_utc + 3600:
            d_end = min(day + 86400, end_utc + 3600)
            tk = real_mt5.copy_ticks_range(symbol, datetime.fromtimestamp(day, UTC), datetime.fromtimestamp(d_end, UTC), real_mt5.COPY_TICKS_ALL)
            if tk is not None and len(tk):
                ms_l.append(tk["time_msc"].astype(np.int64))
                bid_l.append(tk["bid"].astype(float))
                ask_l.append(tk["ask"].astype(float))
            day = d_end
    finally:
        real_mt5.shutdown()
    if not ms_l:
        raise RuntimeError("Không có tick trong khoảng ngày này")
    ms, bid, ask = np.concatenate(ms_l), np.concatenate(bid_l), np.concatenate(ask_l)
    order = np.argsort(ms, kind="stable")
    ms, bid, ask = ms[order], bid[order], ask[order]
    ok = (bid > 0) & (ask > 0)
    ms, bid, ask = ms[ok], bid[ok], ask[ok]
    if path:
        os.makedirs(cache_dir, exist_ok=True)
        np.savez_compressed(path, sym_info=np.array(sym_info, dtype=object), ms=ms, bid=bid, ask=ask,
                            **{f"r_{k}": v for k, v in rates.items()})
    return sym_info, ms, bid, ask, rates


# ───────────────────────── Dựng bot ─────────────────────────
def build_app(bot_mod, pk: str, cfg: dict, logs: list, errors: list, clock: SimClock):
    """KronosDesktopApp không giao diện với đủ trạng thái như __init__ (phần không phải giao diện).
    Profile phát lại chạy ở nhánh NỀN (active_profile_key = profile còn lại), đúng nhánh live dùng cho tab chạy ngầm."""
    app = object.__new__(bot_mod.KronosDesktopApp)
    other = "XAUUSD" if pk == "BTCUSD" else "BTCUSD"
    D = bot_mod.DEFAULT_PROFILE_SETTINGS

    def _log(msg, *a, **k):
        logs.append((clock.now, str(msg)))

    def _err(*a, **k):
        errors.append((clock.now, " | ".join(str(x) for x in a) + (f" | {k}" if k else "")))

    def _file_log(msg, *a, **k):
        if k.get("is_error"):
            errors.append((clock.now, str(msg)))

    app.__dict__.update(
        root=types.SimpleNamespace(after=lambda *a, **k: None, after_cancel=lambda *a, **k: None),
        lic_mgr=None, app_version="replay", updater=None, local_cache={},
        initial_r_cache={}, _initial_r_saved={}, pending_be_exit_tickets=set(), pending_pullback_order=None,
        daily_limit_locked_date=None, daily_limit_reason="", daily_limit_locked_kind=None, daily_limit_locked_threshold=0.0,
        cached_daily_pnl=0.0, cached_win_rate=0.0, cached_win_count=0, cached_loss_count=0, cached_be_count=0,
        cached_closed_count=0, consec_loss_count=0, cooldown_until=None, last_cooldown_deal_ticket=None,
        basket_cooldown_until=None, fast_sl_cooldown_until=None, last_fast_sl_deal_ticket=None, is_basket_closing=False,
        auto_loop_running=False, is_analyzing=False, last_candle_time=None, last_ana_candle_time=None,
        last_recommendation=None, heartbeat_toggle=False, _loading_settings=False,
        cached_scenarios=None, cached_mean_df=None, cached_action=None, cached_sl=None, cached_tp=None,
        cached_df_hist=None, cached_df_analysis=None, _closed_deals_by_ticket={}, _drag_start_px=None,
        _last_deals_check_ts=0.0, _cached_deals_stats=(0.0, 0, None), _cached_deals_by_sym={}, _cached_closed_deals_map={},
        _cached_pid_magic={}, _pid_magic_backoff={}, _logged_magic_fail_pids=set(),
        _ai_scan_lock=threading.Lock(), _ai_inference_lock=threading.RLock(), _mt5_lock=threading.RLock(),
        _log_file_lock=threading.Lock(), _order_guard_lock=threading.Lock(), _pos_action_in_flight=set(),
        _order_in_flight={"XAUUSD": False, "BTCUSD": False}, _modify_fail_until={}, _modify_fail_log_ts={},
        _last_order_open_mono={"XAUUSD": 0.0, "BTCUSD": 0.0}, _last_order_candle_ts={"XAUUSD": None, "BTCUSD": None},
        _auto_worker_token={"XAUUSD": 0, "BTCUSD": 0}, _last_mt5_order_mono=0.0,
        _ai_queued_profiles=set(), _ai_current_running_pk=None, _app_closing=False,
        _sig_title="", _sig_color="", active_profile_key=other,
        profile_settings={pk: dict(cfg), other: dict(D[other])},
        chart_view_tf_by_profile={"XAUUSD": "M5", "BTCUSD": "M5"},
        _telegram_sent_deal_tickets=set(), _app_session_start_ts=int(clock.now), _bt_cancel_flag=False,
        _is_initial_loading=False, pos_tree=Dummy(), pos_summary_lbl=Dummy(), pos_tree_by_profile={},
        pos_summary_lbl_by_profile={}, pred_tree_by_profile={}, pred_title_by_profile={}, log_text_by_profile={},
        telegram_enabled_var=None, telegram_notify_close_var=None, realtime_chart_var=None,
        # Giao diện / Telegram / tải lần đầu: bỏ qua
        log=_log, log_error=_err, _write_file_log=_file_log, show_toast=lambda *a, **k: None,
        _ui_after=lambda *a, **k: None, _set_loading_stage=lambda *a, **k: None, _update_ui_results=lambda *a, **k: None,
        _start_scan_progress_anim=lambda *a, **k: None, _finish_scan_progress_anim=lambda *a, **k: None,
        _update_bot_status_bar=lambda *a, **k: None, _finish_startup_loading=lambda *a, **k: None,
        _cleanup_memory_and_trim_workingset=lambda *a, **k: None, _update_left_quick_info=lambda *a, **k: None,
        _telegram_notify_order_opened=lambda *a, **k: None, _telegram_notify_order_closed=lambda *a, **k: None,
        _mt5_initialize=lambda *a, **k: True, _save_initial_r_cache=lambda *a, **k: None,
    )
    app.profile_ctx = {pk: app._create_empty_profile_ctx(pk), other: app._create_empty_profile_ctx(other)}
    app.profile_ctx[pk]["auto_loop_running"] = True
    return app


def install_ai_cache(app, cache):
    """Bọc _fast_predict_scenarios bằng cache kịch bản (khóa gồm số nến dự báo, nhiệt độ, top_p, dữ liệu vào)."""
    real = app._fast_predict_scenarios

    def cached(predictor, df_in, x_timestamp, y_timestamp, pred_len=6, num_scenarios=8, T=1.0, top_k=0, top_p=0.9):
        key = None
        try:
            key = cache.make_key(predictor, df_in, y_timestamp, num_scenarios, T, top_p, pred_len=pred_len, top_k=top_k)
            hit = cache.get(key, y_timestamp)
            if hit is not None:
                return hit
        except Exception:
            key = None
        res = real(predictor=predictor, df_in=df_in, x_timestamp=x_timestamp, y_timestamp=y_timestamp, pred_len=pred_len,
                   num_scenarios=num_scenarios, T=T, top_k=top_k, top_p=top_p)
        if res and key is not None:
            try:
                cache.put(key, res)
            except Exception:
                pass
        return res
    app._fast_predict_scenarios = cached


# ───────────────────────── Vòng phát lại ─────────────────────────
class Replayer:
    """Điều phối 2 vòng của live trên đồng hồ phát lại.
    - Vòng nến: mỗi auto_step giây (live: 4 x 0.5s) chạy đúng thân vòng _auto_loop_worker (bước 1-2);
      phân tích AI bắt đầu ngay khi phát hiện nến phân tích mới và KẾT THÚC sau ai_latency giây (live ~8s):
      trong lúc đó vùng canh cũ vẫn hoạt động như live.
    - Vòng tick: mỗi 1 giây chạy đúng nhánh chạy ngầm của _realtime_tick_loop.
    Khi không có lệnh mở, không có vùng canh và không có phân tích đang chạy, nhảy thẳng tới mốc vòng nến kế tiếp
    (các hàm vòng tick khi đó không làm gì)."""

    def __init__(self, bot_mod, app, broker: FakeBroker, clock: SimClock, pk: str, cfg: dict,
                 ai_latency: float = 8.5, auto_step: float = 2.0, detect_delay: float = 1.6, progress=None):
        self.bot, self.app, self.broker, self.clock, self.pk, self.cfg = bot_mod, app, broker, clock, pk, cfg
        self.ai_latency, self.auto_step, self.detect_delay = float(ai_latency), float(auto_step), float(detect_delay)
        self.symbol = str(cfg.get("symbol", "")).strip()
        self.scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
        self.ana_tf = str(cfg.get("analysis_tf", self.scalp_tf)).strip() or self.scalp_tf
        self.analysis_due = None
        self.analysis_ts = None
        self.progress = progress
        self.n_analyses = 0

    def _ctx(self):
        return self.app.profile_ctx[self.pk]

    def auto_loop_step(self):
        """Thân vòng _auto_loop_worker (mt5_scalping_bot.py ~10333-10367), không có sleep."""
        app, pk, ctx = self.app, self.pk, self._ctx()
        try:
            df_scalp_chk = app.fetch_mt5_candles(self.symbol, self.scalp_tf, 2, include_forming=False)
            if len(df_scalp_chk) > 0:
                ctx["last_candle_time"] = df_scalp_chk["timestamps"].iloc[-1]
            df_check = df_scalp_chk if self.ana_tf == self.scalp_tf else app.fetch_mt5_candles(self.symbol, self.ana_tf, 3, include_forming=False)
            latest_ana_time = df_check["timestamps"].iloc[-1]
            if ctx.get("last_ana_candle_time") != latest_ana_time and not ctx.get("is_analyzing", False) and self.analysis_due is None:
                ctx["last_ana_candle_time"] = latest_ana_time
                app.log(f"🔔 [{pk} - {self.symbol}] Nến phân tích {self.ana_tf} mới vừa đóng ({latest_ana_time}). Đưa vào hàng đợi phân tích AI...")
                self.request_scan(pk, "AUTO_CANDLE", latest_ana_time)  # như live gọi enqueue_ai_scan (gồm hủy lệnh chờ cũ)
        except Exception as e:
            app.log_error(f"AUTO_CANDLE_WATCH_{pk}", e)

    def request_scan(self, profile_key=None, reason="MANUAL", closed_candle_ts=None):
        """Thay enqueue_ai_scan của live (hàng đợi + luồng tiêu thụ): xếp lịch phân tích sau ai_latency giây."""
        if (profile_key or self.pk) != self.pk or self.analysis_due is not None:
            return False
        self._ctx()["is_analyzing"] = True
        self.app._cancel_pending_for_new_scan(self.pk)  # như enqueue_ai_scan của live: hủy lệnh chờ cũ ngay khi xếp lịch
        self.analysis_due = self.clock.now + self.ai_latency
        self.analysis_ts = closed_candle_ts
        return True

    def run_analysis(self):
        ts, self.analysis_due, self.analysis_ts = self.analysis_ts, None, None
        self.n_analyses += 1
        self.app._run_analysis_worker(profile_key=self.pk, reason="AUTO_CANDLE", closed_candle_ts=ts)
        self._ctx()["is_analyzing"] = False

    def tick_loop_step(self):
        """Nhánh chạy ngầm của _realtime_tick_loop (mt5_scalping_bot.py ~7252-7262)."""
        app, pk, sym, mt5 = self.app, self.pk, self.symbol, self.broker
        try:
            tick = mt5.symbol_info_tick(sym)
            _, profit = app._update_open_positions_and_manage(sym, profile_key=pk)
            app._check_daily_profit_loss_limits(profit, tick, profile_key=pk)
            if tick is not None:
                app._check_pending_pullback_order(sym, tick, profile_key=pk)
                app._pb_heartbeat(pk, sym, tick)
        except Exception as e:
            app.log_error("REALTIME_TICK_LOOP", e)

    def _busy(self):
        ctx = self._ctx()
        return bool(self.broker.positions) or ctx.get("pending_pullback_order") is not None or self.analysis_due is not None

    def run(self, start_utc: float, end_utc: float):
        """Vòng theo sự kiện: vòng tick mỗi 1s, vòng nến mỗi auto_step giây (lệch detect_delay so với mốc đóng nến như live),
        kết thúc phân tích AI tại analysis_due. Sự kiện cùng thời điểm: AI xong -> vòng nến -> vòng tick."""
        ana_sec = TF_SEC.get(self.ana_tf, 900)
        inf = float("inf")
        t_tick = float(start_utc)
        t_auto = math.floor(start_utc / ana_sec) * ana_sec + self.detect_delay
        while t_auto < start_utc:
            t_auto += self.auto_step
        last = float(start_utc)
        last_pct = -1
        eps = 1e-6
        while True:
            t = min(t_tick, t_auto, self.analysis_due if self.analysis_due is not None else inf)
            if t > end_utc:
                break
            self.broker.process_ticks(last, t)
            self.clock.now = t
            self.broker.mark_to_market()
            if self.analysis_due is not None and t >= self.analysis_due - eps:
                self.run_analysis()
                self.broker.mark_to_market()
            if t >= t_auto - eps:
                self.auto_loop_step()
                t_auto += self.auto_step
            if t >= t_tick - eps:
                self.tick_loop_step()
                t_tick += 1.0
            last = t
            if self.progress:
                pct = int((t - start_utc) * 100 / max(end_utc - start_utc, 1))
                if pct != last_pct and pct % 5 == 0:
                    last_pct = pct
                    self.progress(pct, t)
            if not self._busy():
                # Nhàn rỗi (không lệnh mở, không vùng canh, không phân tích): các hàm vòng tick không làm gì ->
                # nhảy tới lần vòng nến phát hiện nến phân tích kế tiếp (mốc đóng nến + detect_delay)
                nxt = (math.floor(t / ana_sec) + 1) * ana_sec + self.detect_delay
                if nxt > t_auto:
                    t_auto = nxt
                    t_tick = nxt
        self.broker.process_ticks(last, end_utc)


# ───────────────────────── Báo cáo ─────────────────────────
def simulate(bot_mod, pk: str, cfg: dict, symbol: str, start_utc: float, end_utc: float, capital: float = 1000.0,
             seed: int = 1, ai_latency: float = 8.5, detect_delay: float = 1.6, use_cache: bool = True, progress=None) -> dict:
    """Phát lại CHÍNH code bot live trên tick MT5 (chỉ đọc). cfg đã được nạp và ghi đè (rb.load_strategy_cfg + --set).
    Trả dict: closed (lệnh đã đóng, có cả lệnh đóng cuối kỳ), rejects, logs, errors, n_analyses, cache, end_utc.
    progress(pct, t_sec, n_closed, n_analyses, cache) được gọi định kỳ nếu có."""
    import trading_modules.trading_window as tw_mod
    from bt_ai_cache import ScenarioCache
    cfg["engine"] = "Chạy trực tiếp máy Local"       # AI chạy trên máy này (cùng mô hình)
    cfg["telegram_enabled"] = False
    cfg["symbol"] = symbol
    cache_root = os.path.join(bot_mod.SETTINGS_DIR, "replay_cache")
    print("[*] Đang đọc tick + nến từ MT5 (chỉ đọc, có cache)...")
    sym_info, ms, bid, ask, rates = load_market_data(symbol, start_utc, end_utc, cache_dir=cache_root)
    print(f"[*] Đã có {len(ms):,} tick, nến: " + ", ".join(f"{k}s={len(v)}" for k, v in sorted(rates.items())))
    last_tick_s = float(ms[-1]) / 1000.0
    if last_tick_s < end_utc:
        print(f"[*] Dữ liệu tick chỉ tới {vn(last_tick_s, is_ms=False)} -> dừng phát lại tại đó")
        end_utc = last_tick_s

    clock = SimClock(start_utc)
    broker = FakeBroker(symbol, sym_info, ms, bid, ask, rates, clock, capital=capital)
    logs, errors = [], []
    app = build_app(bot_mod, pk, cfg, logs, errors, clock)
    app._trace_run = "replay"  # debug_trace (nếu bật) ghi vào thư mục riêng, không lẫn log live
    app._trace_dir = os.path.join(bot_mod.SETTINGS_DIR, "replays", "trace")
    cache = ScenarioCache(os.path.join(bot_mod.SETTINGS_DIR, "bt_ai_cache")) if use_cache else None
    if cache is not None:
        install_ai_cache(app, cache)

    from unittest import mock
    patches = [
        mock.patch.object(bot_mod, "mt5", broker), mock.patch.object(bot_mod, "MT5_AVAILABLE", True),
        mock.patch.object(bot_mod, "time", FakeTimeModule(clock)),
        mock.patch.object(bot_mod, "datetime", make_fake_datetime(clock)),
        mock.patch.object(tw_mod, "datetime", make_fake_datetime(clock)),
        mock.patch.object(bot_mod, "threading", FakeThreadingModule()),
    ]
    rp_holder = {}

    def _progress(pct, t):
        if progress:
            rp = rp_holder["rp"]
            progress(pct, t, len(broker.closed), rp.n_analyses, cache)

    rp = Replayer(bot_mod, app, broker, clock, pk, cfg, ai_latency=ai_latency, detect_delay=detect_delay, progress=_progress)
    rp_holder["rp"] = rp
    app.enqueue_ai_scan = rp.request_scan
    for p in patches:
        p.start()
    try:
        rp.run(start_utc, end_utc)
    finally:
        for p in reversed(patches):
            p.stop()

    # Lệnh còn mở cuối kỳ: đóng theo giá cuối để thống kê
    clock.now = end_utc
    for p in list(broker.positions):
        c = broker._cur()
        if c:
            broker._close(p, c[1] if p.type == 0 else c[2], c[3], broker.DEAL_REASON_EXPERT, p.volume, "end")
            broker.closed[-1]["reason"] = "Đóng cuối kỳ"
    return {"closed": broker.closed, "rejects": broker.rejects, "logs": logs, "errors": errors,
            "n_analyses": rp.n_analyses, "cache": cache, "end_utc": end_utc,
            "contract": float(sym_info.get("trade_contract_size", 100.0) or 100.0)}


def summarize(closed: list) -> dict:
    pnls = [c["profit"] for c in closed]
    win = sum(x for x in pnls if x > 0)
    loss = -sum(x for x in pnls if x < 0)
    eq, peak, mdd = 0.0, 0.0, 0.0
    streak, max_streak = 0, 0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        streak = streak + 1 if x < 0 else 0
        max_streak = max(max_streak, streak)
    n = len(pnls)
    return {"n": n, "net": round(sum(pnls), 2), "wins": sum(1 for x in pnls if x > 0), "losses": sum(1 for x in pnls if x < 0),
            "win_rate": (sum(1 for x in pnls if x > 0) / n * 100.0) if n else 0.0,
            "pf": (win / loss) if loss > 0 else (float("inf") if win > 0 else 0.0), "max_dd": round(mdd, 2),
            "max_loss_streak": max_streak}


def vn(ms_or_s, is_ms=True) -> str:
    s = ms_or_s / 1000.0 if is_ms else ms_or_s
    return datetime.fromtimestamp(s, VN_TZ).strftime("%d/%m %H:%M:%S")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phát lại CHÍNH code bot live trên tick quá khứ (chỉ đọc dữ liệu MT5)")
    ap.add_argument("--profile", required=True, choices=["XAUUSD", "BTCUSD"])
    ap.add_argument("--config", default=None, help="File cấu hình xuất từ app (không đọc token). Mặc định: cài đặt máy này")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (giờ VN)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (giờ VN, tính hết ngày)")
    ap.add_argument("--set", action="append", default=[], metavar="KHÓA=GIÁ_TRỊ", help="Ghi đè 1 khóa cấu hình cho lần chạy")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--ai-latency", type=float, default=8.5, help="Giây từ lúc phát hiện nến phân tích tới khi có kết quả AI (log live 16/11: 7.6-9.7s)")
    ap.add_argument("--detect-delay", type=float, default=1.6, help="Giây sau mốc đóng nến phân tích mà vòng nến phát hiện (log live: 1.2-2.1s)")
    ap.add_argument("--no-cancel-pending", action="store_true", help="So sánh: tắt hủy lệnh chờ cũ khi nến mới (hành vi trước 18/11)")
    ap.add_argument("--out", default=None, help="Thư mục xuất (mặc định SETTINGS_DIR\\replays)")
    args = ap.parse_args(argv)

    import run_backtest as rb
    import mt5_scalping_bot as bot_mod
    import trading_modules.trading_window as tw_mod
    from bt_ai_cache import ScenarioCache

    pk = args.profile
    try:
        cfg, n_loaded, note = rb.load_strategy_cfg(pk, args.config)
        set_notes = rb.apply_set_overrides(cfg, bot_mod.DEFAULT_PROFILE_SETTINGS.get(pk, {}), args.set)
    except (OSError, ValueError) as e:
        print(f"[ERROR] {e}")
        return 2
    cfg["engine"] = "Chạy trực tiếp máy Local"       # AI chạy trên máy này (cùng mô hình)
    cfg["telegram_enabled"] = False
    symbol = str(cfg.get("symbol") or bot_mod.DEFAULT_PROFILE_SETTINGS[pk]["symbol"]).strip()
    cfg["symbol"] = symbol
    d0 = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=VN_TZ)
    d1 = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=VN_TZ) + timedelta(days=1) - timedelta(seconds=1)
    start_utc, end_utc = d0.timestamp(), d1.timestamp()

    phys, logical = rb.cpu_core_counts()
    n_thr, warn = rb.resolve_threads(args.threads, phys)
    if warn:
        print(f"[WARN] {warn}")
    rb.apply_threads(n_thr)
    rb.set_seed(args.seed)

    print("=" * 70)
    print("   KRONOS LIVE REPLAY (CHẠY CHÍNH CODE BOT LIVE, CHỈ ĐỌC DỮ LIỆU)")
    print("=" * 70)
    print(f"[*] Profile / Symbol : {pk} / {symbol} | {args.start} -> {args.end} (giờ VN)")
    print(f"[*] Cấu hình         : {note}")
    keys = ["tf", "analysis_tf", "lot", "consensus", "max_dispersion", "min_move", "sl_buffer", "max_sl_price", "rr",
            "min_entry_spacing", "max_pos", "entry_mode", "max_hold_mins", "max_hold_action", "trend_filter",
            "structure_conflict_mode", "confirm_entry_pa", "pa_min_confirmations", "pred_len", "daily_tp_usd", "daily_sl_usd",
            "blocked_trading_windows"]
    print("[*] Thông số chính   : " + ", ".join(f"{k}={cfg.get(k)}" for k in keys))
    if set_notes:
        print(f"[*] Ghi đè --set     : {', '.join(set_notes)}")
    print(f"[*] Độ trễ AI        : phát hiện nến +{args.detect_delay:g}s, AI {args.ai_latency:g}s | Seed {args.seed} | Luồng torch {n_thr}")

    t_wall = _real_time.perf_counter()

    def progress(pct, t, n_closed, n_an, cache):
        print(f"  [{pct:>3}%] {vn(t, is_ms=False)} | lệnh đã đóng {n_closed} | phân tích AI {n_an} | "
              f"cache AI {cache.hits}/{cache.hits + cache.misses} | {(_real_time.perf_counter() - t_wall) / 60:.1f} phút")

    if args.no_cancel_pending:
        bot_mod.KronosDesktopApp._cancel_pending_for_new_scan = lambda self, pk: None
        print("[*] So sánh       : TẮT hủy lệnh chờ cũ khi nến mới (--no-cancel-pending)")
    res = simulate(bot_mod, pk, cfg, symbol, start_utc, end_utc, capital=args.capital, seed=args.seed,
                   ai_latency=args.ai_latency, detect_delay=args.detect_delay, progress=progress)
    closed, logs, errors, rejects = res["closed"], res["logs"], res["errors"], res["rejects"]
    n_analyses, cache, end_utc = res["n_analyses"], res["cache"], res["end_utc"]

    out_dir = args.out or os.path.join(bot_mod.SETTINGS_DIR, "replays")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    base = os.path.join(out_dir, f"replay_{symbol}_{args.start}_{args.end}_{stamp}")
    with open(base + "_lenh.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticket", "loai", "gio_vao", "gio_ra", "gia_vao", "gia_ra", "sl_ban_dau", "tp_ban_dau", "sl_cuoi", "lot", "lai_lo", "ly_do"])
        for c in closed:
            w.writerow([c["ticket"], c["type"], vn(c["open_ms"]), vn(c["close_ms"]), c["price_open"], c["price_close"],
                        c["sl0"], c["tp0"], c["sl_last"], c["volume"], c["profit"], c["reason"]])
    with open(base + "_log.txt", "w", encoding="utf-8") as f:
        for t, msg in logs:
            f.write(f"[{vn(t, is_ms=False)}] {msg}\n")
        if errors:
            f.write("\n===== LỖI =====\n")
            for t, msg in errors:
                f.write(f"[{vn(t, is_ms=False)}] {msg}\n")

    s = summarize(closed)
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    print("=" * 70)
    print(f"Số lệnh: {s['n']} | Thắng/Thua: {s['wins']}/{s['losses']} ({s['win_rate']:.1f}%) | Lãi ròng: {s['net']:+.2f} $")
    print(f"Profit factor: {pf} | Sụt vốn lớn nhất: {s['max_dd']:.2f} $ | Chuỗi thua dài nhất: {s['max_loss_streak']}")
    by_reason = {}
    for c in closed:
        by_reason.setdefault(c["reason"], [0, 0.0])
        by_reason[c["reason"]][0] += 1
        by_reason[c["reason"]][1] += c["profit"]
    print("Cách thoát lệnh: " + " | ".join(f"{k}: {v[0]} lệnh ({v[1]:+.2f} $)" for k, v in by_reason.items()))
    print(f"Phân tích AI: {n_analyses} lần | Cache AI: {cache.hits} trúng / {cache.misses} chạy mới")
    print(f"Sàn giả từ chối: {len(rejects)} lần | Lỗi ghi nhận trong code bot: {len(errors)}")
    print(f"Thời gian chạy: {(_real_time.perf_counter() - t_wall) / 60:.1f} phút")
    print(f"File lệnh: {base}_lenh.csv")
    print(f"File log : {base}_log.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
