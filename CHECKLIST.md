# Checklist hoàn thành Day 23 — Disaster Recovery for AI Infrastructure

Checklist này được cập nhật theo kết quả đã chạy và xác minh, không chỉ theo việc đã viết mã.

## 1. Chuẩn bị

- [x] Đọc `README.md`, `GUIDE.md`, `RUBRIC.md` và toàn bộ tests.
- [x] Kiểm tra trạng thái ban đầu của source code, reports và evidence.
- [x] Chọn phương án chạy phù hợp với máy hiện tại.
- [x] Cài đầy đủ Python dependencies.

## 2. Implementation

- [x] Hoàn thiện `dr/health_checker.py`.
- [x] Health checker dùng `/readyz`, timeout và consecutive-failure threshold.
- [x] Health checker chỉ ghi log khi state thay đổi và có đủ metadata.
- [x] Hoàn thiện `dr/failover.py` theo đúng 5 bước.
- [x] Failover abort và không DNS cutover khi target chưa ready.
- [x] Failover ghi đủ RPO seconds, docs lost và model version.
- [x] Hoàn thiện `dr/runbook.py` theo đúng 7 bước.
- [x] Runbook mặc định yêu cầu xác nhận, `--auto` dành cho drill/CI.
- [x] Runbook gọi `failover.failover()` đúng một lần và đo golden signals.

## 3. Kiểm thử code

- [x] Unit test tính RPO pass.
- [x] Unit test anti-flapping của health checker pass.
- [x] Unit test không cutover khi target chưa ready pass.

## 4. DR drills và evidence

- [x] Seed Region A và khởi động Region A, Region B, edge.
- [x] Chạy Drill 1 bằng bare mode, `netblock --mock` và ghi `drill-1-nodr.jsonl`.
- [x] Drill 1 có request lỗi và verdict `NO_RECOVERY`.
- [x] Restore Region A an toàn sau Drill 1.
- [x] Chạy ingest và filesystem replication trước Drill 2.
- [x] Chạy Drill 2 bằng bare mode, `netblock --mock`, health checker và automated runbook.
- [x] Drill 2 phục hồi bằng Region B, `valid=true`, không warning.
- [x] RTO đo được không quá 300 giây.
- [x] RPO có cả số giây và số document bị mất.

## 5. Báo cáo

- [x] Hoàn thiện `reports/runbook.md` với command, signal, owner và rollback.
- [x] Hoàn thiện `reports/rto-evidence.md` bằng số liệu và dòng log thật.
- [x] Hoàn thiện `reports/postmortem.md` với timeline, gap analysis và action items.
- [x] Không còn placeholder/template trong ba báo cáo.

## 6. Kiểm tra và đóng gói nộp

- [x] Ghi họ tên `Phạm Đình Minh` và MSV `2A202601979` trong cả ba báo cáo.
- [x] Giữ nguyên tên và định dạng ba file báo cáo theo yêu cầu codelab.
- [x] Kèm output đầy đủ của `python3 -m pytest tests/ -v` tại `reports/pytest-output.txt`.
- [x] Toàn bộ 13 tests pass (Windows chạy với `PYTHONUTF8=1`; Linux mặc định UTF-8).
- [x] Kiểm tra lại hai kết quả `measure_rto.py`.
- [x] Kiểm tra mọi evidence path/line tồn tại và đúng số liệu.
- [x] Kiểm tra `git diff` không có thay đổi ngoài phạm vi bài làm.
- [x] Bỏ ignore có chọn lọc để các evidence bắt buộc xuất hiện trong bài nộp Git.
