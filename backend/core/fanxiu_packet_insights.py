from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyxllib.file.packetstream import LuaPacketSchemaIndex, VarintBinaryReader, maybe_zlib_decompress

from backend.core.fanxiu_activity_packet_sync import get_fanxiu_activity_rank_records, sync_fanxiu_activity_packets
from backend.core.fanxiu_item_catalog import load_fanxiu_item_runtime_index
from backend.core.fanxiu_packet_business_store import upsert_fanxiu_packet_business_records
from backend.core.fanxiu_player_profile_store import upsert_fanxiu_player_profile_rows
from backend.core.fanxiu_resources import resolve_fanxiu_export_root
from backend.core.fanxiu_server_mapping import resolve_fanxiu_region_server_by_id
from backend.core.fanxiu_tcp_flow import (
    DEFAULT_FANXIU_SERVER_HOST,
    DEFAULT_TEXT_ASSETS,
    _build_fanxiu_tcp_entries,
    _decode_lusuo_frames_tolerant,
    _iter_fanxiu_tcp_decoded_sources,
    _patch_fanxiu_schema_long_list,
    decode_fanxiu_tcp_pcap,
    extract_tcp_stream_payloads_with_tshark,
    list_tcp_streams_with_tshark,
    resolve_fanxiu_tcp_store_root,
)
from backend.core.settings import get_settings

PACKET_INSIGHT_SCHEMA_VERSION = 16

PACKET_RUNTIME_INSIGHT_PROTOCOLS = {
    "SM_Login",
    "SM_ActivityRankSync",
    "SM_Wallet",
    "SM_AllBagSyncInfo",
    "SM_ShowOther",
    "SM_SyncPlayer",
    "SM_SyncAllEquipment",
    "SM_BlueStarSeaEnergyChange",
    "SM_TakeMedicineSync",
    "SM_RoleChangedAttrs",
    "SM_ChangedPlayerAttribute",
    "CM_WorshipRank",
    "CM_WorshipInfo",
    "CM_Worship",
    "CM_WorshipGotRecord",
    "CM_WorldLevelWorshipInfoSync",
    "CM_WorldLevelRankWorship",
    "SM_WorshipRank",
    "SM_WorshipInfo",
    "SM_Worship",
    "SM_WorshipGotRecord",
    "SM_WorldLevelWorshipInfoSync",
    "SM_WorldLevelRankWorship",
}
PLAYER_PROFILE_PROTOCOLS = {"SM_ShowOther", "SM_SyncPlayer"}
SELF_PROFILE_ATTRIBUTE_PROTOCOLS = {"SM_RoleChangedAttrs", "SM_ChangedPlayerAttribute"}
PLAYER_PROFILE_IDENTITY_PROTOCOLS = {"SM_Login", "SM_ActivityRankSync"}
PLAYER_PROFILE_SOURCE_PROTOCOLS = PLAYER_PROFILE_PROTOCOLS | SELF_PROFILE_ATTRIBUTE_PROTOCOLS | PLAYER_PROFILE_IDENTITY_PROTOCOLS
ACTIVITY_PACKET_PROTOCOLS = {"SM_WorldLineActivitySync", "SM_ActivityRankSync"}
FANXIU_STORAGE_BAG_OWNER_ROLE_ID = "24082878061086206"
FANXIU_STORAGE_BAG_OWNER_NAME_KEYWORD = "羊驼"


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _insight_root(data_dir: str | Path | None = None) -> Path:
    base = Path(data_dir).expanduser().resolve() if data_dir else get_settings().data_dir
    path = base / "fanxiu" / "packet-insights"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(data_dir: str | Path | None = None) -> Path:
    return _insight_root(data_dir) / "state.json"


def _snapshot_path(data_dir: str | Path | None = None) -> Path:
    return _insight_root(data_dir) / "account_runtime_insights.json"


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


def _source_signature(data_dir: str | Path | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for source in _iter_fanxiu_tcp_decoded_sources(data_dir):
        decoded_path = Path(str(source.get("decoded_path") or ""))
        if not decoded_path.is_file():
            continue
        stat = decoded_path.stat()
        rows.append(
            {
                "path": str(decoded_path),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
        )
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"hash": digest, "decoded_file_count": len(rows), "files": rows}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
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


def _items(value: Any) -> list[Any]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    if isinstance(value, list):
        return value
    return []


def _super(value: dict[str, Any]) -> dict[str, Any]:
    parent = value.get("_super")
    return parent if isinstance(parent, dict) else {}


def _packet_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_id": entry.get("id") or "",
        "protocol": entry.get("name") or "",
        "decoded_at": entry.get("decoded_at") or "",
        "record_id": entry.get("record_id") or "",
        "pcap_name": entry.get("pcap_name") or "",
    }


def _load_item_index(export_root: str | Path | None = None) -> dict[str, Any]:
    try:
        return load_fanxiu_item_runtime_index(export_root=export_root, rebuild_missing=True)
    except Exception:
        return {"cards_by_id": {}}


def _resolve_export_child(export_root: str | Path | None, path: str | Path) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw = Path(path)
    return raw.expanduser().resolve() if raw.is_absolute() else (root / raw).resolve()


def _entry_text_assets_path(entry: dict[str, Any], export_root: str | Path | None = None) -> Path:
    source_path = Path(str(entry.get("source_path") or ""))
    meta_candidates: list[Path] = []
    if source_path:
        meta_candidates.append(source_path.parent / "meta.json")
    stored_pcap = Path(str(entry.get("stored_pcap") or ""))
    if stored_pcap:
        meta_candidates.append(stored_pcap.parent / "meta.json")
    for meta_path in meta_candidates:
        if not meta_path.is_file():
            continue
        meta = _load_json(meta_path, {})
        if not isinstance(meta, dict):
            continue
        text_assets = str(meta.get("text_assets") or "").strip()
        if text_assets:
            path = Path(text_assets).expanduser()
            if path.is_dir():
                return path.resolve()
    return _resolve_export_child(export_root, DEFAULT_TEXT_ASSETS)


def _item_summary(base_id: Any, item_index: dict[str, Any]) -> dict[str, Any]:
    item_id = "" if base_id is None else str(base_id)
    card = (item_index.get("cards_by_id") or {}).get(item_id) or {}
    return {
        "item_id": item_id,
        "name": card.get("name") or (f"道具 {item_id}" if item_id else ""),
        "quality_name": card.get("quality_name") or "",
        "type_name": card.get("type_name") or card.get("sub_type_name") or "",
        "icon": card.get("icon") or "",
        "description": card.get("description") or "",
        "effect_description": card.get("effect_description") or "",
        "effect_detail_preview": card.get("effect_detail_preview") or "",
    }


def _has_truncated_items(value: Any) -> bool:
    if isinstance(value, dict):
        if int(value.get("_truncated_items") or 0) > 0:
            return True
        return any(_has_truncated_items(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_truncated_items(item) for item in value)
    return False


def _bag_section_summaries(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, bag in enumerate(_items(parsed.get("bagInfoVOs"))):
        if not isinstance(bag, dict):
            continue
        item_vos = bag.get("itemVOs")
        if isinstance(item_vos, dict):
            declared = int(item_vos.get("_count") or 0)
            decoded = len(_items(item_vos))
            rows.append(
                {
                    "index": index,
                    "vo_type": item_vos.get("_type") or "",
                    "declared_count": declared,
                    "decoded_count": decoded,
                    "truncated_count": int(item_vos.get("_truncated_items") or max(0, declared - decoded)),
                }
            )
        elif isinstance(item_vos, list):
            rows.append(
                {
                    "index": index,
                    "vo_type": "",
                    "declared_count": len(item_vos),
                    "decoded_count": len(item_vos),
                    "truncated_count": 0,
                }
            )
    return rows


def _full_parsed_from_packet(entry: dict[str, Any], packet_name: str, export_root: str | Path | None = None) -> dict[str, Any] | None:
    pcap_text = str(entry.get("stored_pcap") or entry.get("source_pcap") or "").strip()
    if not pcap_text:
        return None
    pcap_path = Path(pcap_text).expanduser()
    if not pcap_path.is_file():
        return None
    try:
        schema = _patch_fanxiu_schema_long_list(LuaPacketSchemaIndex(_entry_text_assets_path(entry, export_root)))
        _c2s_payload, s2c_payload = extract_tcp_stream_payloads_with_tshark(
            pcap_path,
            int(entry.get("stream") or 0),
            server_host=DEFAULT_FANXIU_SERVER_HOST,
        )
        frames, _warnings = _decode_lusuo_frames_tolerant(s2c_payload, schema)
    except Exception:
        return None
    target_sn = _as_int(entry.get("sn"))
    target_pro_id = _as_int(entry.get("pro_id"))
    candidates = [
        frame
        for frame in frames
        if frame.get("name") == packet_name
        and isinstance(frame.get("parsed"), dict)
        and (target_sn is None or _as_int(frame.get("sn")) == target_sn)
        and (target_pro_id is None or _as_int(frame.get("pro_id")) == target_pro_id)
    ]
    if not candidates and target_sn is None and target_pro_id is None:
        candidates = [frame for frame in frames if frame.get("name") == packet_name and isinstance(frame.get("parsed"), dict)]
    return candidates[-1].get("parsed") if candidates else None


def _is_running_snapshot_capture(entry: dict[str, Any]) -> bool:
    texts = [
        str(entry.get("pcap_name") or ""),
        str(entry.get("source_pcap") or ""),
        str(entry.get("stored_pcap") or ""),
    ]
    return any("fanxiu_runtime_snapshot_" in text for text in texts)


def _should_redecode_profile_pcap(entry: dict[str, Any]) -> bool:
    # Running snapshots are intentionally copied while tcpdump is still writing.
    # They can contain truncated packets; realtime ingestion must not block while
    # trying to reconstruct a long profile list from them.
    return not _is_running_snapshot_capture(entry)


def _iter_stream_packet_payloads(data: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pos = 0
    total = len(data)
    while pos + 4 <= total:
        offset = pos
        length = int.from_bytes(data[pos : pos + 4], "big")
        pos += 4
        if pos + length > total:
            break
        body = data[pos : pos + length]
        pos += length
        try:
            reader = VarintBinaryReader(body)
            sn = reader.read_int()
            pro_id = reader.read_int()
            rows.append(
                {
                    "offset": offset,
                    "frame_len": length,
                    "sn": sn,
                    "pro_id": pro_id,
                    "payload": body[reader.pos :],
                }
            )
        except Exception:
            continue
    return rows


def _parse_show_other_payload_prefix(payload: bytes, schema: LuaPacketSchemaIndex) -> dict[str, Any] | None:
    data, compressed = maybe_zlib_decompress(payload)
    reader = VarintBinaryReader(data)
    bean_id = reader.read_int()
    if bean_id in (-1, 0):
        return {"_class": "SM_ShowOther", "otherRoleVO": None, "zlib": compressed, "partial": True}
    info = schema.by_id.get(bean_id)
    if info is None or info.name != "OtherRoleVO":
        return None
    role_vo: dict[str, Any] = {"_class": "OtherRoleVO", "_bean_id": bean_id}
    role_vo["roleId"] = reader.read_long()
    role_vo["name"] = reader.read_string()
    role_vo["server"] = reader.read_int()
    role_vo["sex"] = reader.read_int()
    role_vo["model"] = reader.read_int()
    role_vo["avatar"] = reader.read_int()
    role_vo["hangPoint"] = schema._read_bean(reader, expected="HangPointVO", depth=1)
    role_vo["face"] = schema._read_list(reader, depth=1)
    role_vo["level"] = reader.read_int()
    role_vo["exp"] = reader.read_long()
    role_vo["vipLevel"] = reader.read_int()
    role_vo["battleScore"] = reader.read_double()
    role_vo["favor"] = reader.read_int()
    role_vo["characterList"] = schema._read_list(reader, depth=1)
    role_vo["allianceId"] = reader.read_int()
    role_vo["identity"] = reader.read_int()
    role_vo["npcId"] = reader.read_int()
    role_vo["attrMap"] = schema._read_map(reader, depth=1)
    return {
        "_class": "SM_ShowOther",
        "otherRoleVO": role_vo,
        "zlib": compressed,
        "partial": True,
        "partial_parsed_bytes": reader.pos,
    }


def _partial_show_other_parsed_from_packet(entry: dict[str, Any], export_root: str | Path | None = None) -> dict[str, Any] | None:
    pcap_text = str(entry.get("stored_pcap") or entry.get("source_pcap") or "").strip()
    if not pcap_text:
        return None
    pcap_path = Path(pcap_text).expanduser()
    if not pcap_path.is_file():
        return None
    try:
        schema = _patch_fanxiu_schema_long_list(LuaPacketSchemaIndex(_entry_text_assets_path(entry, export_root)))
        _c2s_payload, s2c_payload = extract_tcp_stream_payloads_with_tshark(
            pcap_path,
            int(entry.get("stream") or 0),
            server_host=DEFAULT_FANXIU_SERVER_HOST,
        )
    except Exception:
        return None
    target_sn = _as_int(entry.get("sn"))
    target_pro_id = _as_int(entry.get("pro_id")) or 30008
    candidates = [
        packet
        for packet in _iter_stream_packet_payloads(s2c_payload)
        if _as_int(packet.get("pro_id")) == target_pro_id and (target_sn is None or _as_int(packet.get("sn")) == target_sn)
    ]
    if not candidates and target_sn is None:
        candidates = [packet for packet in _iter_stream_packet_payloads(s2c_payload) if _as_int(packet.get("pro_id")) == 30008]
    for packet in reversed(candidates):
        payload = packet.get("payload")
        if not isinstance(payload, bytes):
            continue
        try:
            parsed = _parse_show_other_payload_prefix(payload, schema)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _recover_show_other_parsed_from_packet(entry: dict[str, Any], export_root: str | Path | None = None) -> dict[str, Any] | None:
    return _full_show_other_parsed_from_packet(entry, export_root=export_root) or _partial_show_other_parsed_from_packet(entry, export_root=export_root)


def _full_bag_parsed_from_packet(entry: dict[str, Any], export_root: str | Path | None = None) -> dict[str, Any] | None:
    return _full_parsed_from_packet(entry, "SM_AllBagSyncInfo", export_root=export_root)


def _full_show_other_parsed_from_packet(entry: dict[str, Any], export_root: str | Path | None = None) -> dict[str, Any] | None:
    return _full_parsed_from_packet(entry, "SM_ShowOther", export_root=export_root)


def _full_sync_player_parsed_from_packet(entry: dict[str, Any], export_root: str | Path | None = None) -> dict[str, Any] | None:
    return _full_parsed_from_packet(entry, "SM_SyncPlayer", export_root=export_root)


def _resource_row(value: dict[str, Any], *, item_index: dict[str, Any]) -> dict[str, Any]:
    code = value.get("code")
    row = {
        "type": value.get("type"),
        "code": code,
        "amount": value.get("amount"),
        "history": value.get("history"),
        "borrow": value.get("borrow"),
    }
    if code not in (None, 0, ""):
        row["item"] = _item_summary(code, item_index)
    else:
        row["name"] = f"资源类型 {value.get('type')}"
    return row


def _normalize_role(role: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    account_id = role.get("accountId") or ""
    server = role.get("server") or role.get("realServer")
    if not server and isinstance(account_id, str):
        match = re.match(r"^(\d+):", account_id)
        if match:
            server = _as_int(match.group(1))
    return {
        "account_id": account_id,
        "role_id": role.get("roleId") or role.get("id") or _super(role).get("id"),
        "name": role.get("name") or role.get("playerName") or "",
        "level": role.get("level"),
        "vip_level": role.get("vipLevel"),
        "server": server,
        "battle_score": role.get("battleScore") or role.get("fightScore"),
        "club_name": (role.get("clubOutlookVO") or {}).get("clubName") if isinstance(role.get("clubOutlookVO"), dict) else "",
        "alliance_name": (role.get("allianceOutlookVO") or {}).get("allianceName") if isinstance(role.get("allianceOutlookVO"), dict) else "",
        "captured_at": entry.get("decoded_at") or "",
        "evidence": _packet_evidence(entry),
    }


def _extract_login_account(parsed: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
    role = parsed.get("role")
    if not isinstance(role, dict):
        return None
    row = _normalize_role(role, entry)
    row["account_id"] = parsed.get("accountId") or row.get("account_id") or ""
    if not row.get("server") and isinstance(row.get("account_id"), str):
        match = re.match(r"^(\d+):", str(row.get("account_id") or ""))
        if match:
            row["server"] = _as_int(match.group(1))
    row["token_seen"] = bool(parsed.get("token"))
    return row


def _extract_self_rank_identity(parsed: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
    vo = parsed.get("vo")
    if not isinstance(vo, dict):
        return None
    self_rank = vo.get("selfRankVO")
    if not isinstance(self_rank, dict):
        return None
    parent = _super(self_rank)
    return {
        "role_id": parent.get("id"),
        "name": parent.get("name") or "",
        "account_id": parent.get("key") or "",
        "level": self_rank.get("level"),
        "server": self_rank.get("serverId"),
        "club_name": self_rank.get("clubName") or "",
        "captured_at": entry.get("decoded_at") or "",
        "evidence": _packet_evidence(entry),
    }


def _extract_wallet(parsed: dict[str, Any], entry: dict[str, Any], item_index: dict[str, Any]) -> dict[str, Any] | None:
    rows = [
        _resource_row(item, item_index=item_index)
        for item in _items(parsed.get("items"))
        if isinstance(item, dict)
    ]
    if not rows:
        return None
    rows.sort(key=lambda item: (int(item.get("type") or 0), str(item.get("code") or "")))
    return {
        "captured_at": entry.get("decoded_at") or "",
        "resource_count": len(rows),
        "resources": rows,
        "evidence": _packet_evidence(entry),
    }


def _is_storage_bag_owner_identity(identity: dict[str, Any] | None) -> bool:
    if not identity:
        return False
    role_id = str(identity.get("role_id") or "").strip()
    if role_id == FANXIU_STORAGE_BAG_OWNER_ROLE_ID:
        return True
    name = str(identity.get("name") or "").strip()
    return bool(name and FANXIU_STORAGE_BAG_OWNER_NAME_KEYWORD in name)


def _storage_bag_owner_fields(identity: dict[str, Any] | None) -> dict[str, Any]:
    if not identity:
        return {
            "owner_role_id": None,
            "owner_role_id_text": "",
            "owner_name": "",
            "owner_account_id": "",
            "owner_server": None,
        }
    return {
        "owner_role_id": identity.get("role_id"),
        "owner_role_id_text": str(identity.get("role_id") or ""),
        "owner_name": identity.get("name") or "",
        "owner_account_id": identity.get("account_id") or "",
        "owner_server": identity.get("server"),
    }


def _storage_bag_owner_key(bag: dict[str, Any]) -> str:
    role_id = str(bag.get("owner_role_id_text") or bag.get("owner_role_id") or "").strip()
    if role_id:
        return f"role:{role_id}"
    account_id = str(bag.get("owner_account_id") or "").strip()
    if account_id:
        return f"account:{account_id}"
    name = str(bag.get("owner_name") or "").strip()
    if name:
        return f"name:{name}"
    evidence = bag.get("evidence") if isinstance(bag.get("evidence"), dict) else {}
    return f"unknown:{evidence.get('packet_id') or bag.get('captured_at') or ''}"


def _latest_bag_by_owner(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_owner: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _storage_bag_owner_key(row)
        current = by_owner.get(key)
        if not current or str(row.get("captured_at") or "") >= str(current.get("captured_at") or ""):
            by_owner[key] = row
    return by_owner


def _is_storage_bag_owner_snapshot(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    role_id = str(row.get("owner_role_id_text") or row.get("owner_role_id") or "").strip()
    if role_id == FANXIU_STORAGE_BAG_OWNER_ROLE_ID:
        return True
    name = str(row.get("owner_name") or "").strip()
    return bool(name and FANXIU_STORAGE_BAG_OWNER_NAME_KEYWORD in name)


def _select_storage_bag_owner_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    owner_rows = [row for row in rows if _is_storage_bag_owner_snapshot(row)]
    return _latest(owner_rows)


def _extract_bag(
    parsed: dict[str, Any],
    entry: dict[str, Any],
    item_index: dict[str, Any],
    *,
    owner_identity: dict[str, Any] | None = None,
    export_root: str | Path | None = None,
    allow_pcap_redecode: bool = True,
) -> dict[str, Any] | None:
    if allow_pcap_redecode and _has_truncated_items(parsed):
        full_parsed = _full_bag_parsed_from_packet(entry, export_root=export_root)
        if isinstance(full_parsed, dict) and not _has_truncated_items(full_parsed):
            row = _extract_bag(
                full_parsed,
                entry,
                item_index,
                owner_identity=owner_identity,
                export_root=export_root,
                allow_pcap_redecode=False,
            )
            if row:
                row["decoded_from_pcap"] = True
                row["decoded_source"] = str(entry.get("stored_pcap") or entry.get("source_pcap") or "")
                return row

    compact_items: list[dict[str, Any]] = []
    type_counter: Counter[str] = Counter()
    quality_counter: Counter[str] = Counter()
    stack_count = 0
    declared_stack_count = 0
    total_amount = 0
    for bag in _items(parsed.get("bagInfoVOs")):
        if not isinstance(bag, dict):
            continue
        item_vos = bag.get("itemVOs")
        if isinstance(item_vos, dict):
            declared_stack_count += int(item_vos.get("_count") or 0)
        for raw in _items(item_vos):
            if not isinstance(raw, dict):
                continue
            parent = _super(raw)
            base_id = parent.get("baseId") or raw.get("baseId")
            item_id = parent.get("id") or raw.get("id")
            num = _as_int(parent.get("num") or raw.get("num")) or 0
            item = _item_summary(base_id, item_index)
            stack_count += 1
            total_amount += max(0, num)
            type_counter[item.get("type_name") or "未分类"] += 1
            if item.get("quality_name"):
                quality_counter[str(item["quality_name"])] += 1
            ext = raw.get("ext")
            ext_summary: dict[str, Any] = {}
            if isinstance(ext, str) and ext.strip().startswith("{"):
                try:
                    ext_data = json.loads(ext)
                    if isinstance(ext_data, dict):
                        for key in ("grade", "level", "star", "jie", "pinLevel", "refineNum", "isBreak"):
                            if key in ext_data:
                                ext_summary[key] = ext_data[key]
                except json.JSONDecodeError:
                    pass
            compact_items.append(
                {
                    "instance_id": item_id,
                    "base_id": base_id,
                    "num": num,
                    "item": item,
                    "ext_summary": ext_summary,
                }
            )
    if not stack_count:
        return None
    compact_items.sort(
        key=lambda row: (
            int(row.get("num") or 0),
            str((row.get("item") or {}).get("quality_name") or ""),
            str((row.get("item") or {}).get("name") or ""),
        ),
        reverse=True,
    )
    return {
        "captured_at": entry.get("decoded_at") or "",
        **_storage_bag_owner_fields(owner_identity),
        "stack_count": declared_stack_count or stack_count,
        "decoded_stack_count": stack_count,
        "section_summary": _bag_section_summaries(parsed),
        "is_truncated": _has_truncated_items(parsed),
        "total_amount": total_amount,
        "type_summary": [{"name": key, "count": value} for key, value in type_counter.most_common(16)],
        "quality_summary": [{"name": key, "count": value} for key, value in quality_counter.most_common(12)],
        "items": compact_items,
        "notable_items": compact_items[:160],
        "evidence": _packet_evidence(entry),
    }


def _extract_equipment(parsed: dict[str, Any], entry: dict[str, Any], item_index: dict[str, Any]) -> dict[str, Any] | None:
    rows = []
    for raw in _items(parsed.get("equipmentVOList")):
        if not isinstance(raw, dict):
            continue
        rows.append(
            {
                "slot": raw.get("idx"),
                "item_id": raw.get("itemId"),
                "base_id": raw.get("itemBaseId"),
                "level": raw.get("level"),
                "star": raw.get("star"),
                "item": _item_summary(raw.get("itemBaseId"), item_index),
            }
        )
    if not rows:
        return None
    rows.sort(key=lambda item: int(item.get("slot") or 0))
    return {"captured_at": entry.get("decoded_at") or "", "count": len(rows), "items": rows, "evidence": _packet_evidence(entry)}


def _extract_blue_star_energy(parsed: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
    if parsed.get("energy") is None:
        return None
    return {
        "energy": parsed.get("energy"),
        "last_recover_time": parsed.get("lastRecoverTime"),
        "captured_at": entry.get("decoded_at") or "",
        "evidence": _packet_evidence(entry),
    }


def _extract_medicine(parsed: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
    rows = []
    for raw in _items(parsed.get("takeMedicineVOS")):
        if isinstance(raw, dict):
            rows.append({"medicine_id": raw.get("medicineId"), "num": raw.get("num")})
    if not rows:
        return None
    rows.sort(key=lambda item: int(item.get("num") or 0), reverse=True)
    return {
        "captured_at": entry.get("decoded_at") or "",
        "count": len(rows),
        "top_items": rows[:60],
        "evidence": _packet_evidence(entry),
    }


def _extract_attr_changes(parsed: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
    attrs = parsed.get("attrs") if parsed.get("_class") == "SM_RoleChangedAttrs" else parsed
    if not isinstance(attrs, dict):
        return None
    final_rows = [
        {"key": item.get("key"), "value": item.get("value")}
        for item in _items(attrs.get("finalAttrs") or attrs.get("attributes"))
        if isinstance(item, dict)
    ]
    add_rows = [
        {"key": item.get("key"), "value": item.get("value")}
        for item in _items(attrs.get("addAttrs"))
        if isinstance(item, dict)
    ]
    if not final_rows and not add_rows:
        return None
    return {
        "captured_at": entry.get("decoded_at") or "",
        "protocol": parsed.get("_class") or entry.get("name") or "",
        "final_attrs": final_rows[:80],
        "add_attrs": add_rows[:80],
        "evidence": _packet_evidence(entry),
    }


PLAYER_PROFILE_ATTR_GROUPS: tuple[tuple[str, str, tuple[tuple[int, str], ...]], ...] = (
    ("special_attributes", "特殊属性", ((7739004, "神识"), (99, "天资"))),
    ("immortal_attributes", "仙界属性", ((109, "仙魂"), (110, "悉劫"), (111, "道骨"), (112, "灵慧"))),
    ("combat_attributes", "战斗属性", ((35006, "气血"), (2001, "攻击"), (3001, "灵力"), (4001, "守御"))),
)

PLAYER_PROFILE_CULTIVATION_REALMS = ("炼气", "筑基", "结丹", "元婴", "化神", "炼虚", "合体", "大乘", "真仙", "金仙")
PLAYER_PROFILE_CULTIVATION_STAGES = ("前期", "中期", "后期")
PLAYER_PROFILE_CULTIVATION_LAYER_TEXT = ("壹", "贰", "叁", "肆", "伍", "陆", "柒", "捌", "玖", "拾")
PLAYER_PROFILE_CULTIVATION_ANCHOR_VALUE = 201
PLAYER_PROFILE_CULTIVATION_ANCHOR_REALM = "大乘"
PLAYER_PROFILE_CULTIVATION_ANCHOR_STAGE = "前期"
PLAYER_PROFILE_CULTIVATION_REALM_SPAN = len(PLAYER_PROFILE_CULTIVATION_STAGES) * 10


def _format_player_cultivation_level(value: Any) -> str:
    numeric = _as_int(value)
    if numeric is None:
        return ""
    anchor_realm_index = PLAYER_PROFILE_CULTIVATION_REALMS.index(PLAYER_PROFILE_CULTIVATION_ANCHOR_REALM)
    anchor_stage_index = PLAYER_PROFILE_CULTIVATION_STAGES.index(PLAYER_PROFILE_CULTIVATION_ANCHOR_STAGE)
    offset = numeric - PLAYER_PROFILE_CULTIVATION_ANCHOR_VALUE
    realm_delta = math.floor(offset / PLAYER_PROFILE_CULTIVATION_REALM_SPAN)
    within_realm = offset % PLAYER_PROFILE_CULTIVATION_REALM_SPAN
    realm_index = anchor_realm_index + realm_delta
    if realm_index < 0 or realm_index >= len(PLAYER_PROFILE_CULTIVATION_REALMS):
        return str(numeric)
    stage_index = anchor_stage_index + math.floor(within_realm / 10)
    if stage_index < 0 or stage_index >= len(PLAYER_PROFILE_CULTIVATION_STAGES):
        return str(numeric)
    layer_index = within_realm % 10
    return (
        f"{PLAYER_PROFILE_CULTIVATION_REALMS[realm_index]}"
        f"{PLAYER_PROFILE_CULTIVATION_STAGES[stage_index]}"
        f"{PLAYER_PROFILE_CULTIVATION_LAYER_TEXT[layer_index]}层"
    )


def _format_significant_number(value: float, significant_digits: int = 4) -> str:
    numeric = abs(value)
    if not math.isfinite(numeric) or numeric == 0:
        return "0"
    integer_digits = math.floor(math.log10(numeric)) + 1
    fraction_digits = max(0, significant_digits - integer_digits)
    text = f"{numeric:.{fraction_digits}f}".rstrip("0").rstrip(".")
    return f"-{text}" if value < 0 else text


def _format_fanxiu_game_decimal(value: float, fraction_digits: int = 1) -> str:
    factor = 10**fraction_digits
    truncated = math.floor(abs(value) * factor) / factor
    text = f"{truncated:.{fraction_digits}f}".rstrip("0").rstrip(".")
    return f"-{text}" if value < 0 else text


def _format_panel_number(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "" if value in (None, "") else str(value)
    units = (
        ("秭秭", 10**48),
        ("垓秭", 10**44),
        ("垓垓", 10**40),
        ("京垓", 10**36),
        ("京京", 10**32),
        ("兆京", 10**28),
        ("亿京", 10**24),
        ("万京", 10**20),
        ("京", 10**16),
        ("兆", 10**12),
    )
    for unit, divisor in units:
        if abs(number) >= divisor:
            return f"{_format_fanxiu_game_decimal(number / divisor)}{unit}"
    return _format_significant_number(number)


def _attr_map_values(attr_map: Any) -> dict[int, Any]:
    rows: dict[int, Any] = {}
    for raw in _items(attr_map):
        if not isinstance(raw, dict):
            continue
        key = _as_int(raw.get("key"))
        if key is None:
            continue
        rows[key] = raw.get("value")
    return rows


def _profile_attr_rows(attrs: dict[int, Any], specs: tuple[tuple[int, str], ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, name in specs:
        if key not in attrs:
            continue
        value = attrs[key]
        text = str(int(value)) if key in {7739004, 99} and isinstance(value, int | float) else _format_panel_number(value)
        rows.append({"key": key, "name": name, "value": value, "text": text})
    return rows


def _profile_server_fields(server: Any) -> dict[str, Any]:
    resolved = resolve_fanxiu_region_server_by_id(server)
    return {
        "server": server,
        "region_number": resolved.get("region_number"),
        "region_name": resolved.get("region_name") or "",
        "server_order": resolved.get("server_order"),
        "server_name": resolved.get("server_name") or "",
        "server_id_source": resolved.get("source") or "",
    }


def _extract_show_other_profile(
    parsed: dict[str, Any],
    entry: dict[str, Any],
    *,
    export_root: str | Path | None = None,
    allow_pcap_redecode: bool = True,
) -> dict[str, Any] | None:
    role_vo = parsed.get("otherRoleVO")
    if not isinstance(role_vo, dict):
        if not allow_pcap_redecode or not _should_redecode_profile_pcap(entry):
            return None
        recovered = _recover_show_other_parsed_from_packet(entry, export_root=export_root)
        recovered_role_vo = recovered.get("otherRoleVO") if isinstance(recovered, dict) else None
        if not isinstance(recovered_role_vo, dict):
            return None
        row = _extract_show_other_profile(
            recovered,
            entry,
            export_root=export_root,
            allow_pcap_redecode=False,
        )
        if row:
            row["decoded_from_pcap"] = True
            row["decoded_source"] = str(entry.get("stored_pcap") or entry.get("source_pcap") or "")
            if recovered.get("partial"):
                row["decoded_partial"] = True
        return row
    attr_map = role_vo.get("attrMap")
    if allow_pcap_redecode and _should_redecode_profile_pcap(entry) and _has_truncated_items(attr_map):
        full_parsed = _recover_show_other_parsed_from_packet(entry, export_root=export_root)
        full_role_vo = full_parsed.get("otherRoleVO") if isinstance(full_parsed, dict) else None
        full_attr_map = full_role_vo.get("attrMap") if isinstance(full_role_vo, dict) else None
        if isinstance(full_parsed, dict) and isinstance(full_role_vo, dict) and not _has_truncated_items(full_attr_map):
            row = _extract_show_other_profile(
                full_parsed,
                entry,
                export_root=export_root,
                allow_pcap_redecode=False,
            )
            if row:
                row["decoded_from_pcap"] = True
                row["decoded_source"] = str(entry.get("stored_pcap") or entry.get("source_pcap") or "")
                if full_parsed.get("partial"):
                    row["decoded_partial"] = True
                return row

    attrs = _attr_map_values(attr_map)
    grouped_attrs: dict[str, list[dict[str, Any]]] = {}
    for key, _title, specs in PLAYER_PROFILE_ATTR_GROUPS:
        grouped_attrs[key] = _profile_attr_rows(attrs, specs)
    visible_attrs = [row for rows in grouped_attrs.values() for row in rows]
    if not role_vo.get("roleId") and not role_vo.get("name") and not visible_attrs:
        return None
    attr_count = int(attr_map.get("_count") or len(attrs)) if isinstance(attr_map, dict) else len(attrs)
    server_fields = _profile_server_fields(role_vo.get("server"))
    cultivation_level = role_vo.get("level")
    return {
        "captured_at": entry.get("decoded_at") or "",
        "role_id": role_vo.get("roleId"),
        "role_id_text": str(role_vo.get("roleId") or ""),
        "name": role_vo.get("name") or "",
        **server_fields,
        "location": role_vo.get("location") or "",
        "level": cultivation_level,
        "cultivation_level": _as_int(cultivation_level),
        "cultivation_level_text": _format_player_cultivation_level(cultivation_level),
        "vip_level": role_vo.get("vipLevel"),
        "battle_score": role_vo.get("battleScore"),
        "battle_score_text": _format_panel_number(role_vo.get("battleScore")),
        "attribute_count": attr_count,
        "decoded_attribute_count": len(attrs),
        "is_truncated": _has_truncated_items(attr_map),
        "special_attributes": grouped_attrs["special_attributes"],
        "immortal_attributes": grouped_attrs["immortal_attributes"],
        "combat_attributes": grouped_attrs["combat_attributes"],
        "attributes": visible_attrs,
        "attribute_values": {str(row["key"]): row["value"] for row in visible_attrs},
        "evidence": _packet_evidence(entry),
    }


def _extract_sync_player_profile(
    parsed: dict[str, Any],
    entry: dict[str, Any],
    *,
    export_root: str | Path | None = None,
    allow_pcap_redecode: bool = True,
) -> dict[str, Any] | None:
    player_vo = parsed.get("playerVO")
    if not isinstance(player_vo, dict):
        return None
    visible_vo = _super(player_vo)
    attr_map = visible_vo.get("attrMap")
    if allow_pcap_redecode and _should_redecode_profile_pcap(entry) and _has_truncated_items(attr_map):
        full_parsed = _full_sync_player_parsed_from_packet(entry, export_root=export_root)
        full_player_vo = full_parsed.get("playerVO") if isinstance(full_parsed, dict) else None
        full_visible_vo = _super(full_player_vo) if isinstance(full_player_vo, dict) else {}
        full_attr_map = full_visible_vo.get("attrMap")
        if isinstance(full_player_vo, dict) and isinstance(full_attr_map, dict) and not _has_truncated_items(full_attr_map):
            row = _extract_sync_player_profile(
                full_parsed,
                entry,
                export_root=export_root,
                allow_pcap_redecode=False,
            )
            if row:
                row["decoded_from_pcap"] = True
                row["decoded_source"] = str(entry.get("stored_pcap") or entry.get("source_pcap") or "")
                return row

    attrs = _attr_map_values(attr_map)
    grouped_attrs: dict[str, list[dict[str, Any]]] = {}
    for key, _title, specs in PLAYER_PROFILE_ATTR_GROUPS:
        grouped_attrs[key] = _profile_attr_rows(attrs, specs)
    visible_attrs = [row for rows in grouped_attrs.values() for row in rows]
    role_id = visible_vo.get("id") or player_vo.get("id")
    name = player_vo.get("playerName") or player_vo.get("name") or ""
    if not role_id and not name and not visible_attrs:
        return None
    attr_count = int(attr_map.get("_count") or len(attrs)) if isinstance(attr_map, dict) else len(attrs)
    server = player_vo.get("server") or player_vo.get("realServer")
    server_fields = _profile_server_fields(server)
    cultivation_level = player_vo.get("level") or player_vo.get("roleLevel") or visible_vo.get("level")
    return {
        "captured_at": entry.get("decoded_at") or "",
        "source_kind": "sync_player",
        "source_label": "玩家同步",
        "role_id": role_id,
        "role_id_text": str(role_id or ""),
        "name": name,
        **server_fields,
        "location": "",
        "level": cultivation_level,
        "cultivation_level": _as_int(cultivation_level),
        "cultivation_level_text": _format_player_cultivation_level(cultivation_level),
        "vip_level": player_vo.get("vipLevel"),
        "battle_score": player_vo.get("fightScore"),
        "battle_score_text": _format_panel_number(player_vo.get("fightScore")),
        "attribute_count": attr_count,
        "decoded_attribute_count": len(attrs),
        "is_truncated": _has_truncated_items(attr_map),
        "special_attributes": grouped_attrs["special_attributes"],
        "immortal_attributes": grouped_attrs["immortal_attributes"],
        "combat_attributes": grouped_attrs["combat_attributes"],
        "attributes": visible_attrs,
        "attribute_values": {str(row["key"]): row["value"] for row in visible_attrs},
        "evidence": _packet_evidence(entry),
    }


def _format_worship_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    number = _as_int(value)
    if number is not None:
        if number > 10_000_000_000:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(number / 1000))
        if number > 1_000_000_000:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(number))
    return str(value)


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _extract_lua_record_fields(text: str, record_id: int | str) -> dict[int, Any]:
    match = re.search(rf"\[{re.escape(str(record_id))}\]\s*=\s*setmetatable\(\{{(?P<body>.*?)\}},_P\)", text, re.S)
    if not match:
        return {}
    fields: dict[int, Any] = {}
    for key, raw in re.findall(r"\[(\d+)\]\s*=\s*([^,\}]+)", match.group("body")):
        value = raw.strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            fields[int(key)] = float(value) if "." in value else int(value)
        elif value == "true":
            fields[int(key)] = True
        elif value == "false":
            fields[int(key)] = False
        elif value.startswith("'") and value.endswith("'"):
            fields[int(key)] = value[1:-1]
        else:
            fields[int(key)] = value
    return fields


def _read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _generated_cfg_dir(export_root: str | Path | None, cfg_dir: str) -> Path | None:
    try:
        root = Path(export_root) if export_root else resolve_fanxiu_export_root(None)
    except Exception:
        return None
    base = root / "by_source" / "lscripts" / "generate" / "cfg"
    for path in base.glob(f"{cfg_dir}_*"):
        text_assets = path / "text_assets"
        if text_assets.is_dir():
            return text_assets
    return None


@lru_cache(maxsize=512)
def _worship_activity_cfg(activity_id: int, export_root_text: str = "") -> dict[str, Any]:
    cfg_root = _generated_cfg_dir(export_root_text or None, "activity")
    if not cfg_root:
        return {}
    fields = _extract_lua_record_fields(_read_text_if_exists(cfg_root / "Activity.lua"), activity_id)
    if not fields:
        return {}
    activity_base_id = _as_int(fields.get(28)) or 0
    sub_type = _as_int(fields.get(29)) or 0
    list_subtype = 0
    if sub_type:
        list_fields = _extract_lua_record_fields(_read_text_if_exists(cfg_root / "ActivityList.lua"), sub_type)
        list_subtype = _as_int(list_fields.get(6)) or 0
    rank_type_label = "个人"
    if list_subtype == 9:
        rank_type_label = "社团"
    elif activity_base_id // 100 == 432:
        rank_type_label = "社团"
    cross_group = _as_int(fields.get(18))
    return {
        "activity_id": activity_id,
        "cross_group": cross_group,
        "plane_label": _worship_plane_label(cross_group),
        "activity_base_id": activity_base_id,
        "sub_type": sub_type,
        "activity_list_subtype": list_subtype,
        "rank_type_label": rank_type_label,
    }


@lru_cache(maxsize=512)
def _worship_faze_cfg(faze_id: int, export_root_text: str = "") -> dict[str, Any]:
    cfg_root = _generated_cfg_dir(export_root_text or None, "gongfa")
    if not cfg_root:
        return {}
    fields = _extract_lua_record_fields(_read_text_if_exists(cfg_root / "FazeResource.lua"), faze_id)
    if not fields:
        return {}
    activity_range = _as_int(fields.get(39))
    return {
        "faze_id": faze_id,
        "activity_range": activity_range,
        "activity_range_label": _worship_plane_label(activity_range),
    }


def _worship_rank_type_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if lower in {"1", "personal", "person", "self"}:
        return "个人"
    if lower in {"2", "club", "corps", "guild", "union", "team"}:
        return "社团"
    if "person" in lower or "personal" in lower or "个人" in text:
        return "个人"
    if "club" in lower or "corps" in lower or "guild" in lower or "union" in lower or "社团" in text:
        return "社团"
    return text


def _worship_activity_type_label(value: Any, export_root: str | Path | None = None) -> str:
    number = _as_int(value)
    if number is not None:
        cfg = _worship_activity_cfg(number, str(export_root or ""))
        label = str(cfg.get("rank_type_label") or "").strip()
        if label:
            return label
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if lower in {"1", "personal", "person", "self"}:
        return "个人"
    if lower in {"2", "club", "corps", "guild", "union", "team", "society"}:
        return "社团"
    if "个人" in text or "person" in lower or "personal" in lower:
        return "个人"
    if "社团" in text or "club" in lower or "corps" in lower or "guild" in lower or "union" in lower:
        return "社团"
    return text


def _worship_target_label(*, activity_cfg: dict[str, Any], faze_id: Any = None) -> str:
    activity_base_id = _as_int(activity_cfg.get("activity_base_id"))
    if activity_base_id is not None:
        base_group = activity_base_id // 100
        if base_group in {428, 432}:
            return "天资"
        if base_group == 431:
            return "道丹"
    number = _as_int(faze_id)
    if number is not None:
        if 10050 <= number <= 10055:
            return "天资"
        if 10070 <= number <= 10075:
            return "道丹"
    return ""


def _worship_plane_label(value: Any) -> str:
    if value in (None, ""):
        return ""
    number = _as_int(value)
    if number == 1:
        return "1跨"
    if number in {2, 4, 8, 16, 32, 64}:
        return f"{number}跨"
    text = str(value).strip()
    if not text:
        return ""
    if "跨" in text or "位面" in text:
        return text
    if text == "1":
        return "1跨"
    if text in {"2", "4", "8", "16", "32", "64"}:
        return f"{text}跨"
    return text


def _role_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    merged = {**_super(value), **value}
    name = _first_present(
        merged,
        (
            "playerName",
            "name",
            "roleName",
            "nickName",
            "userName",
            "clubName",
            "corpsName",
            "guildName",
            "unionName",
        ),
    )
    return str(name or "")


def _extract_worship_got_record(raw: dict[str, Any], entry: dict[str, Any], path: str, packet: dict[str, Any]) -> dict[str, Any] | None:
    score = _as_float(raw.get("score"))
    worship_role = raw.get("worshipRoleVO") if isinstance(raw.get("worshipRoleVO"), dict) else {}
    red_role = raw.get("redRoleVO") if isinstance(raw.get("redRoleVO"), dict) else {}
    worship_role_merged = {**_super(worship_role), **worship_role}
    red_role_merged = {**_super(red_role), **red_role}
    worship_role_name = _role_name(worship_role)
    red_role_name = _role_name(red_role)
    role_name = worship_role_name or red_role_name
    if score is None or score <= 0 or not role_name:
        return None
    rank_type = raw.get("activityId")
    activity_cfg = _worship_activity_cfg(_as_int(rank_type) or 0)
    faze_id = packet.get("fazeId") or raw.get("fazeId")
    faze_cfg = _worship_faze_cfg(_as_int(faze_id) or 0)
    plane = _first_present(
        packet,
        (
            "plane",
            "planeName",
            "planeType",
            "cross",
            "crossType",
            "crossGroup",
            "crossRankType",
            "crossServerType",
            "world",
            "worldType",
            "worldLevelType",
            "mergeType",
            "serverPlane",
            "dimension",
        ),
    )
    if plane in (None, ""):
        plane = activity_cfg.get("cross_group") or faze_cfg.get("activity_range")
    plane_label = _worship_plane_label(plane)
    if not plane_label:
        plane_label = str(activity_cfg.get("plane_label") or faze_cfg.get("activity_range_label") or "")
    if not plane_label and (activity_cfg or faze_cfg):
        plane_label = "1跨"
    return {
        "date": _format_worship_time(raw.get("gotTime")),
        "role": role_name,
        "worship_role": worship_role_name,
        "red_role": red_role_name,
        "player_id": worship_role_merged.get("playerId") or red_role_merged.get("playerId"),
        "server": worship_role_merged.get("server") or red_role_merged.get("server"),
        "plane": plane,
        "plane_label": plane_label,
        "rank_type": rank_type,
        "rank_type_label": activity_cfg.get("rank_type_label") or _worship_activity_type_label(rank_type),
        "target_label": _worship_target_label(activity_cfg=activity_cfg, faze_id=faze_id),
        "friendship": score,
        "faze_id": faze_id,
        "activity_id": raw.get("activityId"),
        "got_time": raw.get("gotTime"),
        "captured_at": entry.get("decoded_at") or "",
        "protocol": entry.get("name") or "",
        "source_path": path,
        "evidence": _packet_evidence(entry),
    }


def _extract_worship_rank_record(raw: dict[str, Any], entry: dict[str, Any], path: str, packet: dict[str, Any]) -> dict[str, Any] | None:
    score = _as_float(raw.get("score"))
    worship_role = raw.get("worshipRoleVO") if isinstance(raw.get("worshipRoleVO"), dict) else {}
    worship_role_merged = {**_super(worship_role), **worship_role}
    role_name = _role_name(worship_role)
    if not role_name:
        role_name = str(_first_present({**_super(raw), **raw}, ("name", "playerName", "roleName")) or "")
    if score is None or score <= 0 or not role_name:
        return None
    plane = _first_present(packet, ("cross", "crossGroup", "plane", "planeType", "world", "worldType"))
    rank_type = _first_present(packet, ("rankType", "listType", "type", "worshipType", "scope"))
    activity_cfg = _worship_activity_cfg(_as_int(rank_type) or 0)
    if plane in (None, ""):
        plane = activity_cfg.get("cross_group")
    plane_label = _worship_plane_label(plane)
    if not plane_label and activity_cfg:
        plane_label = "1跨"
    return {
        "date": _format_worship_time(_first_present(packet, ("timeStamp", "recordTime", "time", "date"))),
        "role": role_name,
        "worship_role": role_name,
        "red_role": "",
        "player_id": worship_role_merged.get("playerId"),
        "server": worship_role_merged.get("server"),
        "plane": plane,
        "plane_label": plane_label,
        "rank_type": rank_type,
        "rank_type_label": activity_cfg.get("rank_type_label") or _worship_rank_type_label(rank_type),
        "target_label": _worship_target_label(activity_cfg=activity_cfg),
        "friendship": score,
        "rank": _as_int(raw.get("rank")),
        "captured_at": entry.get("decoded_at") or "",
        "protocol": entry.get("name") or "",
        "source_path": path,
        "evidence": _packet_evidence(entry),
    }


def _iter_worship_candidate_lists(value: Any, path: tuple[str, ...] = ()) -> list[tuple[str, list[Any]]]:
    rows: list[tuple[str, list[Any]]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            key_lower = str(key).lower()
            is_candidate_key = any(part in key_lower for part in ("rank", "record", "history", "worship"))
            child_items = _items(child)
            if is_candidate_key and child_items:
                rows.append((".".join(child_path), child_items))
            if len(path) < 5:
                rows.extend(_iter_worship_candidate_lists(child, child_path))
    elif isinstance(value, list) and len(path) < 5:
        for index, child in enumerate(value[:80]):
            rows.extend(_iter_worship_candidate_lists(child, (*path, str(index))))
    return rows


def _normalize_worship_record(raw: dict[str, Any], entry: dict[str, Any], path: str) -> dict[str, Any] | None:
    parent = _super(raw)
    merged = {**parent, **raw}
    score = _as_float(
        _first_present(
            merged,
            (
                "friendship",
                "friendshipValue",
                "friendly",
                "friendliness",
                "favorability",
                "favor",
                "score",
                "rankScore",
                "worshipValue",
                "value",
                "point",
                "points",
                "totalFriendship",
                "contribution",
            ),
        )
    )
    role_name = _first_present(
        merged,
        (
            "name",
            "playerName",
            "roleName",
            "nickName",
            "userName",
            "clubName",
            "corpsName",
            "guildName",
            "unionName",
        ),
    )
    if score is None or score <= 0 or not role_name:
        return None
    rank_type = _first_present(merged, ("rankType", "listType", "type", "worshipType", "recordType", "scope"))
    plane = _first_present(
        merged,
        (
            "plane",
            "planeName",
            "planeType",
            "cross",
            "crossType",
            "crossGroup",
            "crossRankType",
            "crossServerType",
            "world",
            "worldType",
            "worldLevelType",
            "mergeType",
            "serverPlane",
            "dimension",
        ),
    )
    raw_time = _first_present(merged, ("date", "day", "time", "recordTime", "createTime", "createdAt", "worshipTime", "gotTime", "updateTime"))
    return {
        "date": _format_worship_time(raw_time),
        "role": str(role_name),
        "plane": plane,
        "plane_label": _worship_plane_label(plane),
        "rank_type": rank_type,
        "rank_type_label": _worship_rank_type_label(rank_type),
        "target_label": _worship_target_label(activity_cfg={}, faze_id=_first_present(merged, ("fazeId", "lawId", "ruleId"))),
        "friendship": score,
        "rank": _as_int(_first_present(merged, ("rank", "index", "order"))),
        "faze_id": _first_present(merged, ("fazeId", "lawId", "ruleId")),
        "captured_at": entry.get("decoded_at") or "",
        "protocol": entry.get("name") or "",
        "source_path": path,
        "evidence": _packet_evidence(entry),
    }


def _extract_worship_records(parsed: dict[str, Any], entry: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, float]] = set()
    protocol = str(entry.get("name") or parsed.get("_class") or "")
    explicit_lists: list[tuple[str, list[Any], str]] = []
    if protocol == "SM_WorshipGotRecord":
        explicit_lists.append(("recordVOList", _items(parsed.get("recordVOList")), "got_record"))
    elif protocol == "SM_WorshipRank":
        explicit_lists.append(("rankVOList", _items(parsed.get("rankVOList")), "rank_record"))

    for path, items, kind in explicit_lists:
        for raw in items:
            if not isinstance(raw, dict):
                continue
            if kind == "got_record":
                row = _extract_worship_got_record(raw, entry, path, parsed)
            elif kind == "rank_record":
                row = _extract_worship_rank_record(raw, entry, path, parsed)
            else:
                row = None
            if not row:
                continue
            key = (str(row.get("date") or ""), str(row.get("role") or ""), str(row.get("plane_label") or ""), str(row.get("rank_type_label") or ""), float(row.get("friendship") or 0))
            if key in seen:
                continue
            seen.add(key)
            records.append(row)

    if explicit_lists:
        records.sort(key=lambda item: (float(item.get("friendship") or 0), str(item.get("captured_at") or "")), reverse=True)
        return records

    for path, items in _iter_worship_candidate_lists(parsed):
        for raw in items:
            if not isinstance(raw, dict):
                continue
            row = _normalize_worship_record(raw, entry, path)
            if not row:
                continue
            key = (str(row.get("date") or ""), str(row.get("role") or ""), str(row.get("plane_label") or ""), str(row.get("rank_type_label") or ""), float(row.get("friendship") or 0))
            if key in seen:
                continue
            seen.add(key)
            records.append(row)
    records.sort(key=lambda item: (float(item.get("friendship") or 0), str(item.get("captured_at") or "")), reverse=True)
    return records


def _worship_packet_observation(parsed: dict[str, Any], entry: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "protocol": entry.get("name") or parsed.get("_class") or "",
        "direction": entry.get("direction") or "",
        "captured_at": entry.get("decoded_at") or "",
        "record_count": len(records),
        "evidence": _packet_evidence(entry),
    }


def _format_rank_record(record: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = record.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    top_rows = [item for item in snapshot.get("items") or [] if isinstance(item, dict)]
    personal = snapshot.get("personal_item") if isinstance(snapshot.get("personal_item"), dict) else None
    return {
        "activity_id": snapshot.get("activity_id") or "",
        "group": snapshot.get("group"),
        "rank_list_size": snapshot.get("rank_list_size"),
        "rank_state": snapshot.get("rank_state"),
        "personal_state": snapshot.get("personal_state"),
        "captured_at": record.get("last_seen_at") or "",
        "personal_item": personal,
        "top_items": top_rows[:20],
        "evidence": (record.get("evidence") or [])[-3:],
    }


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=lambda item: str(item.get("captured_at") or ""))[-1]


def _worship_record_key(row: dict[str, Any]) -> tuple[Any, ...]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    return (
        row.get("protocol") or "",
        row.get("faze_id") or "",
        row.get("activity_id") or row.get("rank_type") or "",
        row.get("got_time") or row.get("date") or "",
        row.get("player_id") or row.get("role") or "",
        row.get("friendship") or 0,
        row.get("rank") or "",
        row.get("source_path") or "",
        "" if (row.get("got_time") or row.get("player_id")) else evidence.get("record_id", ""),
    )


def _dedupe_worship_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = _worship_record_key(row)
        old = by_key.get(key)
        if not old or str(row.get("captured_at") or "") >= str(old.get("captured_at") or ""):
            by_key[key] = row
    return sorted(by_key.values(), key=lambda item: (float(item.get("friendship") or 0), str(item.get("captured_at") or "")), reverse=True)


def _player_profile_daily_key(row: dict[str, Any]) -> tuple[str, str]:
    role_id = str(row.get("role_id_text") or row.get("role_id") or "").strip()
    user_key = f"id:{role_id}" if role_id else f"name:{str(row.get('name') or '').strip()}"
    date_key = str(row.get("captured_at") or "")[:10]
    return user_key, date_key


def _player_profile_attack_attr(row: dict[str, Any]) -> dict[str, Any] | None:
    attrs = row.get("combat_attributes")
    if not isinstance(attrs, list):
        return None
    for attr in attrs:
        if isinstance(attr, dict) and _as_int(attr.get("key")) == 2001 and attr.get("value") not in (None, ""):
            return attr
    return None


def _player_profile_has_attack(row: dict[str, Any]) -> bool:
    return _player_profile_attack_attr(row) is not None


def _dedupe_player_profile_daily_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not _player_profile_has_attack(row):
            continue
        key = _player_profile_daily_key(row)
        if not key[0] or not key[1]:
            continue
        old = by_key.get(key)
        if not old or str(row.get("captured_at") or "") >= str(old.get("captured_at") or ""):
            by_key[key] = row
    return sorted(by_key.values(), key=lambda item: str(item.get("captured_at") or ""), reverse=True)


def _attr_rows_to_map(rows: Any) -> dict[int, Any]:
    result: dict[int, Any] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _as_int(row.get("key"))
        if key is None:
            continue
        result[key] = row.get("value")
    return result


def _self_profile_rows_from_attribute_changes(
    attr_rows: list[dict[str, Any]],
    identity: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not identity:
        return []
    role_id = identity.get("role_id")
    name = identity.get("name") or ""
    if not role_id and not name:
        return []
    rows: list[dict[str, Any]] = []
    for attr_row in attr_rows:
        attrs = _attr_rows_to_map(attr_row.get("final_attrs"))
        if 2001 not in attrs:
            continue
        grouped_attrs: dict[str, list[dict[str, Any]]] = {}
        for key, _title, specs in PLAYER_PROFILE_ATTR_GROUPS:
            grouped_attrs[key] = _profile_attr_rows(attrs, specs)
        visible_attrs = [row for group_rows in grouped_attrs.values() for row in group_rows]
        server_fields = _profile_server_fields(identity.get("server"))
        evidence = attr_row.get("evidence") if isinstance(attr_row.get("evidence"), dict) else {}
        evidence = {**evidence, "protocol": attr_row.get("protocol") or evidence.get("protocol") or "SM_RoleChangedAttrs"}
        cultivation_level = identity.get("level")
        rows.append(
            {
                "captured_at": attr_row.get("captured_at") or "",
                "source_kind": "self_attribute_change",
                "source_label": "自身属性变化",
                "role_id": role_id,
                "role_id_text": str(role_id or ""),
                "name": name,
                **server_fields,
                "location": "",
                "level": cultivation_level,
                "cultivation_level": _as_int(cultivation_level),
                "cultivation_level_text": _format_player_cultivation_level(cultivation_level),
                "vip_level": identity.get("vip_level"),
                "battle_score": identity.get("battle_score"),
                "battle_score_text": _format_panel_number(identity.get("battle_score")),
                "attribute_count": len(attrs),
                "decoded_attribute_count": len(attrs),
                "is_truncated": False,
                "special_attributes": grouped_attrs["special_attributes"],
                "immortal_attributes": grouped_attrs["immortal_attributes"],
                "combat_attributes": grouped_attrs["combat_attributes"],
                "attributes": visible_attrs,
                "attribute_values": {str(row["key"]): row["value"] for row in visible_attrs},
                "evidence": evidence,
            }
        )
    return rows


def _identity_from_profile_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    identity = {
        "role_id": row.get("role_id") or row.get("role_id_text"),
        "name": row.get("name") or "",
        "level": row.get("level") or row.get("cultivation_level"),
        "vip_level": row.get("vip_level"),
        "server": row.get("server"),
        "battle_score": row.get("battle_score"),
        "captured_at": row.get("captured_at") or "",
        "evidence": row.get("evidence") if isinstance(row.get("evidence"), dict) else {},
    }
    return identity if _valid_configured_self_profile_identity(identity) else None


def _latest_self_profile_identity_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    candidates: list[dict[str, Any]] = []
    for key in ("latest_login", "latest_identity"):
        value = account.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    identity_candidates = account.get("identity_candidates")
    if isinstance(identity_candidates, list):
        candidates.extend(row for row in identity_candidates if isinstance(row, dict))
    latest_account = _latest_configured_self_profile_identity(candidates, [])
    if latest_account:
        return latest_account

    player_profiles = snapshot.get("player_profiles") if isinstance(snapshot.get("player_profiles"), dict) else {}
    profile_rows = []
    for key in ("daily_records", "records"):
        rows = player_profiles.get(key)
        if isinstance(rows, list):
            profile_rows.extend(row for row in rows if isinstance(row, dict))
    own_rows = [
        row
        for row in profile_rows
        if str(row.get("role_id_text") or row.get("role_id") or "") == FANXIU_STORAGE_BAG_OWNER_ROLE_ID
        or FANXIU_STORAGE_BAG_OWNER_NAME_KEYWORD in str(row.get("name") or "")
    ]
    profile_identities = [identity for row in own_rows for identity in [_identity_from_profile_row(row)] if identity]
    return _latest(profile_identities)


def _latest_self_profile_identity_from_database() -> dict[str, Any] | None:
    try:
        from sqlmodel import Session, select
        from backend.db import engine
        from backend.models import FanxiuPlayerProfileRecord

        with Session(engine) as session:
            rows = session.exec(
                select(FanxiuPlayerProfileRecord)
                .where(FanxiuPlayerProfileRecord.role_id_text == FANXIU_STORAGE_BAG_OWNER_ROLE_ID)
                .order_by(FanxiuPlayerProfileRecord.captured_at.desc(), FanxiuPlayerProfileRecord.created_at.desc())
                .limit(5)
            ).all()
    except Exception:
        return None
    identities = [
        _identity_from_profile_row(
            {
                "role_id": row.role_id,
                "role_id_text": row.role_id_text,
                "name": row.name,
                "server": row.server,
                "region_number": row.region_number,
                "region_name": row.region_name,
                "server_order": row.server_order,
                "server_name": row.server_name,
                "cultivation_level": row.cultivation_level,
                "battle_score": row.battle_score,
                "captured_at": row.captured_at,
                "evidence": row.evidence or {},
            }
        )
        for row in rows
    ]
    return _latest([identity for identity in identities if identity])


def _valid_self_profile_identity(row: dict[str, Any]) -> bool:
    role_id = str(row.get("role_id") or "").strip()
    name = str(row.get("name") or "").strip()
    return bool(name) and len(role_id) >= 12


def _valid_configured_self_profile_identity(row: dict[str, Any]) -> bool:
    if not _valid_self_profile_identity(row):
        return False
    role_id = str(row.get("role_id") or "").strip()
    name = str(row.get("name") or "").strip()
    return role_id == FANXIU_STORAGE_BAG_OWNER_ROLE_ID or FANXIU_STORAGE_BAG_OWNER_NAME_KEYWORD in name


def _latest_self_profile_identity(login_rows: list[dict[str, Any]], self_identity_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    login_candidates = [row for row in login_rows if _valid_self_profile_identity(row)]
    if login_candidates:
        return _latest(login_candidates)
    identity_candidates = [row for row in self_identity_rows if _valid_self_profile_identity(row)]
    if identity_candidates:
        return _latest(identity_candidates)
    return None


def _latest_configured_self_profile_identity(login_rows: list[dict[str, Any]], self_identity_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    login_candidates = [row for row in login_rows if _valid_configured_self_profile_identity(row)]
    if login_candidates:
        return _latest(login_candidates)
    identity_candidates = [row for row in self_identity_rows if _valid_configured_self_profile_identity(row)]
    if identity_candidates:
        return _latest(identity_candidates)
    return None


def _player_profile_rows_from_decoded_source(
    source: dict[str, Any],
    export_root: str | Path | None = None,
    *,
    fallback_self_identity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    decoded_path = Path(str(source.get("decoded_path") or ""))
    data = _load_json(decoded_path, {})
    frames = data.get("frames") if isinstance(data, dict) else None
    if not isinstance(frames, list):
        return []
    decoded_at = str(source.get("created_at") or "").strip()
    if not decoded_at and decoded_path.is_file():
        decoded_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(decoded_path.stat().st_mtime))
    record_id = str(source.get("record_id") or data.get("record_id") or "") if isinstance(data, dict) else str(source.get("record_id") or "")
    pcap_name = str(source.get("pcap_name") or Path(str(data.get("pcap") or decoded_path.name)).name) if isinstance(data, dict) else str(source.get("pcap_name") or decoded_path.name)
    rows: list[dict[str, Any]] = []
    login_rows: list[dict[str, Any]] = []
    self_identity_rows: list[dict[str, Any]] = []
    attr_rows: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            continue
        parsed = frame.get("parsed")
        name = str(frame.get("name") or (parsed.get("_class") if isinstance(parsed, dict) else "") or "")
        if name not in PLAYER_PROFILE_SOURCE_PROTOCOLS:
            continue
        if not isinstance(parsed, dict):
            parsed = {"_class": name, "_parse_error": frame.get("parse_error") or frame.get("decode_error") or ""}
        entry = {
            "id": "|".join(
                [
                    str(record_id or decoded_path),
                    str(frame.get("direction") or ""),
                    str(frame.get("offset") or index),
                    str(frame.get("pro_id") or ""),
                    str(frame.get("sn") or ""),
                ]
            ),
            "decoded_at": decoded_at,
            "record_id": record_id,
            "pcap_name": pcap_name,
            "source_path": str(decoded_path),
            "source_pcap": source.get("source_pcap") or (data.get("pcap") if isinstance(data, dict) else "") or "",
            "stored_pcap": source.get("stored_pcap") or "",
            "stream": int(source.get("stream") or data.get("stream") or 0) if isinstance(data, dict) else int(source.get("stream") or 0),
            "direction": frame.get("direction") or "",
            "name": name,
            "pro_id": int(frame.get("pro_id") or 0),
            "sn": int(frame.get("sn") or 0),
            "frame_index": index,
            "content": parsed,
        }
        if name == "SM_Login":
            row = _extract_login_account(parsed, entry)
            if row:
                login_rows.append(row)
            continue
        if name == "SM_ActivityRankSync":
            row = _extract_self_rank_identity(parsed, entry)
            if row:
                self_identity_rows.append(row)
            continue
        if name in SELF_PROFILE_ATTRIBUTE_PROTOCOLS:
            row = _extract_attr_changes(parsed, entry)
            if row:
                attr_rows.append(row)
            continue
        if name == "SM_ShowOther":
            row = _extract_show_other_profile(parsed, entry, export_root=export_root)
        else:
            row = _extract_sync_player_profile(parsed, entry, export_root=export_root)
        if row:
            rows.append(row)
    safe_fallback_self_identity = (
        fallback_self_identity if isinstance(fallback_self_identity, dict) and _valid_configured_self_profile_identity(fallback_self_identity) else None
    )
    self_identity = _latest_configured_self_profile_identity(login_rows, self_identity_rows) or safe_fallback_self_identity
    rows.extend(_self_profile_rows_from_attribute_changes(attr_rows, self_identity))
    return rows


def _merge_player_profile_rows(snapshot: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    player_profiles = snapshot.get("player_profiles")
    if not isinstance(player_profiles, dict):
        player_profiles = {}
    existing_records = player_profiles.get("records")
    if not isinstance(existing_records, list):
        existing_records = []
    existing_daily = player_profiles.get("daily_records")
    if not isinstance(existing_daily, list):
        existing_daily = []

    known_packet_ids = {
        str(((row.get("evidence") or {}).get("packet_id") if isinstance(row, dict) and isinstance(row.get("evidence"), dict) else "") or "")
        for row in [*existing_records, *existing_daily]
        if isinstance(row, dict)
    }
    new_rows: list[dict[str, Any]] = []
    for row in rows:
        packet_id = str(((row.get("evidence") or {}).get("packet_id") if isinstance(row.get("evidence"), dict) else "") or "")
        if packet_id and packet_id in known_packet_ids:
            continue
        if packet_id:
            known_packet_ids.add(packet_id)
        new_rows.append(row)

    merged_records = sorted(
        [row for row in [*existing_records, *new_rows] if isinstance(row, dict)],
        key=lambda row: str(row.get("captured_at") or ""),
        reverse=True,
    )[:40]
    daily_records = _dedupe_player_profile_daily_rows(
        [row for row in [*existing_daily, *new_rows] if isinstance(row, dict)]
    )
    previous_count = int(player_profiles.get("count") or len(existing_records) or 0)
    player_profiles.update(
        {
            "count": previous_count + len(new_rows),
            "latest": _latest(daily_records),
            "daily_count": len(daily_records),
            "daily_records": daily_records[:120],
            "records": merged_records,
        }
    )
    snapshot["player_profiles"] = player_profiles
    overview = snapshot.get("overview")
    if isinstance(overview, list):
        updated_at = str((player_profiles.get("latest") or {}).get("captured_at") or "")
        found = False
        for item in overview:
            if isinstance(item, dict) and item.get("key") == "player_profile":
                item["count"] = player_profiles["daily_count"]
                item["updated_at"] = updated_at
                found = True
                break
        if not found:
            overview.append({"key": "player_profile", "title": "玩家面板", "count": player_profiles["daily_count"], "updated_at": updated_at})
    return snapshot


def _persist_player_profile_rows_to_database(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"created": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
    try:
        from sqlmodel import Session
        from backend.db import engine

        with Session(engine) as session:
            return upsert_fanxiu_player_profile_rows(session, rows)
    except Exception:
        return {"created": 0, "skipped_invalid": 0, "skipped_duplicate": 0, "error": True}


def _business_record_row(
    domain: str,
    record_key: str,
    payload: dict[str, Any],
    *,
    entity_id: Any = "",
    entity_name: Any = "",
    protocol: str = "",
) -> dict[str, Any]:
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    return {
        "domain": domain,
        "record_key": record_key,
        "protocol": protocol or str(evidence.get("protocol") or payload.get("protocol") or ""),
        "packet_id": str(evidence.get("packet_id") or ""),
        "source_kind": str(evidence.get("source_kind") or ""),
        "entity_id": str(entity_id or ""),
        "entity_name": str(entity_name or ""),
        "captured_at": str(payload.get("captured_at") or evidence.get("captured_at") or ""),
        "payload": payload,
        "evidence": evidence,
    }


def _runtime_business_record_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    for key in ("latest_login", "latest_identity"):
        payload = account.get(key)
        if isinstance(payload, dict):
            role_id = payload.get("role_id") or payload.get("account_id") or key
            rows.append(_business_record_row("account_identity", str(role_id), payload, entity_id=role_id, entity_name=payload.get("name") or "", protocol="SM_Login"))

    wallet = snapshot.get("wallet") if isinstance(snapshot.get("wallet"), dict) else {}
    for resource in wallet.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        payload = dict(resource)
        payload["captured_at"] = wallet.get("captured_at") or ""
        payload["evidence"] = wallet.get("evidence") if isinstance(wallet.get("evidence"), dict) else {}
        record_key = f"{resource.get('type') or ''}|{resource.get('code') or resource.get('id') or ''}"
        rows.append(_business_record_row("wallet_resource", record_key, payload, entity_id=resource.get("code") or resource.get("id"), entity_name=resource.get("name"), protocol="SM_Wallet"))

    bag_records = snapshot.get("bag_records") if isinstance(snapshot.get("bag_records"), list) else []
    if not bag_records and isinstance(snapshot.get("bag"), dict):
        bag_records = [snapshot["bag"]]
    for bag in bag_records:
        if not isinstance(bag, dict):
            continue
        owner_key = _storage_bag_owner_key(bag)
        for item in bag.get("items") or bag.get("notable_items") or []:
            if not isinstance(item, dict):
                continue
            summary = item.get("item") if isinstance(item.get("item"), dict) else {}
            payload = dict(item)
            payload["captured_at"] = bag.get("captured_at") or ""
            payload["owner_role_id"] = bag.get("owner_role_id")
            payload["owner_role_id_text"] = bag.get("owner_role_id_text") or ""
            payload["owner_name"] = bag.get("owner_name") or ""
            payload["owner_account_id"] = bag.get("owner_account_id") or ""
            payload["owner_key"] = owner_key
            payload["evidence"] = bag.get("evidence") if isinstance(bag.get("evidence"), dict) else {}
            record_key = f"{owner_key}|{item.get('instance_id') or ''}|{item.get('base_id') or summary.get('id') or ''}"
            rows.append(_business_record_row("storage_bag_item", record_key, payload, entity_id=item.get("base_id") or summary.get("id"), entity_name=summary.get("name"), protocol="SM_AllBagSyncInfo"))

    equipment = snapshot.get("equipment") if isinstance(snapshot.get("equipment"), dict) else {}
    for item in equipment.get("items") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("item") if isinstance(item.get("item"), dict) else {}
        payload = dict(item)
        payload["captured_at"] = equipment.get("captured_at") or ""
        payload["evidence"] = equipment.get("evidence") if isinstance(equipment.get("evidence"), dict) else {}
        rows.append(_business_record_row("equipment_item", str(item.get("slot") or item.get("item_id") or ""), payload, entity_id=item.get("base_id"), entity_name=summary.get("name"), protocol="SM_SyncAllEquipment"))

    ranks = snapshot.get("activity_ranks") if isinstance(snapshot.get("activity_ranks"), dict) else {}
    for payload in ranks.get("records") or []:
        if not isinstance(payload, dict):
            continue
        key = "|".join(str(payload.get(part) or "") for part in ("activity_id", "rank_type", "group", "rank"))
        rows.append(_business_record_row("activity_rank", key, payload, entity_id=payload.get("activity_id"), entity_name=payload.get("activity_name") or payload.get("name"), protocol="SM_ActivityRankSync"))

    worship = snapshot.get("worship") if isinstance(snapshot.get("worship"), dict) else {}
    for payload in [*(worship.get("records") or []), *(worship.get("rank_records") or [])]:
        if not isinstance(payload, dict):
            continue
        key = "|".join(str(part) for part in _worship_record_key(payload))
        rows.append(_business_record_row("worship_record", key, payload, entity_id=payload.get("player_id") or payload.get("role"), entity_name=payload.get("name") or payload.get("role"), protocol=str(payload.get("protocol") or "")))

    gameplay = snapshot.get("gameplay") if isinstance(snapshot.get("gameplay"), dict) else {}
    energy = gameplay.get("blue_star_energy")
    if isinstance(energy, dict):
        rows.append(_business_record_row("gameplay_state", "blue_star_energy", energy, entity_id="blue_star_energy", entity_name="BlueStarSea", protocol="SM_BlueStarSeaEnergyChange"))
    medicine = gameplay.get("medicine")
    if isinstance(medicine, dict):
        for payload in medicine.get("top_items") or []:
            if not isinstance(payload, dict):
                continue
            payload = dict(payload)
            payload["captured_at"] = medicine.get("captured_at") or ""
            payload["evidence"] = medicine.get("evidence") if isinstance(medicine.get("evidence"), dict) else {}
            rows.append(_business_record_row("medicine_state", str(payload.get("medicine_id") or ""), payload, entity_id=payload.get("medicine_id"), protocol="SM_TakeMedicineSync"))
    for payload in gameplay.get("recent_attribute_changes") or []:
        if isinstance(payload, dict):
            evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
            rows.append(_business_record_row("player_attribute_change", str(evidence.get("packet_id") or payload.get("captured_at") or ""), payload, protocol=str(payload.get("protocol") or "")))
    return [row for row in rows if str(row.get("record_key") or "").strip("|")]


def _runtime_player_profile_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    player_profiles = snapshot.get("player_profiles") if isinstance(snapshot.get("player_profiles"), dict) else {}
    rows: list[dict[str, Any]] = []
    seen_packets: set[str] = set()
    for collection_key in ("daily_records", "records"):
        collection = player_profiles.get(collection_key)
        if not isinstance(collection, list):
            continue
        for row in collection:
            if not isinstance(row, dict):
                continue
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            packet_id = str(evidence.get("packet_id") or "")
            if not packet_id or packet_id in seen_packets:
                continue
            seen_packets.add(packet_id)
            rows.append(row)
    return rows


def _persist_runtime_business_records_to_database(snapshot: dict[str, Any]) -> dict[str, Any]:
    rows = _runtime_business_record_rows(snapshot)
    player_profile_rows = _runtime_player_profile_rows(snapshot)
    empty_player_profile_sync = {"created": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
    if not rows and not player_profile_rows:
        return {
            "created": 0,
            "updated": 0,
            "skipped_invalid": 0,
            "skipped_duplicate": 0,
            "player_profile_database_sync": empty_player_profile_sync,
        }
    try:
        from sqlmodel import Session
        from backend.db import engine

        with Session(engine) as session:
            result = (
                upsert_fanxiu_packet_business_records(session, rows)
                if rows
                else {"created": 0, "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
            )
            result["player_profile_database_sync"] = (
                upsert_fanxiu_player_profile_rows(session, player_profile_rows)
                if player_profile_rows
                else empty_player_profile_sync
            )
            return result
    except Exception:
        return {
            "created": 0,
            "updated": 0,
            "skipped_invalid": 0,
            "skipped_duplicate": 0,
            "player_profile_database_sync": empty_player_profile_sync,
            "error": True,
        }


def sync_fanxiu_packet_player_profiles(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    state_path = _state_path(data_dir)
    snapshot_path = _snapshot_path(data_dir)
    state = _load_json(state_path, {})
    signature = _source_signature(data_dir)
    player_signature = state.get("player_profile_source_signature") if isinstance(state, dict) else None
    if (
        not force
        and snapshot_path.is_file()
        and isinstance(player_signature, dict)
        and player_signature.get("hash") == signature.get("hash")
        and int(state.get("schema_version") or 0) == PACKET_INSIGHT_SCHEMA_VERSION
    ):
        snapshot = _load_json(snapshot_path, {})
        return {
            "ok": True,
            "changed": False,
            "state_path": str(state_path),
            "snapshot_path": str(snapshot_path),
            "source_signature": signature,
            "snapshot": snapshot if isinstance(snapshot, dict) else {},
        }

    snapshot = _load_json(snapshot_path, {})
    if not isinstance(snapshot, dict):
        snapshot = {}
    baseline_signature = player_signature if isinstance(player_signature, dict) else state.get("source_signature") if isinstance(state, dict) and snapshot else {}
    baseline_files = baseline_signature.get("files") if isinstance(baseline_signature, dict) else []
    seen_paths = {
        str(item.get("path") or "")
        for item in baseline_files
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    fallback_self_identity = _latest_self_profile_identity_from_snapshot(snapshot) or _latest_self_profile_identity_from_database()
    for source in _iter_fanxiu_tcp_decoded_sources(data_dir):
        decoded_path = str(source.get("decoded_path") or "")
        if not force and seen_paths and decoded_path in seen_paths:
            continue
        rows.extend(
            _player_profile_rows_from_decoded_source(
                source,
                export_root=export_root,
                fallback_self_identity=fallback_self_identity,
            )
        )

    db_sync = _persist_player_profile_rows_to_database(rows)
    snapshot = _merge_player_profile_rows(snapshot, rows)
    snapshot["player_profile_source_signature"] = {
        "hash": signature.get("hash"),
        "decoded_file_count": signature.get("decoded_file_count"),
    }
    _write_json(snapshot_path, snapshot)
    if not isinstance(state, dict):
        state = {}
    state.update(
        {
            "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
            "updated_at": _now_text(),
            "player_profile_source_signature": signature,
            "snapshot_path": str(snapshot_path),
            "export_root": str(resolve_fanxiu_export_root(export_root)),
        }
    )
    _write_json(state_path, state)
    return {
        "ok": True,
        "changed": bool(rows),
        "state_path": str(state_path),
        "snapshot_path": str(snapshot_path),
        "source_signature": signature,
        "database_sync": db_sync,
        "snapshot": snapshot,
    }


def build_fanxiu_packet_runtime_insights(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    item_index = _load_item_index(export_root=export_root)
    entries = _build_fanxiu_tcp_entries(str(resolve_fanxiu_tcp_store_root(data_dir)), export_root=export_root)
    entries.sort(key=lambda item: (str(item.get("decoded_at") or ""), str(item.get("id") or "")))

    login_rows: list[dict[str, Any]] = []
    self_identity_rows: list[dict[str, Any]] = []
    player_profile_rows: list[dict[str, Any]] = []
    wallet_rows: list[dict[str, Any]] = []
    bag_rows: list[dict[str, Any]] = []
    equipment_rows: list[dict[str, Any]] = []
    energy_rows: list[dict[str, Any]] = []
    medicine_rows: list[dict[str, Any]] = []
    attr_rows: list[dict[str, Any]] = []
    worship_record_rows: list[dict[str, Any]] = []
    worship_packet_rows: list[dict[str, Any]] = []
    protocol_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    current_login_identity: dict[str, Any] | None = None

    for entry in entries:
        parsed = entry.get("content")
        if not isinstance(parsed, dict):
            continue
        name = str(entry.get("name") or parsed.get("_class") or "")
        protocol_counts[name] += 1
        category_counts[str(entry.get("category") or "未归类")] += 1
        if name == "SM_Login":
            row = _extract_login_account(parsed, entry)
            if row:
                login_rows.append(row)
                current_login_identity = row
        if name == "SM_ActivityRankSync":
            row = _extract_self_rank_identity(parsed, entry)
            if row:
                self_identity_rows.append(row)
        if name == "SM_Wallet":
            row = _extract_wallet(parsed, entry, item_index)
            if row:
                wallet_rows.append(row)
        if name == "SM_AllBagSyncInfo":
            row = _extract_bag(parsed, entry, item_index, owner_identity=current_login_identity, export_root=export_root)
            if row:
                bag_rows.append(row)
        if name == "SM_ShowOther":
            row = _extract_show_other_profile(parsed, entry, export_root=export_root)
            if row:
                player_profile_rows.append(row)
        if name == "SM_SyncPlayer":
            row = _extract_sync_player_profile(parsed, entry, export_root=export_root)
            if row:
                player_profile_rows.append(row)
        if name == "SM_SyncAllEquipment":
            row = _extract_equipment(parsed, entry, item_index)
            if row:
                equipment_rows.append(row)
        if name == "SM_BlueStarSeaEnergyChange":
            row = _extract_blue_star_energy(parsed, entry)
            if row:
                energy_rows.append(row)
        if name == "SM_TakeMedicineSync":
            row = _extract_medicine(parsed, entry)
            if row:
                medicine_rows.append(row)
        if name in {"SM_RoleChangedAttrs", "SM_ChangedPlayerAttribute"}:
            row = _extract_attr_changes(parsed, entry)
            if row:
                attr_rows.append(row)
        if "Worship" in name:
            rows = _extract_worship_records(parsed, entry) if name.startswith("SM_") else []
            if rows:
                worship_record_rows.extend(rows)
            worship_packet_rows.append(_worship_packet_observation(parsed, entry, rows))

    rank_payload = get_fanxiu_activity_rank_records(data_dir)
    activity_rank_rows = [
        row
        for record in rank_payload.get("records") or []
        if isinstance(record, dict)
        for row in [_format_rank_record(record)]
        if row
    ]
    personal_rank_rows = [row for row in activity_rank_rows if row.get("personal_item")]
    worship_record_rows = _dedupe_worship_records(worship_record_rows)
    worship_rank_rows = [row for row in worship_record_rows if row.get("protocol") == "SM_WorshipRank"]
    worship_history_rows = [row for row in worship_record_rows if row.get("protocol") != "SM_WorshipRank"]
    self_identity = _latest_self_profile_identity(login_rows, self_identity_rows)
    player_profile_rows.extend(_self_profile_rows_from_attribute_changes(attr_rows, self_identity))
    player_profile_daily_rows = _dedupe_player_profile_daily_rows(player_profile_rows)
    latest_bag_by_owner = _latest_bag_by_owner(bag_rows)
    selected_bag = _select_storage_bag_owner_snapshot(list(latest_bag_by_owner.values()))

    observations = [
        {"key": "account", "title": "账号身份", "count": len(login_rows) + len(self_identity_rows), "updated_at": str((_latest(login_rows + self_identity_rows) or {}).get("captured_at") or "")},
        {"key": "player_profile", "title": "玩家面板", "count": len(player_profile_daily_rows), "updated_at": str((_latest(player_profile_daily_rows) or {}).get("captured_at") or "")},
        {"key": "wallet", "title": "钱包资源", "count": len((_latest(wallet_rows) or {}).get("resources") or []), "updated_at": str((_latest(wallet_rows) or {}).get("captured_at") or "")},
        {"key": "bag", "title": "背包物品", "count": int((selected_bag or {}).get("stack_count") or 0), "updated_at": str((selected_bag or {}).get("captured_at") or "")},
        {"key": "activity_rank", "title": "活动排行", "count": len(activity_rank_rows), "updated_at": str((_latest(activity_rank_rows) or {}).get("captured_at") or "")},
        {"key": "worship", "title": "拜谒榜单", "count": len(worship_history_rows), "updated_at": str((_latest(worship_history_rows) or _latest(worship_packet_rows) or {}).get("captured_at") or "")},
        {"key": "gameplay_state", "title": "玩法状态", "count": len(energy_rows) + len(medicine_rows) + len(equipment_rows), "updated_at": str((_latest(energy_rows + medicine_rows + equipment_rows) or {}).get("captured_at") or "")},
    ]

    return {
        "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
        "updated_at": _now_text(),
        "source": "packet_history",
        "source_summary": {
            "tcp_store_root": str(resolve_fanxiu_tcp_store_root(data_dir)),
            "entry_count": len(entries),
            "protocol_counts": [{"name": key, "count": value} for key, value in protocol_counts.most_common(40)],
            "category_counts": [{"name": key, "count": value} for key, value in category_counts.most_common(20)],
        },
        "account": {
            "latest_login": _latest(login_rows),
            "latest_identity": _latest(self_identity_rows) or _latest(login_rows),
            "identity_candidates": (login_rows + self_identity_rows)[-20:],
        },
        "player_profiles": {
            "count": len(player_profile_rows),
            "latest": _latest(player_profile_daily_rows),
            "daily_count": len(player_profile_daily_rows),
            "daily_records": player_profile_daily_rows[:120],
            "records": sorted(player_profile_rows, key=lambda row: str(row.get("captured_at") or ""), reverse=True)[:40],
        },
        "wallet": _latest(wallet_rows),
        "bag": selected_bag,
        "bag_records": sorted(latest_bag_by_owner.values(), key=lambda row: str(row.get("captured_at") or ""), reverse=True)[:20],
        "bag_by_owner": latest_bag_by_owner,
        "equipment": _latest(equipment_rows),
        "activity_ranks": {
            "count": len(activity_rank_rows),
            "personal_count": len(personal_rank_rows),
            "records": sorted(activity_rank_rows, key=lambda row: str(row.get("captured_at") or ""), reverse=True),
            "personal_records": sorted(personal_rank_rows, key=lambda row: str(row.get("captured_at") or ""), reverse=True),
        },
        "worship": {
            "count": len(worship_history_rows),
            "packet_count": len(worship_packet_rows),
            "records": sorted(worship_history_rows, key=lambda row: (float(row.get("friendship") or 0), str(row.get("captured_at") or "")), reverse=True)[:120],
            "rank_records": sorted(worship_rank_rows, key=lambda row: (float(row.get("rank") or 999999), -float(row.get("friendship") or 0)))[:120],
            "packets": sorted(worship_packet_rows, key=lambda row: str(row.get("captured_at") or ""), reverse=True)[:40],
        },
        "gameplay": {
            "blue_star_energy": _latest(energy_rows),
            "medicine": _latest(medicine_rows),
            "recent_attribute_changes": attr_rows[-24:],
        },
        "observations": observations,
    }


def sync_fanxiu_packet_runtime_insights(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    state = _load_json(_state_path(data_dir), {})
    signature = _source_signature(data_dir)
    snapshot_path = _snapshot_path(data_dir)
    initial_build = not snapshot_path.is_file()
    if (
        not force
        and snapshot_path.is_file()
        and state.get("source_signature", {}).get("hash") == signature.get("hash")
        and int(state.get("schema_version") or 0) == PACKET_INSIGHT_SCHEMA_VERSION
    ):
        snapshot = _load_json(snapshot_path, {})
        return {
            "ok": True,
            "changed": False,
            "state_path": str(_state_path(data_dir)),
            "snapshot_path": str(snapshot_path),
            "source_signature": signature,
            "snapshot": snapshot if isinstance(snapshot, dict) else {},
        }

    sync_fanxiu_activity_packets(data_dir=data_dir, export_root=export_root, force=force or initial_build)
    snapshot = build_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root)
    snapshot["source_signature"] = {"hash": signature.get("hash"), "decoded_file_count": signature.get("decoded_file_count")}
    business_db_sync = _persist_runtime_business_records_to_database(snapshot)
    snapshot["business_db_sync"] = business_db_sync
    _write_json(snapshot_path, snapshot)
    _write_json(
        _state_path(data_dir),
        {
            "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
            "updated_at": _now_text(),
            "source_signature": signature,
            "snapshot_path": str(snapshot_path),
            "export_root": str(resolve_fanxiu_export_root(export_root)),
            "business_db_sync": business_db_sync,
        },
    )
    return {
        "ok": True,
        "changed": True,
        "state_path": str(_state_path(data_dir)),
        "snapshot_path": str(snapshot_path),
        "source_signature": signature,
        "business_db_sync": business_db_sync,
        "snapshot": snapshot,
    }


def sync_fanxiu_packet_runtime_insights_for_decode_result(
    result: dict[str, Any],
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Refresh account runtime insights when a newly decoded pcap contains relevant packets."""
    frames = result.get("frames") if isinstance(result, dict) else None
    if not isinstance(frames, list):
        return None
    names = {
        str(frame.get("name") or "")
        for frame in frames
        if isinstance(frame, dict)
    }
    activity_names = names.intersection(ACTIVITY_PACKET_PROTOCOLS)
    activity_sync = None
    if activity_names:
        activity_sync = sync_fanxiu_activity_packets(data_dir=data_dir, export_root=export_root, force=False)
    if not names.intersection(PACKET_RUNTIME_INSIGHT_PROTOCOLS):
        if activity_sync is not None:
            return {
                "ok": True,
                "changed": bool(
                    activity_sync.get("inserted")
                    or activity_sync.get("updated")
                    or activity_sync.get("rank_inserted")
                    or activity_sync.get("rank_updated")
                ),
                "activity_packet_sync": activity_sync,
            }
        return None
    profile_names = names.intersection(PLAYER_PROFILE_SOURCE_PROTOCOLS)
    non_profile_names = names.intersection(PACKET_RUNTIME_INSIGHT_PROTOCOLS - PLAYER_PROFILE_SOURCE_PROTOCOLS)
    if profile_names:
        decoded_path_text = str(result.get("stored_decoded_path") or result.get("output_path") or "")
        if not decoded_path_text:
            if non_profile_names:
                return sync_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root, force=force)
            return sync_fanxiu_packet_player_profiles(data_dir=data_dir, export_root=export_root, force=force)
        decoded_path = Path(decoded_path_text)
        meta = _load_json(Path(str(result.get("meta_path") or "")), {}) if result.get("meta_path") else {}
        source = {
            "decoded_path": decoded_path,
            "record_id": result.get("record_id") or meta.get("record_id") or "",
            "pcap_name": result.get("pcap_name") or meta.get("pcap_name") or Path(str(result.get("pcap") or decoded_path.name)).name,
            "created_at": meta.get("created_at") or _now_text(),
            "source_kind": "record",
            "source_pcap": result.get("source_pcap") or meta.get("source_pcap") or result.get("pcap") or "",
            "stored_pcap": result.get("stored_pcap") or meta.get("stored_pcap") or "",
            "stream": int(result.get("stream") or meta.get("stream") or 0),
        }
        snapshot = _load_json(_snapshot_path(data_dir), {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        fallback_self_identity = _latest_self_profile_identity_from_snapshot(snapshot) or _latest_self_profile_identity_from_database()
        rows = _player_profile_rows_from_decoded_source(
            source,
            export_root=export_root,
            fallback_self_identity=fallback_self_identity,
        )
        db_sync = _persist_player_profile_rows_to_database(rows)
        snapshot = _merge_player_profile_rows(snapshot, rows)
        signature = _source_signature(data_dir)
        snapshot["player_profile_source_signature"] = {
            "hash": signature.get("hash"),
            "decoded_file_count": signature.get("decoded_file_count"),
        }
        _write_json(_snapshot_path(data_dir), snapshot)
        state = _load_json(_state_path(data_dir), {})
        if not isinstance(state, dict):
            state = {}
        state.update(
            {
                "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
                "updated_at": _now_text(),
                "player_profile_source_signature": signature,
                "snapshot_path": str(_snapshot_path(data_dir)),
                "export_root": str(resolve_fanxiu_export_root(export_root)),
            }
        )
        _write_json(_state_path(data_dir), state)
        if non_profile_names:
            full_sync = sync_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root, force=force)
            full_sync["player_profile_database_sync"] = db_sync
            return full_sync
        return {
            "ok": True,
            "changed": bool(rows),
            "state_path": str(_state_path(data_dir)),
            "snapshot_path": str(_snapshot_path(data_dir)),
            "database_sync": db_sync,
            "source_signature": signature,
            "snapshot": snapshot,
        }
    return sync_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root, force=force)


def get_fanxiu_packet_runtime_insights(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    sync: bool = True,
) -> dict[str, Any]:
    if sync:
        return sync_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root, force=False)
    state = _load_json(_state_path(data_dir), {})
    snapshot = _load_json(_snapshot_path(data_dir), {})
    if isinstance(snapshot, dict) and snapshot:
        return {
            "ok": True,
            "changed": False,
            "stale": int(state.get("schema_version") or 0) != PACKET_INSIGHT_SCHEMA_VERSION,
            "state_schema_version": int(state.get("schema_version") or 0),
            "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
            "state_path": str(_state_path(data_dir)),
            "snapshot_path": str(_snapshot_path(data_dir)),
            "snapshot": snapshot,
        }
    return {
        "ok": True,
        "changed": False,
        "stale": True,
        "state_schema_version": int(state.get("schema_version") or 0),
        "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
        "state_path": str(_state_path(data_dir)),
        "snapshot_path": str(_snapshot_path(data_dir)),
        "snapshot": {},
    }


def get_fanxiu_packet_storage_bag_snapshot(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    sync: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    state: dict[str, Any] = {}
    if sync:
        payload = sync_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root, force=False)
        snapshot = payload.get("snapshot") if isinstance(payload, dict) else {}
        changed = bool(payload.get("changed")) if isinstance(payload, dict) else False
        state_schema_version = int(payload.get("schema_version") or 0) if isinstance(payload, dict) else 0
    else:
        raw_state = _load_json(_state_path(data_dir), {})
        state = raw_state if isinstance(raw_state, dict) else {}
        snapshot = _load_json(_snapshot_path(data_dir), {})
        changed = False
        state_schema_version = int(state.get("schema_version") or 0)
    bag = snapshot.get("bag") if isinstance(snapshot, dict) else {}
    worship = snapshot.get("worship") if isinstance(snapshot, dict) else {}
    source_summary = snapshot.get("source_summary") if isinstance(snapshot, dict) and isinstance(snapshot.get("source_summary"), dict) else {}
    return {
        "ok": True,
        "changed": changed,
        "stale": state_schema_version != PACKET_INSIGHT_SCHEMA_VERSION,
        "state_schema_version": state_schema_version,
        "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
        "state_path": str(_state_path(data_dir)),
        "snapshot_path": str(_snapshot_path(data_dir)),
        "bag": bag if isinstance(bag, dict) else None,
        "worship": worship if isinstance(worship, dict) else None,
        "source_signature": (snapshot.get("source_signature") if isinstance(snapshot, dict) else None) or source_summary.get("source_signature") or {},
    }


def decode_and_sync_fanxiu_runtime_capture(
    pcap_path: str | Path,
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    server_host: str = DEFAULT_FANXIU_SERVER_HOST,
    max_streams: int = 8,
    sync_business: bool = True,
    sync_runtime_insights: bool = True,
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
    decoded_sources: list[dict[str, Any]] = []
    runtime_protocol_count = 0
    worship_protocol_count = 0
    for stream in stream_ids:
        result = decode_fanxiu_tcp_pcap(
            path,
            stream=stream,
            server_host=server_host,
            export_root=export_root,
            persist=True,
            data_dir=data_dir,
            sync_after_decode=False,
        )
        frames = result.get("frames") or []
        runtime_count = sum(
            1
            for frame in frames
            if isinstance(frame, dict) and str(frame.get("name") or "") in PACKET_RUNTIME_INSIGHT_PROTOCOLS
        )
        worship_count = sum(
            1
            for frame in frames
            if isinstance(frame, dict) and "Worship" in str(frame.get("name") or "")
        )
        runtime_protocol_count += runtime_count
        worship_protocol_count += worship_count
        decoded.append(
            {
                "stream": stream,
                "output_path": result.get("output_path") or "",
                "record_id": result.get("record_id") or "",
                "runtime_protocol_count": runtime_count,
                "worship_protocol_count": worship_count,
            }
        )
        output_path = str(result.get("output_path") or "")
        if output_path:
            decoded_sources.append(
                {
                    "decoded_path": Path(output_path),
                    "record_id": result.get("record_id") or "",
                    "pcap_name": path.name,
                    "created_at": "",
                    "source_kind": "runtime",
                    "source_pcap": str(path),
                    "stored_pcap": "",
                    "stream": int(stream),
                }
            )

    sync: dict[str, Any] = {}
    mail_sync: dict[str, Any] = {}
    if sync_business:
        if sync_runtime_insights:
            sync = sync_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root, force=False)
        try:
            from sqlmodel import Session

            from backend.core.fanxiu_mail_packet_sync import sync_fanxiu_mail_packets
            from backend.db import engine

            with Session(engine) as session:
                mail_sync = sync_fanxiu_mail_packets(
                    session,
                    data_dir=data_dir,
                    export_root=export_root,
                    clear_existing=False,
                    decoded_sources=decoded_sources,
                )
        except Exception as exc:
            mail_sync = {"ok": False, "error": str(exc)}
    snapshot = sync.get("snapshot") if isinstance(sync, dict) else {}
    worship = snapshot.get("worship") if isinstance(snapshot, dict) else {}
    bag = snapshot.get("bag") if isinstance(snapshot, dict) else {}
    return {
        "ok": True,
        "pcap_path": str(path),
        "server_host": server_host,
        "stream_count": len(stream_rows),
        "decoded_count": len(decoded),
        "decoded": decoded,
        "runtime_protocol_count": runtime_protocol_count,
        "worship_protocol_count": worship_protocol_count,
        "packet_runtime_sync": {
            "changed": bool(sync.get("changed")) if isinstance(sync, dict) else False,
            "snapshot_path": str(sync.get("snapshot_path") or "") if isinstance(sync, dict) else "",
            "worship_record_count": int((worship or {}).get("count") or 0) if isinstance(worship, dict) else 0,
            "worship_packet_count": int((worship or {}).get("packet_count") or 0) if isinstance(worship, dict) else 0,
            "bag_stack_count": int((bag or {}).get("stack_count") or 0) if isinstance(bag, dict) else 0,
        },
        "mail_packet_sync": mail_sync,
    }
