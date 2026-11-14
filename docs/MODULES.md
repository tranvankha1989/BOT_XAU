# Module giao dịch (trading_modules/): cập nhật 15/11/2026
Mục tiêu: mỗi phần logic là một module độc lập (hàm thuần, không đụng MT5/giao diện), sửa và test riêng được.
Mỗi module tách theo cách: giữ nguyên hành vi 100%; bot (`mt5_scalping_bot.py`) chỉ còn hàm mỏng đọc cấu hình rồi gọi module.
Kiểm bằng golden test: kết quả của hàm GỐC được ghi lại (tests/golden/*.json, sinh bằng tests/golden/gen_*.py TRƯỚC khi tách);
test so module mới và wrapper trong bot với golden trên hàng trăm ca nến giả lập.

| Module | Chức năng | Tách từ | Test |
|---|---|---|---|
| `structure.py` | Cấu trúc thị trường BOS/CHoCH/swing, order-flow, quét râu; xung đột đa khung theo `conflict_mode` | `_analyze_scalping_micro_macro_context` | tests/test_structure_module.py (204 ca) |
| `price_action.py` | Xác nhận Price Action trước khi vào lệnh (chặn bắt dao rơi) | `_check_price_action_entry_confirmation` (phần tính) | tests/test_price_action_module.py (175 ca) |
| `indicators.py` | ATR | `calculate_atr` | tests/test_indicators_sampling_module.py |
| `kronos_sampling.py` | Nhiệt độ/top-p thích ứng cho Kronos (ảnh hưởng khóa cache backtest) | `_compute_adaptive_sampling_params` | như trên |
| `trading_window.py` | Khung giờ cấm giao dịch | `is_trading_window_blocked` | tests/test_patch.py, test_pb_diag.py |
| `risk.py` | Max SL hiệu lực của vùng canh (đo RR tại điểm vào dự kiến) | `calc_effective_max_sl` | tests/test_effective_max_sl.py |
| `backtest_report.py` | Thống kê, định dạng, xuất CSV backtest | `build/format/export_backtest_*` | tests/test_bt_report.py, test_bt_records.py |

Giữ nguyên ở file chính (chưa tách): lõi Kronos (`_fast_predict_scenarios`, gắn chặt với trọng số và khóa AI), luồng quét và ra quyết định
(`_run_analysis_worker`, ~640 dòng), canh vùng, đặt lệnh, quản lý lệnh (BE/trailing), khóa lãi/lỗ ngày, cổng MT5, giao diện, backtest engine.

## Việc tách tiếp theo (theo thứ tự rủi ro tăng dần)
1. Quyết định tín hiệu (đồng thuận, phân tán, độ tin cậy, SL/TP): hiện lặp giữa `_run_analysis_worker` và `_evaluate_bt_ai_candle` -> dùng chung 1 hàm (sẽ làm backtest và live khớp nhau; kết quả backtest cũ có thể đổi nhẹ).
2. Cổng rủi ro (khóa ngày, cooldown, spread, max_pos).
3. Quản lý lệnh mở (BE, trailing, đảo chiều).
4. Cổng MT5 (mọi `mt5.*` qua một chỗ), thực thi lệnh, giao diện.
5. Module mới: chế độ thị trường (ghi nhãn trước), trade_plan (kiểm tra SL/TP/entry, chế độ ghi nhận trước).

## Lưu ý phát hành
Updater ghi đè các file trong gói zip; gói phải chứa cả thư mục `trading_modules/` (từ v1.3.9). Bản đóng gói (launcher/binary_packer) cần kiểm tra có gồm thư mục này không (chưa kiểm).
