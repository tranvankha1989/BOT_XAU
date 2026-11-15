# P61: vì sao tỷ lệ thắng thấp? (7 đoạn vàng, cấu hình hiện tại, seed 42, lot 0.02)

Đoạn: 10-14/8, 17-21/8, 24-28/8, 7-11/9, 5-9/10, 2-6/11, 12/11 (ngày vàng sập). Cache AI trúng, mỗi lần chạy vài chục giây.

## Cách thoát lệnh (440 lệnh, cấu hình hiện tại)
| Loại thoát | Số lệnh | Thắng % | R/lệnh | Tổng $ |
|---|---|---|---|---|
| Chốt lãi (TP 2R) | 33 (7,5%) | 100 | +1,94 | +473 |
| Cắt lỗ (SL) | 54 (12%) | 0 | -1,06 | -392 |
| Hòa vốn (BE+) | 77 (17%) | 10 | +0,13 | +44 |
| Time-Stop (hết giờ) | 276 (63%) | 47 | -0,04 | -73 |

Tỷ lệ thắng gộp 39%, kỳ vọng +0,015R/lệnh (gần 0). Chỉ 7,5% lệnh chạm TP 2R; 63% lệnh bị đóng bởi Time-Stop gần hòa vốn.

## Quét Time-Stop và chế độ cấu trúc (tổng 7 đoạn)
| Cấu hình | Lãi $ | Lệnh | R/lệnh | Thắng % |
|---|---|---|---|---|
| base (Time-Stop theo cài đặt) | +52 | 440 | +0,015 | 39 |
| Time-Stop tắt | +54 | 350 | +0,020 | 24 |
| Time-Stop 15p | +20 | 383 | +0,006 | 31 |
| Time-Stop 30p | +1 | 361 | 0,000 | 26 |
| cấu trúc `scalp` | +1 | 445 | 0,000 | 38 |

## Kết luận
1. Mọi cấu hình đều R/lệnh ≈ 0 (trong khoảng nhiễu). Đổi thông số (P59 + P61) không tạo lợi thế. Win rate thấp/cao chỉ thay đổi theo cách thoát lệnh, không phải chất lượng: tắt Time-Stop làm win rate rơi 39% → 24% mà lãi không đổi.
2. Gốc vấn đề là **chiến lược/tín hiệu AI gần như không có lợi thế sau chi phí spread**, không phải một thông số cài sai.
3. Ngày lỗ nặng đều là ngày giá chạy một chiều (12/11, 7-11/9, 10-14/8): bot mua/bán đỡ ngược chiều. Bộ chặn cấu trúc giảm bớt nhưng không đảo ngược.
4. Hạn chế: backtest TP 2R (live dùng TP của AI), không có PA, cooldown, giới hạn ngày; 7 đoạn, chủ yếu 1 hồ sơ cài đặt. Cấu hình Time-Stop máy này khác máy chạy live (15p).

## Hướng khắc phục (chưa làm, cần thử thêm)
- A. Giảm rủi ro ngày xu hướng một chiều: giới hạn số lệnh ngược xu hướng liên tiếp / tạm dừng sau N lệnh thua liên tiếp (đã có `max_consec_loss`, cần đo).
- B. Nâng chất lượng tín hiệu: chỉ vào lệnh khi AI đồng thuận cao VÀ xu hướng M15/H1 cùng chiều (thử `trend_filter` chặt hơn); cần backtest riêng.
- C. Đánh giá lại mô hình AI (độ chính xác hướng ở 5-15 phút) trước khi tinh chỉnh luật nữa.

---
## Phần 2: nhìn như trader scalping (440 lệnh, cấu hình hiện tại; số liệu từ `tools/scratch/p61_diag2.py`)
**Theo thời gian giữ lệnh**
| Giữ lệnh | Số lệnh | Thắng % | R/lệnh |
|---|---|---|---|
| ≤1 phút | 26 | 23 | -0,07 |
| 1-3 phút | 44 | 7 | **-0,55** |
| 3-5 phút (hết Time-Stop) | 308 | 45 | -0,05 |
| >5 phút (đã dời BE/trailing) | 62 | 40 | **+0,80** |

**Theo độ rộng SL (rủi ro/lệnh, lot 0,02):** SL hẹp (rủi ro ≤7,9$, SL < ~4 giá): 191 lệnh, R -0,070, thắng 33%; SL rộng (>7,9$): 249 lệnh, R +0,081, thắng 44%. Nhóm hẹp kém hơn ở 6/7 đoạn (chênh ~0,15R, ~2 sai số chuẩn: có xu hướng, chưa chắc chắn). Phí spread chỉ ~5-6% rủi ro, không phải thủ phạm chính.

**Theo giờ (VN):** không có giờ nào nhất quán (R từ -0,03 đến +0,09), coi là nhiễu. BUY và SELL ngang nhau (R +0,03 / 0,00).

### Chẩn đoán
1. Lợi nhuận nằm ở ~14% lệnh "chạy được" (>5 phút, +0,8R); phần còn lại gần hòa hoặc lỗ. Đây là hệ thống hòa vốn sau phí, sống nhờ vài lệnh chạy; chuỗi lỗ dài là bình thường (thua liên tiếp tối đa 8 lệnh).
2. 44 lệnh dính SL trong 1-3 phút (-0,55R) là kiểu "bắt dao rơi": vào đúng lúc giá còn đang lao về phía ngược lại. Backtest không có xác nhận Price Action nên con số này có thể tệ hơn live; cần đo bằng log live có PA.
3. Lệnh SL hẹp dễ bị nhiễu quét (cùng lý do với mục 2).

### Phương án (xếp theo kỳ vọng, chưa làm)
- P1. SL tối thiểu (khóa mới `min_sl_price`, mặc định 0=tắt): bỏ lệnh có SL < ~3-4 giá. Cần cài vào cả backtest và live rồi chạy lại 7 đoạn mới kết luận (lọc ngoại tuyến thì lệnh kế tiếp thay đổi nên không đáng tin).
- P2. Chờ xác nhận đảo chiều trước khi vào (bắt buộc nến M1 đóng cùng chiều sau khi chạm vùng): nhắm vào nhóm 1-3 phút.
- P3. Cho lệnh chạy: bỏ TP cố định 2R khi đã BE (trailing), vì lãi nằm ở nhóm >5 phút.
- P4. Tắt giao dịch khi giá chạy một chiều (đã có `max_consec_loss`; cần đo mức phù hợp).
