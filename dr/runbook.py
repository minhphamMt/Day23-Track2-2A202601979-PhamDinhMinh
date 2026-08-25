"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import math
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Ghi một bước runbook có timestamp vào JSONL."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "step": n,
        "name": name,
        **kw,
    }
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("RUNBOOK", json.dumps(record, ensure_ascii=False), flush=True)
    return record


def confirm(auto: bool, msg: str) -> bool:
    """CI có thể auto-confirm; vận hành thật mặc định fail closed với y/N."""
    if auto:
        return True
    return input(f"{msg} [y/N] ").strip().lower() == "y"


def _latest_outage(primary: str) -> dict | None:
    path = pathlib.Path("chaos/chaos-events.jsonl")
    if not path.exists():
        return None
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            if event.get("action") == "kill" and event.get("region") == primary:
                events.append(event)
    return events[-1] if events else None


def _health_detection(primary: str, after: float, timeout: float = 30.0) -> dict | None:
    """Đợi external health checker xác nhận để không cutover trước detection."""
    path = pathlib.Path("reports/health-events.jsonl")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if (event.get("event") == "state_change"
                        and event.get("region") == primary
                        and event.get("to") == "UNHEALTHY"
                        and event.get("ts", 0) >= after):
                    return event
        time.sleep(0.25)
    return None


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Thực thi runbook bảy bước và trả kết quả machine-readable."""
    if primary == target or primary not in URL or target not in URL:
        raise ValueError("primary và target phải là hai region hợp lệ khác nhau")
    started = time.time()
    outage = _latest_outage(primary)
    outage_ts = outage.get("ts") if outage else started

    probe_results = []
    consecutive_failures = 0
    for attempt in range(1, 4):
        try:
            response = httpx.get(f"{URL[primary]}/readyz", timeout=2.0)
            ready = response.status_code == 200
            reason = f"http_{response.status_code}"
        except Exception as exc:
            ready = False
            reason = type(exc).__name__
        consecutive_failures = 0 if ready else consecutive_failures + 1
        probe_results.append({"attempt": attempt, "ready": ready, "reason": reason})
        if attempt < 3:
            time.sleep(1.0)

    if consecutive_failures < 3:
        event = step(1, "xac_nhan_outage", ok=False, primary=primary,
                     consecutive_failures=consecutive_failures, probes=probe_results)
        return {"ok": False, "error": "outage_not_confirmed", "last_step": event}

    detected = _health_detection(primary, outage_ts)
    if detected is None:
        event = step(1, "xac_nhan_outage", ok=False, primary=primary,
                     consecutive_failures=consecutive_failures, probes=probe_results,
                     reason="health_checker_did_not_confirm_within_30s")
        return {"ok": False, "error": "health_detection_timeout", "last_step": event}
    step(1, "xac_nhan_outage", ok=True, primary=primary,
         consecutive_failures=consecutive_failures, probes=probe_results,
         health_detect_ts=detected["ts"])

    if not confirm(auto, f"Xác nhận failover region-{primary} sang region-{target}?"):
        event = step(2, "thong_bao_incident", ok=False, cancelled=True,
                     outage_ts=outage_ts)
        return {"ok": False, "error": "operator_cancelled", "last_step": event}
    incident = step(2, "thong_bao_incident", ok=True, primary=primary, target=target,
                    outage_ts=outage_ts,
                    notification_delay_s=round(time.time() - outage_ts, 3),
                    confirmation="auto" if auto else "operator_y")

    failover_result = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", ok=failover_result.get("ok", False),
         failover_called_once=True, target=target,
         waited_s=failover_result.get("waited_s"),
         error=failover_result.get("error"))

    rpo = failover_result.get("rpo", {})
    target_state = failover_result.get("target_state") or {}
    step(4, "verify_state_replica", ok=failover_result.get("ok", False),
         target=target, vector_count=(target_state.get("vectors") or {}).get("count"),
         weights_ready=not any("weights" in str(reason)
                               for reason in target_state.get("reasons", [])),
         rpo_seconds=rpo.get("rpo_seconds"), docs_lost=rpo.get("docs_lost"))

    cutover_ok = bool(failover_result.get("ok")
                      and failover_result.get("active_region") == target)
    step(5, "dns_cutover", ok=cutover_ok, target=target,
         active_region=failover_result.get("active_region"),
         failover_event_ts=(failover_result.get("cutover_event") or {}).get("ts"))
    if not cutover_ok:
        final = step(7, "post_incident", ok=False,
                     elapsed_s=round(time.time() - started, 3),
                     error=failover_result.get("error"),
                     measure_command=("python3 tools/measure_rto.py --loadgen "
                                      "reports/drill-2-withdr.jsonl --target-rto 300"))
        return {"ok": False, "incident": incident, "failover": failover_result,
                "last_step": final}

    latencies = []
    failures = 0
    for request_no in range(10):
        request_started = time.perf_counter()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer",
                                 params={"q": f"golden signal {request_no}"}, timeout=3.0)
            if response.status_code != 200:
                failures += 1
        except Exception:
            failures += 1
        latencies.append((time.perf_counter() - request_started) * 1000)
    ordered = sorted(latencies)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    golden = {
        "requests": len(latencies),
        "failures": failures,
        "error_rate": round(failures / len(latencies), 3),
        "p95_latency_ms": round(ordered[p95_index], 1),
    }
    step(6, "verify_golden_signals", ok=failures == 0, target=target, **golden)

    final = step(7, "post_incident", ok=failures == 0,
                 elapsed_s=round(time.time() - started, 3),
                 rpo_seconds=rpo.get("rpo_seconds"), docs_lost=rpo.get("docs_lost"),
                 measure_command=("python3 tools/measure_rto.py --loadgen "
                                  "reports/drill-2-withdr.jsonl --target-rto 300"))
    return {"ok": failures == 0, "incident": incident, "failover": failover_result,
            "golden_signals": golden, "last_step": final}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
