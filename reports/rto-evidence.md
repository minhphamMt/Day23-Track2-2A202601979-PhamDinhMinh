# RTO/RPO Evidence — Lab 23

- **Họ và tên:** Phạm Đình Minh
- **Mã sinh viên:** 2A202601979

Mọi số liệu dưới đây đến từ final drill ngày 2026-08-25, chạy bare mode với
`--mock` và network-block simulation theo README. Các timestamp là UTC và RTO được
tính bằng `tools/measure_rto.py`, không ước lượng bằng quan sát thủ công.

## 1. Drill 1 — không có DR

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | `2026-08-25T04:38:18Z` | Chaos `SIGSTOP` Region A | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | `+0.1s` | Dòng `ok:false` đầu tiên sau outage | `reports/drill-1-nodr.jsonl:17` |
| Request thành công sau đó | Không có | 16 request sau outage đều thất bại | `reports/measure-drill-1.json:28` |
| RTO | `NO_RECOVERY` | Không có request recovery trong cửa sổ loadgen | `reports/measure-drill-1.json:27` |

Drill 1 chứng minh hệ thống ban đầu không tự phát hiện, không restore state và không
chuyển traffic khi Region A dừng.

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | `0.0s` | `action:kill`, Region A | `chaos/chaos-events.jsonl:3` |
| User thấy lỗi đầu tiên | `0.3s` | Dòng `ok:false` đầu tiên | `reports/drill-2-withdr.jsonl:26` |
| Health checker phát hiện | `14.8s` | A chuyển sang `UNHEALTHY` sau 3 lỗi liên tiếp | `reports/health-events.jsonl:2` |
| Snapshot restore xong | `15.0s` | Bước `2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region B ready | `21.2s` | Bước `4_wait_ready`, warm-up `6.258s` | `reports/failover-events.jsonl:4` |
| DNS cutover | `21.2s` | `active_region=b` | `reports/failover-events.jsonl:5` |
| **Request đầu tiên phục hồi từ B** | **`22.5s`** | `ok:true`, `served_by:b` | `reports/drill-2-withdr.jsonl:37` |

| Chỉ số | Đo được | Mục tiêu | Verdict | Evidence |
|---|---:|---:|---|---|
| RTO — Inference API | **`22.5s`** | `300s` | **PASS** | `reports/measure-drill-2.json:20` |
| RPO — Vector DB | **`2.0s` / `1` document** | `300s` | **PASS** | `reports/failover-events.jsonl:2` |

Kết quả đo cuối có `valid:true`, `warnings:[]`, Region A là region bị dừng và Region B
là region phục hồi: `reports/measure-drill-2.json:2`.

## 3. RTO breakdown

| Thành phần | Giây | Nguồn tính | Cách giảm |
|---|---:|---|---|
| Health-check detection | `14.790s` | `t_detect - t_outage`; cấu hình `5.0s × 3 = 15.0s` | Giảm interval hoặc threshold, kèm circuit breaker để tránh flapping |
| Verify + snapshot restore | `0.177s` | `t_scale_pool - t_detect`; riêng filesystem copy là `0.005s` | Snapshot nhỏ hơn, restore song song, giữ replica gần-ready |
| GPU pool warm-up | `6.259s` | `t_cutover - t_scale_pool`; event ghi `waited_s=6.258` | Giữ warm pool hoặc dùng model nhỏ hơn |
| DNS/LB cache | `1.269s` | `t_recovered - t_cutover` | Giảm TTL hoặc dùng global load balancer push-based |
| **Tổng** | **`22.495s ≈ 22.5s`** | Bằng RTO đo từ trải nghiệm user | — |

Evidence cho cấu hình health check nằm tại `reports/health-events.jsonl:2`; các mốc
restore/scale/ready/cutover nằm tại `reports/failover-events.jsonl:2`,
`reports/failover-events.jsonl:3`, `reports/failover-events.jsonl:4` và
`reports/failover-events.jsonl:5`.
