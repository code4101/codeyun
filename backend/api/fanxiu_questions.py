"""灵泉题库 HTTP 适配；功能访问控制由父路由统一组合。"""
from fastapi import APIRouter
from typing import (
    Any,
)
from fastapi import (
    Depends,
    HTTPException,
    Query,
)
from backend.models import (
    FanxiuChoiceKnowledge,
    User,
)
from sqlmodel import (
    Session,
)
from backend.core.fanxiu.quiz.store import (
    create_lingquan_question,
    list_lingquan_questions,
    serialize_question,
    update_lingquan_question,
)
from backend.api.fanxiu_access import (
    ensure_fanxiu_write_permission,
)
from backend.core.access.auth import (
    get_current_active_user,
)
from backend.db import (
    get_session,
)

status_router = APIRouter()

@status_router.get("/lingquan-questions")
def get_lingquan_questions(
    query: str = Query(default=""),
    group_name: str = Query(default=""),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    del current_user
    items = list_lingquan_questions(session, query=query, group_name=group_name)
    groups: dict[str, int] = {}
    for item in list_lingquan_questions(session):
        groups[item.group_name] = groups.get(item.group_name, 0) + 1
    return {
        "items": [serialize_question(item) for item in items],
        "groups": [{"name": name, "count": count} for name, count in sorted(groups.items())],
        "total": len(items),
    }

@status_router.post("/lingquan-questions")
def post_lingquan_question(
    payload: dict[str, Any],
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return serialize_question(create_lingquan_question(session, payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@status_router.put("/lingquan-questions/{question_id}")
def put_lingquan_question(
    question_id: str,
    payload: dict[str, Any],
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_fanxiu_write_permission(current_user, session)
    item = session.get(FanxiuChoiceKnowledge, question_id)
    if item is None or item.domain != "lingquan":
        raise HTTPException(status_code=404, detail="灵泉题目不存在")
    try:
        return serialize_question(update_lingquan_question(session, item, payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@status_router.delete("/lingquan-questions/{question_id}")
def delete_lingquan_question(
    question_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    ensure_fanxiu_write_permission(current_user, session)
    item = session.get(FanxiuChoiceKnowledge, question_id)
    if item is None or item.domain != "lingquan":
        raise HTTPException(status_code=404, detail="灵泉题目不存在")
    session.delete(item)
    session.commit()
    from backend.core.fanxiu.choice_knowledge.catalog import choice_knowledge_catalog

    choice_knowledge_catalog.remove(question_id)
    return {"ok": True}
