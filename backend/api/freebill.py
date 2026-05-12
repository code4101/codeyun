from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.auth import get_current_active_user
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.freebill import (
    get_freebill_dashboard,
    get_freebill_status,
    import_alipay_csv_bytes,
    import_wechat_excel_bytes,
    list_freebill_filter_options,
    list_freebill_records,
)
from backend.core.freebill_sheet import get_freebill_sheet_workbook, refresh_freebill_sheet_workbook
from backend.db import get_session
from backend.models import User


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("notes.freebill"))],
)


FreebillProgramRuleAction = Literal["include", "exclude", "filter"]
FreebillProgramMatcherKind = Literal["all", "none", "field", "full_text_contains"]
FreebillProgramOperator = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "gte",
    "lte",
    "between",
]


class FreebillProgramMatcher(BaseModel):
    kind: FreebillProgramMatcherKind = "all"
    field: str | None = None
    op: FreebillProgramOperator | None = None
    value: Any = None
    values: list[Any] = Field(default_factory=list)
    ignore_case: bool = True


class FreebillProgramRule(BaseModel):
    action: FreebillProgramRuleAction = "include"
    matcher: FreebillProgramMatcher = Field(default_factory=FreebillProgramMatcher)


class FreebillProgramChannel(BaseModel):
    default: bool = False
    rules: list[FreebillProgramRule] = Field(default_factory=list)


class FreebillDashboardProgramRequest(BaseModel):
    program: FreebillProgramChannel = Field(default_factory=FreebillProgramChannel)
    trend_granularity: Literal["day", "week", "month", "year"] = "month"


@router.get("/status")
def get_status():
    return get_freebill_status()


@router.get("/dashboard")
def get_dashboard(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    source: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None),
    trend_granularity: Literal["day", "week", "month", "year"] = Query(default="month"),
):
    return get_freebill_dashboard(
        start_date=start_date,
        end_date=end_date,
        source=source,
        direction=direction,
        category=category,
        q=q,
        trend_granularity=trend_granularity,
    )


@router.post("/dashboard-program")
def get_dashboard_by_program(payload: FreebillDashboardProgramRequest):
    try:
        return get_freebill_dashboard(
            program=payload.program.model_dump(),
            trend_granularity=payload.trend_granularity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/records")
def get_records(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    source: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=80, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return list_freebill_records(
        start_date=start_date,
        end_date=end_date,
        source=source,
        direction=direction,
        category=category,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/filter-options")
def get_filter_options():
    return list_freebill_filter_options()


@router.get("/sheet-workbook")
def get_freebill_sheet_file(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return get_freebill_sheet_workbook(session, user_id=int(current_user.id))


@router.post("/sheet-workbook/refresh")
def refresh_freebill_sheet_file(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return refresh_freebill_sheet_workbook(
        session,
        user_id=int(current_user.id),
        actor_user_id=int(current_user.id),
    )


@router.post("/import/{source}")
async def import_files(
    source: Literal["alipay", "wechat"],
    files: list[UploadFile] = File(...),
    header_row: int = Query(default=24, ge=0, le=200),
):
    if not files:
        raise HTTPException(status_code=400, detail="请上传账单文件")

    results: list[dict] = []
    for upload_file in files:
        filename = upload_file.filename or "账单文件"
        try:
            content = await upload_file.read()
            if not content:
                raise ValueError("文件内容为空")
            if source == "alipay":
                result = import_alipay_csv_bytes(filename, content, header_row=header_row)
            else:
                result = import_wechat_excel_bytes(filename, content)
            results.append({"status": "success", **result})
        except Exception as exc:
            results.append(
                {
                    "status": "error",
                    "filename": filename,
                    "processed": 0,
                    "inserted": 0,
                    "skipped": 0,
                    "error": str(exc),
                }
            )
        finally:
            await upload_file.close()

    return {
        "source": source,
        "results": results,
        "processed": sum(int(item.get("processed") or 0) for item in results),
        "inserted": sum(int(item.get("inserted") or 0) for item in results),
        "skipped": sum(int(item.get("skipped") or 0) for item in results),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
    }
