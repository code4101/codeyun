from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from backend.db import get_session
from backend.models import NoteNode, User
from backend.schemas import NoteRead, NoteUpdate
from backend.core.auth import get_current_active_user, get_optional_current_user_from_token
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.fanxiu_status import (
    derive_status_snapshot,
    load_status_payload,
    resolve_status_path_config,
    save_status_payload,
    save_status_config,
)
from backend.core.note_access import note_to_response_dict
from backend.core.note_semantics import (
    NOTE_KIND_FANXIU_CHAR,
    NOTE_WEIGHT_MODE_LINEAR,
    build_legacy_color_type_key,
    derive_note_taxonomy_from_legacy,
    derive_primary_node_type,
    normalize_note_color,
    normalize_note_types,
)
import uuid
import time
from passlib.context import CryptContext

router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

FANXIU_USERNAME = "凡修手游"
FANXIU_CHAR_TYPE = "memo"
FANXIU_CHAR_KIND = NOTE_KIND_FANXIU_CHAR
CODE4101_USERNAME = "code4101"


class FanxiuTaskStatusItem(BaseModel):
    name: str
    scheduled_at: str
    due: bool
    seconds_until_due: int
    is_next: bool = False


class FanxiuAccountStatusItem(BaseModel):
    name: str
    phone: Optional[str] = None
    is_current: bool = False
    has_due_task: bool = False
    due_count: int = 0
    task_count: int = 0
    next_task_name: Optional[str] = None
    next_task_at: Optional[str] = None
    tasks: List[FanxiuTaskStatusItem] = Field(default_factory=list)


class FanxiuRuntimeTimerItem(BaseModel):
    name: str
    scheduled_at: str
    due: bool
    seconds_until_due: int


class FanxiuStatusConfigRead(BaseModel):
    status_path: Optional[str] = None
    auto_detected_path: Optional[str] = None
    effective_path: Optional[str] = None
    mode: str
    file_exists: bool = False


class FanxiuStatusConfigUpdate(BaseModel):
    status_path: Optional[str] = None


class FanxiuStatusParseRequest(BaseModel):
    raw_status: dict[str, Any]


class FanxiuStatusUpdateRequest(BaseModel):
    raw_status: dict[str, Any]


class FanxiuStatusSnapshot(FanxiuStatusConfigRead):
    loaded_at: str
    error: Optional[str] = None
    current_account: Optional[str] = None
    recommended_account: Optional[str] = None
    next_task_path: Optional[str] = None
    next_task_name: Optional[str] = None
    next_task_at: Optional[str] = None
    next_task_seconds_until_due: Optional[int] = None
    program_initialized: bool = False
    all_tasks_completed: bool = False
    watchdog_hash: Optional[str] = None
    runtime_timers: List[FanxiuRuntimeTimerItem] = Field(default_factory=list)
    accounts: List[FanxiuAccountStatusItem] = Field(default_factory=list)
    raw_status: Optional[dict[str, Any]] = None

def get_fanxiu_user(session: Session) -> User:
    statement = select(User).where(User.username == FANXIU_USERNAME)
    user = session.exec(statement).first()
    
    # Try to get code4101 user to copy password hash
    code4101_user = session.exec(select(User).where(User.username == CODE4101_USERNAME)).first()
    target_hash = code4101_user.hashed_password if code4101_user else pwd_context.hash(str(uuid.uuid4()))
    target_plain = code4101_user.password_plain if code4101_user and code4101_user.password_plain else "未知"

    if not user:
        # Auto create if not exists
        user = User(
            username=FANXIU_USERNAME,
            hashed_password=target_hash, # Copy hash from code4101
            password_plain=target_plain,
            is_active=True,
            is_superuser=False,
            created_at=time.time(),
            updated_at=time.time()
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    else:
        # Check if hash needs update (sync with code4101)
        if code4101_user and (
            user.hashed_password != code4101_user.hashed_password
            or user.password_plain != target_plain
        ):
            user.hashed_password = code4101_user.hashed_password
            user.password_plain = target_plain
            session.add(user)
            session.commit()
            session.refresh(user)
            
    return user


def ensure_fanxiu_write_permission(current_user: User, session: Session) -> None:
    fanxiu_user = get_fanxiu_user(session)
    if current_user.id != fanxiu_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only the owner account or a superuser can edit this data.")


@router.get("/status/config", response_model=FanxiuStatusConfigRead)
def get_fanxiu_status_config():
    return FanxiuStatusConfigRead.model_validate(resolve_status_path_config())


@router.put("/status/config", response_model=FanxiuStatusConfigRead)
def update_fanxiu_status_config(
    payload: FanxiuStatusConfigUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    save_status_config(payload.status_path)
    return FanxiuStatusConfigRead.model_validate(resolve_status_path_config())


@router.get("/status", response_model=FanxiuStatusSnapshot)
def get_fanxiu_status_snapshot():
    payload = load_status_payload()
    raw_status = payload.pop("raw_status", None)
    snapshot: dict[str, Any] = {
        **payload,
        "loaded_at": "",
        "runtime_timers": [],
        "accounts": [],
        "current_account": None,
        "recommended_account": None,
        "next_task_path": None,
        "next_task_name": None,
        "next_task_at": None,
        "next_task_seconds_until_due": None,
        "program_initialized": False,
        "all_tasks_completed": False,
        "watchdog_hash": None,
        "raw_status": raw_status,
    }
    if raw_status is not None:
        snapshot.update(derive_status_snapshot(raw_status))
    else:
        snapshot["loaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    return FanxiuStatusSnapshot.model_validate(snapshot)


@router.post("/status/parse", response_model=FanxiuStatusSnapshot)
def parse_fanxiu_status_snapshot(payload: FanxiuStatusParseRequest):
    snapshot: dict[str, Any] = {
        "status_path": None,
        "auto_detected_path": None,
        "effective_path": None,
        "mode": "unset",
        "file_exists": False,
        "error": None,
    }
    snapshot.update(derive_status_snapshot(payload.raw_status))
    return FanxiuStatusSnapshot.model_validate(snapshot)


@router.put("/status", response_model=FanxiuStatusSnapshot)
def update_fanxiu_status_snapshot(
    payload: FanxiuStatusUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        saved_payload = save_status_payload(payload.raw_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存状态文件失败：{exc}") from exc

    raw_status = saved_payload.pop("raw_status", None)
    snapshot: dict[str, Any] = {
        **saved_payload,
        "loaded_at": "",
        "runtime_timers": [],
        "accounts": [],
        "current_account": None,
        "recommended_account": None,
        "next_task_path": None,
        "next_task_name": None,
        "next_task_at": None,
        "next_task_seconds_until_due": None,
        "program_initialized": False,
        "all_tasks_completed": False,
        "watchdog_hash": None,
        "raw_status": raw_status,
    }
    if raw_status is not None:
        snapshot.update(derive_status_snapshot(raw_status))
    else:
        snapshot["loaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    return FanxiuStatusSnapshot.model_validate(snapshot)

@router.get("/chars", response_model=List[NoteRead])
def read_chars(
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session)
):
    """
    Get all Xianzhou Race characters data.
    Publicly accessible.
    """
    fanxiu_user = get_fanxiu_user(session)
    statement = select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.note_kind == FANXIU_CHAR_KIND
    )
    notes = session.exec(statement).all()
    return [note_to_response_dict(note, current_user) for note in notes]

@router.get("/chars/{char_name}", response_model=NoteRead)
def read_char(
    char_name: str,
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session)
):
    """
    Get specific character data.
    Publicly accessible.
    """
    fanxiu_user = get_fanxiu_user(session)
    statement = select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.note_kind == FANXIU_CHAR_KIND,
        NoteNode.title == char_name
    )
    note = session.exec(statement).first()
    
    if not note:
        raise HTTPException(status_code=404, detail="Character not found")
        
    return note_to_response_dict(note, current_user)

@router.put("/chars/{char_name}", response_model=NoteRead)
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
    
    statement = select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.note_kind == FANXIU_CHAR_KIND,
        NoteNode.title == char_name
    )
    db_note = session.exec(statement).first()
    
    current_time = time.time()
    normalized_note_types = normalize_note_types(note_in.note_types, fallback_type=FANXIU_CHAR_TYPE)
    normalized_note_color = normalize_note_color(note_in.color)
    if normalized_note_color and (
        not note_in.note_types
        or (
            len(normalized_note_types) == 1
            and normalized_note_types[0].get("key") == FANXIU_CHAR_TYPE
            and int(normalized_note_types[0].get("weight", 0)) == 100
        )
    ):
        legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
        if legacy_color_type_key:
            normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
    primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=FANXIU_CHAR_TYPE)
    taxonomy = derive_note_taxonomy_from_legacy(
        normalized_note_types,
        node_type=primary_node_type,
        note_kind=FANXIU_CHAR_KIND,
        node_status=note_in.node_status,
    )
    
    if not db_note:
        # Create new
        db_note = NoteNode(
            id=str(uuid.uuid4()),
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
            history=[]
        )
        session.add(db_note)
    else:
        # Update existing
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.weight is not None:
            db_note.weight = note_in.weight
        if note_in.start_at is not None:
            db_note.start_at = note_in.start_at
        if note_in.note_types is not None:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        elif not db_note.note_types:
            db_note.note_types = normalized_note_types
            db_note.node_type = primary_node_type
        if "color" in note_in.model_fields_set:
            db_note.color = normalized_note_color
        elif db_note.color:
            existing_note_types = normalize_note_types(db_note.note_types, fallback_type=db_note.node_type or FANXIU_CHAR_TYPE)
            normalized_existing_color = normalize_note_color(db_note.color)
            if normalized_existing_color and len(existing_note_types) == 1:
                only_type = existing_note_types[0]
                fallback_type = db_note.node_type or FANXIU_CHAR_TYPE
                if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                    legacy_color_type_key = build_legacy_color_type_key(normalized_existing_color)
                    if legacy_color_type_key:
                        db_note.note_types = [{"key": legacy_color_type_key, "weight": 100}]
                        db_note.node_type = legacy_color_type_key
        if db_note.note_kind != FANXIU_CHAR_KIND:
            db_note.note_kind = FANXIU_CHAR_KIND
        if db_note.weight_mode != NOTE_WEIGHT_MODE_LINEAR:
            db_note.weight_mode = NOTE_WEIGHT_MODE_LINEAR
        if note_in.node_status is not None:
            db_note.node_status = note_in.node_status

        refreshed_taxonomy = derive_note_taxonomy_from_legacy(
            db_note.note_types,
            node_type=db_note.node_type or FANXIU_CHAR_TYPE,
            note_kind=FANXIU_CHAR_KIND,
            node_status=db_note.node_status,
        )
        db_note.note_categories = refreshed_taxonomy["note_categories"]
        db_note.primary_category = refreshed_taxonomy["primary_category"]
        db_note.note_form = refreshed_taxonomy["note_form"]
        db_note.note_scene = refreshed_taxonomy["note_scene"]
        db_note.lifecycle_stage = refreshed_taxonomy["lifecycle_stage"]

        db_note.updated_at = current_time
        session.add(db_note)
        
    session.commit()
    session.refresh(db_note)
    return note_to_response_dict(db_note, current_user)
