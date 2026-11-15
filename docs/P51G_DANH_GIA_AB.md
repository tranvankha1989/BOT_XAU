# P51-G: đánh giá A/B/C bộ lọc cấu trúc (17–21/8, seed 42, lot 0.02, vốn 1000$)

Số liệu do Gemini báo (chưa tự kiểm lại từ file tools/scratch/p51_run_*.txt).

| | A (lọc on, neutral) | B (lọc off) | C (lọc on, analysis) |
|---|---|---|---|
| Lệnh | 80 | 218 | 67 |
| PnL | +11,71$ | -35,87$ | +33,33$ |
| PF | 1,08 | 0,92 | 1,30 |
| Thắng | 41,2% | 37,6% | 41,8% |
| Max DD | -56,11$ | -119,91$ | -41,48$ |
| BUY | 22 / -7,54$ | 66 / -52,32$ | 16 / +4,47$ |
| SELL | 58 / +19,25$ | 152 / +16,45$ | 51 / +28,86$ |

## Đọc kết quả
1. Cờ có tác dụng thật: A và C khác nhau (C bớt 13 lệnh: 6 BUY, 7 SELL, tổng -21,62$ ở A). CSV/log ghi đúng cờ ở cả 3 lần.
2. A vs B: bộ lọc loại 138 lệnh, và B lỗ. Lọc đang giúp, chủ yếu ở phía BUY (B: BUY -52$ so với A: -7,5$). SELL gần như không đổi.
3. C vs A: bớt 13 lệnh xấu, nhưng chỉ là 13 lệnh trong 1 tuần.

## Điểm chưa chắc
- Chỉ 1 đoạn 5 ngày. Chênh C so với A là 21,62$ trên 13 lệnh: quá ít để kết luận, KHÔNG đủ cơ sở đổi luật.
- Đoạn 17–21/8 là đoạn trung bình trong 3 đoạn cũ (đoạn 1 lỗ, đoạn 3 lãi mạnh): không biết C có hơn A ở 2 đoạn kia không.
- Gemini nói "67 lệnh của C là tập con chính xác của 80 lệnh của A": hơi lạ vì backtest chỉ giữ 1 lệnh mỗi lúc (bỏ 1 lệnh thường làm các lệnh sau lệch). Chưa kiểm.
- Gemini không đếm được macro_dir/timeframe_conflict vì CSV không có cột này (không phải lỗi, chỉ là cách kiểm không đủ).
- Backtest không có PA, cooldown, max_pos, giới hạn ngày: không đại diện live.

## Việc nên làm tiếp (cần người dùng OK mới đổi gì)
- Dùng cache AI (P53-C) để chạy C ở 2 đoạn còn lại (10–14/8, 24–28/8) với chi phí thấp, rồi mới xét đổi `structure_conflict_mode`.
- Không đổi luật theo số của 1 đoạn.
