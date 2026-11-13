# Dự án: Kronos MT5 Scalping Bot
Bot scalping MetaTrader5 (Tkinter + PyTorch Kronos AI). File chính: `mt5_scalping_bot.py` (~11.800 dòng). GIAO DỊCH TIỀN THẬT: mọi thay đổi phải thận trọng.
Hai profile XAUUSD / BTCUSD: `pk`, `ctx = self.profile_ctx[pk]`, Magic riêng lấy bằng `self._get_profile_magic_number(pk)`.

## Quy tắc cho Claude Code
- Sửa tối thiểu (minimal diff). Không refactor, đổi tên, định dạng lại code không liên quan.
- Giữ nguyên line ending CRLF của file. Giữ phong cách hiện có: comment và log tiếng Việt, kiểu emoji log.
- Mọi lời gọi `mt5.*` phải nằm trong `with self._mt5_lock:` (đây là RLock).
- KHÔNG dùng `mt5.positions_get()` không có symbol, và không lọc lệnh chỉ theo symbol. Dùng `_get_bot_positions` hoặc `_positions_get_checked` (kết quả None kèm `last_error()[0] == 1` nghĩa là danh sách rỗng, không phải lỗi).
- Luôn lọc theo Magic Number của profile. Với deals, tra magic của position gốc qua `_resolve_position_magic`.
- Không retry lệnh MỞ MỚI khi gặp TIMEOUT/CONNECTION/None: kiểm tra lại positions để chống mở đúp lệnh.
- `d.time` / `pos.time` của MT5 là giờ server, không phải UTC. Cẩn thận khi so với `time.time()`.
- Không chạy bot, không gọi MT5 thật. Kiểm thử bằng mock (unittest.mock). Sau mỗi thay đổi chạy `python -m py_compile mt5_scalping_bot.py`.
- Không đọc hoặc in token Telegram/Hugging Face (lưu ở %APPDATA%\Kronos_MT5_Bot\user_settings.json).
- Khi báo cáo: nêu hàm đã sửa, dán git diff và kết quả test; nêu rõ điểm chưa chắc chắn.

## Việc còn lại (Giai đoạn 2)
8. Quy đổi giờ server MT5 (offset) khi tính elapsed/cooldown/ngày.
9. Đưa việc MT5 nặng trong `_realtime_tick_loop` ra luồng nền (làm sau cùng, test kỹ).
10. Backoff 15 giây khi sửa SL/TP thất bại lặp lại theo ticket.
11. `is_trading_window_blocked`: so sánh theo số phút, hỗ trợ khung qua đêm.
12. Price Action BUY: so sánh "bật nảy" bằng giá bid.
13. Pullback: kiểm tra lại RR thực tế trước khi vào lệnh.
14. PnL ngày: cộng commission của deal vào, `is_sl_loss` chỉ dựa vào "[sl", ngưỡng theo symbol.
15. Nhỏ: tf_map thêm M30/H4/D1, dùng `_get_tf_minutes`, xóa dòng chết trong `ensure_single_app_instance`, ghi settings atomic.

