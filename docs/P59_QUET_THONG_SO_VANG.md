# P59: quét thông số vàng quanh cấu hình thật (6 đoạn: 10-14/8, 17-21/8, 24-28/8 | 7-11/9, 5-9/10, 2-6/11; seed 42; lot 0.02)
Cấu hình gốc ("base") = cấu hình thật: analysis, trend_filter on, consensus 80, sl_buffer 2.5, Max SL 4, trail 2.0, BE 1.0R, min_move 1.2. Mỗi dòng đổi MỘT thông số.
Backtest dùng TP = 2R (live dùng rr=0 = TP theo AI) và lot cố định, nên đổi Max SL làm đổi cả rủi ro mỗi lệnh: so sánh theo R (lãi/rủi ro) công bằng hơn so sánh theo $.

| Cấu hình | Lãi $ (6 đoạn) | Lệnh | R mỗi lệnh | Max DD $ | Hơn base ($) ở n/6 đoạn | Nửa tháng 8 | Nửa tháng 9-11 |
|---|---|---|---|---|---|---|---|
| **base** | +87,4 | 402 | +0,028 | -47,4 | - | +56,1 | +31,3 |
| be_trigger_r 1.5 | +226,0 | 409 | +0,074 | -48,0 | 5/6 | +109,0 | +117,0 |
| consensus 75 | +193,9 | 483 | +0,053 | -68,7 | 4/6 | +78,1 | +115,7 |
| max_sl 6 | +487,0 | 974 | +0,046 | -91,0 | 5/6 | +421,8 | +65,2 |
| max_sl 5 | +269,6 | 678 | +0,043 | -62,4 | 4/6 | +190,0 | +79,6 |
| max_sl 3 | +99,3 | 212 | +0,087 | -28,8 | 3/6 | +15,0 | +84,3 |
| sl_buffer 1.5 | +152,5 | 668 | +0,032 | -65,5 | 4/6 | +120,2 | +32,3 |
| sl_buffer 3.5 | +75,0 | 215 | +0,048 | -57,6 | 3/6 | +6,9 | +68,1 |
| trail 3.0 | +110,1 | 402 | +0,036 | -47,4 | 2/6 | +53,7 | +56,3 |
| trail 1.0 | +78,5 | 412 | +0,025 | -47,9 | 2/6 | +15,2 | +63,3 |
| min_move 2.0 | +60,2 | 344 | +0,022 | -52,5 | 3/6 | +11,6 | +48,6 |
| consensus 85 | +16,8 | 328 | +0,006 | -63,9 | 1/6 | -24,5 | +41,4 |
| be_trigger_r 0.7 | -92,9 | 397 | -0,033 | -59,3 | 0/6 | -22,3 | -70,5 |

## Kết luận (thẳng thắn)
1. **Không có thông số nào tốt hơn rõ ràng ngoài mức nhiễu.** Lãi trung bình mỗi lệnh theo R của mọi cấu hình chỉ 0,0 đến 0,09R. Sai số chuẩn của lãi mỗi lệnh với ~400 lệnh khoảng 0,05R: mọi chênh lệch (kể cả be 1.5: 0,074 so với 0,028) nhỏ hơn hoặc ngang sai số.
2. Max SL 5-6 cho nhiều $ hơn chỉ vì rủi ro mỗi lệnh lớn hơn và nhiều lệnh hơn (R mỗi lệnh không cải thiện, DD gấp đôi): KHÔNG phải cải thiện lợi thế.
3. Hướng nhất quán nhất (ứng viên theo dõi, không đổi): `be_trigger_r` 1.5 (hơn ở 5/6 đoạn, cả hai nửa, DD ngang base) và `consensus` 75 (cả hai nửa nhưng DD tăng). Ngược lại: BE 0.7, consensus 85 và min_move 2.0 đều xấu hơn base.
4. Cấu hình hiện tại của bạn (base) nằm trong vùng ổn. Quyết định: **KHÔNG đổi thông số nào** (thông số thật nằm trong cài đặt của bạn, mình cũng không đổi thay bạn). Muốn thử: đặt `be_trigger_r = 1.5` trong Mục 3 ở một tài khoản demo riêng và so sánh sau 2-3 tuần.
5. Cảnh báo: backtest chưa có xác nhận Price Action, cooldown, giới hạn ngày, kiểm spread; TP 2R khác live (rr=0); chưa có commission và trượt giá. Kết quả quét 13 cấu hình trên cùng dữ liệu cũng làm tăng nguy cơ chọn nhầm do may mắn: càng thử nhiều càng dễ có một cấu hình trông tốt.
