# P55: bộ lọc cấu trúc, 3 đoạn (seed 42, lot 0.02, vốn 1000$, cache AI)
Claude đọc trực tiếp file tools/scratch/p55_*.txt (D1, D3) + số P51-G (D2 = 17-21/8). Cache: C và B trúng ~100%.

| Đoạn | A neutral (lệnh/$/PF) | C analysis | B lọc tắt |
|---|---|---|---|
| D1 10-14/8 | 79 / -16,57 / 0,89 | 63 / -36,81 / 0,72 | 183 / +37,03 / 1,10 |
| D2 17-21/8 | 80 / +11,71 / 1,08 | 67 / +33,33 / 1,30 | 218 / -35,87 / 0,92 |
| D3 24-28/8 | 68 / +64,83 / 1,51 | 57 / +59,60 / 1,63 | 160 / +60,99 / 1,20 |
| **Tổng** | **227 / +59,97** (0,26$/lệnh) | **187 / +56,12** (0,30$/lệnh) | **561 / +62,15** (0,11$/lệnh) |

## Kết luận
- Kết luận từ P51-G (1 đoạn: "lọc giúp, analysis tốt hơn") KHÔNG đứng vững: D1 thì ngược hoàn toàn (B lãi, C lỗ nhiều nhất).
- Cả 3 cách cho tổng gần bằng nhau (+56 đến +62$); chênh nhỏ hơn nhiễu giữa các đoạn. Lọc chỉ giảm số lệnh (227 vs 561) và lãi/lệnh cao hơn, DD tổng thể thấp hơn trong 2/3 đoạn.
- Quyết định: KHÔNG đổi `structure_conflict_mode` hay `trend_filter` (không có bằng chứng). Giữ nguyên.
- Lưu ý: hòa vốn ở chi phí ~0,26$/lệnh; backtest chưa có commission/trượt giá, nên lợi thế chung vẫn chưa rõ.
