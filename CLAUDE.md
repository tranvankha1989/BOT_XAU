# Dự án: Kronos MT5 Scalping Bot

Bot scalping vàng **XAUUSDm** (Exness, hiện chạy tài khoản DEMO) trên MetaTrader5: giao diện Tkinter + mô hình AI Kronos (PyTorch).
File chính: `mt5_scalping_bot.py` (~12.000 dòng). Nhánh làm việc: `dev` (nhánh chính: `master`).
**GIAO DỊCH TIỀN THẬT: mọi thay đổi phải thận trọng.** Hai profile `XAUUSD` / `BTCUSD`: `pk`, `ctx = self.profile_ctx[pk]`,
Magic riêng lấy bằng `self._get_profile_magic_number(pk)`.
Đọc thêm: `HANDOFF.md` (trạng thái hiện tại, việc đang chạy, hàng đợi), `AUDIT.md` (danh sách lỗi A01…A44 và trạng thái xử lý).

## Vai trò và cách làm việc
- **CLAUDE (-C)**: chỉ sửa code và chạy test mock. **GEMINI (-G)**: chỉ chạy script/backtest/thống kê; CẤM sửa `.py`, không commit.
- Mỗi lúc chỉ MỘT agent được sửa file; mỗi lần giao MỘT việc. Người dùng nói tiếng Việt và muốn tiết kiệm token:
  trả lời ngắn, Grep rồi Read đúng đoạn, không đọc cả file 12.000 dòng.
- Khi báo cáo: nêu hàm đã sửa, git diff --stat, kết quả test, và NÊU RÕ điểm chưa chắc chắn (phần nào là suy luận, chưa kiểm chứng).

## Quy tắc code
- Sửa tối thiểu (minimal diff). Không refactor, đổi tên, định dạng lại code không liên quan.
- Giữ phong cách hiện có: comment và log tiếng Việt, log có emoji. `mt5_scalping_bot.py` dùng line ending CRLF: khi sửa bằng script phải giữ CRLF.
- Mọi lời gọi `mt5.*` nằm trong `with self._mt5_lock:` (RLock).
- Không dùng `mt5.positions_get()` không có symbol; không lọc lệnh chỉ theo symbol. Dùng `_get_bot_positions` hoặc `_positions_get_checked`
  (kết quả None kèm `last_error()[0] == 1` nghĩa là danh sách rỗng, không phải lỗi; `_get_bot_positions` trả [] cả khi lỗi đọc).
- Luôn lọc theo Magic Number của profile; với deals tra magic của position gốc qua `_resolve_position_magic`. Không đụng lệnh tay (magic khác).
- Không retry lệnh MỞ MỚI khi gặp TIMEOUT/CONNECTION/None: kiểm tra lại positions để chống mở đúp lệnh.
- `d.time` / `pos.time` / `tick.time` của MT5 là giờ SERVER, không phải UTC; log bot ghi giờ VN (UTC+7). Cẩn thận khi so với `time.time()`.
- Bot thật giữ giới hạn luồng PyTorch `min(4, cpu//2)` (máy từng bị Kernel-Power 41). Chỉ công cụ backtest được ghi đè (`--threads`).
- Thêm log chẩn đoán thì phải giới hạn tần suất và bọc `try/except`, không đổi điều kiện vào/hủy lệnh.

## Quy tắc an toàn (KHÔNG làm)
- KHÔNG `kill`/`taskkill` toàn bộ `python.exe` (có thể giết nhầm bot đang chạy). Dừng đúng tiến trình/tác vụ của mình.
- KHÔNG bấm huy hiệu cập nhật trong app: `kronos_updater.py` ghi đè file chính, không sao lưu (xem A29 trong AUDIT.md).
- KHÔNG đọc hay in token Telegram/Hugging Face, KHÔNG đọc `%APPDATA%\Kronos_MT5_Bot\user_settings.json` (chứa token). Log hoạt động có số tài khoản: không dán ra ngoài.
- KHÔNG sửa logic bản quyền: `kronos_license.py`, `kronos_updater.py` (chỉ báo cáo nếu thấy vấn đề).
- KHÔNG chạy bot thật, KHÔNG gọi MT5 thật trong lúc sửa code; kiểm thử bằng mock (`unittest.mock`). Agent -G chạy backtest chỉ ĐỌC dữ liệu.
- BTCUSD đang bị tắt tạm (người dùng quyết định); GIỮ nguyên code BTC. Việc tắt bằng `ENABLED_PROFILES` (P26-C) chưa làm, hiện chưa có trong code.
- KHÔNG commit `tools/scratch/`, `.gitattributes`, thư mục `model/weights/Kronos-*-Exness/`. Commit riêng cho từng mục; message ngắn, không dấu.
- Sau mỗi thay đổi: `python -m py_compile mt5_scalping_bot.py` rồi chạy toàn bộ test.

## Cách chạy
- Test: `python -m unittest discover -s tests` (hiện **129 test**, toàn bộ mock).
- Backtest (chỉ đọc dữ liệu MT5, mọi lệnh gửi bị chặn): `python tools/run_backtest.py --profile XAUUSD --symbol XAUUSDm --start 2026-11-03 --end 2026-11-07 --threads 6`
  (cờ: `--precision S1`, `--no-ai`, `--seed`, `--trend-filter on|off`, `--structure-conflict-mode neutral|analysis|scalp`; `--threads` tối đa = số lõi vật lý).
  Mặc định chế độ nến. **Backtest KHÔNG giống 100% live**: không có xác nhận Price Action, cooldown, `max_pos`/nhồi lệnh, giới hạn lãi/lỗ ngày, khung giờ cấm,
  kiểm spread/`order_check`; TP luôn = 2R (live dùng TP của AI); không chia đôi Max SL; chỉ 1 lệnh mỗi lúc. Đừng tối ưu tham số theo số backtest.
- Replay cổng canh vùng: `python tools/replay_gate.py [--poll-ms 100] [--grid]` phát lại tick CSV (`%APPDATA%\Kronos_MT5_Bot\replay_data`) vào
  `_check_pending_pullback_order`; chỉ đọc file, không MT5. Mẫu 1 đêm: không dùng để chọn luật.
- Kiểm tra demo thật: `python tools/demo_check.py` (MT5 thật, CHỈ tài khoản DEMO, có chốt an toàn); chỉ chạy khi người dùng yêu cầu rõ.

## Quy ước prompt
- Mã việc `P<số>-C` (giao Claude) / `P<số>-G` (giao Gemini); báo lại dạng `P<số>: <kết quả>` (ví dụ `P50-C: ...`).
- Mô hình/độ khó có thể ghi kèm (ví dụ "Sonnet 5.5 Medium", "Opus High"). Việc cần người dùng đồng ý trước khi sửa đánh dấu `[CẦN OK]`.
- Nếu để làm được việc phải sửa `mt5_scalping_bot.py` ngoài phạm vi được giao: DỪNG, báo cần sửa gì, chờ người dùng.
