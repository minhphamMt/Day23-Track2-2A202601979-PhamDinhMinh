# Postmortem — DR Drill Lab 23

- **Họ và tên:** Phạm Đình Minh
- **Mã sinh viên:** 2A202601979

Đây là postmortem blameless: nguyên nhân được quy về thiết kế và quy trình hệ thống,
không quy lỗi cho cá nhân thực hiện chaos drill.

## 1. Timeline

| ISO time (UTC) | Sự kiện | Evidence |
|---|---|---|
| `2026-08-25T04:39:08.906608Z` | Region A bị `SIGSTOP`; bắt đầu RTO clock | `chaos/chaos-events.jsonl:3` |
| `2026-08-25T04:39:09.237354Z` | User đầu tiên nhận lỗi | `reports/drill-2-withdr.jsonl:26` |
| `2026-08-25T04:39:23.696901Z` | Health checker xác nhận A `UNHEALTHY` | `reports/health-events.jsonl:2` |
| `2026-08-25T04:39:23.855236Z` | Operator/runbook xác nhận incident | `reports/runbook-run.jsonl:2` |
| `2026-08-25T04:39:30.132422Z` | Region B ready và DNS cutover hoàn tất | `reports/failover-events.jsonl:5` |
| `2026-08-25T04:39:31.401535Z` | Request đầu tiên thành công từ Region B | `reports/drill-2-withdr.jsonl:37` |

## 2. RTO/RPO so với mục tiêu và gap analysis

- RTO mục tiêu: `300s`; đo được: **`22.5s`**; margin/gap còn lại:
  **`277.5s`**. Verdict: **PASS** (`reports/measure-drill-2.json:20`).
- RPO mục tiêu: `300s`; đo được: **`2.0s`**, tương ứng **1 document bị mất**;
  margin/gap còn lại: **`298.0s`** (`reports/failover-events.jsonl:2`).
- Thành phần tốn nhiều thời gian nhất là health detection: `14.790s`, khoảng
  **65.7% RTO**. GPU warm-up đứng thứ hai với `6.259s`.
- Phần chênh lệch so với RTO bằng 0 về mặt đo lường: tổng breakdown `22.495s`, làm
  tròn thành RTO `22.5s`. Không có warning và không có mốc tự khai báo ngoài log.

## 3. Root cause — 5 Whys

1. Vì sao user nhận lỗi? Edge vẫn cache Region A sau khi serving A dừng.
2. Vì sao edge chưa chuyển ngay? Thiết kế cố ý yêu cầu health checker đạt ngưỡng ba
   lỗi liên tiếp trước khi cho phép cutover.
3. Vì sao Region B chưa thể nhận traffic ngay khi outage xảy ra? B ở warm pool, chưa
   có vector snapshot và model weights được restore vào serving state.
4. Vì sao cần restore trong incident thay vì B luôn ready? Kiến trúc active-passive
   ưu tiên giảm chi phí compute và dùng snapshot replication mỗi 30 giây.
5. Vì sao hệ thống ban đầu không tự hồi phục trong Drill 1? Chưa có detector độc lập,
   runbook orchestration và readiness-gated DNS cutover. Ba thành phần này là control
   plane bắt buộc, không thể thay bằng liveness check đơn thuần.

Rủi ro thật cần lưu ý: nếu snapshot/manifest mất hoặc model version không đồng bộ,
bước restore sẽ abort. Đây là fail-closed đúng hơn cutover sang một region không thể
serve, nhưng vẫn khiến RTO vượt mục tiêu nếu không có bản sao dự phòng khác.

## 4. Action items

| # | Action item | Owner | Deadline | Tác động dự kiến |
|---|---|---|---|---|
| 1 | Giảm health interval từ 5s xuống 2s, giữ threshold=3 và thêm circuit breaker/cooldown 5 phút | SRE lead | 2026-09-01 | Detection floor từ 15s xuống 6s; giảm khoảng 9s RTO |
| 2 | Giữ Region B có model weights và snapshot pre-restored, chỉ để GPU pool warm | ML platform owner | 2026-09-08 | Loại phần restore khỏi critical path; giảm khoảng 0.4s và rủi ro restore |
| 3 | Alert khi replication lag vượt 60s hoặc model version mismatch | Data platform owner | 2026-09-01 | Phát hiện nguy cơ RPO trước incident; giữ RPO dưới 300s |

## 5. Câu hỏi bắt buộc

1. `interval × threshold = 5s × 3 = 15s`. Detection đo thực tế `14.790s`, chiếm
   khoảng `65.7%` của RTO `22.5s`; sai khác nhỏ do outage rơi giữa lịch poll.
2. Nếu hạ interval xuống 1s và giữ threshold=3, detection floor giảm từ 15s xuống
   3s, nên RTO lý tưởng giảm khoảng `12s` còn gần `10.5s`. Đổi lại là lượng probe
   tăng 5 lần, nhạy hơn với lỗi thoáng qua và tăng nguy cơ flapping; cần cooldown,
   hysteresis và rate limit failover.
3. Với outage 6 giờ và primary mất dữ liệu vĩnh viễn, `docs_lost=1` nghĩa là một
   document đã được khách hàng ghi nhận ở Region A nhưng không tồn tại trong snapshot
   phục hồi. Document đó phải được replay từ nguồn sự kiện hoặc đối soát; nếu không,
   kết quả retrieval/inference của khách hàng có thể thiếu dữ liệu dù API đã hồi phục.
