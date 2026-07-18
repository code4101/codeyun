from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings


SERVER_RELATION_CONFIG_VERSION = 1
SERVER_RELATION_ORDERING = "protection_desc"
_FRIENDLY_NODES = (
    ("same_server", "本服"),
    ("alliance", "联盟"),
    ("ally", "盟友"),
)
_NON_FRIENDLY_NODES = (
    ("other_server", "其他区服"),
    ("npc", "NPC"),
)


def get_fanxiu_server_relation_config_path(data_dir: str | Path | None = None) -> Path:
    base = Path(data_dir).expanduser().resolve() if data_dir else get_settings().data_dir
    return base / "fanxiu" / "server_relations.json"


def load_fanxiu_server_relations(data_dir: str | Path | None = None) -> dict[str, Any]:
    """Load the latest relation policy from disk without process-level caching."""

    path = get_fanxiu_server_relation_config_path(data_dir)
    if not path.exists():
        return _normalize_server_relation_config({})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"读取凡修区服关系配置失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"凡修区服关系配置不是有效 JSON：{path}") from exc
    return _normalize_server_relation_config(payload)


def save_fanxiu_server_relations(
    payload: dict[str, Any],
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized = _normalize_server_relation_config(payload)
    path = get_fanxiu_server_relation_config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending_path = path.with_suffix(".json.tmp")
    pending_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pending_path.replace(path)
    return normalized


def classify_fanxiu_target_relation(
    *,
    is_npc: bool,
    server_id: int | str | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Classify one target using a freshly loaded shared relation policy."""

    config = load_fanxiu_server_relations(data_dir)
    if is_npc:
        return _relation_result("non_friendly", "非友军", "npc", "NPC", 4)

    normalized_server_id = _optional_int(server_id)
    friendly = config["groups"][0]
    for relation_order, node in enumerate(friendly["children"]):
        for server_priority, server in enumerate(node.get("servers", [])):
            if server["server_id"] == normalized_server_id:
                return _relation_result(
                    "friendly",
                    "友军",
                    node["key"],
                    node["label"],
                    relation_order,
                    server_priority=server_priority,
                    server=server,
                )
    return _relation_result("non_friendly", "非友军", "other_server", "其他区服", 3)


def _relation_result(
    camp: str,
    camp_label: str,
    relation: str,
    relation_label: str,
    relation_order: int,
    *,
    server_priority: int | None = None,
    server: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "camp": camp,
        "camp_label": camp_label,
        "relation": relation,
        "relation_label": relation_label,
        "path": [camp_label, relation_label],
        "relation_order": relation_order,
        "server_priority": server_priority,
        "server": server,
    }


def _normalize_server_relation_config(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("凡修区服关系配置根节点必须是对象。")
    raw_groups = raw.get("groups") if isinstance(raw.get("groups"), list) else []
    raw_group_by_key = {
        str(group.get("key") or ""): group
        for group in raw_groups
        if isinstance(group, dict)
    }
    friendly_source = raw_group_by_key.get("friendly", {})
    friendly_children = friendly_source.get("children") if isinstance(friendly_source.get("children"), list) else []
    raw_node_by_key = {
        str(node.get("key") or ""): node
        for node in friendly_children
        if isinstance(node, dict)
    }

    seen_server_ids: set[int] = set()
    normalized_friendly_children: list[dict[str, Any]] = []
    for key, label in _FRIENDLY_NODES:
        raw_node = raw_node_by_key.get(key, {})
        raw_servers = raw_node.get("servers") if isinstance(raw_node.get("servers"), list) else []
        servers: list[dict[str, Any]] = []
        for raw_server in raw_servers:
            server = _normalize_server(raw_server)
            if server["server_id"] in seen_server_ids:
                raise ValueError(f"区服 {server['server_id']} 不能同时属于多个友军关系。")
            seen_server_ids.add(server["server_id"])
            servers.append(server)
        normalized_friendly_children.append({"key": key, "label": label, "servers": servers})

    return {
        "version": SERVER_RELATION_CONFIG_VERSION,
        "ordering": SERVER_RELATION_ORDERING,
        "groups": [
            {
                "key": "friendly",
                "label": "友军",
                "children": normalized_friendly_children,
            },
            {
                "key": "non_friendly",
                "label": "非友军",
                "children": [
                    {"key": key, "label": label}
                    for key, label in _NON_FRIENDLY_NODES
                ],
            },
        ],
    }


def _normalize_server(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("区服关系条目必须是对象。")
    server_id = _optional_int(raw.get("server_id"))
    server_order = _optional_int(raw.get("server_order"))
    server_name = str(raw.get("server_name") or "").strip()
    if server_id is None or server_order is None or not server_name:
        raise ValueError("区服关系条目必须包含 server_id、server_order 和 server_name。")
    return {
        "server_id": server_id,
        "server_order": server_order,
        "server_name": server_name,
    }


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None
