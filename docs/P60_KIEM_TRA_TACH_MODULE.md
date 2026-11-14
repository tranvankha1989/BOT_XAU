# P60: kiểm tra sau khi tách module (15/11/2026)
Mục tiêu: code đã tách (trading_modules/) phải cho kết quả GIỐNG HỆT code cũ.

## Kết quả
1. **Backtest 6 đoạn dữ liệu cũ (10-14/8, 17-21/8, 24-28/8, 7-11/9, 5-9/10, 2-6/11), cấu hình thật:** code mới so với code cũ (p59_base_*): số lệnh, giờ vào/ra, loại lệnh, giá vào/ra, lãi/lỗ, lý do thoát GIỐNG HỆT ở cả 6 đoạn (63/67/57/54/91/70 lệnh). Cache AI trúng 100% (khóa cache gồm nhiệt độ/top-p từ module lấy mẫu -> module đó cũng không đổi).
2. **Lõi Kronos trên mô hình THẬT** (tools/verify_engine.py): 4 ca (120/300/450 nến, 8/16 kịch bản, nhiều nhiệt độ/top-p): kịch bản và log_prob GIỐNG HỆT.
3. **Test mock:** 174 test đạt; mỗi module có test so với golden (kết quả hàm gốc ghi lại trước khi tách) trên hàng trăm ca nến giả lập.

## Điều CHƯA kiểm (cần biết)
- Chỉ backtest chế độ nến (M5/M1). Chế độ tick S1 chưa chạy lại (module ghép tick có golden 26 ca riêng).
- Đường LIVE (luồng quét, canh vùng, đặt lệnh) chỉ được kiểm qua test mock và các đoạn dùng chung với backtest (cấu trúc, Price Action, chấm điểm kịch bản). Chưa chạy bot thật sau khi tách -> cần phiên demo thật trước khi phát hành (đề nghị v1.3.9 sau khi demo ổn).
- Đóng gói (launcher, binary_packer, Trading AI PRO.spec) chưa kiểm có gồm thư mục trading_modules/ không.
