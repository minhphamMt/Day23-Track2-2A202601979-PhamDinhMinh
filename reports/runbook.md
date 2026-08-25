# Runbook — Region chính down

- **Họ và tên:** Phạm Đình Minh
- **Mã sinh viên:** 2A202601979

**Phạm vi:** Region A down, chuyển dịch vụ sang Region B. Chạy từ repository root
trong môi trường đã activate virtualenv. RTO/RPO mục tiêu đều là 300 giây.

| # | Bước | Lệnh copy-paste | Biết là xong khi | Owner |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python3 chaos/kill_region.py status --backend bare` | A không ready qua 3 probe liên tiếp; B còn alive | Primary on-call |
| 2 | Mở incident và bấm giờ | `date -u +%Y-%m-%dT%H:%M:%SZ` | Incident channel có timestamp, commander và mục tiêu RTO 300s | Incident commander |
| 3 | Chạy failover có kiểm soát | `python3 dr/runbook.py --primary a --target b --backend fs` | Operator nhập `y`; `reports/failover-events.jsonl` có đủ bước 1→5 và không có `ok:false` | Primary on-call |
| 4 | Verify state replica | `curl -sf http://127.0.0.1:8002/v1/state` | `weights:true`, `count>0`, `pool_state:"full"`; runbook log có RPO seconds và docs lost | Data/ML on-call |
| 5 | Verify DNS/LB cutover | `curl -sf http://127.0.0.1:8080/edge/state` | `active_region:"b"`; không cutover nếu `/readyz` B chưa trả 200 | Network/on-call |
| 6 | Verify golden signals | `tail -n 2 reports/runbook-run.jsonl` | Bước runbook vừa chạy đã gửi 10 request; log ghi error rate `<1%`, p95 `<500ms` | Service owner |
| 7 | Đo RTO và mở postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, `warnings:[]`, `rto_verdict:"PASS"`, phục hồi bởi B | Incident commander |

## Abort và rollback

- **Abort trước cutover:** B không alive, snapshot thiếu model version, restore lỗi,
  `/readyz` B không đạt 200 trong 60 giây, hoặc RPO vượt 300 giây. `dr/failover.py`
  fail closed nên không ghi `edge/active_region` trong các trường hợp này.
- **Rollback về A:** chỉ thực hiện khi A đã được sửa, restore/reconcile dữ liệu hoàn
  tất, `/readyz` A trả 200 ổn định qua ít nhất 3 probe và golden signals đạt error
  rate `<1%`, p95 `<500ms` trong 5 phút.
- **Quyền quyết định:** Incident commander phê duyệt; Primary on-call thực thi.
  Không bật failback tự động để tránh hai region flap qua lại.
- **Lệnh rollback sau khi được duyệt:** chạy cùng quy trình với vai trò đảo ngược:
  `python3 dr/runbook.py --primary b --target a --backend fs`, sau đó verify edge và
  10 golden requests trước khi đóng incident.

Kết quả bare-mode drill hiện tại: 10/10 golden requests thành công, error rate
`0.0%`, p95 `18.0ms` tại `reports/runbook-run.jsonl:6`.
