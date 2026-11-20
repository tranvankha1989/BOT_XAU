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
- Ô số: thêm luật kiểm tra vào `NUMERIC_RULES` + `PARAM_NAMES` (thông số con: cả `PARENT_OF`) trong `trading_modules/param_rules.py`
  (ô sai tô đỏ, cần lưu ý tô cam; nút Lưu và "Áp Dụng Vào Bot Live" bị chặn khi còn ô đỏ; chỉ giao diện, bot vẫn đọc ô như cũ).
- Thông số riêng từng máy (KHÔNG chép khi Xuất / Nhập file cấu hình, người dùng yêu cầu 19/11): thêm vào `MACHINE_LOCAL_KEYS`
  (hiện: Magic, đường dẫn MT5, số luồng AI, toàn bộ Mục 5 Telegram); trạng thái riêng máy ở `MACHINE_LOCAL_STATE_KEYS`.
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
| `fast_sl_cooldown_candles` (> 0) | `fast_sl_same_dir` |
| `confirm_entry_pa` | `pa_min_confirmations`, `pa_fail_open` |
`be_plus_price` dùng chung cho BE, giữ lệnh, đảo chiều nên không là con của mục nào.

**Thông số mới gần đây** (mặc định, Mục): `min_real_rr` (0 = tắt, 2: RR thật tối thiểu đo theo SL THẬT = SL AI + Giá an toàn, chặn trong `execute_mt5_order`; thêm 20/11 vì BTC Giá an toàn 150 + TP AI cho lệnh TP < SL), `debug_trace` (tắt, 4: ghi `logs/trace/trace_*.jsonl`), `recovery_close` (tắt, 3 nhóm giới hạn ngày: gỡ vốn),
`structure_direction` (tắt, 2: hướng theo đỉnh/đáy, replay kém, chưa khuyên bật), `fast_sl_same_dir` (tắt, 3: SL nhanh chỉ nghỉ chiều vừa thua), `allow_opposite_entry` (tắt, 2: vẫn vào lệnh ngược chiều khi lệnh cũ đang âm), `stack_half_be` (tắt, 3: n lệnh cùng chiều chưa BE tăng lên n>=2 -> n//2 lệnh giá xấu nhất vào `pending_be_exit_tickets` chờ Entry+BE+ thoát), `trend_filter` + `structure_conflict_mode` ĐÃ ẨN khỏi giao diện (18/11, người dùng không dùng): trend_filter mặc định tắt và bị đặt tắt mỗi lần nạp cấu hình; code lọc còn giữ, thử bằng `--set trend_filter=True`, `max_hold_mins` mặc định 15,
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
- Test: `python -m unittest discover -s tests` (hiện **375 test**, toàn bộ mock).
- Backtest (chỉ đọc dữ liệu MT5, mọi lệnh gửi bị chặn): `python tools/run_backtest.py --profile XAUUSD --symbol XAUUSDm --start 2026-11-03 --end 2026-11-07 --threads 6`
  (cờ: `--precision S1`, `--no-ai`, `--seed`, `--trend-filter on|off`, `--structure-conflict-mode neutral|analysis|scalp`; `--threads` tối đa = số lõi vật lý).
  Mặc định chế độ nến. Đừng tối ưu tham số theo số backtest.
- **Bộ phát lại live (CÔNG CỤ CHÍNH để đánh giá luật, từ 16/11/2026)**: `python -u tools/live_replay.py --profile BTCUSD --config "<file>" --start YYYY-MM-DD --end YYYY-MM-DD --threads 4`.
  Chạy CHÍNH code bot live (vòng tick + vòng nến + phân tích AI) trên tick quá khứ với sàn giả lập; mọi thông số cấu hình có hiệu lực.
  Đã đối chiếu 16/11: tín hiệu 18:45 và lệnh 19:00 khớp live (giá vào trùng 64387.48). Báo cáo phải nêu số "Lỗi ghi nhận trong code bot" (phải = 0).
  Khi sửa `_realtime_tick_loop`/`_auto_loop_worker` hoặc dùng hàm MT5 mới: cập nhật `tools/live_replay.py` (Replayer/FakeBroker) trong cùng việc.
  Giới hạn: lệnh khớp ở tick tại thời điểm quyết định (không có trễ mạng).
  AI KHÔNG ngẫu nhiên giữa các lần chạy: `kronos_engine` đặt hạt giống theo dữ liệu nến -> cùng nến cho cùng kết quả (19/11: 138/138 nến trùng hệt máy 2).
  `--ai-latency` mặc định 8.5s là thời gian AI của MÁY NÀY; máy 2 chỉ ~1.7s (phát hiện nến ~1.5s): so với máy 2 phải dùng `--ai-latency 1.7 --detect-delay 1.5`,
  nếu không lần kiểm tra Price Action/vùng canh lệch pha và lệnh vào khác. Đối chiếu live phải dùng ĐÚNG cấu hình theo từng giờ (log có dòng "Đã áp dụng ... Nút Lưu Cài Đặt")
  và bỏ các khoảng bot live không chạy. Chi tiết: `docs/SO_SANH_BACKTEST_LIVE_2026-11-19.md`.
  Rà 19/11: hết tạm nghỉ (chuỗi thua/SL nhanh/chốt chùm) live phân tích lại NGAY -> Replayer dừng đúng mốc đó (giữ pha giây vòng tick); nhảy nhàn rỗi tính mốc phát hiện nến CÒN PHÍA TRƯỚC (trước đây có thể bỏ sót 1 nến). Replayer chạy nhánh tab NGẦM: trạng thái chỉ ghi ctx mà tab đang mở bị `_sync_self_to_active_ctx` đè (vd basket_cooldown_until) thì backtest KHÁC live.
- **BACKTEST PHẢI GIỐNG BOT LIVE (bắt buộc, người dùng yêu cầu 18/11/2026):**
  1. Backtest/replay chạy theo file cấu hình y như bot live: MỌI khóa cấu hình có hiệu lực, không bỏ qua khóa nào.
     Dùng engine chạy CHÍNH code live (`tools/live_replay.py`), KHÔNG viết lại luật giao dịch trong engine riêng.
  2. Mỗi khi thêm/sửa luật live (vào/hủy/thoát lệnh, SL/TP, quản lý lệnh, tạm nghỉ...): kiểm tra backtest/replay áp dụng đúng y hệt
     trong CÙNG việc (thêm test hoặc chạy replay ngắn chứng minh); nếu replay dùng hàm MT5 mới thì cập nhật FakeBroker.
  3. Cấu hình backtest TÁCH khỏi cấu hình live: chỉnh thông số để test KHÔNG được làm đổi bot live.
     "Lấy từ Menu" = chép cấu hình live sang backtest; "Áp Dụng Vào Bot Live" = chép cấu hình backtest sang live (có xác nhận, liệt kê khóa thay đổi).
  4. Backtest trong app phải chạy ở tiến trình RIÊNG: `live_replay` thay mt5/time/datetime cấp module, chạy chung tiến trình sẽ phá bot live.
     Giới hạn luồng để không tranh CPU với bot live (Kernel-Power 41).
  5. Giao diện xem lại backtest: biểu đồ nến phát lại kiểu TradingView (tua 1x/2x/5x/10x..., tạm dừng, từng bước), thấy rõ điểm vào/SL/TP/thoát.
     Phải MƯỢT, không giật: tiến trình UI không chạy AI (tiến trình con tính), vẽ theo khung hình cố định, chỉ cập nhật phần thay đổi.
     (19/11, người dùng yêu cầu) Mở tab Backtest -> khung biểu đồ PHÍA TRÊN đổi sang màn hình Backtest riêng (giống tab Vàng/BTC);
     bấm chạy: nạp dữ liệu xong là biểu đồ CHẠY TRỰC TIẾP theo thời gian như thật (dòng tín hiệu AI, nến dự báo, vùng canh, lệnh, nhật ký),
     tua 1x..600x/"Tối đa", không vượt mốc đã tính (chờ AI như video đang tải).
  Trạng thái 19/11: ĐÃ LÀM cả 5 mục.
  - Mục 3: nút "⚙️ Thông số Backtest" = bảng riêng dựng từ `SETTINGS_SECTION_MAP` + `_param_meta` của menu (thông số mới thêm đúng 6 bước tự có),
    lưu `%APPDATA%/Kronos_MT5_Bot/backtest_settings.json` (không token), đọc bằng `_get_bt_cfg(pk)`.
  - Mục 1/4: tab Backtest mặc định engine "🧠 Code Live": `_start_live_engine_backtest` ghi job.json vào `backtest_runs/run_*`, chạy tiến trình con
    (mã nguồn: `python tools/live_replay.py --job`; bản .exe: chạy lại chính .exe với biến môi trường `KRONOS_REPLAY_JOB`,
    hook `_run_packed_replay_job_from_env` ở CUỐI mt5_scalping_bot.py nạp bt_ai_cache/run_backtest/live_replay từ core_engine.bin -> KHÔNG sửa launcher).
    Con: ưu tiên thấp, `_bt_child_threads()` luồng; tiến độ `progress.jsonl`, kết quả `result.json` + `player.npz` + `log.txt`; hủy = file `cancel`.
    `tools/binary_packer.py` phải gói 3 công cụ đó (đã thêm). Engine cũ vẫn chọn được ("⚡ Engine cũ", khác live).
  - Mục 5: `trading_modules/replay_player.py` (Tk Canvas, bộ hình nến dùng lại, ~60 khung/giây, đo ~5ms/khung). Nút "🎬 Xem phát lại", bấm đúp 1 lệnh.
    Xem trực tiếp: con ghi `player.npz` ngay sau khi nạp dữ liệu (sự kiện `data_ready`) + `events.jsonl` (`EventStream` trong live_replay:
    open/sltp/close/ai/zone/log/t; "ai" bắt từ lời gọi `_ui_after(0, _update_ui_results, ...)` của live). App: `chart_holder` chứa
    `live_chart_frame` hoặc `bt_ws_frame` (`_bt_set_workspace_visible`, gọi trong `_on_tab_changed`), trình phát nhúng `parent=bt_ws_body, live=True`;
    bảng lệnh chạy theo thời điểm đang phát khi còn đang tính (`_render_backtest_results(..., final=False)`), tính xong thì đủ lệnh.
    Đổi chữ ký `_update_ui_results` hoặc cách gọi nó ở live: sửa `simulate` (bắt sự kiện "ai") trong cùng việc.
    Tốc độ (19/11): backtest TẮT `debug_trace` (trace ghi nối 1 file/ngày dùng chung, file phình làm chậm 5 lần; muốn có: `--set debug_trace=True`), `install_fetch_memo` dùng lại bảng nến đã đóng (trả bản sao). KHÔNG bỏ qua giờ nghỉ phiên khi có lệnh mở (live thử sửa lệnh mỗi 15s, để lại trạng thái). Sàn giả từ chối mọi lệnh gửi khi không có tick > 60s (10018 Market closed) như MT5 thật.
  - Test có app thật + thanh Backtest phải patch `SETTINGS_DIR` sang thư mục tạm (không ghi APPDATA thật).
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
