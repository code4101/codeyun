from backend.core.fanxiu.data_annotation.tasks.xianyuan_duokui_tail import (
    _click_exact_compact_shop_name,
    _shop_ready,
    _store_panel_wallet,
    read_xianyuan_shop_wallet_from_ocr,
)
from types import SimpleNamespace


def test_exact_shop_header_reads_activity_local_wallet() -> None:
    class Runtime:
        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "a", "text": "当前拥有夺魁灵玉", "x": 10, "y": 100, "w": 120, "h": 20},
                {"parent_line_id": "a", "text": "12080", "x": 140, "y": 100, "w": 50, "h": 20},
                {"parent_line_id": "b", "text": "活动期间累计夺魁灵玉", "x": 10, "y": 130, "w": 160, "h": 20},
                {"parent_line_id": "b", "text": "136080", "x": 180, "y": 130, "w": 60, "h": 20},
            ]

    assert read_xianyuan_shop_wallet_from_ocr(Runtime()) == (12_080, 136_080)


def test_split_shop_header_reads_activity_local_wallet() -> None:
    class Runtime:
        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "a", "text": "当前拥有夺魁灵玉", "x": 10, "y": 100, "w": 120, "h": 20},
                {"parent_line_id": "a2", "text": "12080", "x": 140, "y": 100, "w": 50, "h": 20},
                {"parent_line_id": "b", "text": "活动期间累计夺魁灵玉", "x": 10, "y": 130, "w": 160, "h": 20},
                {"parent_line_id": "b2", "text": "136080", "x": 180, "y": 130, "w": 60, "h": 20},
            ]

    assert read_xianyuan_shop_wallet_from_ocr(Runtime()) == (12_080, 136_080)


def test_shop_ready_requires_two_consistent_valid_frames() -> None:
    class Runtime:
        samples = iter(((212_080, 136_080), (12_080, 136_080), (12_080, 136_080)))

        def full_frame_ocr_tokens(self, **_kwargs):
            current, cumulative = next(self.samples)
            return [
                {"parent_line_id": "a", "text": "当前拥有夺魁灵玉", "x": 10, "y": 100, "w": 120, "h": 20},
                {"parent_line_id": "a2", "text": str(current), "x": 140, "y": 100, "w": 60, "h": 20},
                {"parent_line_id": "b", "text": "活动期间累计夺魁灵玉", "x": 10, "y": 130, "w": 160, "h": 20},
                {"parent_line_id": "b2", "text": str(cumulative), "x": 180, "y": 130, "w": 60, "h": 20},
            ]

        def wait_action_settle(self, _seconds):
            yield None

    operation = _shop_ready(Runtime(), attempts=3)
    while True:
        try:
            next(operation)
        except StopIteration as done:
            assert done.value == (12_080, 136_080)
            break


def test_shop_name_click_distinguishes_xuanling_from_xuanxue() -> None:
    class Runtime:
        clicked = None

        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "a", "text": "玄血丹", "x": 200, "y": 900, "w": 100, "h": 30},
                {"parent_line_id": "a", "text": "·珍", "x": 300, "y": 900, "w": 50, "h": 30},
                {"parent_line_id": "b", "text": "玄灵丹", "x": 210, "y": 1100, "w": 100, "h": 30},
                {"parent_line_id": "b", "text": "·珍", "x": 310, "y": 1100, "w": 50, "h": 30},
            ]

        def click_frame_point(self, scene, x, y):
            self.clicked = (scene, x, y)

    runtime = Runtime()
    _click_exact_compact_shop_name(runtime, "玄灵丹·珍")
    assert runtime.clicked == (559, 285.0, 1115.0)


def test_panel_wallet_invalidates_the_preceding_derived_budget() -> None:
    activity = SimpleNamespace(
        current_currency=0,
        cumulative_currency=0,
        captured_at="",
        evidence={
            "refresh_status": {"shop": "updated", "currency": "retained"},
            "exchange_plan": {"schema": 9, "budget_ready": False},
        },
    )

    class Session:
        def add(self, value):
            assert value is activity

    _store_panel_wallet(
        Session(), activity=activity, current=12_080, cumulative=136_080
    )

    assert activity.current_currency == 12_080
    assert activity.cumulative_currency == 136_080
    assert activity.evidence["refresh_status"]["currency"] == "updated"
    assert activity.evidence["exchange_plan"]["schema"] == 0
