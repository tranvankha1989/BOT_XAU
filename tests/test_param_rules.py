"""Luật kiểm tra thông số nhập (chỉ giao diện): ô sai tô đỏ, ô cần lưu ý tô cam, chặn nút Lưu / Áp dụng vào live.
Mock, không MT5; phần giao diện dùng Tk thật (ẩn cửa sổ)."""
import os
import sys
import tkinter as tk
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b
from trading_modules.param_rules import NUMERIC_RULES, PARAM_NAMES, PARENT_OF, validate_param_values, window_item_ok


def errs(cfg):
    return [it for it in validate_param_values(cfg) if it["level"] == "error"]


def warns(cfg):
    return [it for it in validate_param_values(cfg) if it["level"] == "warn"]


class TestRules(unittest.TestCase):
    def test_rule_keys_exist(self):
        for pk in ("XAUUSD", "BTCUSD"):
            d = b.DEFAULT_PROFILE_SETTINGS[pk]
            for k in list(NUMERIC_RULES) + list(PARENT_OF.values()):
                self.assertIn(k, d, (pk, k))
        self.assertTrue(set(NUMERIC_RULES) <= set(PARAM_NAMES))

    def test_defaults_and_real_like_config_ok(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertEqual(validate_param_values(dict(b.DEFAULT_PROFILE_SETTINGS[pk])), [], pk)
        # Thông số chính cấu hình máy 2 (19/11)
        m2 = {"rr": "2", "be_trigger_r": "1.2", "be_plus_price": "0.35", "auto_be": True, "auto_trail": False,
              "trail_dist_r": "1.5", "sl_buffer": "2.5", "max_sl_price": "4.0", "max_hold_mins": "10",
              "consensus": "75", "lot": "0.02", "max_pos": "2", "min_move": "2.2", "lookback": "60",
              "basket_tp_usd": "50", "basket_cooldown_mins": "15", "blocked_trading_windows": ""}
        self.assertEqual(validate_param_values(m2), [])

    def test_be_trigger_must_be_below_rr(self):
        e = errs({"rr": "0.5", "be_trigger_r": "1.0", "auto_be": True, "auto_trail": False})
        self.assertEqual(len(e), 1)
        self.assertEqual(set(e[0]["keys"]), {"be_trigger_r", "rr"})
        self.assertIn("nhỏ hơn Tỷ lệ R:R", e[0]["msg"])
        # bằng nhau cũng sai (dời SL đúng lúc chạm TP)
        self.assertTrue(errs({"rr": "2", "be_trigger_r": "2", "auto_be": True, "auto_trail": False}))
        # tắt Tự dời SL -> không xét
        self.assertEqual(errs({"rr": "0.5", "be_trigger_r": "1.0", "auto_be": False, "auto_trail": False}), [])
        # R:R = 0 (TP theo AI) -> không biết TP, không xét
        self.assertEqual(errs({"rr": "0", "be_trigger_r": "5", "auto_be": True, "auto_trail": True, "trail_dist_r": "9"}), [])

    def test_trailing_rules(self):
        e = errs({"rr": "2", "auto_trail": True, "trail_dist_r": "2.5", "auto_be": True, "be_trigger_r": "1"})
        self.assertEqual([set(x["keys"]) for x in e], [{"trail_dist_r", "rr"}])
        self.assertEqual(errs({"rr": "2", "auto_trail": False, "trail_dist_r": "2.5", "auto_be": True, "be_trigger_r": "1"}), [])
        # Trailing bắt đầu ở Mức kích hoạt + 0.3R = 1.8R, không nhỏ hơn R:R 1.8 -> sai
        e = errs({"rr": "1.8", "auto_trail": True, "trail_dist_r": "0.35", "auto_be": True, "be_trigger_r": "1.5"})
        self.assertEqual(len(e), 1)
        self.assertIn("Trailing bắt đầu", e[0]["msg"])
        self.assertEqual(errs({"rr": "1.8", "auto_trail": False, "trail_dist_r": "0.35", "auto_be": True, "be_trigger_r": "1.5"}), [])

    def test_rr_below_2_is_warning_only(self):
        cfg = {"rr": "1.5", "auto_be": True, "be_trigger_r": "1.0", "auto_trail": True, "trail_dist_r": "0.35"}
        self.assertEqual(errs(cfg), [])
        w = warns(cfg)
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["keys"], ("rr",))
        self.assertEqual(warns({"rr": "2"}), [])
        self.assertEqual(warns({"rr": "0"}), [])

    def test_number_format(self):
        self.assertIn("dấu chấm", errs({"lot": "0,02"})[0]["msg"])
        self.assertIn("chưa nhập", errs({"lot": " "})[0]["msg"])
        self.assertIn("phải là số", errs({"min_move": "abc"})[0]["msg"])
        self.assertIn("số nguyên", errs({"max_pos": "2.5"})[0]["msg"])
        self.assertIn("số nguyên", errs({"lookback": "60.0"})[0]["msg"])  # code bot đọc int("60.0") -> lỗi
        self.assertEqual(errs({"max_pos": "2", "lookback": 60, "lot": 0.02}), [])
        self.assertTrue(errs({"min_move": "nan"}))

    def test_ranges(self):
        for cfg in ({"lot": "0"}, {"max_pos": "0"}, {"max_spread": "0"}, {"sl_buffer": "-1"},
                    {"consensus": "40"}, {"consensus": "101"}, {"trail_dist_r": "0", "auto_trail": True}):
            self.assertTrue(errs(cfg), cfg)
        self.assertEqual(errs({"consensus": "50"}), [])
        self.assertEqual(errs({"consensus": "100"}), [])
        self.assertEqual(errs({"rev_be_consensus": "0", "auto_rev_close": True}), [])
        self.assertIn("bằng 0 (tắt)", errs({"rev_be_consensus": "30", "auto_rev_close": True})[0]["msg"])

    def test_message_shows_entered_value_and_range(self):
        m = errs({"rev_consensus": "30", "auto_rev_close": True})[0]["msg"]
        self.assertIn("đang nhập 30", m)
        self.assertIn("phải từ 50 đến 100", m)
        self.assertIn("đang nhập 30", errs({"rev_be_consensus": "30", "auto_rev_close": True})[0]["msg"])
        self.assertIn("đang nhập 'abc'", errs({"min_move": "abc"})[0]["msg"])
        self.assertIn("đang nhập '2.5'", errs({"max_pos": "2.5"})[0]["msg"])
        self.assertIn("đang nhập 0", errs({"lot": "0"})[0]["msg"])
        long_m = errs({"min_move": "x" * 50})[0]["msg"]
        self.assertIn("x" * 20 + "…", long_m)
        self.assertNotIn("x" * 21, long_m)

    def test_text_and_conflict_rules(self):
        self.assertIn("Symbol", errs({"symbol": "  "})[0]["msg"])
        self.assertEqual(errs({"symbol": "XAUUSDm"}), [])
        w = warns({"tf": "M15", "analysis_tf": "M5"})
        self.assertEqual(w[0]["keys"], ("tf", "analysis_tf"))
        self.assertEqual(warns({"tf": "M1", "analysis_tf": "M5"}), [])
        # chuỗi thua > 0 nhưng thời gian nghỉ = 0 -> bot không nghỉ
        self.assertIn("KHÔNG tạm nghỉ", warns({"max_consec_loss": "3", "cooldown_mins": "0"})[0]["msg"])
        self.assertEqual(warns({"max_consec_loss": "0", "cooldown_mins": "0"}), [])
        self.assertIn("luôn vượt trần", warns({"sl_buffer": "4", "max_sl_price": "4"})[0]["msg"])
        self.assertEqual(warns({"sl_buffer": "4", "max_sl_price": "0"}), [])  # 0 = theo AI, không có trần
        self.assertTrue(warns({"auto_rev_close": True, "rev_consensus": "80", "rev_be_consensus": "60"}))
        self.assertEqual(warns({"auto_rev_close": True, "rev_consensus": "80", "rev_be_consensus": "0"}), [])
        self.assertEqual(warns({"auto_rev_close": False, "rev_consensus": "80", "rev_be_consensus": "60"}), [])
        tw = warns({"telegram_enabled": True, "telegram_bot_token": "", "telegram_chat_id": "123"})
        self.assertIn("Bot Token", tw[0]["msg"])
        self.assertNotIn("123", tw[0]["msg"])  # không ghi giá trị Telegram vào câu báo
        self.assertEqual(warns({"telegram_enabled": False, "telegram_bot_token": ""}), [])
        self.assertTrue(warns({"engine": "Ưu tiên Hugging Face Space", "hf_space": ""}))
        self.assertEqual(warns({"engine": "Chạy trực tiếp máy Local", "hf_space": ""}), [])

    def test_children_skipped_when_parent_off(self):
        self.assertEqual(errs({"auto_rev_close": False, "rev_consensus": "abc", "rev_be_consensus": "30"}), [])
        self.assertEqual(errs({"basket_tp_usd": "0", "basket_cooldown_mins": "-5"}), [])
        self.assertTrue(errs({"basket_tp_usd": "50", "basket_cooldown_mins": "-5"}))
        self.assertEqual(errs({"max_consec_loss": "0", "cooldown_mins": "x"}), [])
        self.assertEqual(errs({"auto_be": "False", "be_trigger_r": "0"}), [])  # bool dạng chuỗi từ file

    def test_blocked_windows(self):
        ok = "04:00-06:00, THU 19:25-20:00; 2026-11-12 19:25-20:00, t2 22:00-24:00"
        self.assertEqual(errs({"blocked_trading_windows": ok}), [])
        for bad in ("25:00-26:00", "FRI 1900-2000", "XYZ 10:00-11:00", "2026-13-01 10:00-11:00", "10:00", "04:60-05:00"):
            e = errs({"blocked_trading_windows": f"04:00-05:00, {bad}"})
            self.assertEqual(len(e), 1, bad)
            self.assertIn(bad, e[0]["msg"])
        self.assertTrue(window_item_ok("CN 23:00-01:00"))


class TestUi(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"Không có Tk: {e}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def make_app(self, values):
        app = object.__new__(b.KronosDesktopApp)
        vars_ = {k: (tk.BooleanVar(master=self.root, value=v) if isinstance(v, bool) else tk.StringVar(master=self.root, value=v))
                 for k, v in values.items()}
        rows = {}
        for var in vars_.values():
            if isinstance(var, tk.StringVar):
                fr = tk.Frame(self.root)
                rows[str(var)] = (fr, tk.Entry(fr, textvariable=var, bg=b.KronosDesktopApp.PARAM_OK_BG))
        holder = tk.Frame(self.root)
        app.__dict__.update(root=self.root, _param_rows=rows, _get_settings_vars_map=lambda: vars_,
                            param_issue_lbl=tk.Label(holder), save_status_lbl=tk.Label(holder),
                            active_profile_key="XAUUSD", log=mock.Mock(), show_toast=mock.Mock())
        return app, vars_, (lambda k: rows[str(vars_[k])][1])

    def test_paint_and_banner(self):
        app, v, ent = self.make_app({"rr": "0.5", "be_trigger_r": "1.0", "auto_be": True, "auto_trail": False, "lot": "0.02"})
        self.assertEqual(len(app._validate_menu_settings()), 1)
        self.assertEqual(ent("be_trigger_r").cget("bg"), app.PARAM_ERR_BG)
        self.assertEqual(ent("rr").cget("bg"), app.PARAM_ERR_BG)  # lỗi nặng hơn cảnh báo
        self.assertEqual(ent("lot").cget("bg"), app.PARAM_OK_BG)
        self.assertEqual(app.param_issue_lbl.winfo_manager(), "pack")
        self.assertIn("⛔", app.param_issue_lbl.cget("text"))
        # sửa đúng -> hết đỏ; R:R 0.5 < 2 còn tô cam (không chặn)
        v["be_trigger_r"].set("0.4")
        self.assertEqual(app._validate_menu_settings(), [])
        self.assertEqual(ent("be_trigger_r").cget("bg"), app.PARAM_OK_BG)
        self.assertEqual(ent("rr").cget("bg"), app.PARAM_WARN_BG)
        v["rr"].set("2")
        app._validate_menu_settings()
        self.assertEqual(ent("rr").cget("bg"), app.PARAM_OK_BG)
        self.assertEqual(app.param_issue_lbl.winfo_manager(), "")

    def test_traces_revalidate_on_typing(self):
        app, v, ent = self.make_app({"rr": "2", "be_trigger_r": "1.0", "auto_be": True, "auto_trail": False})
        app._install_param_validation()
        self.root.update()
        self.assertEqual(ent("be_trigger_r").cget("bg"), app.PARAM_OK_BG)
        v["be_trigger_r"].set("3")
        self.root.update()
        self.assertEqual(ent("be_trigger_r").cget("bg"), app.PARAM_ERR_BG)
        v["auto_be"].set(False)  # tắt Tự dời SL -> ô con khóa, không còn lỗi
        self.root.update()
        self.assertEqual(ent("be_trigger_r").cget("bg"), app.PARAM_OK_BG)

    def test_manual_save_blocked_when_error(self):
        app, v, _ent = self.make_app({"rr": "0.5", "be_trigger_r": "1.0", "auto_be": True, "auto_trail": False})
        app.save_user_settings = mock.Mock(return_value=True)
        app.apply_live_settings_and_rescan = mock.Mock()
        with mock.patch.object(b.messagebox, "showwarning") as sw:
            app.manual_save_user_settings()
        sw.assert_called_once()
        self.assertIn("nhập lại", sw.call_args[0][1])
        app.save_user_settings.assert_not_called()
        app.apply_live_settings_and_rescan.assert_not_called()
        self.assertIn("Chưa lưu", app.save_status_lbl.cget("text"))
        # chỉ còn cảnh báo cam -> lưu và áp dụng như cũ
        v["be_trigger_r"].set("0.4")
        with mock.patch.object(b.messagebox, "showwarning") as sw:
            app.manual_save_user_settings()
        sw.assert_not_called()
        app.save_user_settings.assert_called_once()
        app.apply_live_settings_and_rescan.assert_called_once()

    def test_inline_hint_under_wrong_field(self):
        """Ô sai: hiện dòng báo NGAY DƯỚI ô (giá trị đang nhập + mức hợp lệ); sửa đúng thì ẩn."""
        parent = tk.Frame(self.root)
        parent.pack()
        v = tk.StringVar(master=self.root, value="30")
        on = tk.BooleanVar(master=self.root, value=True)
        row = tk.Frame(parent)
        row.pack(fill=tk.X, padx=(18, 0))
        ent = tk.Entry(row, textvariable=v)
        ent.pack(side=tk.RIGHT)
        below = tk.Frame(parent)
        below.pack(fill=tk.X)
        app = object.__new__(b.KronosDesktopApp)
        app.__dict__.update(root=self.root, _param_rows={str(v): (row, ent)},
                            _get_settings_vars_map=lambda: {"rev_consensus": v, "auto_rev_close": on},
                            param_issue_lbl=tk.Label(tk.Frame(self.root)))
        self.assertEqual(len(app._validate_menu_settings()), 1)
        hint = app._param_hint_lbls[str(ent)]
        self.assertEqual(hint.winfo_manager(), "pack")
        self.assertIn("đang nhập 30", hint.cget("text"))
        self.assertIn("50 đến 100", hint.cget("text"))
        slaves = parent.pack_slaves()
        self.assertEqual(slaves.index(hint), slaves.index(row) + 1)  # ngay dưới dòng của ô sai
        v.set("75")
        self.assertEqual(app._validate_menu_settings(), [])
        self.assertEqual(hint.winfo_manager(), "")
        v.set("40")
        app._validate_menu_settings()
        self.assertEqual(app._param_hint_lbls[str(ent)], hint)  # dùng lại nhãn, không tạo thêm
        self.assertIn("đang nhập 40", hint.cget("text"))
        on.set(False)  # tắt Bảo vệ đảo chiều -> ô con không xét -> ẩn dòng báo
        app._validate_menu_settings()
        self.assertEqual(hint.winfo_manager(), "")

    def test_bt_form_paint(self):
        app = object.__new__(b.KronosDesktopApp)
        ent_rr, ent_be = tk.Entry(self.root), tk.Entry(self.root)
        lbl = tk.Label(tk.Frame(self.root))
        cfg = {"rr": "0.5", "be_trigger_r": "1.0", "auto_be": True, "auto_trail": False, "magic_number": "abc"}
        app.__dict__.update(_bt_form_vars={"rr": 1, "be_trigger_r": 1}, _bt_form_widgets={"rr": ent_rr, "be_trigger_r": ent_be},
                            _bt_issue_lbl=lbl, _get_bt_cfg=lambda pk: dict(cfg))
        self.assertEqual(len(app._validate_bt_form("XAUUSD")), 1)  # magic_number không có trong bảng Backtest -> bỏ qua
        self.assertEqual(ent_be.cget("bg"), app.PARAM_ERR_BG)
        self.assertIn("⛔", lbl.cget("text"))

    def test_apply_bt_to_live_blocked_when_error(self):
        app = object.__new__(b.KronosDesktopApp)
        cfg = {"rr": "0.5", "be_trigger_r": "1.0", "auto_be": True, "auto_trail": False, "lot": "0.02", "magic_number": "abc"}
        app.__dict__.update(root=self.root, bt_sym_var=tk.StringVar(master=self.root, value="XAUUSDm"),
                            bt_lot_var=tk.StringVar(master=self.root, value="0.02"),
                            bt_settings={"XAUUSD": {"rr": "0.5", "be_trigger_r": "1.0"}},
                            _get_bt_cfg=lambda pk: dict(cfg), _bt_collect_changes=mock.Mock(return_value=[]),
                            show_toast=mock.Mock())
        with mock.patch.object(b.messagebox, "showwarning") as sw, mock.patch.object(b.messagebox, "askyesno") as ask:
            app._apply_bt_settings_to_menu()
        sw.assert_called_once()
        self.assertIn("Mức kích hoạt", sw.call_args[0][1])
        self.assertNotIn("Magic", sw.call_args[0][1])  # khóa ngoài cấu hình Backtest không chặn
        ask.assert_not_called()
        app._bt_collect_changes.assert_not_called()
        cfg["be_trigger_r"] = "0.4"
        with mock.patch.object(b.messagebox, "showwarning") as sw:
            app._apply_bt_settings_to_menu()
        sw.assert_not_called()
        app._bt_collect_changes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
