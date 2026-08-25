from backend.core.fanxiu.instrumentation import landcontend
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)

import pytest


class _Reader:
    def __init__(self, values):
        self.values = values

    def fields(self, value):
        return self.values.get(value, {})

    def long(self, value):
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def list_items(self, value):
        items = self.values.get(("list", value), [])
        return list(items), len(items)


def _reader_values(*, score=15_000, skip=True, triple=False):
    return {
        "instance": {"Model": "model"},
        "model": {"LandcontendData": "data"},
        "data": {
            "V_PlayerRankData": "rank-data",
            "_IsPassFight": skip,
            "_MultiFightState": triple,
            "_MultiFightCount": 3,
            "_CurScheduleStage": 2,
        },
        "rank-data": {"selfRank": "self-rank"},
        "self-rank": {"score": score},
    }


def test_landcontend_snapshot_reads_score_and_toggle_truth_without_ocr(monkeypatch):
    reader = _Reader(_reader_values())
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_attack_options(reader, 123)

    assert result == {
        "score": 15_000,
        "stage": 2,
        "skip_checked": True,
        "triple_checked": False,
        "triple_count": 3,
    }


def test_landcontend_snapshot_fails_closed_when_toggle_state_is_not_loaded(monkeypatch):
    values = _reader_values()
    values["data"]["_MultiFightState"] = None
    reader = _Reader(values)
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match=r"三连状态\s+不是有效布尔值"):
        landcontend._landcontend_attack_options(reader, 123)


def test_landcontend_attack_options_allow_lazy_multi_count(monkeypatch):
    values = _reader_values()
    values["data"].pop("_MultiFightCount")
    reader = _Reader(values)
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_attack_options(reader, 123)

    assert result["triple_count"] is None


def test_landcontend_count_state_reads_attack_and_clone_counts(monkeypatch):
    values = {
        "instance": {"Model": "model"},
        "model": {"LandcontendData": "data"},
        "data": {"countInfoDic": "counts"},
        "counts": {"LuaDic_count": 2},
        ("list", "counts"): ["clone", "attack"],
        "attack": {"type": 1, "count": 7, "recoverTime": 1001},
        "clone": {"type": 2, "count": 8, "recoverTime": 1002},
    }
    reader = _Reader(values)
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_count_state(reader, 123)

    assert result == {
        "attack_count": 7,
        "clone_count": 8,
        "counts": [
            {"type": 1, "count": 7, "recover_time": 1001},
            {"type": 2, "count": 8, "recover_time": 1002},
        ],
    }


def test_landcontend_public_snapshot_is_read_only_runtime_memory(monkeypatch):
    memory = type(
        "Memory",
        (),
        {"pid": 12, "process_start_ticks": 34},
    )()
    monkeypatch.setattr(
        landcontend,
        "read_runtime_snapshot_with_rebind",
        lambda reader: reader(memory, False),
    )
    monkeypatch.setattr(landcontend, "LuaJitReader", lambda _memory: object())
    monkeypatch.setattr(
        landcontend,
        "resolve_manager_root",
        lambda *_args, **_kwargs: (0x1234, True),
    )
    monkeypatch.setattr(
        landcontend,
        "_landcontend_attack_options",
        lambda _reader, _root: {
            "score": 1_000,
            "stage": 1,
            "skip_checked": True,
            "triple_checked": False,
            "triple_count": 3,
        },
    )

    result = landcontend.read_landcontend_attack_options_snapshot()

    assert result["source"] == "runtime_memory"
    assert result["probe_type"] == "legacy-memory-scan"
    assert result["complete"] is True
    assert result["evidence"] == {
        "pid": 12,
        "process_start_ticks": 34,
        "root_address": "0x1234",
        "root_cache_hit": True,
        "manager_resolver": "constructor_marker",
    }


def test_landcontend_immunity_parser_reads_runtime_markup_without_ocr():
    payload = (
        b"stale\x00"
        + "<color=#ff5b40>免战:00:12:34</color>".encode("utf-8")
        + b"\x00other\x00"
        + "免战：01：02：03".encode("utf-8")
    )

    result = landcontend._immunity_countdowns(payload)

    assert sorted(result.values()) == [754, 3723]


def test_landcontend_immunity_active_sample_ignores_immutable_stale_strings():
    before = {100: 983, 200: 1584, 300: 240}
    after = {100: 983, 200: 1584, 300: 240, 400: 238, 500: 1582}

    result = landcontend._active_immunity_seconds(
        before,
        after,
        elapsed_seconds=2.2,
    )

    assert result == 238


def test_landcontend_immunity_active_sample_fails_closed_without_new_value():
    assert (
        landcontend._active_immunity_seconds(
            {100: 240},
            {100: 240},
            elapsed_seconds=2.0,
        )
        is None
    )


def test_landcontend_command_target_joins_command_vo_to_camp_id(monkeypatch):
    values = {
        "instance": {"Model": "model", "_CurFocusCampId": 0},
        "model": {"LandcontendData": "data"},
        "data": {
            "_CurScheduleStage": 2,
            "_HasShowCommand": True,
            "v_campList": "camps",
            "_SceneRankList": "scene-ranks",
        },
        ("list", "camps"): ["camp-a", "camp-b"],
        "camp-a": {"id": 101, "name": "甲盟", "serverId": 11, "pillarCurHp": 80, "pillarMaxHp": 100},
        "camp-b": {"id": 202, "name": "乙盟", "serverId": 22, "pillarCurHp": 20, "pillarMaxHp": 100},
        ("list", "scene-ranks"): ["command"],
        "command": {
            "id": 202,
            "name": "乙盟",
            "commandState": 1,
            "desc": "全力进攻这里",
        },
    }
    reader = _Reader(values)
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_command_target(reader, 123)

    assert result["command_count"] == 1
    assert result["target"] == {
        "id": 202,
        "name": "乙盟",
        "slot": 2,
        "server_id": 22,
        "pillar_cur_hp": 20.0,
        "pillar_max_hp": 100.0,
        "protect_end_time": None,
        "has_super_mirror_hp_protect": False,
        "has_xiaoyan_mirror": False,
        "pivot_name": "",
        "ally_camp_id": None,
        "command_state": 1,
        "command_desc": "全力进攻这里",
    }


def test_landcontend_immunity_state_reads_focused_camp_deadline(monkeypatch):
    values = {
        "instance": {"Model": "model", "_CurFocusCampId": 202},
        "model": {"LandcontendData": "data"},
        "data": {"v_campList": "camps"},
        ("list", "camps"): ["camp-a", "camp-b"],
        "camp-a": {"id": 101, "name": "甲盟", "protectEndTime": 0},
        "camp-b": {"id": 202, "name": "乙盟", "protectEndTime": 1_700_001_234_567},
    }
    reader = _Reader(values)
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_immunity_state(
        reader,
        123,
        now_ms=1_700_000_000_000,
    )

    assert result == {
        "target_id": 202,
        "target_name": "乙盟",
        "protect_end_time": 1_700_001_234_567,
        "cooldown_seconds": 1235,
        "ready": False,
    }


def test_landcontend_command_target_does_not_require_optional_has_show_command(monkeypatch):
    values = {
        "instance": {"Model": "model"},
        "model": {"LandcontendData": "data"},
        "data": {
            "_CurScheduleStage": 1,
            "v_campList": "camps",
            "_SceneRankList": "scene-ranks",
        },
        ("list", "camps"): ["camp"],
        "camp": {"id": 501, "name": "目标盟"},
        ("list", "scene-ranks"): ["command"],
        "command": {
            "id": 501,
            "commandState": 1,
            "desc": "全力进攻这里",
        },
    }
    reader = _Reader(values)
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_command_target(reader, 123)

    assert result["has_command"] is None
    assert result["command_count"] == 1
    assert result["target"]["id"] == 501


def test_landcontend_command_target_reads_attack_dictionary_from_own_camp(monkeypatch):
    values = {
        "instance": {"Model": "model"},
        "model": {"LandcontendData": "data"},
        "data": {
            "_CurScheduleStage": 1,
            "v_campList": "camps",
            "_SceneRankList": "scene-ranks",
        },
        ("list", "camps"): ["own", "enemy"],
        "own": {
            "id": 101,
            "name": "我方",
            "attackClubs": "attack-dict",
        },
        "enemy": {"id": 202, "name": "敌方"},
        ("dict", "attack-dict"): {202: "全力进攻这里"},
        ("list", "scene-ranks"): [],
    }
    reader = _Reader(values)
    reader.dictionary_fields = lambda value: values.get(("dict", value), {})
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_command_target(
        reader,
        123,
        self_club_id=101,
    )

    assert result["command_count"] == 1
    assert result["target"]["id"] == 202
    assert result["target"]["slot"] == 2
    assert result["target"]["command_desc"] == "全力进攻这里"


def test_landcontend_command_target_refuses_ambiguous_commands(monkeypatch):
    values = {
        "instance": {"Model": "model"},
        "model": {"LandcontendData": "data"},
        "data": {
            "_CurScheduleStage": 2,
            "_HasShowCommand": True,
            "v_campList": "camps",
            "_SceneRankList": "scene-ranks",
        },
        ("list", "camps"): ["camp-a", "camp-b"],
        "camp-a": {"id": 101, "name": "甲盟", "commandState": 1},
        "camp-b": {"id": 202, "name": "乙盟", "commandState": 1},
        ("list", "scene-ranks"): [],
    }
    reader = _Reader(values)
    monkeypatch.setattr(
        landcontend,
        "manager_index_fields",
        lambda _reader, _root, _methods: {"inst": "instance"},
    )

    result = landcontend._landcontend_command_target(reader, 123)

    assert result["command_count"] == 2
    assert result["target"] is None
