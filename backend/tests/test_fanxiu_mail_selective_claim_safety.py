import threading

import pytest

from backend.core.fanxiu.behavior_tree.kernel_scheduler import create_behavior_tree_executor
from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeContext
from backend.core.fanxiu.data_annotation.tasks.mail import MailTaskMixin
from backend.core.fanxiu.runtime_gui.mail import MailWindowGeometry
from pyxllib.autogui import View


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_scrolled_mail_title_refines_position_when_ocr_drops_brackets():
    geometry = MailWindowGeometry(
        frame_width=900, frame_height=1600,
        first_center_y=409, second_center_y=599, row_pitch=190,
        title_center_offset=-35,
    )
    point = MailTaskMixin._precise_mail_observed_title_point(
        [{"text": "停服维护补偿", "x": 270, "y": 1020, "w": 260, "h": 42}],
        title="【停服维护补偿】",
        fallback_y=979,
        geometry=geometry,
        max_row_distance_ratio=0.8,
    )
    assert point == (400, 1041)


def _mail(
    mail_id: str,
    status: str,
    *,
    locked: bool = False,
    read: bool | None = None,
) -> dict:
    item = {
        "id": mail_id,
        "execution_status": status,
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
    runner = create_behavior_tree_executor()
    clicks = []
    waits = []
    views = iter((View({"id": 348, "shapes": []}), View({"id": 121, "shapes": []})))

    class Runtime:
        def wait_click(self, scene_id, title, **_kwargs):
            resolved_scene = scene_id.id if isinstance(scene_id, View) else int(scene_id)
            resolved_title = title if isinstance(title, str) else title.title
            clicks.append((resolved_scene, resolved_title))
            if False:
                yield None

        def click_shape(self, scene_id, title, **_kwargs):
            resolved_scene = scene_id.id if isinstance(scene_id, View) else int(scene_id)
            resolved_title = title if isinstance(title, str) else title.title
            clicks.append((resolved_scene, resolved_title))

        def wait_scene(self, layer0, **_kwargs):
            waits.append(tuple(int(value) for value in layer0))
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
    assert waits == [(348,), (121,)]


def test_delete_read_mail_requires_strict_runtime_decrease(monkeypatch):
    runner = create_behavior_tree_executor()
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
    runner = create_behavior_tree_executor()
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
    runner = create_behavior_tree_executor()
    unread = _mail("unread-info", "no_attachment", read=False)
    read = _mail("read-info", "no_attachment", read=True)
    snapshot = {"complete": True, "items": [unread, read]}

    assert set(runner._deletable_runtime_mail_garbage(snapshot)) == {"read-info"}
    assert runner._protected_runtime_mail_ids(snapshot) == set()


def test_delete_read_mail_allows_unread_no_attachment_side_effect(monkeypatch):
    runner = create_behavior_tree_executor()
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
    runner = create_behavior_tree_executor()
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


def test_mail_policy_snapshot_requires_explicit_complete_classification():
    runner = create_behavior_tree_executor()
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

    with pytest.raises(RuntimeError, match="存在未知道具"):
        runner._validate_precise_mail_policy_snapshot(
            {"items": [_attachment_mail("unknown-retained", desired_status="留存", item_name="未知道具99")]},
            reason="终态测试",
            require_all_classified=True,
        )

    always_claim_with_unknown = _attachment_mail(
        "four-ke-with-unknown",
        desired_status="可领",
        action_policy="claim",
        item_name="潜修心得·四刻",
    )
    always_claim_with_unknown["payload"]["mail_rewards"].append(
        {"item_name": "未知道具 #400013004", "count": 1}
    )
    runner._validate_precise_mail_policy_snapshot(
        {"items": [always_claim_with_unknown]},
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


def test_mail_terminal_unknown_requests_deduplicated_engineering_assistance(monkeypatch):
    runner = create_behavior_tree_executor()
    captured = []
    bind = object()
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.mail._db_engine",
        lambda: bind,
    )

    def fake_enqueue(evidence, **kwargs):
        assert kwargs["db_bind"] is bind
        captured.extend(evidence)
        return {
            "queued": True,
            "signature": "unknown-signature",
            "task_id": "codex-task",
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.client.unknown_item_assistance.enqueue_fanxiu_unknown_item_assistance",
        fake_enqueue,
    )
    unknown = _attachment_mail(
        "unknown-retained",
        desired_status="留存",
        item_name="未知道具 #999999",
    )
    unknown["payload"]["mail_rewards"][0].update(
        item_id="999999",
        type=0,
        runtime_name_id=123456,
    )

    with pytest.raises(RuntimeError, match="拒绝领取并拒绝顺延到次日"):
        runner._validate_mail_policy_with_unknown_assistance(
            {"items": [unknown]},
            reason="任务完成复查",
            require_all_classified=True,
        )

    assert captured == [
        {
            "mail_id": "unknown-retained",
            "item_id": "999999",
            "reward_type": 0,
            "item_type": "",
            "item_type_id": None,
            "item_sub_type_id": None,
            "runtime_name_id": 123456,
            "icon": "",
            "use_condition": "",
            "name_source": "",
            "policy_resolution": "",
        }
    ]


def test_mail_terminal_result_requires_claim_and_garbage_zero_contract():
    runner = create_behavior_tree_executor()
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


def test_mail_detail_action_shape_disambiguates_base_list_projection():
    runner = create_behavior_tree_executor()

    class Runtime:
        def shape_score(self, scene_id, title, **_kwargs):
            return {(122, "领取"): 100.0, (123, "删除"): 82.0}[(scene_id, title)]

    assert runner._mail_detail_action_shape_scene(Runtime(), "frame") == 122


def test_mail_detail_action_shape_fails_closed_without_clear_margin():
    runner = create_behavior_tree_executor()

    class Runtime:
        def shape_score(self, scene_id, title, **_kwargs):
            return {(122, "领取"): 96.0, (123, "删除"): 91.0}[(scene_id, title)]

    assert runner._mail_detail_action_shape_scene(Runtime(), "frame") is None


def test_mail_detail_overlay_does_not_confuse_list_bulk_actions(monkeypatch):
    runner = create_behavior_tree_executor()
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda _ctx, frame, candidates=None, **_options: ({"list": None, "claim-detail": 122, "delete-detail": 123}[frame], 100.0),
    )
    ctx = {"asset_tree": [], "images": {}}
    context = BehaviorTreeContext(runner, ctx)

    assert runner._mail_detail_overlay_scene(context, "list") is None
    assert runner._mail_detail_overlay_scene(context, "claim-detail") == 122
    assert runner._mail_detail_overlay_scene(context, "delete-detail") == 123
