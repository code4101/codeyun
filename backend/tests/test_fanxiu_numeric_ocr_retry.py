from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values, retry_numeric_ocr


def test_unreadable_samples_retry_until_valid_zero():
    samples = iter(["", "剩余次数：（C", "剩余次数：0"])
    waits = []
    result = retry_numeric_ocr(lambda: next(samples), parse_ocr_values, wait=waits.append)
    assert result == ((0,), "剩余次数：0")
    assert waits == [2.0, 2.0]


@pytest.mark.parametrize("value", [0, 0.25, (0, 8)])
def test_valid_numeric_result_is_not_retried(value):
    waits = []
    assert retry_numeric_ocr(lambda: "text", lambda _: value, wait=waits.append) == (value, "text")
    assert waits == []


def test_exhaustion_preserves_last_text_and_has_no_final_sleep():
    samples = iter(["a", "b", "c"])
    waits = []
    assert retry_numeric_ocr(
        lambda: next(samples), parse_ocr_values, wait=waits.append, max_attempts=3,
    ) == (None, "c")
    assert waits == [2.0, 2.0]


def test_fraction_requires_both_numbers_before_acceptance():
    samples = iter(["0", "0/8"])
    value, text = retry_numeric_ocr(
        lambda: next(samples), lambda raw: parse_ocr_values(raw, expected_count=2), wait=lambda _: None,
    )
    assert value == (0, 8)
    assert text == "0/8"


def test_read_errors_are_not_swallowed_or_replayed():
    calls = []

    def read():
        calls.append("read")
        raise RuntimeError("capture failed")

    with pytest.raises(RuntimeError, match="capture failed"):
        retry_numeric_ocr(read, parse_ocr_values, wait=lambda _: None)
    assert calls == ["read"]


def test_interruption_during_wait_stops_before_next_read():
    calls = []

    def read():
        calls.append("read")
        return ""

    def interrupted(_):
        raise InterruptedError("stopped")

    with pytest.raises(InterruptedError):
        retry_numeric_ocr(read, parse_ocr_values, wait=interrupted)
    assert calls == ["read"]
