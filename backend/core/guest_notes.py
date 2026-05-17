from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException
from sqlmodel import Session, select

from backend.core.auth import get_optional_current_user_from_token, get_password_hash, oauth2_scheme_optional
from backend.core.note_semantics import (
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DEFAULT,
    NOTE_KIND_DEFAULT,
    NOTE_LIFECYCLE_STAGE_DEFAULT,
    NOTE_SCENE_DEFAULT,
)
from backend.db import get_session
from backend.models import AppSetting, NoteEdge, NoteNode, User


GUEST_NOTES_USERNAME = "__guest_notes__"
GUEST_NOTES_NICKNAME = "游客公共星图"
GUEST_NOTES_SEED_SETTING_KEY = "note.guest_star_notes.seed"
GUEST_NOTES_SEED_VERSION = 2
RUANYF_WEEKLY_ISSUE_FIELD = "__ruanyf_weekly_issue_number"
RUANYF_WEEKLY_SOURCE_URL_FIELD = "__ruanyf_weekly_source_url"
RUANYF_WEEKLY_PUBLISHED_AT_FIELD = "__ruanyf_weekly_published_at"


def is_guest_notes_user(user: User | None) -> bool:
    return bool(user and user.username == GUEST_NOTES_USERNAME)


def ensure_guest_notes_user(session: Session) -> User:
    user = session.exec(select(User).where(User.username == GUEST_NOTES_USERNAME)).first()
    now = time.time()

    if user is None:
        user = User(
            username=GUEST_NOTES_USERNAME,
            nickname=GUEST_NOTES_NICKNAME,
            hashed_password=get_password_hash(secrets.token_urlsafe(32)),
            password_plain="not-login",
            email=None,
            is_active=True,
            is_superuser=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    changed = False
    if user.nickname != GUEST_NOTES_NICKNAME:
        user.nickname = GUEST_NOTES_NICKNAME
        changed = True
    if not user.is_active:
        user.is_active = True
        changed = True
    if user.is_superuser:
        user.is_superuser = False
        changed = True
    if user.password_plain != "not-login":
        user.hashed_password = get_password_hash(secrets.token_urlsafe(32))
        user.password_plain = "not-login"
        changed = True
    if changed:
        user.updated_at = now
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def _month_anchor() -> datetime:
    now = datetime.now()
    return datetime(now.year, now.month, min(now.day, 20), 9, 0, 0)


def _ts(days: int, *, hour: int = 9) -> float:
    anchor = _month_anchor() + timedelta(days=days)
    return anchor.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()


def _note(
    *,
    note_id: str,
    user_id: int,
    title: str,
    content: str,
    category: str = NOTE_CATEGORY_DEFAULT,
    form: str = NOTE_FORM_DEFAULT,
    stage: str = NOTE_LIFECYCLE_STAGE_DEFAULT,
    weight: int = 0,
    start_at: float,
    custom_fields: list[list[object]] | None = None,
) -> NoteNode:
    now = time.time()
    return NoteNode(
        id=note_id,
        user_id=user_id,
        title=title,
        content=content,
        weight=weight,
        node_type=category,
        note_types=[{"key": category, "weight": 100}],
        note_categories=[{"key": category, "weight": 100}],
        primary_category=category,
        note_form=form,
        note_kind=NOTE_KIND_DEFAULT,
        note_scene=NOTE_SCENE_DEFAULT,
        node_status=stage,
        lifecycle_stage=stage,
        private_level=0,
        custom_fields=custom_fields or [],
        created_at=now,
        updated_at=now,
        start_at=start_at,
        history=[],
    )


def _edge(user_id: int, source_id: str, target_id: str) -> NoteEdge:
    return NoteEdge(
        id=str(uuid.uuid4()),
        user_id=user_id,
        source_id=source_id,
        target_id=target_id,
        created_at=time.time(),
    )


def _load_seed_version(session: Session) -> int:
    row = session.get(AppSetting, GUEST_NOTES_SEED_SETTING_KEY)
    if row is None:
        return 0
    value = row.value
    if isinstance(value, dict):
        value = value.get("version")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _save_seed_version(session: Session) -> None:
    row = session.get(AppSetting, GUEST_NOTES_SEED_SETTING_KEY)
    if row is None:
        row = AppSetting(key=GUEST_NOTES_SEED_SETTING_KEY)
    row.value = {"version": GUEST_NOTES_SEED_VERSION}
    row.updated_at = time.time()
    session.add(row)


def ensure_guest_star_notes_seed(session: Session, user: User) -> None:
    if user.id is None:
        return

    seed_version = _load_seed_version(session)
    if seed_version >= GUEST_NOTES_SEED_VERSION:
        return

    user_id = int(user.id)
    ids = {
        "root": "guest-star-notes-root",
        "filter": "guest-star-notes-filter",
        "calendar": "guest-star-notes-calendar",
        "task": "guest-star-notes-task",
        "doc": "guest-star-notes-doc",
        "memo": "guest-star-notes-memo",
        "bug": "guest-star-notes-bug",
        "weekly": "guest-star-notes-weekly-issue-300",
    }

    existing_note_ids = {
        str(note_id)
        for note_id in session.exec(select(NoteNode.id).where(NoteNode.user_id == user_id)).all()
        if note_id
    }
    notes = [
        _note(
            note_id=ids["root"],
            user_id=user_id,
            title="星图笔记体验库",
            content="<p>这里是一组公开共享的游客示例节点。可以新建、编辑、连线、删除，所有游客看到的是同一份公共数据。</p>",
            category="project",
            form="document",
            stage="doing",
            weight=3,
            start_at=_ts(-8),
            custom_fields=[["用途", "string", "演示星图笔记的基本工作流"]],
        ),
        _note(
            note_id=ids["filter"],
            user_id=user_id,
            title="后端筛选和前端筛选",
            content="<p>后端筛选决定加载哪些节点；前端筛选只影响当前已加载结果的显示。规则按顺序执行，后面的规则可以覆盖前面的结果。</p>",
            category="module",
            form="note",
            stage="idea",
            weight=2,
            start_at=_ts(-6),
        ),
        _note(
            note_id=ids["calendar"],
            user_id=user_id,
            title="日历视图示例",
            content="<p>节点的起始时间会出现在日历里。右键日期或点击日期上的加号，可以直接创建当天节点。</p>",
            category="general",
            form="memo",
            stage="done",
            weight=1,
            start_at=_ts(-2, hour=18),
            custom_fields=[["完成度", "number", 1]],
        ),
        _note(
            note_id=ids["task"],
            user_id=user_id,
            title="试着拖一条连线",
            content="<p>在星图里从一个节点拖到另一个节点，可以创建有向关系。删除边和删除节点都会实时写入公共体验库。</p>",
            category="task",
            form="note",
            stage="todo",
            weight=1,
            start_at=_ts(1),
        ),
        _note(
            note_id=ids["doc"],
            user_id=user_id,
            title="文档型节点",
            content="<h2>文档型节点</h2><p>较长的说明、读书笔记、方案草稿可以放成文档形态；普通节点和文档节点仍然共享同一套分类、权重和关系。</p>",
            category="general",
            form="document",
            stage="idea",
            weight=2,
            start_at=_ts(4),
        ),
        _note(
            note_id=ids["memo"],
            user_id=user_id,
            title="快速备忘",
            content="<p>备忘适合短句记录。后续可以把它连到项目、任务或文档上。</p>",
            category="general",
            form="memo",
            stage="idea",
            weight=0,
            start_at=_ts(6, hour=21),
        ),
        _note(
            note_id=ids["bug"],
            user_id=user_id,
            title="示例问题：分类还可以更精细",
            content="<p>这是一个缺陷类节点，用来演示不同分类、阶段和颜色在列表与星图里的区别。</p>",
            category="bug",
            form="note",
            stage="todo",
            weight=1,
            start_at=_ts(9),
        ),
        _note(
            note_id=ids["weekly"],
            user_id=user_id,
            title="科技爱好者周刊（第 300 期）：三十年，解决人生三大问题",
            content=(
                '<p><a href="https://github.com/ruanyf/weekly/blob/master/docs/issue-300.md" '
                'target="_blank" rel="noopener noreferrer">'
                "https://github.com/ruanyf/weekly/blob/master/docs/issue-300.md"
                "</a></p>"
            ),
            category="general",
            form="document",
            stage="done",
            weight=1,
            start_at=_ts(-4),
            custom_fields=[
                [RUANYF_WEEKLY_ISSUE_FIELD, "number", 300],
                [RUANYF_WEEKLY_SOURCE_URL_FIELD, "string", "https://github.com/ruanyf/weekly/blob/master/docs/issue-300.md"],
                [RUANYF_WEEKLY_PUBLISHED_AT_FIELD, "string", "2024-05-17T00:00:00Z"],
            ],
        ),
    ]
    added_note_ids: set[str] = set()
    for note in notes:
        if str(note.id) in existing_note_ids:
            continue
        session.add(note)
        added_note_ids.add(str(note.id))

    available_seed_note_ids = existing_note_ids | added_note_ids
    existing_edges = {
        (str(edge.source_id), str(edge.target_id))
        for edge in session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    }

    for source_key, target_key in [
        ("root", "filter"),
        ("root", "calendar"),
        ("root", "task"),
        ("root", "doc"),
        ("root", "weekly"),
        ("task", "memo"),
        ("filter", "bug"),
    ]:
        source_id = ids[source_key]
        target_id = ids[target_key]
        if source_id not in available_seed_note_ids or target_id not in available_seed_note_ids:
            continue
        if (source_id, target_id) in existing_edges:
            continue
        session.add(_edge(user_id, ids[source_key], ids[target_key]))

    _save_seed_version(session)
    session.commit()


async def get_current_active_or_guest_notes_user(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    current_user: Optional[User] = Depends(get_optional_current_user_from_token),
    session: Session = Depends(get_session),
) -> User:
    if current_user is not None:
        if not current_user.is_active:
            raise HTTPException(status_code=400, detail="Inactive user")
        return current_user

    if token:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    guest_user = ensure_guest_notes_user(session)
    ensure_guest_star_notes_seed(session, guest_user)
    return guest_user
