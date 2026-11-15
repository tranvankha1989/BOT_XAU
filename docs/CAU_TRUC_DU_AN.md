# Cấu trúc dự án (sau khi tách module)

```
Kronos/
├─ launcher.py, mt5_scalping_bot.py      file CHẠY chính (giao diện + điều phối), nằm ngoài cùng
├─ kronos_license.py, kronos_updater.py  bản quyền / cập nhật (không sửa logic)
├─ version.json, requirements.txt, SETUP_CAI_DAT.bat, LICENSE, README*.md
├─ CLAUDE.md, AUDIT.md, HANDOFF.md       quy tắc làm việc, danh sách lỗi, bàn giao (nội bộ, không đưa lên kho công khai)
├─ trading_modules/                      MODULE giao dịch: hàm thuần, test riêng (xem docs/MODULES.md)
├─ model/                                mô hình Kronos (+ weights/ ngoài Git)
├─ tools/                                công cụ dòng lệnh: run_backtest, replay_gate, demo_check, publish_release, keygen...
├─ backtest_data/                        DỮ LIỆU/KẾT QUẢ backtest gom một chỗ (xem backtest_data/README.md)
├─ tests/                                test mock (+ golden/: kết quả hàm gốc dùng so sánh khi tách module)
├─ docs/                                 tài liệu (MODULES, ARCHITECTURE_MAP, báo cáo P5x...)
├─ finetune/, finetune_csv/              huấn luyện lại mô hình
└─ (sinh tự động, không commit) installer_output/, build_staging/, graphify-out/
```
Quy ước: file chạy ở ngoài cùng; logic giao dịch nằm trong `trading_modules/`; mọi dữ liệu backtest trong `backtest_data/`; test cho từng module trong `tests/test_<module>_module.py`.
