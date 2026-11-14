# P56: backtest 6 đoạn (M5 phân tích + M1 scalp, model AI-base, seed 42, lot 0.02, vốn 1000$, spread cố định 0,20, KHÔNG commission/trượt giá)
Nguồn: tools/scratch/p51_run_*, p55_*, p56_*. Tính lại từ CSV (BUY/SELL, PF, thắng). A = lọc on + neutral (đang dùng), C = lọc on + analysis, B = lọc tắt.
Mỗi ô: số lệnh / lãi $ / PF / thắng% / maxDD $ | BUY lệnh/$ | SELL lệnh/$

| Đoạn | A (hiện tại) | C (analysis) | B (tắt lọc) |
|---|---|---|---|
| D1 10-14/8 | 79 / -16,6 / 0,89 / 34% / -49,7 \| B 29/-25,0 \| S 50/+8,4 | 63 / -36,8 / 0,72 / 32% / -43,3 \| B 25/-21,1 \| S 38/-15,7 | 183 / +37,0 / 1,10 / 35% / -78,8 \| B 69/-9,3 \| S 114/+46,3 |
| D2 17-21/8 | 80 / +11,7 / 1,08 / 41% / -56,1 \| B 22/-7,5 \| S 58/+19,2 | 67 / +33,3 / 1,30 / 42% / -41,5 \| B 16/+4,5 \| S 51/+28,9 | 218 / -35,9 / 0,92 / 38% / -119,9 \| B 66/-52,3 \| S 152/+16,4 |
| D3 24-28/8 | 68 / +64,8 / 1,51 / 47% / -35,8 \| B 23/+51,9 \| S 45/+12,9 | 57 / +59,6 / 1,63 / 47% / -42,1 \| B 19/+59,4 \| S 38/+0,2 | 160 / +61,0 / 1,20 / 38% / -58,0 \| B 66/+59,2 \| S 94/+1,8 |
| D4 7-11/9 | 67 / -51,7 / 0,64 / 24% / -57,4 \| B 49/-35,4 \| S 18/-16,3 | 54 / -35,5 / 0,68 / 26% / -47,4 \| B 38/-21,9 \| S 16/-13,6 | 157 / -90,3 / 0,74 / 32% / -106,9 \| B 111/-35,7 \| S 46/-54,6 |
| D5 5-9/10 | 107 / +53,9 / 1,33 / 44% / -40,2 \| B 53/+46,7 \| S 54/+7,2 | 91 / +74,9 / 1,57 / 46% / -32,3 \| B 47/+49,2 \| S 44/+25,7 | 232 / -27,6 / 0,94 / 39% / -94,3 \| B 108/+41,0 \| S 124/-68,7 |
| D6 2-6/11 | 87 / -0,8 / 0,99 / 44% / -49,0 \| B 29/+8,1 \| S 58/-8,8 | 70 / -8,1 / 0,93 / 40% / -39,3 \| B 24/+17,4 \| S 46/-25,4 | 166 / -3,0 / 0,99 / 37% / -84,0 \| B 48/-0,7 \| S 118/-2,3 |
| **Tổng** | **488 lệnh / +61,5$ / 0,13$ mỗi lệnh** | **402 / +87,4$ / 0,22$** | **1116 / -58,7$ / -0,05$** |

Cache AI: các lần C/B trúng ~100% (đoạn mới mỗi A ~36-37 phút, C/B ~30 giây).

## Kết luận
1. **Bộ lọc cấu trúc BẬT tốt hơn TẮT: bằng chứng khá rõ.** A hơn B ở 5/6 đoạn (trừ D1), tổng +120$; DD của A thấp hơn B ở 6/6 đoạn. Giữ `trend_filter` bật.
2. **A (neutral) vs C (analysis): chưa phân biệt được.** C hơn tổng +26$ nhưng chỉ hơn A ở 3/6 đoạn (D2, D4, D5), kém ở 3/6 (D1, D3, D6). DD của C thấp hơn ở 5/6. Chênh nhỏ hơn dao động giữa các đoạn -> KHÔNG đổi (giữ neutral).
3. **Chiến lược chưa có lợi thế đủ chắc.** 3/6 đoạn lỗ hoặc hòa (D1, D4, D6), PF dao động 0,64 đến 1,51. Lãi trung bình chỉ 0,13$/lệnh (A) trên backtest chưa có commission/trượt giá; chỉ cần chi phí thật cao hơn ~0,13$/lệnh là hết lãi. Đây là mức "chưa khả thi để tăng lot", chỉ đủ tiếp tục demo.
4. Các đoạn kém (D1, D4) có tỷ lệ thắng 24-34%: đoạn D4 BUY lỗ -35$ (49 lệnh) -> hợp với giả thuyết "bắt đáy trong xu hướng giảm" (chưa kiểm chứng, cần gắn nhãn chế độ thị trường để xác nhận).
5. BUY/SELL: tổng A BUY +38,8$ / SELL +22,6$; không lệch hẳn một phía.

## Đề xuất (mức tự tin)
1. Giữ nguyên `trend_filter` bật + `neutral` (cao).
2. Thêm commission + trượt giá + spread theo cấu hình vào backtest (`run_backtest.py`, tham số) để biết lợi thế thật sau chi phí (cao; chỉ sửa công cụ, không đổi live).
3. Cho backtest mô phỏng luật Max SL (có/không chia đôi, canh vùng) để kiểm cơ chế gây 0 lệnh đêm 13/11 trên nhiều đoạn (trung bình; backtest hiện KHÔNG có cơ chế này, nên phần này chưa đo được).
4. Module chế độ thị trường (chỉ ghi nhãn trong backtest) để xem lệnh thua tập trung ở chế độ nào, rồi mới xét chặn (thấp-trung bình, cần dữ liệu).
5. Chưa nên tăng lot hay lên tài khoản thật; chờ kết quả demo thật + chi phí thật.
