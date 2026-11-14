# backtest_data/: dữ liệu và kết quả backtest (gom một chỗ)
- `baseline_*.json`, `trades_*.csv`, `blocked_signals_*`: mốc so sánh cũ (trước/sau các lần sửa lớn).
- `runs/` (không commit): log và CSV của từng lần chạy `tools/run_backtest.py` / quét thông số (p51_*, p55_*, p56_*, p58_*, p59_*...).
- Cache kết quả AI nằm NGOÀI dự án: `%APPDATA%\Kronos_MT5_Bot\bt_ai_cache\` (có thể xóa, sẽ tự tạo lại). CSV mặc định của run_backtest: `%APPDATA%\Kronos_MT5_Bot\backtests\`.
