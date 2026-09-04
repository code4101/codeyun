from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.magic_invasion import _wait_scene


def test_magic_invasion_wait_delegates_to_interruption_aware_scene_pipeline() -> None:
    calls: list[tuple[list[int], dict[str, object]]] = []

    class Context:
        def wait_scene(self, scenes: list[int], **options: object):
            calls.append((scenes, options))
            yield "tick"
            return SimpleNamespace(
                scene_id=513,
                score=97.5,
                frame_data_url="frame-after-popup",
            )

        def sample_scene_once(self, *_args: object, **_kwargs: object):
            raise AssertionError("magic invasion must not bypass popup handling")

    iterator = _wait_scene(Context(), (513,), timeout_seconds=12.0)

    assert next(iterator) == "tick"
    try:
        next(iterator)
    except StopIteration as stop:
        result = stop.value
    else:
        raise AssertionError("expected the scene wait generator to finish")

    assert result == (513, 97.5, "frame-after-popup")
    assert calls == [
        (
            [513],
            {"wait": 12.0, "label": "魔道入侵：等待场景"},
        )
    ]
