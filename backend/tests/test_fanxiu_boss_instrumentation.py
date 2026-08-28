from __future__ import annotations

import threading
from pathlib import Path

from backend.core.fanxiu.instrumentation import boss
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
)


def test_boss_root_prefers_lua_global(monkeypatch):
    memory = object()
    monkeypatch.setattr(
        boss,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        boss,
        "resolve_lua_global_manager_root",
        lambda *_args, **kwargs: (
            0x5678,
            True,
            0x9999,
        )
        if kwargs["global_name"] == "BossMgr"
        else (_ for _ in ()).throw(AssertionError("wrong global")),
    )
    monkeypatch.setattr(
        boss,
        "resolve_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("全局快路径成功后不应扫描构造器 marker")
        ),
    )

    assert boss._resolve_boss_root(memory) == (
        0x5678,
        True,
        "lua_global",
    )


def test_boss_root_falls_back_to_constructor_marker(monkeypatch):
    memory = object()
    monkeypatch.setattr(
        boss,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        boss,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("全局未找到", code="manager_not_found")
        ),
    )
    monkeypatch.setattr(
        boss,
        "resolve_manager_root",
        lambda *_args, **_kwargs: (0x6789, False),
    )

    assert boss._resolve_boss_root(memory) == (
        0x6789,
        False,
        "constructor_marker",
    )


def test_boss_root_does_not_heap_scan_when_global_data_not_loaded(
    monkeypatch,
):
    memory = object()
    monkeypatch.setattr(
        boss,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        boss,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError(
                "BossData 尚未初始化",
                code="data_not_loaded",
            )
        ),
    )
    monkeypatch.setattr(
        boss,
        "resolve_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("已定位全局但数据未加载时不应慢扫")
        ),
    )

    try:
        boss._resolve_boss_root(memory)
    except FanxiuRuntimeMemoryError as exc:
        assert exc.code == "data_not_loaded"
    else:
        raise AssertionError("应保留精确的 data_not_loaded 失败")
from backend.core.fanxiu.behavior_tree.runtime import (
    create_behavior_tree_runtime_runner,
)


def test_boss_snapshot_reads_reward_count_and_refresh_time(
    monkeypatch,
):
    boss_list = LuaRef("table", 0x1000)
    big_boss = LuaRef("table", 0x1100)
    next_refresh = LuaRef("table", 0x1200)
    monkeypatch.setattr(
        boss,
        "_boss_data_fields",
        lambda _reader, _root: {
            "bossInfoVOS": boss_list,
            "fatigue": 2.0,
            "bigBossRecRewardTimes": 1.0,
            "bigBossRecRewardTimesKill": 0.0,
            "bigBossInfoVo": big_boss,
        },
    )

    def fake_fields(_reader, value):
        if value == boss_list:
            return {"count": 8.0}
        if value == big_boss:
            return {
                "isDead": True,
                "bossGroupId": 2004.0,
                "bossId": 1104005.0,
                "bigBossNextRefreshTime": next_refresh,
            }
        return {}

    monkeypatch.setattr(LuaJitReader, "fields", fake_fields)
    monkeypatch.setattr(
        LuaJitReader,
        "long",
        lambda _reader, value: (
            1_700_000_010_000
            if value == next_refresh
            else None
        ),
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = boss._snapshot(
        memory,
        0x2000,
        root_cache_hit=False,
        now_epoch_ms=1_700_000_000_000,
    )

    assert result["ok"] is True
    assert result["list_loaded"] is True
    assert result["boss_list_count"] == 8
    assert result["reward_remaining"] == 2
    assert result["big_boss_reward_remaining"] == 1
    assert result["kill_reward_remaining"] == 0
    assert result["big_boss_dead"] is True
    assert result["refresh_remaining_seconds"] == 10


def test_daily_boss_next_time_prefers_runtime_refresh(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    rechecks: list[int] = []
    monkeypatch.setattr(
        runner,
        "_daily_boss_reward_remaining_from_scene",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("Runtime 完整时不应读取 OCR")
            )
        ),
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_boss_recheck_time",
        lambda _payload, *, seconds: (
            rechecks.append(seconds)
            or "2026-07-30 05:04:20"
        ),
    )
    payload = {
        "__daily_boss_runtime_snapshot_override": {
            "complete": True,
            "list_loaded": True,
            "reward_remaining": 2,
            "big_boss_dead": True,
            "refresh_remaining_seconds": 250,
        }
    }

    next_time, source = (
        runner._record_daily_boss_next_time_from_current_list(
            {"images": {178: {}}},
            payload,
        )
    )

    assert next_time == "2026-07-30 05:04:20"
    assert source == "按 Runtime 大首领刷新时间读取 250 秒"
    assert rechecks == [260]


def test_daily_boss_refresh_cd_uses_scene_178_floating_item(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    watched_item = object()

    class FakeRuntime:
        def cur_frame(self, *, update):
            assert update is True
            return "frame"

        def find_floating_item_by_anchor(
            self,
            scene_id,
            item_template,
            anchor_field,
            *,
            container_shape,
            frame_data_url,
        ):
            assert scene_id == 178
            assert item_template == "条目"
            assert anchor_field == "注视中"
            assert container_shape == "首领列表"
            assert frame_data_url == "frame"
            return watched_item

        def read_floating_item_field(
            self,
            item,
            field,
            *,
            frame_data_url,
            padding,
        ):
            assert item is watched_item
            assert field == "刷新时间"
            assert frame_data_url == "frame"
            assert padding == 12
            return "刷新时间00:08:48首"

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            raise AssertionError("#178 条目已读到 CD 时不应扫描整个列表")

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: FakeRuntime(),
    )

    seconds, source = runner._daily_boss_refresh_cd_from_list(
        {
            "asset_tree_path": Path("asset-tree.json"),
            "images": {178: {}},
        }
    )

    assert seconds == 528
    assert source == "刷新时间00:08:48首"


def test_daily_boss_zero_runtime_count_does_not_need_ocr(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()

    class FakeRuntime:
        def __getattr__(self, name):
            raise AssertionError(
                f"Runtime 完整且为 0 时不应调用 GUI/OCR：{name}"
            )

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: FakeRuntime(),
    )
    monkeypatch.setattr(
        runner,
        "_find_shape",
        lambda _image, name: (
            object() if name == "首领列表" else None
        ),
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_boss_done_for_today",
        lambda _payload: "2026-07-31 05:00:00",
    )
    payload = {
        "__daily_boss_runtime_snapshot_override": {
            "complete": True,
            "list_loaded": True,
            "reward_remaining": 0,
        }
    }
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {178: {"shapes": []}},
    }
    result = runner._open_watched_daily_boss_detail(
        ctx,
        threading.Event(),
        payload,
    )

    try:
        while True:
            next(result)
    except StopIteration as stopped:
        assert stopped.value == "done"


def test_daily_boss_done_uses_post_battle_next_time_probe(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    probes: list[dict[str, object]] = []
    returned: list[bool] = []

    def fake_next_time(_ctx, _stop_event, payload):
        probes.append(dict(payload))
        if False:
            yield
        return (
            "2026-07-31 05:00:00",
            "战后 Runtime 剩余奖励次数为 0，奖励次数已用尽",
            True,
        )

    def fake_return(_ctx, _stop_event, **_kwargs):
        returned.append(True)
        if False:
            yield
        return "success"

    monkeypatch.setattr(
        runner,
        "_record_daily_boss_next_time_after_done",
        fake_next_time,
    )
    monkeypatch.setattr(
        runner,
        "_return_daily_boss_to_world",
        fake_return,
    )

    result = runner._finish_daily_boss_round_after_done(
        {},
        object(),
        threading.Event(),
        {"_daily_boss_challenge_remaining": 1},
    )

    try:
        while True:
            next(result)
    except StopIteration as stopped:
        assert stopped.value == "success"

    assert probes == [{"_daily_boss_challenge_remaining": 1}]
    assert returned == [True]
    assert runner.status()["message"] == (
        "日常_首领：本轮挑战已结束；"
        "战后 Runtime 剩余奖励次数为 0，奖励次数已用尽；"
        "下次 2026-07-31 05:00:00"
    )


def test_daily_boss_done_keeps_business_success_when_world_cleanup_fails(monkeypatch):
    runner = create_behavior_tree_runtime_runner()

    def fake_next_time(_ctx, _stop_event, _payload):
        if False:
            yield
        return (
            "2026-07-31 05:00:00",
            "战后 Runtime 剩余奖励次数为 0，奖励次数已用尽",
            True,
        )

    def failing_return(_ctx, _stop_event):
        if False:
            yield
        raise RuntimeError("战斗转场尚未稳定")

    monkeypatch.setattr(runner, "_record_daily_boss_next_time_after_done", fake_next_time)
    monkeypatch.setattr(runner, "_return_daily_boss_to_world", failing_return)

    result = runner._finish_daily_boss_round_after_done(
        {}, object(), threading.Event(), {"_daily_boss_challenge_remaining": 1}
    )

    try:
        while True:
            next(result)
    except StopIteration as stopped:
        assert stopped.value == "success"

    assert any(
        "业务已完成" in item["message"] and "避免重复挑战" in item["message"]
        for item in runner.status()["logs"]
    )


def test_boss_ranking_known_scene_does_not_borrow_unknown_return_shape(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    clicked: list[tuple[float, float]] = []
    image424 = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "return",
                "kind": "shape",
                "title": "返回",
                "x": 0.05555555555555555,
                "y": 0.9385416666666667,
                "w": 0.07222222222222222,
                "h": 0.038541666666666585,
            }
        ],
    }
    monkeypatch.setattr(
        runner,
        "_click_frame_point",
        lambda _ctx, _image, x, y, **_kwargs: clicked.append((x, y)),
    )
    monkeypatch.setattr(
        runner,
        "_save_action_trace",
        lambda *_args, **_kwargs: None,
    )

    result = runner._try_navigation_fallback_return(
        {"images": {424: image424}},
        "frame",
        navigation_state_key="scene:384",
        target_scene_id=34,
        attempted_actions={},
        current_scene_id=384,
    )

    assert result.status == "unavailable"
    assert clicked == []


def test_daily_boss_done_rechecks_when_post_battle_runtime_still_has_reward(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    rechecks: list[int] = []

    class FakeRuntime:
        def get_view(self, _scene_id):
            return None

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: FakeRuntime(),
    )
    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime_scene_text",
        lambda *_args, **_kwargs: (186, 100.0, "frame", ""),
    )
    monkeypatch.setattr(
        runner,
        "_daily_boss_runtime_snapshot",
        lambda _payload: {
            "complete": True,
            "reward_remaining": 1,
        },
    )
    monkeypatch.setattr(
        runner,
        "_record_daily_boss_recheck_time",
        lambda _payload, *, seconds: (
            rechecks.append(seconds)
            or "2026-07-30 07:52:00"
        ),
    )
    result = runner._record_daily_boss_next_time_after_done(
        {"asset_tree_path": Path("asset-tree.json")},
        threading.Event(),
        {"_daily_boss_challenge_remaining": 1},
    )

    try:
        while True:
            next(result)
    except StopIteration as stopped:
        assert stopped.value == (
            "2026-07-30 07:52:00",
            "战后 Runtime 仍有 1 次奖励，60 秒后复核首领/CD",
            False,
        )

    assert rechecks == [60]
