from __future__ import annotations

from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.magic_invasion_auto_config import (
    desired_magic_invasion_auto_choices,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_auto_configurator import (
    MAGIC_INVASION_AUTO_SHAPE_BY_KEY,
    MAGIC_INVASION_QUALITY_SCROLL_SHAPE,
    MAGIC_INVASION_START_AUTO_SHAPE,
    configure_magic_invasion_auto_options,
    expected_magic_invasion_auto_shape_titles,
)


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _View:
    def __init__(self, context) -> None:
        self.context = context

    def get_shape(self, title):
        self.context.events.append(("get_shape", title))
        return title if title in self.context.shapes else None


class _Context:
    def __init__(self, choices, *, shapes=None, mutate=True) -> None:
        self.choices = choices
        self.shapes = set(shapes or expected_magic_invasion_auto_shape_titles())
        self.mutate = mutate
        self.page = 2
        self.events = []
        self.key_by_shape = {
            shape: key for key, shape in MAGIC_INVASION_AUTO_SHAPE_BY_KEY.items()
        }
        keys = list(MAGIC_INVASION_AUTO_SHAPE_BY_KEY)
        quality_keys = keys[:-4]
        last_quality_page = (len(quality_keys) - 1) // 3
        self.page_by_shape = {
            MAGIC_INVASION_AUTO_SHAPE_BY_KEY[key]: index // 3
            for index, key in enumerate(quality_keys)
        }
        self.last_quality_page = last_quality_page

    def view(self, scene_id):
        assert scene_id == 698
        return _View(self)

    def cur_frame(self, *, update=False):
        assert update is True
        return f"page:{self.page}"

    def shape_matches(self, scene_id, title, *, frame_data_url):
        assert scene_id == 698
        assert frame_data_url == f"page:{self.page}"
        return {"matched": True} if self.page_by_shape.get(title) == self.page else None

    def click_shape_center(self, scene_id, title):
        assert scene_id == 698
        self.events.append(("click", title))
        if self.mutate:
            key = self.key_by_shape[title]
            self.choices[key] = not self.choices[key]

    def wait_action_settle(self, seconds):
        self.events.append(("settle", seconds))
        if False:
            yield None

    def scroll_shape_content(
        self,
        scene_id,
        shape,
        *,
        direction,
        unchanged_confirmations,
    ):
        assert (scene_id, shape, unchanged_confirmations) == (
            698,
            MAGIC_INVASION_QUALITY_SCROLL_SHAPE,
            2,
        )
        before = self.page
        if direction == "up":
            self.page = max(0, self.page - 1)
        else:
            self.page = min(self.last_quality_page, self.page + 1)
        self.events.append(("scroll", direction, before, self.page))
        if False:
            yield None
        return before != self.page


def _runtime_reader(choices, *, panel_address=0x698):
    def read():
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "active_map_act_auto_tips_view",
            "auto_exorcism_choices": deepcopy(choices),
            "evidence": {
                "pid": 41,
                "process_start_ticks": 99,
                "panel_address": panel_address,
            },
        }

    return read


def _runtime_snapshots(choices, panel_addresses):
    addresses = iter(panel_addresses)

    def read():
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "active_map_act_auto_tips_view",
            "auto_exorcism_choices": deepcopy(choices),
            "evidence": {
                "pid": 41,
                "process_start_ticks": 99,
                "panel_address": next(addresses),
            },
        }

    return read


def test_expected_shape_contract_is_explicit_and_does_not_start_auto() -> None:
    titles = expected_magic_invasion_auto_shape_titles()
    assert titles[0] == "品质配置滑窗"
    assert set(MAGIC_INVASION_AUTO_SHAPE_BY_KEY.values()) < set(titles)
    assert "圣主" in titles
    assert "圣主·四倍功勋符" in titles
    assert "圣主·追命索" in titles
    assert MAGIC_INVASION_START_AUTO_SHAPE not in titles


def test_configurator_preflights_scrolls_clicks_and_proves_zero_delta() -> None:
    desired = desired_magic_invasion_auto_choices()
    current = {key: not value for key, value in desired.items()}
    context = _Context(current)

    result = _drain(
        configure_magic_invasion_auto_options(
            context,
            runtime_reader=_runtime_reader(current),
        )
    )

    click_events = [event for event in context.events if event[0] == "click"]
    first_click = context.events.index(click_events[0])
    shape_checks = [
        index
        for index, event in enumerate(context.events)
        if event[0] == "get_shape"
    ]
    assert len(shape_checks) == len(expected_magic_invasion_auto_shape_titles())
    assert max(shape_checks) < first_click
    assert {title for _, title in click_events} == set(
        MAGIC_INVASION_AUTO_SHAPE_BY_KEY.values()
    )
    assert all(title != MAGIC_INVASION_START_AUTO_SHAPE for _, title in click_events)
    assert result["applied_keys"] == list(MAGIC_INVASION_AUTO_SHAPE_BY_KEY)
    assert result["after"] == desired


def test_configurator_fails_before_click_when_shape_is_missing() -> None:
    desired = desired_magic_invasion_auto_choices()
    current = dict(desired)
    current["quality_saint_master"] = False
    shapes = set(expected_magic_invasion_auto_shape_titles()) - {"圣主"}
    context = _Context(current, shapes=shapes)

    with pytest.raises(RuntimeError, match="缺少待点正式 Shape.*圣主"):
        _drain(
            configure_magic_invasion_auto_options(
                context,
                runtime_reader=_runtime_reader(current),
            )
        )
    assert not any(event[0] == "click" for event in context.events)


def test_configurator_fails_when_runtime_after_does_not_converge() -> None:
    desired = desired_magic_invasion_auto_choices()
    current = dict(desired)
    current["tenfold_exorcism"] = True
    context = _Context(current, mutate=False)

    with pytest.raises(RuntimeError, match="配置后仍有差异.*tenfold_exorcism"):
        _drain(
            configure_magic_invasion_auto_options(
                context,
                runtime_reader=_runtime_reader(current),
            )
        )
    assert ("click", "十连除魔") in context.events


def test_configurator_rejects_a_different_runtime_panel_after_clicks() -> None:
    desired = desired_magic_invasion_auto_choices()
    current = dict(desired)
    current["tenfold_exorcism"] = True
    context = _Context(current)

    with pytest.raises(RuntimeError, match="不是同一 Runtime 面板"):
        _drain(
            configure_magic_invasion_auto_options(
                context,
                runtime_reader=_runtime_snapshots(current, [0x698, 0x699]),
            )
        )
