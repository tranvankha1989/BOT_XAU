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

## 4. Đang chạy
- **P51-G** (Gemini): A/B bộ lọc cấu trúc, đoạn 17–21/8, seed 42; A = `trend_filter on` + `neutral`, B = `off`, C = `on` + `analysis`
  (dùng cờ `--trend-filter` / `--structure-conflict-mode` của c255c08). Kết quả chưa có; trong repo không có dấu vết của lần chạy này (người dùng).

## 5. Hàng đợi
- **A10** [CẦN OK]: `_get_today_deals_stats` trả `(0.0, 0, None)` khi `history_deals_get` lỗi và chưa có cache -> giới hạn ngày/cooldown vô hiệu (xem AUDIT.md).
- **A03** (Opus High): race ghi đè `pending_pullback_order` trong `_check_pending_pullback_order`.
- **P26-C**: tắt BTC bằng `ENABLED_PROFILES` (hiện chưa có trong code; giữ code BTC).
- **P28-C**: giao diện, tối đa 3 điểm.
- Cache kết quả AI + `--resume` cho `run_backtest`.
- **P23/P24**: so sánh model. Model Gold phải test SAU giai đoạn huấn luyện. Thư mục `model/weights/Kronos-Gold-Exness/` và
  `Kronos-Tokenizer-Gold-Exness/` nằm NGOÀI Git (untracked) -> cần sao lưu riêng.
- **P11-C**: nhật ký quyết định.
- Phase 2 còn lại (CLAUDE.md cũ): giờ server (offset) khi tính elapsed/cooldown/ngày; việc nặng của `_realtime_tick_loop` ra luồng nền (làm sau cùng, giữ `min(4, cpu//2)`);
  backoff sửa SL/TP theo ticket; khung giờ cấm so theo phút; PA BUY dùng bid; kiểm RR thực trước khi vào; PnL ngày cộng commission; việc nhỏ (tf_map, ghi settings atomic).
  Chưa xác nhận mục nào trong danh sách này đã xong một phần (một số đã có ở ded6451).
- **A29** (nghiêm trọng, `kronos_updater.py`: SSL không kiểm chứng, không thấy chữ ký/sha256): phải xử lý TRƯỚC khi phát hành cho khách.

## 6. Kết quả backtest hợp lệ (người dùng)
227 lệnh, +59,97$, PF 1,14; hòa vốn chỉ ở chi phí khoảng 0,26$/lệnh. **KHÔNG chỉnh tham số theo số này.**
Nhắc lại: backtest chế độ nến không có PA/cooldown/max_pos/giới hạn ngày/khung giờ cấm và TP luôn 2R (xem `CLAUDE.md`).

## 7. Việc thủ công / lưu ý
- Git: `trades_after.csv`, `trades_before.csv` đang ở trạng thái đã xóa (chưa commit); `.gitattributes`, `tools/scratch/`, thư mục model Gold untracked — không commit.
- Dữ liệu replay: `%APPDATA%\Kronos_MT5_Bot\replay_data\` (tick/nến đêm 13/11); log: `%APPDATA%\Kronos_MT5_Bot\logs\bot_activity_YYYY-MM-DD.log`.
- Bot đang chạy (demo) nạp code lúc khởi động: sửa code xong phải khởi động lại bot mới có hiệu lực; KHÔNG dùng nút cập nhật trong app.
