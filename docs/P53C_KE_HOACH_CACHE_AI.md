# P53-C: Kế hoạch cache kết quả AI cho backtest (chưa làm, chỉ mới đọc code)

Ngày: 14/11/2026. Trạng thái: CHƯA SỬA CODE. Chờ P51-G báo xong và người dùng OK.

## Phát hiện từ code
1. Seed AI không phụ thuộc `--seed`. `_fast_predict_scenarios` (mt5_scalping_bot.py ~dòng 9963) tự đặt
   `torch.manual_seed(data_seed)` trong `fork_rng`; `data_seed` = hash(số nến, OHLC làm tròn 2 số của nến cuối).
   - Kịch bản AI của mỗi nến do dữ liệu nến quyết định, không phụ thuộc thứ tự hay số luồng.
   - Hash tuple số trong Python không bị ngẫu nhiên hóa theo tiến trình (suy luận, cần test 2 tiến trình).
   - Chưa kiểm chứng: Kronos có nguồn không tái lập khác khi đổi số luồng không -> test "có/không cache giống hệt" là bắt buộc.
2. Cache hiện chỉ trong RAM: `bt_ai_cache[idx_ana]` (~dòng 4406) lưu kết quả SAU lọc cấu trúc/ngưỡng/SL,
   nên đổi cờ vẫn phải chạy lại AI.
3. Điểm chèn cache thô: ngay trước lời gọi `_fast_predict_scenarios` trong `_evaluate_bt_ai_candle` (~dòng 4107).
   Đầu ra = list DataFrame OHLCV + `attrs["log_prob"]`. Phần phía sau (đồng thuận, phân tán, SL/TP, lọc) tính lại nhanh từ cache.

## Thiết kế
- Hook tùy chọn `self._bt_scenario_cache` (mặc định None = hành vi cũ y hệt; live và 129 test không đổi).
- Cache nằm ở `tools/run_backtest.py`, thư mục `%APPDATA%\Kronos_MT5_Bot\bt_ai_cache\`, mỗi khóa một file `.npz`.
- Khóa: symbol, khung, thời điểm nến cuối, model + hash trọng số, lookback, số kịch bản, pred_len, dyn_t, dyn_top_p,
  hash byte của sub_df (OHLCV + timestamps), phiên bản định dạng.
- Cờ: `--ai-cache {on,off}`, `--ai-cache-dir`. `--resume` = chạy lại và trúng cache (không lưu trạng thái giao dịch).
- Ghi atomic (file tạm rồi rename); đọc lỗi/file hỏng = coi như miss; log số hit/miss.

## Test (mock, không MT5, không nạp model thật)
- Predictor giả có đếm số lần gọi: lần 1 miss, lần 2 hit, số lần gọi không tăng.
- Đổi một giá trị nến -> khóa đổi. File hỏng -> rơi về miss.
- Kết quả `_evaluate_bt_ai_candle` có cache == không cache.
- Kiểm tay: chạy 1 ngày thật có/không cache, số lệnh và tổng $ phải giống hệt (Gemini/người dùng chạy).

## Chưa chắc chắn
- Dung lượng cache chưa đo (ước vài trăm byte đến ~1 KB mỗi nến).
- `attrs` của kịch bản có thể còn trường ngoài `log_prob` mà phần sau dùng: cần Grep trước khi chốt.

## Cần người dùng quyết
- Đồng ý thiết kế trên?
- Cache mặc định bật (`on`, đề nghị vì khóa có hash dữ liệu) hay tắt?

## Kết quả thực hiện (14/11/2026)
- Sửa: `_evaluate_bt_ai_candle` (hook `_bt_scenario_cache`, mặc định None), `tools/run_backtest.py` (`--ai-cache on|off`, `--ai-cache-dir`, in thống kê trúng/trượt), file mới `tools/bt_ai_cache.py`, `tests/test_bt_ai_cache.py` (8 test).
- Tổng test: 137 đạt (129 cũ + 8 mới). `py_compile` đạt, CRLF của file chính nguyên vẹn.
- Khóa cache gồm hash trọng số model+tokenizer (state_dict), thiết bị, max_context, toàn bộ nến đầu vào, thời gian dự báo, số kịch bản, nhiệt độ, top_p, top_k.
- Số luồng KHÔNG nằm trong khóa (giả định kết quả không đổi theo số luồng; chưa kiểm chứng trực tiếp).
- CHƯA kiểm với model thật: cần chạy 1 ngày thật 2 lần (lần 1 tạo cache, lần 2 trúng cache) và so số lệnh/tổng $ với 1 lần `--ai-cache off`. Gemini chạy.
- CHƯA commit (chờ người dùng).
