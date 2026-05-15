from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.auth import get_current_active_user
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.freebill import (
    clear_freebill_record_overrides,
    get_freebill_dashboard,
    get_freebill_status,
    import_alipay_csv_bytes,
    import_ccb_excel_bytes,
    import_wechat_excel_bytes,
    list_freebill_interpret_rules,
    list_freebill_category_branch_records,
    list_freebill_filter_options,
    list_freebill_records,
    recompute_freebill_interpretation,
    save_freebill_interpret_rules,
    upsert_freebill_category_branch_overrides,
    upsert_freebill_category_branch_manual_overrides,
    upsert_freebill_record_manual_overrides,
    upsert_freebill_record_overrides,
)
from backend.core.freebill_sheet import get_freebill_sheet_workbook, refresh_freebill_sheet_workbook
from backend.db import get_session
from backend.models import User


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("notes.freebill"))],
)


FreebillProgramRuleAction = Literal["include", "exclude", "filter"]
FreebillProgramMatcherKind = Literal["all", "none", "field", "full_text_contains"]
FreebillInterpretRuleMatcherKind = Literal["all", "none", "field", "full_text_contains"]
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
    "year",
]
FreebillInterpretRuleOperator = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "gt",
    "gte",
    "lt",
    "lte",
]
FreebillCategoryDimension = Literal[
    "standard_direction",
    "standard_nature",
    "type",
    "counterparty",
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


class FreebillInterpretRuleMatcher(BaseModel):
    kind: FreebillInterpretRuleMatcherKind = "field"
    field: str | None = "product_name"
    op: FreebillInterpretRuleOperator | None = "contains"
    value: Any = None
    values: list[Any] = Field(default_factory=list)
    ignore_case: bool = True


class FreebillInterpretRuleItem(BaseModel):
    id: int | None = None
    name: str = ""
    enabled: bool = True
    order_index: int = 0
    matcher: FreebillInterpretRuleMatcher = Field(default_factory=FreebillInterpretRuleMatcher)
    set_direction: Literal["支出", "收支", "收入"] | None = None
    set_nature: Literal["常规", "借贷", "理财", "转账", "流水"] | None = None
    note: str | None = None


class FreebillInterpretRuleSettings(BaseModel):
    signed_category_values: bool = False
    built_in_rules: dict[str, bool] = Field(default_factory=dict)


class FreebillInterpretRulesRequest(BaseModel):
    rules: list[FreebillInterpretRuleItem] = Field(default_factory=list)
    settings: FreebillInterpretRuleSettings = Field(default_factory=FreebillInterpretRuleSettings)


class FreebillDashboardProgramRequest(BaseModel):
    program: FreebillProgramChannel = Field(default_factory=FreebillProgramChannel)
    programs: list[FreebillProgramChannel] = Field(default_factory=list)
    trend_granularity: Literal["day", "week", "month", "year"] = "month"
    trend_standard_nature: Literal["常规", "借贷", "理财", "转账", "流水"] | None = None
    category_dimensions: list[FreebillCategoryDimension] = Field(default_factory=list)


class FreebillCategoryPathItem(BaseModel):
    dimension: FreebillCategoryDimension
    value: str


class FreebillCategoryBranchRecordsRequest(BaseModel):
    program: FreebillProgramChannel = Field(default_factory=FreebillProgramChannel)
    programs: list[FreebillProgramChannel] = Field(default_factory=list)
    path: list[FreebillCategoryPathItem] = Field(default_factory=list)
    direction: str | None = None
    category: str | None = None
    counterparty: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)
    sort_by: Literal["amount", "create_time", "source", "product_name", "remark"] = "amount"
    sort_order: Literal["asc", "desc"] = "desc"


class FreebillRecordOverrideRequest(BaseModel):
    trade_nos: list[str] = Field(min_length=1)
    direction: str = "不计收支"
    category: str = "流水"
    note: str | None = None


class FreebillCategoryBranchOverrideRequest(FreebillCategoryBranchRecordsRequest):
    override_direction: str = "不计收支"
    override_category: str = "流水"
    note: str | None = None


class FreebillRecordOverrideClearRequest(BaseModel):
    trade_nos: list[str] = Field(min_length=1)


class FreebillRecordManualOverrideRequest(BaseModel):
    trade_no: str
    overrides: dict[str, object] = Field(default_factory=dict)
    note: str | None = None


class FreebillCategoryBranchManualOverrideRequest(FreebillCategoryBranchRecordsRequest):
    overrides: dict[str, object] = Field(default_factory=dict)
    note: str | None = None


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
    trend_standard_nature: Literal["常规", "借贷", "理财", "转账", "流水"] | None = Query(default=None),
):
    return get_freebill_dashboard(
        start_date=start_date,
        end_date=end_date,
        source=source,
        direction=direction,
        category=category,
        q=q,
        trend_granularity=trend_granularity,
        trend_standard_nature=trend_standard_nature,
    )


@router.post("/dashboard-program")
def get_dashboard_by_program(payload: FreebillDashboardProgramRequest):
    try:
        return get_freebill_dashboard(
            program=payload.program.model_dump(),
            programs=[program.model_dump() for program in payload.programs] or None,
            trend_granularity=payload.trend_granularity,
            trend_standard_nature=payload.trend_standard_nature,
            category_dimensions=payload.category_dimensions or None,
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


@router.post("/category-branch-records")
def get_category_branch_records(payload: FreebillCategoryBranchRecordsRequest):
    try:
        return list_freebill_category_branch_records(
            program=payload.program.model_dump(),
            programs=[program.model_dump() for program in payload.programs] or None,
            path=[item.model_dump() for item in payload.path] or None,
            direction=payload.direction,
            category=payload.category,
            counterparty=payload.counterparty,
            limit=payload.limit,
            offset=payload.offset,
            sort_by=payload.sort_by,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/filter-options")
def get_filter_options():
    return list_freebill_filter_options()


@router.get("/interpret-rules")
def get_interpret_rules():
    return list_freebill_interpret_rules()


@router.put("/interpret-rules")
def save_interpret_rules(payload: FreebillInterpretRulesRequest):
    try:
        return save_freebill_interpret_rules(
            [item.model_dump() for item in payload.rules],
            settings=payload.settings.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/interpret-rules/recompute")
def recompute_interpret_rules():
    return recompute_freebill_interpretation()


@router.post("/record-overrides")
def apply_record_overrides(payload: FreebillRecordOverrideRequest):
    try:
        return upsert_freebill_record_overrides(
            payload.trade_nos,
            direction=payload.direction,
            category=payload.category,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/record-manual-overrides")
def apply_record_manual_overrides(payload: FreebillRecordManualOverrideRequest):
    try:
        return upsert_freebill_record_manual_overrides(
            payload.trade_no,
            payload.overrides,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/category-branch-overrides")
def apply_category_branch_overrides(payload: FreebillCategoryBranchOverrideRequest):
    try:
        return upsert_freebill_category_branch_overrides(
            program=payload.program.model_dump(),
            programs=[program.model_dump() for program in payload.programs] or None,
            path=[item.model_dump() for item in payload.path] or None,
            direction=payload.direction,
            category=payload.category,
            counterparty=payload.counterparty,
            override_direction=payload.override_direction,
            override_category=payload.override_category,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/category-branch-manual-overrides")
def apply_category_branch_manual_overrides(payload: FreebillCategoryBranchManualOverrideRequest):
    try:
        return upsert_freebill_category_branch_manual_overrides(
            program=payload.program.model_dump(),
            programs=[program.model_dump() for program in payload.programs] or None,
            path=[item.model_dump() for item in payload.path] or None,
            overrides=payload.overrides,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/record-overrides/clear")
def clear_record_overrides(payload: FreebillRecordOverrideClearRequest):
    try:
        return clear_freebill_record_overrides(payload.trade_nos)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    source: Literal["alipay", "wechat", "ccb"],
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
            elif source == "wechat":
                result = import_wechat_excel_bytes(filename, content)
            else:
                result = import_ccb_excel_bytes(filename, content)
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
