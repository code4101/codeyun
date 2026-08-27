from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from sqlmodel import Session

from backend.models import FanxiuExchangeActivity

from backend.core.fanxiu.activity.exchange_activity_spec import (
    ExchangeActivityAdapter,
    ExchangeActivityMaterializer,
    ExchangeOccurrenceRankAdapter,
    ExchangeOccurrenceShopAdapter,
    ExchangeActivitySpec,
    PageContract,
    RankActivityIdBinding,
    RankScopeRole,
    RankScopeSpec,
    RankSubject,
    ResourceRankingResourceAdapter,
    ResourceRankingTaskAdapter,
    ShopSpec,
)


PERSONAL_RANK_VO = "ActivityRankPersonalVO"
PLANE_RANK_VO = "ActivityRankCrossServerVO"
TEAM_RANK_VO = "ActivityRankTeamVO"
PUBLIC_EXCHANGE_ACTIVITY_TYPES = (
    "yunmeng-trial",
    "xianyuan-duokui",
    "xutian-palace",
    "magic-invasion",
    "beast-abyss",
    "lingzhuang-huadao",
    "yaochi-flower-festival",
    "yuanding-sansheng",
    "lingchong-jingwu",
    "lianti-faxiang",
    "dandao-wending",
    "tiandi-yiju",
)


class YunmengTrialExchangeActivityAdapter:
    def collect_activity(
        self,
        session: Session,
        *,
        activity_id: str,
    ) -> Any:
        from backend.core.fanxiu.activity.yunmeng_exchange import (
            collect_and_store_yunmeng_exchange_activity,
        )

        return collect_and_store_yunmeng_exchange_activity(
            session,
            activity_id=activity_id,
        )


class XianyuanDuokuiExchangeActivityAdapter:
    def materialize_activity(self, session: Session) -> str:
        from backend.core.fanxiu.activity.xianyuan_duokui import (
            ensure_xianyuan_duokui_activity,
        )

        return ensure_xianyuan_duokui_activity(session)

    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.xianyuan_duokui import (
            collect_and_store_xianyuan_duokui_activity,
        )

        return collect_and_store_xianyuan_duokui_activity(
            session, activity_id=activity_id
        )


class XutianPalaceExchangeActivityAdapter:
    def materialize_activity(self, session: Session) -> str:
        from backend.core.fanxiu.activity.xutian_palace_instrumentation import (
            ensure_xutian_palace_activity,
        )

        return ensure_xutian_palace_activity(session)

    def collect_activity(
        self,
        session: Session,
        *,
        activity_id: str,
    ) -> Any:
        from backend.core.fanxiu.activity.xutian_palace_instrumentation import (
            collect_and_store_xutian_palace_activity,
        )

        return collect_and_store_xutian_palace_activity(
            session,
            activity_id=activity_id,
        )


class MagicInvasionExchangeActivityAdapter:
    def resolve_occurrence_shop(self, *, cross_count: int) -> ShopSpec:
        from backend.core.fanxiu.activity.magic_invasion import (
            resolve_magic_invasion_shop_identity,
        )

        base_id, currency_type, _expected_cross_count = (
            resolve_magic_invasion_shop_identity(cross_count=cross_count)
        )
        return ShopSpec(base_id=base_id, currency_type=currency_type)

    def collect_activity(
        self,
        session: Session,
        *,
        activity_id: str,
    ) -> Any:
        from backend.core.fanxiu.activity.magic_invasion import (
            collect_and_store_magic_invasion_activity,
        )

        return collect_and_store_magic_invasion_activity(
            session,
            activity_id=activity_id,
        )


class BeastAbyssExchangeActivityAdapter:
    def collect_activity(
        self,
        session: Session,
        *,
        activity_id: str,
    ) -> Any:
        from backend.core.fanxiu.activity.beast_abyss import (
            collect_and_store_beast_abyss_activity,
        )

        return collect_and_store_beast_abyss_activity(
            session,
            activity_id=activity_id,
        )


class TiandiYijuExchangeActivityAdapter:
    _RANK_IDS = {
        8090001: {"personal": 90101, "alliance": 90102},
        8090004: {"personal": 90808, "alliance": 90813},
    }

    def resolve_occurrence_rank_activity_ids(
        self,
        *,
        activity_id: int,
    ) -> Mapping[str, int]:
        try:
            return self._RANK_IDS[int(activity_id)]
        except KeyError as exc:
            raise ValueError(f"天地弈局活动 {int(activity_id)} 不是可物化棋局") from exc

    def resolve_occurrence_shop(self, *, cross_count: int) -> ShopSpec:
        if int(cross_count) == 1:
            return ShopSpec(base_id=90000, currency_type=11)
        if int(cross_count) == 8:
            return ShopSpec(base_id=90002, currency_type=13)
        raise ValueError(f"天地弈局不支持 {int(cross_count)} 跨商店")

    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.tiandi_yiju import (
            collect_and_store_tiandi_yiju_activity,
        )

        return collect_and_store_tiandi_yiju_activity(session, activity_id=activity_id)


class LingzhuangHuadaoActivityAdapter:
    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.resource_ranking import (
            collect_and_store_lingzhuang_huadao_activity,
        )

        return collect_and_store_lingzhuang_huadao_activity(
            session, activity_id=activity_id
        )


class YaochiFlowerFestivalActivityAdapter:
    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.resource_ranking import (
            collect_and_store_yaochi_flower_festival_activity,
        )

        return collect_and_store_yaochi_flower_festival_activity(
            session, activity_id=activity_id
        )


class YuandingSanshengActivityAdapter:
    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.resource_ranking import (
            collect_and_store_yuanding_sansheng_activity,
        )

        return collect_and_store_yuanding_sansheng_activity(
            session, activity_id=activity_id
        )


class LingchongJingwuActivityAdapter:
    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lingchong_jingwu import (
            collect_and_store_lingchong_jingwu_activity,
        )

        return collect_and_store_lingchong_jingwu_activity(
            session, activity_id=activity_id
        )

    def load_tasks(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lingchong_jingwu import (
            load_lingchong_jingwu_observed_tasks,
        )

        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "lingchong-jingwu":
            raise ValueError("8跨灵宠竞武活动不存在")
        return load_lingchong_jingwu_observed_tasks(
            session,
            start_date=activity.start_date,
            end_date=activity.end_date,
        )

    def load_resources(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lingchong_jingwu import (
            load_lingchong_jingwu_resource_snapshot,
        )

        return load_lingchong_jingwu_resource_snapshot(session, activity_id=activity_id)

    def collect_resources(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lingchong_jingwu import (
            collect_lingchong_jingwu_resource_snapshot,
            store_lingchong_jingwu_resource_snapshot,
        )

        return store_lingchong_jingwu_resource_snapshot(
            session,
            collect_lingchong_jingwu_resource_snapshot(activity_id=activity_id),
        )


class LiantiFaxiangActivityAdapter:
    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lianti_faxiang import (
            collect_and_store_lianti_faxiang_activity,
        )

        return collect_and_store_lianti_faxiang_activity(
            session, activity_id=activity_id
        )

    def load_tasks(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lianti_faxiang import (
            load_lianti_faxiang_observed_tasks,
        )
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "lianti-faxiang":
            raise ValueError("炼体法相活动不存在")
        return load_lianti_faxiang_observed_tasks(
            session,
            start_date=activity.start_date,
            end_date=activity.end_date,
        )

    def load_resources(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lianti_faxiang import (
            load_lianti_faxiang_resource_snapshot,
        )

        return load_lianti_faxiang_resource_snapshot(session, activity_id=activity_id)

    def collect_resources(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.lianti_faxiang import (
            collect_lianti_faxiang_resource_snapshot,
            store_lianti_faxiang_resource_snapshot,
        )

        return store_lianti_faxiang_resource_snapshot(
            session,
            collect_lianti_faxiang_resource_snapshot(activity_id=activity_id),
        )


class DandaoWendingActivityAdapter:
    def materialize_activity(self, session: Session) -> str:
        from backend.core.fanxiu.activity.dandao_wending import (
            ensure_dandao_wending_activity,
        )

        return ensure_dandao_wending_activity(session)

    def collect_activity(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.dandao_wending import (
            collect_and_store_dandao_wending_activity,
        )

        return collect_and_store_dandao_wending_activity(
            session, activity_id=activity_id
        )

    def load_tasks(self, session: Session, *, activity_id: str) -> Any:
        from backend.core.fanxiu.activity.dandao_wending import (
            load_dandao_wending_tasks,
        )

        return load_dandao_wending_tasks(session, activity_id=activity_id)


def _rank_scope(
    scope: str,
    *,
    label: str,
    role: RankScopeRole,
    subject: RankSubject,
    reward_tiers_enabled: bool,
    required: bool,
    vo_type: str,
    binding: RankActivityIdBinding,
) -> RankScopeSpec:
    return RankScopeSpec(
        scope=scope,
        required=required,
        accepted_vo_types=(vo_type,),
        activity_id=binding,
        label=label,
        role=role,
        subject=subject,
        row_mode="full_observed",
        reward_tiers_enabled=reward_tiers_enabled,
    )


XUTIAN_PALACE_SPEC = ExchangeActivitySpec(
    activity_type="xutian-palace",
    label="虚天殿",
    worldline_vo_types=("HeavenActivityVO",),
    currency_type=12,
    currency_name="纳元晶",
    rank_scopes=(
        _rank_scope(
            "personal",
            label="个人榜",
            role="primary",
            subject="role",
            reward_tiers_enabled=True,
            required=True,
            vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(
                source="cross_count_formula",
                base_id=80000,
                suffix=91,
            ),
        ),
        _rank_scope(
            "plane",
            label="位面榜",
            role="comparative",
            subject="server",
            reward_tiers_enabled=True,
            required=False,
            vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(
                source="cross_count_formula",
                base_id=80000,
                suffix=71,
            ),
        ),
    ),
    shop=ShopSpec(
        base_id=80000,
        currency_type=12,
        required_on_explicit_collect=False,
    ),
    page=PageContract(
        page_kind="exchange-ranking",
        ranking_scopes=("personal", "plane"),
        has_shop=True,
    ),
    adapter=XutianPalaceExchangeActivityAdapter(),
)


YUNMENG_TRIAL_SPEC = ExchangeActivitySpec(
    activity_type="yunmeng-trial",
    label="云梦试剑",
    worldline_vo_types=("YunmengActivityVO",),
    currency_type=19,
    currency_name="论剑玉",
    rank_scopes=(
        _rank_scope(
            "personal",
            label="个人榜",
            role="primary",
            subject="role",
            reward_tiers_enabled=True,
            required=True,
            vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(
                source="activity_follow",
                follow_index=0,
            ),
        ),
        _rank_scope(
            "plane",
            label="位面榜",
            role="comparative",
            subject="server",
            reward_tiers_enabled=True,
            required=False,
            vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(
                source="activity_follow",
                follow_index=1,
            ),
        ),
    ),
    shop=ShopSpec(base_id=210001, currency_type=19),
    page=PageContract(
        page_kind="exchange-ranking",
        ranking_scopes=("personal", "plane"),
        has_shop=True,
    ),
    adapter=YunmengTrialExchangeActivityAdapter(),
)


XIANYUAN_DUOKUI_SPEC = ExchangeActivitySpec(
    activity_type="xianyuan-duokui",
    label="仙缘夺魁",
    worldline_vo_types=("YunmengActivityVO",),
    currency_type=23002,
    currency_name="夺魁灵玉",
    rank_scopes=(
        _rank_scope(
            "personal",
            label="个人榜",
            role="primary",
            subject="role",
            reward_tiers_enabled=True,
            required=True,
            vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(source="activity_follow", follow_index=0),
        ),
        _rank_scope(
            "plane",
            label="位面榜",
            role="comparative",
            subject="server",
            reward_tiers_enabled=True,
            required=False,
            vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(source="activity_follow", follow_index=1),
        ),
    ),
    shop=ShopSpec(base_id=360001, currency_type=23002),
    page=PageContract(
        page_kind="exchange-ranking",
        ranking_scopes=("personal", "plane"),
        has_shop=True,
    ),
    adapter=XianyuanDuokuiExchangeActivityAdapter(),
)


MAGIC_INVASION_SPEC = ExchangeActivitySpec(
    activity_type="magic-invasion",
    label="魔道入侵",
    worldline_vo_types=("MagicInvadeActivityVO",),
    currency_type=17,
    currency_name="魔晶",
    rank_scopes=(
        _rank_scope(
            "personal",
            label="个人榜",
            role="primary",
            subject="role",
            reward_tiers_enabled=True,
            required=True,
            vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(
                source="activity_follow",
                follow_index=0,
            ),
        ),
        _rank_scope(
            "plane",
            label="位面榜",
            role="comparative",
            subject="server",
            reward_tiers_enabled=True,
            required=False,
            vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(
                source="activity_follow",
                follow_index=1,
            ),
        ),
    ),
    shop=ShopSpec(base_id=70001, currency_type=17),
    page=PageContract(
        page_kind="exchange-ranking",
        ranking_scopes=("personal", "plane"),
        has_shop=True,
    ),
    adapter=MagicInvasionExchangeActivityAdapter(),
)


BEAST_ABYSS_SPEC = ExchangeActivitySpec(
    activity_type="beast-abyss",
    label="兽渊探秘",
    worldline_vo_types=("BeastExplodeActivityVO",),
    currency_type=14,
    currency_name="兽元",
    rank_scopes=(
        _rank_scope(
            "personal",
            label="个人榜",
            role="primary",
            subject="role",
            reward_tiers_enabled=True,
            required=True,
            vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(
                source="activity_follow",
                follow_index=0,
            ),
        ),
        _rank_scope(
            "team",
            label="团队榜",
            role="comparative",
            subject="team",
            reward_tiers_enabled=True,
            required=False,
            vo_type=TEAM_RANK_VO,
            binding=RankActivityIdBinding(
                source="activity_follow",
                follow_index=1,
            ),
        ),
    ),
    shop=ShopSpec(base_id=150000, currency_type=14),
    page=PageContract(
        page_kind="exchange-ranking",
        ranking_scopes=("personal", "team"),
        has_shop=True,
    ),
    adapter=BeastAbyssExchangeActivityAdapter(),
)


TIANDI_YIJU_SPEC = ExchangeActivitySpec(
    activity_type="tiandi-yiju",
    label="天地弈局",
    worldline_vo_types=("AlliancePlayChessActivityVO",),
    currency_type=11,
    currency_name="棋符",
    rank_scopes=(
        _rank_scope(
            "personal",
            label="个人榜",
            role="primary",
            subject="role",
            reward_tiers_enabled=True,
            required=True,
            vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=90101),
        ),
        _rank_scope(
            "alliance",
            label="宗门/位面榜",
            role="comparative",
            subject="team",
            reward_tiers_enabled=True,
            required=False,
            vo_type=TEAM_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=90102),
        ),
    ),
    shop=ShopSpec(base_id=90000, currency_type=11),
    page=PageContract(
        page_kind="exchange-ranking",
        ranking_scopes=("personal", "alliance"),
        has_shop=True,
    ),
    adapter=TiandiYijuExchangeActivityAdapter(),
)


def _resource_page(scopes: tuple[str, ...]) -> PageContract:
    return PageContract(
        page_kind="resource-ranking",
        ranking_scopes=scopes,
        has_shop=False,
        allow_collect=True,
        allow_priority=False,
        allow_lock=False,
    )


LINGZHUANG_HUADAO_SPEC = ExchangeActivitySpec(
    activity_type="lingzhuang-huadao",
    label="灵装化道",
    worldline_vo_types=("CrossRankActivityVO",),
    currency_type=0,
    currency_name="玄铁",
    rank_scopes=(
        _rank_scope(
            "personal", label="个人榜", role="primary", subject="role",
            reward_tiers_enabled=True, required=True, vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=44307),
        ),
        _rank_scope(
            "plane", label="位面榜", role="comparative", subject="server",
            reward_tiers_enabled=True, required=False, vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=44308),
        ),
    ),
    shop=None,
    page=_resource_page(("personal", "plane")),
    adapter=LingzhuangHuadaoActivityAdapter(),
)


YAOCHI_FLOWER_FESTIVAL_SPEC = ExchangeActivitySpec(
    activity_type="yaochi-flower-festival",
    label="瑶池花会",
    worldline_vo_types=("CrossRankActivityVO",),
    currency_type=0,
    currency_name="仙花友好度",
    rank_scopes=(
        _rank_scope(
            "personal", label="个人榜", role="primary", subject="role",
            reward_tiers_enabled=True, required=True, vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(source="activity_follow", follow_index=0),
        ),
        _rank_scope(
            "plane", label="位面榜", role="comparative", subject="server",
            reward_tiers_enabled=True, required=False, vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(source="activity_follow", follow_index=1),
        ),
    ),
    shop=None,
    page=_resource_page(("personal", "plane")),
    adapter=YaochiFlowerFestivalActivityAdapter(),
)


YUANDING_SANSHENG_SPEC = ExchangeActivitySpec(
    activity_type="yuanding-sansheng",
    label="缘定三生",
    worldline_vo_types=("EmptyActivityVO",),
    currency_type=0,
    currency_name="联姻评分",
    rank_scopes=(
        _rank_scope(
            "personal", label="个人榜", role="primary", subject="role",
            reward_tiers_enabled=False, required=True, vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=45105),
        ),
        _rank_scope(
            "plane", label="分组榜", role="comparative", subject="server",
            reward_tiers_enabled=False, required=False, vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=45107),
        ),
    ),
    shop=None,
    page=_resource_page(("personal", "plane")),
    adapter=YuandingSanshengActivityAdapter(),
)


LINGCHONG_JINGWU_SPEC = ExchangeActivitySpec(
    activity_type="lingchong-jingwu",
    label="灵宠竞武",
    worldline_vo_types=("CrossRankActivityVO",),
    currency_type=0,
    currency_name="灵兽资质积分",
    rank_scopes=(
        _rank_scope(
            "personal", label="个人榜", role="primary", subject="role",
            reward_tiers_enabled=True, required=True, vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=42905),
        ),
        _rank_scope(
            "plane", label="位面榜", role="comparative", subject="server",
            reward_tiers_enabled=True, required=True, vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=42906),
        ),
    ),
    shop=None,
    page=_resource_page(("personal", "plane")),
    adapter=LingchongJingwuActivityAdapter(),
)


LIANTI_FAXIANG_SPEC = ExchangeActivitySpec(
    activity_type="lianti-faxiang",
    label="炼体法相",
    worldline_vo_types=("RankActivityVO",),
    currency_type=0,
    currency_name="炼体积分",
    rank_scopes=(
        _rank_scope(
            "personal", label="个人榜", role="primary", subject="role",
            reward_tiers_enabled=True, required=True, vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(source="fixed", fixed_id=1043011),
        ),
    ),
    shop=None,
    page=_resource_page(("personal",)),
    adapter=LiantiFaxiangActivityAdapter(),
)


DANDAO_WENDING_SPEC = ExchangeActivitySpec(
    activity_type="dandao-wending",
    label="丹道问鼎",
    worldline_vo_types=("RankActivityVO",),
    currency_type=0,
    currency_name="炼丹熟练度",
    rank_scopes=(
        _rank_scope(
            "personal", label="个人榜", role="primary", subject="role",
            reward_tiers_enabled=True, required=True, vo_type=PERSONAL_RANK_VO,
            binding=RankActivityIdBinding(
                source="activity_self_or_follow",
                follow_index=0,
            ),
        ),
        _rank_scope(
            "plane", label="位面榜", role="comparative", subject="server",
            reward_tiers_enabled=True, required=False, vo_type=PLANE_RANK_VO,
            binding=RankActivityIdBinding(source="activity_follow", follow_index=1),
        ),
    ),
    shop=None,
    page=_resource_page(("personal", "plane")),
    adapter=DandaoWendingActivityAdapter(),
)


def build_exchange_activity_registry(
    specs: Iterable[ExchangeActivitySpec],
) -> Mapping[str, ExchangeActivitySpec]:
    registry: dict[str, ExchangeActivitySpec] = {}
    for spec in specs:
        if spec.activity_type in registry:
            raise ValueError(f"重复的 ExchangeActivitySpec：{spec.activity_type}")
        registry[spec.activity_type] = spec
    return MappingProxyType(registry)


EXCHANGE_ACTIVITY_SPECS = build_exchange_activity_registry(
    (
        YUNMENG_TRIAL_SPEC,
        XIANYUAN_DUOKUI_SPEC,
        XUTIAN_PALACE_SPEC,
        MAGIC_INVASION_SPEC,
        BEAST_ABYSS_SPEC,
        TIANDI_YIJU_SPEC,
        LINGZHUANG_HUADAO_SPEC,
        YAOCHI_FLOWER_FESTIVAL_SPEC,
        YUANDING_SANSHENG_SPEC,
        LINGCHONG_JINGWU_SPEC,
        LIANTI_FAXIANG_SPEC,
        DANDAO_WENDING_SPEC,
    )
)

_missing_public_types = set(PUBLIC_EXCHANGE_ACTIVITY_TYPES) - set(
    EXCHANGE_ACTIVITY_SPECS
)
if _missing_public_types:
    raise RuntimeError(
        f"公开玩法榜尚未注册 ExchangeActivitySpec：{sorted(_missing_public_types)}"
    )


def get_exchange_activity_spec(activity_type: str) -> ExchangeActivitySpec:
    try:
        return EXCHANGE_ACTIVITY_SPECS[str(activity_type)]
    except KeyError as exc:
        raise ValueError(f"玩法榜 {activity_type} 尚未注册") from exc


def collect_registered_exchange_activity(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
) -> Any:
    return get_exchange_activity_spec(activity_type).adapter.collect_activity(
        session,
        activity_id=activity_id,
    )


def materialize_registered_exchange_activity(
    session: Session,
    *,
    activity_type: str,
) -> str | None:
    spec = get_exchange_activity_spec(activity_type)
    if not spec.page.materialize_on_get:
        return None
    adapter = spec.adapter
    if not isinstance(adapter, ExchangeActivityMaterializer):
        return None
    return adapter.materialize_activity(session)


def resolve_registered_occurrence_shop(
    *,
    activity_type: str,
    cross_count: int,
) -> ShopSpec | None:
    """Resolve the effective shop for one concrete activity occurrence."""

    spec = get_exchange_activity_spec(activity_type)
    adapter = spec.adapter
    if isinstance(adapter, ExchangeOccurrenceShopAdapter):
        return adapter.resolve_occurrence_shop(cross_count=int(cross_count))
    return spec.shop


def resolve_registered_occurrence_rank_activity_ids(
    *,
    activity_type: str,
    activity_id: int,
) -> Mapping[str, int] | None:
    adapter = get_exchange_activity_spec(activity_type).adapter
    if isinstance(adapter, ExchangeOccurrenceRankAdapter):
        return adapter.resolve_occurrence_rank_activity_ids(activity_id=int(activity_id))
    return None


def load_registered_resource_ranking_tasks(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
) -> Any:
    adapter = get_exchange_activity_spec(activity_type).adapter
    if not isinstance(adapter, ResourceRankingTaskAdapter):
        raise ValueError(f"玩法榜 {activity_type} 不提供任务里程碑")
    return adapter.load_tasks(session, activity_id=activity_id)


def load_registered_resource_ranking_resources(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
) -> Any:
    adapter = get_exchange_activity_spec(activity_type).adapter
    if not isinstance(adapter, ResourceRankingResourceAdapter):
        raise ValueError(f"玩法榜 {activity_type} 不提供资源库存")
    return adapter.load_resources(session, activity_id=activity_id)


def collect_registered_resource_ranking_resources(
    session: Session,
    *,
    activity_type: str,
    activity_id: str,
) -> Any:
    adapter = get_exchange_activity_spec(activity_type).adapter
    if not isinstance(adapter, ResourceRankingResourceAdapter):
        raise ValueError(f"玩法榜 {activity_type} 不提供资源库存")
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != activity_type:
        raise ValueError("资源榜活动不存在")
    from backend.core.fanxiu.activity.exchange_event import is_exchange_activity_active

    if not is_exchange_activity_active(activity):
        raise ValueError("资源榜活动不在有效日期内")
    return adapter.collect_resources(session, activity_id=activity_id)


def registered_exchange_activity_adapters() -> Mapping[str, ExchangeActivityAdapter]:
    return MappingProxyType(
        {
            activity_type: spec.adapter
            for activity_type, spec in EXCHANGE_ACTIVITY_SPECS.items()
        }
    )
