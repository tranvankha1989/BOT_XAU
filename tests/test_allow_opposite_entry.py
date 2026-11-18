"""allow_opposite_entry: lệnh ngược chiều đang âm không còn chặn lệnh chiều mới khi BẬT (mock, không MT5 thật)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b  # noqa: E402
from test_pb_diag import Base, make_app, diag, tk, PK, SYM  # noqa: E402


class TestAllowOppositeEntryZone(Base):
    # Lệnh chờ là SELL (giá 3886.90 trong vùng), đang có 1 lệnh BUY âm
    def losing_buy(self):
        return [SimpleNamespace(type=0, profit=-2.0)]

    def test_off_blocks_like_before(self):
        app = make_app({"max_pos": 3}, positions=self.losing_buy())
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        lines = diag(app, "max_pos_hoac_lenh_nguoc")
        self.assertEqual(len(lines), 1)
        self.assertIn("lệnh ngược chiều đang âm", lines[0])
        app.execute_mt5_order.assert_not_called()

    def test_on_enters_opposite(self):
        app = make_app({"max_pos": 3, "allow_opposite_entry": True}, positions=self.losing_buy())
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(diag(app, "max_pos_hoac_lenh_nguoc"), [])
        app.execute_mt5_order.assert_called_once()

    def test_on_still_respects_max_pos(self):
        app = make_app({"max_pos": 1, "allow_opposite_entry": "True"}, positions=self.losing_buy())
        app._check_pending_pullback_order(SYM, tk(3886.90), profile_key=PK)
        self.assertEqual(len(diag(app, "max_pos_hoac_lenh_nguoc")), 1)
        app.execute_mt5_order.assert_not_called()

    def test_default_off_both_profiles(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertIs(b.DEFAULT_PROFILE_SETTINGS[pk]["allow_opposite_entry"], False)
        self.assertIn("allow_opposite_entry", b.PARAM_HELP_GUIDE)
