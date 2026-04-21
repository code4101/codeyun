from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from sqlmodel import Session

from backend.plugins.discovery import iter_plugin_permission_registry_files
from backend.core.settings import ROOT_DIR
from backend.models import FeatureAccessPolicy, User


FeatureAccessDecision = Literal["inherit", "allow", "deny"]
FeatureAccessNodeType = Literal["group", "feature"]
FeatureAccessSource = Literal[
    "default_allow",
    "default_deny",
    "explicit_allow",
    "explicit_deny",
    "inherit_anonymous",
    "ancestor_denied",
    "superuser",
]

FEATURE_ACCESS_SUBJECT_ANONYMOUS = "anonymous"
FEATURE_ACCESS_SUBJECT_USER = "user"
FEATURE_ACCESS_ANONYMOUS_SUBJECT_KEY = "anonymous"
FEATURE_ACCESS_REGISTRY_PATH = (
    ROOT_DIR
    / "frontend"
    / "src"
    / "features"
    / "access"
    / "permissionRegistry.json"
)
_feature_access_registry_cache_lock = threading.Lock()
_feature_access_registry_cache: "FeatureAccessRegistry | None" = None
_feature_access_registry_cache_signature: tuple[tuple[str, int, int], ...] | None = None


@dataclass(frozen=True)
class FeatureAccessRegistryNode:
    key: str
    title: str
    node_type: FeatureAccessNodeType
    parent_key: str | None
    sort_order: int
    route_paths: tuple[str, ...]
    menu_paths: tuple[str, ...]
    api_scopes: tuple[str, ...]
    default_anonymous_allow: bool


@dataclass(frozen=True)
class FeatureAccessRegistry:
    version: int
    node_map: dict[str, FeatureAccessRegistryNode]
    root_keys: tuple[str, ...]
    children_map: dict[str | None, tuple[str, ...]]


def _sort_registry_nodes(
    keys: list[str],
    node_map: dict[str, FeatureAccessRegistryNode],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            keys,
            key=lambda item: (
                node_map[item].sort_order,
                node_map[item].title,
                item,
            ),
        )
    )


def _read_feature_access_registry_payload() -> dict[str, Any]:
    with FEATURE_ACCESS_REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise RuntimeError("权限注册表格式错误")

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise RuntimeError("权限注册表缺少节点定义")

    merged_nodes = list(raw_nodes)
    for registry_file in iter_plugin_permission_registry_files():
        with registry_file.open("r", encoding="utf-8") as handle:
            plugin_payload = json.load(handle)

        if isinstance(plugin_payload, dict):
            plugin_nodes = plugin_payload.get("nodes", [])
        elif isinstance(plugin_payload, list):
            plugin_nodes = plugin_payload
        else:
            raise RuntimeError(f"插件权限注册表格式错误：{registry_file}")

        if not isinstance(plugin_nodes, list):
            raise RuntimeError(f"插件权限注册表节点格式错误：{registry_file}")

        merged_nodes.extend(plugin_nodes)

    return {
        **payload,
        "nodes": merged_nodes,
    }


def _get_feature_access_registry_signature() -> tuple[tuple[str, int, int], ...]:
    files = (FEATURE_ACCESS_REGISTRY_PATH, *iter_plugin_permission_registry_files())
    signatures: list[tuple[str, int, int]] = []
    for file_path in files:
        stat = file_path.stat()
        signatures.append((str(file_path), stat.st_mtime_ns, stat.st_size))
    return tuple(signatures)


def clear_feature_access_registry_cache() -> None:
    global _feature_access_registry_cache, _feature_access_registry_cache_signature
    with _feature_access_registry_cache_lock:
        _feature_access_registry_cache = None
        _feature_access_registry_cache_signature = None


def load_feature_access_registry() -> FeatureAccessRegistry:
    global _feature_access_registry_cache, _feature_access_registry_cache_signature
    signature = _get_feature_access_registry_signature()
    cached_registry = _feature_access_registry_cache
    if cached_registry is not None and _feature_access_registry_cache_signature == signature:
        return cached_registry

    with _feature_access_registry_cache_lock:
        signature = _get_feature_access_registry_signature()
        cached_registry = _feature_access_registry_cache
        if cached_registry is not None and _feature_access_registry_cache_signature == signature:
            return cached_registry

        payload = _read_feature_access_registry_payload()
        if not isinstance(payload, dict):
            raise RuntimeError("权限注册表格式错误")

        raw_version = payload.get("version")
        if not isinstance(raw_version, int) or raw_version <= 0:
            raise RuntimeError("权限注册表缺少合法版本号")

        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise RuntimeError("权限注册表缺少节点定义")

        node_map: dict[str, FeatureAccessRegistryNode] = {}
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                raise RuntimeError("权限注册表节点格式错误")

            key = str(raw_node.get("key") or "").strip()
            title = str(raw_node.get("title") or "").strip()
            node_type = str(raw_node.get("node_type") or "").strip()
            parent_key = str(raw_node.get("parent_key") or "").strip() or None
            sort_order = raw_node.get("sort_order", 0)

            if not key or not title:
                raise RuntimeError("权限注册表节点缺少 key 或 title")
            if node_type not in {"group", "feature"}:
                raise RuntimeError(f"权限注册表节点 {key} 的 node_type 非法")
            if key in node_map:
                raise RuntimeError(f"权限注册表节点 key 重复：{key}")
            if not isinstance(sort_order, int):
                raise RuntimeError(f"权限注册表节点 {key} 的 sort_order 非法")

            def _normalize_string_list(value: Any, field_name: str) -> tuple[str, ...]:
                if value is None:
                    return ()
                if not isinstance(value, list):
                    raise RuntimeError(f"权限注册表节点 {key} 的 {field_name} 必须为数组")
                normalized_items: list[str] = []
                for item in value:
                    if not isinstance(item, str) or not item.strip():
                        raise RuntimeError(f"权限注册表节点 {key} 的 {field_name} 含非法项")
                    normalized_items.append(item.strip())
                return tuple(normalized_items)

            default_anonymous_allow = bool(raw_node.get("default_anonymous_allow", False))
            node_map[key] = FeatureAccessRegistryNode(
                key=key,
                title=title,
                node_type=node_type,  # type: ignore[arg-type]
                parent_key=parent_key,
                sort_order=sort_order,
                route_paths=_normalize_string_list(raw_node.get("route_paths"), "route_paths"),
                menu_paths=_normalize_string_list(raw_node.get("menu_paths"), "menu_paths"),
                api_scopes=_normalize_string_list(raw_node.get("api_scopes"), "api_scopes"),
                default_anonymous_allow=default_anonymous_allow,
            )

        children_map: dict[str | None, list[str]] = {}
        for key, node in node_map.items():
            if node.parent_key and node.parent_key not in node_map:
                raise RuntimeError(f"权限注册表节点 {key} 的父节点不存在：{node.parent_key}")
            children_map.setdefault(node.parent_key, []).append(key)

        registry = FeatureAccessRegistry(
            version=raw_version,
            node_map=node_map,
            root_keys=_sort_registry_nodes(children_map.get(None, []), node_map),
            children_map={
                parent_key: _sort_registry_nodes(child_keys, node_map)
                for parent_key, child_keys in children_map.items()
            },
        )
        _feature_access_registry_cache = registry
        _feature_access_registry_cache_signature = signature
        return registry


def build_feature_access_subject_key(
    subject_type: str,
    subject_user_id: int | None = None,
) -> str:
    normalized_type = (subject_type or "").strip().lower()
    if normalized_type == FEATURE_ACCESS_SUBJECT_ANONYMOUS:
        return FEATURE_ACCESS_ANONYMOUS_SUBJECT_KEY
    if normalized_type == FEATURE_ACCESS_SUBJECT_USER and subject_user_id is not None:
        return f"user:{int(subject_user_id)}"
    raise ValueError("非法权限主体")


def _normalize_feature_access_decision(value: Any) -> FeatureAccessDecision | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"inherit", "allow", "deny"}:
        return normalized  # type: ignore[return-value]
    return None


def normalize_feature_access_overrides(
    raw_overrides: Any,
    registry: FeatureAccessRegistry | None = None,
    *,
    keep_inherit: bool = False,
) -> dict[str, FeatureAccessDecision]:
    effective_registry = registry or load_feature_access_registry()
    if not isinstance(raw_overrides, dict):
        return {}

    normalized: dict[str, FeatureAccessDecision] = {}
    for raw_key, raw_value in raw_overrides.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if key not in effective_registry.node_map:
            continue
        decision = _normalize_feature_access_decision(raw_value)
        if decision is None:
            continue
        if decision == "inherit" and not keep_inherit:
            continue
        normalized[key] = decision
    return normalized


def _expand_required_ancestor_allows(
    overrides: dict[str, FeatureAccessDecision],
    registry: FeatureAccessRegistry,
) -> dict[str, FeatureAccessDecision]:
    expanded = dict(overrides)
    for key, decision in list(overrides.items()):
        if decision != "allow":
            continue
        current_key = registry.node_map[key].parent_key
        while current_key:
            if expanded.get(current_key) == "allow":
                break
            expanded[current_key] = "allow"
            current_key = registry.node_map[current_key].parent_key
    return expanded


def _iter_descendant_keys(
    key: str,
    registry: FeatureAccessRegistry,
) -> tuple[str, ...]:
    descendant_keys: list[str] = []
    pending_keys = list(registry.children_map.get(key, ()))
    while pending_keys:
        current_key = pending_keys.pop()
        descendant_keys.append(current_key)
        pending_keys.extend(registry.children_map.get(current_key, ()))
    return tuple(descendant_keys)


def _cascade_required_descendant_denies(
    overrides: dict[str, FeatureAccessDecision],
    registry: FeatureAccessRegistry,
) -> dict[str, FeatureAccessDecision]:
    cascaded = dict(overrides)
    for key, decision in list(overrides.items()):
        if decision != "deny":
            continue
        for descendant_key in _iter_descendant_keys(key, registry):
            cascaded[descendant_key] = "deny"
    return cascaded


def build_default_anonymous_overrides(
    registry: FeatureAccessRegistry | None = None,
) -> dict[str, FeatureAccessDecision]:
    effective_registry = registry or load_feature_access_registry()
    return {
        key: "allow"
        for key, node in effective_registry.node_map.items()
        if node.default_anonymous_allow
    }


def _get_policy_row(
    session: Session,
    *,
    subject_type: str,
    subject_user_id: int | None = None,
) -> FeatureAccessPolicy | None:
    return session.get(
        FeatureAccessPolicy,
        build_feature_access_subject_key(subject_type, subject_user_id),
    )


def get_feature_access_policy_overrides(
    session: Session,
    *,
    subject_type: str,
    subject_user_id: int | None = None,
    registry: FeatureAccessRegistry | None = None,
) -> dict[str, FeatureAccessDecision]:
    effective_registry = registry or load_feature_access_registry()
    row = _get_policy_row(
        session,
        subject_type=subject_type,
        subject_user_id=subject_user_id,
    )
    if row is None:
        if subject_type == FEATURE_ACCESS_SUBJECT_ANONYMOUS:
            return build_default_anonymous_overrides(effective_registry)
        return {}
    return normalize_feature_access_overrides(row.overrides, effective_registry)


def save_feature_access_policy_overrides(
    session: Session,
    *,
    subject_type: str,
    overrides: dict[str, Any],
    updated_by_user_id: int | None = None,
    subject_user_id: int | None = None,
) -> dict[str, FeatureAccessDecision]:
    registry = load_feature_access_registry()
    normalized = normalize_feature_access_overrides(overrides, registry)
    normalized = _cascade_required_descendant_denies(normalized, registry)
    normalized = _expand_required_ancestor_allows(normalized, registry)

    row = _get_policy_row(
        session,
        subject_type=subject_type,
        subject_user_id=subject_user_id,
    )
    now = time.time()
    subject_key = build_feature_access_subject_key(subject_type, subject_user_id)

    if subject_type == FEATURE_ACCESS_SUBJECT_USER and not normalized:
        if row is not None:
            session.delete(row)
            session.commit()
        return {}

    if row is None:
        row = FeatureAccessPolicy(
            subject_key=subject_key,
            subject_type=subject_type,
            subject_user_id=subject_user_id,
            created_at=now,
        )

    row.overrides = normalized
    row.updated_at = now
    row.updated_by_user_id = updated_by_user_id
    session.add(row)
    session.commit()
    session.refresh(row)
    return normalize_feature_access_overrides(row.overrides, registry)


def _build_registry_tree_items(
    *,
    registry: FeatureAccessRegistry,
    current_user: Optional[User],
    local_overrides: dict[str, FeatureAccessDecision],
    anonymous_effective_map: dict[str, bool] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    flat_items: dict[str, dict[str, Any]] = {}

    def build_node(key: str, parent_effective: bool | None) -> dict[str, Any]:
        node = registry.node_map[key]
        disabled_by_ancestor = parent_effective is False

        if current_user is not None and current_user.is_superuser:
            local_decision: FeatureAccessDecision = "inherit"
            inherited_effective_value = None
            base_value = True
            source: FeatureAccessSource = "superuser"
        elif current_user is None:
            local_decision = local_overrides.get(key, "inherit")
            inherited_effective_value = None
            if local_decision == "allow":
                base_value = True
                source = "explicit_allow"
            elif local_decision == "deny":
                base_value = False
                source = "explicit_deny"
            else:
                base_value = node.default_anonymous_allow
                source = "default_allow" if base_value else "default_deny"
        else:
            local_decision = local_overrides.get(key, "inherit")
            inherited_effective_value = bool((anonymous_effective_map or {}).get(key, False))
            if local_decision == "allow":
                base_value = True
                source = "explicit_allow"
            elif local_decision == "deny":
                base_value = False
                source = "explicit_deny"
            else:
                base_value = inherited_effective_value
                source = "inherit_anonymous"

        effective_value = base_value if parent_effective is None else bool(parent_effective and base_value)
        effective_source: FeatureAccessSource = "ancestor_denied" if disabled_by_ancestor else source

        flat_item = {
            "key": node.key,
            "title": node.title,
            "node_type": node.node_type,
            "parent_key": node.parent_key,
            "sort_order": node.sort_order,
            "route_paths": list(node.route_paths),
            "menu_paths": list(node.menu_paths),
            "api_scopes": list(node.api_scopes),
            "default_anonymous_allow": node.default_anonymous_allow,
            "local_decision": local_decision,
            "base_value": base_value,
            "effective_value": effective_value,
            "inherited_effective_value": inherited_effective_value,
            "disabled_by_ancestor": disabled_by_ancestor,
            "source": effective_source,
        }
        item = {**flat_item, "children": []}

        child_items = [
            build_node(child_key, effective_value)
            for child_key in registry.children_map.get(key, ())
        ]
        item["children"] = child_items
        flat_items[key] = flat_item
        return item

    tree = [build_node(root_key, None) for root_key in registry.root_keys]
    return tree, flat_items


def build_feature_access_subject_context(
    session: Session,
    *,
    current_user: Optional[User],
) -> dict[str, Any]:
    registry = load_feature_access_registry()
    anonymous_overrides = get_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_ANONYMOUS,
        registry=registry,
    )
    anonymous_tree, anonymous_flat_items = _build_registry_tree_items(
        registry=registry,
        current_user=None,
        local_overrides=anonymous_overrides,
    )
    anonymous_effective_map = {
        key: bool(item["effective_value"])
        for key, item in anonymous_flat_items.items()
    }

    if current_user is None:
        return {
            "registry_version": registry.version,
            "subject": {
                "kind": FEATURE_ACCESS_SUBJECT_ANONYMOUS,
                "is_authenticated": False,
                "is_superuser": False,
                "user_id": None,
                "username": None,
            },
            "overrides": anonymous_overrides,
            "items": anonymous_tree,
            "flat_items": anonymous_flat_items,
            "effective_keys": sorted(
                [
                    key
                    for key, item in anonymous_flat_items.items()
                    if item["effective_value"]
                ]
            ),
        }

    user_overrides = get_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=current_user.id,
        registry=registry,
    )
    user_tree, user_flat_items = _build_registry_tree_items(
        registry=registry,
        current_user=current_user,
        local_overrides=user_overrides,
        anonymous_effective_map=anonymous_effective_map,
    )
    return {
        "registry_version": registry.version,
        "subject": {
            "kind": FEATURE_ACCESS_SUBJECT_USER,
            "is_authenticated": True,
            "is_superuser": bool(current_user.is_superuser),
            "user_id": current_user.id,
            "username": current_user.username,
        },
        "overrides": user_overrides,
        "items": user_tree,
        "flat_items": user_flat_items,
        "effective_keys": sorted(
            [
                key
                for key, item in user_flat_items.items()
                if item["effective_value"]
            ]
        ),
    }


def build_feature_access_admin_subject_context(
    session: Session,
    *,
    subject_type: str,
    subject_user: User | None = None,
) -> dict[str, Any]:
    if subject_type == FEATURE_ACCESS_SUBJECT_ANONYMOUS:
        return build_feature_access_subject_context(session, current_user=None)
    if subject_type != FEATURE_ACCESS_SUBJECT_USER or subject_user is None:
        raise ValueError("非法权限主体")
    return build_feature_access_subject_context(session, current_user=subject_user)


def is_feature_access_allowed(
    session: Session,
    *,
    feature_key: str,
    current_user: Optional[User],
) -> bool:
    context = build_feature_access_subject_context(session, current_user=current_user)
    flat_items = context["flat_items"]
    item = flat_items.get(feature_key)
    if not item:
        return False
    return bool(item["effective_value"])


def serialize_feature_access_registry(
    registry: FeatureAccessRegistry | None = None,
) -> dict[str, Any]:
    effective_registry = registry or load_feature_access_registry()
    return {
        "version": effective_registry.version,
        "items": [
            {
                "key": node.key,
                "title": node.title,
                "node_type": node.node_type,
                "parent_key": node.parent_key,
                "sort_order": node.sort_order,
                "route_paths": list(node.route_paths),
                "menu_paths": list(node.menu_paths),
                "api_scopes": list(node.api_scopes),
                "default_anonymous_allow": node.default_anonymous_allow,
            }
            for node in sorted(
                effective_registry.node_map.values(),
                key=lambda item: (item.sort_order, item.title, item.key),
            )
        ],
    }
