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
