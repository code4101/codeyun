from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu_tcp_flow import (
    DEFAULT_FANXIU_SERVER_HOST,
    _build_fanxiu_tcp_entries,
    _normalize_fanxiu_worldline_activity_item,
    decode_fanxiu_tcp_pcap,
    get_latest_fanxiu_worldline_activity_schedule,
    list_fanxiu_worldline_activity_schedule_snapshots,
    list_tcp_streams_with_tshark,
    resolve_fanxiu_tcp_store_root,
)
from backend.core.settings import get_settings

WORLDLINE_ACTIVITY_PROTOCOL = "SM_WorldLineActivitySync"
ACTIVITY_RANK_PROTOCOL = "SM_ActivityRankSync"


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _sync_root(data_dir: str | Path | None = None) -> Path:
    base = Path(data_dir).expanduser().resolve() if data_dir else get_settings().data_dir
    path = base / "fanxiu" / "activity-packet-sync"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(data_dir: str | Path | None = None) -> Path:
    return _sync_root(data_dir) / "state.json"


def _records_path(data_dir: str | Path | None = None) -> Path:
    return _sync_root(data_dir) / "worldline_activity_records.json"


def _rank_records_path(data_dir: str | Path | None = None) -> Path:
    return _sync_root(data_dir) / "activity_rank_records.json"


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _load_state(data_dir: str | Path | None = None) -> dict[str, Any]:
    state = _load_json(_state_path(data_dir), {})
    return state if isinstance(state, dict) else {}


def _load_records(data_dir: str | Path | None = None) -> dict[str, Any]:
    payload = _load_json(_records_path(data_dir), {"records": []})
    if not isinstance(payload, dict):
        payload = {"records": []}
    records = payload.get("records")
    if not isinstance(records, list):
        payload["records"] = []
    return payload


def _load_rank_records(data_dir: str | Path | None = None) -> dict[str, Any]:
    payload = _load_json(_rank_records_path(data_dir), {"records": []})
    if not isinstance(payload, dict):
        payload = {"records": []}
    records = payload.get("records")
    if not isinstance(records, list):
        payload["records"] = []
    return payload


def _packet_order_key(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry.get("decoded_at") or ""), str(entry.get("id") or "")


def _is_after_cursor(entry: dict[str, Any], cursor: dict[str, Any]) -> bool:
    cursor_at = str(cursor.get("last_packet_scan_at") or "")
    cursor_id = str(cursor.get("last_packet_id") or "")
    if not cursor_at:
        return True
    return _packet_order_key(entry) > (cursor_at, cursor_id)


def _activity_record_key(item: dict[str, Any]) -> str:
    return "|".join(
        str(item.get(key) or "")
        for key in ("activityId", "id", "startTime", "endTime", "closePanelTime", "scheduleId", "loopDay")
    )


def _activity_identity_key(item: dict[str, Any]) -> str:
    values = [
        item.get("activityId"),
        item.get("id"),
        item.get("scheduleId"),
        item.get("loopDay"),
        item.get("name"),
    ]
    key = "|".join("" if value is None else str(value) for value in values)
    return key if key.strip("|") else _activity_record_key(item)


def _has_activity_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list | dict | tuple | set):
        return bool(value)
    return True


def _merge_activity_item(existing: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in item.items():
        if _has_activity_value(value) or key not in merged:
            merged[key] = value
    return merged


def _extract_worldline_items(entry: dict[str, Any], *, export_root: str | Path | None = None) -> list[dict[str, Any]]:
    parsed = entry.get("content")
    if not isinstance(parsed, dict):
        return []
    activity_vos = parsed.get("activityVOS") or {}
    raw_items = activity_vos.get("items") if isinstance(activity_vos, dict) else []
    return [
        normalized
        for raw in raw_items or []
        if isinstance(raw, dict)
        for normalized in [_normalize_fanxiu_worldline_activity_item(raw, export_root=export_root)]
        if normalized
    ]


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and text.lstrip("-").isdigit():
            return int(text)
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _rank_super(item: dict[str, Any]) -> dict[str, Any]:
    parent = item.get("_super")
    return parent if isinstance(parent, dict) else {}


def _format_rank_number(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return ""
    if abs(number - int(number)) < 0.000001:
        return str(int(number))
    return f"{number:g}"


def _rank_progress_text(activity_id: str, item: dict[str, Any]) -> str:
    score = _as_float(item.get("score"))
    if score is None:
        return ""
    if activity_id.startswith("1162"):
        rate = _as_float(item.get("extScore"))
        if rate is None:
            rate = _as_float(item.get("extScore2"))
        if rate is not None and rate > 0:
            if rate > 100:
                rate = rate / 100
            return f"通过第{int(score)}关{_format_rank_number(rate)}%"
        return f"通过第{int(score)}关"
    return f"积分 {_format_rank_number(score)}"


def _server_names_from_packet(value: Any) -> dict[int, str]:
    result: dict[int, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("_class") == "ServerVO":
                server_id = _as_int(node.get("id"))
                name = str(node.get("name") or "").strip()
                if server_id is not None and name:
                    result[server_id] = name
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def _rank_entry_text(activity_id: str, item: dict[str, Any], server_names: dict[int, str]) -> str:
    parent = _rank_super(item)
    name = str(parent.get("name") or item.get("name") or "").strip()
    server_id = _as_int(item.get("serverId"))
    prefix = server_names.get(server_id or -1, "") or str(
        item.get("clubName") or item.get("crossUnionName") or item.get("campName") or ""
    ).strip()
    subject = f"{prefix}：{name}" if prefix and name else name or prefix
    progress = _rank_progress_text(activity_id, item)
    return "，".join(part for part in (subject, progress) if part)


def _extract_rank_snapshot(entry: dict[str, Any], server_names: dict[int, str]) -> dict[str, Any] | None:
    parsed = entry.get("content")
    if not isinstance(parsed, dict):
        return None
    vo = parsed.get("vo")
    if not isinstance(vo, dict):
        return None
    activity_id = str(vo.get("activityId") or vo.get("id") or "").strip()
    rank_vos = vo.get("rankVOS")
    raw_items = rank_vos.get("items") if isinstance(rank_vos, dict) else []
    if not activity_id or not isinstance(raw_items, list):
        return None
    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        parent = _rank_super(raw_item)
        rank = _as_int(parent.get("rank"))
        if rank is None:
            continue
        items.append(
            {
                "rank": rank,
                "id": parent.get("id"),
                "name": parent.get("name") or raw_item.get("name") or "",
                "key": parent.get("key") or "",
                "index": parent.get("index"),
                "server_id": raw_item.get("serverId"),
                "server_name": server_names.get(_as_int(raw_item.get("serverId")) or -1, ""),
                "score": raw_item.get("score"),
                "ext_score": raw_item.get("extScore"),
                "ext_score2": raw_item.get("extScore2"),
                "club_name": raw_item.get("clubName") or "",
                "cross_union_name": raw_item.get("crossUnionName") or "",
                "camp_name": raw_item.get("campName") or "",
                "text": _rank_entry_text(activity_id, raw_item, server_names),
            }
        )
    if not items:
        return None
    items.sort(key=lambda item: int(item.get("rank") or 10**9))
    return {
        "activity_id": activity_id,
        "id": vo.get("id"),
        "group": vo.get("group"),
        "rank_list_size": vo.get("rankListSize"),
        "rank_state": vo.get("rankState"),
        "personal_state": vo.get("personalState"),
        "rank_vo_type": rank_vos.get("_type") if isinstance(rank_vos, dict) else "",
        "rank_vo_type_id": rank_vos.get("_type_id") if isinstance(rank_vos, dict) else "",
        "items": items,
    }


def _merge_record(existing: dict[str, Any] | None, item: dict[str, Any], entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    evidence = {
        "packet_id": entry.get("id") or "",
        "decoded_at": entry.get("decoded_at") or "",
        "record_id": entry.get("record_id") or "",
        "pcap_name": entry.get("pcap_name") or "",
        "source_kind": entry.get("source_kind") or "",
        "protocol": entry.get("name") or WORLDLINE_ACTIVITY_PROTOCOL,
        "pro_id": entry.get("pro_id") or 51006,
    }
    if not existing:
        return {
            "key": _activity_identity_key(item),
            "item": item,
            "first_seen_at": entry.get("decoded_at") or _now_text(),
            "last_seen_at": entry.get("decoded_at") or _now_text(),
            "evidence": [evidence],
        }, True

    next_record = dict(existing)
    next_record["key"] = _activity_identity_key(_merge_activity_item(existing.get("item") or {}, item))
    next_record["item"] = _merge_activity_item(existing.get("item") or {}, item)
    next_record["last_seen_at"] = entry.get("decoded_at") or existing.get("last_seen_at") or _now_text()
    evidence_rows = [row for row in existing.get("evidence") or [] if isinstance(row, dict)]
    evidence_keys = {str(row.get("packet_id") or "") for row in evidence_rows}
    changed = next_record["item"] != existing.get("item") or next_record["last_seen_at"] != existing.get("last_seen_at")
    packet_id = str(evidence.get("packet_id") or "")
    if packet_id and packet_id not in evidence_keys:
        evidence_rows.append(evidence)
        changed = True
    next_record["evidence"] = evidence_rows[-12:]
    return next_record, changed


def _rank_record_key(snapshot: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("activity_id", "group"):
        value = snapshot.get(key)
        values.append("" if value is None else str(value))
    return "|".join(values)


def _merge_rank_snapshot(existing: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    next_snapshot = {**existing, **snapshot}
    items_by_index: dict[int, dict[str, Any]] = {}
    for items in (existing.get("items") or [], snapshot.get("items") or []):
        if not isinstance(items, list):
            continue
        for source in items:
            if not isinstance(source, dict):
                continue
            try:
                index = int(source.get("index") or 0)
            except (TypeError, ValueError):
                continue
            if index < 0:
                continue
            items_by_index[index] = source
    next_snapshot["items"] = [items_by_index[index] for index in sorted(items_by_index)]
    return next_snapshot


def _merge_rank_record(existing: dict[str, Any] | None, snapshot: dict[str, Any], entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    evidence = {
        "packet_id": entry.get("id") or "",
        "decoded_at": entry.get("decoded_at") or "",
        "record_id": entry.get("record_id") or "",
        "pcap_name": entry.get("pcap_name") or "",
        "source_kind": entry.get("source_kind") or "",
        "protocol": entry.get("name") or ACTIVITY_RANK_PROTOCOL,
        "pro_id": entry.get("pro_id") or 51104,
    }
    if not existing:
        return {
            "key": _rank_record_key(snapshot),
            "snapshot": snapshot,
            "first_seen_at": entry.get("decoded_at") or _now_text(),
            "last_seen_at": entry.get("decoded_at") or _now_text(),
            "evidence": [evidence],
        }, True

    next_record = dict(existing)
    next_record["snapshot"] = _merge_rank_snapshot(existing.get("snapshot") or {}, snapshot)
    next_record["last_seen_at"] = entry.get("decoded_at") or existing.get("last_seen_at") or _now_text()
    evidence_rows = [row for row in existing.get("evidence") or [] if isinstance(row, dict)]
    evidence_keys = {str(row.get("packet_id") or "") for row in evidence_rows}
    changed = next_record["snapshot"] != existing.get("snapshot") or next_record["last_seen_at"] != existing.get("last_seen_at")
    packet_id = str(evidence.get("packet_id") or "")
    if packet_id and packet_id not in evidence_keys:
        evidence_rows.append(evidence)
        changed = True
    next_record["evidence"] = evidence_rows[-12:]
    return next_record, changed


def sync_fanxiu_activity_packets(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    state = _load_state(data_dir)
    cursor = {} if force else dict(state.get("worldline_activity") or {})
    rank_cursor = {} if force else dict(state.get("activity_rank") or {})
    store = _load_records(data_dir)
    rank_store = _load_rank_records(data_dir)
    records_by_key = {
        _activity_identity_key(row.get("item") or {}) or str(row.get("key") or ""): row
        for row in store.get("records") or []
        if isinstance(row, dict) and str(row.get("key") or "")
    }
    rank_records_by_key = {
        str(row.get("key") or ""): row
        for row in rank_store.get("records") or []
        if isinstance(row, dict) and str(row.get("key") or "")
    }

    entries = _build_fanxiu_tcp_entries(str(resolve_fanxiu_tcp_store_root(data_dir)), export_root=export_root)
    entries.sort(key=_packet_order_key)
    server_names: dict[int, str] = {}
    scanned_packets = 0
    matched_packets = 0
    matched_rank_packets = 0
    inserted = 0
    updated = 0
    rank_inserted = 0
    rank_updated = 0
    skipped_duplicates = 0
    rank_skipped_duplicates = 0
    last_entry: dict[str, Any] | None = None
    last_rank_entry: dict[str, Any] | None = None
    for snapshot in reversed(list_fanxiu_worldline_activity_schedule_snapshots(data_dir=data_dir, export_root=export_root)):
        snapshot_entry = {
            "id": snapshot.get("source_path") or "",
            "decoded_at": snapshot.get("created_at") or _now_text(),
            "record_id": "",
            "pcap_name": Path(str(snapshot.get("pcap") or snapshot.get("source_path") or "")).name,
            "source_kind": snapshot.get("source_kind") or "",
            "name": snapshot.get("protocol") or WORLDLINE_ACTIVITY_PROTOCOL,
            "pro_id": snapshot.get("pro_id") or 51006,
        }
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = _activity_identity_key(item)
            if not key.strip("|"):
                continue
            merged, _changed = _merge_record(records_by_key.get(key), item, snapshot_entry)
            records_by_key[key] = merged

    for entry in entries:
        server_names.update(_server_names_from_packet(entry.get("content")))
        name = str(entry.get("name") or "")
        pro_id = int(entry.get("pro_id") or 0)
        should_scan_worldline = _is_after_cursor(entry, cursor)
        should_scan_rank = _is_after_cursor(entry, rank_cursor)
        if not should_scan_worldline and not should_scan_rank:
            continue
        scanned_packets += 1
        if should_scan_worldline:
            last_entry = entry
            if name == WORLDLINE_ACTIVITY_PROTOCOL or pro_id == 51006:
                items = _extract_worldline_items(entry, export_root=export_root)
                if items:
                    matched_packets += 1
                for item in items:
                    key = _activity_identity_key(item)
                    if not key.strip("|"):
                        continue
                    merged, changed = _merge_record(records_by_key.get(key), item, entry)
                    if key in records_by_key:
                        if changed:
                            updated += 1
                        else:
                            skipped_duplicates += 1
                    else:
                        inserted += 1
                    records_by_key[key] = merged
        if should_scan_rank:
            last_rank_entry = entry
            if name == ACTIVITY_RANK_PROTOCOL or pro_id == 51104:
                snapshot = _extract_rank_snapshot(entry, server_names)
                if snapshot:
                    matched_rank_packets += 1
                    key = _rank_record_key(snapshot)
                    merged, changed = _merge_rank_record(rank_records_by_key.get(key), snapshot, entry)
                    if key in rank_records_by_key:
                        if changed:
                            rank_updated += 1
                        else:
                            rank_skipped_duplicates += 1
                    elif key.strip("|"):
                        rank_inserted += 1
                    if key.strip("|"):
                        rank_records_by_key[key] = merged

    records = sorted(
        records_by_key.values(),
        key=lambda row: (
            str((row.get("item") or {}).get("startTime") or ""),
            str((row.get("item") or {}).get("name") or ""),
            str(row.get("key") or ""),
        ),
        reverse=True,
    )
    _write_json(
        _records_path(data_dir),
        {
            "updated_at": _now_text(),
            "source": "packet_history",
            "records": records,
        },
    )
    rank_records = sorted(
        rank_records_by_key.values(),
        key=lambda row: (
            str((row.get("snapshot") or {}).get("activity_id") or ""),
            str((row.get("snapshot") or {}).get("group") or ""),
        ),
    )
    _write_json(
        _rank_records_path(data_dir),
        {
            "updated_at": _now_text(),
            "source": "packet_history",
            "records": rank_records,
        },
    )
    if last_entry:
        state["worldline_activity"] = {
            "last_packet_scan_at": last_entry.get("decoded_at") or "",
            "last_packet_id": last_entry.get("id") or "",
            "updated_at": _now_text(),
        }
    if last_rank_entry:
        state["activity_rank"] = {
            "last_packet_scan_at": last_rank_entry.get("decoded_at") or "",
            "last_packet_id": last_rank_entry.get("id") or "",
            "updated_at": _now_text(),
        }
    if last_entry or last_rank_entry:
        _write_json(_state_path(data_dir), state)

    return {
        "ok": True,
        "state_path": str(_state_path(data_dir)),
        "records_path": str(_records_path(data_dir)),
        "rank_records_path": str(_rank_records_path(data_dir)),
        "cursor": state.get("worldline_activity") or cursor,
        "rank_cursor": state.get("activity_rank") or rank_cursor,
        "scanned_packets": scanned_packets,
        "matched_packets": matched_packets,
        "matched_rank_packets": matched_rank_packets,
        "inserted": inserted,
        "updated": updated,
        "rank_inserted": rank_inserted,
        "rank_updated": rank_updated,
        "skipped_duplicates": skipped_duplicates,
        "rank_skipped_duplicates": rank_skipped_duplicates,
        "record_count": len(records),
        "rank_record_count": len(rank_records),
    }


def get_fanxiu_activity_rank_records(data_dir: str | Path | None = None) -> dict[str, Any]:
    return _load_rank_records(data_dir)


def decode_and_sync_fanxiu_activity_capture(
    pcap_path: str | Path,
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    server_host: str = DEFAULT_FANXIU_SERVER_HOST,
    max_streams: int = 8,
) -> dict[str, Any]:
    path = Path(pcap_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到 pcap：{path}")
    stream_rows = list_tcp_streams_with_tshark(path, host=server_host)
    stream_ids: list[int] = []
    for row in stream_rows[: max(1, int(max_streams))]:
        stream_value = row.get("stream")
        if stream_value is None or str(stream_value).strip() == "":
            continue
        stream_ids.append(int(stream_value))
    decoded: list[dict[str, Any]] = []
    target_protocols = {WORLDLINE_ACTIVITY_PROTOCOL, ACTIVITY_RANK_PROTOCOL}
    target_ids = {51006, 51104}
    for stream in stream_ids:
        result = decode_fanxiu_tcp_pcap(
            path,
            stream=stream,
            server_host=server_host,
            export_root=export_root,
            persist=True,
            data_dir=data_dir,
        )
        frames = result.get("frames") or []
        target_count = sum(
            1
            for frame in frames
            if isinstance(frame, dict)
            and (str(frame.get("name") or "") in target_protocols or int(frame.get("pro_id") or 0) in target_ids)
        )
        decoded.append(
            {
                "stream": stream,
                "output_path": result.get("output_path") or "",
                "record_id": result.get("record_id") or "",
                "target_count": target_count,
            }
        )
    sync = sync_fanxiu_activity_packets(data_dir=data_dir, export_root=export_root, force=False)
    return {
        "ok": True,
        "pcap_path": str(path),
        "server_host": server_host,
        "stream_count": len(stream_rows),
        "decoded_count": len(decoded),
        "decoded": decoded,
        "activity_packet_sync": sync,
    }


def get_fanxiu_activity_packet_schedule(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    store = _load_records(data_dir)
    records = [row for row in store.get("records") or [] if isinstance(row, dict)]
    latest = get_latest_fanxiu_worldline_activity_schedule(data_dir=data_dir, export_root=export_root)
    snapshots = list_fanxiu_worldline_activity_schedule_snapshots(data_dir=data_dir, export_root=export_root)
    if records or snapshots:
        items_by_key: dict[str, dict[str, Any]] = {}
        for row in records:
            item = row.get("item")
            if isinstance(item, dict):
                key = _activity_identity_key(item)
                items_by_key[key] = _merge_activity_item(items_by_key.get(key) or {}, item)

        if not snapshots and latest.get("items"):
            snapshots = [latest]
        for snapshot in reversed(snapshots):
            for item in snapshot.get("items") or []:
                if isinstance(item, dict):
                    key = _activity_identity_key(item)
                    items_by_key[key] = _merge_activity_item(items_by_key.get(key) or {}, item)

        items = sorted(
            items_by_key.values(),
            key=lambda item: (
                str(item.get("startTime") or ""),
                str(item.get("name") or ""),
                str(item.get("activityId") or item.get("id") or ""),
            ),
            reverse=True,
        )
        return {
            "available": bool(items),
            "source_kind": "activity_packet_sync+latest",
            "source_path": str(_records_path(data_dir)),
            "created_at": store.get("updated_at") or "",
            "pcap": "",
            "stream": 0,
            "server_host": "",
            "protocol": WORLDLINE_ACTIVITY_PROTOCOL,
            "pro_id": 51006,
            "openServerTime": "",
            "openServerTimeText": "",
            "count": len(items),
            "decode_warnings": [],
            "items": items,
            "sync": {
                "cursor": (_load_state(data_dir).get("worldline_activity") or {}),
                "record_count": len(records),
                "latest_count": len(latest.get("items") or []),
                "snapshot_count": len(snapshots),
            },
        }
    return latest
