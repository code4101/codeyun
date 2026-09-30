from dataclasses import replace

import pytest


@pytest.mark.parametrize("activity_id, cross_count, follow, expected", [
    (1042801, 1, (), {"personal": 1042801}),
    (1042811, 1, (), {"personal": 1042811}),
    (2042801, 2, (42831, 42832), {"personal": 42831, "plane": 42832}),
    (4042801, 4, (42803, 42804), {"personal": 42803, "plane": 42804}),
])
def test_yaochi_occurrence_rank_ids_follow_actual_activity_config(activity_id, cross_count, follow, expected):
    """Activity rows: same-server owns a rank; cross-server references children."""
    identities = resolve_registered_occurrence_rank_identities(
        activity_type="yaochi-flower-festival", game_activity_id=activity_id,
        cross_count=cross_count, activity_follow=follow,
    )
    assert {key: value.runtime_rank_activity_id for key, value in identities.items()} == expected
    assert {key: value.reward_activity_id for key, value in identities.items()} == expected

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
    resolve_registered_occurrence_rank_identities,
    resolve_registered_occurrence_shop,
)
from backend.core.fanxiu.activity.exchange_activity_spec import (
    ExchangeActivityAdapter,
    PageContract,
    RankActivityIdBinding,
    deserialize_rank_scope_identities,
    RankScopeSpec,
    ShopSpec,
)


def test_rank_scope_identity_deserializer_merges_partial_canonical_and_legacy_maps() -> None:
    identities = deserialize_rank_scope_identities(
        {
            "rank_scope_identities": {
                "personal": {
                    "runtime_rank_activity_id": 83291,
                    "reward_activity_id": 83291,
                }
            }
        },
        {
            "rank_scope_activity_ids": {"personal": 83291, "plane": 83271},
            "reward_scope_activity_ids": {"personal": 83291, "plane": 83271},
        },
    )
    assert identities["personal"].runtime_rank_activity_id == 83291
    assert identities["plane"].runtime_rank_activity_id == 83271


def test_public_exchange_activity_types_are_uniquely_registered() -> None:
    assert set(EXCHANGE_ACTIVITY_SPECS) == set(PUBLIC_EXCHANGE_ACTIVITY_TYPES)
    assert get_exchange_activity_spec("xutian-palace") is XUTIAN_PALACE_SPEC
    assert get_exchange_activity_spec("magic-invasion") is MAGIC_INVASION_SPEC
    assert get_exchange_activity_spec("beast-abyss") is BEAST_ABYSS_SPEC
    assert get_exchange_activity_spec("yunmeng-trial") is YUNMENG_TRIAL_SPEC
    assert get_exchange_activity_spec("xianyuan-duokui") is XIANYUAN_DUOKUI_SPEC
    assert get_exchange_activity_spec("tiandi-yiju") is TIANDI_YIJU_SPEC
    assert {
        key for key, spec in EXCHANGE_ACTIVITY_SPECS.items()
        if spec.family == "gameplay_rank"
    } == {
        "yunmeng-trial", "xianyuan-duokui", "xutian-palace",
        "magic-invasion", "beast-abyss", "tiandi-yiju", "shengxian-hui",
    }
    assert {
        key for key, spec in EXCHANGE_ACTIVITY_SPECS.items()
        if spec.family == "resource_rank"
    } == {
        "lingzhuang-huadao", "yaochi-flower-festival", "yuanding-sansheng",
        "lingchong-jingwu", "shequn-lingchong", "lianti-faxiang",
        "dandao-wending", "xiling-zhengwu",
    }
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


def test_rank_scope_supports_typed_runtime_binding_and_team_subject() -> None:
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
        runtime_rank_activity_id=RankActivityIdBinding(source="activity_follow", follow_index=1),
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

    assert xutian_scopes["personal"].runtime_rank_activity_id.resolve(cross_count=8) == 80891
    assert xutian_scopes["plane"].runtime_rank_activity_id.resolve(cross_count=8) == 80871
    assert magic_scopes["personal"].runtime_rank_activity_id.resolve(
        activity_follow=(70841, 70842)
    ) == 70841
    assert magic_scopes["plane"].runtime_rank_activity_id.resolve(
        activity_follow=(70841, 70842)
    ) == 70842
    assert beast_scopes["personal"].runtime_rank_activity_id.resolve(
        activity_follow=(110108, 110208)
    ) == 110108
    assert beast_scopes["team"].runtime_rank_activity_id.resolve(
        activity_follow=(110108, 110208)
    ) == 110208
    assert yunmeng_scopes["personal"].runtime_rank_activity_id.resolve(
        activity_follow=(210203, 210204)
    ) == 210203
    assert yunmeng_scopes["plane"].runtime_rank_activity_id.resolve(
        activity_follow=(210203, 210204)
    ) == 210204

    dandao_scopes = {
        scope.scope: scope
        for scope in get_exchange_activity_spec("dandao-wending").rank_scopes
    }
    assert dandao_scopes["personal"].runtime_rank_activity_id.resolve(
        activity_id=1043111,
    ) == 1043111
    assert dandao_scopes["personal"].runtime_rank_activity_id.resolve(
        activity_id=4043101,
        activity_follow=(43103, 43104),
    ) == 43103
    assert dandao_scopes["plane"].runtime_rank_activity_id.resolve(
        activity_id=4043101,
        activity_follow=(43103, 43104),
    ) == 43104

    identities = resolve_registered_occurrence_rank_identities(
        activity_type="tiandi-yiju", game_activity_id=8090001, cross_count=1
    )
    assert {scope: item.runtime_rank_activity_id for scope, item in identities.items()} == {
        "personal": 90101, "alliance": 90102
    }
    shequn_identities = resolve_registered_occurrence_rank_identities(
        activity_type="shequn-lingchong",
        game_activity_id=4043501,
        cross_count=4,
        activity_follow=(43502,),
    )
    assert {
        scope: item.runtime_rank_activity_id
        for scope, item in shequn_identities.items()
    } == {"alliance": 43502}
    identities = resolve_registered_occurrence_rank_identities(
        activity_type="tiandi-yiju", game_activity_id=8090004, cross_count=8
    )
    assert {scope: item.runtime_rank_activity_id for scope, item in identities.items()} == {
        "personal": 90808, "alliance": 90813
    }

    xutian_identities = resolve_registered_occurrence_rank_identities(
        activity_type="xutian-palace",
        game_activity_id=4080001,
        cross_count=4,
    )
    current_xutian = resolve_registered_occurrence_rank_identities(
        activity_type="xutian-palace", game_activity_id=8080001, cross_count=8)
    assert current_xutian["personal"].runtime_rank_activity_id == 80851
    assert xutian_identities["personal"].runtime_rank_activity_id == 80491
    assert xutian_identities["personal"].reward_activity_id == 80452
    assert xutian_identities["plane"].runtime_rank_activity_id == 80471
    assert xutian_identities["plane"].reward_activity_id == 80472
    assert resolve_registered_occurrence_shop(
        activity_type="tiandi-yiju", cross_count=1
    ) == ShopSpec(base_id=90000, currency_type=11)
    assert resolve_registered_occurrence_shop(
        activity_type="tiandi-yiju", cross_count=8
    ) == ShopSpec(base_id=90002, currency_type=13)


@pytest.mark.parametrize(
    ("activity_id", "cross_count", "rank_ids", "shop", "task_id"),
    [
        (16090001, 1, (90101, 90102), ShopSpec(base_id=90000, currency_type=11), 16090001),
        (16090004, 16, (91608, 91613), ShopSpec(base_id=90002, currency_type=13), 16090003),
    ],
)
def test_tiandi_sixteen_server_edition_identity(activity_id, cross_count, rank_ids, shop, task_id):
    """The edition prefix must not turn the local preliminary into 16-cross."""
    from backend.core.fanxiu.activity.ranking_lifecycle import TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
    from backend.core.fanxiu.instrumentation.tiandi_yiju_task_rewards import tiandi_yiju_task_activity_id

    scopes = resolve_registered_occurrence_rank_identities(
        activity_type="tiandi-yiju", game_activity_id=activity_id, cross_count=cross_count,
    )
    assert tuple(scopes[name].runtime_rank_activity_id for name in ("personal", "alliance")) == rank_ids
    assert resolve_registered_occurrence_shop(
        activity_type="tiandi-yiju", activity_id=activity_id, cross_count=cross_count,
    ) == shop
    assert resolve_registered_occurrence_shop(activity_type="tiandi-yiju", cross_count=cross_count) == shop
    assert activity_id in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
    assert tiandi_yiju_task_activity_id(activity_id) == task_id
    with pytest.raises(ValueError, match="跨数不一致"):
        resolve_registered_occurrence_shop(
            activity_type="tiandi-yiju", activity_id=activity_id, cross_count=8,
        )
    assert 16090002 not in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS
    assert 16090003 not in TIANDI_YIJU_PLAYABLE_ACTIVITY_IDS


@pytest.mark.parametrize("activity_id,cross_count,follow,expected", [
    (1044311, 1, (), {"personal": 1044311}),
    (8044301, 8, (44305, 44306), {"personal": 44305, "plane": 44306}),
    (16044301, 16, (44307, 44308), {"personal": 44307, "plane": 44308}),
    (32044301, 32, (44309, 44310), {"personal": 44309, "plane": 44310}),
])
def test_lingzhuang_rank_bindings_follow_each_occurrence(activity_id, cross_count, follow, expected):
    identities = resolve_registered_occurrence_rank_identities(
        activity_type="lingzhuang-huadao", game_activity_id=activity_id,
        cross_count=cross_count, activity_follow=follow,
    )
    assert {scope: identity.runtime_rank_activity_id for scope, identity in identities.items()} == expected


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
        lambda session, *, activity_id, **kwargs: (
            calls.append((session, activity_id, kwargs)) or activity_type
        ),
    )
    session = object()

    result = collect_registered_exchange_activity(
        session,
        activity_type=activity_type,
        activity_id="activity-1",
    )

    assert result == activity_type
    expected_options = (
        {"collect_runtime_rank": True, "collect_related_runtime_ranks": True}
        if activity_type == "beast-abyss"
        else {}
    )
    assert calls == [(session, "activity-1", expected_options)]
