"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """Append một failover event có timestamp và đồng thời in ra stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **kw,
    }
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("FAILOVER", json.dumps(record, ensure_ascii=False), flush=True)
    return record


def state_of(region: str) -> dict:
    """Đọc serving state qua API thay vì suy luận từ process/liveness."""
    response = httpx.get(f"{URL[region]}/v1/state", timeout=2.0)
    response.raise_for_status()
    return response.json()


def failover(target: str, backend: str, wait: float) -> dict:
    """Restore và cut over theo đúng năm bước; mọi lỗi đều fail closed."""
    if target not in URL:
        raise ValueError(f"region không hợp lệ: {target}")
    if wait <= 0:
        raise ValueError("wait phải > 0")
    source = "b" if target == "a" else "a"
    result = {"ok": False, "source": source, "target": target, "backend": backend}

    try:
        target_before = state_of(target)
        emit(step="1_verify_target", target=target, ok=True,
             pool_state=target_before.get("pool_state"),
             weights=target_before.get("weights"),
             vector_count=target_before.get("count"))
    except Exception as exc:
        emit(step="1_verify_target", target=target, ok=False,
             error=type(exc).__name__, reason=str(exc))
        result.update(error="verify_target_failed", reason=str(exc))
        return result

    restore_started = time.monotonic()
    try:
        manifest = snapshot.get(target, backend)
        rpo = snapshot.rpo(
            pathlib.Path(f"state/region-{source}/vectors.sqlite"),
            pathlib.Path(f"state/region-{target}/vectors.sqlite"),
        )
        restore_s = round(time.monotonic() - restore_started, 3)
        restore_event = emit(
            step="2_restore_snapshot", target=target, ok=True,
            restore_seconds=restore_s,
            snapshot_at=manifest.get("snapshot_at"),
            restored_at=manifest.get("restored_at"),
            embed_model_version=manifest.get("embed_model_version"),
            rpo_seconds=rpo.get("rpo_seconds"),
            docs_lost=rpo.get("docs_lost"),
        )
        result.update(manifest=manifest, rpo=rpo, restore_event=restore_event)
    except (Exception, SystemExit) as exc:
        emit(step="2_restore_snapshot", target=target, ok=False,
             error=type(exc).__name__, reason=str(exc))
        result.update(error="restore_snapshot_failed", reason=str(exc))
        return result

    pool_file = pathlib.Path(f"state/region-{target}/pool_state")
    try:
        pool_file.parent.mkdir(parents=True, exist_ok=True)
        pool_file.write_text("full\n", encoding="utf-8")
        emit(step="3_scale_pool", target=target, ok=True, pool_state="full")
    except Exception as exc:
        emit(step="3_scale_pool", target=target, ok=False,
             error=type(exc).__name__, reason=str(exc))
        result.update(error="scale_pool_failed", reason=str(exc))
        return result

    ready_started = time.monotonic()
    deadline = ready_started + wait
    attempts = 0
    last_reason = "not_probed"
    ready_state = None
    while time.monotonic() < deadline:
        attempts += 1
        try:
            response = httpx.get(f"{URL[target]}/readyz", timeout=min(2.0, wait))
            ready_state = response.json()
            if response.status_code == 200:
                waited_s = round(time.monotonic() - ready_started, 3)
                emit(step="4_wait_ready", target=target, ok=True,
                     waited_s=waited_s, attempts=attempts)
                break
            last_reason = json.dumps(ready_state.get("reasons", []), ensure_ascii=False)
        except Exception as exc:
            last_reason = f"{type(exc).__name__}: {exc}"
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    else:
        waited_s = round(time.monotonic() - ready_started, 3)
        emit(step="4_wait_ready", target=target, ok=False,
             waited_s=waited_s, attempts=attempts, reason=last_reason)
        result.update(error="target_not_ready", reason=last_reason,
                      waited_s=waited_s, target_state=ready_state)
        return result

    active_file = pathlib.Path("edge/active_region")
    try:
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(target, encoding="utf-8")
        cutover_event = emit(step="5_dns_cutover", target=target, ok=True,
                             active_region=target)
    except Exception as exc:
        emit(step="5_dns_cutover", target=target, ok=False,
             error=type(exc).__name__, reason=str(exc))
        result.update(error="dns_cutover_failed", reason=str(exc))
        return result

    result.update(ok=True, target_state=ready_state, waited_s=waited_s,
                  active_region=target, cutover_event=cutover_event)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
