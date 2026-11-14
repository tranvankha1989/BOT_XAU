# P58: backtest BTCUSDm (analysis M15, vào lệnh M5, lot 0.01, vốn 1000$, seed 42, cấu hình BTC hiện hành, spread cố định 6,4 giá)
Giá/biến động đo từ MT5 (chỉ đọc): BTC ~65.000; ATR14 trung vị M5 ~120 giá, M15 ~187; spread thật ~6,4 giá; hợp đồng 1 BTC; lot tối thiểu 0,01.
Mỗi ô: lệnh / lãi $ / PF / thắng% / maxDD $ | BUY lệnh/$ | SELL lệnh/$

| Đoạn | A (lọc on, neutral) | C (analysis) | B (tắt lọc) |
|---|---|---|---|
| 19-23/10 | 56 / +20,4 / 1,91 / 70% / -4,1 \| B 11/+8,6 \| S 45/+11,8 | 42 / +7,8 / 1,38 / 64% / -7,5 | 139 / +25,3 / 1,37 / 64% / -6,2 |
| 26-30/10 | 66 / +26,9 / 1,97 / 70% / -5,6 \| B 17/+5,2 \| S 49/+21,6 | 50 / +22,4 / 2,11 / 72% / -3,9 | 143 / +21,9 / 1,28 / 61% / -8,3 |
| 2-6/11 | 54 / +13,6 / 1,54 / 67% / -10,7 \| B 13/+0,9 \| S 41/+12,6 | 50 / +13,9 / 1,63 / 68% / -9,2 | 153 / +19,7 / 1,23 / 58% / -14,4 \| B 64/-4,2 |
| **Tổng** | **176 / +60,9 / 0,35$ mỗi lệnh** | 142 / +44,1 / 0,31$ | 435 / +66,9 / 0,15$ |

## Nhận xét
- Cả 3 đoạn đều lãi ở cả 3 cách; PF 1,5-2,0 (A), thắng ~70%. Khác hẳn vàng (nhiều đoạn lỗ, thắng 24-47%).
- Lọc bật vs tắt: tổng lãi gần như nhau (+61 vs +67) nhưng lọc cho lãi/lệnh gấp ~2,3 lần và DD thấp hơn ở 3/3 đoạn. Giữ lọc bật, giữ neutral.
- Chi phí: spread chỉ 0,06$/lệnh. Lãi 0,35$/lệnh chịu được thêm ~0,3$/lệnh (= ~30 giá mỗi lệnh) mới hòa vốn: vùng đệm lớn so với vàng.
- Rủi ro mỗi lệnh ~1,3$ (SL ~130 giá, thấp hơn Max SL 350): hầu hết lệnh vào ngay, ít canh vùng.

## CẢNH BÁO (vì sao chưa kết luận "BTC tốt")
- Chỉ 3 tuần liên tiếp, một chế độ thị trường: BTC rơi từ ~78.000 xuống ~65.000. SELL chiếm phần lớn lãi (SELL +45,9$ vs BUY +14,7$ ở A). Chưa biết hiệu quả khi BTC tăng/đi ngang.
- Thắng 70% với TP 2R cao bất thường: có thể do BE/trailing biến nhiều lệnh thành "thắng nhỏ"; cần đọc kỹ cách backtest tính BE (chưa kiểm).
- Backtest vẫn không có xác nhận PA, cooldown, giới hạn ngày, kiểm spread; không có trượt giá BTC (thường lớn hơn vàng).

## Đánh giá thông số BTC mặc định
- Hợp lý: min_move 60 (~0,3 ATR M15), sl_atr 1,5, sl_buffer 20, Max SL 350 (> SL điển hình ~300), min_entry_spacing 50, be_plus_price 5 (đơn vị giá).
- Nên chỉnh (suy luận, backtest KHÔNG đo được vì không có cổng spread): `max_spread` 30 -> ~12-15 (spread thật 6,4, gấp 4,7 lần là quá rộng; bên vàng 1,5 lần). Người dùng tự chỉnh trên giao diện: cấu hình đang chạy nằm trong user_settings.json (không đọc được), đổi mặc định trong code không ảnh hưởng.
- Cần thêm: thử BTC ở đoạn BTC tăng/đi ngang (tháng 8-9) trước khi chạy demo BTC lâu dài; đề nghị demo BTC với lot 0,01.
