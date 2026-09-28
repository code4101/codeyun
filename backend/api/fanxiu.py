from backend.api.fanxiu_notes import (
    inventory_router as inventory_notes_router,
    chars_router as character_notes_router,
    FANXIU_ACTIVITY_KIND,
    FANXIU_ACTIVITY_TYPE,
    FANXIU_MAGIC_TREASURE_KIND,
    FANXIU_MAGIC_TREASURE_TYPE,
    FANXIU_SPIRIT_BEAST_KIND,
    FANXIU_SPIRIT_BEAST_TYPE,
    FANXIU_WARDROBE_KIND,
    FANXIU_WARDROBE_TYPE,
    XIANZHOU_RACE_CHAR_NAMES,
    _upsert_fanxiu_inventory_item_note,
    find_activity_item,
    find_magic_treasure_item,
    find_spirit_beast_item,
    find_wardrobe_item,
    read_char,
    read_chars,
    read_fanxiu_activity_note,
    read_fanxiu_magic_treasure_note,
    read_fanxiu_spirit_beast_note,
    read_fanxiu_wardrobe_note,
    serialize_fanxiu_note_read,
    update_char,
    update_fanxiu_activity_note,
    update_fanxiu_magic_treasure_note,
    update_fanxiu_spirit_beast_note,
    update_fanxiu_wardrobe_note,
)
from backend.core.fanxiu.game.window_streaming import stream_response_from_requests as _stream_response_from_requests
from backend.core.fanxiu.game.window_remote import request_remote_game_window2_image
from backend.core.fanxiu.game.window_remote import request_remote_game_window2_json
from backend.core.fanxiu.data_annotation.state import (
    normalize_kernel_scheduler_current_scene as _coerce_status_current_scene,
)
from backend.core.fanxiu.data_annotation.kernel_log_views import (
    log_entry_base_id, log_entries, cell_source,
    persisted_cell_views, historical_cell_views,
)
from backend.core.fanxiu.mail.query import mail_record_view, query_mail_records
from backend.core.fanxiu.instrumentation.catalog_collection import build_catalog_collection_code
from backend.core.fanxiu.notes import (
    MissingInventoryNoteTitle,
    activity_item_start_to_timestamp,
    get_fanxiu_note_by_id,
    sync_activity_note_fields,
    sync_hall_note_refs,
    sync_item_note_refs,
    sync_wardrobe_note_fields,
    upsert_inventory_item_note,
    FANXIU_CHAR_TYPE,
    FANXIU_CHAR_KIND,
    get_or_migrate_fanxiu_char_note,
    upsert_character_note,
)
from backend.api.fanxiu_questions import (
    status_router as questions_router,
    get_lingquan_questions,
    post_lingquan_question,
    put_lingquan_question,
    delete_lingquan_question,
)
from backend.api.fanxiu_players import (
    status_router as players_router,
    list_fanxiu_business_player_profiles,
    get_fanxiu_server_relations,
    update_fanxiu_server_relations,
)
from backend.api.fanxiu_access import (
    pwd_context,
    FANXIU_USERNAME,
    CODE4101_USERNAME,
    get_fanxiu_user,
    ensure_fanxiu_write_permission,
)
from backend.api.fanxiu_activities import (
    inventory_router as activities_router,
    get_fanxiu_yunmeng_trial_snapshot,
    get_latest_fanxiu_exchange_activity_snapshot,
    get_fanxiu_exchange_activity_snapshot,
    get_fanxiu_schedule_rankings,
    get_fanxiu_exchange_activity_observations,
    get_fanxiu_lingzhuang_strengthening_snapshot,
    collect_fanxiu_lingzhuang_strengthening_snapshot,
    get_fanxiu_lingzhuang_relationship_samples,
    record_fanxiu_lingzhuang_relationship_sample,
    update_fanxiu_exchange_activity_priorities,
    plan_fanxiu_exchange_activity_shop,
    update_fanxiu_exchange_activity_shop_item_lock,
    get_fanxiu_exchange_activity_rankings,
    get_fanxiu_yaochi_flower_festival_tasks,
    get_fanxiu_yuanding_sansheng_tasks,
    get_fanxiu_lingchong_jingwu_tasks,
    get_fanxiu_registered_resource_ranking_tasks,
    get_fanxiu_registered_resource_ranking_resources,
    collect_fanxiu_registered_resource_ranking_resources,
    get_fanxiu_lingchong_jingwu_resources,
    collect_fanxiu_lingchong_jingwu_resources,
    get_fanxiu_yaochi_flower_resources,
    collect_fanxiu_yaochi_flower_resources,
    collect_fanxiu_exchange_activity,
    update_fanxiu_yunmeng_trial_priorities,
    update_fanxiu_yunmeng_trial_shop_item_lock,
    get_fanxiu_yunmeng_trial_rankings,
    get_fanxiu_yunmeng_trial_measurements,
    collect_fanxiu_yunmeng_trial_measurement,
)
import base64
import asyncio
import hashlib
import io
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable, List, Optional

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from jose import JWTError, jwt
from sqlmodel import Session, select
from pyxllib.autogui import View, image_number
from pyxllib.prog.behavior_tree import Status as BehaviorTreeStatus

from backend.core.access.auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    get_current_active_user,
    get_optional_current_user_from_token,
    verify_api_token,
)
from backend.core.access.feature_access_guard import ensure_feature_access, require_feature_access_dependency
from backend.core.devices.http_proxy import REMOTE_DEVICE_DIRECT_PROXIES
from backend.core.devices.host_access import ensure_host_device_access
from backend.core.runtime.game_window_service import (
    GameWindowServiceError,
    get_game_window_service_status,
    open_game_window_service_stream,
    start_game_window_service,
)
from backend.core.access.service_tokens import SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL, require_service_scope
from backend.core.settings import get_settings
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.behavior_tree.errors import BehaviorTreeExecutionError
from backend.core.notes.refs import note_public_id
from backend.db import engine, get_session
from backend.models import FanxiuPseudoCodeCard, NoteNode, User, UserDevice
from backend.schemas import NoteRead, NoteUpdate
from backend.core.fanxiu.client.mumu_control import (
    activate_mumu_window,
    capture_fresh_mumu_adb_stream_frame,
    clear_fanxiu_burst_frames,
    delete_fanxiu_screenshot,
    get_fanxiu_burst_frame_path,
    get_fanxiu_match_frame_path,
    get_fanxiu_screenshot_path,
    get_mumu_adb_stream_frame_status,
    import_fanxiu_burst_frames,
    list_fanxiu_burst_frames,
    list_fanxiu_screenshots,
    read_fanxiu_screenshot_pre_label,
    recover_mumu_device,
    save_fanxiu_burst_frame,
    save_fanxiu_screenshot_frame,
    stream_mumu_adb_screencap_mjpeg,
    stream_mumu_window_mjpeg,
    write_fanxiu_screenshot_pre_label,
)
from backend.core.fanxiu.game.window_actions import click_game_window2_service as _core_click_game_window2_service, drag_game_window2_service as _core_drag_game_window2_service, game_window2_desktop_title as _core_game_window2_desktop_title, keyevent_game_window2_service as _core_keyevent_game_window2_service, match_game_window2_service as _core_match_game_window2_service, normalize_game_window2_title as _core_normalize_game_window2_title, screencap_game_window2_service as _core_screencap_game_window2_service, text_game_window2_service as _core_text_game_window2_service
from backend.core.fanxiu.game.window_remote import click_remote_game_window2 as _core_click_remote_game_window2, drag_remote_game_window2 as _core_drag_remote_game_window2, extract_stream_error as _core_extract_stream_error, keyevent_remote_game_window2 as _core_keyevent_remote_game_window2, match_remote_game_window2 as _core_match_remote_game_window2, post_remote_game_window2_json as _core_post_remote_game_window2_json, remote_entry_base_url as _core_remote_entry_base_url, remote_entry_headers as _core_remote_entry_headers, remote_game_window2_screencap as _core_remote_game_window2_screencap, text_remote_game_window2 as _core_text_remote_game_window2
from backend.core.fanxiu.game.pseudocode_executor import compile_fanxiu_pseudocode, start_fanxiu_pseudocode_script
from backend.core.fanxiu.game.visual_macro_executor import (
    VisualMacroCallbacks,
    begin_visual_macro_run,
    end_visual_macro_run,
    run_fanxiu_visual_script,
    stop_visual_macro_run,
)
from backend.core.ai.app_config import (
    AiAppConfigError,
)
from backend.core.ai.chat import OllamaClientError
from backend.core.fanxiu.catalog.inventory import load_magic_treasure_hall, save_magic_treasure_hall
from backend.core.fanxiu.catalog.inventory import load_spirit_artifact_hall, save_spirit_artifact_hall
from backend.core.fanxiu.catalog.inventory import load_wardrobe_hall, save_wardrobe_hall
from backend.core.fanxiu.catalog.inventory import load_spirit_beast_hall, save_spirit_beast_hall
from backend.core.fanxiu.catalog.inventory import load_activity_list, save_activity_list
from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
    upsert_inventory_hall_snapshot,
)
from backend.core.fanxiu.client.processes import match_fanxiu_process_fields, list_fanxiu_processes, terminate_fanxiu_processes
from backend.core.fanxiu.activity.runtime_schedule import (
    read_fanxiu_activity_runtime_schedule,
)
from backend.core.fanxiu.catalog.status_models import (
    FanxiuMailRuntimeSyncResponse,
    FanxiuMailRecordListResponse,
    FanxiuMailRecordUpdateRequest,
    FanxiuMailRecordUpdateResponse,
    FanxiuStorageBagAutoClaimUpdateRequest,
    FanxiuStorageBagAutoClaimUpdateResponse,
    FanxiuStorageBagNoteUpdateRequest,
    FanxiuStorageBagNoteUpdateResponse,
    FanxiuStorageBagSnapshotResponse,
    FanxiuProcessItem,
    FanxiuProcessListResponse,
    FanxiuProcessTerminateError,
    FanxiuProcessTerminateResponse,
    LocalScriptProcessItem,
    LocalScriptProcessListResponse,
)
from backend.core.fanxiu.game.window_models import (
    FanxiuGameWindow2ActivateRequest,
    FanxiuGameWindow2BurstClearRequest,
    FanxiuGameWindow2BurstFrameRequest,
    FanxiuGameWindow2BurstImportRequest,
    FanxiuGameWindow2BurstListRequest,
    FanxiuGameWindow2ClickRequest,
    FanxiuGameWindow2DragRequest,
    FanxiuGameWindow2KeyeventRequest,
    FanxiuGameWindow2MatchBox,
    FanxiuGameWindow2MatchRequest,
    FanxiuGameWindow2SaveFrameRequest,
    FanxiuGameWindow2ScreencapRequest,
    FanxiuGameWindow2ScreenshotDeleteRequest,
    FanxiuGameWindow2ScreenshotListRequest,
    FanxiuGameWindow2ScreenshotPreLabelRequest,
    FanxiuGameWindow2ScreenshotPreLabelSaveRequest,
    FanxiuGameWindow2ServiceActivateRequest,
    FanxiuGameWindow2ServiceBurstClearRequest,
    FanxiuGameWindow2ServiceBurstFrameRequest,
    FanxiuGameWindow2ServiceBurstImportRequest,
    FanxiuGameWindow2ServiceBurstListRequest,
    FanxiuGameWindow2ServiceClickRequest,
    FanxiuGameWindow2ServiceDragRequest,
    FanxiuGameWindow2ServiceKeyeventRequest,
    FanxiuGameWindow2ServiceMatchRequest,
    FanxiuGameWindow2ServiceSaveFrameRequest,
    FanxiuGameWindow2ServiceScreenshotDeleteRequest,
    FanxiuGameWindow2ServiceScreenshotPreLabelRequest,
    FanxiuGameWindow2ServiceScreenshotPreLabelSaveRequest,
    FanxiuGameWindow2ServiceTextRequest,
    FanxiuGameWindow2StreamTokenRequest,
    FanxiuGameWindow2StreamTokenResponse,
    FanxiuGameWindow2TextRequest,
    FanxiuDataAnnotationAssetTreeRequest,
    FanxiuDataAnnotationMacroAnnotateRequest,
    FanxiuDataAnnotationMacroAnnotateResponse,
    FanxiuDataAnnotationMacroPoint,
    FanxiuDataAnnotationOcrFrameRequest,
    FanxiuDataAnnotationOcrFrameResponse,
    FanxiuDataAnnotationRemoveBackgroundRequest,
    FanxiuDataAnnotationRemoveBackgroundResponse,
    FanxiuDataAnnotationSaveFrameRequest,
    FanxiuPseudoCodeCardCreateRequest,
    FanxiuPseudoCodeCardListResponse,
    FanxiuPseudoCodeCardRead,
    FanxiuPseudoCodeCardUpdateRequest,
    FanxiuPseudoCodeCompileRequest,
    FanxiuPseudoCodeRunResponse,
    FanxiuPseudoCodeStartRequest,
    FanxiuVisualScriptRunRequest,
    FanxiuVisualScriptStopRequest,
)
from backend.core.fanxiu.catalog.inventory_models import (
    FanxiuActivityListSnapshot,
    FanxiuMagicTreasureHallSnapshot,
    FanxiuSpiritArtifactHallSnapshot,
    FanxiuSpiritBeastHallSnapshot,
    FanxiuWardrobeHallSnapshot,
)
from backend.core.fanxiu.mail.store import mark_fanxiu_mail_action, normalize_fanxiu_mail_title, update_fanxiu_mail_desired_status
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_visible_group_action_policy,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_rewards_unresolved,
)
from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    delete_storage_bag_atlas_item,
    load_storage_bag_atlas,
    sync_storage_bag_atlas,
)
from backend.core.fanxiu.storage_bag_settings import (
    apply_storage_bag_item_settings,
    delete_storage_bag_item_setting,
    set_storage_bag_auto_claim,
    set_storage_bag_note,
)
from backend.core.fanxiu.storage_bag_usage import (
    delete_storage_bag_usage_history,
    ensure_storage_bag_atlas_analysis,
)
from backend.core.fanxiu.mail.runtime_sync import sync_fanxiu_mail_from_runtime
from backend.core.fanxiu.data_annotation.jobs import (
    DataAnnotationTaskCellDefinition as _DataAnnotationTaskCellDefinition,
    _DATA_ANNOTATION_TASK_CELL_REGISTRY,
    get_fanxiu_data_annotation_task_cell_definition as _data_annotation_task_cell_definition,
    register_fanxiu_data_annotation_task_cell,
)
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as _kernel_scheduler_control
from backend.core.fanxiu.data_annotation import behavior_tree_framework as _behavior_tree_framework
from backend.core.fanxiu.data_annotation.models import (
    FanxiuDataAnnotationDoctorWatchEnsureResponse,
    FanxiuDataAnnotationDoctorWatchLatestResponse,
    FanxiuKernelSchedulerCellLog,
    FanxiuKernelSchedulerCellLogResponse,
    FanxiuKernelSchedulerCodeCellRequest,
    FanxiuKernelSchedulerLogEntry,
    FanxiuKernelSchedulerLogResponse,
    FanxiuKernelSchedulerBehaviorTreeRequest,
    FanxiuKernelSchedulerDeviceRestartRequest,
    FanxiuKernelSchedulerDeviceRestartResponse,
    FanxiuKernelSchedulerKernelRestartRequest,
    FanxiuKernelSchedulerStatus,
    FanxiuKernelSchedulerTaskCellRequest,
    FanxiuKernelSchedulerStopRequest,
    FanxiuKernelSchedulerGuardGroupRequest,
    FanxiuKernelSchedulerGuardRequest,
    FanxiuKernelSchedulerIsolationRequest,
    FanxiuInfoWindowControlStatus,
    FanxiuInfoWindowSettingsRequest,
    FanxiuKernelSchedulerTaskItem,
    FanxiuKernelSchedulerTaskUpdate,
    FanxiuKernelSchedulerTasksResponse,
    FanxiuGameStateInspectionStatus,
    FanxiuKernelSchedulerTimeSequenceResponse,
    FanxiuKernelSchedulerTimeSequenceUpdateRequest,
    FanxiuKernelSchedulerPlanItem,
    FanxiuKernelSchedulerPlanResponse,
    FanxiuKernelSchedulerRunDueRequest,
    FanxiuKernelSchedulerRunNowRequest,
    FanxiuKernelSchedulerNextTimeRequest,
    FanxiuKernelSchedulerNextTimeResponse,
    FanxiuKernelSchedulerTriggerOnceRequest,
    FanxiuKernelSchedulerTriggerOnceResponse,
    FanxiuKernelSchedulerSettingsRequest,
    FanxiuDataAnnotationWorldFactsResponse,
)
from backend.core.fanxiu.data_annotation.game_state_inspection import (
    read_game_state_inspection_status,
)
from backend.core.fanxiu.data_annotation.state import (
    append_kernel_scheduler_log_once,
    append_kernel_scheduler_status_log,
    kernel_scheduler_task_state as _kernel_scheduler_task_state,
    data_annotation_task_due as _data_annotation_task_due,
    initial_kernel_scheduler_status,
    initial_data_annotation_world_facts as _initial_data_annotation_world_facts,
    is_kernel_scheduler_live_empty,
    normalize_kernel_scheduler_guard_items,
    parse_data_annotation_task_time,
    persist_kernel_scheduler_status,
    read_kernel_scheduler_status,
    read_data_annotation_json as _read_data_annotation_json,
    read_data_annotation_world_facts,
    record_kernel_scheduler_task_fact,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_plan import (
    build_kernel_scheduler_plan,
    kernel_scheduler_run_now_task as _core_kernel_scheduler_run_now_task,
    kernel_scheduler_order_key,
    kernel_scheduler_task_plan_reason,
    data_annotation_world_facts_summary,
    merge_kernel_scheduler_task_updates,
    repair_kernel_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    default_kernel_scheduler_tasks as _default_kernel_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.behavior_tree_container import (
    BehaviorTreeContainer as _BehaviorTreeContainer,
    BehaviorTreeGroupSpec as _BehaviorTreeGroupSpec,
    BehaviorTreeNodeSpec as _BehaviorTreeNodeSpec,
)
from backend.core.fanxiu.behavior_tree.kernel_scheduler import (
    DEFAULT_FANXIU_ENTRY_ID,
    create_behavior_tree_executor,
    data_annotation_asset_tree_path as _core_data_annotation_asset_tree_path,
    fanxiu_data_annotation_dir as _core_data_annotation_dir,
    fanxiu_kernel_scheduler_dir as _core_behavior_tree_executor_dir,
    fanxiu_kernel_scheduler_logs as _core_kernel_scheduler_logs,
    fanxiu_kernel_scheduler_status as _core_kernel_scheduler_status,
    fanxiu_kernel_execution_state_path as _core_execution_state_path,
    fanxiu_kernel_scheduler_settings_path as _core_scheduler_settings_path,
    fanxiu_kernel_scheduler_state_path as _core_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path as _core_world_facts_path,
    clear_fanxiu_kernel_scheduler_logs as _core_clear_kernel_scheduler_logs,
    register_behavior_tree_executor,
    resolve_fanxiu_entry,
)
from backend.core.fanxiu.data_annotation.recognition_ops import build_recognition_ops_report
from backend.core.fanxiu.data_annotation.navigation_incidents import (
    list_navigation_incident_summaries,
    load_navigation_incident,
)
from backend.core.fanxiu.data_annotation.recognition_ambiguity_incidents import (
    list_recognition_ambiguity_summaries,
    load_recognition_ambiguity,
)
from backend.core.fanxiu.data_annotation.storage import (
    FanxiuDataAnnotationAssetTreeConflict,
    decode_data_annotation_image_data_url,
    read_data_annotation_asset_tree_snapshot,
    resolve_data_annotation_scene_node_id,
    resolve_data_annotation_image_asset,
    save_data_annotation_asset_tree_snapshot,
    save_data_annotation_frame_tree_node,
    save_data_annotation_image_bytes,
)
from backend.core.fanxiu.game.macro_annotation import (
    _annotate_game_macro_shape_with_ai,
    _build_game_macro_annotation_prompt,
    _build_game_macro_ocr_context,
    _clamp_game_macro_box,
    _coerce_float,
    _decode_game_macro_data_url_to_bytes,
    _extract_game_macro_annotation_json,
    _recognize_data_annotation_ocr_frame,
    _summarize_game_macro_ocr_document,
)
from backend.core.fanxiu.data_annotation.rembg import remove_fanxiu_data_annotation_background
from backend.core.runtime.local_script_processes import list_local_script_processes
from backend.core.notes.access import note_to_response_dict
from backend.core.notes.semantics import NOTE_KIND_FANXIU_ACTIVITY_ITEM, NOTE_KIND_FANXIU_MAGIC_TREASURE_ITEM, NOTE_KIND_FANXIU_SPIRIT_BEAST_ITEM, NOTE_KIND_FANXIU_WARDROBE_ITEM


_DATA_ANNOTATION_OCR_FRAME_LOG_LOCK = threading.Lock()


def _rough_data_url_payload_size(value: str) -> int:
    payload = str(value or "").strip()
    if "," in payload and payload.split(",", 1)[0].lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    payload = "".join(payload.split())
    if not payload:
        return 0
    padding = payload.count("=")
    return max(0, (len(payload) * 3 // 4) - padding)


def _log_data_annotation_ocr_frame_request(
    request: Request,
    req: Any,
    current_user: User,
) -> None:
    try:
        log_dir = codeyun_temp_root("fanxiu-ocr-frame")
        log_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "event": "data_annotation_ocr_frame",
            "time": datetime.now().isoformat(timespec="seconds"),
            "client": request.client.host if request.client else "",
            "user": getattr(current_user, "username", "") or "",
            "referer": (request.headers.get("referer") or "")[:500],
            "origin": (request.headers.get("origin") or "")[:200],
            "user_agent": (request.headers.get("user-agent") or "")[:300],
            "image_chars": len(req.image_data_url or ""),
            "image_bytes_approx": _rough_data_url_payload_size(req.image_data_url),
        }
        with _DATA_ANNOTATION_OCR_FRAME_LOG_LOCK:
            with (log_dir / "requests.ndjson").open("a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)
status_router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)
# 本机 Kernel 家族有无设备 ID 的全局操作，不能继承公开图鉴的访问权限。
kernel_scheduler_router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu.kernel-scheduler"))],
)
service_router = APIRouter()
chars_router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)
inventory_router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)


FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE = "fanxiu.game-window2:stream"
FANXIU_GAME_WINDOW2_STREAM_TOKEN_EXPIRE_HOURS = 2


@status_router.get("/scripts", response_model=LocalScriptProcessListResponse)
def get_local_script_processes(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    items = []
    for item in list_local_script_processes():
        items.append(
            {
                **item,
                "is_fanxiu": bool(
                    match_fanxiu_process_fields(
                        name=str(item.get("name") or ""),
                        command_line=str(item.get("command_line") or ""),
                        cwd=item.get("cwd"),
                    )
                ),
            }
        )
    return LocalScriptProcessListResponse(items=items)


@status_router.get("/processes", response_model=FanxiuProcessListResponse)
def get_fanxiu_processes(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuProcessListResponse(items=list_fanxiu_processes())


@status_router.get("/activity-runtime-schedule/latest")
def get_fanxiu_latest_worldline_activity_schedule(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_fanxiu_write_permission(current_user, session)
    return read_fanxiu_activity_runtime_schedule()


@status_router.get("/mail-records", response_model=FanxiuMailRecordListResponse)
def list_fanxiu_mail_records(
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    status: str = Query(""),
    action_policy: str = Query(""),
    source: str = Query("runtime_memory"),
    include_absent: bool = Query(True),
    include_empty_actions: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    payload = query_mail_records(
        session, limit=limit, offset=offset, status=status,
        action_policy=action_policy, source=source,
        include_absent=include_absent, include_empty_actions=include_empty_actions,
    )
    response = Response(
        content=json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        media_type="application/json",
    )
    for key, value in payload.items():
        setattr(response, key, value)
    return response


@status_router.patch("/mail-records/{mail_key}", response_model=FanxiuMailRecordUpdateResponse)
def update_fanxiu_mail_record_status(
    mail_key: str,
    payload: FanxiuMailRecordUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        record = update_fanxiu_mail_desired_status(session, mail_key, desired_status=payload.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="邮件状态只能是：锁定、留存、可领")
    if record is None:
        raise HTTPException(status_code=404, detail="邮件记录不存在")
    session.commit()
    session.refresh(record)
    return FanxiuMailRecordUpdateResponse(ok=True, record=mail_record_view(record))


@status_router.post("/mail-records/sync-runtime", response_model=FanxiuMailRuntimeSyncResponse)
def sync_fanxiu_mail_records_from_runtime(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    result = sync_fanxiu_mail_from_runtime(session)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("reason") or "邮件动态快照不可用")
    return FanxiuMailRuntimeSyncResponse.model_validate(result)


@status_router.get(
    "/business-data/storage-bag",
    response_model=FanxiuStorageBagSnapshotResponse,
)
def get_fanxiu_business_storage_bag(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    del current_user
    bag = load_storage_bag_atlas()
    if bag is None:
        return {
            "ok": False,
            "state": "cache_empty",
            "reason": "尚无储物袋缓存；请显式从游戏同步",
            "bag": None,
        }
    return {
        "ok": True,
        "state": "cached",
        "reason": None,
        "bag": apply_storage_bag_item_settings(session, bag),
    }


@status_router.post(
    "/business-data/storage-bag/sync",
    response_model=FanxiuStorageBagSnapshotResponse,
)
def sync_fanxiu_business_storage_bag(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        runtime_snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
        cards_by_id = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
        bag = sync_storage_bag_atlas(
            runtime_snapshot,
            cards_by_id,
            captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
    except (FanxiuRuntimeMemoryError, KeyError, TypeError, ValueError) as exc:
        bag = load_storage_bag_atlas(reason=str(exc))
        return {
            "ok": False,
            "state": "runtime_unavailable",
            "reason": str(exc),
            "bag": (
                apply_storage_bag_item_settings(session, bag)
                if bag is not None
                else None
            ),
        }
    ensure_storage_bag_atlas_analysis(session, bag)
    session.commit()
    return {
        "ok": True,
        "state": "complete",
        "reason": None,
        "bag": apply_storage_bag_item_settings(session, bag),
    }


@status_router.put(
    "/business-data/storage-bag/atlas/{base_id}/auto-claim",
    response_model=FanxiuStorageBagAutoClaimUpdateResponse,
)
def update_fanxiu_business_storage_bag_auto_claim(
    base_id: int,
    payload: FanxiuStorageBagAutoClaimUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> FanxiuStorageBagAutoClaimUpdateResponse:
    ensure_fanxiu_write_permission(current_user, session)
    atlas = load_storage_bag_atlas()
    if atlas is None or not any(int(row.get("base_id") or 0) == base_id for row in atlas.get("items") or []):
        raise HTTPException(status_code=404, detail="储物袋图鉴中不存在该物品")
    record = set_storage_bag_auto_claim(session, base_id=base_id, auto_claim=payload.auto_claim)
    session.commit()
    session.refresh(record)
    return FanxiuStorageBagAutoClaimUpdateResponse(
        ok=True,
        base_id=record.base_id,
        auto_claim=record.auto_claim,
    )


@status_router.put(
    "/business-data/storage-bag/atlas/{base_id}/note",
    response_model=FanxiuStorageBagNoteUpdateResponse,
)
def update_fanxiu_business_storage_bag_note(
    base_id: int,
    payload: FanxiuStorageBagNoteUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> FanxiuStorageBagNoteUpdateResponse:
    ensure_fanxiu_write_permission(current_user, session)
    atlas = load_storage_bag_atlas()
    if atlas is None or not any(int(row.get("base_id") or 0) == base_id for row in atlas.get("items") or []):
        raise HTTPException(status_code=404, detail="储物袋图鉴中不存在该物品")
    record = set_storage_bag_note(session, base_id=base_id, note=payload.note)
    session.commit()
    session.refresh(record)
    return FanxiuStorageBagNoteUpdateResponse(ok=True, base_id=record.base_id, note=record.note)


@status_router.delete("/business-data/storage-bag/atlas/{base_id}")
def delete_fanxiu_business_storage_bag_atlas_item(
    base_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        result = delete_storage_bag_atlas_item(base_id)
        delete_storage_bag_item_setting(session, base_id=base_id)
        delete_storage_bag_usage_history(session, base_id=base_id)
        session.commit()
        return {"ok": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail="储物袋图鉴中不存在该物品")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def _recommended_fanxiu_proxy_address(status: dict[str, Any]) -> str:
    addresses = [str(item) for item in status.get("addresses") or []]
    for address in addresses:
        if not address.startswith("127.") and not address.startswith("198.18."):
            return address
    return addresses[0] if addresses else ""


@status_router.post("/processes/terminate", response_model=FanxiuProcessTerminateResponse, deprecated=True)
def terminate_fanxiu_scripts(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    return FanxiuProcessTerminateResponse.model_validate(terminate_fanxiu_processes())


def _stream_fanxiu_game_window(
    title: Optional[str] = None,
    fps: float = 10.0,
    quality: int = 80,
    mode: str = "screen",
    area: str = "outer",
    crop: Optional[str] = None,
    trim_border: Optional[str] = None,
    rotate: str = "90",
    fixed_width: int = 0,
    fixed_height: int = 0,
    auto_dismiss_popup: bool = False,
    popup_check_interval: float = 3.0,
):
    try:
        frames = stream_mumu_window_mjpeg(
            title=title,
            fps=fps,
            quality=quality,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            auto_dismiss_popup=auto_dismiss_popup,
            popup_check_interval=popup_check_interval,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StreamingResponse(
        frames,
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@status_router.get("/live-annotation/stream")
def stream_fanxiu_live_annotation(
    title: Optional[str] = Query(None),
    fps: float = Query(10.0, ge=1.0, le=30.0),
    quality: int = Query(80, ge=1, le=100),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("outer", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("90", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
):
    return _stream_fanxiu_game_window(
        title=title,
        fps=fps,
        quality=quality,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
    )


def _get_user_device_or_404(session: Session, current_user: User, entry_id: str) -> UserDevice:
    entry = session.get(UserDevice, entry_id)
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Device entry not found")
    ensure_host_device_access(current_user, mode=entry.mode, device_id=entry.device_id)
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="Device entry is inactive")
    return entry


def _get_service_user_device_or_404(session: Session, entry_id: str) -> UserDevice:
    entry = session.get(UserDevice, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Device entry not found")
    if not entry.is_active:
        raise HTTPException(status_code=400, detail="Device entry is inactive")
    return entry


def _decode_game_window2_stream_token(session: Session, token: str) -> tuple[UserDevice, User]:
    credentials_exception = HTTPException(status_code=401, detail="Invalid game window stream token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise credentials_exception from exc

    if payload.get("scope") != FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE:
        raise credentials_exception
    user_id = payload.get("user_id")
    entry_id = payload.get("entry_id")
    if type(user_id) is not int or user_id <= 0 or not entry_id:
        raise credentials_exception

    current_user = session.get(User, user_id)
    if current_user is None:
        raise credentials_exception
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return _get_user_device_or_404(session, current_user, str(entry_id)), current_user


def _remote_entry_base_url(entry: UserDevice) -> str:
    return _core_remote_entry_base_url(entry)


def _remote_entry_headers(entry: UserDevice) -> dict[str, str]:
    return _core_remote_entry_headers(entry)


def _normalize_game_window2_title(title: Optional[str]) -> Optional[str]:
    return _core_normalize_game_window2_title(title)


def _game_window2_desktop_title(title: Optional[str]) -> str:
    return _core_game_window2_desktop_title(title)


def _game_window2_stream_params(
    *,
    title: Optional[str],
    title_match: str,
    fps: float,
    quality: int,
    mode: str,
    area: str,
    crop: Optional[str],
    trim_border: Optional[str],
    rotate: str,
    fixed_width: int,
    fixed_height: int,
    auto_dismiss_popup: bool,
    popup_check_interval: float,
) -> dict[str, Any]:
    normalized_title = _normalize_game_window2_title(title)
    return {
        "title": normalized_title or "",
        "title_match": title_match,
        "fps": fps,
        "quality": quality,
        "mode": mode,
        "area": area,
        "crop": crop or "",
        "trim_border": trim_border or "",
        "rotate": rotate,
        "fixed_width": fixed_width,
        "fixed_height": fixed_height,
        "auto_dismiss_popup": "true" if auto_dismiss_popup else "false",
        "popup_check_interval": popup_check_interval,
    }


def _extract_stream_error(response: requests.Response) -> str:
    return _core_extract_stream_error(response)


def _open_remote_game_window2_stream(entry: UserDevice, params: dict[str, Any]) -> requests.Response:
    target_url = f"{_remote_entry_base_url(entry)}/api/fanxiu/game-window2/service-stream"
    try:
        return requests.get(
            target_url,
            headers=_remote_entry_headers(entry),
            params=params,
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=(5.0, 60.0),
            stream=True,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程游戏画面流不可达：{exc}") from exc


def _stream_game_window2_service(params: dict[str, Any]) -> StreamingResponse:
    try:
        response = open_game_window_service_stream(params)
    except GameWindowServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _stream_response_from_requests(
        response,
    )


def _game_window2_click_payload(req: FanxiuGameWindow2ClickRequest | FanxiuGameWindow2ServiceClickRequest) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_activate_payload(
    req: FanxiuGameWindow2ActivateRequest | FanxiuGameWindow2ServiceActivateRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_drag_payload(req: FanxiuGameWindow2DragRequest | FanxiuGameWindow2ServiceDragRequest) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_keyevent_payload(
    req: FanxiuGameWindow2KeyeventRequest | FanxiuGameWindow2ServiceKeyeventRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_text_payload(req: FanxiuGameWindow2TextRequest | FanxiuGameWindow2ServiceTextRequest) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_save_frame_payload(
    req: FanxiuGameWindow2SaveFrameRequest | FanxiuGameWindow2ServiceSaveFrameRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True, exclude={"entry_id"})


def _game_window2_match_payload(
    req: FanxiuGameWindow2MatchRequest | FanxiuGameWindow2ServiceMatchRequest,
) -> dict[str, Any]:
    return req.model_dump(exclude_none=True)


def _click_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_click_game_window2_service(payload)


def _activate_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return activate_mumu_window(
            title=_game_window2_desktop_title(payload.get("title")),
            title_match=payload.get("title_match") or "contains",
            click_title=bool(payload.get("click_title", True)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _drag_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_drag_game_window2_service(payload)


def _keyevent_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_keyevent_game_window2_service(payload)


def _text_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_text_game_window2_service(payload)


def _screencap_game_window2_service(
    *,
    prefer_cached: bool = False,
    cached_only: bool = False,
    allow_window_fallback: bool = False,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
) -> Response:
    return _core_screencap_game_window2_service(
        prefer_cached=prefer_cached,
        cached_only=cached_only,
        allow_window_fallback=allow_window_fallback,
        title=title,
        title_match=title_match,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
    )


def _save_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    title = _normalize_game_window2_title(payload.get("title"))
    try:
        return save_fanxiu_screenshot_frame(
            title=title,
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            quality=int(payload.get("quality") or 82),
            current_frame_data_url=payload.get("current_frame_data_url"),
            overwrite_filename=payload.get("overwrite_filename"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _match_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    return _core_match_game_window2_service(payload)


def _save_burst_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return save_fanxiu_burst_frame(
            title=payload.get("title"),
            title_match=payload.get("title_match") or "contains",
            mode=payload.get("mode"),
            area=payload.get("area"),
            crop=payload.get("crop"),
            trim_border=payload.get("trim_border"),
            rotate=payload.get("rotate"),
            fixed_width=int(payload.get("fixed_width") or 0),
            fixed_height=int(payload.get("fixed_height") or 0),
            current_frame_data_url=payload.get("current_frame_data_url"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _list_burst_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return list_fanxiu_burst_frames(
            page=int(payload.get("page") or 1),
            page_size=int(payload.get("page_size") or 24),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _clear_burst_game_window2_service() -> dict[str, Any]:
    try:
        return clear_fanxiu_burst_frames()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _import_burst_game_window2_service(filenames: list[str]) -> dict[str, Any]:
    try:
        return import_fanxiu_burst_frames(filenames)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _burst_game_window2_service_image(filename: str) -> FileResponse:
    try:
        path = get_fanxiu_burst_frame_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


def _click_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _core_click_remote_game_window2(entry, payload)


def _activate_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return request_remote_game_window2_json(entry, "service-input/activate", payload, "窗口激活")
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=502,
                detail="远程 codeyun 缺少激活窗口接口，请更新并重启远程 codeyun；如果已更新，请停止并重启“凡修游戏画面流”服务。",
            ) from exc
        raise


def _drag_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _core_drag_remote_game_window2(entry, payload)


def _post_remote_game_window2_json(entry: UserDevice, service_path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
    return _core_post_remote_game_window2_json(entry, service_path, payload, action)


def _keyevent_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _core_keyevent_remote_game_window2(entry, payload)


def _text_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _core_text_remote_game_window2(entry, payload)


def _save_remote_game_window2_frame(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return request_remote_game_window2_json(entry, "service-save-frame", payload, "保存帧", read_timeout=20.0)


def _match_remote_game_window2(entry: UserDevice, payload: dict[str, Any]) -> dict[str, Any]:
    return _core_match_remote_game_window2(entry, payload)


def _screenshot_game_window2_service_list() -> dict[str, Any]:
    try:
        return list_fanxiu_screenshots()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _screenshot_game_window2_service_pre_label(filename: str) -> dict[str, Any]:
    try:
        return read_fanxiu_screenshot_pre_label(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _save_screenshot_game_window2_service_pre_label(filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return write_fanxiu_screenshot_pre_label(filename, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _delete_screenshot_game_window2_service_image(filename: str) -> dict[str, Any]:
    try:
        return delete_fanxiu_screenshot(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _screenshot_game_window2_service_image(filename: str) -> FileResponse:
    try:
        path = get_fanxiu_screenshot_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Cache-Control": "private, no-cache"},
    )


def _match_game_window2_service_image(filename: str) -> FileResponse:
    try:
        path = get_fanxiu_match_frame_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


def _remote_game_window2_screenshot_json(
    entry: UserDevice,
    path: str,
    *,
    method: str = "post",
    payload: dict[str, Any] | None = None,
    action: str,
) -> dict[str, Any]:
    return request_remote_game_window2_json(entry, path, payload, action, method=method, read_timeout=20.0)


def _remote_game_window2_screenshot_image(entry: UserDevice, filename: str) -> Response:
    return request_remote_game_window2_image(
        entry, "service-screenshot/image", params={"filename": filename},
        action="截图", cache_control="private, no-cache",
    )


def _remote_game_window2_screencap(entry: UserDevice) -> Response:
    return _core_remote_game_window2_screencap(entry)


def _remote_game_window2_match_image(entry: UserDevice, filename: str) -> Response:
    return request_remote_game_window2_image(
        entry, "service-match/image", params={"filename": filename}, action="匹配帧",
    )


class _BehaviorTreeExecutorProxy:
    def __init__(self) -> None:
        self._runner: Any | None = None

    def resolve(self) -> Any:
        if self._runner is None:
            self._runner = create_behavior_tree_executor()
        return self._runner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.resolve(), name)


_BEHAVIOR_TREE_EXECUTOR: Any = _BehaviorTreeExecutorProxy()
_RECOGNITION_OPS_RECOMPUTE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fanxiu-recognition-ops")
_RECOGNITION_OPS_RECOMPUTE_LOCK = threading.Lock()
_RECOGNITION_OPS_RECOMPUTE_RUNNING: set[str] = set()


def _recognition_ops_recompute_state_path(cache_key: str) -> Path:
    return _BEHAVIOR_TREE_EXECUTOR._scene_match_cache_dir() / f"{cache_key}.recompute.json"


def _read_recognition_ops_recompute_state(cache_key: str) -> dict[str, Any] | None:
    path = _recognition_ops_recompute_state_path(cache_key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_recognition_ops_recompute_state(cache_key: str, payload: dict[str, Any]) -> None:
    path = _recognition_ops_recompute_state_path(cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _recognition_ops_recompute_view(cache_key: str) -> dict[str, Any] | None:
    payload = _read_recognition_ops_recompute_state(cache_key)
    if not payload:
        return None
    running = bool(payload.get("running")) and cache_key in _RECOGNITION_OPS_RECOMPUTE_RUNNING
    return {
        "cache_key": cache_key,
        "running": running,
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "error": str(payload.get("error") or ("" if running or not payload.get("running") else "重算任务已中断")),
    }


def _submit_recognition_ops_recompute(
    *,
    cache_key: str,
    ctx: dict[str, Any],
    layer: int,
    scene_ids: list[int],
) -> dict[str, Any]:
    with _RECOGNITION_OPS_RECOMPUTE_LOCK:
        if cache_key in _RECOGNITION_OPS_RECOMPUTE_RUNNING:
            return _recognition_ops_recompute_view(cache_key) or {"cache_key": cache_key, "running": True}
        _RECOGNITION_OPS_RECOMPUTE_RUNNING.add(cache_key)
        _write_recognition_ops_recompute_state(
            cache_key,
            {
                "cache_key": cache_key,
                "running": True,
                "started_at": time.time(),
                "finished_at": None,
                "error": "",
            },
        )

    def run() -> None:
        try:
            matrix = _BEHAVIOR_TREE_EXECUTOR.match_scene_matrix(ctx, scene_ids=scene_ids, layer=int(layer), use_cache=False)
            if isinstance(matrix, dict) and matrix.get("cache_path"):
                cache_path = Path(str(matrix["cache_path"]))
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
            _write_recognition_ops_recompute_state(
                cache_key,
                {
                    "cache_key": cache_key,
                    "running": False,
                    "started_at": _read_recognition_ops_recompute_state(cache_key).get("started_at") if _read_recognition_ops_recompute_state(cache_key) else None,
                    "finished_at": time.time(),
                    "error": "",
                },
            )
        except Exception as exc:
            started_at = (_read_recognition_ops_recompute_state(cache_key) or {}).get("started_at")
            _write_recognition_ops_recompute_state(
                cache_key,
                {
                    "cache_key": cache_key,
                    "running": False,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "error": str(exc),
                },
            )
        finally:
            with _RECOGNITION_OPS_RECOMPUTE_LOCK:
                _RECOGNITION_OPS_RECOMPUTE_RUNNING.discard(cache_key)

    _RECOGNITION_OPS_RECOMPUTE_EXECUTOR.submit(run)
    return _recognition_ops_recompute_view(cache_key) or {"cache_key": cache_key, "running": True}


def _recognition_ops_match_edge_scene_ids(edge: dict[str, Any]) -> tuple[int, int] | None:
    if "s" in edge:
        source = edge.get("s")
        target = edge.get("x")
    elif "reference" in edge:
        source = edge.get("reference")
        target = edge.get("frame")
    else:
        source = edge.get("y")
        target = edge.get("x")
    try:
        return int(str(source).lstrip("#")), int(str(target).lstrip("#"))
    except (TypeError, ValueError):
        return None


def _derive_recognition_ops_matrix_subset(
    matrix: dict[str, Any],
    *,
    scene_ids: list[int],
    cache_key: str,
    cache_path: Path,
    layer: int,
) -> dict[str, Any] | None:
    current_ids = list(dict.fromkeys(int(scene_id) for scene_id in scene_ids))
    current_set = set(current_ids)
    cached_ids = [
        int(scene_id)
        for scene_id in matrix.get("scene_ids", [])
        if isinstance(scene_id, int) or (isinstance(scene_id, str) and scene_id.isdigit())
    ]
    if not current_ids or not cached_ids or not current_set.issubset(set(cached_ids)):
        return None

    matches: list[dict[str, Any]] = []
    for edge in matrix.get("matches") if isinstance(matrix.get("matches"), list) else []:
        if not isinstance(edge, dict):
            continue
        pair = _recognition_ops_match_edge_scene_ids(edge)
        if pair is None:
            continue
        source_id, target_id = pair
        if source_id in current_set and target_id in current_set:
            matches.append(edge)

    return {
        **matrix,
        "cache_key": cache_key,
        "cache_path": str(cache_path),
        "cache_hit": True,
        "cache_stale": False,
        "cache_partial": False,
        "cache_derived": True,
        "derived_from_cache_key": matrix.get("cache_key"),
        "derived_removed_node_ids": sorted(set(cached_ids) - current_set),
        "layer": int(layer),
        "scene_ids": current_ids,
        "match_count": len(matches),
        "matches": matches,
        "expected_node_count": len(current_ids),
        "updated_at": matrix.get("updated_at") or time.time(),
    }


def __getattr__(name: str) -> Any:
    if name == "_BehaviorTreeExecutor":
        from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor

        return BehaviorTreeExecutor
    raise AttributeError(name)


def _sync_behavior_tree_executor_to_core() -> None:
    register_behavior_tree_executor(_BEHAVIOR_TREE_EXECUTOR)


def _raise_behavior_tree_execution_http_error(exc: BehaviorTreeExecutionError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _data_annotation_dir() -> Path:
    return _core_data_annotation_dir()


def _behavior_tree_executor_dir() -> Path:
    return _core_behavior_tree_executor_dir()


def _kernel_execution_state_path() -> Path:
    return _core_execution_state_path()


def _data_annotation_world_facts_path() -> Path:
    return _core_world_facts_path()


def _kernel_scheduler_state_path() -> Path:
    return _core_scheduler_state_path()


def _kernel_scheduler_settings_path() -> Path:
    return _core_scheduler_settings_path()


def _read_data_annotation_world_facts() -> dict[str, Any]:
    return _kernel_scheduler_control.read_world_facts(_data_annotation_world_facts_path())


def _record_kernel_scheduler_task_fact(task: dict[str, Any], result: str) -> None:
    _kernel_scheduler_control.record_scheduler_task_fact(task, result, world_facts_path=_data_annotation_world_facts_path())


def _persist_kernel_scheduler_status(status: dict[str, Any]) -> None:
    _kernel_scheduler_control.persist_kernel_scheduler_status(
        status,
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _read_kernel_scheduler_status() -> dict[str, Any]:
    return _kernel_scheduler_control.read_kernel_scheduler_status(_kernel_execution_state_path())


def _is_kernel_scheduler_live_empty(status: dict[str, Any]) -> bool:
    return is_kernel_scheduler_live_empty(status)


def _append_kernel_scheduler_log_once(status: dict[str, Any], kind: str, message: str) -> None:
    _kernel_scheduler_control.append_scheduler_log_once(status, kind, message)


def _normalize_kernel_scheduler_guard_items(status: dict[str, Any]) -> None:
    _sync_behavior_tree_executor_to_core()
    _kernel_scheduler_control.normalize_scheduler_guard_items(status)


def _kernel_scheduler_status(*, include_cell_logs: bool = True) -> dict[str, Any]:
    _sync_behavior_tree_executor_to_core()
    status = _core_kernel_scheduler_status(
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        include_cell_logs=include_cell_logs,
    )
    settings = _kernel_scheduler_control.read_scheduler_settings(
        scheduler_settings_path=_kernel_scheduler_settings_path()
    )
    # A legacy/buggy status write once persisted ``current_scene`` as a result
    # string (e.g. "success"), which then failed the int-typed response model
    # and turned /kernel-scheduler/status into a 500.  Keep the public
    # contract int|None regardless of what the persisted state contains.
    _coerce_status_current_scene(status)
    behavior_enabled = bool(settings.get("behavior_tree_enabled", True))
    status["behavior_tree_enabled"] = behavior_enabled
    if not behavior_enabled:
        status.update({
            "running": False,
            "guard_running": False,
            "guard_group_running": False,
            "phase": "behavior_tree_disabled",
            "message": "行为树已关闭",
        })
    return status


def _read_kernel_scheduler_tasks() -> list[dict[str, Any]]:
    return _kernel_scheduler_control.read_scheduler_tasks(
        scheduler_state_path=_kernel_scheduler_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        now=datetime.now(),
    )


def _write_kernel_scheduler_tasks(tasks: list[dict[str, Any]]) -> None:
    _kernel_scheduler_control.write_scheduler_tasks(tasks, scheduler_state_path=_kernel_scheduler_state_path())


def _data_annotation_task_supported(task: dict[str, Any]) -> bool:
    return _kernel_scheduler_control.task_supported(task)


def _kernel_scheduler_task_views(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _kernel_scheduler_control.scheduler_task_views(
        tasks,
        scheduler_settings_path=_kernel_scheduler_settings_path(),
    )


def _kernel_scheduler_task_plan_reason(task: dict[str, Any], due: bool) -> str:
    return _kernel_scheduler_control.scheduler_task_plan_reason(task, due)


def _data_annotation_world_facts_summary(facts: dict[str, Any]) -> dict[str, Any]:
    return _kernel_scheduler_control.world_facts_summary(facts)


def _build_kernel_scheduler_plan() -> dict[str, Any]:
    _sync_behavior_tree_executor_to_core()
    entry_id = DEFAULT_FANXIU_ENTRY_ID
    try:
        entry = resolve_fanxiu_entry(entry_id)
    except Exception:
        entry = None
    _ensure_engineering_scheduler_kernel(entry, entry_id)
    return _kernel_scheduler_control.build_scheduler_plan(
        entry=entry,
        entry_id=entry_id,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        scheduler_state_path=_kernel_scheduler_state_path(),
        scheduler_settings_path=_kernel_scheduler_settings_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _ensure_engineering_scheduler_kernel(entry: Any | None, entry_id: str) -> None:
    settings = _kernel_scheduler_control.read_scheduler_settings(
        scheduler_settings_path=_kernel_scheduler_settings_path()
    )
    if not (bool(settings.get("job_group_enabled", True)) and bool(settings.get("behavior_tree_enabled", True))):
        return
    if entry is None:
        return
    resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id)
    _behavior_tree_framework.ensure_kernel(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=_data_annotation_asset_tree_path(resolved_entry_id),
        scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
def _data_annotation_task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    return _kernel_scheduler_control.task_payload_with_meta(task)


def _kernel_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return _core_kernel_scheduler_run_now_task(tasks, task_id, payload_override)


def _prepare_behavior_tree_executor_for_scheduler_task(task: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    _sync_behavior_tree_executor_to_core()
    return _kernel_scheduler_control.prepare_kernel_scheduler_for_task(
        task,
        tasks,
        scheduler_state_path=_kernel_scheduler_state_path(),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _submit_data_annotation_task_cell(
    entry: UserDevice,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None,
    *,
    timeout_seconds: float | None = None,
    source: str = "",
) -> dict[str, Any]:
    _sync_behavior_tree_executor_to_core()
    cell_payload = dict(payload or {})
    if timeout_seconds is not None:
        cell_payload.setdefault("timeout_seconds", float(timeout_seconds))
        cell_payload.setdefault("max_execution_seconds", float(timeout_seconds))
    before_keys = {_scheduler_log_item_key(item) for item in _scheduler_log_items_for_cell()}
    try:
        status = _behavior_tree_framework.submit_task_cell(
            entry=entry,
            entry_id=entry_id,
            task_type=task_type,
            payload=cell_payload,
        )
    except BehaviorTreeExecutionError as exc:
        _raise_behavior_tree_execution_http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _coerce_status_current_scene(status)
    log_source = {"code": f"run_task_cell({task_type!r}, {cell_payload!r})"}
    if source:
        log_source["source"] = source
    return _record_cell_log(
        status,
        title=f"任务 cell：{task_type}",
        source=log_source,
        before_keys=before_keys,
    )


def _submit_data_annotation_code_cell(
    entry: UserDevice,
    entry_id: str,
    req: FanxiuKernelSchedulerCodeCellRequest,
    *,
    source: str = "",
) -> dict[str, Any]:
    _sync_behavior_tree_executor_to_core()
    before_keys = {_scheduler_log_item_key(item) for item in _scheduler_log_items_for_cell()}
    try:
        status = _behavior_tree_framework.submit_code_cell(
            entry=entry,
            entry_id=entry_id,
            code=req.code,
            timeout_seconds=req.timeout_seconds,
            max_output_chars=req.max_output_chars,
        )
    except BehaviorTreeExecutionError as exc:
        _raise_behavior_tree_execution_http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _coerce_status_current_scene(status)
    log_source = {
        "cmd": "submit_code_cell",
        "entry_id": entry_id,
        "code": req.code,
        "timeout_seconds": req.timeout_seconds,
        "max_output_chars": req.max_output_chars,
    }
    if source:
        log_source["source"] = source
    return _record_cell_log(
        status,
        title="代码 cell",
        source=log_source,
        before_keys=before_keys,
    )


def _serialize_fanxiu_pseudocode_card(card: FanxiuPseudoCodeCard) -> dict[str, Any]:
    return {
        "id": card.id,
        "scope": card.scope,
        "title": card.title or "",
        "body": card.body or "",
        "enabled": bool(card.enabled),
        "order_index": int(card.order_index or 0),
        "created_at": float(card.created_at or 0),
        "updated_at": float(card.updated_at or 0),
    }


def _list_fanxiu_pseudocode_card_rows(session: Session, user_id: int) -> list[FanxiuPseudoCodeCard]:
    return session.exec(
        select(FanxiuPseudoCodeCard)
        .where(FanxiuPseudoCodeCard.user_id == user_id)
        .order_by(FanxiuPseudoCodeCard.order_index.asc(), FanxiuPseudoCodeCard.created_at.asc())
    ).all()


def _next_fanxiu_pseudocode_card_order(session: Session, user_id: int, scope: str) -> int:
    rows = session.exec(
        select(FanxiuPseudoCodeCard.order_index)
        .where(FanxiuPseudoCodeCard.user_id == user_id)
        .where(FanxiuPseudoCodeCard.scope == scope)
    ).all()
    return max((int(value or 0) for value in rows), default=-1) + 1


FANXIU_PSEUDOCODE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})#([A-Za-z0-9_\-\u4e00-\u9fff（）()《》【】「」]+)?")


def _extract_fanxiu_pseudocode_refs(*segments: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for segment in segments:
        for match in FANXIU_PSEUDOCODE_REF_RE.finditer(segment or ""):
            image_no = int(match.group(1))
            label = (match.group(2) or "").strip()
            key = (image_no, label)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "ref": f"{image_no}#{label}" if label else f"{image_no}#",
                    "image_no": image_no,
                    "filename": f"{image_no:04d}.jpg",
                    "label": label,
                }
            )
    return refs


def _read_game_window2_pre_label_for_entry(entry: UserDevice, filename: str) -> dict[str, Any]:
    if entry.mode == "local":
        return _screenshot_game_window2_service_pre_label(filename)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/pre-label",
        payload={"filename": filename},
        action="截图预标注",
    )


def _normalize_fanxiu_pseudocode_image_context(data: dict[str, Any], filename: str, image_no: int) -> dict[str, Any]:
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        payload = {}
    size = payload.get("size") if isinstance(payload.get("size"), dict) else {}
    boxes: list[dict[str, Any]] = []
    raw_boxes = payload.get("boxes")
    if isinstance(raw_boxes, list):
        for index, raw_box in enumerate(raw_boxes, start=1):
            if not isinstance(raw_box, dict):
                continue
            try:
                x = int(raw_box.get("x", 0))
                y = int(raw_box.get("y", 0))
                w = int(raw_box.get("w", 0))
                h = int(raw_box.get("h", 0))
            except (TypeError, ValueError):
                continue
            boxes.append(
                {
                    "index": index,
                    "name": str(raw_box.get("name") or "").strip(),
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "xywh": [x, y, w, h],
                }
            )
    return {
        "image_no": image_no,
        "filename": filename,
        "pre_label_filename": str(data.get("filename") or f"{Path(filename).stem}_pre.json") if isinstance(data, dict) else f"{Path(filename).stem}_pre.json",
        "exists": bool(data.get("exists")) if isinstance(data, dict) else False,
        "size": {
            "width": int(size.get("width") or 0),
            "height": int(size.get("height") or 0),
        },
        "boxes": boxes,
    }


def _build_fanxiu_pseudocode_annotation_context(entry: UserDevice | None, card: FanxiuPseudoCodeCard) -> dict[str, Any]:
    refs = _extract_fanxiu_pseudocode_refs(card.title or "", card.body or "")
    image_map: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for ref in refs:
        filename = str(ref["filename"])
        image_no = int(ref["image_no"])
        if filename not in image_map:
            if entry is None:
                image_map[filename] = {
                    "image_no": image_no,
                    "filename": filename,
                    "exists": False,
                    "size": {"width": 0, "height": 0},
                    "boxes": [],
                    "error": "未选择设备，无法读取截图标注",
                }
                errors.append(f"{filename}: 未选择设备")
            else:
                try:
                    data = _read_game_window2_pre_label_for_entry(entry, filename)
                    image_map[filename] = _normalize_fanxiu_pseudocode_image_context(data, filename, image_no)
                except Exception as exc:
                    image_map[filename] = {
                        "image_no": image_no,
                        "filename": filename,
                        "exists": False,
                        "size": {"width": 0, "height": 0},
                        "boxes": [],
                        "error": str(exc),
                    }
                    errors.append(f"{filename}: {exc}")
        label = str(ref.get("label") or "").strip()
        if label:
            image_context = image_map[filename]
            matched_box = next((box for box in image_context.get("boxes", []) if box.get("name") == label), None)
            ref["matched_box"] = matched_box
            if matched_box is None:
                ref["error"] = f"{filename} 中没有标注框：{label}"

    return {
        "refs": refs,
        "images": list(image_map.values()),
        "errors": errors,
    }


def _serialize_fanxiu_pseudocode_card_for_compile(card: FanxiuPseudoCodeCard, entry: UserDevice | None) -> dict[str, Any]:
    payload = _serialize_fanxiu_pseudocode_card(card)
    payload["annotation_context"] = _build_fanxiu_pseudocode_annotation_context(entry, card)
    return payload


def _run_fanxiu_pseudocode_operation(action: str, operation) -> dict[str, Any]:
    try:
        return operation()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _visual_macro_run_key(user_id: int, entry_id: str, card_id: str) -> str:
    return f"{user_id}:{entry_id}:{card_id}"


@status_router.get("/game-window2/pseudocode-cards", response_model=FanxiuPseudoCodeCardListResponse)
def list_fanxiu_game_window2_pseudocode_cards(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    rows = _list_fanxiu_pseudocode_card_rows(session, current_user.id)
    return {"items": [_serialize_fanxiu_pseudocode_card(row) for row in rows]}


@status_router.post("/game-window2/pseudocode-cards", response_model=FanxiuPseudoCodeCardRead)
def create_fanxiu_game_window2_pseudocode_card(
    req: FanxiuPseudoCodeCardCreateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    now = time.time()
    card = FanxiuPseudoCodeCard(
        user_id=current_user.id,
        scope=req.scope,
        title=req.title,
        body=req.body,
        enabled=req.enabled,
        order_index=req.order_index if req.order_index is not None else _next_fanxiu_pseudocode_card_order(session, current_user.id, req.scope),
        created_at=now,
        updated_at=now,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    return _serialize_fanxiu_pseudocode_card(card)


@status_router.patch("/game-window2/pseudocode-cards/{card_id}", response_model=FanxiuPseudoCodeCardRead)
def update_fanxiu_game_window2_pseudocode_card(
    card_id: str,
    req: FanxiuPseudoCodeCardUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    card = session.get(FanxiuPseudoCodeCard, card_id)
    if not card or card.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="伪代码卡片不存在")
    updates = req.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(card, key, value)
    card.updated_at = time.time()
    session.add(card)
    session.commit()
    session.refresh(card)
    return _serialize_fanxiu_pseudocode_card(card)


@status_router.delete("/game-window2/pseudocode-cards/{card_id}")
def delete_fanxiu_game_window2_pseudocode_card(
    card_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    card = session.get(FanxiuPseudoCodeCard, card_id)
    if not card or card.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="伪代码卡片不存在")
    session.delete(card)
    session.commit()
    return {"ok": True, "id": card_id}


@status_router.post("/game-window2/pseudocode/compile", response_model=FanxiuPseudoCodeRunResponse)
def compile_fanxiu_game_window2_pseudocode(
    req: FanxiuPseudoCodeCompileRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    entry = _get_user_device_or_404(session, current_user, req.entry_id) if req.entry_id.strip() else None
    rows = _list_fanxiu_pseudocode_card_rows(session, current_user.id)
    cards = [_serialize_fanxiu_pseudocode_card_for_compile(row, entry) for row in rows]
    return _run_fanxiu_pseudocode_operation(
        "编译",
        lambda: compile_fanxiu_pseudocode(cards, model=req.model, timeout=req.timeout),
    )


@status_router.post("/game-window2/pseudocode/start", response_model=FanxiuPseudoCodeRunResponse)
def start_fanxiu_game_window2_pseudocode(
    req: FanxiuPseudoCodeStartRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    return _run_fanxiu_pseudocode_operation(
        "启动",
        lambda: start_fanxiu_pseudocode_script(timeout=req.timeout),
    )


@status_router.post("/game-window2/visual-script/run", response_model=FanxiuPseudoCodeRunResponse)
def run_fanxiu_game_window2_visual_script(
    req: FanxiuVisualScriptRunRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    rows = _list_fanxiu_pseudocode_card_rows(session, current_user.id)
    cards = [_serialize_fanxiu_pseudocode_card(row) for row in rows]
    base_payload = req.model_dump(
        exclude_none=True,
        exclude={"entry_id", "card_id", "timeout", "tick_interval"},
    )

    def run_match(payload: dict[str, Any]) -> dict[str, Any]:
        return _match_game_window2_service(payload) if entry.mode == "local" else _match_remote_game_window2(entry, payload)

    def run_click(payload: dict[str, Any]) -> dict[str, Any]:
        return _click_game_window2_service(payload) if entry.mode == "local" else _click_remote_game_window2(entry, payload)

    def run_drag(payload: dict[str, Any]) -> dict[str, Any]:
        return _drag_game_window2_service(payload) if entry.mode == "local" else _drag_remote_game_window2(entry, payload)

    def run_activate(payload: dict[str, Any]) -> dict[str, Any]:
        return (
            _activate_game_window2_service(payload)
            if entry.mode == "local"
            else _activate_remote_game_window2(entry, payload)
        )

    run_key = _visual_macro_run_key(current_user.id, req.entry_id, req.card_id)
    stop_event = begin_visual_macro_run(run_key)

    def run_operation() -> dict[str, Any]:
        try:
            activate_result = run_activate(
                {
                    "title": base_payload.get("title"),
                    "title_match": base_payload.get("title_match") or "contains",
                    "click_title": True,
                }
            )
            result = run_fanxiu_visual_script(
                cards,
                selected_card_id=req.card_id,
                base_payload=base_payload,
                callbacks=VisualMacroCallbacks(match=run_match, click=run_click, drag=run_drag),
                timeout=req.timeout,
                tick_interval=req.tick_interval,
                stop_event=stop_event,
            )
            title = activate_result.get("window_title") or activate_result.get("title") or "目标窗口"
            result["log"] = f"{time.strftime('%H:%M:%S')} 激活窗口：{title}\n{result.get('log') or ''}".rstrip()
            return result
        finally:
            end_visual_macro_run(run_key, stop_event)

    return _run_fanxiu_pseudocode_operation(
        "执行",
        run_operation,
    )


@status_router.post("/game-window2/visual-script/stop")
def stop_fanxiu_game_window2_visual_script(
    req: FanxiuVisualScriptStopRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if current_user.id is None:
        raise HTTPException(status_code=401, detail="用户未登录")
    _get_user_device_or_404(session, current_user, req.entry_id)
    stopped = stop_visual_macro_run(_visual_macro_run_key(current_user.id, req.entry_id, req.card_id))
    return {"ok": True, "stopped": stopped}


@status_router.post("/game-window2/stream-token", response_model=FanxiuGameWindow2StreamTokenResponse)
def create_fanxiu_game_window2_stream_token(
    req: FanxiuGameWindow2StreamTokenRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, req.entry_id)
    expires = timedelta(hours=FANXIU_GAME_WINDOW2_STREAM_TOKEN_EXPIRE_HOURS)
    token = create_access_token(
        {
            "sub": FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE,
            "scope": FANXIU_GAME_WINDOW2_STREAM_TOKEN_SCOPE,
            "user_id": current_user.id,
            "entry_id": req.entry_id,
        },
        expires_delta=expires,
    )
    return {
        "token": token,
        "expires_in_seconds": int(expires.total_seconds()),
    }


_GAME_WINDOW2_SERVICE_STATUS_CACHE_TTL = 10.0
_game_window2_service_status_cache_lock = threading.Lock()
_game_window2_service_status_cache: tuple[float, dict[str, Any]] | None = None


def _clone_game_window2_service_status(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _get_game_window2_service_status_cache() -> dict[str, Any] | None:
    now = time.monotonic()
    with _game_window2_service_status_cache_lock:
        if _game_window2_service_status_cache is None:
            return None
        cached_at, status = _game_window2_service_status_cache
        if now - cached_at > _GAME_WINDOW2_SERVICE_STATUS_CACHE_TTL:
            return None
        return _clone_game_window2_service_status(status)


def _set_game_window2_service_status_cache(status: dict[str, Any]) -> None:
    global _game_window2_service_status_cache
    with _game_window2_service_status_cache_lock:
        _game_window2_service_status_cache = (time.monotonic(), _clone_game_window2_service_status(status))


def _clear_game_window2_service_status_cache() -> None:
    global _game_window2_service_status_cache
    with _game_window2_service_status_cache_lock:
        _game_window2_service_status_cache = None


@status_router.get("/game-window2/service-status")
async def get_fanxiu_game_window2_service_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    cached = _get_game_window2_service_status_cache()
    if cached is not None:
        return cached
    status = await asyncio.to_thread(get_game_window_service_status)
    _set_game_window2_service_status_cache(status)
    return status


@status_router.get("/game-window2/frame-status")
def get_fanxiu_game_window2_frame_status(
    entry_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, entry_id)
    return {"ok": True, "entry_id": entry_id, **get_mumu_adb_stream_frame_status()}


@status_router.post("/game-window2/service-start")
def start_fanxiu_game_window2_service(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _clear_game_window2_service_status_cache()
    try:
        result = start_game_window_service()
        service = result.get("service") if isinstance(result, dict) else None
        if isinstance(service, dict):
            _set_game_window2_service_status_cache(service)
        return result
    except GameWindowServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@status_router.get("/game-window2/stream")
def stream_fanxiu_game_window2(
    token: str = Query(...),
    title: Optional[str] = Query(None),
    title_match: str = Query("contains", pattern="^(contains|exact)$"),
    fps: float = Query(12.0, ge=1.0, le=30.0),
    quality: int = Query(82, ge=1, le=100),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("client", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("0", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    adb_screencap: bool = Query(False),
    auto_dismiss_popup: bool = Query(False),
    popup_check_interval: float = Query(3.0, ge=1.0, le=30.0),
    session: Session = Depends(get_session),
):
    entry, _current_user = _decode_game_window2_stream_token(session, token)
    params = _game_window2_stream_params(
        title=title,
        title_match=title_match,
        fps=fps,
        quality=quality,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
        auto_dismiss_popup=auto_dismiss_popup,
        popup_check_interval=popup_check_interval,
    )
    if adb_screencap:
        return StreamingResponse(
            stream_mumu_adb_screencap_mjpeg(fps=fps),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )
    if entry.mode == "local":
        return _stream_game_window2_service(params)
    return _stream_response_from_requests(_open_remote_game_window2_stream(entry, params))


@status_router.get("/game-window2/service-stream")
def stream_fanxiu_game_window2_service(
    title: Optional[str] = Query(None),
    title_match: str = Query("contains", pattern="^(contains|exact)$"),
    fps: float = Query(12.0, ge=1.0, le=30.0),
    quality: int = Query(82, ge=1, le=100),
    mode: str = Query("screen", pattern="^(auto|printwindow|screen)$"),
    area: str = Query("client", pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query("0", pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    auto_dismiss_popup: bool = Query(False),
    popup_check_interval: float = Query(3.0, ge=1.0, le=30.0),
    _token_device: Any = Depends(verify_api_token),
):
    params = _game_window2_stream_params(
        title=title,
        title_match=title_match,
        fps=fps,
        quality=quality,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
        auto_dismiss_popup=auto_dismiss_popup,
        popup_check_interval=popup_check_interval,
    )
    return _stream_game_window2_service(params)


@status_router.post("/game-window2/input/click")
def click_fanxiu_game_window2(
    req: FanxiuGameWindow2ClickRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_click_payload(req)
    if entry.mode == "local":
        return _click_game_window2_service(payload)
    return _click_remote_game_window2(entry, payload)


@status_router.post("/game-window2/input/activate")
def activate_fanxiu_game_window2(
    req: FanxiuGameWindow2ActivateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_activate_payload(req)
    if entry.mode == "local":
        return _activate_game_window2_service(payload)
    return _activate_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/activate")
def activate_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceActivateRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _activate_game_window2_service(_game_window2_activate_payload(req))


@status_router.post("/game-window2/service-input/click")
def click_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceClickRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _click_game_window2_service(_game_window2_click_payload(req))


@status_router.post("/game-window2/input/drag")
def drag_fanxiu_game_window2(
    req: FanxiuGameWindow2DragRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_drag_payload(req)
    if entry.mode == "local":
        return _drag_game_window2_service(payload)
    return _drag_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/drag")
def drag_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceDragRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _drag_game_window2_service(_game_window2_drag_payload(req))


@status_router.post("/game-window2/input/keyevent")
def keyevent_fanxiu_game_window2(
    req: FanxiuGameWindow2KeyeventRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_keyevent_payload(req)
    if entry.mode == "local":
        return _keyevent_game_window2_service(payload)
    return _keyevent_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/keyevent")
def keyevent_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceKeyeventRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _keyevent_game_window2_service(_game_window2_keyevent_payload(req))


@status_router.post("/game-window2/input/text")
def text_fanxiu_game_window2(
    req: FanxiuGameWindow2TextRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_text_payload(req)
    if entry.mode == "local":
        return _text_game_window2_service(payload)
    return _text_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-input/text")
def text_fanxiu_game_window2_service(
    req: FanxiuGameWindow2ServiceTextRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _text_game_window2_service(_game_window2_text_payload(req))


@status_router.post("/game-window2/screencap")
def screencap_fanxiu_game_window2(
    req: FanxiuGameWindow2ScreencapRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _screencap_game_window2_service(
            prefer_cached=req.prefer_cached,
            cached_only=req.cached_only,
            title=req.title,
            title_match=req.title_match,
            mode=req.mode,
            area=req.area,
            crop=req.crop,
            trim_border=req.trim_border,
            rotate=req.rotate,
            fixed_width=req.fixed_width,
            fixed_height=req.fixed_height,
        )
    return _remote_game_window2_screencap(entry)


@status_router.get("/game-window2/service-screencap")
def screencap_fanxiu_game_window2_service(
    _token_device: Any = Depends(verify_api_token),
):
    return _screencap_game_window2_service()


def _data_annotation_asset_tree_path(entry_id: str) -> Path:
    return _core_data_annotation_asset_tree_path(entry_id)


@status_router.get("/data-annotation/asset-tree")
def get_fanxiu_data_annotation_asset_tree(
    entry_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, entry_id)
    path = _data_annotation_asset_tree_path(entry_id)
    snapshot = read_data_annotation_asset_tree_snapshot(path)
    return {
        "ok": True,
        "entry_id": entry_id,
        "exists": snapshot.exists,
        "tree": snapshot.tree,
        "revision": snapshot.revision,
        "updated_at": snapshot.updated_at,
    }


@status_router.get("/data-annotation/recognition-ops")
def get_fanxiu_data_annotation_recognition_ops(
    entry_id: str,
    layer: int = 2,
    recompute: bool = False,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, entry_id)
    path = _data_annotation_asset_tree_path(entry_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="资产树不存在")

    def load_latest_shared_scene_matrix(
        scene_ids: list[int],
        *,
        layer: int,
        expected_cache_key: str,
        expected_cache_path: Path,
        allow_derive_subset: bool,
    ) -> dict[str, Any] | None:
        scene_set = {int(scene_id) for scene_id in scene_ids}
        cache_dir = _BEHAVIOR_TREE_EXECUTOR._scene_match_cache_dir()
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for candidate_path in cache_dir.glob("*.json"):
            try:
                payload = json.loads(candidate_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("score_mode") != "strict_scene_identity":
                continue
            if payload.get("cache_key") == expected_cache_key:
                continue
            if int(payload.get("layer") or layer) != int(layer):
                continue
            cached_ids = [
                int(scene_id)
                for scene_id in payload.get("scene_ids", [])
                if isinstance(scene_id, int) or (isinstance(scene_id, str) and scene_id.isdigit())
            ]
            if not cached_ids:
                continue
            overlap = len(scene_set.intersection(cached_ids))
            if overlap <= 0:
                continue
            try:
                mtime = candidate_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if allow_derive_subset and scene_set.issubset(set(cached_ids)):
                derived = _derive_recognition_ops_matrix_subset(
                    payload,
                    scene_ids=scene_ids,
                    cache_key=expected_cache_key,
                    cache_path=expected_cache_path,
                    layer=int(layer),
                )
                if derived is not None:
                    expected_cache_path.parent.mkdir(parents=True, exist_ok=True)
                    expected_cache_path.write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
                    return derived
            payload["cache_path"] = str(candidate_path)
            payload["cache_hit"] = True
            payload["cache_stale"] = True
            payload["cache_partial"] = set(cached_ids) != scene_set
            payload["expected_node_count"] = len(scene_ids)
            candidates.append((mtime, overlap, payload))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
        return candidates[0][2]

    try:
        tree = _BEHAVIOR_TREE_EXECUTOR._load_asset_tree(path)
        images = _BEHAVIOR_TREE_EXECUTOR._index_images(tree)
        ctx = {
            "entry_id": entry_id,
            "asset_tree_path": path,
            "asset_tree": tree,
            "images": images,
        }
        scene_ids = [
            int(scene_id)
            for scene_id, image in images.items()
            if isinstance(image, dict) and int(View(image).layer) == int(layer)
        ]
        image_dir = path.parent / "images"
        computable_scene_ids = [
            int(scene_id)
            for scene_id in scene_ids
            if str(images.get(int(scene_id), {}).get("filename") or "").strip()
            and (image_dir / str(images.get(int(scene_id), {}).get("filename") or "")).is_file()
        ]
        computable_scene_id_set = set(computable_scene_ids)
        skipped_scene_ids = [int(scene_id) for scene_id in scene_ids if int(scene_id) not in computable_scene_id_set]
        cache_key = _BEHAVIOR_TREE_EXECUTOR._scene_match_cache_key(ctx, computable_scene_ids, threshold=None)
        cache_path = _BEHAVIOR_TREE_EXECUTOR._scene_match_cache_dir() / f"{cache_key}.json"
        recompute_state: dict[str, Any] | None = None
        if bool(recompute):
            recompute_state = _submit_recognition_ops_recompute(cache_key=cache_key, ctx=ctx, layer=int(layer), scene_ids=computable_scene_ids)
        if cache_path.is_file():
            matrix = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(matrix, dict) or matrix.get("cache_key") != cache_key:
                matrix = {}
            matrix["cache_hit"] = True
        else:
            matrix = load_latest_shared_scene_matrix(
                computable_scene_ids,
                layer=int(layer),
                expected_cache_key=cache_key,
                expected_cache_path=cache_path,
                allow_derive_subset=not bool(recompute),
            )
            if matrix is None:
                matrix = {
                    "cache_key": cache_key,
                    "cache_path": str(cache_path),
                    "cache_hit": False,
                    "cache_missing": True,
                    "score_mode": "strict_scene_identity",
                    "layer": int(layer),
                    "threshold": "per_scene",
                    "scene_ids": computable_scene_ids,
                    "match_count": 0,
                    "matches": [],
                    "updated_at": None,
                    "expected_node_count": len(computable_scene_ids),
                }
        matrix["expected_node_count"] = len(scene_ids)
        matrix["skipped_node_ids"] = skipped_scene_ids
        if recompute_state is None:
            recompute_state = _recognition_ops_recompute_view(cache_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = build_recognition_ops_report(
        matrix,
        images,
        navigation_incidents=list_navigation_incident_summaries(entry_id),
        recognition_ambiguities=list_recognition_ambiguity_summaries(entry_id),
    )
    result.update(
        {
            "ok": True,
            "entry_id": entry_id,
            "asset_tree_updated_at": path.stat().st_mtime if path.is_file() else 0,
            "recompute": recompute_state,
        }
    )
    return result


@status_router.get("/data-annotation/recognition-ops/incidents/{incident_id}")
def get_fanxiu_data_annotation_navigation_incident(
    incident_id: str,
    entry_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, entry_id)
    try:
        incident = load_navigation_incident(entry_id, incident_id, include_frames=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if incident is None:
        raise HTTPException(status_code=404, detail="导航停滞事件不存在")
    return {"ok": True, "entry_id": entry_id, "incident": incident}


@status_router.get("/data-annotation/recognition-ops/ambiguities/{signature}")
def get_fanxiu_data_annotation_recognition_ambiguity(
    signature: str,
    entry_id: str,
    recompute: bool = False,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, entry_id)
    try:
        ambiguity = load_recognition_ambiguity(entry_id, signature, include_frames=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if ambiguity is None:
        raise HTTPException(status_code=404, detail="识别并列事件不存在")
    if recompute:
        samples = ambiguity.get("sample_frames") if isinstance(ambiguity.get("sample_frames"), list) else []
        sample = samples[-1] if samples and isinstance(samples[-1], dict) else None
        frame_url = (
            (ambiguity.get("frame_data_urls") or {}).get(str(sample.get("path") or ""))
            if isinstance(sample, dict) and isinstance(ambiguity.get("frame_data_urls"), dict)
            else None
        )
        tied_scene_ids = [
            int(item)
            for item in ambiguity.get("tied_scene_ids") or []
            if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
        ]
        if not frame_url or len(tied_scene_ids) < 2:
            raise HTTPException(status_code=409, detail="识别并列事件没有可重算的代表原帧")
        path = _data_annotation_asset_tree_path(entry_id)
        tree = _BEHAVIOR_TREE_EXECUTOR._load_asset_tree(path)
        ctx = {
            "entry_id": entry_id,
            "asset_tree_path": path,
            "asset_tree": tree,
            "images": _BEHAVIOR_TREE_EXECUTOR._index_images(tree),
            "asset_tree_revision": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
            "_disable_recognition_ambiguity_recording": True,
        }
        trace: list[dict[str, Any]] = []
        scene_id, score, status = _BEHAVIOR_TREE_EXECUTOR._identify_scene_number_in_graph_candidates(
            ctx,
            frame_url,
            tied_scene_ids,
            layer_label=f"layer{int(ambiguity.get('layer') or 0)}",
            trace=trace,
        )
        ambiguity["recompute"] = {
            "scene_id": scene_id,
            "score": round(float(score or 0.0), 3),
            "status": status,
            "calculated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "asset_tree_sha256": ctx["asset_tree_revision"],
            "trace": trace,
        }
    return {"ok": True, "entry_id": entry_id, "ambiguity": ambiguity}


def _backup_data_annotation_asset_tree_before_save(path: Path) -> None:
    if not path.is_file():
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = path.with_name(f"{path.name}.autosave-{stamp}.bak")
    backup_path.write_bytes(path.read_bytes())
    backups = sorted(
        path.parent.glob(f"{path.name}.autosave-*.bak"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale_backup in backups[20:]:
        try:
            stale_backup.unlink()
        except OSError:
            pass


@status_router.put("/data-annotation/asset-tree")
def save_fanxiu_data_annotation_asset_tree(
    req: FanxiuDataAnnotationAssetTreeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, req.entry_id)
    path = _data_annotation_asset_tree_path(req.entry_id)
    try:
        snapshot = save_data_annotation_asset_tree_snapshot(
            path,
            req.tree,
            entry_id=req.entry_id,
            expected_revision=req.base_revision,
            before_write=lambda: _backup_data_annotation_asset_tree_before_save(path),
        )
    except FanxiuDataAnnotationAssetTreeConflict as exc:
        raise HTTPException(status_code=409, detail="资产树已在其它页面更新；当前编辑仍保留在本页") from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "entry_id": req.entry_id,
        "exists": True,
        "tree": snapshot.tree,
        "revision": snapshot.revision,
        "updated_at": snapshot.updated_at,
    }


@status_router.post("/data-annotation/save-frame")
def save_fanxiu_data_annotation_frame(
    req: FanxiuDataAnnotationSaveFrameRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    frame_status: dict[str, Any] = {}
    try:
        if req.fresh_capture:
            if entry.mode == "local":
                data, frame_status = capture_fresh_mumu_adb_stream_frame()
            else:
                response = _remote_game_window2_screencap(entry)
                data = bytes(response.body)
        elif req.current_frame_data_url:
            data = decode_data_annotation_image_data_url(req.current_frame_data_url)
        else:
            raise ValueError("current_frame_data_url 与 fresh_capture 至少需要一个")
        asset = None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"暂时无法取得最新画面：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    width = 0
    height = 0
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception:
        pass
    tree_snapshot = None
    try:
        semantic_insert = req.same_level_as_scene_id is not None
        if semantic_insert and any((req.asset_node is not None, req.parent_id, req.after_node_id, req.filename)):
            raise ValueError(
                "same_level_as_scene_id 不能与 asset_node、parent_id、after_node_id 或 filename 同时使用"
            )
        if req.title is not None and not semantic_insert:
            raise ValueError("title 仅与 same_level_as_scene_id 配合使用")

        node = None
        after_node_id = req.after_node_id
        if semantic_insert:
            current = read_data_annotation_asset_tree_snapshot(
                _data_annotation_asset_tree_path(req.entry_id)
            )
            after_node_id = resolve_data_annotation_scene_node_id(
                current.tree,
                int(req.same_level_as_scene_id),
            )
            node = {
                "id": f"image-{uuid.uuid4().hex}",
                "type": "image",
                "title": str(req.title or "").strip(),
                "shapes": [],
            }
        elif req.asset_node is not None:
            node = dict(req.asset_node)

        if node is not None:
            if width:
                node["width"] = width
            if height:
                node["height"] = height
            saved = save_data_annotation_frame_tree_node(
                _data_annotation_asset_tree_path(req.entry_id),
                data,
                node,
                entry_id=req.entry_id,
                parent_id=req.parent_id,
                after_node_id=after_node_id,
                expected_revision=req.base_revision,
                before_write=lambda: _backup_data_annotation_asset_tree_before_save(
                    _data_annotation_asset_tree_path(req.entry_id)
                ),
            )
            asset = saved.asset
            tree_snapshot = saved.snapshot
        else:
            asset = save_data_annotation_image_bytes(data, entry_id=req.entry_id, filename=req.filename)
    except FanxiuDataAnnotationAssetTreeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    assert asset is not None
    return {
        "ok": True,
        "entry_id": asset.entry_id,
        "filename": asset.filename,
        "path": os.fspath(asset.path),
        "directory": os.fspath(asset.path.parent),
        "width": width,
        "height": height,
        "fresh_capture": bool(req.fresh_capture),
        "frame_sequence": int(frame_status.get("sequence") or 0),
        "captured_at": float(frame_status.get("captured_at") or 0.0),
        "tree": tree_snapshot.tree if tree_snapshot is not None else None,
        "revision": tree_snapshot.revision if tree_snapshot is not None else None,
        "updated_at": tree_snapshot.updated_at if tree_snapshot is not None else None,
    }


@status_router.get("/data-annotation/image")
def get_fanxiu_data_annotation_image(
    entry_id: str,
    filename: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, entry_id)
    try:
        asset = resolve_data_annotation_image_asset(filename, entry_id=entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not asset.exists:
        raise HTTPException(
            status_code=404,
            detail="data-annotation 图片不存在",
            headers={"Cache-Control": "no-store"},
        )
    # 前端已经按 entry + 节点 + 文件名持有对象 URL；HTTP 层不再缓存同名覆盖帧或瞬时失败。
    return FileResponse(asset.path, headers={"Cache-Control": "private, no-store"})


@status_router.post("/game-window2/save-frame")
def save_fanxiu_game_window2_frame(
    req: FanxiuGameWindow2SaveFrameRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_save_frame_payload(req)
    if entry.mode == "local":
        return _save_game_window2_service(payload)
    return _save_remote_game_window2_frame(entry, payload)


@status_router.post("/game-window2/service-save-frame")
def save_fanxiu_game_window2_frame_service(
    req: FanxiuGameWindow2ServiceSaveFrameRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _save_game_window2_service(_game_window2_save_frame_payload(req))


@status_router.post("/game-window2/burst/save")
def save_fanxiu_game_window2_burst_frame(
    req: FanxiuGameWindow2BurstFrameRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _save_burst_game_window2_service(_game_window2_save_frame_payload(req))


@status_router.post("/game-window2/service-burst/save")
def save_fanxiu_game_window2_burst_frame_service(
    req: FanxiuGameWindow2ServiceBurstFrameRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _save_burst_game_window2_service(_game_window2_save_frame_payload(req))


@status_router.post("/game-window2/burst/list")
def list_fanxiu_game_window2_burst_frames(
    req: FanxiuGameWindow2BurstListRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _list_burst_game_window2_service(req.model_dump(exclude_none=True, exclude={"entry_id"}))


@status_router.post("/game-window2/service-burst/list")
def list_fanxiu_game_window2_burst_frames_service(
    req: FanxiuGameWindow2ServiceBurstListRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _list_burst_game_window2_service(req.model_dump(exclude_none=True))


@status_router.get("/game-window2/burst/image")
def get_fanxiu_game_window2_burst_frame_image(
    entry_id: str = Query(...),
    filename: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _burst_game_window2_service_image(filename)


@status_router.get("/game-window2/service-burst/image")
def get_fanxiu_game_window2_burst_frame_image_service(
    filename: str = Query(...),
    _token_device: Any = Depends(verify_api_token),
):
    return _burst_game_window2_service_image(filename)


@status_router.post("/game-window2/burst/clear")
def clear_fanxiu_game_window2_burst_frames(
    req: FanxiuGameWindow2BurstClearRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _clear_burst_game_window2_service()


@status_router.post("/game-window2/service-burst/clear")
def clear_fanxiu_game_window2_burst_frames_service(
    _req: FanxiuGameWindow2ServiceBurstClearRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _clear_burst_game_window2_service()


@status_router.post("/game-window2/burst/import")
def import_fanxiu_game_window2_burst_frames(
    req: FanxiuGameWindow2BurstImportRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="连拍缓存暂仅支持本机设备")
    return _import_burst_game_window2_service(req.filenames)


@status_router.post("/game-window2/service-burst/import")
def import_fanxiu_game_window2_burst_frames_service(
    req: FanxiuGameWindow2ServiceBurstImportRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _import_burst_game_window2_service(req.filenames)


@status_router.post("/game-window2/match")
def match_fanxiu_game_window2_screenshot_box(
    req: FanxiuGameWindow2MatchRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    payload = _game_window2_match_payload(req)
    if entry.mode == "local":
        return _match_game_window2_service(payload)
    return _match_remote_game_window2(entry, payload)


@status_router.post("/game-window2/service-match")
def match_fanxiu_game_window2_screenshot_box_service(
    req: FanxiuGameWindow2ServiceMatchRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _match_game_window2_service(_game_window2_match_payload(req))


@status_router.get("/game-window2/match/image")
def get_fanxiu_game_window2_match_image(
    entry_id: str = Query(...),
    filename: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _match_game_window2_service_image(filename)
    return _remote_game_window2_match_image(entry, filename)


@status_router.get("/game-window2/service-match/image")
def get_fanxiu_game_window2_match_image_service(
    filename: str = Query(...),
    _token_device: Any = Depends(verify_api_token),
):
    return _match_game_window2_service_image(filename)


@kernel_scheduler_router.get("/kernel-scheduler/status", response_model=FanxiuKernelSchedulerStatus)
def get_fanxiu_kernel_scheduler_status(
    entry_id: str = Query("", max_length=128),
    include_cell_logs: bool = Query(True),
    include_logs: bool = Query(True),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if entry_id:
        entry = _get_user_device_or_404(session, current_user, entry_id)
        resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        _sync_behavior_tree_executor_to_core()
        _behavior_tree_framework.ensure_kernel(
            entry=entry,
            entry_id=resolved_entry_id,
            asset_tree_path=_data_annotation_asset_tree_path(resolved_entry_id),
            scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
    payload = dict(_kernel_scheduler_status(include_cell_logs=include_cell_logs))
    if not include_cell_logs:
        payload.pop("cell_logs", None)
    if not include_logs:
        payload.pop("logs", None)
    return FanxiuKernelSchedulerStatus.model_validate(payload)


@kernel_scheduler_router.get(
    "/kernel-scheduler/info-window",
    response_model=FanxiuInfoWindowControlStatus,
)
def get_fanxiu_data_annotation_info_window(
    entry_id: str = Query("", max_length=128),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if entry_id:
        _get_user_device_or_404(session, current_user, entry_id)
    from backend.core.fanxiu.windows_info_window import read_info_window_control_status

    return FanxiuInfoWindowControlStatus.model_validate(
        read_info_window_control_status(user_id=int(current_user.id), ensure_renderer=True)
    )


@kernel_scheduler_router.post(
    "/kernel-scheduler/info-window/settings",
    response_model=FanxiuInfoWindowControlStatus,
)
def set_fanxiu_data_annotation_info_window(
    req: FanxiuInfoWindowSettingsRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    if req.entry_id:
        _get_user_device_or_404(session, current_user, req.entry_id)
    from backend.core.fanxiu.windows_info_window import update_info_window_control

    return FanxiuInfoWindowControlStatus.model_validate(
        update_info_window_control(
            req.model_dump(exclude={"entry_id"}),
            user_id=int(current_user.id),
        )
    )


@service_router.get(
    "/kernel-scheduler/service/status",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def get_fanxiu_kernel_scheduler_service_status(
    entry_id: str = Query("", max_length=128),
    include_logs: bool = Query(True),
    session: Session = Depends(get_session),
):
    if entry_id:
        entry = _get_service_user_device_or_404(session, entry_id)
        resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        _sync_behavior_tree_executor_to_core()
        _behavior_tree_framework.ensure_kernel(
            entry=entry,
            entry_id=resolved_entry_id,
            asset_tree_path=_data_annotation_asset_tree_path(resolved_entry_id),
            scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
    payload = dict(_kernel_scheduler_status())
    # Cell logs have their own endpoint; omitting them here avoids shipping the same
    # large history twice during Kernel scheduler page bootstrap.
    payload.pop("cell_logs", None)
    if not include_logs:
        payload.pop("logs", None)
    return FanxiuKernelSchedulerStatus.model_validate(payload)


def _set_fanxiu_kernel_scheduler_behavior_tree_enabled(
    entry: Any,
    entry_id: str,
    req: FanxiuKernelSchedulerBehaviorTreeRequest,
) -> FanxiuKernelSchedulerStatus:
    _sync_behavior_tree_executor_to_core()
    status = _behavior_tree_framework.set_kernel_enabled(
        entry=entry,
        entry_id=entry_id,
        enabled=req.enabled,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuKernelSchedulerStatus.model_validate(status)


def _restart_fanxiu_kernel_scheduler_kernel(
    entry: Any,
    entry_id: str,
    req: FanxiuKernelSchedulerKernelRestartRequest,
) -> FanxiuKernelSchedulerStatus:
    _sync_behavior_tree_executor_to_core()
    status = _behavior_tree_framework.restart_kernel(
        entry=entry,
        entry_id=entry_id,
        timeout_seconds=req.timeout_seconds,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuKernelSchedulerStatus.model_validate(status)


def _restart_fanxiu_kernel_scheduler_device(
    entry_id: str,
) -> FanxiuKernelSchedulerDeviceRestartResponse:
    """Interrupt the current Cell and force-restart the shared MuMu device.

    The resident Kernel and Scheduler ownership are deliberately left intact.
    """
    _sync_behavior_tree_executor_to_core()
    before = dict(_kernel_scheduler_status(include_cell_logs=False))
    if bool(before.get("running")):
        _behavior_tree_framework.interrupt_current_cell(
            entry_id,
        execution_state_path=_kernel_execution_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )

    device = dict(
        recover_mumu_device(
            vmindex="1",
            reason="manual_kernel_scheduler_page_request",
            force_restart=True,
        )
    )
    device_status = str(device.get("status") or "unknown")
    recovered = bool(device.get("recovered"))
    if not recovered or device_status != "healthy":
        detail = str(device.get("last_error") or device.get("recovery_skipped") or device_status)
        raise HTTPException(status_code=503, detail=f"模拟器重启失败：{detail}")

    scheduler = FanxiuKernelSchedulerStatus.model_validate(
        _kernel_scheduler_status(include_cell_logs=False)
    )
    return FanxiuKernelSchedulerDeviceRestartResponse(
        ok=True,
        recovered=True,
        status=device_status,
        message="模拟器已重启，游戏画面可用",
        device=device,
        scheduler=scheduler,
    )


@kernel_scheduler_router.post("/kernel-scheduler/behavior-tree/set", response_model=FanxiuKernelSchedulerStatus)
def set_fanxiu_kernel_scheduler_behavior_tree(
    req: FanxiuKernelSchedulerBehaviorTreeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _set_fanxiu_kernel_scheduler_behavior_tree_enabled(entry, entry_id, req)


@service_router.post(
    "/kernel-scheduler/service/behavior-tree/set",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def set_fanxiu_kernel_scheduler_service_behavior_tree(
    req: FanxiuKernelSchedulerBehaviorTreeRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _set_fanxiu_kernel_scheduler_behavior_tree_enabled(entry, entry_id, req)


@kernel_scheduler_router.post("/kernel-scheduler/kernel/restart", response_model=FanxiuKernelSchedulerStatus)
def restart_fanxiu_kernel_scheduler_kernel(
    req: FanxiuKernelSchedulerKernelRestartRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _restart_fanxiu_kernel_scheduler_kernel(entry, entry_id, req)


@service_router.post(
    "/kernel-scheduler/service/kernel/restart",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def restart_fanxiu_kernel_scheduler_service_kernel(
    req: FanxiuKernelSchedulerKernelRestartRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _restart_fanxiu_kernel_scheduler_kernel(entry, entry_id, req)


@kernel_scheduler_router.post(
    "/kernel-scheduler/device/restart",
    response_model=FanxiuKernelSchedulerDeviceRestartResponse,
)
def restart_fanxiu_kernel_scheduler_device(
    req: FanxiuKernelSchedulerDeviceRestartRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _restart_fanxiu_kernel_scheduler_device(entry_id)


@service_router.post(
    "/kernel-scheduler/service/device/restart",
    response_model=FanxiuKernelSchedulerDeviceRestartResponse,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def restart_fanxiu_kernel_scheduler_service_device(
    req: FanxiuKernelSchedulerDeviceRestartRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _restart_fanxiu_kernel_scheduler_device(entry_id)


@kernel_scheduler_router.post("/kernel-scheduler/cells/task", response_model=FanxiuKernelSchedulerStatus)
def submit_fanxiu_kernel_scheduler_task_cell(
    req: FanxiuKernelSchedulerTaskCellRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    payload = dict(req.payload)
    if req.effective_now is not None:
        payload["effective_now"] = req.effective_now.isoformat(sep=" ")
    return FanxiuKernelSchedulerStatus.model_validate(
        _submit_data_annotation_task_cell(
            entry,
            entry_id,
            req.task_type,
            payload,
            timeout_seconds=req.timeout_seconds,
        )
    )


@service_router.post(
    "/kernel-scheduler/service/cells/task",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def submit_fanxiu_kernel_scheduler_service_task_cell(
    req: FanxiuKernelSchedulerTaskCellRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    payload = dict(req.payload)
    if req.effective_now is not None:
        payload["effective_now"] = req.effective_now.isoformat(sep=" ")
    return FanxiuKernelSchedulerStatus.model_validate(
        _submit_data_annotation_task_cell(
            entry,
            entry_id,
            req.task_type,
            payload,
            timeout_seconds=req.timeout_seconds,
            source="service",
        )
    )


@kernel_scheduler_router.post("/kernel-scheduler/cells/code", response_model=FanxiuKernelSchedulerStatus)
def submit_fanxiu_kernel_scheduler_code_cell(
    req: FanxiuKernelSchedulerCodeCellRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return FanxiuKernelSchedulerStatus.model_validate(_submit_data_annotation_code_cell(entry, entry_id, req))


@service_router.post(
    "/kernel-scheduler/service/cells/code",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def submit_fanxiu_kernel_scheduler_service_code_cell(
    req: FanxiuKernelSchedulerCodeCellRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return FanxiuKernelSchedulerStatus.model_validate(
        _submit_data_annotation_code_cell(entry, entry_id, req, source="service")
    )


@kernel_scheduler_router.post("/kernel-scheduler/task/stop", response_model=FanxiuKernelSchedulerStatus)
def stop_fanxiu_kernel_scheduler_task(
    req: FanxiuKernelSchedulerStopRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Compatibility endpoint: stop only the current business task.

    This endpoint must not be treated as resident behavior-tree service shutdown.
    """
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return _stop_behavior_tree_executor_task(req)


def _stop_behavior_tree_executor_task(
    req: FanxiuKernelSchedulerStopRequest,
) -> FanxiuKernelSchedulerStatus:
    _sync_behavior_tree_executor_to_core()
    status = _behavior_tree_framework.take_ai_control(
        req.entry_id or "",
        interrupt_any_cell=True,
        scheduler_state_path=_kernel_scheduler_state_path(),
        scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuKernelSchedulerStatus.model_validate(status)


@service_router.post(
    "/kernel-scheduler/service/task/stop",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def stop_fanxiu_kernel_scheduler_service_task(
    req: FanxiuKernelSchedulerStopRequest,
):
    return _stop_behavior_tree_executor_task(req)


def _set_fanxiu_kernel_scheduler_guard_item(
    entry: Any,
    entry_id: str,
    req: FanxiuKernelSchedulerGuardRequest,
) -> FanxiuKernelSchedulerStatus:
    _sync_behavior_tree_executor_to_core()
    status = _behavior_tree_framework.set_guard_item_enabled(
        entry=entry,
        entry_id=entry_id,
        guard_id=req.guard_id,
        enabled=req.enabled,
        interval_seconds=req.interval_seconds,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuKernelSchedulerStatus.model_validate(status)


def _set_fanxiu_kernel_scheduler_guard_group(
    entry: Any,
    entry_id: str,
    req: FanxiuKernelSchedulerGuardGroupRequest,
) -> FanxiuKernelSchedulerStatus:
    _sync_behavior_tree_executor_to_core()
    status = _behavior_tree_framework.set_guard_group_enabled(
        entry=entry,
        entry_id=entry_id,
        enabled=req.enabled,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuKernelSchedulerStatus.model_validate(status)


@kernel_scheduler_router.post("/kernel-scheduler/guard/set", response_model=FanxiuKernelSchedulerStatus)
def set_fanxiu_kernel_scheduler_guard(
    req: FanxiuKernelSchedulerGuardRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _set_fanxiu_kernel_scheduler_guard_item(entry, entry_id, req)


@kernel_scheduler_router.post("/kernel-scheduler/guard/group/set", response_model=FanxiuKernelSchedulerStatus)
def set_fanxiu_kernel_scheduler_guard_group(
    req: FanxiuKernelSchedulerGuardGroupRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _set_fanxiu_kernel_scheduler_guard_group(entry, entry_id, req)


@service_router.post(
    "/kernel-scheduler/service/guard/set",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def set_fanxiu_kernel_scheduler_service_guard(
    req: FanxiuKernelSchedulerGuardRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _set_fanxiu_kernel_scheduler_guard_item(entry, entry_id, req)


@service_router.post(
    "/kernel-scheduler/service/guard/group/set",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def set_fanxiu_kernel_scheduler_service_guard_group(
    req: FanxiuKernelSchedulerGuardGroupRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _set_fanxiu_kernel_scheduler_guard_group(entry, entry_id, req)


@kernel_scheduler_router.get("/kernel-scheduler/logs", response_model=FanxiuKernelSchedulerLogResponse)
def get_fanxiu_kernel_scheduler_logs(
    limit: int = Query(80, ge=1, le=2000),
    scope: str = Query("", max_length=64),
    item_id: str = Query("", max_length=128),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _sync_behavior_tree_executor_to_core()
    log_items = _core_kernel_scheduler_logs(
        limit=limit,
        scope=scope,
        item_id=item_id,
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    entries = log_entries(log_items)
    return FanxiuKernelSchedulerLogResponse(entries=entries, path=str(_kernel_execution_state_path()))


def _scheduler_log_item_key(item: dict[str, Any]) -> str:
    return log_entry_base_id(item)


def _scheduler_log_items_for_cell(limit: int = 5000) -> list[dict[str, Any]]:
    return _core_kernel_scheduler_logs(
        limit=limit,
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )


def _record_cell_log(
    status: dict[str, Any],
    *,
    title: str,
    source: dict[str, Any],
    before_keys: set[str],
) -> dict[str, Any]:
    after_items = _scheduler_log_items_for_cell()
    new_items = [item for item in after_items if _scheduler_log_item_key(item) not in before_keys]
    new_items = sorted(new_items, key=lambda item: float(item.get("ts") or 0))
    if not new_items:
        new_items = [
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "kind": "info",
                "scope": "cell",
                "item_id": "framework",
                "message": f"提交 cell：{title}",
                "ts": str(time.time()),
            }
        ]
    entries = [entry.model_dump() for entry in log_entries(new_items)]
    cell_id = f"cell-{hashlib.sha1((title + cell_source(source) + str(time.time())).encode('utf-8')).hexdigest()[:16]}"
    cell = {
        "id": cell_id,
        "title": title,
        "source_kind": "command",
        "source": cell_source(source),
        "started_at": entries[0].get("time", ""),
        "ended_at": entries[-1].get("time", ""),
        "entries": entries,
    }
    persisted_status = _read_kernel_scheduler_status()
    existing = persisted_status.get("cell_logs") if isinstance(persisted_status.get("cell_logs"), list) else []
    merged_status = {**persisted_status, **status}
    merged_status["cell_logs"] = [cell, *[item for item in existing if isinstance(item, dict) and item.get("id") != cell_id]][:100]
    _kernel_scheduler_control.persist_kernel_scheduler_status(
        merged_status,
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return merged_status


@kernel_scheduler_router.get("/kernel-scheduler/cell-logs", response_model=FanxiuKernelSchedulerCellLogResponse)
def get_fanxiu_kernel_scheduler_cell_logs(
    limit: int = Query(20, ge=1, le=200),
    log_limit: int = Query(1000, ge=1, le=5000),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _sync_behavior_tree_executor_to_core()
    status = _read_kernel_scheduler_status()
    response_cells = persisted_cell_views(status, limit)
    if len(response_cells) < limit:
        log_items = _core_kernel_scheduler_logs(
            limit=log_limit,
            execution_state_path=_kernel_execution_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
        response_cells = historical_cell_views(log_items, response_cells, limit)
    return FanxiuKernelSchedulerCellLogResponse(cells=response_cells, path=str(_kernel_execution_state_path()))


@kernel_scheduler_router.get("/kernel-scheduler/world-facts", response_model=FanxiuDataAnnotationWorldFactsResponse)
def get_fanxiu_data_annotation_world_facts(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationWorldFactsResponse(
        facts=_read_data_annotation_world_facts(),
        path=str(_data_annotation_world_facts_path()),
    )


def _doctor_watch_latest_payload_for_frontend() -> dict[str, Any]:
    payload = _kernel_scheduler_control.read_doctor_watch_latest()
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict) or "auto_run_due" not in snapshot:
        return payload
    # The Kernel scheduler page only consumes the summary fields, not the full auto-run trace.
    return {
        **payload,
        "snapshot": {
            **snapshot,
            "auto_run_due": None,
        },
    }


@kernel_scheduler_router.get("/kernel-scheduler/doctor-watch/latest", response_model=FanxiuDataAnnotationDoctorWatchLatestResponse)
def get_fanxiu_data_annotation_doctor_watch_latest(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationDoctorWatchLatestResponse.model_validate(_doctor_watch_latest_payload_for_frontend())


@kernel_scheduler_router.post("/kernel-scheduler/doctor-watch/ensure", response_model=FanxiuDataAnnotationDoctorWatchEnsureResponse)
def ensure_fanxiu_data_annotation_doctor_watch(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuDataAnnotationDoctorWatchEnsureResponse.model_validate(_kernel_scheduler_control.ensure_doctor_watch_background())


@kernel_scheduler_router.delete("/kernel-scheduler/logs", response_model=FanxiuKernelSchedulerLogResponse)
def clear_fanxiu_kernel_scheduler_logs(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _sync_behavior_tree_executor_to_core()
    _core_clear_kernel_scheduler_logs(
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    status = _read_kernel_scheduler_status()
    status["cell_logs"] = []
    _kernel_scheduler_control.persist_kernel_scheduler_status(
        status,
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    return FanxiuKernelSchedulerLogResponse(entries=[], path=str(_kernel_execution_state_path()))


@kernel_scheduler_router.get("/kernel-scheduler/tasks", response_model=FanxiuKernelSchedulerTasksResponse)
def get_fanxiu_kernel_scheduler_tasks(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    settings = _kernel_scheduler_control.read_scheduler_settings(
        scheduler_settings_path=_kernel_scheduler_settings_path()
    )
    tasks = _read_kernel_scheduler_tasks()
    return FanxiuKernelSchedulerTasksResponse(
        tasks=[
            FanxiuKernelSchedulerTaskItem.model_validate(item)
            for item in _kernel_scheduler_task_views(tasks)
        ],
        job_group_enabled=bool(settings.get("job_group_enabled", True)),
        path=str(_kernel_scheduler_state_path()),
    )


@kernel_scheduler_router.get(
    "/kernel-scheduler/state-inspection",
    response_model=FanxiuGameStateInspectionStatus,
)
def get_fanxiu_game_state_inspection_status(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuGameStateInspectionStatus.model_validate(read_game_state_inspection_status())


@kernel_scheduler_router.get("/kernel-scheduler/plan", response_model=FanxiuKernelSchedulerPlanResponse)
def get_fanxiu_kernel_scheduler_plan(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    return FanxiuKernelSchedulerPlanResponse.model_validate(_build_kernel_scheduler_plan())


@kernel_scheduler_router.get(
    "/kernel-scheduler/time-sequence",
    response_model=FanxiuKernelSchedulerTimeSequenceResponse,
)
def get_fanxiu_kernel_scheduler_time_sequence(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    tasks = _read_kernel_scheduler_tasks()
    return FanxiuKernelSchedulerTimeSequenceResponse(
        groups=_kernel_scheduler_control.scheduler_time_sequence_groups(
            tasks,
            scheduler_settings_path=_kernel_scheduler_settings_path(),
        )
    )


@kernel_scheduler_router.put(
    "/kernel-scheduler/time-sequence",
    response_model=FanxiuKernelSchedulerTimeSequenceResponse,
)
def put_fanxiu_kernel_scheduler_time_sequence(
    request: FanxiuKernelSchedulerTimeSequenceUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _kernel_scheduler_control.update_scheduler_time_sequence(
        [group.model_dump() for group in request.groups],
        scheduler_settings_path=_kernel_scheduler_settings_path(),
    )
    _sync_behavior_tree_executor_to_core()
    tasks = _read_kernel_scheduler_tasks()
    return FanxiuKernelSchedulerTimeSequenceResponse(
        groups=_kernel_scheduler_control.scheduler_time_sequence_groups(
            tasks,
            scheduler_settings_path=_kernel_scheduler_settings_path(),
        )
    )


@kernel_scheduler_router.put("/kernel-scheduler/tasks", response_model=FanxiuKernelSchedulerTasksResponse)
def put_fanxiu_kernel_scheduler_tasks(
    tasks: list[FanxiuKernelSchedulerTaskUpdate],
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    payload = _kernel_scheduler_control.update_scheduler_tasks(
        [item.model_dump(exclude_none=True) for item in tasks],
        scheduler_state_path=_kernel_scheduler_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        now=datetime.now(),
    )
    _sync_behavior_tree_executor_to_core()
    return FanxiuKernelSchedulerTasksResponse(
        tasks=[
            FanxiuKernelSchedulerTaskItem.model_validate(item)
            for item in _kernel_scheduler_task_views(payload)
        ],
        job_group_enabled=bool(_kernel_scheduler_control.read_scheduler_settings(
            scheduler_settings_path=_kernel_scheduler_settings_path()
        ).get("job_group_enabled", True)),
        path=str(_kernel_scheduler_state_path()),
    )


@kernel_scheduler_router.get("/kernel-scheduler/settings", response_model=FanxiuKernelSchedulerTasksResponse)
def get_fanxiu_kernel_scheduler_settings(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    settings = _kernel_scheduler_control.read_scheduler_settings(
        scheduler_settings_path=_kernel_scheduler_settings_path()
    )
    tasks = _read_kernel_scheduler_tasks()
    return FanxiuKernelSchedulerTasksResponse(
        tasks=[
            FanxiuKernelSchedulerTaskItem.model_validate(item)
            for item in _kernel_scheduler_task_views(tasks)
        ],
        job_group_enabled=bool(settings.get("job_group_enabled", True)),
        path=str(_kernel_scheduler_state_path()),
    )


@kernel_scheduler_router.put("/kernel-scheduler/settings", response_model=FanxiuKernelSchedulerTasksResponse)
def put_fanxiu_kernel_scheduler_settings(
    req: FanxiuKernelSchedulerSettingsRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _sync_behavior_tree_executor_to_core()
    if req.entry_id and not req.job_group_enabled:
        entry = _get_user_device_or_404(session, current_user, req.entry_id)
        entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
        control = _behavior_tree_framework.take_ai_control(
            entry_id,
            # Selecting AI is an ownership handoff, not merely a future
            # dispatch preference.  A Cell without Scheduler metadata can
            # still own the shared GUI, so it must be interrupted as well.
            interrupt_any_cell=True,
            scheduler_state_path=_kernel_scheduler_state_path(),
            scheduler_settings_path=_kernel_scheduler_settings_path(),
            execution_state_path=_kernel_execution_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
        settings = {"job_group_enabled": bool(control.get("job_group_enabled", False))}
    else:
        if req.entry_id:
            entry = _get_user_device_or_404(session, current_user, req.entry_id)
            entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
            _behavior_tree_framework.set_kernel_enabled(
                entry=entry,
                entry_id=entry_id,
                enabled=True,
                asset_tree_path=_data_annotation_asset_tree_path(entry_id),
                scheduler_settings_path=_kernel_scheduler_settings_path(),
                execution_state_path=_kernel_execution_state_path(),
                world_facts_path=_data_annotation_world_facts_path(),
            )
        if req.job_group_enabled:
            settings = _kernel_scheduler_control.resume_engineering_control()
        else:
            settings = _kernel_scheduler_control.set_scheduler_job_group_enabled(
                False,
                scheduler_settings_path=_kernel_scheduler_settings_path(),
            )
    tasks = _read_kernel_scheduler_tasks()
    return FanxiuKernelSchedulerTasksResponse(
        tasks=[
            FanxiuKernelSchedulerTaskItem.model_validate(item)
            for item in _kernel_scheduler_task_views(tasks)
        ],
        job_group_enabled=bool(settings.get("job_group_enabled", True)),
        path=str(_kernel_scheduler_state_path()),
    )


def _run_now_fanxiu_kernel_scheduler_task(
    entry: Any,
    entry_id: str,
    req: FanxiuKernelSchedulerRunNowRequest,
) -> FanxiuKernelSchedulerStatus:
    _sync_behavior_tree_executor_to_core()
    try:
        payload = dict(req.payload)
        if req.effective_now is not None:
            payload["effective_now"] = req.effective_now.isoformat(sep=" ")
        status = _kernel_scheduler_control.run_now_scheduler_task(
            entry=entry,
            entry_id=entry_id,
            task_id=req.task_id,
            payload_override=payload,
            business_time_mode=req.business_time_mode,
            interrupt_same_group=req.interrupt_same_group,
            scheduler_state_path=_kernel_scheduler_state_path(),
            scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            asset_tree_path=_data_annotation_asset_tree_path(entry_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FanxiuKernelSchedulerStatus.model_validate(status)


def _trigger_once_fanxiu_kernel_scheduler_task(
    req: FanxiuKernelSchedulerTriggerOnceRequest,
) -> FanxiuKernelSchedulerTriggerOnceResponse:
    try:
        next_time = _kernel_scheduler_control.trigger_scheduler_task_once(
            req.task_id,
            scheduler_state_path=_kernel_scheduler_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FanxiuKernelSchedulerTriggerOnceResponse(
        task_id=req.task_id,
        next_time=next_time,
    )


def _set_fanxiu_kernel_scheduler_task_next_time(
    req: FanxiuKernelSchedulerNextTimeRequest,
) -> FanxiuKernelSchedulerNextTimeResponse:
    try:
        next_time = _kernel_scheduler_control.set_scheduler_task_next_time(
            req.task_id,
            req.next_time,
            scheduler_state_path=_kernel_scheduler_state_path(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FanxiuKernelSchedulerNextTimeResponse(
        task_id=req.task_id,
        next_time=next_time,
    )


def _run_due_fanxiu_kernel_scheduler_tasks(
    entry: Any,
    entry_id: str,
) -> FanxiuKernelSchedulerStatus:
    _sync_behavior_tree_executor_to_core()
    status = _kernel_scheduler_control.run_due_scheduler_tasks(
        entry=entry,
        entry_id=entry_id,
        scheduler_state_path=_kernel_scheduler_state_path(),
        scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )
    return FanxiuKernelSchedulerStatus.model_validate(status)


@kernel_scheduler_router.post("/kernel-scheduler/task/run-now", response_model=FanxiuKernelSchedulerStatus)
def run_now_fanxiu_kernel_scheduler_task(
    req: FanxiuKernelSchedulerRunNowRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Submit one concrete Job Cell using the requested business clock.

    ``planned`` is the global default: a future Job runs now while its business
    clock is anchored just after the stored ``next_time``. ``current`` runs the
    same Cell against the real wall clock.
    """
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _run_now_fanxiu_kernel_scheduler_task(entry, entry_id, req)


@kernel_scheduler_router.post(
    "/kernel-scheduler/task/trigger-once",
    response_model=FanxiuKernelSchedulerTriggerOnceResponse,
)
def trigger_once_fanxiu_kernel_scheduler_task(
    req: FanxiuKernelSchedulerTriggerOnceRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Set ``next_time=now``; the external Scheduler decides when it runs."""

    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, req.entry_id)
    return _trigger_once_fanxiu_kernel_scheduler_task(req)


@kernel_scheduler_router.put(
    "/kernel-scheduler/task/next-time",
    response_model=FanxiuKernelSchedulerNextTimeResponse,
)
def set_fanxiu_kernel_scheduler_task_next_time(
    req: FanxiuKernelSchedulerNextTimeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Set or clear one Job's explicit ``next_time`` value."""

    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _get_user_device_or_404(session, current_user, req.entry_id)
    return _set_fanxiu_kernel_scheduler_task_next_time(req)


@status_router.post(
    "/kernel-scheduler/service/task/run-now",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def run_now_fanxiu_kernel_scheduler_service_task(
    req: FanxiuKernelSchedulerRunNowRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _run_now_fanxiu_kernel_scheduler_task(entry, entry_id, req)


@kernel_scheduler_router.post("/kernel-scheduler/run-due", response_model=FanxiuKernelSchedulerStatus)
def run_due_fanxiu_kernel_scheduler_tasks(
    req: FanxiuKernelSchedulerRunDueRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _run_due_fanxiu_kernel_scheduler_tasks(entry, entry_id)


@status_router.post(
    "/kernel-scheduler/service/run-due",
    response_model=FanxiuKernelSchedulerStatus,
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_FANXIU_KERNEL_SCHEDULER_CONTROL))],
)
def run_due_fanxiu_kernel_scheduler_service_tasks(
    req: FanxiuKernelSchedulerRunDueRequest,
    session: Session = Depends(get_session),
):
    entry = _get_service_user_device_or_404(session, req.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or req.entry_id)
    return _run_due_fanxiu_kernel_scheduler_tasks(entry, entry_id)


@status_router.post("/data-annotation/ocr-frame", response_model=FanxiuDataAnnotationOcrFrameResponse)
def recognize_fanxiu_data_annotation_ocr_frame(
    req: FanxiuDataAnnotationOcrFrameRequest,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    _log_data_annotation_ocr_frame_request(request, req, current_user)
    try:
        return _recognize_data_annotation_ocr_frame(req.image_data_url, options=req.options)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@status_router.post("/data-annotation/remove-background", response_model=FanxiuDataAnnotationRemoveBackgroundResponse)
def remove_fanxiu_data_annotation_background_api(
    req: FanxiuDataAnnotationRemoveBackgroundRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    try:
        return remove_fanxiu_data_annotation_background(
            req.image_data_url,
            model=req.model,
            alpha_matting=req.alpha_matting,
            post_process_mask=req.post_process_mask,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@status_router.post("/data-annotation/macro/annotate", response_model=FanxiuDataAnnotationMacroAnnotateResponse)
def annotate_fanxiu_data_annotation_macro_shape(
    req: FanxiuDataAnnotationMacroAnnotateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    try:
        return _annotate_game_macro_shape_with_ai(req, current_user=current_user, session=session)
    except (AiAppConfigError, OllamaClientError, ValueError, RuntimeError) as exc:
        return FanxiuDataAnnotationMacroAnnotateResponse(
            ok=False,
            used_ai=False,
            box=_clamp_game_macro_box(req.fallback_box.model_dump(), req.fallback_box, req.frame_width, req.frame_height),
            confidence=0,
            label="",
            reason=str(exc),
            raw="",
        )


@status_router.post("/game-window2/screenshot/list")
def list_fanxiu_game_window2_screenshot(
    req: FanxiuGameWindow2ScreenshotListRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _screenshot_game_window2_service_list()
    return _remote_game_window2_screenshot_json(entry, "service-screenshot/list", action="截图列表")


@status_router.post("/game-window2/service-screenshot/list")
def list_fanxiu_game_window2_screenshot_service(
    _token_device: Any = Depends(verify_api_token),
):
    return _screenshot_game_window2_service_list()


@status_router.post("/game-window2/screenshot/delete")
def delete_fanxiu_game_window2_screenshot(
    req: FanxiuGameWindow2ScreenshotDeleteRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _delete_screenshot_game_window2_service_image(req.filename)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/delete",
        payload={"filename": req.filename},
        action="截图删除",
    )


@status_router.post("/game-window2/service-screenshot/delete")
def delete_fanxiu_game_window2_screenshot_service(
    req: FanxiuGameWindow2ServiceScreenshotDeleteRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _delete_screenshot_game_window2_service_image(req.filename)


@status_router.get("/game-window2/screenshot/image")
def get_fanxiu_game_window2_screenshot_image(
    entry_id: str = Query(...),
    filename: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, entry_id)
    if entry.mode == "local":
        return _screenshot_game_window2_service_image(filename)
    return _remote_game_window2_screenshot_image(entry, filename)


@status_router.get("/game-window2/service-screenshot/image")
def get_fanxiu_game_window2_screenshot_image_service(
    filename: str = Query(...),
    _token_device: Any = Depends(verify_api_token),
):
    return _screenshot_game_window2_service_image(filename)


@status_router.post("/game-window2/screenshot/pre-label")
def get_fanxiu_game_window2_screenshot_pre_label(
    req: FanxiuGameWindow2ScreenshotPreLabelRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _screenshot_game_window2_service_pre_label(req.filename)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/pre-label",
        payload={"filename": req.filename},
        action="截图预标注",
    )


@status_router.post("/game-window2/service-screenshot/pre-label")
def get_fanxiu_game_window2_screenshot_pre_label_service(
    req: FanxiuGameWindow2ServiceScreenshotPreLabelRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _screenshot_game_window2_service_pre_label(req.filename)


@status_router.put("/game-window2/screenshot/pre-label")
def save_fanxiu_game_window2_screenshot_pre_label(
    req: FanxiuGameWindow2ScreenshotPreLabelSaveRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_feature_access(session, feature_key="fanxiu", current_user=current_user)
    entry = _get_user_device_or_404(session, current_user, req.entry_id)
    if entry.mode == "local":
        return _save_screenshot_game_window2_service_pre_label(req.filename, req.payload)
    return _remote_game_window2_screenshot_json(
        entry,
        "service-screenshot/pre-label",
        method="put",
        payload={"filename": req.filename, "payload": req.payload},
        action="截图预标注保存",
    )


@status_router.put("/game-window2/service-screenshot/pre-label")
def save_fanxiu_game_window2_screenshot_pre_label_service(
    req: FanxiuGameWindow2ServiceScreenshotPreLabelSaveRequest,
    _token_device: Any = Depends(verify_api_token),
):
    return _save_screenshot_game_window2_service_pre_label(req.filename, req.payload)


@inventory_router.get("/inventory/wardrobe-hall", response_model=FanxiuWardrobeHallSnapshot)
def get_fanxiu_wardrobe_hall(session: Session = Depends(get_session)):
    database_payload = load_inventory_hall_snapshot(session, "wardrobe_hall")
    if database_payload:
        return FanxiuWardrobeHallSnapshot.model_validate(database_payload)
    try:
        payload = load_wardrobe_hall()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuWardrobeHallSnapshot.model_validate(payload)


@inventory_router.put("/inventory/wardrobe-hall", response_model=FanxiuWardrobeHallSnapshot)
def update_fanxiu_wardrobe_hall(
    payload: FanxiuWardrobeHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_payload = payload.model_dump(mode="json")
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = sync_hall_note_refs(
        session,
        fanxiu_user,
        normalized_payload,
        note_kind=FANXIU_WARDROBE_KIND,
        sync_note_fields=sync_wardrobe_note_fields,
    )

    if touched_existing_note:
        session.commit()

    row = upsert_inventory_hall_snapshot(
        session,
        "wardrobe_hall",
        normalized_payload,
        source_kind="manual",
        entity_name="衣装阁",
    )
    return FanxiuWardrobeHallSnapshot.model_validate(row.payload)


def _submit_catalog_collection(kind: str) -> None:
    """图鉴采集的 HTTP 编排：检查运行权，提交正式 Cell，保留诊断与错误契约。

    调用前完成写权限检查；本函数会从真实游戏采集并落盘。
    各图鉴的快照读取、响应模型和完整性判定仍由其端点负责。
    """
    label, source, timeout, output_limit, hall = {
        "wardrobe": ("衣装", "wardrobe-hall", 120.0, 2000, True),
        "magic_treasure": ("法宝", "magic-treasure-hall", 120.0, 2000, True),
        "xianyuan": ("仙缘图鉴", "xianyuan-atlas", 180.0, 3000, False),
        "gongfa": ("个人功法", "gongfa-atlas", 120.0, 3000, False),
    }[kind]
    execution_status = _kernel_scheduler_control.kernel_scheduler_status(
        scheduler_settings_path=_kernel_scheduler_settings_path(),
        execution_state_path=_kernel_execution_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
    )
    if execution_status.get("running"):
        if hall:
            current_task = str(execution_status.get("task_type") or "当前 Cell")
            message = str(execution_status.get("message") or "Kernel 调度器 正在执行其它任务")
            detail = f"Kernel 调度器忙碌（{current_task}）：{message}，请稍后再从游戏更新"
        else:
            detail = f"Kernel 调度器 正在执行其它 Cell，请稍后更新{label}"
        raise HTTPException(status_code=409, detail=detail)
    entry_id = DEFAULT_FANXIU_ENTRY_ID
    try:
        entry = resolve_fanxiu_entry(entry_id)
    except Exception as exc:
        if not hall:
            raise
        raise HTTPException(status_code=409, detail=f"Kernel 调度器 入口不可用：{exc}") from exc
    request = FanxiuKernelSchedulerCodeCellRequest(
        entry_id=entry_id,
        code=build_catalog_collection_code(kind),
        timeout_seconds=timeout,
        max_output_chars=output_limit,
    )
    try:
        _submit_data_annotation_code_cell(entry, entry_id, request, source=source)
    except Exception as exc:
        if hall and isinstance(exc, HTTPException):
            raise
        action = "实时更新" if hall else "更新"
        raise HTTPException(status_code=409, detail=f"{label}{action}未执行：{exc}") from exc


@inventory_router.post(
    "/inventory/wardrobe-hall/collect",
    response_model=FanxiuWardrobeHallSnapshot,
)
def collect_fanxiu_wardrobe_hall(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Run one ordinary read-only Cell and return the persisted wardrobe snapshot."""

    ensure_fanxiu_write_permission(current_user, session)
    _submit_catalog_collection('wardrobe')
    session.expire_all()
    payload = load_inventory_hall_snapshot(session, "wardrobe_hall")
    if not payload or not payload.get("runtime_complete"):
        raise HTTPException(status_code=502, detail="衣装动态插桩未生成完整数据库快照")
    return FanxiuWardrobeHallSnapshot.model_validate(payload)


@inventory_router.get("/inventory/spirit-beast-hall", response_model=FanxiuSpiritBeastHallSnapshot)
def get_fanxiu_spirit_beast_hall():
    try:
        payload = load_spirit_beast_hall()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuSpiritBeastHallSnapshot.model_validate(payload)


@inventory_router.put("/inventory/spirit-beast-hall", response_model=FanxiuSpiritBeastHallSnapshot)
def update_fanxiu_spirit_beast_hall(
    payload: FanxiuSpiritBeastHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_payload = payload.model_dump(mode="json")
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = sync_hall_note_refs(
        session,
        fanxiu_user,
        normalized_payload,
        note_kind=FANXIU_SPIRIT_BEAST_KIND,
        sync_note_fields=sync_wardrobe_note_fields,
    )

    if touched_existing_note:
        session.commit()

    try:
        saved_payload = save_spirit_beast_hall(normalized_payload)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修灵兽仓库失败：{exc}") from exc
    return FanxiuSpiritBeastHallSnapshot.model_validate(saved_payload)


@inventory_router.get("/inventory/magic-treasure-hall", response_model=FanxiuMagicTreasureHallSnapshot)
def get_fanxiu_magic_treasure_hall(session: Session = Depends(get_session)):
    payload = load_inventory_hall_snapshot(session, "magic_treasure_hall") or {}
    return FanxiuMagicTreasureHallSnapshot.model_validate(payload)


@inventory_router.get("/inventory/xianyuan-atlas")
def get_fanxiu_xianyuan_atlas(session: Session = Depends(get_session)):
    # The loader overlays versioned gift eligibility onto the last runtime
    # snapshot, so this read never needs to operate the game.
    from backend.core.fanxiu.instrumentation.xianyuan_atlas import (
        load_xianyuan_atlas_snapshot,
    )

    return load_xianyuan_atlas_snapshot(session)


@inventory_router.get("/wiki/guide-videos")
def get_fanxiu_guide_videos(
    query: str = Query(default="", max_length=200),
    source_id: str = Query(default="", max_length=300),
    platform: str = Query(default="", max_length=30),
    role: str = Query(default="", max_length=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
):
    from backend.core.fanxiu.catalog.guide_videos import query_guide_videos

    return query_guide_videos(
        query=query,
        source_id=source_id,
        platform=platform,
        role=role,
        page=page,
        page_size=page_size,
    )


@inventory_router.post("/wiki/guide-videos/sync")
def sync_fanxiu_guide_videos(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    from backend.core.fanxiu.catalog.guide_videos import start_guide_video_sync

    ensure_fanxiu_write_permission(current_user, session)
    return start_guide_video_sync()


@inventory_router.get("/wiki/guide-videos/research-file")
def get_fanxiu_guide_video_research_file(
    item_id: str = Query(min_length=1, max_length=200),
    kind: str = Query(pattern="^(media|document|transcript)$"),
):
    from backend.core.fanxiu.catalog.guide_video_research import resolve_research_artifact

    try:
        path = resolve_research_artifact(item_id, kind)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="攻略研究文件不存在") from exc
    media_type = {
        "media": "video/mp4",
        "document": "text/html; charset=utf-8",
        "transcript": "text/plain; charset=utf-8",
    }[kind]
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-cache"})


@inventory_router.post("/inventory/xianyuan-atlas/collect")
def collect_fanxiu_xianyuan_atlas(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    _submit_catalog_collection('xianyuan')
    session.expire_all()
    from backend.core.fanxiu.instrumentation.xianyuan_atlas import load_xianyuan_atlas_snapshot

    payload = load_xianyuan_atlas_snapshot(session)
    if not payload.get("runtime_complete"):
        raise HTTPException(status_code=502, detail="仙缘动态插桩未生成完整快照")
    return payload


@inventory_router.get("/inventory/gongfa-atlas")
def get_fanxiu_gongfa_atlas(session: Session = Depends(get_session)):
    from backend.core.fanxiu.instrumentation.gongfa_atlas import load_gongfa_atlas_snapshot

    return load_gongfa_atlas_snapshot(session)


@inventory_router.get("/inventory/gongfa-atlas/books/{book_id}")
def get_fanxiu_gongfa_atlas_book_detail(
    book_id: int,
    session: Session = Depends(get_session),
):
    from backend.core.fanxiu.instrumentation.gongfa_atlas import (
        load_gongfa_atlas_book_detail,
    )

    try:
        return load_gongfa_atlas_book_detail(session, book_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@inventory_router.post("/inventory/gongfa-atlas/collect")
def collect_fanxiu_gongfa_atlas(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    _submit_catalog_collection('gongfa')
    session.expire_all()
    from backend.core.fanxiu.instrumentation.gongfa_atlas import load_gongfa_atlas_snapshot

    payload = load_gongfa_atlas_snapshot(session)
    if not payload.get("runtime_complete"):
        raise HTTPException(status_code=502, detail="个人功法动态插桩未生成完整快照")
    return payload


@inventory_router.put("/inventory/magic-treasure-hall", response_model=FanxiuMagicTreasureHallSnapshot)
def update_fanxiu_magic_treasure_hall(
    payload: FanxiuMagicTreasureHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_payload = payload.model_dump(mode="json")
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = sync_hall_note_refs(
        session,
        fanxiu_user,
        normalized_payload,
        note_kind=FANXIU_MAGIC_TREASURE_KIND,
        sync_note_fields=sync_wardrobe_note_fields,
    )

    if touched_existing_note:
        session.commit()
    row = upsert_inventory_hall_snapshot(
        session,
        "magic_treasure_hall",
        normalized_payload,
        source_kind="manual",
        entity_name="法宝殿",
    )
    return FanxiuMagicTreasureHallSnapshot.model_validate(row.payload)


@inventory_router.post(
    "/inventory/magic-treasure-hall/collect",
    response_model=FanxiuMagicTreasureHallSnapshot,
)
def collect_fanxiu_magic_treasure_hall(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Run one ordinary read-only Cell and return its persisted DB snapshot."""

    ensure_fanxiu_write_permission(current_user, session)
    _submit_catalog_collection('magic_treasure')
    session.expire_all()
    payload = load_inventory_hall_snapshot(session, "magic_treasure_hall")
    if not payload or not payload.get("runtime_complete"):
        raise HTTPException(status_code=502, detail="法宝动态插桩未生成完整数据库快照")
    return FanxiuMagicTreasureHallSnapshot.model_validate(payload)


@inventory_router.get("/inventory/spirit-artifact-hall", response_model=FanxiuSpiritArtifactHallSnapshot)
def get_fanxiu_spirit_artifact_hall(session: Session = Depends(get_session)):
    from backend.core.fanxiu.instrumentation.spirit_artifact_store import (
        load_spirit_artifact_runtime_snapshot,
    )

    runtime_payload = load_spirit_artifact_runtime_snapshot(session)
    try:
        payload = load_spirit_artifact_hall(
            artifact_snapshot=(runtime_payload or {}).get("artifacts"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if runtime_payload:
        payload = {
            **payload,
            **runtime_payload,
            "artifacts": runtime_payload["artifacts"],
        }
    from backend.core.fanxiu.instrumentation.spirit_artifact_storage_bag import (
        load_spirit_artifact_storage_bag_snapshot,
    )
    bag_payload = load_spirit_artifact_storage_bag_snapshot(session, hall_snapshot=payload)
    if bag_payload is not None:
        payload["storage_bag_items"] = bag_payload["storage_bag_items"]
        if 'market_currency_count' in bag_payload:
            payload['market_currency_count'] = bag_payload['market_currency_count']
    return FanxiuSpiritArtifactHallSnapshot.model_validate(payload)


@inventory_router.post("/inventory/spirit-artifact-storage-bag/sync", response_model=FanxiuSpiritArtifactHallSnapshot)
def sync_fanxiu_spirit_artifact_storage_bag(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    from backend.core.fanxiu.instrumentation.spirit_artifact_storage_bag import sync_spirit_artifact_storage_bag
    try:
        sync_spirit_artifact_storage_bag(session)
    except (FanxiuRuntimeMemoryError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return get_fanxiu_spirit_artifact_hall(session)


@inventory_router.put("/inventory/spirit-artifact-hall", response_model=FanxiuSpiritArtifactHallSnapshot)
def update_fanxiu_spirit_artifact_hall(
    payload: FanxiuSpiritArtifactHallSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        saved_payload = save_spirit_artifact_hall(payload.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修灵器仓库失败：{exc}") from exc
    return FanxiuSpiritArtifactHallSnapshot.model_validate(saved_payload)


@inventory_router.get("/activity-list", response_model=FanxiuActivityListSnapshot)
def get_fanxiu_activity_list():
    try:
        payload = load_activity_list()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FanxiuActivityListSnapshot(items=payload)


@inventory_router.put("/activity-list", response_model=FanxiuActivityListSnapshot)
def update_fanxiu_activity_list(
    payload: FanxiuActivityListSnapshot,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    normalized_items = payload.model_dump(mode="json")["items"]
    fanxiu_user = get_fanxiu_user(session)
    touched_existing_note = sync_item_note_refs(
        session,
        fanxiu_user,
        normalized_items,
        note_kind=FANXIU_ACTIVITY_KIND,
        sync_note_fields=sync_activity_note_fields,
    )

    if touched_existing_note:
        session.commit()

    try:
        saved_payload = save_activity_list(normalized_items)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存凡修活动列表失败：{exc}") from exc
    return FanxiuActivityListSnapshot(items=saved_payload)


# 复用原导出路由和 URL；服务令牌路由保持原有 scope 校验。
inventory_router.include_router(inventory_notes_router)
chars_router.include_router(character_notes_router)
inventory_router.include_router(activities_router)
status_router.include_router(questions_router)
status_router.include_router(players_router)
status_router.include_router(kernel_scheduler_router)
router.include_router(status_router)
router.include_router(inventory_router)
router.include_router(chars_router)
