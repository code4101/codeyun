"""凡修角色、物品及活动笔记的 HTTP 适配。

父路由提供功能访问控制；这里处理用户权限与 HTTP 错误转换，笔记更新
和兼容迁移由 core.fanxiu.notes 实施。GET 仅返回兼容视图，真实迁移只在更新时执行。
"""
from fastapi import APIRouter
from backend.core.fanxiu.notes import MissingInventoryNoteTitle, activity_item_start_to_timestamp, get_fanxiu_note_by_id, upsert_inventory_item_note, read_character_note, upsert_character_note
from backend.api.fanxiu_access import find_fanxiu_user, get_fanxiu_user, ensure_fanxiu_write_permission
from typing import Any, List, Optional
from fastapi import Depends, HTTPException
from sqlmodel import Session
from backend.core.access.auth import get_current_active_user, get_optional_current_user_from_token
from backend.core.notes.refs import note_public_id
from backend.db import get_session
from backend.models import NoteNode, User
from backend.schemas import NoteRead, NoteUpdate
from backend.core.fanxiu.catalog.inventory import load_magic_treasure_hall, save_magic_treasure_hall
from backend.core.fanxiu.catalog.inventory import load_wardrobe_hall, save_wardrobe_hall
from backend.core.fanxiu.catalog.inventory import load_spirit_beast_hall, save_spirit_beast_hall
from backend.core.fanxiu.catalog.inventory import load_activity_list, save_activity_list
from backend.core.notes.access import note_to_response_dict
from backend.core.notes.semantics import NOTE_KIND_FANXIU_ACTIVITY_ITEM, NOTE_KIND_FANXIU_MAGIC_TREASURE_ITEM, NOTE_KIND_FANXIU_SPIRIT_BEAST_ITEM, NOTE_KIND_FANXIU_WARDROBE_ITEM

inventory_router = APIRouter()
chars_router = APIRouter()


FANXIU_WARDROBE_TYPE = "doc"


FANXIU_WARDROBE_KIND = NOTE_KIND_FANXIU_WARDROBE_ITEM


FANXIU_SPIRIT_BEAST_TYPE = "doc"


FANXIU_SPIRIT_BEAST_KIND = NOTE_KIND_FANXIU_SPIRIT_BEAST_ITEM


FANXIU_MAGIC_TREASURE_TYPE = "doc"


FANXIU_MAGIC_TREASURE_KIND = NOTE_KIND_FANXIU_MAGIC_TREASURE_ITEM


FANXIU_ACTIVITY_TYPE = "doc"


FANXIU_ACTIVITY_KIND = NOTE_KIND_FANXIU_ACTIVITY_ITEM


XIANZHOU_RACE_CHAR_NAMES = (
    "凌玉灵",
    "大衍神君",
    "黑凤王",
    "黛儿",
    "南宫婉",
    "向之礼",
    "冰凤仙子",
    "银月",
    "甲天木",
    "元刹",
    "天元圣皇",
    "冰魄仙子",
)


def find_wardrobe_item(
    wardrobe_hall: dict[str, list[dict[str, Any]]],
    item_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    target_id = str(item_id or "").strip()
    if not target_id:
        return None, None

    for section_key, items in wardrobe_hall.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "").strip() == target_id:
                return section_key, item
    return None, None


def find_spirit_beast_item(
    spirit_beast_hall: dict[str, list[dict[str, Any]]],
    item_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    return find_wardrobe_item(spirit_beast_hall, item_id)


def find_magic_treasure_item(
    magic_treasure_hall: dict[str, list[dict[str, Any]]],
    item_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    return find_wardrobe_item(magic_treasure_hall, item_id)


def find_activity_item(
    activity_list: list[dict[str, Any]],
    item_id: str,
) -> dict[str, Any] | None:
    target_id = str(item_id or "").strip()
    if not target_id:
        return None

    for item in activity_list:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == target_id:
            return item
    return None


def serialize_fanxiu_note_read(
    note: NoteNode,
    current_user: Optional[User],
    **extra_fields: Any,
) -> dict[str, Any]:
    payload = note_to_response_dict(note, current_user, **extra_fields)
    if not isinstance(payload.get("custom_fields"), list):
        payload["custom_fields"] = []
    if not isinstance(payload.get("history"), list):
        payload["history"] = []
    return payload


@inventory_router.get("/inventory/wardrobe-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_wardrobe_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    wardrobe_hall = load_wardrobe_hall()
    _, item = find_wardrobe_item(wardrobe_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Wardrobe item not found")

    fanxiu_user = find_fanxiu_user(session)
    if fanxiu_user is None:
        return None
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_WARDROBE_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


def _upsert_fanxiu_inventory_item_note(
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
    try:
        return upsert_inventory_item_note(
            session, fanxiu_user, item, note_in,
            note_kind=note_kind,
            fallback_type=fallback_type,
            item_weight=item_weight,
            item_start_at=item_start_at,
            title_error_message=title_error_message,
            sync_weight=sync_weight,
        )
    except MissingInventoryNoteTitle as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@inventory_router.put("/inventory/wardrobe-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_wardrobe_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    wardrobe_hall = load_wardrobe_hall()
    _, item = find_wardrobe_item(wardrobe_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Wardrobe item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = _upsert_fanxiu_inventory_item_note(
        session,
        fanxiu_user,
        item,
        note_in,
        note_kind=FANXIU_WARDROBE_KIND,
        fallback_type=FANXIU_WARDROBE_TYPE,
    )
    item["note_id"] = note_public_id(db_note)
    try:
        save_wardrobe_hall(wardrobe_hall)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修道具仓库失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.get("/inventory/spirit-beast-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_spirit_beast_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    spirit_beast_hall = load_spirit_beast_hall()
    _, item = find_spirit_beast_item(spirit_beast_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Spirit beast item not found")

    fanxiu_user = find_fanxiu_user(session)
    if fanxiu_user is None:
        return None
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_SPIRIT_BEAST_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.put("/inventory/spirit-beast-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_spirit_beast_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    spirit_beast_hall = load_spirit_beast_hall()
    _, item = find_spirit_beast_item(spirit_beast_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Spirit beast item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = _upsert_fanxiu_inventory_item_note(
        session,
        fanxiu_user,
        item,
        note_in,
        note_kind=FANXIU_SPIRIT_BEAST_KIND,
        fallback_type=FANXIU_SPIRIT_BEAST_TYPE,
    )
    item["note_id"] = note_public_id(db_note)
    try:
        save_spirit_beast_hall(spirit_beast_hall)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修灵兽仓库失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.get("/inventory/magic-treasure-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_magic_treasure_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    magic_treasure_hall = load_magic_treasure_hall()
    _, item = find_magic_treasure_item(magic_treasure_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Magic treasure item not found")

    fanxiu_user = find_fanxiu_user(session)
    if fanxiu_user is None:
        return None
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_MAGIC_TREASURE_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.put("/inventory/magic-treasure-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_magic_treasure_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    magic_treasure_hall = load_magic_treasure_hall()
    _, item = find_magic_treasure_item(magic_treasure_hall, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Magic treasure item not found")

    fanxiu_user = get_fanxiu_user(session)
    db_note = _upsert_fanxiu_inventory_item_note(
        session,
        fanxiu_user,
        item,
        note_in,
        note_kind=FANXIU_MAGIC_TREASURE_KIND,
        fallback_type=FANXIU_MAGIC_TREASURE_TYPE,
    )

    item["note_id"] = note_public_id(db_note)
    try:
        save_magic_treasure_hall(magic_treasure_hall)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修法宝仓库失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.get("/activity-notes/{item_id}", response_model=Optional[NoteRead])
def read_fanxiu_activity_note(
    item_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
):
    activity_list = load_activity_list()
    item = find_activity_item(activity_list, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="活动条目不存在")

    fanxiu_user = find_fanxiu_user(session)
    if fanxiu_user is None:
        return None
    db_note = get_fanxiu_note_by_id(session, fanxiu_user, item.get("note_id"), FANXIU_ACTIVITY_KIND)
    if not db_note:
        return None
    return serialize_fanxiu_note_read(db_note, current_user)


@inventory_router.put("/activity-notes/{item_id}", response_model=NoteRead)
def update_fanxiu_activity_note(
    item_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    activity_list = load_activity_list()
    item = find_activity_item(activity_list, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="活动条目不存在")

    fanxiu_user = get_fanxiu_user(session)
    db_note = _upsert_fanxiu_inventory_item_note(
        session,
        fanxiu_user,
        item,
        note_in,
        note_kind=FANXIU_ACTIVITY_KIND,
        fallback_type=FANXIU_ACTIVITY_TYPE,
        item_weight=0,
        item_start_at=activity_item_start_to_timestamp(item.get("start_date")),
        title_error_message="请先填写活动名称，再编辑文档。",
        sync_weight=False,
    )

    item["note_id"] = note_public_id(db_note)
    try:
        save_activity_list(activity_list)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修活动列表失败：{exc}") from exc
    return serialize_fanxiu_note_read(db_note, current_user)


@chars_router.get("/chars", response_model=List[NoteRead])
def read_chars(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session)
):
    """
    Get all Xianzhou Race characters data.
    Publicly accessible.
    """
    fanxiu_user = find_fanxiu_user(session)
    if fanxiu_user is None:
        return []
    notes = [read_character_note(session, fanxiu_user, name) for name in XIANZHOU_RACE_CHAR_NAMES]
    return [serialize_fanxiu_note_read(note, current_user) for note in notes if note is not None]


@chars_router.get("/chars/{char_name}", response_model=NoteRead)
def read_char(
    char_name: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session)
):
    """
    Get specific character data.
    Publicly accessible.
    """
    fanxiu_user = find_fanxiu_user(session)
    note = read_character_note(session, fanxiu_user, char_name) if fanxiu_user is not None else None
    if note is None:
        raise HTTPException(status_code=404, detail="Character not found")
    return serialize_fanxiu_note_read(note, current_user)


@chars_router.put("/chars/{char_name}", response_model=NoteRead)
def update_char(
    char_name: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Update or create character data.
    Restricted to specific users.
    """
    # STRICT PERMISSION: Only 'fanxiu_official' itself can edit.
    # Even 'code4101' cannot edit directly via this API unless logged in as 'fanxiu_official'.
    # This enforces data ownership isolation.
    
    ensure_fanxiu_write_permission(current_user, session)
    fanxiu_user = get_fanxiu_user(session)
    db_note = upsert_character_note(session, fanxiu_user, char_name, note_in)
    return serialize_fanxiu_note_read(db_note, current_user)
