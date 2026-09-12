"""On-demand Runtime transport, distinct from server-host Runtime readers.

The client owns reading its game process and decoding named queries. CodeYun
accepts only the observation requested by this attempt; client evidence never
updates the server's own account, assets, global Runtime caches or inventory.
The first query proves read-only memory access. Inventory/quest readers must be
ported as additional named client adapters before advertising their support.
"""
from __future__ import annotations

import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class RuntimeProcessIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_name: str = Field(min_length=1, max_length=255)
    pid: int = Field(gt=0)
    start_ticks: int = Field(gt=0)
    device_serial: str = Field(min_length=1, max_length=128)


class RuntimeReadError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(max_length=500)


class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    query: Literal["process"]
    captured_at: float
    source: Literal["adb_proc_mem"]
    process_identity: RuntimeProcessIdentity | None = None
    completeness: Literal["complete", "partial", "unavailable"]
    facts: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)
    error: RuntimeReadError | None = None

    @model_validator(mode="after")
    def bounded_evidence(self):
        if len(json.dumps(self.facts, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
            raise ValueError("Runtime 事实超过 256 KiB 限制")
        if self.completeness == "complete" and (self.process_identity is None or self.error is not None):
            raise ValueError("完整 Runtime 观测需要进程身份且不能包含错误")
        return self


def plan_runtime_probe(snapshot: dict | None, *, now: float | None = None) -> dict:
    """Request one named local read, then report its evidence without GUI actions."""
    if snapshot is None:
        return {"status": "running", "observation": {},
                "action": {"kind": "collect_runtime", "query": "process"}}
    data = RuntimeSnapshot.model_validate(snapshot).model_dump(exclude_none=True)
    age = (time.time() if now is None else now) - data["captured_at"]
    observation = {"runtime": data}
    if not -5 <= age <= 30:
        return {"status": "blocked", "reason": "runtime_stale", "observation": observation}
    if data["completeness"] != "complete":
        return {"status": "blocked", "reason": "runtime_incomplete", "observation": observation}
    return {"status": "completed", "observation": observation}
