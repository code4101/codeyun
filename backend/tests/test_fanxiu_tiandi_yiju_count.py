from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju_count as count


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value


class _Context:
    def __init__(self, readings=()):
        self.readings = iter(readings)

    def ocr_numbers_in_shapes(self, scene_id, shapes, **options):
        assert scene_id == 680
        assert shapes == ["单次对弈"]
        assert options == {"padding": 0, "crop": True}
        value = next(self.readings)
        return ([value] if value is not None else [], f"次数：{value}")

    def shape_box(self, scene_id, shape):
        assert scene_id == 680
        widths = {"对弈次数_增加": 58.5, "对弈次数_滑块": 45.0}
        return {"w": widths[shape], "h": 35.0}


class _SnapshotReader:
    def __init__(self, *snapshots):
        self.snapshots = iter(snapshots)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return next(self.snapshots)


def test_read_count_requires_one_positive_ocr_value() -> None:
    runtime = _Context([12])
    assert count.read_tiandi_yiju_round_count(
        runtime, count.TiandiYijuCountAssets()
    ) == 12


def test_read_count_fails_closed_when_ocr_is_empty() -> None:
    with pytest.raises(RuntimeError, match="无法唯一读回"):
        count.read_tiandi_yiju_round_count(
            _Context([None]), count.TiandiYijuCountAssets()
        )


def test_default_assets_expose_proven_minimum_and_right_boundary() -> None:
    assets = count.TiandiYijuCountAssets()
    assert assets.count_slider_left_anchor == "对弈次数_最小端点"
    assert assets.count_slider_right_anchor == "对弈次数_增加"
    assert assets.count_slider_track is None
    assert assets.count_decrease_large is None


def test_round_count_uses_live_native_maximum_not_ordinary_batch_cap(
    monkeypatch,
) -> None:
    calls = []

    def set_count(context, assets, desired, **options):
        calls.append((context, assets, desired, options))
        if False:
            yield None
        return {"before": 4325, "after": desired, "maximum": options["maximum"]}

    monkeypatch.setattr(count, "set_verified_integer_slider_count", set_count)
    reader = _SnapshotReader(
        {"current": 4325, "maximum": 4325},
        {"current": 1500, "maximum": 4325},
    )
    context = _Context()

    result = _drain(
        count.set_tiandi_yiju_round_count(
            context, 1500, runtime_count_reader=reader
        )
    )

    assert result["after"] == 1500
    assert calls[0][3]["maximum"] == 4325
    assert calls[0][3]["max_adjustments"] == 10
    assert calls[0][3]["initial_count"] == 4325
    assert "runtime_count_reader" not in calls[0][3]
    assert calls[0][1].count_slider_right_center_offset == -29.25


def test_funded_rounds_separates_resource_capacity_from_native_maximum(
    monkeypatch,
) -> None:
    calls = []

    def set_count(context, assets, desired, **options):
        calls.append((desired, options))
        if False:
            yield None
        return {"before": 4450, "after": desired, "maximum": options["maximum"]}

    monkeypatch.setattr(count, "set_verified_integer_slider_count", set_count)
    reader = _SnapshotReader(
        {"current": 4450, "maximum": 4450},
        {"current": 1500, "maximum": 4450},
    )

    result = _drain(
        count.set_tiandi_yiju_funded_rounds(
            _Context(),
            1500,
            4325,
            runtime_count_reader=reader,
        )
    )

    assert calls[0][0] == 1500
    assert calls[0][1]["maximum"] == 4450
    assert calls[0][1]["max_adjustments"] == 10
    assert result["after"] == 1500
    assert result["resource_capacity"] == 4325
    assert result["native_maximum"] == 4450


def test_funded_rounds_never_accepts_an_approximate_landing(monkeypatch) -> None:
    def set_count(*args, **kwargs):
        if False:
            yield None
        return {"before": 4325, "after": 1485, "maximum": 4325}

    monkeypatch.setattr(count, "set_verified_integer_slider_count", set_count)
    reader = _SnapshotReader(
        {"current": 4325, "maximum": 4325},
        {"current": 1485, "maximum": 4325},
    )
    with pytest.raises(RuntimeError, match="Runtime 复验失败"):
        _drain(
            count.set_tiandi_yiju_funded_rounds(
                _Context(), 1500, 4325, runtime_count_reader=reader
            )
        )


def test_resource_capacity_and_native_maximum_are_independent_guards() -> None:
    reader = _SnapshotReader({"current": 100, "maximum": 100})
    with pytest.raises(ValueError, match="Runtime 可用次数内"):
        _drain(
            count.set_tiandi_yiju_funded_rounds(
                _Context(), 101, 100, runtime_count_reader=reader
            )
        )
    assert reader.calls == 0

    with pytest.raises(RuntimeError, match="面板原生上限"):
        _drain(
            count.set_tiandi_yiju_funded_rounds(
                _Context(), 101, 120, runtime_count_reader=reader
            )
        )


@pytest.mark.parametrize("target", [0, -1, True, "all", "max"])
def test_invalid_target_is_rejected_before_runtime(target) -> None:
    reader = _SnapshotReader({"current": 1, "maximum": 5000})
    with pytest.raises(ValueError, match="必须为正整数"):
        _drain(
            count.set_tiandi_yiju_round_count(
                _Context(), target, runtime_count_reader=reader
            )
        )
    assert reader.calls == 0
