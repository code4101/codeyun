from sqlmodel import SQLModel, Session, create_engine

from backend.core.fanxiu_region_data import (
    build_region_character_history_snapshot,
    build_region_character_snapshot,
    build_region_data_snapshot,
    create_region_character_record,
    create_region_character_record_if_stronger,
    disable_region_character_record,
)


def make_session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_region_data_seed_contains_known_regions_and_marks() -> None:
    with make_session() as session:
        snapshot = build_region_data_snapshot(session)

    assert len(snapshot["regions"]) == 24
    tianlan = next(item for item in snapshot["regions"] if item["name"] == "天澜圣殿")
    assert tianlan["number"] == 17
    assert tianlan["start_date"] == "2025-02-27"
    assert tianlan["end_date"] == "2025-05-01"
    assert tianlan["known_count"] == 64
    assert any(
        server["name"] == "岁序更替" and server["mark_type"] == "current"
        for server in tianlan["servers"]
    )


def test_character_records_append_history_and_current_view_uses_latest_enabled() -> None:
    with make_session() as session:
        base_item = {
            "region_name": "天澜圣殿",
            "server_name": "岁序更替",
            "guild_name": "三清道宗",
            "role_name": "玉清ღ清锋",
            "attack": "50.8兆",
            "cultivation_level": "合体中期4层",
            "recorded_date": "2026-04-27",
        }

        first = create_region_character_record(session, base_item)
        second = create_region_character_record(
            session,
            {
                **base_item,
                "attack": "60兆",
                "cultivation_level": "合体中期5层",
                "recorded_date": "2026-04-28",
            },
        )

        current = build_region_character_snapshot(session)["characters"]
        assert len(current) == 1
        assert current[0]["id"] == second.id
        assert current[0]["attack"] == "60兆"
        assert current[0]["cultivation_level"] == "合体中期5层"

        disabled = disable_region_character_record(session, second.id)
        assert disabled.disabled is True

        current_after_disable = build_region_character_snapshot(session)["characters"]
        assert len(current_after_disable) == 1
        assert current_after_disable[0]["id"] == first.id

        history = build_region_character_history_snapshot(session, role_name="玉清ღ清锋")["characters"]
        assert [item["id"] for item in history] == [first.id, second.id]
        assert history[1]["disabled"] is True


def test_character_ocr_record_only_inserts_when_attack_increases() -> None:
    with make_session() as session:
        base_item = {
            "region_name": "天澜圣殿",
            "server_name": "金相玉质",
            "guild_name": "三清道宗",
            "role_name": "测试ღ问道",
            "attack": "50.2万京",
            "cultivation_level": "大乘后期6层",
            "recorded_date": "2026-04-27",
        }

        first, first_created = create_region_character_record_if_stronger(session, base_item)
        lower, lower_created = create_region_character_record_if_stronger(
            session,
            {
                **base_item,
                "attack": "49.9万京",
                "recorded_date": "2026-04-28",
            },
        )
        equal, equal_created = create_region_character_record_if_stronger(
            session,
            {
                **base_item,
                "recorded_date": "2026-04-29",
            },
        )
        higher, higher_created = create_region_character_record_if_stronger(
            session,
            {
                **base_item,
                "attack": "50.3万京",
                "cultivation_level": "大乘后期7层",
                "recorded_date": "2026-04-30",
            },
        )

        assert first_created is True
        assert lower_created is False
        assert lower.id == first.id
        assert equal_created is False
        assert equal.id == first.id
        assert higher_created is True
        assert higher.id != first.id

        history = build_region_character_history_snapshot(session, role_name="测试ღ问道")["characters"]
        assert [(item["id"], item["attack"], item["recorded_date"]) for item in history] == [
            (first.id, "50.2万京", "2026-04-27"),
            (higher.id, "50.3万京", "2026-04-30"),
        ]

        current = [
            item
            for item in build_region_character_snapshot(session)["characters"]
            if item["role_name"] == "测试ღ问道"
        ]
        assert len(current) == 1
        assert current[0]["id"] == higher.id
        assert current[0]["attack"] == "50.3万京"
        assert current[0]["cultivation_level"] == "大乘后期7层"


def test_character_current_view_allows_missing_guild() -> None:
    with make_session() as session:
        base_item = {
            "region_name": "天澜圣殿",
            "server_name": "金相玉质",
            "guild_name": "",
            "role_name": "上清ღ半跪",
            "attack": "60京",
            "cultivation_level": "大乘后期6层",
            "recorded_date": "2026-04-27",
        }

        first, first_created = create_region_character_record_if_stronger(session, base_item)
        lower, lower_created = create_region_character_record_if_stronger(
            session,
            {
                **base_item,
                "attack": "59京",
                "recorded_date": "2026-04-28",
            },
        )
        higher, higher_created = create_region_character_record_if_stronger(
            session,
            {
                **base_item,
                "attack": "61京",
                "recorded_date": "2026-04-29",
            },
        )

        assert first_created is True
        assert lower_created is False
        assert lower.id == first.id
        assert higher_created is True

        current = [
            item
            for item in build_region_character_snapshot(session)["characters"]
            if item["role_name"] == "上清ღ半跪"
        ]
        assert len(current) == 1
        assert current[0]["guild_name"] == ""
        assert current[0]["id"] == higher.id
        assert current[0]["attack"] == "61京"
        assert current[0]["cultivation_level"] == "大乘后期6层"
