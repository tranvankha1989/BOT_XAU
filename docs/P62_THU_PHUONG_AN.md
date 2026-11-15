# P62: thử các phương án cải thiện (vàng, 7 đoạn, seed 42, lot 0,02, analysis + trend on)
Công tắc thử nghiệm MỚI (chỉ backtest chế độ nến, mặc định TẮT, đặt bằng `--set`): `exp_min_sl` (bỏ lệnh SL hẹp), `exp_confirm` (chờ nến M1 đóng đúng chiều rồi vào ở giá đóng), `exp_runner` (lệnh đã dời BE bỏ TP), `exp_daystop` (dừng ngày sau N lệnh thua liên tiếp), `exp_realfill` (lệnh chờ khớp đúng giá vùng).

## ⚠️ Hai kết quả đầu là ẢO ẢNH do cách mô phỏng (đã phát hiện và loại bỏ)
- Backtest cũ khớp lệnh ở `min(giá đóng nến, giá vùng)`: nến đóng thấp hơn vùng thì lấy giá đóng (tốt hơn thực tế, SL gần hơn). Hai thứ "ăn" từ lỗi này:
  - Phát hiện P61 "SL hẹp thua, SL rộng thắng" = chọn theo việc nến có đóng bật lại hay không (nhìn trước tương lai).
  - Lần chạy `confirm` đầu cho +335$ (PF 1,98) vì chọn nến theo màu nến đóng nhưng vẫn khớp ở giữa nến. Sau khi sửa đúng (vào ở giá đóng nến xác nhận): -13,5$.
- Khớp lệnh thực tế (`exp_realfill=1`): cấu hình hiện tại **-92$, PF 0,90, R -0,025**, thua rõ so với con số cũ +52$ (R +0,015). Vậy số backtest cũ (P59/P61) lạc quan, và P61 mục "SL hẹp" bị bác bỏ.

## Kết quả (tổng 7 đoạn; khớp lệnh thực tế hoặc vào ở giá đóng nến)
| Phương án | Lãi $ | Lệnh | Thắng % | R/lệnh | PF | Max DD $ | Ngày lãi | Đoạn dương |
|---|---|---|---|---|---|---|---|---|
| Hiện tại, khớp thực tế | -91,9 | 441 | 38 | -0,025 | 0,90 | 135 | 13/31 | 2/7 |
| + lệnh chạy (bỏ TP sau BE) | -147,4 | 427 | 37 | -0,041 | 0,84 | 161 | 10/31 | 1/7 |
| + dừng ngày sau 3 thua | -38,3 | 263 | 38 | -0,017 | 0,93 | 105 | 10/31 | 3/7 |
| + BE 1,5R | -48,7 | 447 | 45 | -0,012 | 0,95 | 111 | 15/31 | 3/7 |
| Xác nhận nến | -13,5 | 110 | 37 | -0,019 | 0,92 | 44 | 11/30 | 2/7 |
| Xác nhận + dừng ngày 3 thua | -16,4 | 104 | 37 | -0,026 | 0,90 | 44 | 12/30 | 2/7 |
| Xác nhận + BE 1,5R | -49,0 | 110 | 44 | -0,073 | 0,75 | 55 | 11/30 | 3/7 |
| Xác nhận + consensus 85 | +10,2 | 92 | 39 | +0,018 | 1,07 | 47 | 11/30 | 3/7 |
| Xác nhận + min_move 2,0 | +16,2 | 87 | 40 | +0,028 | 1,13 | 32 | 13/28 | 3/7 |

## Kết luận
1. **Không phương án nào có lợi thế đáng tin.** Tốt nhất (xác nhận + min_move 2,0 / consensus 85) chỉ +10 đến +16$ trên 7 đoạn, ~87-92 lệnh, dương chỉ 3/7 đoạn: nằm trong nhiễu.
2. Xác nhận nến là cải thiện lớn nhất và nhất quán (từ -92$ lên -13$, DD 135 → 44, vì lọc bớt 330 lệnh xấu), nhưng chưa biến thành lãi.
3. Chiến lược "canh pullback theo AI" khi khớp thực tế có kỳ vọng hơi âm; gốc nằm ở chất lượng tín hiệu hướng của AI, không phải thông số.
4. Hạn chế: mô phỏng nến (chưa có PA/cooldown/giới hạn ngày như live); `exp_realfill` bỏ qua diễn biến trong nến sau khi khớp; 7 đoạn, 1 hồ sơ cài đặt; tick S1 không đổi.

## Hướng tiếp theo đề xuất
- Không đổi luật live dựa trên các phương án này.
- Đo độ chính xác hướng của mô hình AI ở 5-15 phút (nếu ~50% thì không có gì để tinh chỉnh); thử dùng AI như bộ lọc xu hướng thay vì canh pullback ngược.
- Đối chiếu với log live có PA (nếu PA lọc được nhóm "bắt dao rơi" thì live tốt hơn backtest).
