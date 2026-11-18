# Dự án: Kronos MT5 Scalping Bot

Bot scalping vàng **XAUUSDm** (Exness, hiện chạy tài khoản DEMO) trên MetaTrader5: giao diện Tkinter + mô hình AI Kronos (PyTorch).
File chính: `mt5_scalping_bot.py` (~12.000 dòng). Nhánh làm việc: `dev` (nhánh chính: `master`).
**GIAO DỊCH TIỀN THẬT: mọi thay đổi phải thận trọng.** Hai profile `XAUUSD` / `BTCUSD`: `pk`, `ctx = self.profile_ctx[pk]`,
Magic riêng lấy bằng `self._get_profile_magic_number(pk)`.
Đọc thêm: `HANDOFF.md` (trạng thái hiện tại, việc đang chạy, hàng đợi), `AUDIT.md` (danh sách lỗi A01…A44 và trạng thái xử lý).

## Vai trò và cách làm việc
- **CLAUDE (-C)**: sửa code, chạy test mock, VÀ (từ 14/11/2026, người dùng ủy quyền) tự chạy backtest chỉ-đọc/replay, tự phân tích và tự quyết sửa khi có bằng chứng.
  **GEMINI (-G)**: chỉ chạy script/backtest/thống kê; CẤM sửa `.py`, không commit. Không chạy 2 backtest song song (tranh CPU, Kernel-Power 41).
- Cập nhật 15/11/2026 (thay thế phần "tự quyết luật giao dịch" bên dưới): Claude ĐƯỢC tự sửa code không đổi luật giao dịch (lỗi, log chẩn đoán, hiển thị UI, test, docs)
  và tự chạy chẩn đoán/backtest/replay chỉ-đọc, đọc log hoạt động. Với thay đổi ĐỔI LUẬT GIAO DỊCH (điều kiện vào/hủy/thoát lệnh, SL/TP, lot, giờ cấm, ngưỡng, mặc định cấu hình):
  Claude chỉ ĐỀ XUẤT phương án (bằng chứng, diff dự kiến, rủi ro, điều chưa chắc chắn), CHỜ người dùng xác nhận rồi mới sửa.
- Ủy quyền 14/11/2026 (cũ): người dùng không rành code, để Claude tự quyết thay đổi có lợi cho app, kể cả luật giao dịch, với điều kiện: có bằng chứng
  từ nhiều đoạn dữ liệu (không dựa 1 đoạn), thay đổi nhỏ và đặt được bằng cấu hình, test đạt, commit riêng, và báo rõ điều chưa chắc chắn.
  VẪN CẦN hỏi: chạy bot thật/`demo_check.py`, chuyển sang tài khoản thật, tăng lot, đụng license/updater/token.
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
- Sửa file bằng script qua Bash heredoc: ký tự `\` có thể bị mất (`"\\n"` thành xuống dòng thật, `\t` thành tab).
  Tạo `\` bằng `bytes([92])`/`chr(92)` hoặc dùng `/`, rồi `py_compile` và kiểm tra nội dung chuỗi.

## Thêm thông số cấu hình mới (bắt buộc đủ 6 bước)
1. Khóa mặc định trong `DEFAULT_PROFILE_SETTINGS` cho CẢ `XAUUSD` và `BTCUSD` (tính năng mới đổi luật: mặc định TẮT).
2. Ô giao diện (`tk.BooleanVar` + `_add_checkbutton`, hoặc `_add_labeled_entry` / `_add_labeled_combo`) trong đúng Mục.
3. Thêm vào `_get_settings_vars_map` (thiếu thì không lưu/nạp được).
4. Mục giải thích trong `PARAM_HELP_GUIDE` (`title`, `what_is_it`, `how_to_set`) và truyền `help_key=` để có nút (?).
5. Thêm khóa vào danh sách nút "Khôi phục mặc định" của Mục tương ứng (`reset_section_settings`, dict `1:`..`5:`).
6. Nếu là thông số con: khai báo trong khối "Cây thông số cha/con" (`_bind_param_children`, sau nút khôi phục Mục 3);
   cha tắt (bỏ tích hoặc = 0) thì con mờ và khóa. Cập nhật bảng cây bên dưới.
- Luật mới cần replay được: `tools/live_replay.py --set <khóa>=<giá trị>` (chạy chính code live, không cần sửa tool).

**Cây thông số hiện tại** (cha → con):
| Cha | Con |
|---|---|
| `auto_be` (Tự dời SL về Entry) | `be_trigger_r` |
| `auto_trail` (Trailing) | `trail_dist_r` |
| `auto_rev_close` (Bảo vệ đảo chiều) | `rev_consensus`, `rev_be_consensus`, `rev_min_move`, `rev_loss_mode` |
| `basket_tp_usd` (> 0) | `basket_cooldown_mins` |
| `max_hold_mins` (> 0) | `max_hold_action` |
| `max_consec_loss` (> 0) | `cooldown_mins` |
| `confirm_entry_pa` | `pa_min_confirmations`, `pa_fail_open` |
`be_plus_price` dùng chung cho BE, giữ lệnh, đảo chiều nên không là con của mục nào.

**Thông số mới gần đây** (mặc định, Mục): `debug_trace` (tắt, 2: ghi `logs/trace/trace_*.jsonl`), `recovery_close` (tắt, 3: gỡ vốn),
`structure_direction` (tắt, 2: hướng theo đỉnh/đáy, replay kém, chưa khuyên bật), `max_hold_mins` mặc định 15,
`max_hold_action` mặc định "Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)".
Mã lý do đóng lệnh ghi vào comment MT5 `KR_<mã>` (TIME, TIMEALL, BE, GOV, REV, REVCUT, BASKET, DAILY, MANUAL, ALL):
thêm nơi đóng lệnh mới thì truyền `reason` cho `_close_single_pos` và thêm nhãn vào `_kr_reasons`.

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
- Test: `python -m unittest discover -s tests` (hiện **265 test**, toàn bộ mock).
- Backtest (chỉ đọc dữ liệu MT5, mọi lệnh gửi bị chặn): `python tools/run_backtest.py --profile XAUUSD --symbol XAUUSDm --start 2026-11-03 --end 2026-11-07 --threads 6`
  (cờ: `--precision S1`, `--no-ai`, `--seed`, `--trend-filter on|off`, `--structure-conflict-mode neutral|analysis|scalp`; `--threads` tối đa = số lõi vật lý).
  Mặc định chế độ nến. Đừng tối ưu tham số theo số backtest.
- **Bộ phát lại live (CÔNG CỤ CHÍNH để đánh giá luật, từ 16/11/2026)**: `python -u tools/live_replay.py --profile BTCUSD --config "<file>" --start YYYY-MM-DD --end YYYY-MM-DD --threads 4`.
  Chạy CHÍNH code bot live (vòng tick + vòng nến + phân tích AI) trên tick quá khứ với sàn giả lập; mọi thông số cấu hình có hiệu lực.
  Đã đối chiếu 16/11: tín hiệu 18:45 và lệnh 19:00 khớp live (giá vào trùng 64387.48). Báo cáo phải nêu số "Lỗi ghi nhận trong code bot" (phải = 0).
  Khi sửa `_realtime_tick_loop`/`_auto_loop_worker` hoặc dùng hàm MT5 mới: cập nhật `tools/live_replay.py` (Replayer/FakeBroker) trong cùng việc.
  Giới hạn: lệnh khớp ở tick tại thời điểm quyết định (không có trễ mạng); AI ngẫu nhiên nên không trùng từng lệnh với live.
- **QUY TẮC ĐỘ TIN CẬY BACKTEST (bắt buộc, người dùng yêu cầu 16/11/2026):**
  1. Chạy `--precision S1` khi kết quả dùng để quyết định. Chế độ nến chỉ để kiểm tra nhanh, phải ghi rõ "chế độ nến, độ tin cậy thấp".
  2. Cấu hình backtest phải giống cấu hình live đang chạy. Khi người dùng đưa file cấu hình (xuất từ app, chép từ máy khác,
     ví dụ `Cấu Hình 1/1.json` = cấu hình TÀI KHOẢN THẬT) thì BẮT BUỘC chạy `--config "<file>"` (không ghi đè tay từng khóa);
     lot lấy theo file trừ khi truyền `--lot`. Báo cáo phải chép dòng "Thông số chính" in ra. Nêu rõ mọi tham số đã ghi đè bằng `--set`/cờ.
  3. Mỗi báo cáo backtest phải có mục **"Phần chưa giống live"**: liệt kê các khác biệt bên dưới có ảnh hưởng tới kết quả lần đó.
  4. Nếu luật đang thử nằm trong phần backtest CHƯA mô phỏng (ví dụ Price Action) thì KHÔNG kết luận từ backtest;
     nói thẳng điều đó và dùng log live / replay tick để đánh giá.
  5. Khi có log live cùng khoảng ngày: đối chiếu số lệnh, giờ vào, chiều lệnh giữa backtest và live, báo tỉ lệ khớp.
  6. Khi sửa luật vào/hủy/thoát lệnh ở live: sửa phần mô phỏng backtest tương ứng trong cùng việc; nếu chưa làm được thì thêm vào danh sách dưới.
- **Backtest còn KHÁC live (cập nhật mỗi khi thu hẹp được):** không có xác nhận Price Action; không xét lại cấu trúc lúc khớp lệnh canh vùng (v1.3.17);
  TP luôn = 2R (live dùng TP AI); không chia đôi SL tối đa; chỉ 1 lệnh mỗi lúc (không `max_pos`/nhồi lệnh); không cooldown, giới hạn lãi/lỗ ngày, khung giờ cấm,
  kiểm spread/`order_check`; hết giờ giữ lệnh đóng theo giá nến (live xử lý theo hành động cấu hình); không commission/slippage/swap;
  chế độ nến không thấy diễn biến trong nến (dùng S1).
- Replay cổng canh vùng: `python tools/replay_gate.py [--poll-ms 100] [--grid]` phát lại tick CSV (`%APPDATA%\Kronos_MT5_Bot\replay_data`) vào
  `_check_pending_pullback_order`; chỉ đọc file, không MT5. Mẫu 1 đêm: không dùng để chọn luật.
- Kiểm tra demo thật: `python tools/demo_check.py` (MT5 thật, CHỈ tài khoản DEMO, có chốt an toàn); chỉ chạy khi người dùng yêu cầu rõ.

## Quy ước prompt
- Mã việc `P<số>-C` (giao Claude) / `P<số>-G` (giao Gemini); báo lại dạng `P<số>: <kết quả>` (ví dụ `P50-C: ...`).
- Mô hình/độ khó có thể ghi kèm (ví dụ "Sonnet 5.5 Medium", "Opus High"). Việc cần người dùng đồng ý trước khi sửa đánh dấu `[CẦN OK]`.
- Nếu để làm được việc phải sửa `mt5_scalping_bot.py` ngoài phạm vi được giao: DỪNG, báo cần sửa gì, chờ người dùng.
