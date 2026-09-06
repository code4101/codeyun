"""Atomic receipts owned by the pet-resource provider, scoped to an occurrence."""

import json
import os
from datetime import datetime
from math import isfinite
from uuid import uuid4
from filelock import FileLock
from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


def _path(activity_id: int, occurrence: str):
    if activity_id <= 0 or not occurrence or any(c not in "0123456789-" for c in occurrence):
        raise ValueError("Expected activity ID and occurrence date")
    folder = fanxiu_data_annotation_dir() / "pet-resource-receipts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{activity_id}-{occurrence}.json"


def read_pet_resource_receipts(activity_id: int, occurrence: str) -> list[dict]:
    path = _path(activity_id, occurrence)
    with FileLock(str(path) + ".lock", timeout=5):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def record_pet_resource_receipt(activity_id: int, occurrence: str, receipt: dict,
                                 *, action_id: str | None = None) -> str:
    path = _path(activity_id, occurrence)
    with FileLock(str(path) + ".lock", timeout=5):
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if action_id is None:
            if any(row.get("status") == "submitted" for row in rows):
                raise RuntimeError("上一批资源操作结果未核实，禁止重复消耗")
            action_id = str(uuid4())
            rows.append({**receipt, "action_id": action_id})
        else:
            existing = next((row for row in rows if row["action_id"] == action_id), None)
            if existing is None:
                raise ValueError("Unknown resource action")
            existing.update(receipt)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return action_id


def record_pet_rank_sample(activity_id: int, occurrence: str, *, pet_id: int,
                           action_ids: list[str], before_rank: dict, after_rank: dict,
                           observation_action_id: str | None = None) -> dict:
    """Atomically attribute one refreshed rank delta to same-item verified batches.

    The caller must naturally refresh the board before and after the batches;
    Runtime capture time alone does not prove server freshness. Repeating the
    same calibration is idempotent. Overlapping or mixed-item attribution fails.
    """
    if not action_ids or len(set(action_ids)) != len(action_ids):
        raise ValueError("Expected distinct source actions")
    for rank in (before_rank, after_rank):
        if not rank.get("complete") or rank.get("rank_activity_id") != activity_id:
            raise ValueError("Rank sample activity is unavailable or mismatched")
    before, after = before_rank["self_ranking"], after_rank["self_ranking"]
    identity = before.get("role_id") or before.get("role_key")
    if not identity or identity != (after.get("role_id") or after.get("role_key")):
        raise ValueError("Rank sample player identity changed")
    scores = [float(row["score"]) for row in (before, after)]
    if any(not isfinite(score) or score < 0 for score in scores) or scores[1] < scores[0]:
        raise ValueError("Rank scores must be finite and nondecreasing")
    start, end = [datetime.fromisoformat(rank["captured_at"]) for rank in (before_rank, after_rank)]
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ValueError("Rank sample requires ordered timezone-aware captures")
    if start.astimezone().date().isoformat() != occurrence or end.astimezone().date().isoformat() != occurrence:
        raise ValueError("Rank observations cross the resource occurrence")
    path = _path(activity_id, occurrence)
    with FileLock(str(path) + ".lock", timeout=5):
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        observation = None
        if observation_action_id is not None:
            observation = next((r for r in rows if r.get("action_id") == observation_action_id), None)
            if (observation is None or observation.get("pet_id") != pet_id
                    or observation.get("status") not in {"rank_observation_pending", "rank_observation_verified"}
                    or observation.get("before_rank") != before_rank):
                raise ValueError("Rank observation identity changed")
        sources = [row for row in rows if row.get("action_id") in action_ids]
        if len(sources) != len(action_ids) or any(
            row.get("status") != "verified" or row.get("pet_id") != pet_id
            or row.get("activity_id", activity_id) != activity_id
            or row.get("occurrence", occurrence) != occurrence for row in sources
        ):
            raise ValueError("Rank sample requires verified batches from this pet and activity")
        if len({row["item_id"] for row in sources}) != 1:
            raise ValueError("Cannot allocate one rank delta across different resources")
        for row in sources:
            captured = datetime.fromisoformat(row["captured_at"])
            if captured.tzinfo is None or not start <= captured <= end:
                raise ValueError("Resource action falls outside rank observations")
        selected = set(action_ids)
        for row in rows:
            if row.get("status") == "submitted":
                raise RuntimeError("Resource result is unresolved; cannot calibrate ranking")
            if row.get("status") == "verified" and row.get("action_id") not in selected:
                captured = datetime.fromisoformat(row["captured_at"])
                # Receipts currently have second precision. An unrelated batch
                # at the baseline second is ambiguous, not proof of exclusion.
                if start <= captured <= end:
                    raise ValueError("Rank observations include another resource batch")
            overlap = selected.intersection(row.get("source_action_ids", []))
            if row.get("status") == "rank_sample" and overlap:
                if (set(row["source_action_ids"]) == selected and row.get("rank_before") == scores[0]
                        and row.get("rank_after") == scores[1] and row.get("role_identity") == identity
                        and row.get("activity_id") == activity_id and row.get("pet_id") == pet_id
                        and row.get("occurrence") == occurrence):
                    if observation is not None and observation.get("status") == "rank_observation_pending":
                        observation.update(status="rank_observation_verified", sample_action_id=row["action_id"])
                        temporary = path.with_suffix(".tmp")
                        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                        os.replace(temporary, path)
                    return dict(row)
                raise ValueError("Resource batch already has a different rank calibration")
        sample = {"status": "rank_sample", "action_id": str(uuid4()),
                  "activity_id": activity_id, "occurrence": occurrence, "pet_id": pet_id,
                  "item_id": sources[0]["item_id"], "source_action_ids": sorted(selected),
                  "quantity": sum(row["quantity"] for row in sources),
                  "base_total": sum(row.get("base_total") or 0 for row in sources),
                  "rank_before": scores[0], "rank_after": scores[1],
                  "rank_delta": scores[1] - scores[0], "role_identity": identity,
                  "rank_before_captured_at": before_rank["captured_at"],
                  "captured_at": after_rank["captured_at"]}
        rows.append(sample)
        if observation is not None:
            observation.update(status="rank_observation_verified", sample_action_id=sample["action_id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return sample
