from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    operate_activity_store_region,
    scan_activity_store_region,
)


def _tokens(text: str, *, x: float, y: float, line: str) -> list[dict]:
    result = []
    cursor = x
    for order, character in enumerate(text):
        width = 18.0 if character.isdigit() else 24.0
        result.append(
            {
                "text": character,
                "x": cursor,
                "y": y,
                "w": width,
                "h": 30.0,
                "parent_line_id": line,
                "line_order": int(y),
                "order": order,
            }
        )
        cursor += width
    return result


def test_store_region_finds_arbitrary_integer_prices_and_classifies_cash():
    tokens = (
        _tokens("488", x=100, y=1100, line="first")
        + _tokens("12345", x=360, y=1100, line="second")
        + _tokens("6元", x=700, y=1100, line="cash")
    )

    scan = scan_activity_store_region(tokens)

    assert [(target.value, target.is_cash) for target in scan.targets] == [
        (488, False),
        (12345, False),
        (6, True),
    ]
    assert scan.targets[0].center == pytest.approx((127, 1115))


def test_store_region_normalizes_fullwidth_digits_before_classifying_cash():
    scan = scan_activity_store_region(
        _tokens("９８８", x=100, y=1100, line="stone")
        + _tokens("３０元", x=500, y=1100, line="cash")
    )

    assert [(target.value, target.is_cash) for target in scan.targets] == [
        (988, False),
        (30, True),
    ]


def test_store_region_rejects_partial_box_inside_multi_character_token():
    scan = scan_activity_store_region(
        [
            {
                "text": "售价488",
                "x": 100,
                "y": 1100,
                "w": 120,
                "h": 30,
                "parent_line_id": "merged",
                "line_order": 1,
                "order": 0,
            }
        ]
    )

    assert scan.targets == ()


@dataclass
class _FakeTarget:
    value: int
    cash: bool = False


class _FakeRuntime:
    def __init__(self, targets: list[_FakeTarget]):
        self.targets = list(targets)
        self.clicks: list[tuple[int, float, float]] = []
        self.frame_id = 0

    def current_scene(self, scene_ids, *, update: bool):
        assert update is True
        assert 449 in scene_ids
        self.frame_id += 1
        return 449, 100.0, f"frame-{self.frame_id}"

    def ocr_tokens_in_shapes(self, scene_id, shape_titles, *, frame_data_url, padding):
        assert scene_id == 449
        assert shape_titles == ["区域"]
        assert frame_data_url.startswith("frame-")
        assert padding == 0
        tokens: list[dict] = []
        for index, target in enumerate(self.targets):
            suffix = "元" if target.cash else ""
            tokens.extend(
                _tokens(
                    f"{target.value}{suffix}",
                    x=100 + index * 250,
                    y=1100,
                    line=f"target-{index}",
                )
            )
        return tokens

    def click_frame_point(self, scene_id: int, x: float, y: float):
        self.clicks.append((scene_id, x, y))
        centers = []
        for index, target in enumerate(self.targets):
            text = f"{target.value}{'元' if target.cash else ''}"
            width = sum(18.0 if character.isdigit() else 24.0 for character in text)
            centers.append(100 + index * 250 + width / 2)
        clicked_index = min(
            range(len(self.targets)),
            key=lambda index: abs(centers[index] - x),
        )
        self.targets.pop(clicked_index)


class _JitteringFakeRuntime(_FakeRuntime):
    def ocr_tokens_in_shapes(self, scene_id, shape_titles, *, frame_data_url, padding):
        tokens = super().ocr_tokens_in_shapes(
            scene_id,
            shape_titles,
            frame_data_url=frame_data_url,
            padding=padding,
        )
        jitter = (-3.0, 2.0, 4.0)[self.frame_id % 3]
        for token in tokens:
            token["x"] += jitter
            token["y"] -= jitter
        return tokens


def test_store_operation_applies_explicit_selector_one_fresh_scan_at_a_time(monkeypatch):
    runtime = _FakeRuntime([_FakeTarget(488), _FakeTarget(988), _FakeTarget(6, cash=True)])
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_store.time.sleep",
        lambda _seconds: None,
    )

    result = operate_activity_store_region(
        runtime,
        scene_id=449,
        select_targets=lambda scan: tuple(
            target for target in scan.targets if not target.is_cash
        ),
    )

    assert result.clicked_values == (488, 988)
    assert [(target.value, target.is_cash) for target in result.remaining_targets] == [(6, True)]
    assert len(runtime.clicks) == 2


def test_store_stability_accepts_bounded_live_ocr_box_jitter(monkeypatch):
    runtime = _JitteringFakeRuntime([_FakeTarget(248), _FakeTarget(988)])
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_store.time.sleep",
        lambda _seconds: None,
    )

    result = operate_activity_store_region(
        runtime,
        scene_id=449,
        select_targets=lambda scan: scan.targets,
    )

    assert result.clicked_values == (248, 988)


def test_store_completion_is_successful_when_region_is_already_empty(monkeypatch):
    runtime = _FakeRuntime([])
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_store.time.sleep",
        lambda _seconds: None,
    )

    result = operate_activity_store_region(
        runtime,
        scene_id=449,
        select_targets=lambda scan: scan.targets,
    )

    assert result.completed is True
    assert result.clicked_values == ()
    assert runtime.clicks == []


def test_store_operation_does_nothing_when_business_selector_returns_empty(monkeypatch):
    runtime = _FakeRuntime([_FakeTarget(6, cash=True), _FakeTarget(30, cash=True)])
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_store.time.sleep",
        lambda _seconds: None,
    )

    result = operate_activity_store_region(
        runtime,
        scene_id=449,
        select_targets=lambda _scan: (),
    )

    assert result.clicked_values == ()
    assert [(target.value, target.is_cash) for target in result.remaining_targets] == [
        (6, True),
        (30, True),
    ]
    assert runtime.clicks == []


def test_store_operation_can_select_cash_only_when_business_explicitly_requests_it(monkeypatch):
    runtime = _FakeRuntime([_FakeTarget(488), _FakeTarget(6, cash=True)])
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.activity_store.time.sleep",
        lambda _seconds: None,
    )

    result = operate_activity_store_region(
        runtime,
        scene_id=449,
        select_targets=lambda scan: tuple(target for target in scan.targets if target.is_cash),
    )

    assert result.clicked_values == (6,)
    assert [(target.value, target.is_cash) for target in result.remaining_targets] == [(488, False)]
