from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest

from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang import (
    XianzangChoiceNotApplicableError,
    XianzangRewardCandidate,
    choose_xianzang_shenlian_candidate,
    choose_xianzang_rare_resource_columns,
    complete_xianzang_optional_reward_selection,
    complete_xianzang_store,
    derive_xianzang_row_button_points,
    detect_xianzang_selected_columns,
    ensure_xianzang_row_choices_selected,
    is_xianzang_main_page_text,
    parse_xianzang_row_selected_fraction,
    shenlian_distance_to_next_stage,
    xianzang_optional_reward_plan,
)


def _shape(title: str, x: float, y: float, w: float = 0.04, h: float = 0.025) -> dict:
    return {
        "kind": "shape",
        "title": title,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "children": [],
    }


def _view448() -> dict:
    return {
        "kind": "image",
        "number": 448,
        "width": 900,
        "height": 1600,
        "shapes": [
            _shape("按钮1-1", 0.18, 0.25),
            _shape("按钮1-2", 0.34, 0.25),
            _shape("按钮2-1", 0.18, 0.41),
            _shape("按钮3-1", 0.18, 0.57),
        ],
    }


def _candidate(column: int, talisman_id: int | None, *, kind: str = "talisman_refine_material"):
    return XianzangRewardCandidate(
        column=column,
        reward_item_id=4_400_000 + column,
        reward_name=f"候选{column}",
        target_talisman_id=talisman_id,
        kind=kind,
    )


def _data_url(image: np.ndarray) -> str:
    ok, payload = cv2.imencode(".png", image)
    assert ok
    return "data:image/png;base64," + base64.b64encode(payload.tobytes()).decode("ascii")


class _FakeRuntime:
    def __init__(self, selected: set[int] | None = None, *, unknown_after_confirm: bool = False):
        self.selected = {1: set(selected or ()), 2: set(), 3: set()}
        self.clicks: list[tuple[float, float]] = []
        self.confirmed = False
        self.unknown_after_confirm = unknown_after_confirm
        self.view448 = _view448()
        self.points = {
            row: derive_xianzang_row_button_points(self.view448, row)
            for row in (1, 2, 3)
        }

    def _frame(self) -> str:
        image = np.zeros((1600, 900, 3), dtype=np.uint8)
        for row, columns in self.selected.items():
            for column in columns:
                x, y = (int(round(value)) for value in self.points[row][column - 1])
                cv2.line(image, (x - 10, y), (x - 2, y + 9), (60, 220, 120), 6)
                cv2.line(image, (x - 2, y + 9), (x + 12, y - 11), (60, 220, 120), 6)
        return _data_url(image)

    def view(self, scene_id: int):
        assert scene_id == 448
        return self.view448

    def current_scene(self, scene_ids, *, update: bool):
        assert update is True
        if self.confirmed:
            assert 447 in scene_ids
            if self.unknown_after_confirm:
                return None, 85.9, self._frame()
            return 447, 100.0, self._frame()
        assert 448 in scene_ids
        return 448, 100.0, self._frame()

    def cur_frame(self, *, update: bool):
        assert update is True
        return self._frame()

    def ocr_text(self, _frame: str):
        if self.confirmed and self.unknown_after_confirm:
            return "蓬莱仙藏规则活动时间自选累计鉴宝十次鉴宝大奖记录奖励预览任务商店"
        return (
            f"珍宝奖励(3%){len(self.selected[1])}/1"
            f"稀有奖励(24%){len(self.selected[2])}/3"
            f"普通奖励(73%){len(self.selected[3])}/5"
        )

    def click_frame_point(self, _view, x: float, y: float):
        row, column = min(
            (
                (row, column)
                for row, points in self.points.items()
                for column in range(1, len(points) + 1)
            ),
            key=lambda candidate: (
                abs(self.points[candidate[0]][candidate[1] - 1][0] - x)
                + abs(self.points[candidate[0]][candidate[1] - 1][1] - y)
            ),
        )
        self.selected[row].symmetric_difference_update({column})
        self.clicks.append((x, y))

    def click_shape(self, scene_id: int, title: str, *, frame_data_url: str):
        assert scene_id == 448
        assert title == "确认"
        assert frame_data_url.startswith("data:image/png;base64,")
        self.confirmed = True


@pytest.mark.parametrize(
    ("level", "distance"),
    [(0, 1), (1, 8), (2, 7), (8, 1), (9, 9), (11, 7), (17, 1), (18, 9)],
)
def test_shenlian_distance_uses_initial_one_ninth_then_integer_boundaries(level: int, distance: int):
    assert shenlian_distance_to_next_stage(level) == distance


def test_choose_shenlian_candidate_excludes_unowned_and_breaks_ties_leftmost():
    candidates = [_candidate(1, 8), _candidate(2, 1023), _candidate(3, 5), _candidate(4, 45)]
    talismans = [
        {"talisman_id": 8, "name": "青竹蜂云剑", "owned": True, "rank": 35, "wujing_level": 11},
        {"talisman_id": 1023, "name": "妙弈珍珑棋", "owned": True, "rank": 1, "wujing_level": 1},
        {"talisman_id": 5, "name": "乌龙夺", "owned": True, "rank": 48, "wujing_level": 2},
        {"talisman_id": 45, "name": "狼首玉如意", "owned": True, "rank": 98, "wujing_level": 2},
    ]

    choice = choose_xianzang_shenlian_candidate(candidates, talismans)

    assert choice.candidate.column == 1
    assert choice.talisman_name == "青竹蜂云剑"
    assert choice.distance_to_next_stage == 7


def test_choose_shenlian_candidate_never_selects_zero_rank_body():
    candidates = [_candidate(1, 8), _candidate(2, 1023), _candidate(3, 5), _candidate(4, 45)]
    talismans = [
        {"talisman_id": 8, "name": "左侧未拥有", "owned": False, "rank": 0, "wujing_level": 8},
        {"talisman_id": 1023, "name": "第二项", "owned": True, "rank": 1, "wujing_level": 1},
        {"talisman_id": 5, "name": "第三项", "owned": True, "rank": 1, "wujing_level": 2},
        {"talisman_id": 45, "name": "第四项", "owned": True, "rank": 1, "wujing_level": 3},
    ]

    choice = choose_xianzang_shenlian_candidate(candidates, talismans)

    assert choice.candidate.column == 4


def test_unrefined_owned_body_precedes_refined_body_one_step_from_boundary():
    candidates = [_candidate(1, 8), _candidate(2, 1023), _candidate(3, 5), _candidate(4, 45)]
    talismans = [
        {"talisman_id": 8, "owned": True, "rank": 1, "wujing_level": 8},
        {"talisman_id": 1023, "owned": True, "rank": 1, "wujing_level": 0},
        {"talisman_id": 5, "owned": True, "rank": 1, "wujing_level": 17},
        {"talisman_id": 45, "owned": True, "rank": 1, "wujing_level": 3},
    ]

    choice = choose_xianzang_shenlian_candidate(candidates, talismans)

    assert choice.candidate.column == 2
    assert choice.selection_reason.startswith("优先未神炼")
    assert [item.selected for item in choice.candidate_evidence] == [False, True, False, False]
    assert choice.candidate_evidence[0].elimination_reason == (
        "存在未神炼且已拥有本体的候选，后者绝对优先"
    )


def test_candidate_evidence_covers_all_four_ids_progress_and_elimination_reasons():
    candidates = [_candidate(1, 8), _candidate(2, 1023), _candidate(3, 5), _candidate(4, 45)]
    talismans = [
        {"talisman_id": 8, "owned": False, "rank": 0, "wujing_level": 0},
        {"talisman_id": 1023, "owned": True, "rank": 2, "wujing_level": 2},
        {"talisman_id": 5, "owned": True, "rank": 3, "wujing_level": 11},
        {"talisman_id": 45, "owned": True, "rank": 4, "wujing_level": 2},
    ]

    choice = choose_xianzang_shenlian_candidate(candidates, talismans)

    assert choice.candidate.column == 2
    assert [
        (
            item.target_talisman_id,
            item.body_owned,
            item.rank,
            item.wujing_level,
            item.distance_to_next_stage,
            item.selected,
        )
        for item in choice.candidate_evidence
    ] == [
        (8, False, 0, 0, None, False),
        (1023, True, 2, 2, 7, True),
        (5, True, 3, 11, 7, False),
        (45, True, 4, 2, 7, False),
    ]
    assert choice.candidate_evidence[0].elimination_reason == "未拥有法宝本体"
    assert choice.candidate_evidence[2].elimination_reason == (
        "距下一9级梯度所需材料并列，按从左到右优先"
    )


def test_mixed_treasure_choices_do_not_enter_shenlian_policy():
    candidates = [_candidate(1, 8), _candidate(2, None, kind="currency"), _candidate(3, 5), _candidate(4, 45)]

    with pytest.raises(XianzangChoiceNotApplicableError, match="不是完整"):
        choose_xianzang_shenlian_candidate(candidates, [])


def test_button_points_use_first_two_buttons_as_pitch_for_every_row():
    first = derive_xianzang_row_button_points(_view448(), 1)
    second = derive_xianzang_row_button_points(_view448(), 2)

    assert np.allclose(first, ((180, 420), (324, 420), (468, 420), (612, 420)))
    assert np.allclose(second, ((180, 676), (324, 676), (468, 676), (612, 676), (756, 676)))


def test_green_check_detection_uses_derived_checkbox_centers():
    points = derive_xianzang_row_button_points(_view448(), 1)
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    x, y = (int(round(value)) for value in points[2])
    cv2.line(image, (x - 10, y), (x - 2, y + 9), (60, 220, 120), 6)
    cv2.line(image, (x - 2, y + 9), (x + 12, y - 11), (60, 220, 120), 6)

    assert detect_xianzang_selected_columns(_data_url(image), points) == (3,)


def test_parse_selected_fraction_is_bound_to_the_requested_reward_row():
    text = "珍宝奖励(3%)1/1 稀有奖励(24%)2/3 普通奖励(73%)0/5"

    assert parse_xianzang_row_selected_fraction(text, 1) == (1, 1)
    assert parse_xianzang_row_selected_fraction(text, 2) == (2, 3)
    assert parse_xianzang_row_selected_fraction(text, 3) == (0, 5)


def test_parse_selected_fraction_ignores_adjacent_reward_amounts_joined_by_full_frame_ocr():
    text = "珍宝奖励(3%)1/1稀有奖励(24%)0/350200普通奖励(73%)0/540"

    assert parse_xianzang_row_selected_fraction(text, 1) == (1, 1)
    assert parse_xianzang_row_selected_fraction(text, 2) == (0, 3)
    assert parse_xianzang_row_selected_fraction(text, 3) == (0, 5)


def test_main_page_text_requires_business_evidence_and_rejects_the_selection_popup():
    assert is_xianzang_main_page_text("蓬莱仙藏规则活动时间鉴宝十次任务商店") is True
    assert is_xianzang_main_page_text("蓬莱仙藏自选奖励珍宝奖励确认") is False
    assert is_xianzang_main_page_text("其它活动鉴宝") is False


def test_ensure_row_selection_is_idempotent_when_target_is_already_checked(monkeypatch):
    runtime = _FakeRuntime({1})
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.penglai_xianzang.time.sleep", lambda _seconds: None)

    result = ensure_xianzang_row_choices_selected(runtime, 1, [1])

    assert result.changed is False
    assert result.selected_columns == (1,)
    assert runtime.clicks == []


def test_ensure_row_selection_clicks_derived_target_and_verifies_green_check_and_ocr(monkeypatch):
    runtime = _FakeRuntime(set())
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.penglai_xianzang.time.sleep", lambda _seconds: None)

    result = ensure_xianzang_row_choices_selected(runtime, 1, [3])

    assert result.changed is True
    assert result.selected_columns == (3,)
    assert runtime.clicks == [pytest.approx(runtime.points[1][2])]


def test_existing_exact_green_check_is_idempotent_when_fraction_ocr_is_missing(
    monkeypatch,
):
    runtime = _FakeRuntime({3})
    runtime.ocr_text = lambda _frame: ""
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang.time.sleep",
        lambda _seconds: None,
    )

    result = ensure_xianzang_row_choices_selected(
        runtime,
        1,
        [3],
        allow_missing_fraction_ocr=True,
    )

    assert result.changed is False
    assert result.selected_columns == (3,)
    assert runtime.clicks == []


def test_fraction_ocr_contradiction_still_rejects_matching_green_check(monkeypatch):
    runtime = _FakeRuntime({3})
    runtime.ocr_text = lambda _frame: "珍宝奖励(3%)0/1"
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(RuntimeError, match=r"绿色勾=\(3,\)，OCR=\(0, 1\)"):
        ensure_xianzang_row_choices_selected(
            runtime,
            1,
            [3],
            timeout_seconds=0.5,
            allow_missing_fraction_ocr=True,
        )


@pytest.mark.parametrize(
    ("prayer", "columns"),
    [
        ("淬体", (4, 3, 1)),
        ("炼丹", (3, 1, 4)),
        ("仙花", (1, 3, 4)),
        ("灵兽", (2, 3, 1)),
        ("洗灵", (5, 3, 1)),
    ],
)
def test_rare_reward_reserves_prayer_resource_then_fills_by_priority(prayer: str, columns: tuple[int, ...]):
    assert choose_xianzang_rare_resource_columns(prayer) == columns


def test_complete_reward_plan_keeps_treasure_selects_three_rare_and_all_normal():
    assert xianzang_optional_reward_plan(1, prayer_category="淬体") == {
        1: (1,),
        2: (4, 3, 1),
        3: (1, 2, 3, 4, 5),
    }


def test_complete_selection_verifies_all_rows_before_confirming_and_closes_448(monkeypatch):
    runtime = _FakeRuntime({1})
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.penglai_xianzang.time.sleep", lambda _seconds: None)

    result = complete_xianzang_optional_reward_selection(
        runtime,
        1,
        prayer_category="淬体",
    )

    assert runtime.selected == {1: {1}, 2: {1, 3, 4}, 3: {1, 2, 3, 4, 5}}
    assert runtime.confirmed is True
    assert result.confirmed is True
    assert result.final_scene == 447


def test_complete_selection_accepts_real_unnumbered_xianzang_main_page_after_448_closes(monkeypatch):
    runtime = _FakeRuntime({1}, unknown_after_confirm=True)
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.tasks.penglai_xianzang.time.sleep", lambda _seconds: None)

    result = complete_xianzang_optional_reward_selection(
        runtime,
        1,
        prayer_category="淬体",
    )

    assert result.confirmed is True
    assert result.final_scene is None
    assert result.final_scene_score == pytest.approx(85.9)


def test_xianzang_store_uses_generic_449_region_completion(monkeypatch):
    expected = object()
    calls = []

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_store.open_xianzang_tab",
        lambda runtime, tab: calls.append(("open", runtime, tab)),
    )

    def fake_complete(runtime, **kwargs):
        calls.append(("operate", runtime, kwargs))
        return expected

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_store.operate_activity_store_region",
        fake_complete,
    )
    runtime = object()

    assert complete_xianzang_store(runtime) is expected
    assert len(calls) == 2
    assert calls[0] == ("open", runtime, "商店")
    _kind, called_runtime, kwargs = calls[1]
    assert called_runtime is runtime
    assert kwargs["scene_id"] == 449
    assert kwargs["region_title"] == "区域"
    selector = kwargs["select_targets"]
    from backend.core.fanxiu.data_annotation.tasks.activity_store import (
        ActivityStoreNumericTarget,
        ActivityStoreRegionScan,
    )

    stone = ActivityStoreNumericTarget(488, "488", False, 10, 20, 30, 40)
    cash = ActivityStoreNumericTarget(6, "6", True, 50, 20, 30, 40)
    assert selector(ActivityStoreRegionScan((stone, cash))) == (stone,)
