"""Accept one downloaded CodeHub material package and publish verified local progress.

Uses public automation/catalog/sync APIs only. Never submits a native download.
Evidence stays outside the original package; repeated acceptance verifies the
previous local hashes and reuses an already acknowledged catalog record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def write_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def immutable(directory: Path, value: dict, suffix: str) -> tuple[Path, str]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()
    sha = hashlib.sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (sha + suffix)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("immutable evidence collision")
    else:
        path.write_bytes(raw)
    return path, sha


def accept(job: dict, state: Path, *, wait_seconds: float = 0) -> dict:
    from backend.core.baidu_netdisk.automation import NetdiskAutomation
    from backend.core.baidu_netdisk.automation_config import load_automation_config
    from backend.core.baidu_netdisk.operations import normalize_request
    from backend.core.skilldep_materials.catalog import CatalogStore
    from backend.core.skilldep_materials.codehub import CodeHubSkillDep
    from backend.core.skilldep_materials.local_sync import publish_local
    from backend.core.skilldep_materials.payload_evidence import SCHEMA
    from filelock import FileLock

    if not 0 <= wait_seconds <= 90:
        raise ValueError("acceptance observation budget must be between 0 and 90 seconds")
    service = NetdiskAutomation(load_automation_config(state / "download-config.json"))
    request = job["request"]
    if job.get("inventory_complete") is not True or request["remote_path"] != job["material_cloud_path"]:
        raise ValueError("entire measured material root required")
    mid = job["material_id"]
    store = CatalogStore()
    current = store.load()["items"][mid]
    if current.get("delivery", {}).get("resource_version") != job["resource_version"]:
        raise ValueError("material version changed")
    local_dir = service.config.resolve_destination(request["destination"])
    expected_request = normalize_request({"kind": "download_dir", "local_dir": str(local_dir),
        **{k: request.get(k) for k in ("remote_path", "resource_id", "resource_version", "expected_manifest")}})
    op = service.inspect(request["operation_id"])
    if op is None or op["request"] != expected_request:
        raise ValueError("operation does not bind this exact package request")
    result = service.reconcile(request["operation_id"])
    # The client can return immediately after submission. Observe this same
    # operation briefly while inventory runs; never retry the native action.
    deadline = time.monotonic() + wait_seconds
    while result["status"] == "in_progress" and time.monotonic() < deadline:
        time.sleep(min(5, max(0, deadline - time.monotonic())))
        result = service.reconcile(request["operation_id"])
    if result["status"] == "ready":
        result = service.recover(request["operation_id"])  # filesystem only, never resubmits
    if result["status"] != "verified":
        raise ValueError("package is not verified: " + result["status"])
    target = local_dir / PurePosixPath(request["remote_path"]).name
    relative = target.relative_to(service.config.local_root).as_posix()
    completed_path = state / "completed-packages.json"
    # This lock protects our registry and evidence publication, not the account.
    with FileLock(str(state / "package-acceptance.lock"), timeout=0):
        completed = json.loads(completed_path.read_bytes()) if completed_path.exists() else {
            "schema": "completed-material-packages/v1", "items": {}}
        prior = completed["items"].get(mid)
        if prior and prior["resource_version"] == job["resource_version"]:
            raw = Path(prior["evidence_path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != prior["evidence_sha256"]:
                raise ValueError("previous immutable evidence changed")
            previous = json.loads(raw)
            if (previous["material_id"] != mid or previous["target_path"] != str(target)
                    or service.verify(relative, previous["verification"]["manifest"])["status"] != "content_verified"):
                raise ValueError("previously accepted payload changed")
        verification = service.verify(relative, request["expected_manifest"])
        size = sum(f["size"] for f in verification["manifest"]["files"])
        if verification["status"] not in {"structure_verified", "content_verified"} or size != job["resource_total_bytes"]:
            raise ValueError("complete package file set or total bytes mismatch")
        acceptance = {"schema": "material-whole-package-download-acceptance/v1", "at": time.time(),
            "material_id": mid, "resource_version": job["resource_version"], "resource_root_id": request["resource_id"],
            "target_path": str(target), "operation_id": request["operation_id"],
            "source_inventory_sha256": job["inventory_sha256"], "complete_raw_package": True,
            "formal_cleaning_complete": False, "bytes": size, "files": verification["counts"]["files"],
            "verification": verification}
        evidence, evidence_sha = immutable(state / "package-evidence", acceptance, ".json")
        completed["items"][mid] = {"resource_version": job["resource_version"], "operation_id": request["operation_id"],
            "target_path": str(target), "bytes": size, "files": acceptance["files"],
            "evidence_path": str(evidence), "evidence_sha256": evidence_sha}
        write_json(completed_path, completed)
        manifest = {"schema": SCHEMA, "material_id": mid, "resource_version": job["resource_version"],
            "root": str(target), "complete": True, "resource_id": request["resource_id"],
            "hash_source": "local_read", "files": verification["manifest"]["files"]}
        manifest_path, manifest_sha = immutable(state / "package-evidence", manifest, ".payload-manifest.json")
        current = store.load()["items"][mid]
        local = current.get("local", {})
        if (current.get("publication", {}).get("remote_registered") and local.get("status") == "downloaded"
                and local.get("manifest_sha256") == manifest_sha and local.get("path") == str(target)):
            publication = {"local_saved": True, "remote_registered": True, "already_registered": True}
        else:
            record = {"status": "downloaded", "path": str(target), "size_bytes": size,
                "manifest_path": str(manifest_path), "manifest_sha256": manifest_sha,
                "scope": {"items": acceptance["files"], "note": "整行素材包原件齐全；逐文件大小/本地SHA核验。未声称源端可信SHA或正式清洗完成。"},
                "finished_at": datetime.now(timezone.utc).isoformat()}
            def registry(**options):
                hub = CodeHubSkillDep(**options)
                hub.verify_peer()
                return hub
            publication = publish_local(store, mid, record, registry_factory=registry,
                                        timeout=8, retries=1, budget_seconds=25)
        receipt = {"at": time.time(), "material_id": mid, "bytes": size, "files": acceptance["files"],
            "evidence": str(evidence), "external_manifest": str(manifest_path), "result": publication}
        # Filename derived from the ID hash: untrusted input never becomes a path.
        write_json(state / (hashlib.sha256(mid.encode()).hexdigest()[:16] + ".package-publication-receipt.json"), receipt)
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-file", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--wait-seconds", type=float, default=0,
                        help="Observe the same in-progress operation for at most 90 seconds; never resubmit")
    args = parser.parse_args()
    from backend.core.baidu_netdisk.automation_config import bootstrap_runtime
    bootstrap_runtime()
    jobs = json.loads(args.jobs_file.read_bytes())
    if len(jobs) != 1:
        raise ValueError("accept exactly one whole material at a time")
    print(json.dumps(accept(jobs[0], args.state_dir, wait_seconds=args.wait_seconds), ensure_ascii=False))


if __name__ == "__main__":
    main()
