# Bản đồ kiến trúc `mt5_scalping_bot.py` (P52-C)

Lập ngày 14/11/2026 từ commit `d057060` (file 12.430 dòng, MỘT class `KronosDesktopApp` dòng 1049–12143 + vài hàm module). **Số dòng sẽ lệch khi file đổi.**
Chỉ đọc code, không đề xuất sửa logic. `~` = ước lượng ranh giới theo comment/neo mã; "chưa xác nhận" = suy luận chưa kiểm chứng.
Cách lập: liệt kê `def`, quét toàn bộ lời gọi `mt5.*`, thống kê `self.<attr>` đọc/ghi theo hàm (script đọc file, không chạy bot).

## 1. Nhóm chức năng và hàm thuộc nhóm
| Nhóm | Hàm (dòng) |
|---|---|
| **mt5_gateway** (mọi `mt5.*`; hiện KHÔNG có module riêng, 27 hàm rải rác) | tick/symbol: `_get_symbol_tolerances` 1356, `_get_candle_countdown_str` 6730, `_get_fresh_tick` 9394, `_calc_target_pnl` 8876 · nến: `fetch_mt5_candles` 10120, `_run_backtest_simulation` 4310 (`copy_rates_range`), `_fetch_or_load_ticks` 3970 (`copy_ticks_range`) · vị thế: `_positions_get_checked` 8106, `_get_bot_positions` 8135, `close_selected_position` 9549, `_generate_telegram_stats_report` 6118 (`positions_get` KHÔNG lọc magic: A36) · lịch sử: `_get_today_deals_stats` 7470, `_get_closed_deals_by_range` 7539, `_get_closed_deal_info` 7720, `_resolve_position_magic` 8170 · lệnh: `_send_mt5_order_with_retry` 8244 (`order_send`), `execute_mt5_order` 10325 (`order_check`), `_modify_position_sltp` 9411, `_close_single_pos` 9470 · kết nối: `check_mt5_connection` 9618, `_realtime_tick_loop` 7392, `_switch_active_profile` 1491, `_on_close_window` 6500 (`shutdown`), `log_error` 7328 (`last_error`) |
| **kronos_engine** | `get_local_predictor` 9640, `_fast_predict_scenarios` 9936, `_compute_adaptive_sampling_params` 9723, `_cleanup_memory_and_trim_workingset` 9692, `MODEL_CONFIGS` 93, giới hạn luồng torch 39–49; trong `_run_analysis_worker`: nhánh Hugging Face ~10950–11016, nhánh local ~11017–11047, chấm điểm kịch bản/Bayesian ~11048–11072 |
| **Bộ lọc cấu trúc** | `_analyze_scalping_micro_macro_context` 9757–9935 (BOS/CHoCH, order-flow, wick, `structure_conflict_mode` 9775–9781); nơi dùng: worker 10949 (gọi), ~11077–11095 (`ctx_bonus`), 11097 + ~11121/~11149 (`trend_filter`); backtest `_evaluate_bt_ai_candle` 4093–4098, 4179–4186, 4192–4207 |
| **Price Action** | `_check_price_action_entry_confirmation` 8425–8543; gọi từ worker 11401 và `_check_pending_pullback_order` 8758. Backtest KHÔNG có |
| **Chi phí/spread** | `_get_symbol_tolerances` 1356–1408 (min_stop, sl_break_tol, max_sl_tol, floors); worker ~11098–11106 (`slippage_est`, `min_effective_move`) và gate spread 11388; pending 8733; `execute_mt5_order` (độ lệch/deviation ~10478); backtest 4165–4171, chi phí cố định 4714–4721 |
| **Risk gates** | `_check_daily_profit_loss_limits` 7795–8029 (+`_daily_limit_close_bot_positions` 8030), `_is_daily_limit_locked` 8045, `_is_cooldown_active` 8050, `_get_cooldown_remaining_str` 8062, `is_trading_window_blocked` 822 (hàm module), max_pos/`_is_pos_at_be` 9008, nhồi lệnh (một lệnh/nến, khoảng cách, entry_mode) trong `execute_mt5_order` 10370–10426; nguồn dữ liệu: `_get_today_deals_stats` 7470, `_get_today_closed_deals` 7790 |
| **Execution** | `execute_mt5_order` 10325–10547, `_send_mt5_order_with_retry` 8244, `_get_supported_filling_modes` 8229, `_check_pending_pullback_order` 8648–8806 (+ chẩn đoán `_pb_*` 8544–8647), tạo vùng canh (zone watch) trong worker ~11346–11427 |
| **position_manager** | `_update_open_positions_and_manage` 9024–9363, `_modify_position_sltp` 9411, `_close_single_pos` 9470, `_dispatch_pos_action_async` 9364, `breakeven_all_positions` 9504, `_close_all_positions_for_profile` 9558, `_execute_basket_tp_close` 9577, `close_all_positions` 9604, `_get_pos_one_r_dist` 8972 + cache 1R 8896–8971, `_is_pos_at_be` 9008, `_calc_target_pnl` 8876; đảo chiều/tighten trong worker ~11223–11327 |
| **UI** | khung/hoạt ảnh/title 1585–2014, `_build_ui` 2385–3064, tab vị thế 3065–3370, tab lịch sử 3371–3555 (+`_refresh_history_tab` 5069), biểu đồ 6582–7260 và `_draw_candlestick_chart` 11540–12143, `_refresh_realtime_chart` 10152, toast 5302, log UI 7369–7391, `_update_ui_results` 11478, `_update_bot_status_bar` 10622, `CollapsibleSection` 613, `DatePickerDialog` 675 |
| **Telegram** | `_telegram_api_call` 5797, `_get_telegram_inline_keyboard` 5821, `_telegram_dispatcher_loop` 5849, `_telegram_polling_worker` 5872, `_handle_telegram_callback` 5938, `_handle_telegram_command` 6034, `_generate_telegram_stats_report` 6105, `_telegram_notify_order_opened` 6183 / `_closed` 6217, `test_telegram_connection` 6265 |
| **license/updater** (KHÔNG đụng logic) | `__init__` 1057–1070 (nạp LicenseManager/AppUpdater), `_update_license_badge_ui` 2015, `_open_license_dialog` 2033, `_check_update_on_startup` 2048, badge cập nhật 2060–2139, `_on_update_hot_applied` 2157 (nạp lại module, ghi đè file), `run_first_time_setup` 12144, `ensure_single_app_instance` 12323. Kiểm tra bản quyền chen vào luồng giao dịch: `execute_mt5_order` 10330, `_update_left_quick_info`/vùng 8853, `start_auto_scan` 10718 |
| **backtest** | hàm module 917–1048 (`build_backtest_report`, `format_backtest_report`, `export_backtest_csv`), tab UI 3556–3969, `_fetch_or_load_ticks` 3970, `_resample_ticks_to_s1` 4007, `_evaluate_bt_ai_candle` 4046–4225, `_run_backtest_simulation` 4226–4759, kết quả/UI 4760–4985; công cụ ngoài `tools/run_backtest.py`, `tools/replay_gate.py` |
| **Cấu hình/trạng thái/log (xuyên suốt)** | `DEFAULT_PROFILE_SETTINGS` 433, `PROFILE_STATE_ATTRS` 554, `AI_MANAGED_STATE_ATTRS` 594, `_create_empty_profile_ctx` 1303, `_get_profile_cfg` 1409, `_sync_*` 1427/1467, `_switch_active_profile` 1491, `_get_settings_vars_map` 5240, save/load/import/export settings 5391–5796, logger 7269–7391, `_resolve_profile_by_symbol` 8093, `_get_profile_magic_number` 8158 |

## 2. Trạng thái dùng chung (`self.xxx`) theo nhóm — chỗ khó tách
| Nhóm / hàm chính | Ghi (W) | Đọc (R) | Độ khó |
|---|---|---|---|
| gateway: `_positions_get_checked`, `_get_bot_positions`, `fetch_mt5_candles`, `_get_symbol_tolerances`, `_get_fresh_tick` | không (hai hàm 7470 ghi cache: `_cached_deals_by_sym`, `_cached_deals_stats`, `_last_deals_check_ts`) | `_mt5_lock` (RLock) | **Dễ**: chỉ phụ thuộc `_mt5_lock`; nhưng `mt5` là biến module mà 129 test patch bằng `b.mt5` |
| engine: `get_local_predictor`, `_fast_predict_scenarios` | `local_cache` | `_ai_inference_lock` | **Dễ** |
| cấu trúc `_analyze_...` 9757, `_compute_adaptive...` | không | không (hàm gần thuần) — NGOẠI LỆ: đọc `active_profile_key` + `_get_profile_cfg` (9777–9779) để lấy `structure_conflict_mode`; chưa xác nhận hệ quả khi quét profile không phải active | **Dễ–vừa** |
| PA 8425 | không | `_mt5_lock`, `_get_profile_cfg`, `fetch_mt5_candles` | **Dễ** |
| risk gates: `_check_daily_profit_loss_limits` | `cooldown_until`, `fast_sl_cooldown_until`, `basket_cooldown_until`, `daily_limit_locked_*`, `daily_limit_reason`, `last_*_deal_ticket`, `cached_daily_pnl`, `consec_loss_count`, `pending_pullback_order`, cache deals | `profile_ctx`, `active_profile_key`, `auto_loop_running` | **Khó**: mỗi biến tồn tại 2 bản (`self.x` và `ctx["x"]`, đồng bộ tay ở 8002–8013) |
| execution: `execute_mt5_order` | `_last_order_candle_ts`, `_last_order_open_mono`, `_order_in_flight` | `_order_guard_lock`, `_mt5_lock`, `lic_mgr`, `profile_ctx`, `daily_limit_reason`, `ctx["last_candle_time"]` | **Khó**: nhúng bản quyền + risk gates + nhồi lệnh |
| pending `_check_pending_pullback_order` | `pending_pullback_order`, `_last_order_candle_ts` | `_order_in_flight`, `auto_loop_running`, `cached_tp`, `profile_ctx`, `ctx["last_candle_time"]` | **Khó**: A03 (race ghi đè `pending`), chạy trên luồng UI |
| position_manager: `_update_open_positions_and_manage` | `cached_win/loss/be/closed_*` | `pos_tree(_by_profile)`, `pos_summary_lbl(_by_profile)` (widget Tk), `pending_be_exit_tickets`, `_telegram_sent_deal_tickets`, `telegram_*_var`, `initial_r_cache`, `ctx` | **Rất khó**: logic BE/trailing dính chặt vào vẽ Treeview và Telegram |
| worker `_run_analysis_worker` | `is_analyzing`, `last_ana_candle_time`, `last_candle_time`, `pending_pullback_order`, `_sig_title/_sig_color`, ~15 khóa `ctx["cached_*"]` | `_ai_scan_lock`, `_mt5_lock`, `_is_initial_loading`, `pending_be_exit_tickets`, `profile_ctx`, `auto_loop_running` | **Rất khó** (644 dòng, xem mục 3) |
| `_auto_loop_worker` 10764, `enqueue_ai_scan` 10548, `_ai_queue_consumer_loop` 10587 | `last_candle_time`, `_ai_current_running_pk`, `is_analyzing` | `_ai_queue(_lock)`, `_ai_queued_profiles`, `_auto_worker_token` | Vừa |
| `_realtime_tick_loop` 7392 | `heartbeat_toggle` | label UI, `tf_var`, `symbol_var`, `analysis_tf_var`, `profile_settings`, `is_analyzing`, `realtime_chart_var` | Vừa–khó: trộn UI + quản lý lệnh + risk + pending |
| telegram | hàng đợi/dispatcher | `_telegram_queue`, `*_var` (Tk), `profile_ctx`, `root` | Vừa (đọc biến Tk từ luồng nền: A35) |
| backtest `_run_backtest_simulation` | `_bt_trades_cache`, `_bt_equity_points` | `bt_*_var` (Tk), `root`, `_bt_cancel_flag`, `profile_ctx`, `analysis_tf_var`, `tf_var` | Vừa (tools/run_backtest.py dựng app giả để chạy) |

**Ba "đinh" chung khó nhất:**
1. **Trạng thái kép `self.x` ↔ `profile_ctx[pk]["x"]`**: `PROFILE_STATE_ATTRS` (554–593) + `_sync_self_to_active_ctx` 1427 + `_sync_active_ctx_to_self` 1467 + mẫu `if pk == self.active_profile_key: self.x = ctx[x]` rải khắp; hàm nào cũng phải biết profile nào đang active.
2. **Cấu hình lấy từ widget Tk**: `_get_profile_cfg` 1409 đọc `*_var.get()` khi ở luồng chính và đúng profile active, ngược lại lấy bản chụp `profile_settings`; mọi nhóm đều phụ thuộc hàm này.
3. **`self.log` / `_ui_after` / `show_toast`** (UI) được gọi từ mọi nhóm, kể cả luồng nền.

## 3. Hàm trộn logic của nhiều nhóm (ranh giới dòng)
**`_run_analysis_worker` 10834–11477**
| Dòng | Nhóm |
|---|---|
| 10834–10847 | khóa quét + chụp cfg (cấu hình/UI) |
| 10848–10860 | risk gate: khung giờ cấm (xóa pending) |
| 10861–10898 | parse tham số cấu hình (cấu hình) |
| 10899–10935 | gateway: nến M5/scalp, tick (10905), ATR (`calculate_atr`) |
| 10936–10949 | engine (thời gian, sampling) + cấu trúc (10949) |
| 10950–11047 | engine: HF/local predictor (HF token lấy từ cfg) |
| 11048–11072 | engine/tín hiệu: đồng thuận, Bayesian, phân tán |
| 11073–11108 | tín hiệu + cấu trúc (`ctx_bonus`) + chi phí (`min_effective_move` 11101–11106) |
| 11109–11222 | tín hiệu: chặn cấu trúc (~11121/~11149), SL/TP (11138–11191), tiêu đề UI (`sig_title`) |
| 11223–11327 | position_manager: đảo chiều, close_if_strong, tighten_sl, wait_be (gọi `_close_single_pos`/`_modify_position_sltp`) |
| 11328–11345 | execution: trần SL, chia đôi Max SL (`effective_max_sl`), RR ≥ 2R |
| 11346–11427 | execution + risk gates + chi phí + PA: tạo `zone_watch_order`, gate khóa ngày 11363, cooldown 11368, lệnh ngược 11372, SL vượt trần 11376, spread 11388, một lệnh/nến 11392, PA 11401, gọi `execute_mt5_order` 11414 |
| 11428–11447 | log/UI text |
| 11448–11477 | gateway (nến để vẽ) + UI (`_update_ui_results`), `finally` reset trạng thái + dọn bộ nhớ |

**`execute_mt5_order` 10325–10547**
| Dòng | Nhóm |
|---|---|
| 10325–10334 | cấu hình + **bản quyền** (`lic_mgr.check_status`) |
| 10335–10342 | execution: khóa chống đúp (`_order_in_flight`, 3 giây) |
| 10343–10352 | gateway (`initialize`) + risk gate (khóa ngày, cooldown) |
| 10353–10369 | gateway (positions + lọc magic) + risk gate (`max_pos`, loại lệnh đã BE) |
| 10370–10426 | risk gate nhồi lệnh: một lệnh/nến, khoảng cách tối thiểu, entry_mode (Risk-Free/DCA) |
| 10427–10442 | execution: chuẩn hóa lot theo sàn |
| 10443–10481 | chi phí/risk: SL sai phía/quá sát, chia đôi Max SL, hạ TP về 2R (trùng worker 11328–11345) |
| 10482–10523 | execution + gateway: dựng request, `order_check` |
| 10524–10547 | execution: gửi lệnh, cập nhật trạng thái, Telegram |

**`_check_pending_pullback_order` 8648–8806** (đường canh vùng, luồng UI mỗi ~1 giây)
| Dòng | Nhóm |
|---|---|
| 8648–8657 | chặn đúp (`_order_in_flight`) |
| 8658–8683 | risk gate: khóa ngày/cooldown/khung giờ (xóa pending) |
| 8684–8707 | cấu hình + giá + hủy khi giá phá SL (`sl_break_tol`) |
| 8708–8722 | vùng kích hoạt (`effective_max_sl`), bật AUTO |
| 8719–8732 | gateway (`_get_bot_positions`) + risk gate `max_pos`, lệnh ngược đang âm |
| 8733–8736 | chi phí: spread |
| 8737–8752 | TP (TP AI đã lưu hoặc 2R), hủy khi chạm TP |
| 8753–8768 | Price Action |
| 8769–8777 | RR thực tế |
| 8778–8806 | execution: pop pending nguyên tử, gọi `execute_mt5_order`, đặt lại pending (zone_watch) — nguồn A03 |

**`_update_open_positions_and_manage` 9024–9363**
| Dòng | Nhóm |
|---|---|
| 9024–9073 | gateway (`_get_bot_positions`) + UI (lưu dòng chọn trong Treeview) + cấu hình |
| 9074–9095 | position_manager: tính R (`_get_pos_one_r_dist`) |
| 9096–9145 | position_manager: giữ lệnh quá thời gian (max_hold) — lưu ý `elapsed_sec` dùng giờ server (A38) |
| 9146–9174 | position_manager: chờ hồi về BE thoát (quy tắc 3) |
| 9175–9230 | position_manager: tự BE / trailing BUY & SELL |
| 9231–9304 | lịch sử deal + Telegram (`_telegram_notify_order_closed` 9258) + thống kê + **vẽ Treeview** |
| 9305–9340 | position_manager: chốt lời chùm (basket) |
| 9341–9363 | UI (nhãn tóm tắt) + `_sync_initial_r_cache` |

**Các hàm trộn khác:** `_check_daily_profit_loss_limits` 7795–8029 (dữ liệu deals 7800 [gateway] → cooldown/chuỗi thua 7835–7909 → mở khóa 7921–7970 → khóa + ĐÓNG LỆNH [execution] 7972–8000 + toast/log UI + đồng bộ `self`↔`ctx` 8002–8013 · `_realtime_tick_loop` 7392–7469 (gateway + nhãn UI + quản lý lệnh + risk + pending + vẽ chart) · `_run_backtest_simulation` 4226–4759 (nạp dữ liệu MT5 + mô phỏng 2 chế độ + cập nhật UI qua `root.after`) · `_switch_active_profile` 1491–1565 (UI + `symbol_select`/`symbol_info_tick` gateway).

## 4. Logic lặp giữa live và backtest (bản sao cổng chặn)
| Logic | Live | Backtest | Ghi chú |
|---|---|---|---|
| Đồng thuận, phân tán, `confidence`, `ctx_bonus`, cổng chi phí `min_effective_move` | worker 11048–11108 | `_evaluate_bt_ai_candle` 4121–4189 | bản sao gần nguyên văn; khác: spread live = tick thật, backtest = `max_spread*0.5` |
| Chặn ngược cấu trúc (`trend_filter`, `macro_dir`) | worker ~11121/~11149 | 4192–4207 | cùng luật (backtest cần cờ `bt_use_live_structure`) |
| Tính SL AI (`base_atr_sl`, swing, `sl_buffer`) | worker 11138, 11166 | 4173, 4198–4207 | sàn ATR khác: live `tols["min_atr_sl_floor"]`, backtest hằng 0.5/8.0 |
| Công thức ATR đa khung `max(atr_scalp, atr_ana*0.65)` | worker ~10936–10975 (`calculate_atr` 10318) | 4094–4096 và ATR trung bình TR TỰ VIẾT 4394–4401, 4575–4587 | **3 cách tính ATR khác nhau** (chưa xác nhận mức lệch số) |
| Vào lệnh tại Max SL / vùng canh | worker 11346 + pending 8708 | 4438–4455 (S1), 4628–4650 (nến) | luật khác nhau (xem P44-C): backtest không chia đôi Max SL, khớp tại limit |
| TP | AI hoặc RR (worker 11141–11171) | luôn 2R (4453, 4640) | khác nhau do thiết kế/drift |
| BE, trailing | `_update_open_positions_and_manage` 9175–9230 | 4476–4481, 4502–4507, 4669–4675, 4692–4698 | hai bản độc lập; backtest bỏ qua tolerance/`min_trail_step` |
| Map khung thời gian | `tf_sec_map` 7416, `fetch_mt5_candles` 10121, `_get_tf_minutes` 8087 | `tf_map` 4277, `interval_map` 4284 | 5 bản |
| Hợp đồng/oz (100 / 1.0) | `_calc_target_pnl` 8876 | 4275 | tools/replay_gate.py có thêm 1 bản (`OZ`) |
| Spread/chi phí | tick thật | 0.20 / 6.4 cố định (4714) hoặc spread S1 nhúng | khác nhau do thiết kế |
| **Lặp NGAY TRONG live** | chia đôi Max SL: worker 11328–11345 + `execute_mt5_order` 10461–10463 + pending 8738 (`min_rr`) · khóa ngày/cooldown: worker 11363/11368, pending 8658, execute 10348/10351 · khung giờ cấm: worker 10848, pending 8670 · spread: worker 11388, pending 8733 · PA: worker 11401, pending 8758 | — | cùng luật kiểm ở 2–3 nơi, mỗi nơi tự đọc cfg |
| Không có trong backtest (không phải bản sao) | PA, cooldown, `max_pos`/nhồi lệnh, khóa ngày, khung giờ cấm, spread/`order_check`, một lệnh/nến | — | xem CLAUDE.md |

## 5. Thứ tự tách đề xuất (từ ít rủi ro đến nhiều) — không đổi logic
| Bước | Nội dung | Rủi ro / lưu ý |
|---|---|---|
| 0 | Giữ 129 test + `tools/replay_gate.py` làm lưới an toàn; chụp nhanh kết quả backtest ngắn trước/sau mỗi bước | Test patch `b.mt5`, `b.time`, `b.SETTINGS_DIR`...: khi chuyển hàm sang module khác phải giữ lối vào cũ (re-export) hoặc sửa test |
| 1 | Hằng/hàm thuần: `is_trading_window_blocked`, `build/format/export_backtest_report`, `DEFAULT_PROFILE_SETTINGS`, `MODEL_CONFIGS` | Thấp. Kiểm đường import của `tools/run_backtest.py` |
| 2 | Telegram (api, hàng đợi, lệnh, thông báo) | Thấp–vừa: gọi từ luồng UI/nền; `_handle_telegram_command` gọi `start/stop_auto_scan` qua `root.after`; A35 (biến Tk trong luồng nền) |
| 3 | kronos_engine (predictor, `_fast_predict_scenarios`, sampling, dọn bộ nhớ) | Thấp–vừa: giữ `_ai_inference_lock`, giới hạn luồng `min(4, cpu//2)`; không đổi seed/nhiệt độ (kết quả backtest phải y hệt) |
| 4 | Bộ lọc cấu trúc + Price Action thành hàm thuần (nhận cfg/DataFrame, không đọc `self`) | Vừa: `structure_conflict_mode` đang đọc cfg của profile ACTIVE (9777); PA đọc nến qua `fetch_mt5_candles` |
| 5 | mt5_gateway (một lớp bọc `_mt5_lock`, `_positions_get_checked`, nến, tick, deals, order) | Vừa–cao: 27 hàm gọi `mt5.*`; mọi test mock `b.mt5`; `tools/run_backtest.py` thay `bot_mod.mt5` bằng proxy chỉ-đọc; quy tắc lọc magic phải giữ |
| 6 | Dùng chung luật "tín hiệu" giữa live và backtest (đồng thuận, phân tán, cổng chi phí, ATR, SL) | **Cao về kết quả số**: backtest hiện đã khác live; hợp nhất sẽ đổi kết quả backtest cũ (cần quyết định riêng, dù không đổi logic live) |
| 7 | Risk gates (khóa ngày, cooldown, khung giờ, max_pos) sau một đối tượng trạng thái | **Cao**: trạng thái kép `self`/`ctx`; 4 nơi đọc (worker, pending, execute, UI); A02/A10 liên quan |
| 8 | Tách trạng thái theo profile (`ProfileState`) thay cho `self.x` ↔ `ctx` | **Rất cao**: ảnh hưởng gần như mọi hàm; làm cuối hoặc cùng bước 7 |
| 9 | position_manager (tách khỏi vẽ Treeview và Telegram trong `_update_open_positions_and_manage`) | **Cao**: đang chạy trên luồng UI và qua `_dispatch_pos_action_async`; A37/A38 liên quan |
| 10 | Execution: `execute_mt5_order` + canh vùng + zone_watch trong worker, gom luật chia đôi Max SL/RR về một nơi | **Cao nhất**: tiền thật, 3 chỗ lặp luật, A03 race; nên có replay_gate chạy trước/sau |
| 11 | Worker `_run_analysis_worker` còn lại + UI (`_update_ui_results`, chart) | Cao ở worker; UI thấp nhưng nhiều |
| — | license/updater | KHÔNG tách/đụng logic; chỉ ghi nhận điểm chen (execute 10330, 8853, start_auto_scan 10718) |

**Chưa xác nhận:** ranh giới `~` trong worker; mức lệch số giữa 3 cách tính ATR; hệ quả `structure_conflict_mode` đọc cfg profile active khi quét profile khác; vùng 8853 thuộc hàm nào về mặt chức năng (đọc qua script, chưa mở); danh sách `self.x` chỉ là thống kê regex, có thể sót thuộc tính truy cập qua `setattr`/`getattr`.
