"""凡修人物笔记的兼容迁移与更新。

get_or_migrate_fanxiu_char_note 合并旧记录并暂存变更，由调用方提交；
upsert_character_note 完成一次更新并提交。字段与关系迁移规则集中在此，
read_character_note 提供不落盘的兼容视图；HTTP 层只负责鉴权、参数和响应。
共享语义函数也供物品笔记更新复用。
"""

from copy import deepcopy
from datetime import date, time as dt_time
from typing import Callable
from sqlmodel import or_
from backend.core.notes.refs import note_public_id
import json
import time
from datetime import datetime
from typing import Any
from sqlmodel import Session, select
from backend.core.notes.identity import allocate_new_note_identity
from backend.core.notes.refs import note_edge_ref, note_ref_aliases
from backend.models import NoteEdge, NoteNode, User
from backend.schemas import NoteUpdate
from backend.core.notes.semantics import NOTE_KIND_FANXIU_CHAR, NOTE_KIND_DEFAULT, NOTE_WEIGHT_MODE_LINEAR, build_legacy_color_type_key, derive_note_taxonomy_from_legacy, derive_primary_node_type, normalize_note_color, normalize_note_types

FANXIU_CHAR_TYPE = "memo"

FANXIU_CHAR_KIND = NOTE_KIND_FANXIU_CHAR

def _normalize_fanxiu_note_shapes(note: NoteNode) -> bool:
    changed = False
    if not isinstance(note.history, list):
        note.history = []
        changed = True
    if not isinstance(note.custom_fields, list):
        note.custom_fields = []
        changed = True
    return changed

def _ensure_fanxiu_char_note_semantics(note: NoteNode) -> bool:
    changed = _normalize_fanxiu_note_shapes(note)
    normalized_note_types = normalize_note_types(note.note_types, fallback_type=FANXIU_CHAR_TYPE)
    normalized_note_color = normalize_note_color(note.color)
    if normalized_note_color and len(normalized_note_types) == 1:
        only_type = normalized_note_types[0]
        if only_type.get("key") == FANXIU_CHAR_TYPE and int(only_type.get("weight", 0)) == 100:
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]

    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_CHAR_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_CHAR_KIND,
        node_status=note.node_status,
    )

    expected_updates = {
        "note_types": normalized_note_types,
        "node_type": primary_node_type,
        "note_categories": taxonomy["note_categories"],
        "primary_category": taxonomy["primary_category"],
        "note_form": taxonomy["note_form"],
        "note_kind": FANXIU_CHAR_KIND,
        "note_scene": taxonomy["note_scene"],
        "lifecycle_stage": taxonomy["lifecycle_stage"],
        "weight_mode": NOTE_WEIGHT_MODE_LINEAR,
    }

    for field_name, expected_value in expected_updates.items():
        if getattr(note, field_name) != expected_value:
            setattr(note, field_name, expected_value)
            changed = True

    return changed

def _has_fanxiu_note_custom_fields(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return False

def _is_fanxiu_char_stub(note: NoteNode) -> bool:
    has_content = bool(str(note.content or "").strip())
    has_weight = int(note.weight or 0) > 0
    has_custom_fields = _has_fanxiu_note_custom_fields(note.custom_fields)
    has_history = isinstance(note.history, list) and len(note.history) > 0
    return not (has_content or has_weight or has_custom_fields or has_history)

def _has_meaningful_fanxiu_char_data(note: NoteNode) -> bool:
    return not _is_fanxiu_char_stub(note)

def _merge_legacy_fanxiu_char_note_data(target: NoteNode, legacy: NoteNode, *, now: float | None = None) -> bool:
    if not _is_fanxiu_char_stub(target) or not _has_meaningful_fanxiu_char_data(legacy):
        return False

    target.content = legacy.content
    target.weight = legacy.weight
    target.start_at = legacy.start_at
    target.history = legacy.history if isinstance(legacy.history, list) else []
    target.custom_fields = legacy.custom_fields if isinstance(legacy.custom_fields, list) else []
    target.updated_at = max(float(target.updated_at or 0), float(legacy.updated_at or 0), time.time() if now is None else now)
    return True

def _normalize_fanxiu_custom_fields(value: Any) -> list[list[Any]]:
    if isinstance(value, list):
        normalized: list[list[Any]] = []
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) >= 3 and str(item[0] or "").strip():
                normalized.append([str(item[0]).strip(), str(item[1] or "string"), item[2]])
                continue
            if isinstance(item, dict) and str(item.get("key") or "").strip():
                field_value = item.get("value")
                field_type = item.get("type")
                if not field_type:
                    field_type = "boolean" if isinstance(field_value, bool) else "number" if isinstance(field_value, (int, float)) else "string"
                normalized.append([str(item["key"]).strip(), str(field_type), field_value])
        return normalized

    if isinstance(value, dict):
        normalized = []
        for key, field_value in value.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            field_type = "boolean" if isinstance(field_value, bool) else "number" if isinstance(field_value, (int, float)) else "string"
            normalized.append([key_text, field_type, field_value])
        return normalized

    return []

def _merge_fanxiu_char_note_fields(target: NoteNode, source: NoteNode, *, now: float | None = None) -> bool:
    current_time = time.time() if now is None else now
    changed = False
    target_content = str(target.content or "").strip()
    source_content = str(source.content or "").strip()
    if source_content and not target_content:
        target.content = source.content
        changed = True
    elif source_content and target_content and source_content != target_content:
        source_label = datetime.fromtimestamp(float(source.updated_at or source.start_at or current_time)).strftime("%Y-%m-%d %H:%M:%S")
        target.content = (
            f"{target.content or ''}"
            f'<hr data-codeyun-merged-fanxiu-char="true">'
            f"<p>以下内容来自旧重复文档（{source_label}）：</p>"
            f"{source.content or ''}"
        )
        changed = True

    if int(target.weight or 0) <= 0 and int(source.weight or 0) > 0:
        target.weight = int(source.weight or 0)
        changed = True

    target_fields = _normalize_fanxiu_custom_fields(target.custom_fields)
    source_fields = _normalize_fanxiu_custom_fields(source.custom_fields)
    if source_fields:
        existing_keys = {item[0] for item in target_fields}
        merged_fields = [*target_fields]
        for item in source_fields:
            if item[0] not in existing_keys:
                merged_fields.append(item)
                existing_keys.add(item[0])
        if merged_fields != target_fields:
            target.custom_fields = merged_fields
            changed = True
    elif not isinstance(target.custom_fields, list):
        target.custom_fields = target_fields
        changed = True

    target_history = target.history if isinstance(target.history, list) else []
    source_history = source.history if isinstance(source.history, list) else []
    if source_history:
        seen_history = {(item.get("ts"), item.get("f"), json.dumps(item.get("v"), sort_keys=True, ensure_ascii=False)) for item in target_history if isinstance(item, dict)}
        merged_history = [item for item in target_history if isinstance(item, dict)]
        for item in source_history:
            if not isinstance(item, dict):
                continue
            key = (item.get("ts"), item.get("f"), json.dumps(item.get("v"), sort_keys=True, ensure_ascii=False))
            if key in seen_history:
                continue
            merged_history.append(item)
            seen_history.add(key)
        merged_history.sort(key=lambda item: float(item.get("ts") or 0))
        if merged_history != target_history:
            target.history = merged_history
            changed = True
    elif not isinstance(target.history, list):
        target.history = []
        changed = True

    target.updated_at = max(float(target.updated_at or 0), float(source.updated_at or 0), current_time if changed else 0)
    return changed

def _retarget_fanxiu_char_edges(session: Session, source_note: NoteNode, target_note: NoteNode) -> None:
    if not source_note.id or not target_note.id or source_note.id == target_note.id:
        return

    source_refs = note_ref_aliases(source_note)
    target_ref = note_edge_ref(target_note)
    edges = session.exec(
        select(NoteEdge).where(
            (NoteEdge.source_id.in_(source_refs)) | (NoteEdge.target_id.in_(source_refs))
        )
    ).all()

    for edge in edges:
        next_source_id = target_ref if str(edge.source_id) in source_refs else edge.source_id
        next_target_id = target_ref if str(edge.target_id) in source_refs else edge.target_id
        if next_source_id == next_target_id:
            session.delete(edge)
            continue

        duplicate_edge = session.exec(
            select(NoteEdge).where(
                NoteEdge.id != edge.id,
                NoteEdge.user_id == edge.user_id,
                NoteEdge.source_id == next_source_id,
                NoteEdge.target_id == next_target_id,
                NoteEdge.label == edge.label,
            )
        ).first()
        if duplicate_edge:
            session.delete(edge)
            continue

        edge.source_id = next_source_id
        edge.target_id = next_target_id
        session.add(edge)

def _merge_duplicate_fanxiu_char_notes(
    session: Session,
    target: NoteNode,
    duplicate_notes: list[NoteNode],
) -> bool:
    changed = False
    for duplicate in duplicate_notes:
        if duplicate.id == target.id:
            continue
        changed = _merge_fanxiu_char_note_fields(target, duplicate) or changed
        _retarget_fanxiu_char_edges(session, duplicate, target)
        session.delete(duplicate)
        changed = True

    if changed:
        target.title = str(target.title or "").strip()
        target.updated_at = max(float(target.updated_at or 0), time.time())
        session.add(target)
    return changed

def _fanxiu_char_note_rank(note: NoteNode) -> tuple[int, int, int, int, int, float, float, str]:
    return (
        1 if note.note_kind == FANXIU_CHAR_KIND else 0,
        1 if str(note.content or "").strip() else 0,
        1 if _has_fanxiu_note_custom_fields(note.custom_fields) else 0,
        1 if isinstance(note.history, list) and len(note.history) > 0 else 0,
        1 if int(note.weight or 0) > 0 else 0,
        float(note.updated_at or 0),
        float(note.start_at or 0),
        str(note.id or ""),
    )

def _character_note_candidates(session: Session, fanxiu_user: User, char_name: str) -> list[NoteNode]:
    notes = session.exec(select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.title == char_name,
    )).all()
    return [note for note in notes if note.note_kind in (FANXIU_CHAR_KIND, None, "", NOTE_KIND_DEFAULT)]


def read_character_note(session: Session, fanxiu_user: User, char_name: str) -> NoteNode | None:
    """返回脱离 Session 的兼容视图；不创建、合并落盘、改关系或更新修改时间。

    复用写入路径的主记录选择与字段合并规则。旧记录仅在返回值中合并，
    upsert_character_note 才执行真实迁移。也不刷出调用方已有的待写变更。
    """
    with session.no_autoflush:
        candidates = _character_note_candidates(session, fanxiu_user, char_name)
    primary = max(candidates, key=_fanxiu_char_note_rank, default=None)
    if primary is None:
        return None
    view = NoteNode(**deepcopy(primary.model_dump()))
    observed_at = max(float(note.updated_at or note.start_at or 0) for note in candidates)
    legacy = max(
        (note for note in candidates if note.note_kind in (None, "", NOTE_KIND_DEFAULT)),
        key=_fanxiu_char_note_rank, default=None,
    )
    if legacy is not None and legacy.id != primary.id:
        _merge_legacy_fanxiu_char_note_data(view, legacy, now=observed_at)
    for note in candidates:
        if note.id != primary.id:
            _merge_fanxiu_char_note_fields(view, note, now=observed_at)
    _ensure_fanxiu_char_note_semantics(view)
    return view


def get_or_migrate_fanxiu_char_note(
    session: Session,
    fanxiu_user: User,
    char_name: str,
) -> NoteNode | None:
    candidate_notes = _character_note_candidates(session, fanxiu_user, char_name)
    primary_note = max(candidate_notes, key=_fanxiu_char_note_rank, default=None)
    legacy_note = max(
        [note for note in candidate_notes if note.note_kind in (None, "", NOTE_KIND_DEFAULT)],
        key=_fanxiu_char_note_rank,
        default=None,
    )

    changed = False
    if primary_note is None and legacy_note is not None:
        primary_note = legacy_note

    if primary_note is None:
        return None

    if legacy_note is not None and legacy_note is not primary_note:
        changed = _merge_legacy_fanxiu_char_note_data(primary_note, legacy_note) or changed

    duplicate_notes = [note for note in candidate_notes if note.id != primary_note.id]
    changed = _merge_duplicate_fanxiu_char_notes(session, primary_note, duplicate_notes) or changed
    changed = _ensure_fanxiu_char_note_semantics(primary_note) or changed
    if changed:
        session.add(primary_note)
    return primary_note

def prepare_note_update_semantics(
    note_in: NoteUpdate,
    *,
    note_kind: str,
    fallback_type: str,
) -> tuple[list[dict[str, Any]], str | None, str, dict[str, Any]]:
    normalized_note_types = normalize_note_types(note_in.note_types, fallback_type=fallback_type)
    normalized_note_color = normalize_note_color(note_in.color)
    if normalized_note_color and (
        not note_in.note_types
        or (
            len(normalized_note_types) == 1
            and normalized_note_types[0].get("key") == fallback_type
            and int(normalized_note_types[0].get("weight", 0)) == 100
        )
    ):
        legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
        if legacy_color_type_key:
            normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=fallback_type)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=note_kind,
        node_status=note_in.node_status,
    )
    return normalized_note_types, normalized_note_color, primary_node_type, taxonomy

def refresh_existing_note_semantics(
    db_note: NoteNode,
    note_in: NoteUpdate,
    *,
    normalized_note_types: list[dict[str, Any]],
    normalized_note_color: str | None,
    primary_node_type: str,
    note_kind: str,
    fallback_type: str,
) -> None:
    if note_in.note_types is not None:
        db_note.note_types = normalized_note_types
        db_note.node_type = primary_node_type
    elif not db_note.note_types:
        db_note.note_types = normalized_note_types
        db_note.node_type = primary_node_type
    if "color" in note_in.model_fields_set:
        db_note.color = normalized_note_color
    elif db_note.color:
        existing_note_types = normalize_note_types(db_note.note_types, fallback_type=db_note.node_type or fallback_type)
        normalized_existing_color = normalize_note_color(db_note.color)
        if normalized_existing_color and len(existing_note_types) == 1:
            only_type = existing_note_types[0]
            existing_fallback_type = db_note.node_type or fallback_type
            if only_type.get("key") == existing_fallback_type and int(only_type.get("weight", 0)) == 100:
                legacy_color_type_key = build_legacy_color_type_key(normalized_existing_color)
                if legacy_color_type_key:
                    db_note.note_types = [{"key": legacy_color_type_key, "weight": 100}]
                    db_note.node_type = legacy_color_type_key

    refreshed_taxonomy = derive_note_taxonomy_from_legacy(
        db_note.note_types,
        node_type=db_note.node_type or fallback_type,
        note_kind=note_kind,
        node_status=db_note.node_status,
    )
    db_note.note_categories = refreshed_taxonomy["note_categories"]
    db_note.primary_category = refreshed_taxonomy["primary_category"]
    db_note.note_form = refreshed_taxonomy["note_form"]
    db_note.note_scene = refreshed_taxonomy["note_scene"]
    db_note.lifecycle_stage = refreshed_taxonomy["lifecycle_stage"]

def upsert_character_note(
    session: Session,
    fanxiu_user: User,
    char_name: str,
    note_in: NoteUpdate,
) -> NoteNode:
    db_note = get_or_migrate_fanxiu_char_note(session, fanxiu_user, char_name)

    current_time = time.time()
    normalized_note_types, normalized_note_color, primary_node_type, taxonomy = prepare_note_update_semantics(
        note_in,
        note_kind=FANXIU_CHAR_KIND,
        fallback_type=FANXIU_CHAR_TYPE,
    )

    if not db_note:
        note_identity = allocate_new_note_identity(session)
        db_note = NoteNode(
            id=note_identity.primary_id,
            numeric_id=note_identity.numeric_id,
            legacy_id=note_identity.legacy_id,
            user_id=fanxiu_user.id,
            title=char_name,
            content=note_in.content or "",
            weight=note_in.weight if note_in.weight is not None else 0,
            node_type=primary_node_type,
            note_types=normalized_note_types,
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=FANXIU_CHAR_KIND,
            note_scene=taxonomy["note_scene"],
            node_status=note_in.node_status,
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=normalized_note_color,
            weight_mode=NOTE_WEIGHT_MODE_LINEAR,
            created_at=current_time,
            updated_at=current_time,
            start_at=note_in.start_at if note_in.start_at is not None else current_time,
            history=[],
            custom_fields=[],
        )
        session.add(db_note)
    else:
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.weight is not None:
            db_note.weight = note_in.weight
        if note_in.start_at is not None:
            db_note.start_at = note_in.start_at
        if db_note.note_kind != FANXIU_CHAR_KIND:
            db_note.note_kind = FANXIU_CHAR_KIND
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status
        refresh_existing_note_semantics(
            db_note,
            note_in,
            normalized_note_types=normalized_note_types,
            normalized_note_color=normalized_note_color,
            primary_node_type=primary_node_type,
            note_kind=FANXIU_CHAR_KIND,
            fallback_type=FANXIU_CHAR_TYPE,
        )

        db_note.updated_at = current_time
        session.add(db_note)

    session.commit()
    session.refresh(db_note)
    return db_note


class MissingInventoryNoteTitle(ValueError):
    """目录条目缺少名称，无法创建或更新其笔记。"""


def wardrobe_item_date_to_timestamp(value: Any) -> float:
    if isinstance(value, date):
        item_date = value
    else:
        try:
            item_date = date.fromisoformat(str(value or "").strip())
        except ValueError:
            item_date = date.today()
    return datetime.combine(item_date, dt_time.min).timestamp()

def activity_item_start_to_timestamp(value: Any) -> float:
    return wardrobe_item_date_to_timestamp(value)

def get_fanxiu_note_by_id(
    session: Session,
    fanxiu_user: User,
    note_id: str | None,
    note_kind: str,
) -> NoteNode | None:
    normalized_note_id = str(note_id or "").strip()
    if not normalized_note_id:
        return None

    conditions = [NoteNode.id == normalized_note_id, NoteNode.legacy_id == normalized_note_id]
    if normalized_note_id.isdecimal():
        conditions.append(NoteNode.numeric_id == int(normalized_note_id))
    statement = select(NoteNode).where(
        or_(*conditions),
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.note_kind == note_kind,
    )
    return session.exec(statement).first()

def sync_wardrobe_note_fields(note: NoteNode, item: dict[str, Any]) -> None:
    note.title = str(item.get("name") or "").strip()
    note.weight = int(item.get("rank") or 0)
    note.start_at = wardrobe_item_date_to_timestamp(item.get("date"))
    note.updated_at = time.time()

def sync_activity_note_fields(note: NoteNode, item: dict[str, Any]) -> None:
    note.title = str(item.get("name") or "").strip()
    note.start_at = activity_item_start_to_timestamp(item.get("start_date"))
    note.updated_at = time.time()

def sync_hall_note_refs(
    session: Session,
    fanxiu_user: User,
    normalized_payload: dict[str, Any],
    *,
    note_kind: str,
    sync_note_fields: Callable[[NoteNode, dict[str, Any]], None],
) -> bool:
    touched_existing_note = False
    for items in normalized_payload.values():
        if not isinstance(items, list):
            continue
        touched_existing_note = (
            sync_item_note_refs(
                session,
                fanxiu_user,
                items,
                note_kind=note_kind,
                sync_note_fields=sync_note_fields,
            )
            or touched_existing_note
        )
    return touched_existing_note

def sync_item_note_refs(
    session: Session,
    fanxiu_user: User,
    items: list[Any],
    *,
    note_kind: str,
    sync_note_fields: Callable[[NoteNode, dict[str, Any]], None],
) -> bool:
    touched_existing_note = False
    for item in items:
        if not isinstance(item, dict):
            continue
        db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), note_kind)
        if db_note:
            sync_note_fields(db_note, item)
            item["note_id"] = note_public_id(db_note)
            session.add(db_note)
            touched_existing_note = True
        elif item.get("note_id"):
            item.pop("note_id", None)
    return touched_existing_note

def upsert_inventory_item_note(
    session: Session,
    fanxiu_user: User,
    item: dict[str, Any],
    note_in: NoteUpdate,
    *,
    note_kind: str,
    fallback_type: str,
    item_weight: int | None = None,
    item_start_at: float | None = None,
    title_error_message: str = "请先填写条目名称，再编辑文档。",
    sync_weight: bool = True,
) -> NoteNode:
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), note_kind)

    current_time = time.time()
    normalized_note_types, normalized_note_color, primary_node_type, taxonomy = prepare_note_update_semantics(
        note_in,
        note_kind=note_kind,
        fallback_type=fallback_type,
    )

    item_title = str(item.get("name") or "").strip()
    resolved_item_weight = int(item.get("rank") or 0) if item_weight is None else item_weight
    resolved_item_start_at = wardrobe_item_date_to_timestamp(item.get("date")) if item_start_at is None else item_start_at
    if not item_title:
        raise MissingInventoryNoteTitle(title_error_message)

    if not db_note:
        note_identity = allocate_new_note_identity(session)
        db_note = NoteNode(
            id=note_identity.primary_id,
            numeric_id=note_identity.numeric_id,
            legacy_id=note_identity.legacy_id,
            user_id=fanxiu_user.id,
            title=item_title,
            content=note_in.content or "",
            weight=resolved_item_weight,
            node_type=primary_node_type,
            note_types=normalized_note_types,
            note_categories=taxonomy["note_categories"],
            primary_category=taxonomy["primary_category"],
            note_form=taxonomy["note_form"],
            note_kind=note_kind,
            note_scene=taxonomy["note_scene"],
            node_status=note_in.node_status,
            lifecycle_stage=taxonomy["lifecycle_stage"],
            color=normalized_note_color,
            weight_mode=NOTE_WEIGHT_MODE_LINEAR,
            created_at=current_time,
            updated_at=current_time,
            start_at=resolved_item_start_at,
            history=[],
            custom_fields=[],
        )
        session.add(db_note)
    else:
        if note_in.content is not None:
            db_note.content = note_in.content
        if db_note.note_kind != note_kind:
            db_note.note_kind = note_kind
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status
        refresh_existing_note_semantics(
            db_note,
            note_in,
            normalized_note_types=normalized_note_types,
            normalized_note_color=normalized_note_color,
            primary_node_type=primary_node_type,
            note_kind=note_kind,
            fallback_type=fallback_type,
        )
        if note_in.custom_fields is not None:
            db_note.custom_fields = note_in.custom_fields
        elif not isinstance(db_note.custom_fields, list):
            db_note.custom_fields = []
        db_note.updated_at = current_time
        session.add(db_note)

    db_note.title = item_title
    if sync_weight:
        db_note.weight = resolved_item_weight
    db_note.start_at = resolved_item_start_at

    session.commit()
    session.refresh(db_note)
    return db_note
