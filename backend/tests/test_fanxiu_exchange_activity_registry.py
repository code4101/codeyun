from dataclasses import replace

import pytest

from backend.core.fanxiu.activity.exchange_activity_registry import (
    EXCHANGE_ACTIVITY_SPECS,
    BEAST_ABYSS_SPEC,
    MAGIC_INVASION_SPEC,
    PLANE_RANK_VO,
    PERSONAL_RANK_VO,
    TEAM_RANK_VO,
    TIANDI_YIJU_SPEC,
    PUBLIC_EXCHANGE_ACTIVITY_TYPES,
    XIANYUAN_DUOKUI_SPEC,
    YUNMENG_TRIAL_SPEC,
    XUTIAN_PALACE_SPEC,
    build_exchange_activity_registry,
    collect_registered_exchange_activity,
    get_exchange_activity_spec,
    resolve_registered_occurrence_rank_activity_ids,
    resolve_registered_occurrence_shop,
)
from backend.core.fanxiu.activity.exchange_activity_spec import (
    ExchangeActivityAdapter,
    PageContract,
    RankActivityIdBinding,
    RankScopeSpec,
    ShopSpec,
)


def test_public_exchange_activity_types_are_uniquely_registered() -> None:
    assert set(EXCHANGE_ACTIVITY_SPECS) == set(PUBLIC_EXCHANGE_ACTIVITY_TYPES)
    assert get_exchange_activity_spec("xutian-palace") is XUTIAN_PALACE_SPEC
    assert get_exchange_activity_spec("magic-invasion") is MAGIC_INVASION_SPEC
    assert get_exchange_activity_spec("beast-abyss") is BEAST_ABYSS_SPEC
    assert get_exchange_activity_spec("yunmeng-trial") is YUNMENG_TRIAL_SPEC
    assert get_exchange_activity_spec("xianyuan-duokui") is XIANYUAN_DUOKUI_SPEC
    assert get_exchange_activity_spec("tiandi-yiju") is TIANDI_YIJU_SPEC
    with pytest.raises(ValueError, match="重复"):
        build_exchange_activity_registry(
            (XUTIAN_PALACE_SPEC, replace(XUTIAN_PALACE_SPEC, label="重复"))
        )


@pytest.mark.parametrize(
    (
        "activity_type",
        "currency_type",
        "currency_name",
        "shop_base_id",
        "shop_required",
        "worldline_vo",
        "comparative_scope",
        "comparative_label",
        "comparative_subject",
        "comparative_vo",
    ),
    [
        ("yunmeng-trial", 19, "论剑玉", 210001, True, "YunmengActivityVO", "plane", "位面榜", "server", PLANE_RANK_VO),
        ("xianyuan-duokui", 23002, "夺魁灵玉", 360001, True, "YunmengActivityVO", "plane", "位面榜", "server", PLANE_RANK_VO),
        ("xutian-palace", 12, "纳元晶", 80000, False, "HeavenActivityVO", "plane", "位面榜", "server", PLANE_RANK_VO),
        ("magic-invasion", 17, "魔晶", 70001, True, "MagicInvadeActivityVO", "plane", "位面榜", "server", PLANE_RANK_VO),
        ("beast-abyss", 14, "兽元", 150000, True, "BeastExplodeActivityVO", "team", "团队榜", "team", TEAM_RANK_VO),
        ("tiandi-yiju", 11, "棋符", 90000, True, "AlliancePlayChessActivityVO", "alliance", "宗门/位面榜", "team", TEAM_RANK_VO),
    ],
)
def test_registered_exchange_activity_contracts_are_conformant(
    activity_type: str,
    currency_type: int,
    currency_name: str,
    shop_base_id: int,
    shop_required: bool,
    worldline_vo: str,
    comparative_scope: str,
    comparative_label: str,
    comparative_subject: str,
    comparative_vo: str,
) -> None:
    spec = get_exchange_activity_spec(activity_type)

    assert isinstance(spec.adapter, ExchangeActivityAdapter)
    assert spec.currency_type == currency_type
    assert spec.currency_name == currency_name
    assert spec.shop == ShopSpec(
        base_id=shop_base_id,
        currency_type=currency_type,
        required_on_explicit_collect=shop_required,
    )
    assert spec.worldline_vo_types == (worldline_vo,)
    assert spec.page == PageContract(
        page_kind="exchange-ranking",
        ranking_scopes=("personal", comparative_scope),
        has_shop=True,
    )
    scope_contracts = [
        (
            scope.scope,
            scope.required,
            scope.accepted_vo_types,
            scope.effective_label,
            scope.effective_role,
            scope.subject,
            scope.row_mode,
            scope.reward_tiers_enabled,
        )
        for scope in spec.rank_scopes
    ]
    assert scope_contracts == [
        ("personal", True, (PERSONAL_RANK_VO,), "个人榜", "primary", "role", "full_observed", True),
        (comparative_scope, False, (comparative_vo,), comparative_label, "comparative", comparative_subject, "full_observed", True),
    ]


def test_rank_scope_keeps_old_constructor_and_supports_team_subject() -> None:
    legacy = RankScopeSpec(
        "personal",
        True,
        (PERSONAL_RANK_VO,),
        RankActivityIdBinding(source="activity_follow", follow_index=0),
    )
    team = RankScopeSpec(
        scope="team",
        required=False,
        accepted_vo_types=("ActivityRankTeamVO",),
        activity_id=RankActivityIdBinding(source="activity_follow", follow_index=1),
        label="队伍榜",
        role="comparative",
        subject="team",
        row_mode="full_observed",
    )

    assert (legacy.effective_label, legacy.effective_role, legacy.row_mode) == (
        "personal", "primary", "key_points",
    )
    beast_like = replace(
        MAGIC_INVASION_SPEC,
        rank_scopes=(legacy, team),
        page=replace(MAGIC_INVASION_SPEC.page, ranking_scopes=("personal", "team")),
    )
    assert beast_like.rank_scopes[1].subject == "team"


def test_rank_activity_id_bindings_resolve_authoritative_ids() -> None:
    xutian_scopes = {scope.scope: scope for scope in XUTIAN_PALACE_SPEC.rank_scopes}
    magic_scopes = {scope.scope: scope for scope in MAGIC_INVASION_SPEC.rank_scopes}
    beast_scopes = {scope.scope: scope for scope in BEAST_ABYSS_SPEC.rank_scopes}
    yunmeng_scopes = {scope.scope: scope for scope in YUNMENG_TRIAL_SPEC.rank_scopes}

    assert xutian_scopes["personal"].activity_id.resolve(cross_count=8) == 80891
    assert xutian_scopes["plane"].activity_id.resolve(cross_count=8) == 80871
    assert magic_scopes["personal"].activity_id.resolve(
        activity_follow=(70841, 70842)
    ) == 70841
    assert magic_scopes["plane"].activity_id.resolve(
        activity_follow=(70841, 70842)
    ) == 70842
    assert beast_scopes["personal"].activity_id.resolve(
        activity_follow=(110108, 110208)
    ) == 110108
    assert beast_scopes["team"].activity_id.resolve(
        activity_follow=(110108, 110208)
    ) == 110208
    assert yunmeng_scopes["personal"].activity_id.resolve(
        activity_follow=(210203, 210204)
    ) == 210203
    assert yunmeng_scopes["plane"].activity_id.resolve(
        activity_follow=(210203, 210204)
    ) == 210204

    dandao_scopes = {
        scope.scope: scope
        for scope in get_exchange_activity_spec("dandao-wending").rank_scopes
    }
    assert dandao_scopes["personal"].activity_id.resolve(
        activity_id=1043111,
    ) == 1043111
    assert dandao_scopes["personal"].activity_id.resolve(
        activity_id=4043101,
        activity_follow=(43103, 43104),
    ) == 43103
    assert dandao_scopes["plane"].activity_id.resolve(
        activity_id=4043101,
        activity_follow=(43103, 43104),
    ) == 43104

    assert resolve_registered_occurrence_rank_activity_ids(
        activity_type="tiandi-yiju", activity_id=8090001
    ) == {"personal": 90101, "alliance": 90102}
    assert resolve_registered_occurrence_rank_activity_ids(
        activity_type="tiandi-yiju", activity_id=8090004
    ) == {"personal": 90808, "alliance": 90813}
    assert resolve_registered_occurrence_shop(
        activity_type="tiandi-yiju", cross_count=1
    ) == ShopSpec(base_id=90000, currency_type=11)
    assert resolve_registered_occurrence_shop(
        activity_type="tiandi-yiju", cross_count=8
    ) == ShopSpec(base_id=90002, currency_type=13)


def test_spec_rejects_page_scope_and_shop_currency_drift() -> None:
    with pytest.raises(ValueError, match="页面榜单 scope"):
        replace(
            MAGIC_INVASION_SPEC,
            page=replace(MAGIC_INVASION_SPEC.page, ranking_scopes=("personal",)),
        )
    with pytest.raises(ValueError, match="商店币种"):
        replace(MAGIC_INVASION_SPEC, shop=ShopSpec(base_id=70001, currency_type=12))
    with pytest.raises(ValueError, match="follow 缺少索引"):
        RankActivityIdBinding(
            source="activity_follow",
            follow_index=1,
        ).resolve(activity_follow=(70841,))


@pytest.mark.parametrize(
    ("activity_type", "module_name", "function_name"),
    [
        (
            "yunmeng-trial",
            "backend.core.fanxiu.activity.yunmeng_exchange",
            "collect_and_store_yunmeng_exchange_activity",
        ),
        (
            "xianyuan-duokui",
            "backend.core.fanxiu.activity.xianyuan_duokui",
            "collect_and_store_xianyuan_duokui_activity",
        ),
        (
            "xutian-palace",
            "backend.core.fanxiu.activity.xutian_palace_instrumentation",
            "collect_and_store_xutian_palace_activity",
        ),
        (
            "magic-invasion",
            "backend.core.fanxiu.activity.magic_invasion",
            "collect_and_store_magic_invasion_activity",
        ),
        (
            "beast-abyss",
            "backend.core.fanxiu.activity.beast_abyss",
            "collect_and_store_beast_abyss_activity",
        ),
        (
            "tiandi-yiju",
            "backend.core.fanxiu.activity.tiandi_yiju",
            "collect_and_store_tiandi_yiju_activity",
        ),
    ],
)
def test_registered_adapter_delegates_to_existing_collector(
    monkeypatch,
    activity_type: str,
    module_name: str,
    function_name: str,
) -> None:
    module = __import__(module_name, fromlist=[function_name])
    calls = []
    monkeypatch.setattr(
        module,
        function_name,
        lambda session, *, activity_id: (
            calls.append((session, activity_id)) or activity_type
        ),
    )
    session = object()

    result = collect_registered_exchange_activity(
        session,
        activity_type=activity_type,
        activity_id="activity-1",
    )

    assert result == activity_type
    assert calls == [(session, "activity-1")]
