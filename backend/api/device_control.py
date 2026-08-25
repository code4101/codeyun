import os
import socket
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional

import psutil
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, func, select

from backend.db import get_session
from backend.core.attendance.service import apply_attendance_order_operation_password_env
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
from backend.core.access.auth import verify_api_token
from backend.core.attendance.order import (
    OrderAutomationError,
    execute_order_action,
    query_order_refund_details,
)
from backend.core.devices.device import (
    device_manager,
    get_device_id,
    match_cmdline,
)
from backend.core.services.launcher import popen_service
from backend.core.devices.ui_automation import ensure_ui_automation_thread_context
from backend.core.devices.trusted_python_runs import get_trusted_python_run, start_trusted_python_run
from backend.core.attendance.clockin_link_detector import (
    detect_clockin_links_browser,
    detect_xiaoe_attendance_clockin_activities_browser,
)
ensure_attendance_engine_importable()

from xlsln.kq5034.engine.fanbei_schedule import (
    FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID,
    FANBEI_ATTENDANCE_COURSE_NAME,
    _run_fanbei_attendance_step2_local,
    run_fanbei_attendance_step3_for_sheet,
)
from xlsln.kq5034.engine.fanbei_course_sheets import (
    FANBEI_WORKBOOK_NUMERIC_ID,
    materialize_fanbei_course_sheets,
    rebuild_fanbei_attendance_from_course_sheets,
)
from xlsln.kq5034.engine.nianzhu_schedule import (
    NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID,
    NIANZHU_CHUANGGUAN_COURSE_NAME,
)
from xlsln.kq5034.engine.nianzhu_course_sheets import (
    NIANZHU_WORKBOOK_NUMERIC_ID,
    compact_nianzhu_course_sheet_step2,
    materialize_nianzhu_course_sheets,
    rebuild_nianzhu_attendance_from_course_sheets,
    run_nianzhu_course_sheet_step1,
    sync_progressive_zen_catalog,
)
from xlsln.kq5034.resource_cache import (
    lookup_resource_cache,
    store_resource_cache,
)
from xlsln.kq5034.engine.master_data import (
    PAYMENT_DATASET,
    USER_DATASET,
    ingest_master_data_file,
)
from xlsln.kq5034.engine.models import (
    AttendanceDataImport,
    AttendancePaymentLedger,
    AttendancePaymentOrder,
    AttendanceUser,
)
from xlsln.kq5034.engine.db import get_engine as get_attendance_engine
from xlsln.kq5034.engine.client import (
    AttendanceStorageError,
    AttendanceVersionConflict,
    LocalAttendanceSheetClient,
)
from xlsln.kq5034.engine.job_runs import latest_job_run as latest_attendance_job_run
from kq5034.attendance_api import (
    lookup_registration_users_browser,
)

router = APIRouter(dependencies=[Depends(verify_api_token)])


class MatchProcessItem(BaseModel):
    id: str
    command: str


class MatchProcessesRequest(BaseModel):
    tasks: List[MatchProcessItem]


@router.post("/match_processes")
def match_processes(req: MatchProcessesRequest):
    results = {}

    procs = []
    try:
        procs = list(
            psutil.process_iter(
                ["pid", "name", "cmdline", "create_time", "cpu_percent", "memory_info"]
            )
        )
    except Exception:
        pass

    used_pids = set()

    for task in req.tasks:
        found = False
        for proc in procs:
            if proc.pid in used_pids:
                continue

            try:
                cmdline = proc.info["cmdline"]
                if not cmdline:
                    continue

                if match_cmdline(task.command, cmdline):
                    found = True
                    used_pids.add(proc.pid)

                    try:
                        mem = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
                    except Exception:
                        mem = 0

                    results[task.id] = {
                        "id": task.id,
                        "running": True,
                        "pid": proc.pid,
                        "started_at": proc.info["create_time"],
                        "cpu_percent": proc.info["cpu_percent"],
                        "memory_rss": mem,
                    }
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not found:
            results[task.id] = {"id": task.id, "running": False}

    return results


class ExecCmdRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None


class PythonRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["script", "module_call"] = "script"
    script: str = ""
    module: str = ""
    callable_name: str = Field(default="", alias="callable")
    args: List[Any] = Field(default_factory=list)
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    async_run: bool = Field(default=False, alias="async")
    timeout: int = Field(default=3600, ge=1, le=86400)


@router.post("/exec_cmd")
def execute_command(req: ExecCmdRequest):
    try:
        run_env = os.environ.copy()
        if req.env:
            run_env.update(req.env)

        import shlex

        try:
            cmd_args = shlex.split(req.command, posix=(sys.platform != "win32"))
        except Exception:
            cmd_args = req.command.split()

        proc = popen_service(
            cmd_args,
            cwd=req.cwd,
            env=run_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return {
            "status": "started",
            "pid": proc.pid,
            "message": "Process started. Note: Log streaming for raw exec_cmd is not yet implemented.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/python-runs")
def create_python_run(req: PythonRunRequest):
    try:
        return start_trusted_python_run(
            mode=req.mode,
            script=req.script,
            module=req.module,
            callable_name=req.callable_name,
            args=req.args,
            kwargs=req.kwargs,
            cwd=req.cwd,
            env=req.env,
            async_run=req.async_run,
            timeout=req.timeout,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/python-runs/{run_id}")
def get_python_run(run_id: str):
    try:
        return get_trusted_python_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="运行记录不存在") from exc


@router.get("/status")
def get_device_control_status():
    hostname = socket.gethostname()
    device_id = get_device_id()

    local_dev = device_manager.get_device(device_id)
    python_exec = None
    if local_dev:
        python_exec = local_dev.python_exec

    return {
        "status": "ok",
        "hostname": hostname,
        "platform": sys.platform,
        "id": device_id,
        "python_exec": python_exec,
    }


class RenameRequest(BaseModel):
    name: str


@router.post("/rename")
def rename_device(req: RenameRequest):
    _ = req
    raise HTTPException(status_code=400, detail="本机设备名称请通过用户入口别名管理，不再支持设备面重命名")


class ConfigRequest(BaseModel):
    python_exec: Optional[str] = None


@router.post("/config")
def update_device_control_config(req: ConfigRequest):
    _ = req
    raise HTTPException(status_code=400, detail="本机运行配置不再支持通过该接口持久化")


class AttendanceOrderExecuteRequest(BaseModel):
    action: Literal["inspect", "refund"]
    rows: List[dict[str, Any]] = Field(default_factory=list)
    login_users: List[str] = Field(default_factory=list)
    lookup_mode: Literal["hybrid", "db_only", "browser_only"] = "browser_only"
    operation_password: Optional[str] = None


class AttendanceOrderRefundDetailRequest(BaseModel):
    order_id: str
    query_type: Literal["auto", "pay_order", "merchant_order", "refund_id"] = "auto"
    login_users: List[str] = Field(default_factory=list)


class AttendanceUserMatchLookupItem(BaseModel):
    key: str
    names: List[str] = Field(default_factory=list)
    phones: List[str] = Field(default_factory=list)


class AttendanceUserMatchLookupRequest(BaseModel):
    course_name: str = ""
    course_product_name: str = ""
    shop_id: int = 1
    close_browser: bool = True
    items: List[AttendanceUserMatchLookupItem] = Field(default_factory=list)


class AttendanceResourceCacheLookupRequest(BaseModel):
    resource_type: Literal["video", "clockin"]
    shop_id: int
    resource_url: str
    options: Dict[str, Any] = Field(default_factory=dict)


class AttendanceResourceCacheStoreRequest(AttendanceResourceCacheLookupRequest):
    content_base64: str = ""
    suffix: str = ".bin"
    empty: bool = False


class AttendanceFanbeiStep3Request(BaseModel):
    sheet_id: int = FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID
    course_name: str = FANBEI_ATTENDANCE_COURSE_NAME


class AttendanceFanbeiCourseSheetsRequest(BaseModel):
    workbook_id: int = FANBEI_WORKBOOK_NUMERIC_ID
    attendance_sheet_id: int = FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID
    course_name: str = FANBEI_ATTENDANCE_COURSE_NAME
    replace: bool = False
    rebuild: bool = False


class AttendanceFanbeiRebuildFromSheetsRequest(BaseModel):
    attendance_sheet_id: int = FANBEI_ATTENDANCE_ATTENDANCE_SHEET_ID
    user_alias_map: Dict[str, str] = Field(default_factory=dict)


class AttendanceNianzhuStep3Request(BaseModel):
    sheet_id: int = NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID
    course_name: str = NIANZHU_CHUANGGUAN_COURSE_NAME
    include_frozen: bool = False


class AttendanceNianzhuStep1Request(BaseModel):
    workbook_id: int = NIANZHU_WORKBOOK_NUMERIC_ID
    attendance_sheet_id: int = NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID
    course_name: str = NIANZHU_CHUANGGUAN_COURSE_NAME
    shop_id: int = 1
    update_lessons: bool = True
    update_clockins: bool = True
    clockin_pattern: str = ""
    # 课程事故专用补数据插件，默认必须为空；新课程配置不要继承旧课程的插件值。
    dynamic_clockin_plugin: str = ""
    close_browser: bool = True


class AttendanceNianzhuStep0CatalogSyncRequest(BaseModel):
    attendance_sheet_id: int
    course_name: str
    catalog_url: str
    catalog_weeks: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    expected_lesson_count: Optional[int] = None


class AttendanceNianzhuStep2Request(BaseModel):
    attendance_sheet_id: int = NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID
    course_name: str = NIANZHU_CHUANGGUAN_COURSE_NAME
    rebuild: bool = False
    include_frozen: bool = False


class AttendanceNianzhuCourseSheetsRequest(BaseModel):
    workbook_id: int = NIANZHU_WORKBOOK_NUMERIC_ID
    attendance_sheet_id: int = NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID
    course_name: str = NIANZHU_CHUANGGUAN_COURSE_NAME
    replace: bool = False
    rebuild: bool = False
    include_frozen: bool = False


class AttendanceNianzhuRebuildFromSheetsRequest(BaseModel):
    attendance_sheet_id: int = NIANZHU_CHUANGGUAN_ATTENDANCE_SHEET_ID
    course_name: str = NIANZHU_CHUANGGUAN_COURSE_NAME
    include_frozen: bool = False


class AttendanceClockinLinkDetectRequest(BaseModel):
    root_url: str
    targets: List[str] = Field(default_factory=list)
    provider_id: str = "codex-cli"
    model: str = "gpt-5.3-codex-spark"
    close_tabs: bool = True


class AttendanceXiaoeClockinActivityDetectRequest(BaseModel):
    target_keywords: List[str] = Field(default_factory=list)
    shop_name: str = "5034山中薪"
    close_browser: bool = False


class AttendanceIndependentTablePatchRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    operations: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/attendance/independent/status")
def get_independent_attendance_status():
    from xlsln.kq5034 import service as attendance_service

    return attendance_service.status()


@router.get("/attendance/independent/job-runs/latest")
def get_latest_independent_attendance_job_run():
    return latest_attendance_job_run()


@router.get("/attendance/independent/sheets/{sheet_id}/table")
def get_independent_attendance_sheet_table(
    sheet_id: int,
    workbook_id: int | None = Query(default=None, ge=1),
    include_grid: bool = False,
):
    try:
        return LocalAttendanceSheetClient().get_table(
            SimpleNamespace(sheet_id=sheet_id, workbook_id=workbook_id),
            include_grid=include_grid,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/attendance/independent/sheets/{sheet_id}")
def get_independent_attendance_sheet_document(
    sheet_id: int,
    workbook_id: int | None = Query(default=None, ge=1),
):
    """Expose the authoritative attendance document to the thin CodeYun UI shell."""
    try:
        return LocalAttendanceSheetClient().get_document(
            SimpleNamespace(sheet_id=sheet_id, workbook_id=workbook_id),
        )
    except AttendanceStorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/attendance/independent/sheets/{sheet_id}/table")
def patch_independent_attendance_sheet_table(
    sheet_id: int,
    payload: AttendanceIndependentTablePatchRequest,
    workbook_id: int | None = Query(default=None, ge=1),
):
    try:
        return LocalAttendanceSheetClient().patch_table(
            SimpleNamespace(sheet_id=sheet_id, workbook_id=workbook_id),
            payload.operations,
            expected_version=payload.expected_version,
        )
    except AttendanceVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/order/execute")
def execute_attendance_order(req: AttendanceOrderExecuteRequest):
    try:
        with ensure_ui_automation_thread_context():
            with apply_attendance_order_operation_password_env(req.operation_password):
                return execute_order_action(
                    action=req.action,
                    rows=req.rows,
                    weipay_login_users=req.login_users,
                    lookup_mode=req.lookup_mode,
                )
    except OrderAutomationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/resource-cache/lookup")
def lookup_attendance_resource_cache(req: AttendanceResourceCacheLookupRequest):
    try:
        return lookup_resource_cache(
            resource_type=req.resource_type,
            shop_id=req.shop_id,
            resource_url=req.resource_url,
            options=req.options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/resource-cache/store")
def store_attendance_resource_cache(req: AttendanceResourceCacheStoreRequest):
    try:
        return store_resource_cache(
            resource_type=req.resource_type,
            shop_id=req.shop_id,
            resource_url=req.resource_url,
            options=req.options,
            content_base64=req.content_base64,
            suffix=req.suffix,
            empty=req.empty,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/master-data/import")
async def import_attendance_master_data(
    dataset_type: Literal["xiaoe_users", "wechat_pay_ledger"] = Form(...),
    scope_key: str = Form(...),
    collector_device: str = Form(default=""),
    collected_at: float = Form(default=0),
    file: UploadFile = File(...),
):
    try:
        content = await file.read()
        with Session(get_attendance_engine()) as session:
            return ingest_master_data_file(
                session,
                dataset_type=dataset_type,
                scope_key=scope_key,
                source_filename=file.filename or "source.csv",
                content=content,
                collector_device=collector_device,
                collected_at=collected_at,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await file.close()


@router.get("/attendance/master-data/status")
def get_attendance_master_data_status():
    with Session(get_attendance_engine()) as session:
        latest_imports = {}
        for dataset_type in (USER_DATASET, PAYMENT_DATASET):
            item = session.exec(
                select(AttendanceDataImport)
                .where(AttendanceDataImport.dataset_type == dataset_type)
                .order_by(AttendanceDataImport.received_at.desc())
            ).first()
            latest_imports[dataset_type] = (
                {
                    "import_id": item.id,
                    "scope_key": item.scope_key,
                    "status": item.status,
                    "received_at": item.received_at,
                    "completed_at": item.completed_at,
                    "total_rows": item.total_rows,
                    "inserted_rows": item.inserted_rows,
                    "updated_rows": item.updated_rows,
                    "unchanged_rows": item.unchanged_rows,
                    "conflict_rows": item.conflict_rows,
                    "error_summary": item.error_summary,
                }
                if item is not None
                else None
            )
        return {
            "users": int(session.exec(select(func.count()).select_from(AttendanceUser)).one()),
            "payment_ledger": int(session.exec(select(func.count()).select_from(AttendancePaymentLedger)).one()),
            "payment_orders": int(session.exec(select(func.count()).select_from(AttendancePaymentOrder)).one()),
            "latest_imports": latest_imports,
        }


@router.post("/attendance/fanbei/step2")
def run_attendance_fanbei_step2():
    try:
        message = _run_fanbei_attendance_step2_local()
        return {"message": message}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/fanbei/step3")
def run_attendance_fanbei_step3(req: AttendanceFanbeiStep3Request | None = None):
    req = req or AttendanceFanbeiStep3Request()
    try:
        return run_fanbei_attendance_step3_for_sheet(
            sheet_id=req.sheet_id,
            course_name=req.course_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/fanbei/course-sheets")
def materialize_attendance_fanbei_course_sheets(req: AttendanceFanbeiCourseSheetsRequest | None = None):
    req = req or AttendanceFanbeiCourseSheetsRequest()
    try:
        with Session(get_attendance_engine()) as session:
            materialize_summary = materialize_fanbei_course_sheets(
                session,
                workbook_id=req.workbook_id,
                attendance_sheet_id=req.attendance_sheet_id,
                course_name=req.course_name,
                replace=req.replace,
            )
            rebuild_summary = None
            if req.rebuild:
                rebuild_summary = rebuild_fanbei_attendance_from_course_sheets(
                    session,
                    attendance_sheet_id=req.attendance_sheet_id,
                    user_alias_map={},
                )
            session.commit()
            return {
                "materialize": materialize_summary,
                "rebuild": rebuild_summary,
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/fanbei/rebuild-from-sheets")
def rebuild_attendance_fanbei_from_course_sheets(req: AttendanceFanbeiRebuildFromSheetsRequest | None = None):
    req = req or AttendanceFanbeiRebuildFromSheetsRequest()
    try:
        with Session(get_attendance_engine()) as session:
            summary = rebuild_fanbei_attendance_from_course_sheets(
                session,
                attendance_sheet_id=req.attendance_sheet_id,
                user_alias_map=req.user_alias_map,
            )
            session.commit()
            return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/nianzhu/step0/sync-catalog")
def sync_attendance_nianzhu_step0_catalog(req: AttendanceNianzhuStep0CatalogSyncRequest):
    try:
        with Session(get_attendance_engine()) as session:
            summary = sync_progressive_zen_catalog(
                session,
                attendance_sheet_id=req.attendance_sheet_id,
                course_name=req.course_name,
                catalog_url=req.catalog_url,
                catalog_weeks=req.catalog_weeks,
                expected_lesson_count=req.expected_lesson_count,
            )
            session.commit()
            return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/nianzhu/step1")
def run_attendance_nianzhu_step1(req: AttendanceNianzhuStep1Request | None = None):
    req = req or AttendanceNianzhuStep1Request()
    try:
        with ensure_ui_automation_thread_context():
            with Session(get_attendance_engine()) as session:
                summary = run_nianzhu_course_sheet_step1(
                    session,
                    workbook_id=req.workbook_id,
                    attendance_sheet_id=req.attendance_sheet_id,
                    course_name=req.course_name,
                    shop_id=req.shop_id,
                    update_lessons=req.update_lessons,
                    update_clockins=req.update_clockins,
                    clockin_pattern=req.clockin_pattern,
                    dynamic_clockin_plugin=req.dynamic_clockin_plugin,
                    close_browser=req.close_browser,
                )
                session.commit()
                return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/nianzhu/step2")
def run_attendance_nianzhu_step2(req: AttendanceNianzhuStep2Request | None = None):
    req = req or AttendanceNianzhuStep2Request()
    try:
        with Session(get_attendance_engine()) as session:
            step2_summary = compact_nianzhu_course_sheet_step2(
                session,
                attendance_sheet_id=req.attendance_sheet_id,
                course_name=req.course_name,
            )
            rebuild_summary = None
            if req.rebuild:
                rebuild_summary = rebuild_nianzhu_attendance_from_course_sheets(
                    session,
                    attendance_sheet_id=req.attendance_sheet_id,
                    active_only=not req.include_frozen,
                    course_name=req.course_name,
                )
            session.commit()
            return {
                "step2": step2_summary,
                "rebuild": rebuild_summary,
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/nianzhu/step3")
def run_attendance_nianzhu_step3(req: AttendanceNianzhuStep3Request | None = None):
    req = req or AttendanceNianzhuStep3Request()
    try:
        with Session(get_attendance_engine()) as session:
            summary = rebuild_nianzhu_attendance_from_course_sheets(
                session,
                attendance_sheet_id=req.sheet_id,
                active_only=not req.include_frozen,
                course_name=req.course_name,
            )
            session.commit()
            return {
                "sheet_id": int(req.sheet_id),
                "course_name": req.course_name,
                **summary,
                "message": (
                    f"当前 CodeYun 实例已执行念住闯关 step3："
                    f"从课程存储 sheet 重建 {summary.get('rows', 0)} 行，"
                    f"更新 {summary.get('updated_rows', 0)} 行/"
                    f"{summary.get('updated_cells', 0)} 格，"
                    f"渲染 {summary.get('styled_cells', 0)} 格"
                ),
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/nianzhu/course-sheets")
def materialize_attendance_nianzhu_course_sheets(req: AttendanceNianzhuCourseSheetsRequest | None = None):
    req = req or AttendanceNianzhuCourseSheetsRequest()
    try:
        with Session(get_attendance_engine()) as session:
            materialize_summary = materialize_nianzhu_course_sheets(
                session,
                workbook_id=req.workbook_id,
                attendance_sheet_id=req.attendance_sheet_id,
                course_name=req.course_name,
                replace=req.replace,
            )
            rebuild_summary = None
            if req.rebuild:
                rebuild_summary = rebuild_nianzhu_attendance_from_course_sheets(
                    session,
                    attendance_sheet_id=req.attendance_sheet_id,
                    active_only=not req.include_frozen,
                    course_name=req.course_name,
                )
            session.commit()
            return {
                "materialize": materialize_summary,
                "rebuild": rebuild_summary,
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/nianzhu/rebuild-from-sheets")
def rebuild_attendance_nianzhu_from_course_sheets(req: AttendanceNianzhuRebuildFromSheetsRequest | None = None):
    req = req or AttendanceNianzhuRebuildFromSheetsRequest()
    try:
        with Session(get_attendance_engine()) as session:
            summary = rebuild_nianzhu_attendance_from_course_sheets(
                session,
                attendance_sheet_id=req.attendance_sheet_id,
                active_only=not req.include_frozen,
                course_name=req.course_name,
            )
            session.commit()
            return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/clockin-links/detect")
def detect_attendance_clockin_links(req: AttendanceClockinLinkDetectRequest):
    try:
        with ensure_ui_automation_thread_context():
            return detect_clockin_links_browser(
                root_url=req.root_url,
                targets=req.targets,
                provider_id=req.provider_id,
                model=req.model,
                close_tabs=req.close_tabs,
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/xiaoe-clockin-activities/detect")
def detect_attendance_xiaoe_clockin_activities(req: AttendanceXiaoeClockinActivityDetectRequest):
    try:
        with ensure_ui_automation_thread_context():
            return detect_xiaoe_attendance_clockin_activities_browser(
                target_keywords=req.target_keywords,
                shop_name=req.shop_name,
                close_browser=req.close_browser,
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/user-match/lookup")
def lookup_attendance_users(req: AttendanceUserMatchLookupRequest):
    if not req.items:
        return {"results": []}

    try:
        with ensure_ui_automation_thread_context():
            results = lookup_registration_users_browser(
                [item.model_dump() for item in req.items],
                course_name=req.course_name,
                course_product_name=req.course_product_name,
                shop_id=req.shop_id,
                close_browser=req.close_browser,
            )
            return {"results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attendance/order/refund-details")
def query_attendance_order_refund_details(req: AttendanceOrderRefundDetailRequest):
    try:
        with ensure_ui_automation_thread_context():
            return query_order_refund_details(
                req.order_id,
                query_type=req.query_type,
                weipay_login_users=req.login_users,
            )
    except OrderAutomationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
