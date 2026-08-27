import threading
from pathlib import Path

import pytest

from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from pyxllib.autogui import View


class _StopEvent:
    def is_set(self) -> bool:
        return False

    def wait(self, _seconds: float) -> bool:
        return False


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _mail(
    mail_id: str,
    status: str,
    *,
    locked: bool = False,
    read: bool | None = None,
) -> dict:
    item = {
        "id": mail_id,
        "runtime_status": status,
        "present_in_runtime": True,
        "locked": locked,
    }
    if read is not None:
        item["payload"] = {"runtime": {"read": read}}
    return item


def _attachment_mail(
    mail_id: str,
    *,
    desired_status: str,
    action_policy: str = "",
    item_name: str = "灵石",
    locked: bool = False,
) -> dict:
    return {
        **_mail(mail_id, "unclaimed", locked=locked),
        "has_attachment": True,
        "desired_status": desired_status,
        "action_policy": action_policy,
        "payload": {"mail_rewards": [{"item_name": item_name, "count": 1}]},
    }


def test_delete_read_mail_confirms_prompt_and_returns_to_mail():
    runner = create_behavior_tree_runtime_runner()
    clicks = []
    waits = []
    views = iter((View({"id": 348, "shapes": []}), View({"id": 121, "shapes": []})))

    class Runtime:
        def click_shape(self, scene_id, title, **_kwargs):
            resolved_scene = scene_id.id if isinstance(scene_id, View) else int(scene_id)
            resolved_title = title if isinstance(title, str) else title.title
            clicks.append((resolved_scene, resolved_title))

        def wait_view(self, *_args, **_kwargs):
            waits.append(tuple(int(value) for value in _args))
            if False:
                yield None
            return next(views)

    mail_view = View(
        {
            "id": 121,
            "width": 900,
            "height": 1600,
            "shapes": [
                {"kind": "rect", "title": "一键删除", "x": 0.2, "y": 0.8, "w": 0.2, "h": 0.08}
            ],
        }
    )

    result = _drain(runner._delete_read_mail_once(Runtime(), mail_view, reason="测试"))

    assert result == 121
    assert clicks == [(121, "一键删除"), (348, "确认")]
    assert waits == [(348, 210, 278), (121,)]


def test_delete_read_mail_requires_strict_runtime_decrease(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    snapshot = {"complete": True, "items": [_mail("garbage", "claimed"), _mail("keep", "unclaimed")]}
    monkeypatch.setattr(runner, "_delete_read_mail_once", lambda *_args, **_kwargs: _return_scene(121))
    monkeypatch.setattr(runner, "_read_complete_precise_mail_snapshot", lambda *_args, **_kwargs: snapshot)

    with pytest.raises(RuntimeError, match="没有严格减少"):
        _drain(
            runner._delete_read_mail_until_clean(
                object(), View({"id": 121, "shapes": []}), threading.Event(), reason="测试",
                initial_snapshot=snapshot,
            )
        )


def _return_scene(scene_id):
    if False:
        yield None
    return scene_id


def test_delete_read_mail_cleans_multiple_batches_and_preserves_locked(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    snapshots = iter(
        (
            {"complete": True, "items": [_mail("g2", "no_attachment", read=True), _mail("locked", "claimed", locked=True), _attachment_mail("keep", desired_status="留存")]},
            {"complete": True, "items": [_mail("locked", "claimed", locked=True), _attachment_mail("keep", desired_status="留存")]},
        )
    )
    clicks = []

    def delete(*_args, **_kwargs):
        clicks.append("delete")
        if False:
            yield None
        return 121

    monkeypatch.setattr(runner, "_delete_read_mail_once", delete)
    monkeypatch.setattr(runner, "_read_complete_precise_mail_snapshot", lambda *_args, **_kwargs: next(snapshots))
    initial = {
        "complete": True,
        "items": [
            _mail("g1", "claimed"),
            _mail("g2", "no_attachment", read=True),
            _mail("locked", "claimed", locked=True),
            _attachment_mail("keep", desired_status="留存"),
        ],
    }

    result = _drain(
        runner._delete_read_mail_until_clean(
            object(), View({"id": 121, "shapes": []}), threading.Event(), reason="测试",
            initial_snapshot=initial,
        )
    )

    assert result["before_count"] == 2
    assert result["deleted_count"] == 2
    assert result["after_count"] == 0
    assert result["protected_count"] == 2
    assert clicks == ["delete", "delete"]


def test_unread_no_attachment_mail_is_neither_delete_target_nor_protected() -> None:
    runner = create_behavior_tree_runtime_runner()
    unread = _mail("unread-info", "no_attachment", read=False)
    read = _mail("read-info", "no_attachment", read=True)
    snapshot = {"complete": True, "items": [unread, read]}

    assert set(runner._deletable_runtime_mail_garbage(snapshot)) == {"read-info"}
    assert runner._protected_runtime_mail_ids(snapshot) == set()


def test_delete_read_mail_allows_unread_no_attachment_side_effect(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    initial = {
        "complete": True,
        "items": [
            _mail("garbage", "claimed"),
            _mail("unread-info", "no_attachment", read=False),
            _attachment_mail("keep", desired_status="留存"),
        ],
    }
    after = {
        "complete": True,
        "items": [_attachment_mail("keep", desired_status="留存")],
    }
    monkeypatch.setattr(runner, "_delete_read_mail_once", lambda *_args, **_kwargs: _return_scene(121))
    monkeypatch.setattr(runner, "_read_complete_precise_mail_snapshot", lambda *_args, **_kwargs: after)

    result = _drain(
        runner._delete_read_mail_until_clean(
            object(), View({"id": 121, "shapes": []}), threading.Event(), reason="测试",
            initial_snapshot=initial,
        )
    )

    assert result["deleted_count"] == 1
    assert result["protected_count"] == 1


def test_delete_read_mail_is_idempotent_without_garbage(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    snapshot = {"complete": True, "items": [_mail("locked", "claimed", locked=True), _attachment_mail("keep", desired_status="留存")]}
    monkeypatch.setattr(
        runner,
        "_delete_read_mail_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not click")),
    )

    result = _drain(
        runner._delete_read_mail_until_clean(
            object(), View({"id": 121, "shapes": []}), threading.Event(), reason="测试",
            initial_snapshot=snapshot,
        )
    )

    assert result["before_count"] == result["deleted_count"] == 0
    assert result["protected_count"] == 2


def test_selective_claim_reads_and_plans_before_any_delete(monkeypatch, tmp_path: Path):
    runner = create_behavior_tree_runtime_runner()
    stop_event = _StopEvent()
    image121 = {
        "type": "image",
        "title": "邮件",
        "filename": "0121.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "mail-list",
                "kind": "rect",
                "title": "邮件清单2",
                "x": 0.1,
                "y": 0.2,
                "w": 0.8,
                "h": 0.6,
                "loadDirection": "down",
            },
            {
                "id": "mail-row-1",
                "kind": "rect",
                "title": "第1封",
                "x": 0.1,
                "y": 0.2,
                "w": 0.8,
                "h": 0.1,
            },
            {
                "id": "mail-row-2",
                "kind": "rect",
                "title": "第2封",
                "x": 0.1,
                "y": 0.3,
                "w": 0.8,
                "h": 0.1,
            },
            {
                "id": "back",
                "kind": "point",
                "title": "空白-返回",
                "x": 0.03,
                "y": 0.95,
            },
            {
                "id": "delete-read",
                "kind": "rect",
                "title": "一键删除",
                "x": 0.2,
                "y": 0.8,
                "w": 0.2,
                "h": 0.08,
            },
        ],
    }
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {121: image121},
    }
    runtime = object()
    calls: list[str] = []

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.behavior_tree_runtime.ensure_fanxiu_mail_table",
        lambda: None,
    )
    refresh_calls = []
    monkeypatch.setattr(
        runner,
        "_refresh_runtime_mail_snapshot",
        lambda *_args, **kwargs: refresh_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.mail.current_runtime_mail_sequence_snapshot",
        lambda *_args, **_kwargs: {"complete": True, "items": []},
    )
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    def open_entry(_runtime):
        calls.append("open")
        if False:
            yield None
        return "success"

    def leave_mail(*_args, **_kwargs):
        calls.append("leave")
        if False:
            yield None
        return "success"

    def ordered_batch(*_args, **_kwargs):
        calls.append("dynamic-plan")
        if False:
            yield None
        return {
            "result": "success",
            "claimed_count": 0,
            "garbage_before": 0,
            "garbage_after": 0,
            "deleted_count": 0,
            "protected_count": 0,
        }

    monkeypatch.setattr(runner, "_open_mail_selective_claim_entry", open_entry)
    monkeypatch.setattr(runner, "_leave_mail_scene_to_world", leave_mail)
    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime_scene_text",
        lambda *_args, **_kwargs: (121, 100.0, "frame", "邮件"),
    )
    monkeypatch.setattr(runner, "_mail_detail_overlay_scene", lambda *_args: None)
    monkeypatch.setattr(runner, "_execute_ordered_runtime_claim_batch", ordered_batch)
    monkeypatch.setattr(
        runner,
        "_delete_read_mail_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("读取动态清单并制定领取计划前不得执行一键删除")
        ),
    )

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_mail_selective_claim_task(ctx, stop_event, {}),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert calls == ["open", "leave", "open", "dynamic-plan"]
    assert refresh_calls[0]["force_refresh"] is True


def test_mail_policy_snapshot_requires_explicit_complete_classification():
    runner = create_behavior_tree_runtime_runner()
    runner._validate_precise_mail_policy_snapshot(
        {
            "items": [
                _attachment_mail("claim", desired_status="可领", action_policy="claim"),
                _attachment_mail("retain", desired_status="留存"),
                _attachment_mail("locked", desired_status="锁定", locked=True),
                _attachment_mail("pending-lock", desired_status="锁定", locked=False),
            ]
        },
        reason="测试",
    )

    runner._validate_precise_mail_policy_snapshot(
        {"items": [_attachment_mail("unknown-retained", desired_status="留存", item_name="未知道具99")]},
        reason="测试",
    )

    with pytest.raises(RuntimeError, match="拒绝领取并拒绝顺延到次日"):
        runner._validate_precise_mail_policy_snapshot(
            {
                "items": [
                    _attachment_mail(
                        "unknown-claim",
                        desired_status="可领",
                        action_policy="claim",
                        item_name="未知道具99",
                    )
                ]
            },
            reason="测试",
        )

    with pytest.raises(RuntimeError, match="策略不一致|desired"):
        runner._validate_precise_mail_policy_snapshot(
            {"items": [_attachment_mail("missing-policy", desired_status="可领")]},
            reason="测试",
        )


def test_mail_terminal_result_requires_claim_and_garbage_zero_contract():
    runner = create_behavior_tree_runtime_runner()
    runner._validate_precise_mail_terminal_result(
        {
            "result": "success",
            "claimed_count": 2,
            "garbage_before": 5,
            "deleted_count": 5,
            "garbage_after": 0,
            "protected_count": 7,
        },
        target_count=2,
    )

    with pytest.raises(RuntimeError, match="待领取目标|领取计数"):
        runner._validate_precise_mail_terminal_result(
            {
                "result": "success",
                "claimed_count": 1,
                "garbage_before": 1,
                "deleted_count": 1,
                "garbage_after": 0,
                "protected_count": 7,
            },
            target_count=2,
        )

    with pytest.raises(RuntimeError, match="垃圾未形成归零"):
        runner._validate_precise_mail_terminal_result(
            {
                "result": "success",
                "claimed_count": 2,
                "garbage_before": 5,
                "deleted_count": 4,
                "garbage_after": 1,
                "protected_count": 7,
            },
            target_count=2,
        )


def test_mail_detail_action_ocr_can_override_one_false_template_match(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image122 = {
        "type": "image",
        "number": 122,
        "filename": "0122.png",
        "title": "可领取邮件",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "claim",
                "kind": "rect",
                "title": "领取",
                "x": 0.4,
                "y": 0.8,
                "w": 0.2,
                "h": 0.08,
            }
        ],
    }
    image123 = {
        "type": "image",
        "number": 123,
        "title": "不可领取邮件",
        "width": 900,
        "height": 1600,
        "shapes": [],
    }

    class _Runtime:
        def cur_frame(self, *, update=False):
            assert update
            return "frame"

        def current_scene(self, *_args, **_kwargs):
            return 123, 100.0, "frame"

        def view(self, scene_id):
            return View(image122 if scene_id == 122 else image123)

        def ocr_fragments(self, _frame):
            return [
                {"text": "参与击杀魔祖未取奖励", "x": 100, "y": 100, "w": 300, "h": 40},
                {"text": "领取", "x": 360, "y": 1280, "w": 180, "h": 60},
            ]

        def wait_action_settle(self, _seconds):
            if False:
                yield None
            return "success"

    detail_view, action_point = runner._run_direct_runtime_action(
        lambda: runner._wait_precise_mail_detail(
            _Runtime(),
            "参与击杀魔祖未取奖励",
            timeout=1.0,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert detail_view.raw["number"] == 122
    assert action_point == pytest.approx((450.0, 1344.0))


def test_mail_detail_uses_stable_detail_subgraph_when_combined_scene_is_unknown(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image122 = {
        "type": "image",
        "number": 122,
        "filename": "0122.png",
        "title": "可领取邮件",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "claim",
                "kind": "rect",
                "title": "领取",
                "x": 0.4,
                "y": 0.8,
                "w": 0.2,
                "h": 0.08,
            }
        ],
    }

    class _Runtime:
        ctx = {"images": {122: image122}}

        def cur_frame(self, *, update=False):
            assert update
            return "detail-frame"

        def current_scene(self, *_args, **_kwargs):
            return None, 0.0, "detail-frame"

        def view(self, scene_id):
            assert scene_id == 122
            return View(image122)

        def ocr_fragments(self, _frame):
            return []

        def wait_action_settle(self, _seconds):
            if False:
                yield None
            return "success"

    monkeypatch.setattr(
        runner,
        "_mail_detail_overlay_scene",
        lambda _ctx, frame: 122 if frame == "detail-frame" else None,
    )

    detail_view, action_point = runner._run_direct_runtime_action(
        lambda: runner._wait_precise_mail_detail(
            _Runtime(),
            "宗门镇邪活动奖励",
            timeout=2.0,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert detail_view.raw["number"] == 122
    assert action_point is None


def test_mail_detail_action_shape_disambiguates_base_list_projection():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def shape_score(self, scene_id, title, **_kwargs):
            return {(122, "领取"): 100.0, (123, "删除"): 82.0}[(scene_id, title)]

    assert runner._mail_detail_action_shape_scene(Runtime(), "frame") == 122


def test_mail_detail_action_shape_fails_closed_without_clear_margin():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        def shape_score(self, scene_id, title, **_kwargs):
            return {(122, "领取"): 96.0, (123, "删除"): 91.0}[(scene_id, title)]

    assert runner._mail_detail_action_shape_scene(Runtime(), "frame") is None


def test_mail_detail_action_shape_requires_two_stable_reads(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    image122 = {
        "type": "image",
        "number": 122,
        "title": "可领取邮件",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "id": "claim",
                "kind": "rect",
                "title": "领取",
                "x": 0.4,
                "y": 0.8,
                "w": 0.2,
                "h": 0.08,
            }
        ],
    }

    class Runtime:
        ctx = {"images": {122: image122}}

        def __init__(self):
            self.frame_reads = 0

        def cur_frame(self, *, update=False):
            assert update
            self.frame_reads += 1
            return f"frame-{self.frame_reads}"

        def current_scene(self, *_args, **_kwargs):
            return 121, 100.0, "frame"

        def shape_score(self, scene_id, title, **_kwargs):
            return {(122, "领取"): 100.0, (123, "删除"): 82.0}[(scene_id, title)]

        def view(self, scene_id):
            assert scene_id == 122
            return View(image122)

        def ocr_fragments(self, _frame):
            return []

        def wait_action_settle(self, _seconds):
            if False:
                yield None
            return "success"

    runtime = Runtime()
    monkeypatch.setattr(runner, "_mail_detail_overlay_scene", lambda *_args: None)
    detail_view, action_point = runner._run_direct_runtime_action(
        lambda: runner._wait_precise_mail_detail(
            runtime,
            "异火每日奖励补发",
            timeout=2.0,
        ),
        stop_event=threading.Event(),
        tick_seconds=0.01,
    )

    assert detail_view.raw["number"] == 122
    assert action_point is None
    assert runtime.frame_reads >= 2


def test_mail_detail_overlay_does_not_confuse_list_bulk_actions(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, candidates: ({"list": None, "claim-detail": 122, "delete-detail": 123}[frame], 100.0),
    )
    ctx = {"asset_tree": [], "images": {}}

    assert runner._mail_detail_overlay_scene(ctx, "list") is None
    assert runner._mail_detail_overlay_scene(ctx, "claim-detail") == 122
    assert runner._mail_detail_overlay_scene(ctx, "delete-detail") == 123
