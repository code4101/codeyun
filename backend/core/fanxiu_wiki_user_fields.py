from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.fanxiu_resources import FanxiuResourceError
from backend.core.settings import get_settings


_STORAGE_VERSION = 1
_STORAGE_FILENAME = Path("fanxiu") / "wiki-user-fields.json"
_OBJECT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TEXT_FIELD_LIMIT = 20000


def get_fanxiu_wiki_user_fields_storage_path() -> Path:
    return get_settings().data_dir / _STORAGE_FILENAME


def get_fanxiu_wiki_user_fields(object_type: str, object_id: str | int) -> dict[str, Any]:
    normalized_type = _normalize_object_type(object_type)
    normalized_id = _normalize_object_id(object_id)
    payload = _read_storage()
    fields = _get_object_fields(payload, normalized_type, normalized_id)
    return _normalize_user_fields(fields, normalized_type, normalized_id)


def save_fanxiu_wiki_user_fields(
    object_type: str,
    object_id: str | int,
    *,
    note: Any = "",
    source: Any = "",
) -> dict[str, Any]:
    normalized_type = _normalize_object_type(object_type)
    normalized_id = _normalize_object_id(object_id)
    payload = _read_storage()
    objects = payload.get("objects", {})
    if not isinstance(objects, dict):
        objects = {}
    typed_objects = objects.get(normalized_type, {})
    if not isinstance(typed_objects, dict):
        typed_objects = {}

    normalized = _normalize_user_fields(
        {
            "note": note,
            "source": source,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        normalized_type,
        normalized_id,
    )
    if normalized["note"] or normalized["source"]:
        typed_objects[normalized_id] = normalized
    else:
        typed_objects.pop(normalized_id, None)

    if typed_objects:
        objects[normalized_type] = typed_objects
    else:
        objects.pop(normalized_type, None)
    payload["version"] = _STORAGE_VERSION
    payload["objects"] = objects
    _write_storage(payload)
    return normalized


def _normalize_object_type(value: str) -> str:
    text = str(value or "").strip().lower()
    if not _OBJECT_TYPE_RE.fullmatch(text):
        raise FanxiuResourceError(f"不支持的凡修图鉴对象类型：{value}")
    return text


def _normalize_object_id(value: str | int) -> str:
    text = str(value or "").strip()
    if not text:
        raise FanxiuResourceError("凡修图鉴对象 ID 不能为空")
    return text


def _normalize_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > _TEXT_FIELD_LIMIT:
        raise FanxiuResourceError(f"文本过长，最多 {_TEXT_FIELD_LIMIT} 个字符")
    return text


def _normalize_user_fields(fields: Any, object_type: str, object_id: str) -> dict[str, Any]:
    if not isinstance(fields, dict):
        fields = {}
    return {
        "object_type": object_type,
        "object_id": object_id,
        "note": _normalize_text(fields.get("note")),
        "source": _normalize_text(fields.get("source")),
        "updated_at": str(fields.get("updated_at") or ""),
    }


def _get_object_fields(payload: dict[str, Any], object_type: str, object_id: str) -> dict[str, Any]:
    objects = payload.get("objects", {})
    if not isinstance(objects, dict):
        return {}
    typed_objects = objects.get(object_type, {})
    if not isinstance(typed_objects, dict):
        return {}
    fields = typed_objects.get(object_id, {})
    return fields if isinstance(fields, dict) else {}


def _read_storage() -> dict[str, Any]:
    path = get_fanxiu_wiki_user_fields_storage_path()
    if not path.exists():
        return {"version": _STORAGE_VERSION, "objects": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FanxiuResourceError(f"读取凡修图鉴用户字段失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise FanxiuResourceError(f"凡修图鉴用户字段不是有效 JSON：{path}") from exc
    if not isinstance(payload, dict):
        raise FanxiuResourceError("凡修图鉴用户字段根节点不是对象")
    return payload


def _write_storage(payload: dict[str, Any]) -> None:
    path = get_fanxiu_wiki_user_fields_storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
