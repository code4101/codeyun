from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyxllib.file.packetstream import LuaPacketSchemaIndex, extract_tcp_stream_payloads_with_tshark

from backend.core.fanxiu_activity_packet_sync import get_fanxiu_activity_rank_records, sync_fanxiu_activity_packets
from backend.core.fanxiu_item_catalog import load_fanxiu_item_runtime_index
from backend.core.fanxiu_resources import resolve_fanxiu_export_root
from backend.core.fanxiu_tcp_flow import (
    DEFAULT_FANXIU_SERVER_HOST,
    DEFAULT_TEXT_ASSETS,
    _build_fanxiu_tcp_entries,
    _decode_lusuo_frames_tolerant,
    _iter_fanxiu_tcp_decoded_sources,
    decode_fanxiu_tcp_pcap,
    list_tcp_streams_with_tshark,
    resolve_fanxiu_tcp_store_root,
)
from backend.core.settings import get_settings

PACKET_INSIGHT_SCHEMA_VERSION = 11

PACKET_RUNTIME_INSIGHT_PROTOCOLS = {
    "SM_Login",
    "SM_ActivityRankSync",
    "SM_Wallet",
    "SM_AllBagSyncInfo",
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


def _full_bag_parsed_from_packet(entry: dict[str, Any], export_root: str | Path | None = None) -> dict[str, Any] | None:
    pcap_text = str(entry.get("stored_pcap") or entry.get("source_pcap") or "").strip()
    if not pcap_text:
        return None
    pcap_path = Path(pcap_text).expanduser()
    if not pcap_path.is_file():
        return None
    try:
        schema = LuaPacketSchemaIndex(_resolve_export_child(export_root, DEFAULT_TEXT_ASSETS))
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
        if frame.get("name") == "SM_AllBagSyncInfo"
        and isinstance(frame.get("parsed"), dict)
        and (target_sn is None or _as_int(frame.get("sn")) == target_sn)
        and (target_pro_id is None or _as_int(frame.get("pro_id")) == target_pro_id)
    ]
    if not candidates:
        candidates = [frame for frame in frames if frame.get("name") == "SM_AllBagSyncInfo" and isinstance(frame.get("parsed"), dict)]
    return candidates[-1].get("parsed") if candidates else None


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
    return {
        "account_id": role.get("accountId") or "",
        "role_id": role.get("roleId") or role.get("id") or _super(role).get("id"),
        "name": role.get("name") or role.get("playerName") or "",
        "level": role.get("level"),
        "vip_level": role.get("vipLevel"),
        "server": role.get("server") or role.get("realServer"),
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


def _extract_bag(
    parsed: dict[str, Any],
    entry: dict[str, Any],
    item_index: dict[str, Any],
    *,
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
        if name == "SM_ActivityRankSync":
            row = _extract_self_rank_identity(parsed, entry)
            if row:
                self_identity_rows.append(row)
        if name == "SM_Wallet":
            row = _extract_wallet(parsed, entry, item_index)
            if row:
                wallet_rows.append(row)
        if name == "SM_AllBagSyncInfo":
            row = _extract_bag(parsed, entry, item_index, export_root=export_root)
            if row:
                bag_rows.append(row)
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

    observations = [
        {"key": "account", "title": "账号身份", "count": len(login_rows) + len(self_identity_rows), "updated_at": str((_latest(login_rows + self_identity_rows) or {}).get("captured_at") or "")},
        {"key": "wallet", "title": "钱包资源", "count": len((_latest(wallet_rows) or {}).get("resources") or []), "updated_at": str((_latest(wallet_rows) or {}).get("captured_at") or "")},
        {"key": "bag", "title": "背包物品", "count": int((_latest(bag_rows) or {}).get("stack_count") or 0), "updated_at": str((_latest(bag_rows) or {}).get("captured_at") or "")},
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
        "wallet": _latest(wallet_rows),
        "bag": _latest(bag_rows),
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
    _write_json(snapshot_path, snapshot)
    _write_json(
        _state_path(data_dir),
        {
            "schema_version": PACKET_INSIGHT_SCHEMA_VERSION,
            "updated_at": _now_text(),
            "source_signature": signature,
            "snapshot_path": str(snapshot_path),
            "export_root": str(resolve_fanxiu_export_root(export_root)),
        },
    )
    return {
        "ok": True,
        "changed": True,
        "state_path": str(_state_path(data_dir)),
        "snapshot_path": str(snapshot_path),
        "source_signature": signature,
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
    if not names.intersection(PACKET_RUNTIME_INSIGHT_PROTOCOLS):
        return None
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

    sync = sync_fanxiu_packet_runtime_insights(data_dir=data_dir, export_root=export_root, force=False)
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
    }
