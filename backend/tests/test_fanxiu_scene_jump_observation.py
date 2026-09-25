"""A transient recognition must not become a persisted navigation edge."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import threading

import pytest

from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_executor


@pytest.mark.parametrize(
    ("observed", "landing", "targets"),
    [
        ([279, 121, 121], 121, "121"),
        ([279, 279], 279, "121"),
        ([279, 121], 121, "279(1),121(20)"),
        ([279, 279], 279, "279(1),121(20)"),
    ],
)
def test_uncertain_landing_needs_fresh_frame_before_recording(
    monkeypatch, tmp_path, observed, landing, targets,
):
    runner = create_behavior_tree_executor()
    frames = iter(observed)
    recorded = []

    class Context:
        frame_data_url = ""

        def wait_scene(self, _scenes, **_kwargs):
            scene_id = next(frames)
            if False:
                yield None
            return SimpleNamespace(
                scene_id=scene_id, score=100.0, frame_data_url=f"frame-{scene_id}",
            )

    def settle(*_args, **_kwargs):
        if False:
            yield None

    monkeypatch.setattr(runner, "_behavior_tree_context", lambda *_args, **_kwargs: Context())
    monkeypatch.setattr(runner, "_scene_observation_probe", lambda _ctx: nullcontext())
    monkeypatch.setattr(runner, "_wait_action_settle", settle)
    monkeypatch.setattr(runner, "_scene_jump_preferred_wait_seconds", lambda **_kwargs: 0.0)
    monkeypatch.setattr(runner, "_commit_scene_observation", lambda *_args: None)
    monkeypatch.setattr(runner, "_record_scene_jump_landing", lambda *args, **_kwargs: recorded.append(args[4]))
    monkeypatch.setattr(runner, "_find_scene_route", lambda *_args: None)

    iterator = runner._wait_scene_jump_result(
        {"images": {}}, tmp_path / "asset-tree.json", [],
        source_scene_id=68, target_scene_id=121,
        edge={"shape": {"id": "mail", "title": "邮件", "sceneJumpTarget": targets},
              "target_ids": [121, 279] if "279" in targets else [121]},
        stop_event=threading.Event(),
    )
    with pytest.raises(StopIteration) as result:
        next(iterator)

    assert result.value.value == landing
    assert recorded == [landing]
