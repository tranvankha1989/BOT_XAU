# Kiểm tra backtest so với bot live (16/11/2026)

Viết cho: Claude các phiên sau và người dùng. Nguồn: đọc code `_evaluate_bt_ai_candle` + `_run_backtest_simulation`
(mt5_scalping_bot.py ~3837-4543) so với luồng live (phân tích AI ~10400-10950, `_check_pending_pullback_order`, quản lý lệnh).

## A. Thông số trong file cấu hình mà backtest KHÔNG dùng
| Ô cài đặt (khóa) | Live dùng để | Backtest |
|---|---|---|
| Số nến dự báo (`pred_len`) | AI dự báo N nến (cấu hình thật = 8) | **Cố định 6** → dự báo, biên độ, SL/TP khác live |
| Lot (`lot`) | Khối lượng lệnh | Lấy từ `--lot` (từ 16/11 lấy theo `--config`) |
| Spread tối đa (`max_spread`) | Cổng chặn khi spread cao | Chỉ dùng ước lượng spread = 50% giá trị này cho ngưỡng bù phí (live dùng spread thật) |
| R:R (`rr`) = 0 | TP = TP của AI | **TP luôn = 2R** |
| Xác nhận PA, Số điều kiện PA, PA Fail-Open | Cổng chờ nến xác nhận | **Không có** |
| Số lệnh tối đa (`max_pos`), Chế độ nhồi (`entry_mode`), Khoảng cách nhồi (`min_entry_spacing`) | Nhiều lệnh, nhồi lệnh | **Chỉ 1 lệnh mỗi lúc** |
| Chỉ 1 lệnh mỗi cây nến (`one_order_per_candle`) | Khóa nến | Không có |
| Khung giờ cấm (`blocked_trading_windows`) | Hủy canh vùng trong giờ cấm | Không có |
| Hành động khi hết giờ giữ (`max_hold_action`) | Chốt nếu lãi / dời TP về hòa nếu lỗ... | **Đóng ngay theo giá nến** |
| Đảo chiều: `auto_rev_close`, `rev_consensus`, `rev_be_consensus`, `rev_min_move`, `rev_loss_mode` | Đóng/siết lệnh khi AI đảo chiều | Không có |
| Rổ lệnh: `basket_tp_usd`, `basket_cooldown_mins` | Chốt cả rổ | Không có |
| Giới hạn ngày: `daily_tp_usd`, `daily_sl_usd`, `max_consec_loss` | Dừng giao dịch trong ngày | Không có |
| Nghỉ: `cooldown_mins`, `fast_sl_cooldown_candles`, `fast_sl_cooldown_mins` | Tạm dừng sau thua | Không có |

## B. Logic khác live (dù có dùng thông số)
1. **Chia đôi SL tối đa khi R:R < 2R** (`calc_effective_max_sl_ai`): live có, backtest không.
2. **Cổng R:R thực tế ≥ 2R lúc khớp**: live có, backtest không.
3. **Xét lại cấu trúc lúc khớp (v1.3.17)**: backtest không.
4. **Hủy vùng canh ở chế độ S1**: backtest hủy khi giá chạm SL THẬT theo giây; live hủy khi nến M5 ĐÓNG CỬA vượt SL AI.
5. **Giá vào ở chế độ nến**: backtest khớp đúng giá biên vùng; live khớp ở giá tick lúc mọi cổng đạt.
6. **Vùng canh sau khi khớp**: live giữ vùng cho nhịp hồi tiếp (đến max_pos); backtest bỏ vùng.
7. **ATR ở vòng S1/nến**: backtest tự tính trung bình TR 13 nến; live dùng `calculate_atr` (cần đối chiếu công thức).
8. **Chi phí**: chế độ nến trừ spread cố định 6.4 (BTC) / 0.20 (XAU); không commission/slippage/swap.
9. **Quản lý lệnh (BE, trailing)**: backtest dùng `step_position`; chưa đối chiếu từng bước với bộ quản lý lệnh live.

## C. Hướng sửa đề xuất
- Cách 1 (vá từng chỗ trong backtest hiện có): nhanh từng bước nhưng là code thứ hai bắt chước live, sẽ lệch lại mỗi khi sửa live.
- Cách 2 (khuyến nghị): **bộ phát lại (replay)** chạy CHÍNH các hàm của bot live trên dữ liệu tick quá khứ, với MT5 giả lập
  (cấp nến/tick theo thời gian mô phỏng, giữ danh sách lệnh, khớp SL/TP theo tick). Mọi module dùng chung code với live
  nên tự động giống; sửa live là backtest đổi theo. Đã có nền: `tools/replay_gate.py` phát lại tick vào `_check_pending_pullback_order`.
  Khó: code live dùng `time.time()`, luồng, giao diện Tk → cần đồng hồ giả và tắt UI. Chỉ chạy được ở độ phân giải tick/S1.

## D. Đã làm (16/11/2026): Cách 2 = `tools/live_replay.py`
- Sàn giả `FakeBroker` (tick/nến thật, khớp SL/TP từng tick, deals/orders), đồng hồ giả (time/datetime), luồng chạy đồng bộ.
- Nhịp như live: vòng tick 1s; vòng nến 2s, phát hiện nến +1.6s; AI xong sau 8.5s (đo từ log live 16/11).
- Kiểm chứng BTC 16/11 theo `Cấu Hình 1/1.json`: tín hiệu 18:45 trùng live (SL 64167.67 vs 64167.71, PA từng dòng giống), lệnh 19:00 giá vào trùng 64387.48.
  Lệnh 13:14/13:15 khớp sát. Chưa giải thích: lệnh 06:15 thoát khác (live -1.55, replay SL -3.86) và replay có thêm 07:45, 09:00
  (có thể do cấu hình live trong ngày khác file xuất lúc 18:29, hoặc AI ngẫu nhiên) -> cần log live để đối chiếu.
