from __future__ import annotations

import ipaddress
import time
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field


PROJECT_GRAPH_RUNTIME_STALE_SECONDS = 10.0


class ProjectGraphRuntimeVector(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    x: float
    y: float


class ProjectGraphRuntimeObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=100_000)
    position: ProjectGraphRuntimeVector | None = None
    size: ProjectGraphRuntimeVector | None = None
    source_uuid: str | None = Field(default=None, max_length=128)
    target_uuid: str | None = Field(default=None, max_length=128)
    child_uuids: list[str] | None = Field(default=None, max_length=10_000)
    attachment_id: str | None = Field(default=None, max_length=128)


class ProjectGraphRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    bridge_version: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    observed_at: float = Field(gt=0)
    project_uri: str = Field(default="", max_length=4096)
    project_title: str = Field(default="", max_length=1024)
    project_state: str = Field(default="", max_length=128)
    objects: list[ProjectGraphRuntimeObject] = Field(default_factory=list, max_length=20_000)
    selected_uuids: list[str] = Field(default_factory=list, max_length=20_000)


class ProjectGraphRuntimeSnapshotStore:
    def __init__(self, *, stale_seconds: float = PROJECT_GRAPH_RUNTIME_STALE_SECONDS):
        self._lock = RLock()
        self._snapshot: ProjectGraphRuntimeSnapshot | None = None
        self._received_at = 0.0
        self._sequence = 0
        self._stale_seconds = max(0.1, float(stale_seconds))

    def publish(
        self,
        snapshot: ProjectGraphRuntimeSnapshot,
        *,
        received_at: float | None = None,
    ) -> dict[str, object]:
        timestamp = time.time() if received_at is None else float(received_at)
        with self._lock:
            self._snapshot = snapshot.model_copy(deep=True)
            self._received_at = timestamp
            self._sequence += 1
            sequence = self._sequence
        return {
            "accepted": True,
            "sequence": sequence,
            "received_at": timestamp,
            "object_count": len(snapshot.objects),
            "selected_count": len(snapshot.selected_uuids),
        }

    def latest(self, *, now: float | None = None) -> dict[str, object]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            snapshot = self._snapshot.model_copy(deep=True) if self._snapshot is not None else None
            received_at = self._received_at
            sequence = self._sequence

        if snapshot is None:
            return {
                "connected": False,
                "stale": True,
                "sequence": sequence,
                "received_at": None,
                "age_seconds": None,
                "snapshot": None,
            }

        age_seconds = max(0.0, timestamp - received_at)
        stale = age_seconds > self._stale_seconds
        return {
            "connected": not stale,
            "stale": stale,
            "sequence": sequence,
            "received_at": received_at,
            "age_seconds": age_seconds,
            "snapshot": snapshot.model_dump(mode="json"),
        }

    def clear(self) -> None:
        with self._lock:
            self._snapshot = None
            self._received_at = 0.0
            self._sequence = 0


def is_loopback_host(host: str | None) -> bool:
    value = (host or "").strip().lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


runtime_snapshot_store = ProjectGraphRuntimeSnapshotStore()
