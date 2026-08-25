import json

from sqlmodel import Session, SQLModel, create_engine
import pytest
from backend.core.fanxiu.instrumentation import activity_shop

from backend.core.fanxiu.activity.yunmeng_trial import (
    format_yunmeng_trial_label,
    list_yunmeng_trial_rankings,
    list_yunmeng_trial_snapshot,
    list_yunmeng_trial_measurements,
    store_yunmeng_trial_measurement,
    update_yunmeng_trial_priorities,
    update_yunmeng_trial_shop_item_lock,
    upsert_yunmeng_trial_snapshot,
)
from backend.core.fanxiu.activity.yunmeng_trial_instrumentation import (
    infer_yunmeng_cross_count,
    normalize_yunmeng_shop_runtime_rows,
)
from backend.core.fanxiu.activity.yunmeng_rank_reward import (
    load_yunmeng_rank_reward_tiers,
)
from backend.core.fanxiu.instrumentation.activity_shop import (
    _decode_config_rows_from_shop_dictionary,
    _discover_active_index,
    _discover_page_show_list_groups,
    _read_activity_shop_purchase_counts,
    _read_show_list_groups,
    FanxiuActivityShopCollectionError,
    lua51_sort_goods_ids,
)
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef
from backend.core.fanxiu.instrumentation.yunmeng_trial import reward_guard_tiers
from backend.models import (
    FanxiuYunmengTrialActivity,
    FanxiuYunmengTrialMeasurement,
    FanxiuYunmengTrialRanking,
    FanxiuYunmengTrialShopItem,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            FanxiuYunmengTrialActivity.__table__,
            FanxiuYunmengTrialShopItem.__table__,
            FanxiuYunmengTrialRanking.__table__,
            FanxiuYunmengTrialMeasurement.__table__,
        ],
    )
    return Session(engine)


def test_yunmeng_trial_label_omits_repeated_date_parts() -> None:
    assert format_yunmeng_trial_label(1, "2026-08-02", "2026-08-02") == "1跨,2026/8/2"
    assert format_yunmeng_trial_label(4, "2026-09-05", "2026-09-06") == "4跨,2026/9/5-9/6"
    assert format_yunmeng_trial_label(8, "2026-12-31", "2027-01-01") == "8跨,2026/12/31-2027/1/1"


def test_yunmeng_reward_guard_ranks_follow_effective_reward_config(tmp_path) -> None:
    activity_dir = tmp_path / "parsed_configs" / "Activity"
    reward_dir = tmp_path / "parsed_configs" / "ActivityListReward"
    activity_dir.mkdir(parents=True)
    reward_dir.mkdir(parents=True)
    (activity_dir / "rows.json").write_text(
        json.dumps([{"id": 210801, "rewardGroup": 8210001}]),
        encoding="utf-8",
    )
    (reward_dir / "rows.json").write_text(
        json.dumps(
            [
                {"id": 1, "group": 8210001, "rankingRange": ["1", "1"], "condition": "ActivityIdOpenBefore|210801_20250411"},
                {"id": 2, "group": 8210001, "rankingRange": ["1", "1"], "reward": ["Item|9070095_80"], "condition": "ActivityIdOpenAfter|210801_20250411"},
                {"id": 3, "group": 8210001, "rankingRange": ["2", "2"], "reward": ["Item|9070095_60"], "condition": "ActivityIdOpenAfter|210801_20250411"},
                {"id": 4, "group": 8210001, "rankingRange": ["3", "4"], "reward": ["Item|9070095_40"], "condition": "ActivityIdOpenAfter|210801_20250411"},
                {"id": 5, "group": 8210001, "rankingRange": ["5", "8"], "reward": ["Item|9070095_20"], "condition": "ActivityIdOpenAfter|210801_20250411"},
            ]
        ),
        encoding="utf-8",
    )

    tiers = load_yunmeng_rank_reward_tiers(
        rank_activity_id=210801,
        event_date="2026-08-02",
        export_root=tmp_path,
    )

    assert [(row["rank_start"], row["rank_end"]) for row in tiers] == [
        (1, 1),
        (2, 2),
        (3, 4),
        (5, 8),
    ]
    assert [row["rewards"] for row in tiers] == [
        ["Item|9070095_80"],
        ["Item|9070095_60"],
        ["Item|9070095_40"],
        ["Item|9070095_20"],
    ]
    assert [row["rank_end"] for row in reward_guard_tiers(tiers, 7)] == [1, 2, 4]
    assert [row["rank_end"] for row in reward_guard_tiers(tiers, 8)] == [1, 2, 4, 8]


def test_rank_reward_config_supports_composite_activity_date_conditions(tmp_path) -> None:
    activity_dir = tmp_path / "parsed_configs" / "Activity"
    reward_dir = tmp_path / "parsed_configs" / "ActivityListReward"
    activity_dir.mkdir(parents=True)
    reward_dir.mkdir(parents=True)
    (activity_dir / "rows.json").write_text(
        json.dumps([{"id": 44307, "rewardGroup": 44307}]),
        encoding="utf-8",
    )
    (reward_dir / "rows.json").write_text(
        json.dumps(
            [
                {"id": 1, "group": 44307, "rankingRange": [1, 1], "condition": "ActivityIdOpenBefore|44307_20250411"},
                {"id": 2, "group": 44307, "rankingRange": [1, 1], "condition": "ActivityIdOpenBefore|44307_20250808,ActivityIdOpenAfter|44307_20250411"},
                {"id": 3, "group": 44307, "rankingRange": [1, 1], "condition": "ActivityIdOpenAfter|44307_20250808"},
            ]
        ),
        encoding="utf-8",
    )

    tiers = load_yunmeng_rank_reward_tiers(
        rank_activity_id=44307,
        event_date="2026-08-03",
        export_root=tmp_path,
    )

    assert [row["config_id"] for row in tiers] == [3]


def test_rank_reward_config_supports_semicolon_sibling_activity_conditions(tmp_path) -> None:
    activity_dir = tmp_path / "parsed_configs" / "Activity"
    reward_dir = tmp_path / "parsed_configs" / "ActivityListReward"
    activity_dir.mkdir(parents=True)
    reward_dir.mkdir(parents=True)
    (activity_dir / "rows.json").write_text(
        json.dumps([{"id": 1042811, "rewardGroup": 1042801}]),
        encoding="utf-8",
    )
    (reward_dir / "rows.json").write_text(
        json.dumps(
            [
                {"id": 1, "group": 1042801, "serverDay": [31, 9999], "rankingRange": [1, 1], "condition": "ActivityIdOpenBefore|1042801_20250411;ActivityIdOpenBefore|1042811_20250411"},
                {"id": 2, "group": 1042801, "serverDay": [31, 9999], "rankingRange": [1, 1], "reward": ["Item|9070095_10"], "condition": "ActivityIdOpenAfter|1042801_20250411;ActivityIdOpenAfter|1042811_20250411"},
            ]
        ),
        encoding="utf-8",
    )

    tiers = load_yunmeng_rank_reward_tiers(
        rank_activity_id=1042811,
        event_date="2026-08-05",
        export_root=tmp_path,
        server_day=473,
    )

    assert [row["config_id"] for row in tiers] == [2]


def test_ranking_page_keeps_configured_tiers_without_players(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.yunmeng_trial.load_yunmeng_rank_reward_tiers",
        lambda **_: [
            {"rank_start": 65, "rank_end": 128, "rewards": ["Item|9070095_8"]},
            {"rank_start": 129, "rank_end": 256, "rewards": ["Item|9070095_4"]},
            {"rank_start": 257, "rank_end": 512, "rewards": []},
        ],
    )
    with _session() as session:
        activity = FanxiuYunmengTrialActivity(
            cross_count=8,
            start_date="2026-08-02",
            end_date="2026-08-02",
            game_rank_activity_id=210801,
        )
        session.add(activity)
        session.commit()
        session.refresh(activity)
        session.add(
            FanxiuYunmengTrialRanking(
                activity_id=activity.id,
                rank=128,
                score=-30040,
                role_key="rank-128",
                name="荒昊",
                raw_data={
                    "is_reward_guard": True,
                    "reward_rank_start": 65,
                    "reward_rank_end": 128,
                },
            )
        )
        session.commit()

        result = list_yunmeng_trial_rankings(
            session,
            activity_id=activity.id,
            page=1,
            page_size=20,
        )

        assert result.total == 3
        assert [row.rank for row in result.items] == [128, 256, 512]
        assert [row.has_player for row in result.items] == [True, False, False]
        assert [row.talent_pill_count for row in result.items] == [8, 4, 0]
        assert result.items[0].score_per_talent_pill == -3755
        assert result.items[1].score_per_talent_pill is None
        assert result.items[2].score_per_talent_pill is None
        assert result.items[1].reward_rank_start == 129
        assert result.items[1].name == ""
        assert result.items[1].score == 0


def test_ranking_page_projects_self_reward_tier(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.yunmeng_trial.load_yunmeng_rank_reward_tiers",
        lambda **_: [
            {"rank_start": 9, "rank_end": 16, "rewards": ["Item|9070095_30"]},
        ],
    )
    with _session() as session:
        activity = FanxiuYunmengTrialActivity(
            cross_count=8,
            start_date="2026-08-02",
            end_date="2026-08-02",
            game_rank_activity_id=210801,
        )
        session.add(activity)
        session.commit()
        session.refresh(activity)
        session.add_all(
            [
                FanxiuYunmengTrialRanking(
                    activity_id=activity.id,
                    rank=13,
                    score=2138000,
                    role_key="self",
                    name="自己",
                    is_self=True,
                ),
                FanxiuYunmengTrialRanking(
                    activity_id=activity.id,
                    rank=16,
                    score=1748000,
                    role_key="rank-16",
                    name="守门员",
                    raw_data={
                        "is_reward_guard": True,
                        "reward_rank_start": 9,
                        "reward_rank_end": 16,
                    },
                ),
            ]
        )
        session.commit()

        result = list_yunmeng_trial_rankings(
            session,
            activity_id=activity.id,
            page=1,
            page_size=20,
        )

        self_item = next(row for row in result.items if row.is_self)
        assert (self_item.reward_rank_start, self_item.reward_rank_end) == (9, 16)
        assert self_item.talent_pill_count == 30
        assert self_item.score_per_talent_pill == pytest.approx(2138000 / 30)


def test_activity_shop_discovers_complete_runtime_index() -> None:
    expected = {8210101: 3, 1010871: 0, 8210134: 2, 8210025: 1}
    nodes = [
        (0x1000 + position * 24, goods_id, position)
        for goods_id, position in expected.items()
    ]
    assert _discover_active_index(nodes) == expected


def test_activity_shop_reads_grouped_page_show_list_exactly() -> None:
    row_a = [None, 101, 7001, 70001, 29602, 1, 1000, 17, 6,
             10, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]
    row_b = [None, 102, 7001, 70001, 29602, 1, 1000, 17, 6,
             6, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]
    row_c = [None, 103, 7010, 70001, 19010068, 1, 9000, 17, 6,
             1, None, "EqualCrossGroup|70001_8", None, 2, 1, None, None]

    class Reader:
        lists = {
            1: ([LuaRef("table", 2), LuaRef("table", 3)], 2),
            2: ([LuaRef("table", 11), LuaRef("table", 12)], 2),
            3: ([LuaRef("table", 13)], 1),
        }
        tables = {11: row_a, 12: row_b, 13: row_c}

        def list_items(self, value):
            return self.lists[value.address]

        def table(self, address):
            return {"array": self.tables[address]}

    diagnostics = {}
    groups = _read_show_list_groups(
        Reader(),
        LuaRef("table", 1),
        rows=[row_a, row_b, row_c],
        diagnostics=diagnostics,
    )

    assert [[row[1] for row in group] for group in groups] == [[101, 102], [103]]
    assert diagnostics == {
        "outer_keys": [1, 2],
        "declared_outer_count": 2,
        "groups": [
            {
                "outer_key": 1,
                "inner_keys": [1, 2],
                "config_ids": [101, 102],
                "config_groups": [7001, 7001],
            },
            {
                "outer_key": 2,
                "inner_keys": [1],
                "config_ids": [103],
                "config_groups": [7010],
            },
        ],
    }


def test_activity_shop_reads_and_aggregates_server_purchase_ledger() -> None:
    row_a = [None, 101, 7001, 70001, 29602, 1, 1000, 17, 6,
             10, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]
    row_b = [None, 102, 7001, 70001, 29602, 1, 1000, 17, 6,
             6, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]
    row_c = [None, 103, 7010, 70001, 19010068, 1, 9000, 17, 6,
             1, None, "EqualCrossGroup|70001_8", None, 2, 1, None, None]

    class Reader:
        def dictionary_fields(self, value):
            assert value.address == 1
            # Config 102 is absent because it has never been purchased.
            return {101: LuaRef("table", 11), 103: LuaRef("table", 13)}

        def table(self, address):
            return {
                11: {
                    "fields": {
                        "cfg": LuaRef("table", 21),
                        "svr": LuaRef("table", 31),
                    }
                },
                13: {
                    "fields": {
                        "cfg": LuaRef("table", 23),
                        "svr": LuaRef("table", 33),
                    }
                },
                21: {"array": row_a},
                23: {"array": row_c},
                31: {"fields": {"shopItemId": 101, "num": 4}},
                33: {"fields": {"shopItemId": 103, "num": 1}},
            }[address]

    counts, evidence = _read_activity_shop_purchase_counts(
        Reader(),
        {"V_ShopInfo": LuaRef("table", 1)},
        [[row_a, row_b], [row_c]],
    )

    assert counts == {101: 4, 103: 1}
    assert evidence["declared_server_record_count"] == 2
    assert evidence["active_nonzero_limited_records"] == {"101": 4, "103": 1}


def test_activity_shop_purchase_ledger_fails_closed_on_identity_mismatch() -> None:
    row = [None, 101, 7001, 70001, 29602, 1, 1000, 17, 6,
           10, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]

    class Reader:
        def dictionary_fields(self, _value):
            return {101: LuaRef("table", 11)}

        def table(self, address):
            return {
                11: {
                    "fields": {
                        "cfg": LuaRef("table", 21),
                        "svr": LuaRef("table", 31),
                    }
                },
                21: {"array": row},
                31: {"fields": {"shopItemId": 999, "num": 4}},
            }[address]

    with pytest.raises(FanxiuActivityShopCollectionError, match="身份或购买数量"):
        _read_activity_shop_purchase_counts(
            Reader(),
            {"V_ShopInfo": LuaRef("table", 1)},
            [[row]],
        )


def test_activity_shop_ignores_cross_period_unlimited_purchase_count() -> None:
    row = [None, 101, 7001, 70001, 29602, 1, 1000, 17, 6,
           -1, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]

    class Reader:
        def dictionary_fields(self, _value):
            return {101: LuaRef("table", 11)}

        def table(self, address):
            return {
                11: {
                    "fields": {
                        "cfg": LuaRef("table", 21),
                        "svr": LuaRef("table", 31),
                    }
                },
                21: {"array": row},
                31: {"fields": {"shopItemId": 101, "num": 3421}},
            }[address]

    counts, evidence = _read_activity_shop_purchase_counts(
        Reader(),
        {"V_ShopInfo": LuaRef("table", 1)},
        [[row]],
    )

    assert counts == {101: 0}
    assert evidence["active_nonzero_limited_records"] == {}
    assert evidence["ignored_cross_period_unlimited_records"] == {"101": 3421}


def test_activity_shop_follows_fixed_active_redemption_panel_chain(monkeypatch) -> None:
    row = [None, 101, 7001, 70001, 29602, 1, 1000, 17, 6,
           16, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]

    class Memory:
        def read(self, address, size, **_kwargs):
            assert (address, size) == (0x1000 + 72, 8)
            return (1).to_bytes(8, "little")

    class Reader:
        fields = {
            (1, "package"): LuaRef("table", 2),
            (2, "loaded"): LuaRef("table", 3),
            (3, "Core.UIManager.Manager.UIShowMgr"): LuaRef("table", 4),
            (5, "V_M_compDic"): LuaRef("table", 6),
            (6, "_dt_"): LuaRef("table", 7),
            (9, "m_panel"): LuaRef("table", 10),
            (10, "tabPanelGroup"): LuaRef("table", 11),
            (11, "curTabIndex"): 4,
            (11, "panelShowComps"): LuaRef("table", 12),
            (12, "count"): 5,
            (12, "_dt_"): LuaRef("table", 18),
            (13, "m_panel"): LuaRef("table", 14),
            (14, "V_BaseActivityId"): 70001,
            (14, "V_WalletType"): 17,
            (14, "V_ShowList"): LuaRef("table", 15),
        }

        def interned_string_field(self, address, name, **_kwargs):
            return self.fields.get((address, name))

        def metatable_index_string_field(self, address, name, **_kwargs):
            assert (address, name) == (4, "inst")
            return LuaRef("table", 5)

        def list_items(self, value):
            return {
                8: ([LuaRef("table", 9)], 1),
                15: ([LuaRef("table", 16)], 1),
                16: ([LuaRef("table", 17)], 1),
            }[value.address]

        def table(self, address):
            if address == 7:
                return {"fields": {114202: LuaRef("table", 8)}}
            if address == 17:
                return {"array": row}
            if address == 18:
                return {
                    "array": [None, None, None],
                    "fields": {5: LuaRef("table", 13)},
                }
            raise AssertionError(address)

    monkeypatch.setattr(activity_shop, "_lua_addresses", lambda _memory: {"state": "0x1000"})
    monkeypatch.setattr(
        activity_shop,
        "lua_jit_intern_state",
        lambda _memory, _state: (0, 0x2000, 7, 0),
    )
    groups, panel_address, show_list_evidence = _discover_page_show_list_groups(
        Memory(), Reader(), shop_base_id=70001, expected_currency_type=17, rows=[row]
    )
    assert panel_address == 14
    assert groups == [[row]]
    assert show_list_evidence["outer_keys"] == [1]


def test_activity_shop_rejects_incomplete_page_show_list() -> None:
    class Reader:
        def list_items(self, _value):
            return [LuaRef("table", 2)], 2

    with pytest.raises(FanxiuActivityShopCollectionError, match="缺少可校验"):
        _read_show_list_groups(Reader(), LuaRef("table", 1), rows=[])


def test_activity_shop_reads_short_and_long_rows_from_dictionary_backing() -> None:
    short_row = [None, 101, 7001, 70001, 29602, 1, 1000, 17, 6,
                 16, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]
    long_row = [None, 102, 7002, 70001, 3020167, 20, 5000, 17, 6,
                2, None, "EqualCrossGroup|70001_8", None, 2, 1, None,
                None, 15, 50, 10000, None] + [None] * 11

    class Reader:
        def dictionary_fields(self, value):
            return (
                {70001: LuaRef("table", 2)}
                if value.address == 1
                else {17: LuaRef("table", 3)}
            )

        def list_items(self, value):
            assert value.address == 3
            return [LuaRef("table", 11), LuaRef("table", 12)], 2

        def table(self, address):
            return {"array": {11: short_row, 12: long_row}[address]}

    rows = _decode_config_rows_from_shop_dictionary(
        Reader(), LuaRef("table", 1), shop_base_id=70001, currency_type=17
    )

    assert len(short_row) == 17
    assert rows == [short_row, long_row]


def test_activity_shop_canonicalizes_reward_rows_and_rejects_mixed_layouts() -> None:
    compact = [None, 101, 7001, 70001, 29602, 1, 1000, 17, 6,
              16, None, "EqualCrossGroup|70001_8", None, 1, 1, None, None]
    reward = [None, 102, 7002, 70001, 19010068, 1, None, 9000, 17, 6,
                 1, None, "EqualCrossGroup|70001_8", None, 2, 1, None]

    class Reader:
        def __init__(self, rows):
            self.rows = rows

        def dictionary_fields(self, value):
            return (
                {70001: LuaRef("table", 2)}
                if value.address == 1
                else {17: LuaRef("table", 3)}
            )

        def list_items(self, value):
            assert value.address == 3
            return [LuaRef("table", 11 + i) for i in range(len(self.rows))], len(self.rows)

        def table(self, address):
            return {"array": self.rows[address - 11]}

    [compact_canonical] = _decode_config_rows_from_shop_dictionary(
        Reader([compact]), LuaRef("table", 1), shop_base_id=70001, currency_type=17
    )
    assert compact_canonical == compact
    [reward_canonical] = _decode_config_rows_from_shop_dictionary(
        Reader([reward]), LuaRef("table", 1), shop_base_id=70001, currency_type=17
    )
    assert (reward_canonical[6], reward_canonical[7]) == (9000, 17)
    assert (reward_canonical[9], reward_canonical[13]) == (
        1, 2
    )

    with pytest.raises(FanxiuActivityShopCollectionError, match="混入两种配置行布局"):
        _decode_config_rows_from_shop_dictionary(
            Reader([compact, reward]),
            LuaRef("table", 1),
            shop_base_id=70001,
            currency_type=17,
        )


def test_activity_shop_reproduces_lua51_unstable_tie_order() -> None:
    active_order = [8210129, 8210105, 8210111, 8210101, 8210023, 8210120,
                    8210124, 8210112, 8210116, 8210137, 8210126, 8210134,
                    8210106, 8210108, 8210102, 8210024, 8210118, 1010871,
                    8210132, 8210123, 8210121, 8210119, 8210117, 8210115,
                    8210114, 8210113, 8210107, 8210103, 8210025]
    sort_order = {
        8210101: 1, 1010871: 1, 8210134: 2, 8210025: 3,
        8210023: 4, 8210024: 4, 8210132: 4, 8210121: 10,
        8210123: 14, 8210124: 15, 8210102: 16, 8210103: 17,
        8210105: 19, 8210106: 20, 8210107: 21, 8210108: 22,
        8210137: 23, 8210126: 26, 8210129: 27, 8210111: 28,
        8210112: 29, 8210113: 30, 8210114: 31, 8210115: 32,
        8210116: 33, 8210117: 34, 8210118: 35, 8210119: 36,
        8210120: 37,
    }
    assert lua51_sort_goods_ids(active_order, sort_order) == [
        8210101, 1010871, 8210134, 8210025, 8210024, 8210132,
        8210023, 8210121, 8210123, 8210124, 8210102, 8210103,
        8210105, 8210106, 8210107, 8210108, 8210137, 8210126,
        8210129, 8210111, 8210112, 8210113, 8210114, 8210115,
        8210116, 8210117, 8210118, 8210119, 8210120,
    ]


def test_yunmeng_trial_snapshot_is_persisted_and_priorities_are_cumulative() -> None:
    with _session() as session:
        activity_id = upsert_yunmeng_trial_snapshot(
            session,
            {
                "cross_count": 1,
                "start_date": "2026-08-02",
                "current_currency": 15926,
                "cumulative_currency": 15926,
                "shop_items": [
                    {
                        "goods_id": 101,
                        "item_id": 1001,
                        "name": "甲",
                        "token_cost": 100,
                        "purchase_limit": 2,
                    },
                    {
                        "goods_id": 102,
                        "item_id": 1002,
                        "name": "乙",
                        "token_cost": 50,
                        "purchase_limit": 3,
                    },
                ],
                "rankings": [
                    {
                        "rank": 15,
                        "score": 135058,
                        "name": "止清ღ羊驼",
                        "role_key": "self",
                        "is_self": True,
                    },
                    {
                        "ranking_scope": "plane",
                        "rank": 8,
                        "score": -100,
                        "role_key": "22049",
                        "server_id": 22049,
                        "is_last_player": True,
                    },
                ],
            },
        )

        snapshot = list_yunmeng_trial_snapshot(session)
        assert snapshot.selected_activity is not None
        assert snapshot.selected_activity.id == activity_id
        assert snapshot.selected_activity.label == "1跨,2026/8/2"
        assert [item.source_order for item in snapshot.selected_activity.shop_items] == [1, 2]

        detail = update_yunmeng_trial_priorities(
            session,
            activity_id=activity_id,
            ordered_goods_ids=[102, 101],
        )
        assert [item.priority_order for item in detail.shop_items] == [2, 1]
        assert [item.cumulative_tokens for item in detail.shop_items] == [350, 150]

        detail = update_yunmeng_trial_shop_item_lock(
            session,
            activity_id=activity_id,
            goods_id=102,
            locked=True,
        )
        assert [item.locked for item in detail.shop_items] == [False, True]

        upsert_yunmeng_trial_snapshot(
            session,
            {
                "cross_count": 1,
                "start_date": "2026-08-02",
                "expected_shop_item_count": 2,
                "shop_items": [
                    {"goods_id": 101, "item_id": 1001},
                    {"goods_id": 102, "item_id": 1002},
                ],
            },
        )
        refreshed = list_yunmeng_trial_snapshot(session, activity_id=activity_id)
        assert refreshed.selected_activity is not None
        assert [item.locked for item in refreshed.selected_activity.shop_items] == [False, True]

        ranking_page = list_yunmeng_trial_rankings(
            session,
            activity_id=activity_id,
            page=1,
            page_size=10,
        )
        assert ranking_page.total == 1
        assert ranking_page.items[0].score == 135058
        plane_page = list_yunmeng_trial_rankings(
            session,
            activity_id=activity_id,
            page=1,
            page_size=10,
            ranking_scope="plane",
        )
        assert plane_page.total == 1
        assert plane_page.items[0].server_name == "万事如意"
        assert plane_page.items[0].is_last_player is True


def test_yunmeng_runtime_measurement_appends_history_and_calculates_average(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.yunmeng_trial.load_yunmeng_rank_reward_tiers",
        lambda **_: [{"rank_start": 1, "rank_end": 1, "rewards": []}],
    )
    with _session() as session:
        activity_id = upsert_yunmeng_trial_snapshot(
            session,
            {
                "cross_count": 8,
                "start_date": "2026-08-02",
                "game_rank_activity_id": 210801,
                "currency_type": 19,
                "current_currency": 15926,
                "cumulative_currency": 15926,
                "captured_at": "2026-08-02T14:26:21+08:00",
                "shop_items": [
                    {
                        "goods_id": 101,
                        "item_id": 1001,
                        "name": "已满足",
                        "token_cost": 10000,
                        "purchase_limit": 2,
                    },
                    {
                        "goods_id": 102,
                        "item_id": 1002,
                        "name": "待挑战",
                        "token_cost": 25000,
                        "purchase_limit": 2,
                    },
                    {
                        "goods_id": 103,
                        "item_id": 1003,
                        "name": "无限",
                        "token_cost": 10,
                        "purchase_limit": -1,
                    },
                ],
                "rankings": [
                    {
                        "rank": 16,
                        "score": 100000,
                        "name": "旧守门员",
                        "role_key": "rank-16",
                        "server_id": 22027,
                        "is_self": False,
                        "is_reward_guard": True,
                        "reward_rank_start": 9,
                        "reward_rank_end": 16,
                    },
                    {
                        "rank": 15,
                        "score": 135058,
                        "name": "自己",
                        "role_key": "self",
                        "is_self": True,
                    }
                ],
            },
        )
        activity = session.get(FanxiuYunmengTrialActivity, activity_id)
        assert activity is not None
        session.add(
            FanxiuYunmengTrialMeasurement(
                activity_id=activity_id,
                captured_at=activity.captured_at,
                score=135058,
                exchange_currency=15926,
                rank=15,
                challenge_count_delta=0,
                source_kind="backfilled_activity_snapshot",
            )
        )
        session.commit()

        runtime_snapshot = {
            "complete": True,
            "rank_activity_id": 210801,
            "currency_type": 19,
            "captured_at": "2026-08-02T19:30:00+08:00",
            "score": 323902,
            "rank": 25,
            "exchange_currency": 43910,
            "cumulative_currency": 43910,
            "name": "自己",
            "role_key": "self",
            "server_id": 22077,
            "club_name": "宗门",
            "rankings": [
                {
                    "rank": 1,
                    "score": 12812430,
                    "name": "第一名",
                    "role_key": "rank-1",
                    "server_id": 22088,
                    "club_name": "甲宗",
                    "is_self": False,
                    "is_reward_guard": True,
                },
                {
                    "rank": 25,
                    "score": 323902,
                    "name": "自己",
                    "role_key": "self",
                    "server_id": 22077,
                    "club_name": "宗门",
                    "is_self": True,
                    "is_reward_guard": False,
                },
            ],
            "protocol": {},
            "currency_derivation": "previous_measurement_plus_quick_auto_reward",
            "auto_fight_count": 100,
            "auto_win_count": 93,
            "auto_fail_count": 7,
            "auto_total_score": 93621,
            "exchange_currency_delta": 27984,
            "evidence": {
                "process_start_ticks": 123,
                "yunmeng_root_address": "0xabc",
            },
        }
        with pytest.raises(ValueError, match="必须明确提供本批挑战次数"):
            store_yunmeng_trial_measurement(
                session,
                activity=activity,
                note="缺少批次数量",
                snapshot=runtime_snapshot,
            )
        result = store_yunmeng_trial_measurement(
            session,
            activity=activity,
            challenge_count_delta=100,
            note="100 次挑战后",
            snapshot=runtime_snapshot,
        )

        assert result.score_delta == 188844
        assert result.exchange_currency_delta == 27984
        assert result.average_score_per_challenge == 1888.44
        assert result.average_exchange_currency_per_challenge == 279.84
        assert result.measurement.challenge_count_delta == 100
        assert len(
            list_yunmeng_trial_measurements(
                session, activity_id=activity_id
            ).items
        ) == 2
        ranking_page = list_yunmeng_trial_rankings(
            session, activity_id=activity_id, page=1, page_size=20
        )
        assert [(row.rank, row.is_reward_guard, row.is_self) for row in ranking_page.items] == [
            (1, True, False),
            (16, True, False),
            (25, False, True),
        ]
        assert ranking_page.items[1].name == "旧守门员"
        assert ranking_page.items[2].server_name == "岁序更替"
        assert ranking_page.last_captured_at == "2026-08-02T19:30:00+08:00"
        refreshed = session.get(FanxiuYunmengTrialActivity, activity_id)
        assert refreshed is not None
        assert refreshed.current_currency == 43910
        detail = update_yunmeng_trial_priorities(
            session,
            activity_id=activity_id,
            ordered_goods_ids=[101, 102, 103],
        )
        assert detail.yield_rate is not None
        assert detail.yield_rate.sample_challenges == 100
        assert detail.yield_rate.average_score_per_100 == 188844
        assert detail.yield_rate.average_exchange_currency_per_100 == 27984
        assert [item.remaining_challenges for item in detail.shop_items] == [
            None,
            94,
            None,
        ]
        duplicate_snapshot = dict(runtime_snapshot)
        duplicate_snapshot["rankings"] = [
            {**runtime_snapshot["rankings"][0], "score": 13000000},
            runtime_snapshot["rankings"][1],
        ]
        with pytest.raises(ValueError, match="已经采集"):
            store_yunmeng_trial_measurement(
                session,
                activity=refreshed,
                challenge_count_delta=100,
                note="重复采集",
                snapshot=duplicate_snapshot,
            )
        ranking_page = list_yunmeng_trial_rankings(
            session, activity_id=activity_id, page=1, page_size=20
        )
        assert ranking_page.items[0].score == 13000000

        refreshed.current_currency = 2000
        refreshed.cumulative_currency = 80000
        session.add(refreshed)
        session.commit()
        completed = list_yunmeng_trial_snapshot(
            session,
            activity_id=activity_id,
        ).selected_activity
        assert completed is not None
        assert [item.remaining_challenges for item in completed.shop_items] == [
            None,
            None,
            None,
        ]


def test_yunmeng_trial_snapshot_rejects_truncated_shop_data() -> None:
    with _session() as session:
        with pytest.raises(ValueError, match="期望 29 项，实际 5 项"):
            upsert_yunmeng_trial_snapshot(
                session,
                {
                    "cross_count": 1,
                    "start_date": "2026-08-02",
                    "expected_shop_item_count": 29,
                    "shop_items": [
                        {"goods_id": value, "item_id": value}
                        for value in range(1, 6)
                    ],
                },
            )


def test_yunmeng_trial_partial_snapshot_does_not_delete_shop_items() -> None:
    with _session() as session:
        activity_id = upsert_yunmeng_trial_snapshot(
            session,
            {
                "cross_count": 1,
                "start_date": "2026-08-02",
                "expected_shop_item_count": 2,
                "shop_items": [
                    {"goods_id": 1, "item_id": 11},
                    {"goods_id": 2, "item_id": 22},
                ],
            },
        )
        upsert_yunmeng_trial_snapshot(
            session,
            {
                "cross_count": 1,
                "start_date": "2026-08-02",
                "current_currency": 99,
            },
        )

        snapshot = list_yunmeng_trial_snapshot(session, activity_id=activity_id)
        assert snapshot.selected_activity is not None
        assert len(snapshot.selected_activity.shop_items) == 2


def test_yunmeng_runtime_rows_use_shop_item_id_and_merge_limits() -> None:
    def row(config_id: int, shop_item_id: int, limit: int, sort_order: int):
        return [
            None,
            config_id,
            shop_item_id,
            210001,
            3020168,
            20,
            None,
            5000,
            19,
            6,
            limit,
            None,
            "EqualCrossGroup|210001_8",
            "CL|999",
            sort_order,
        ]

    items = normalize_yunmeng_shop_runtime_rows(
        [row(8210121, 8210121, 2, 10), row(8210122, 8210121, 2, 11)],
        active_shop_item_ids=[8210121],
        display_order=[8210121],
        item_names={3020168: "真言化轮残页"},
    )

    assert items == [
        {
            "goods_id": 8210121,
            "item_id": 3020168,
            "name": "真言化轮残页",
            "goods_num": 20,
            "token_cost": 5000,
            "purchase_limit": 4,
            "discount": None,
            "original_price": None,
            "show_limit": "EqualCrossGroup|210001_8",
            "disappear_limit": "CL|999",
            "raw_data": {
                "config_ids": [8210121, 8210122],
                "aggregated_config_count": 2,
                "shop_item_id": 8210121,
                "cross_count": 8,
            },
            "source_order": 1,
        }
    ]


def test_yunmeng_runtime_rows_preserve_discount_and_original_price() -> None:
    row = [
        None, 8210102, 8210102, 210001, 3020151, 20, None, 2500, 19, 6,
        4, None, "EqualCrossGroup|210001_8", "CL|999", 16, 1, None,
        15, 50, 5000,
    ]
    [item] = normalize_yunmeng_shop_runtime_rows(
        [row],
        active_shop_item_ids=[8210102],
        display_order=[8210102],
        item_names={3020151: "无极御剑诀残页"},
    )
    assert item["token_cost"] == 2500
    assert item["discount"] == 50
    assert item["original_price"] == 5000


def test_yunmeng_cross_count_comes_from_runtime_condition() -> None:
    assert infer_yunmeng_cross_count(
        ["CL|25,EqualCrossGroup|210001_8", "EqualCrossGroup|210001_8,CL|141"]
    ) == 8

    with _session() as session:
        with pytest.raises(ValueError, match="填写 1 跨，运行时为 8 跨"):
            upsert_yunmeng_trial_snapshot(
                session,
                {
                    "cross_count": 1,
                    "start_date": "2026-08-02",
                    "shop_items": [
                        {
                            "goods_id": 8210101,
                            "item_id": 19010068,
                            "show_limit": "EqualCrossGroup|210001_8",
                        }
                    ],
                },
            )
