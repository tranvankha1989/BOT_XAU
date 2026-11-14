# P57: lỗi chia đôi Max SL vô cớ (nguyên nhân chính bot 0 lệnh đêm 13/11)
Phát hiện từ đọc code (live, tạo vùng canh trong `_run_analysis_worker`); đã sửa bằng hàm thuần `calc_effective_max_sl`.

## Lỗi
- Khi SL của AI xa hơn Max SL (D > M), bot dựng điểm vào dự kiến = SL ± M và TP = điểm vào dự kiến ± rr x M.
- Rồi kiểm "RR >= 2R" bằng khoảng cách từ GIÁ HIỆN TẠI (xa hơn điểm vào dự kiến) tới TP: dist_tp = 3M - D (rr=2), so với 2D.
- 3M - D < 2D <=> D > M: luôn đúng mỗi khi cần canh vùng -> Max SL luôn bị chia đôi (4 -> 2) -> vùng canh nằm quá sâu (SL + 2) -> giá hầu như không chạm -> 0 lệnh.
- Backtest KHÔNG chia đôi (vùng = SL + M) nên các số backtest 6 đoạn đại diện cho hành vi đúng ý (không chia đôi), không đại diện cho live cũ.

## Sửa (đổi hành vi live, có chủ đích)
- Kiểm RR tại điểm vào dự kiến (SL ± Max SL) thay vì giá hiện tại. TP thật sự thấp (< 2R tại điểm vào dự kiến) vẫn chia đôi như trước.
- Test: tests/test_effective_max_sl.py (6 test; có test chứng minh luật cũ chia đôi vô cớ).
- Hệ quả: vùng canh nằm ở SL + Max SL (như backtest), rủi ro mỗi lệnh = Max SL người dùng cài (không phải nửa). Số lệnh live sẽ tăng so với đêm 13/11.
- Điểm chưa chắc: phần tính tay ở trên giả định rr=2 (mặc định), chưa chạy với bot live thật; cần theo dõi demo Thứ Hai (log "Tối Ưu RR" nên hiếm xuất hiện hơn).
