from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.ocr_values import (
    parse_ocr_fraction_numbers,
    parse_ocr_values,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7", (7,)),
        ("1/28", (1, 28)),
        ("1丨28", (1, 28)),
        ("1｜28", (1, 28)),
        ("1 28", (1, 28)),
        ("1\n28", (1, 28)),
        ("０／２８", (0, 28)),
        ("第 3 天，累计 12 次", (3, 12)),
    ],
)
def test_parse_ocr_values_extracts_ordered_integer_group(text: str, expected: tuple[int, ...]) -> None:
    assert parse_ocr_values(text) == expected


def test_parse_ocr_values_can_require_business_cardinality() -> None:
    assert parse_ocr_values("剩余 3", expected_count=1) == (3,)
    assert parse_ocr_values("1丨28", expected_count=2) == (1, 28)
    assert parse_ocr_values("1", expected_count=2) is None
    assert parse_ocr_values("第5天 1/28", expected_count=2) is None
    assert parse_ocr_values(
        "第5天 1/28",
        expected_count=2,
        allow_extra_numbers=True,
    ) == (1, 28)


def test_parse_ocr_values_reports_no_numeric_evidence() -> None:
    assert parse_ocr_values("") is None
    assert parse_ocr_values("今日已完成") is None


def test_parse_ocr_values_rejects_invalid_contract() -> None:
    with pytest.raises(ValueError):
        parse_ocr_values("1", expected_count=0)
    with pytest.raises(ValueError):
        parse_ocr_values("1 2", allow_extra_numbers=True)


def test_fraction_wrapper_is_only_a_compatibility_cardinality_adapter() -> None:
    assert parse_ocr_fraction_numbers("1 28") == (1, 28)
    assert parse_ocr_fraction_numbers("1") is None
    assert parse_ocr_fraction_numbers("1 0") is None


def test_fanxiu_business_modules_use_the_generic_ocr_values_interface() -> None:
    package_root = Path(__file__).parents[1] / "core" / "fanxiu" / "data_annotation"
    offenders = [
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*.py")
        if path.name != "ocr_values.py"
        and "parse_ocr_fraction_numbers" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
