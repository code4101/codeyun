import pytest

from backend.core.library.book_metadata import normalize_book_start_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        (" 1989 ", "1989"),
        ("1989-06", "1989-06"),
        ("2008-01-15", "2008-01-15"),
        ("2000-02-29", "2000-02-29"),
    ],
)
def test_normalize_book_start_date_preserves_declared_precision(raw, expected):
    assert normalize_book_start_date(raw) == expected


@pytest.mark.parametrize(
    "value",
    ["89", "1989-6", "1989-13", "1989-02-29", "0000"],
)
def test_normalize_book_start_date_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        normalize_book_start_date(value)
