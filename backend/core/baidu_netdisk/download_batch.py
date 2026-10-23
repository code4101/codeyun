"""Bounded, durable download batches using the public automation service.

The primary supplies one fully inventoried CodeHub material row per job.
This layer owns ordering and disk budget; the service owns submission, locking,
provenance and recovery. An incomplete result stops the batch without replay.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from filelock import FileLock


def run_batch(service, jobs: list[dict], state_dir: Path, *, limit: int = 3,
              reserve_bytes: int = 200 * 1024**3, free_bytes=None,
              max_inflight: int = 1, inflight_jobs: list[dict] | None = None,
              max_queued: int | None = None) -> dict:
    """Download at most ``limit`` whole material packages, smallest package first.

    Each job carries ``resource_total_bytes`` and ``inventory_complete=True``;
    ordering is among supplied measured resources, never a claim about unknown
    resources. ``request`` is the public ``download_dir`` keyword contract, with
    the material row's entire cloud directory and complete expected manifest.
    Individual files and nested folders are not independent queue objects.
    ``current.json`` is durable before each call and afterwards, including errors.
    The caller must stop its own inventory worker before entering this batch.
    ``max_inflight>1`` permits sequential native submissions while previously
    submitted packages transfer concurrently in the official client. The caller
    supplies all outstanding measured jobs (after read-only reconciliation) in
    ``inflight_jobs``; their full bytes remain reserved conservatively. Unknown
    submissions stop the batch. This never runs competing native sessions.
    ``max_queued`` explicitly switches to client-managed queueing, permitting up
    to 50 outstanding whole packages independently of transfer concurrency.
    All queued bytes remain reserved. This does not change client settings.
    """
    if type(limit) is not int or limit < 1 or type(reserve_bytes) is not int or reserve_bytes < 0:
        raise ValueError("invalid batch budget")
    if type(max_inflight) is not int or not 1 <= max_inflight <= 5:
        raise ValueError("max_inflight must be between 1 and 5")
    if max_queued is not None and (type(max_queued) is not int or not 1 <= max_queued <= 50):
        raise ValueError("max_queued must be between 1 and 50")
    capacity = max_inflight if max_queued is None else max_queued
    pending = list(inflight_jobs or [])
    if len(pending) > capacity:
        raise ValueError("existing inflight packages exceed capacity")
    identifiers = set()
    materials = set()
    for job in [*pending, *jobs]:
        total = job.get("resource_total_bytes")
        req = job["request"]
        files = (req.get("expected_manifest") or {}).get("files")
        material_id = job.get("material_id")
        if (job.get("inventory_complete") is not True or type(total) is not int or total < 0
                or not files or not isinstance(material_id, str) or not material_id
                or req.get("remote_path") != job.get("material_cloud_path")
                or "expected_size" in req
                or any(type(f.get("size")) is not int or f["size"] < 0 for f in files)
                or sum(f["size"] for f in files) != total):
            raise ValueError("complete byte inventory required")
        if req["operation_id"] in identifiers or material_id in materials:
            raise ValueError("duplicate operation_id or material row")
        identifiers.add(req["operation_id"])
        materials.add(material_id)
    ordered = sorted(jobs, key=lambda j: (j["resource_total_bytes"],
                                          j["request"]["operation_id"]))
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    available = free_bytes or (lambda: shutil.disk_usage(service.config.local_root.anchor).free)
    digest = hashlib.sha256(json.dumps(ordered, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    result = {"schema": "netdisk-download-batch/v1", "jobs_sha256": digest,
              "pid": os.getpid(), "status": "running", "receipts": [], "reserve_bytes": reserve_bytes,
              "max_inflight": max_inflight, "max_queued": max_queued, "inflight_jobs": pending}

    def save():
        result["updated_at"] = time.time()
        temp = state_dir / "current.tmp"
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, state_dir / "current.json")

    with FileLock(str(state_dir / "batch.lock"), timeout=0):
        # Retain previous attempts; the provider's operation IDs prevent replay.
        prior = state_dir / "current.json"
        if prior.exists():
            raw = prior.read_bytes()
            (state_dir / (hashlib.sha256(raw).hexdigest() + ".receipt.json")).write_bytes(raw)
        save()
        for job in ordered[:limit]:
            request = job["request"]
            result["active_operation_id"] = request["operation_id"]
            if (state_dir / "STOP").exists():
                result["status"] = "stopped"
                break
            if len(pending) >= capacity:
                result["status"] = "paused_queue_capacity" if max_queued is not None else "paused_inflight_capacity"
                break
            if available() < reserve_bytes + sum(j["resource_total_bytes"] for j in pending) + job["resource_total_bytes"]:
                result["status"] = "paused_disk_budget"
                break
            save()  # a crash here is resolved via this exact public operation
            try:
                outcome = service.download_dir(**request, owner="netdisk-measured-package-batch")
            except Exception as exc:
                outcome = {"status": "provider_failure", "error_type": type(exc).__name__}
                if isinstance(getattr(exc, "receipt", None), dict):
                    outcome["receipt"] = exc.receipt
            result["receipts"].append({"operation_id": request["operation_id"], "result": outcome})
            if outcome.get("status") == "submitted":
                pending.append(job)
                save()  # persist the capacity reservation before another native call
                if capacity > 1:
                    continue
            if outcome.get("status") != "verified":
                result["status"] = "paused_needs_observation"
                save()
                break
            save()
        else:
            result["status"] = "bounded_batch_submitted" if pending else "bounded_batch_complete"
        save()
    return result
