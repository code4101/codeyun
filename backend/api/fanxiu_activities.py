"""活动快照、兑换配置与榜单的 HTTP 适配。

业务判定与持久化由 core.fanxiu.activity 提供；访问依赖由父路由统一组合。
"""
from fastapi import APIRouter
from backend.api.fanxiu_access import ensure_fanxiu_write_permission
from fastapi import Depends, HTTPException, Query
from backend.core.fanxiu.activity.exchange_event import (
    ExchangeActivityDetail,
    ExchangeActivityObservationPage,
    ExchangeActivitySnapshot,
    ExchangePriorityUpdateRequest,
    ExchangeRankingPage,
    ExchangeShopItemLockUpdateRequest,
    LatestExchangeActivitySnapshot,
    apply_exchange_shop_plan,
    is_exchange_activity_active,
    latest_exchange_activity_snapshot,
    list_exchange_activity_observations,
    list_exchange_activity_snapshot,
    list_exchange_rankings,
    update_exchange_priorities,
    update_exchange_shop_item_lock,
)
from backend.core.fanxiu.activity.schedule_page import FanxiuScheduleRankingSnapshot, load_fanxiu_schedule_ranking_snapshot
from backend.core.fanxiu.activity.lingchong_jingwu import (
    LingchongJingwuResourceSnapshot,
    collect_lingchong_jingwu_resource_snapshot,
    load_lingchong_jingwu_observed_tasks,
    load_lingchong_jingwu_resource_snapshot,
    store_lingchong_jingwu_resource_snapshot,
)
from backend.core.fanxiu.activity.lingzhuang_strengthening import LingzhuangStrengtheningSnapshot, collect_and_store_lingzhuang_strengthening_snapshot, load_lingzhuang_strengthening_snapshot
from backend.core.fanxiu.activity.lingzhuang_relationship import RelationshipDataset, list_lingzhuang_relationship_samples, record_lingzhuang_relationship_sample
from sqlmodel import Session
from backend.models import User
from backend.core.fanxiu.activity.yaochi_flower_resources import YaochiFlowerResourceSnapshot, collect_and_store_yaochi_flower_resource_snapshot, load_yaochi_flower_resource_snapshot
from backend.core.fanxiu.activity.yunmeng_trial import (
    YunmengTrialActivityDetail,
    YunmengTrialMeasurementCollectRequest,
    YunmengTrialMeasurementCollectResult,
    YunmengTrialMeasurementPage,
    YunmengTrialPriorityUpdateRequest,
    YunmengTrialRankingPage,
    YunmengTrialShopItemLockUpdateRequest,
    YunmengTrialSnapshotResponse,
    collect_and_store_yunmeng_trial_measurement,
    list_yunmeng_trial_measurements,
    list_yunmeng_trial_rankings,
    list_yunmeng_trial_snapshot,
    update_yunmeng_trial_priorities,
    update_yunmeng_trial_shop_item_lock,
)
from backend.core.fanxiu.activity.exchange_activity_registry import (
    collect_registered_exchange_activity,
    collect_registered_resource_ranking_resources,
    load_registered_resource_ranking_resources,
    load_registered_resource_ranking_tasks,
    materialize_registered_exchange_activity,
)
from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.core.fanxiu.activity.resource_ranking import load_yaochi_flower_task_milestones, load_yuanding_sansheng_task_milestones, resolve_yaochi_flower_activity_references

inventory_router = APIRouter()

@inventory_router.get(
    "/activity-list/yunmeng-trial",
    response_model=YunmengTrialSnapshotResponse,
)
def get_fanxiu_yunmeng_trial_snapshot(
    activity_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    return list_yunmeng_trial_snapshot(session, activity_id=activity_id)

@inventory_router.get(
    "/activity-list/latest-exchange-event",
    response_model=LatestExchangeActivitySnapshot,
)
def get_latest_fanxiu_exchange_activity_snapshot(
    activity_types: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
):
    return latest_exchange_activity_snapshot(
        session,
        activity_types=activity_types.split(","),
    )

@inventory_router.get(
    "/activity-list/exchange-events/{activity_type}",
    response_model=ExchangeActivitySnapshot,
)
def get_fanxiu_exchange_activity_snapshot(
    activity_type: str,
    activity_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """返回兑换活动快照；沿用现有契约，先物化已登记的活动投影。"""

    materialized_activity_id = materialize_registered_exchange_activity(
        session,
        activity_type=activity_type,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=activity_type,
        activity_id=activity_id or materialized_activity_id,
    )

@inventory_router.get(
    "/schedule/rankings",
    response_model=FanxiuScheduleRankingSnapshot,
)
def get_fanxiu_schedule_rankings(
    session: Session = Depends(get_session),
):
    return load_fanxiu_schedule_ranking_snapshot(session)

@inventory_router.get(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/observations",
    response_model=ExchangeActivityObservationPage,
)
def get_fanxiu_exchange_activity_observations(
    activity_type: str,
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        return list_exchange_activity_observations(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/lingzhuang-huadao/strengthening",
    response_model=LingzhuangStrengtheningSnapshot,
)
def get_fanxiu_lingzhuang_strengthening_snapshot(
    session: Session = Depends(get_session),
):
    return load_lingzhuang_strengthening_snapshot(session)

@inventory_router.post(
    "/activity-list/lingzhuang-huadao/{activity_id}/strengthening/collect",
    response_model=LingzhuangStrengtheningSnapshot,
)
def collect_fanxiu_lingzhuang_strengthening_snapshot(
    activity_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return collect_and_store_lingzhuang_strengthening_snapshot(
            session,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/lingzhuang-huadao/{activity_id}/relationship-samples",
    response_model=RelationshipDataset,
)
def get_fanxiu_lingzhuang_relationship_samples(
    activity_id: str,
    session: Session = Depends(get_session),
):
    return list_lingzhuang_relationship_samples(session, activity_id=activity_id)

@inventory_router.post(
    "/activity-list/lingzhuang-huadao/{activity_id}/relationship-samples/record",
    response_model=RelationshipDataset,
)
def record_fanxiu_lingzhuang_relationship_sample(
    activity_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return record_lingzhuang_relationship_sample(session, activity_id=activity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.put(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/priorities",
    response_model=ExchangeActivityDetail,
)
def update_fanxiu_exchange_activity_priorities(
    activity_type: str,
    activity_id: str,
    payload: ExchangePriorityUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return update_exchange_priorities(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
            ordered_goods_ids=payload.ordered_goods_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.post(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/plan",
    response_model=ExchangeActivityDetail,
)
def plan_fanxiu_exchange_activity_shop(
    activity_type: str,
    activity_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return apply_exchange_shop_plan(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.put(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/shop-items/{goods_id}/lock",
    response_model=ExchangeActivityDetail,
)
def update_fanxiu_exchange_activity_shop_item_lock(
    activity_type: str,
    activity_id: str,
    goods_id: int,
    payload: ExchangeShopItemLockUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return update_exchange_shop_item_lock(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
            goods_id=goods_id,
            locked=payload.locked,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/rankings",
    response_model=ExchangeRankingPage,
)
def get_fanxiu_exchange_activity_rankings(
    activity_type: str,
    activity_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ranking_scope: str = Query(default="personal"),
    session: Session = Depends(get_session),
):
    try:
        return list_exchange_rankings(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
            page=page,
            page_size=page_size,
            ranking_scope=ranking_scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/yaochi-flower-festival/{activity_id}/tasks",
)
def get_fanxiu_yaochi_flower_festival_tasks(
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        snapshot = list_exchange_activity_snapshot(
            session,
            activity_type="yaochi-flower-festival",
            activity_id=activity_id,
        )
        activity = snapshot.selected_activity
        if activity is None or activity.game_rank_activity_id is None:
            raise ValueError("瑶池花会活动缺少任务配置 ID")
        references = resolve_yaochi_flower_activity_references(
            rank_activity_id=activity.game_rank_activity_id,
            cross_count=activity.cross_count,
        )
        return {
            "references": references,
            "items": load_yaochi_flower_task_milestones(
                rank_activity_id=int(
                    references.get("task_activity_id")
                    or activity.game_rank_activity_id
                ),
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/yuanding-sansheng/{activity_id}/tasks",
)
def get_fanxiu_yuanding_sansheng_tasks(
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        snapshot = list_exchange_activity_snapshot(
            session,
            activity_type="yuanding-sansheng",
            activity_id=activity_id,
        )
        if snapshot.selected_activity is None:
            raise ValueError("缘定三生活动不存在")
        return {"items": load_yuanding_sansheng_task_milestones()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/lingchong-jingwu/{activity_id}/tasks",
)
def get_fanxiu_lingchong_jingwu_tasks(
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        snapshot = list_exchange_activity_snapshot(
            session,
            activity_type="lingchong-jingwu",
            activity_id=activity_id,
        )
        if snapshot.selected_activity is None:
            raise ValueError("8跨灵宠竞武活动不存在")
        return load_lingchong_jingwu_observed_tasks(
            session,
            start_date=snapshot.selected_activity.start_date,
            end_date=snapshot.selected_activity.end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/tasks",
)
def get_fanxiu_registered_resource_ranking_tasks(
    activity_type: str,
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        return load_registered_resource_ranking_tasks(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/resources",
)
def get_fanxiu_registered_resource_ranking_resources(
    activity_type: str,
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        return load_registered_resource_ranking_resources(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.post(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/resources/collect",
)
def collect_fanxiu_registered_resource_ranking_resources(
    activity_type: str,
    activity_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return collect_registered_resource_ranking_resources(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/lingchong-jingwu/{activity_id}/resources",
    response_model=LingchongJingwuResourceSnapshot,
)
def get_fanxiu_lingchong_jingwu_resources(
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        return load_lingchong_jingwu_resource_snapshot(
            session, activity_id=activity_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.post(
    "/activity-list/lingchong-jingwu/{activity_id}/resources/collect",
    response_model=LingchongJingwuResourceSnapshot,
)
def collect_fanxiu_lingchong_jingwu_resources(
    activity_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        snapshot = list_exchange_activity_snapshot(
            session,
            activity_type="lingchong-jingwu",
            activity_id=activity_id,
        )
        if snapshot.selected_activity is None:
            raise ValueError("8跨灵宠竞武活动不存在")
        if not is_exchange_activity_active(snapshot.selected_activity):
            raise ValueError("8跨灵宠竞武活动不在有效日期内")
        collected = collect_lingchong_jingwu_resource_snapshot(
            activity_id=activity_id
        )
        return store_lingchong_jingwu_resource_snapshot(session, collected)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/yaochi-flower-festival/resources",
    response_model=YaochiFlowerResourceSnapshot,
)
def get_fanxiu_yaochi_flower_resources(
    session: Session = Depends(get_session),
):
    try:
        return load_yaochi_flower_resource_snapshot(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.post(
    "/activity-list/yaochi-flower-festival/{activity_id}/resources/collect",
    response_model=YaochiFlowerResourceSnapshot,
)
def collect_fanxiu_yaochi_flower_resources(
    activity_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return collect_and_store_yaochi_flower_resource_snapshot(
            session,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.post(
    "/activity-list/exchange-events/{activity_type}/{activity_id}/collect",
    response_model=ExchangeActivityDetail,
)
def collect_fanxiu_exchange_activity(
    activity_type: str,
    activity_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return collect_registered_exchange_activity(
            session,
            activity_type=activity_type,
            activity_id=activity_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.put(
    "/activity-list/yunmeng-trial/{activity_id}/priorities",
    response_model=YunmengTrialActivityDetail,
)
def update_fanxiu_yunmeng_trial_priorities(
    activity_id: str,
    payload: YunmengTrialPriorityUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return update_yunmeng_trial_priorities(
            session,
            activity_id=activity_id,
            ordered_goods_ids=payload.ordered_goods_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.put(
    "/activity-list/yunmeng-trial/{activity_id}/shop-items/{goods_id}/lock",
    response_model=YunmengTrialActivityDetail,
)
def update_fanxiu_yunmeng_trial_shop_item_lock(
    activity_id: str,
    goods_id: int,
    payload: YunmengTrialShopItemLockUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return update_yunmeng_trial_shop_item_lock(
            session,
            activity_id=activity_id,
            goods_id=goods_id,
            locked=payload.locked,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/yunmeng-trial/{activity_id}/rankings",
    response_model=YunmengTrialRankingPage,
)
def get_fanxiu_yunmeng_trial_rankings(
    activity_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ranking_scope: str = Query(default="personal"),
    session: Session = Depends(get_session),
):
    try:
        return list_yunmeng_trial_rankings(
            session,
            activity_id=activity_id,
            page=page,
            page_size=page_size,
            ranking_scope=ranking_scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@inventory_router.get(
    "/activity-list/yunmeng-trial/{activity_id}/measurements",
    response_model=YunmengTrialMeasurementPage,
)
def get_fanxiu_yunmeng_trial_measurements(
    activity_id: str,
    session: Session = Depends(get_session),
):
    try:
        return list_yunmeng_trial_measurements(session, activity_id=activity_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@inventory_router.post(
    "/activity-list/yunmeng-trial/{activity_id}/measurements/collect",
    response_model=YunmengTrialMeasurementCollectResult,
)
def collect_fanxiu_yunmeng_trial_measurement(
    activity_id: str,
    payload: YunmengTrialMeasurementCollectRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    ensure_fanxiu_write_permission(current_user, session)
    try:
        return collect_and_store_yunmeng_trial_measurement(
            session,
            activity_id=activity_id,
            challenge_count_delta=payload.challenge_count_delta,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"活动运行态数据更新失败：{exc}",
        ) from exc
