import os
import sys, unittest
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b

PK = "XAUUSD"
def mk(tp, ask=101.0, rr=2.0):
    p = {"symbol": "XAUUSD", "action": "BUY", "sl_price": 100.0, "target_tp_price": tp,
         "effective_max_sl": 5.0, "manual_queued": True}
    ctx = {"pending_pullback_order": p}
    s = SimpleNamespace(
        _order_in_flight={PK: False}, profile_ctx={PK: ctx}, active_profile_key=PK,
        pending_pullback_order=p, auto_loop_running=True, cached_tp=None,
        _last_order_candle_ts={}, log=mock.Mock(),
        _pb_gate_log=lambda *a, **k: None, _pb_note_check=lambda *a, **k: (None, None),
        _is_daily_limit_locked=lambda pk: False, _is_cooldown_active=lambda pk: False,
        _get_profile_cfg=lambda pk: {"rr": rr, "max_sl_price": 5.0, "max_spread": 1.0, "lot": 0.01,
                                     "max_pos": 3, "confirm_entry_pa": False},
        _get_symbol_tolerances=lambda *a, **k: {"sl_break_tol": 0.0, "max_sl_tol": 0.0, "eps_equal": 0.05},
        _get_bot_positions=lambda *a, **k: [],
        execute_mt5_order=mock.Mock(return_value=(True, "ok")))
    tick = SimpleNamespace(ask=ask, bid=ask - 0.1)
    return s, p, tick

def run(s, tick, now=1000.0):
    with mock.patch.object(b.time, "time", return_value=now):
        b.KronosDesktopApp._check_pending_pullback_order(s, "XAUUSD", tick, PK)

class TestRRGate(unittest.TestCase):
    # cur_price=101, sl=100 -> cur_sl_dist=1 -> dung sai = 0.005/1 = 0.005
    def test_a_below_threshold_blocks_and_keeps_pending(self):
        s, p, tick = mk(tp=102.99)            # real_rr = 1.99 < 2.0 - 0.005
        run(s, tick)
        s.execute_mt5_order.assert_not_called()
        self.assertIs(s.profile_ctx[PK]["pending_pullback_order"], p)
        self.assertIs(s.pending_pullback_order, p)
        self.assertEqual(s.log.call_count, 1)
    def test_a2_rr_cfg_above_2_uses_cfg(self):
        s, p, tick = mk(tp=103.5, rr=3.0)     # real 2.5 < 3.0
        run(s, tick)
        s.execute_mt5_order.assert_not_called()
        self.assertIs(s.profile_ctx[PK]["pending_pullback_order"], p)
    def test_a3_rr_cfg_below_2_floors_to_2(self):
        s, p, tick = mk(tp=102.5, rr=1.0)     # real 1.5 < max(1,2)
        run(s, tick)
        s.execute_mt5_order.assert_not_called()
    def test_b_enough_rr_executes(self):
        for tp in (103.0, 102.996, 104.0):     # =2.0, trong dung sai, >2
            s, p, tick = mk(tp=tp)
            run(s, tick)
            s.execute_mt5_order.assert_called_once()
            self.assertIsNone(s.profile_ctx[PK]["pending_pullback_order"])
            self.assertIn("RR", s.log.call_args[0][0])
    def test_c_log_rate_limit_10s(self):
        s, p, tick = mk(tp=102.0)
        run(s, tick, now=1000.0); self.assertEqual(s.log.call_count, 1)
        run(s, tick, now=1005.0); self.assertEqual(s.log.call_count, 1)
        run(s, tick, now=1009.9); self.assertEqual(s.log.call_count, 1)
        run(s, tick, now=1010.0); self.assertEqual(s.log.call_count, 2)
        s.execute_mt5_order.assert_not_called()
        self.assertIs(s.profile_ctx[PK]["pending_pullback_order"], p)

if __name__ == "__main__":
    unittest.main(verbosity=2)
