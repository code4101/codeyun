from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session

from backend.core.auth import get_current_active_user
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.ocr_preview import OcrPreviewError, run_paddle_ocr_preview
from backend.core.stock import (
    EastmoneyTradeError,
    get_latest_asset_snapshot,
    import_mobile_trade_detail_record,
    list_fund_flow_categories,
    list_fund_flow_filter_options,
    list_fund_flow_records,
    list_latest_position_snapshots,
    list_sync_runs,
    list_trade_records,
    open_trade_account_page,
    read_trade_snapshot,
    refresh_eastmoney_sheet_workbook,
    snapshot_to_dict,
    sync_trade_data,
)
from backend.core.stock.eastmoney_ocr import parse_mobile_trade_detail_from_ocr_document
from backend.db import get_session
from backend.models import User


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("notes.eastmoney"))],
)


class EastmoneySyncRequest(BaseModel):
    start_date: str | None = PydanticField(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = PydanticField(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.get("/trade-snapshot")
def get_trade_snapshot(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    try:
        return snapshot_to_dict(read_trade_snapshot(start_date=start_date, end_date=end_date))
    except EastmoneyTradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取东方财富交易页失败：{exc}") from exc


@router.post("/trade-account/open")
def open_eastmoney_trade_account_page():
    try:
        return open_trade_account_page()
    except EastmoneyTradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"打开东方财富交易页失败：{exc}") from exc


@router.post("/sync")
def sync_eastmoney_trade_data(
    payload: EastmoneySyncRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    try:
        run = sync_trade_data(
            session,
            user_id=int(current_user.id),
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
        return {
            **run,
            "sheet_workbook": refresh_eastmoney_sheet_workbook(
                session,
                user_id=int(current_user.id),
                actor_user_id=int(current_user.id),
            ),
        }
    except EastmoneyTradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"同步东方财富数据失败：{exc}") from exc


@router.post("/sheet-workbook/refresh")
def refresh_eastmoney_sheet_file(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return refresh_eastmoney_sheet_workbook(
        session,
        user_id=int(current_user.id),
        actor_user_id=int(current_user.id),
    )


@router.post("/trade-detail/import/ocr")
async def import_eastmoney_trade_detail_from_ocr(
    image: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="请粘贴图片截图")

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        row, lines = parse_mobile_trade_detail_from_ocr_document(preview.get("document") or {})
        result = import_mobile_trade_detail_record(
            session,
            user_id=int(current_user.id),
            row=row,
            ocr_lines=lines,
        )
        return {
            **result,
            "sheet_workbook": refresh_eastmoney_sheet_workbook(
                session,
                user_id=int(current_user.id),
                actor_user_id=int(current_user.id),
            ),
        }
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (EastmoneyTradeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"导入东方财富截图失败：{exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.get("/trade-records")
def get_local_trade_records(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    source: str | None = Query(default=None),
    security_code: str | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_trade_records(
        session,
        user_id=int(current_user.id),
        start_date=start_date,
        end_date=end_date,
        source=source,
        security_code=security_code,
        limit=limit,
        offset=offset,
    )


@router.get("/fund-flows")
def get_local_fund_flow_records(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    flow_category: str | None = Query(default=None),
    security_code: str | None = Query(default=None),
    security_name: str | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_fund_flow_records(
        session,
        user_id=int(current_user.id),
        start_date=start_date,
        end_date=end_date,
        flow_category=flow_category,
        security_code=security_code,
        security_name=security_name,
        limit=limit,
        offset=offset,
    )


@router.get("/fund-flow-categories")
def get_local_fund_flow_categories(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return {"items": list_fund_flow_categories(session, user_id=int(current_user.id))}


@router.get("/fund-flow-filter-options")
def get_local_fund_flow_filter_options(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_fund_flow_filter_options(session, user_id=int(current_user.id))


@router.get("/sync-runs")
def get_eastmoney_sync_runs(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return {"items": list_sync_runs(session, user_id=int(current_user.id), limit=limit)}


@router.get("/asset-snapshot/latest")
def get_latest_eastmoney_asset_snapshot(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return {"item": get_latest_asset_snapshot(session, user_id=int(current_user.id))}


@router.get("/positions/latest")
def get_latest_eastmoney_positions(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_latest_position_snapshots(session, user_id=int(current_user.id))
