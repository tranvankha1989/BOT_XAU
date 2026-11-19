# So sánh backtest trong app với bot live máy 2, ngày 19/11/2026

Nguồn: log và trace máy 2 (`logs.rar`, file cấu hình xuất 13:19), lượt backtest trong app `run_20261119_132029`
(19 lệnh, −24,62$), và 2 lượt phát lại `tools/live_replay.py` (tick, chạy chính code live).

## Kết luận
- Engine backtest chạy đúng như live. Khi đặt cùng điều kiện với máy 2 (cấu hình theo từng giờ, thời gian AI như máy 2),
  phát lại ra lại đủ 7 lệnh live. 5 lệnh trùng tới giây và giá: 03:44, 11:30, 12:28, 12:29, 13:10.
  2 lệnh lệch 1–2 phút: 04:31 → 04:33, 04:55 → 04:56.
- Kết quả trong app khác live (19 lệnh so với 7) vì 3 nguyên nhân nằm ngoài engine:
  1. Cấu hình máy 2 đổi 2 lần trong ngày, còn backtest dùng bản cuối (13:19) cho cả ngày.
  2. Bot máy 2 không chạy từ 05:05 tới 06:53.
  3. Thời gian AI: backtest mặc định 8,5 giây (đo trên máy này), máy 2 chỉ ~1,7 giây.
- Dữ liệu giá và AI không gây khác biệt: 138/138 nến cả hai bên cùng phân tích có giá đóng nến, dự báo AI và tín hiệu trùng hệt
  (`trading_modules/kronos_engine.py` đặt hạt giống theo dữ liệu nến).

## 1. Cấu hình máy 2 theo giờ (log "Đã áp dụng ... Nút Lưu Cài Đặt" + trace)
| Từ | SL tối đa | Hết giờ giữ lệnh (10 phút) |
|---|---|---|
| 00:00 | 3.0 | Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ) |
| 11:00:43 | 4.0 | Đóng tất cả (Bất kể Lãi/Lỗ) |
| 11:41:08 | 4.0 | Dời SL về hòa vốn (BE+) (= file 13:19, backtest dùng bản này cả ngày) |

Ảnh hưởng: với SL tối đa 4.0, backtest trong app vào thêm các lệnh mà máy 2 (3.0) không được vào: 01:08, 01:30, 01:32, 02:35, 03:41, 07:26, 07:50
(lượt phát lại AI 1,7 giây còn thêm lệnh 10:07). Chạy lại với 3.0 thì các lệnh này đều mất (mục 4, lượt B).

## 2. Bot máy 2 không chạy
- 05:04:57 → 06:31:28: không có dòng log hay trace nào, kể cả luồng nền (lệnh SELL 04:55 đang mở cũng không còn được theo dõi).
  Sàn mở lại lúc 06:00 nhưng bot không phân tích nến nào. `app_error.log` không ghi lỗi. Có thể máy ngủ, app bị tắt hoặc treo; cần kiểm máy 2.
- 06:31:38 → 06:53:33: bật AUTO xong nhưng không phân tích nến nào (06:35–06:50).
- Khởi động lại để cập nhật lúc 06:54 (v1.3.24) và 08:55 (v1.3.25).
- Backtest trong app có 5 lệnh trong khoảng 06:05–06:38 (tổng −16,35$).

## 3. Thời gian AI
- Máy 2 (136 nến): phát hiện nến sau mốc 1,51 giây (trung vị), AI 1,69 giây (1,42–4,27).
- Bộ phát lại mặc định: phát hiện 1,6 giây, AI 8,5 giây (đo trên máy này ngày 16/11).
- Ví dụ 12:25: máy 2 có kết quả AI lúc 12:25:01,9, kiểm tra Price Action lần đầu lúc 12:25:05,9 (chưa đạt) và mãi 12:28:00 mới vào lệnh.
  Backtest có kết quả lúc 12:25:10,1, lần kiểm tra đầu đạt ngay nên vào lệnh SELL 3731,91 → SL 12:34:31 (−12,51$), lệnh này live không có.

## 4. Kiểm chứng bằng phát lại (tick, code live, AI 1,7 giây, phát hiện 1,5 giây)
| Lệnh live máy 2 | Phát lại |
|---|---|
| SELL 03:44:02 @3732.34, đóng 04:50:03 (AI đảo chiều) +17,34$ | B: SELL 03:44:00 @3732.34, đóng 04:50:02 +17,36$ |
| SELL 04:31:02 @3727.59, đóng 04:50:03 +7,84$ | B: SELL 04:33:00 @3726.92, đóng 04:50:02 +6,53$ |
| SELL 04:55:05 @3723.93 SL 3729.15 (bot tắt lúc mở phiên) | B: SELL 04:56:00 @3724.12 SL 3729.15, SL lúc 06:00:30 −11,33$ |
| SELL 11:30:24 @3726.72, đóng khẩn cấp 11:40:25 +7,03$ | A: SELL 11:30:23 @3726.94, Gỡ vốn 11:38:24 +8,68$ (*) |
| SELL 12:28:00 @3733.07 SL 3738.03, SL 12:34:31 | A: SELL 12:28:00 @3732.97 SL 3738.03, SL 12:34:31 −10,40$ |
| SELL 12:29:02 @3734.24 SL 3738.03, SL 12:34:31 −7,58$ | A: SELL 12:29:02 @3734.24 SL 3738.03, SL 12:34:31 −7,73$ |
| BUY 13:10:01 @3733.14 SL 3729 TP 3748.5, đóng 13:10:11 +0,70$ | A: BUY 13:10:00 @3733.25 SL 3729 TP 3748.5, đóng 13:10:12 +0,75$ |

- A = cấu hình 13:19 (`--ai-latency 1.7 --detect-delay 1.5`), so khoảng 11:41–13:14. B = cấu hình buổi sáng
  (`--set max_sl_price=3.0 --set "max_hold_action=Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)"`), so khoảng 00:00–05:05.
  Ở B không còn lệnh thừa lúc 01:08–02:35, 07:26, 07:50, 10:07.
- (*) Hiệu ứng dây chuyền: "Gỡ vốn" chỉ chạy khi lỗ đã chốt trong ngày đang âm. Phát lại có thêm lệnh thua lúc máy 2 tắt nên âm
  (−8,41$) và đóng sớm; máy 2 đang dương nên không đóng. Tương tự trong app: lệnh 12:28 bị Gỡ vốn lúc 12:30 (+5,58$),
  còn live giữ tới SL 12:34.
- Lệch nhỏ còn lại: vòng kiểm tra vùng canh của live chạy mỗi ~10,3–10,4 giây (lệch dần), phát lại đúng 10 giây,
  nên thỉnh thoảng lệnh vào lệch 1–2 phút.

## 5. Lỗi Telegram phát hiện kèm (đã sửa, commit 5e059fe)
"Lãi ngày" trong tin đóng lệnh lấy từ bộ đệm 10 giây. Tin 12:34:35 báo +21,82$ nhưng chưa trừ 2 lệnh SL lúc 12:34:31 (~−18$).
Tin đóng lệnh trễ tới 15 giây vì danh sách lệnh đã đóng có bộ đệm 15 giây.

## 6. Cách đối chiếu lần sau
1. So với máy 2 thì dùng `--ai-latency 1.7 --detect-delay 1.5` (backtest trong app hiện vẫn dùng 8,5 giây).
2. Chia ngày theo các mốc "Đã áp dụng ... Nút Lưu Cài Đặt" trong log máy 2, chạy từng cấu hình, chỉ so lệnh trong khoảng của cấu hình đó.
3. Bỏ các khoảng bot live không chạy (không có dòng "Nến phân tích ... mới vừa đóng").
