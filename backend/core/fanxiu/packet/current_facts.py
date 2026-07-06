from __future__ import annotations

from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.packet.decoded_store import list_fanxiu_packet_decoded_records
from backend.core.fanxiu.packet.service_runtime import (
    get_fanxiu_packet_worker_status,
    request_fanxiu_packet_service_catch_up,
    start_fanxiu_packet_service,
)


def catch_up_and_list_fanxiu_packet_decoded_records(
    session: Session,
    *,
    names: list[str] | None = None,
    pro_ids: list[int] | None = None,
    since_seconds: int | None = None,
    limit: int = 50,
    reason: str = "decoded-records-api",
    wait_seconds: float = 30.0,
) -> dict[str, Any]:
    start_result = start_fanxiu_packet_service()
    catch_up_result = request_fanxiu_packet_service_catch_up(
        reason=reason,
        wait_seconds=wait_seconds,
    )
    decoded_records = list_fanxiu_packet_decoded_records(
        session,
        names=names,
        pro_ids=pro_ids,
        since_seconds=since_seconds,
        limit=limit,
    )
    return {
        "ok": bool(catch_up_result.get("ok", True) and decoded_records.get("ok", True)),
        "status": catch_up_result.get("status") or "pending",
        "action": "packet-facts-catch-up-and-query-decoded-records",
        "start_result": start_result,
        "catch_up": catch_up_result,
        "decoded_records": decoded_records,
        "worker": get_fanxiu_packet_worker_status(),
    }
