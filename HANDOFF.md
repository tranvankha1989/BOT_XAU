# HANDOFF — cập nhật ngày 14/11/2026

Tài liệu bàn giao để hội thoại mới hiểu ngay trạng thái dự án. Quy tắc làm việc: xem `CLAUDE.md`. Danh sách lỗi: `AUDIT.md`.
Quy ước độ tin cậy: mục ghi "(người dùng)" là số liệu/nhận định người dùng cung cấp, chưa được xác nhận lại bằng repo trong lần cập nhật này.

## 1. Đã xong (nhánh `dev`; mô tả theo `git log`)
| Commit | Ngày | Nội dung |
|---|---|---|
| ded6451 | 13/11 | Giai đoạn 1 + backoff sửa SL/TP + khung giờ cấm + cổng RR |
| 4b7530f | 13/11 | Thêm thư mục `tests/` |
| 5f27faf | 13/11 | Thêm test hồi quy Giai đoạn 1 |
| ec4c815 | 13/11 | Siết `tools/demo_check.py` |
| f539c98 | 13/11 | Thêm `tools/run_backtest.py` |
| f559656 | 13/11 | run_backtest: lọc lệnh ngoài khoảng ngày, thêm `--seed` |
| 971e628 | 13/11 | Backtest: cổng ngày, đủ ngữ cảnh (nến warm-up) |
| 12cfa71 | 13/11 | AUDIT: bổ sung P10 |
| 1c17318 | 13/11 | run_backtest: thêm `--threads` (mặc định = lõi vật lý, chặn ở lõi vật lý; bot thật không đổi) |
| e1b4ada | 13/11 | P35-C: sửa A37/A39 (`_modify_position_sltp` đọc lại vị thế, không nới SL; `tighten_sl` dùng tick mới) |
| a20dab9 | 14/11 | Telegram: chỉ nhận đúng lệnh (A31); A32 bỏ qua theo quyết định người dùng |
| 592c86e | 14/11 | Log chẩn đoán canh vùng (cổng thoát im lặng, heartbeat 60s, bộ đếm khóa MT5 bận/ngoại lệ) |
| 0894ab9 | 14/11 | A02: kiểm tra kết quả đóng lệnh khi chạm ngưỡng ngày, thử lại (<= 1 lần/5 giây) |
| 82479b0 | 14/11 | A11: lưu/nạp `initial_r_cache` (ghi atomic), dọn ticket không còn mở |
| cf3cef8 | 14/11 | Thêm `tools/replay_gate.py` (phát lại tick vào `_check_pending_pullback_order`) |
| c255c08 | 14/11 | run_backtest: `--trend-filter` và `--structure-conflict-mode` (chỉ ảnh hưởng lần chạy) |
Test: 129/129 đạt (`python -m unittest discover -s tests`).

## 2. Phát hiện đêm demo 13/11
- 0 lệnh dù có tín hiệu: log có 17 vùng canh XAU (15 SELL, 2 BUY). Nguyên nhân: vùng canh đặt quá sâu — khi TP của AI < 2R thì Max SL bị chia đôi
  (4,0 -> 2,0, `effective_max_sl`), nên chỉ vào lệnh khi giá hồi sát SL của AI; bid không bao giờ vào vùng (cách Z 2,13 đến 11,24$ ở các ca SELL).
- `tools/replay_gate.py` (cf3cef8) phát lại 111.953 tick vào đúng hàm bot: 0 lệnh giả, khớp kết quả thật, ở cả poll 1000ms và 100ms; 8 tổ hợp biến thể
  (`--no-halve-max-sl`, `--tp-rr 2`, `--no-pa`) cũng đều 0 lệnh vì giá không tới vùng. Nghĩa là không thấy lỗi ẩn ở các cổng TRƯỚC vùng; các cổng SAU vùng
  (PA, RR, spread, max_pos) không được chạm tới ở kịch bản gốc (chỉ chạm khi nới vùng thử nghiệm).
- 34/52 tín hiệu đạt đồng thuận bị bộ lọc cấu trúc BOS/CHoCH chặn (người dùng).
- Đêm đó bot bị khởi động lại nhiều lần (log có các dòng "KHỞI ĐỘNG HỆ THỐNG" lúc 20:50, 20:56, 20:58).

## 3. Nhận xét về bộ lọc cấu trúc `_analyze_scalping_micro_macro_context` (CHƯA sửa — cần người dùng OK)
Ghi theo người dùng, chưa xác nhận lại bằng đọc code trong lần cập nhật này:
- Chỉ dùng nến đã đóng; đi ngang bị coi là xu hướng (không ngưỡng swing theo ATR); đảo chiều chậm (~11 nến).
- `structure_conflict_mode` mặc định `neutral`: M1 và M5 ngược nhau thì không chặn gì; M1 (60 nến) lấn M5.
- Cấu trúc không được kiểm lại lúc vào lệnh ở đường canh vùng (`_check_pending_pullback_order`).
- Help text ghi xung đột "M5 vs M15" (đã thấy ở mô tả cài đặt) nhưng code dùng khung scalp/phân tích theo cấu hình (người dùng: M5/M1).

## 3b. Tách module (15/11/2026)
- Logic giao dịch đã tách một phần ra `trading_modules/` (13 module: cấu trúc, Price Action, ATR, lấy mẫu Kronos, lõi Kronos, chấm điểm kịch bản, khung giờ cấm, Max SL, quy tắc quản lý lệnh mô phỏng, ngưỡng theo mã, ghép tick, báo cáo backtest). Xem docs/MODULES.md, docs/CAU_TRUC_DU_AN.md.
- Đã kiểm: backtest 6 đoạn cũ giống hệt code cũ, lõi Kronos giống hệt trên mô hình thật, 174 test (báo cáo cũ đã xóa, xem git lịch sử). Chưa chạy bot live sau khi tách; chưa phát hành (v1.3.9 chờ demo thật).
- Dữ liệu backtest gom vào `backtest_data/`; log chạy backtest trong `tools/scratch/` (không commit).
- Việc tách còn lại: luồng quyết định tín hiệu (SL/TP, cổng), cổng rủi ro, quản lý lệnh LIVE, cổng MT5, thực thi lệnh, giao diện.
- Cấu hình thật (đọc qua load_strategy_cfg): vàng dùng structure_conflict_mode=analysis, BTC tắt trend_filter, lot 0.05, max_pos 5.

## 4. Đã xong ngày 14/11 (sau bản bàn giao trước)
- **P51-G/P55**: bộ lọc cấu trúc A/B/C trên 3 đoạn: KHÔNG có bằng chứng đổi luật (báo cáo cũ đã xóa, xem git lịch sử). Giữ nguyên `trend_filter` và `neutral`.
- **P53-C** (aff3fa5): cache AI cho backtest, `--ai-cache on|off` mặc định BẬT, thư mục `%APPDATA%\Kronos_MT5_Bott_ai_cache`. Chạy lại cùng đoạn mất ~30 giây thay vì ~50 phút. Đã kiểm 1 ngày thật: kết quả giống hệt (P54).
- **A03** (1eedc27): không ghi đè pending canh vùng mới khi khôi phục sau execute.
- **A10** (3a05c0c): không đọc được lịch sử deals (chưa có cache) -> chặn mở lệnh mới tới khi đọc lại được (cờ `ctx["deals_unknown"]`).
- **A12**: đọc code: cooldown chuỗi thua và SL nhanh được dựng lại từ deals hôm nay mỗi giây (`_check_daily_profit_loss_limits`), nên sau restart vẫn có hiệu lực. Chỉ suy luận từ code, chưa có test riêng. Cooldown chốt chùm (`basket_cooldown_until`) chỉ ở RAM, mất sau restart (ít quan trọng).
- Quy ước mới (CLAUDE.md, 4aab3ac): Claude được tự chạy backtest chỉ-đọc và tự quyết sửa khi có bằng chứng nhiều đoạn.

## 5. Hàng đợi
- **P26-C**: tắt BTC bằng `ENABLED_PROFILES`: có ~15 chỗ lặp `("XAUUSD", "BTCUSD")`, rủi ro cao so với lợi ích; BTC hiện chỉ không bật auto. Chưa làm.
- **P28-C**: giao diện, tối đa 3 điểm (cần người dùng liệt kê).
- **P23/P24**: so sánh model base/small/gold. Model Gold test SAU giai đoạn huấn luyện; thư mục `model/weights/Kronos-*-Exness/` untracked -> sao lưu riêng NGOÀI Git.
- **P11-C**: nhật ký quyết định CSV.
- Tách module (docs/ARCHITECTURE_MAP.md), giai đoạn A trước; `trade_plan` chế độ ghi nhận; module chế độ thị trường (ghi nhận trước).
- Phase 2 còn lại: việc nặng của `_realtime_tick_loop` ra luồng nền, PA BUY dùng bid, việc nhỏ (tf_map, ghi settings atomic). Giờ server: Exness server = UTC (suy luận, chưa kiểm).
- **A29** (nghiêm trọng, `kronos_updater.py`): xử lý TRƯỚC khi phát hành cho khách.

## 6. Kết quả backtest hợp lệ (người dùng)
227 lệnh, +59,97$, PF 1,14; hòa vốn chỉ ở chi phí khoảng 0,26$/lệnh. **KHÔNG chỉnh tham số theo số này.**
Nhắc lại: backtest chế độ nến không có PA/cooldown/max_pos/giới hạn ngày/khung giờ cấm và TP luôn 2R (xem `CLAUDE.md`).

## 7. Việc thủ công / lưu ý
- Git: `trades_after.csv`, `trades_before.csv` đang ở trạng thái đã xóa (chưa commit); `.gitattributes`, `tools/scratch/`, thư mục model Gold untracked — không commit.
- Dữ liệu replay: `%APPDATA%\Kronos_MT5_Bot\replay_data\` (tick/nến đêm 13/11); log: `%APPDATA%\Kronos_MT5_Bot\logs\bot_activity_YYYY-MM-DD.log`.
- Bot đang chạy (demo) nạp code lúc khởi động: sửa code xong phải khởi động lại bot mới có hiệu lực; KHÔNG dùng nút cập nhật trong app.
