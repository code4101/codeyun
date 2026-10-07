import pytest

from backend.core.fanxiu.runtime_gui import (
    best_ocr_name_match,
    normalize_ocr_name,
    ocr_name_similarity,
    rank_ocr_name_matches,
)


def test_ocr_name_normalization_removes_combining_marks_and_symbols() -> None:
    assert normalize_ocr_name("\u0361仙-小鱼") == "仙小鱼"


def test_ocr_name_similarity_uses_partial_edit_distance() -> None:
    assert ocr_name_similarity("仙-小鱼", "【称号】仙-小渔【联盟】") == pytest.approx(2 / 3)


def test_ocr_name_similarity_keeps_multiple_missing_glyphs_with_title() -> None:
    # Real Lundao roster: OCR drops two uncommon Han glyphs but retains four.
    match = best_ocr_name_match("永恒、奫幪仙皇", ["[前途无量]永恒、仙皇"])
    assert match.similarity == pytest.approx(4 / 6)
    assert match.passed_threshold is True
    assert ocr_name_similarity("永恒、奫幪仙皇", "[前途无量]仙皇") < 0.62


def test_best_ocr_name_match_returns_highest_below_threshold() -> None:
    match = best_ocr_name_match("仙-小鱼", ["云-小雨", "仙-小渔"], threshold=0.9)

    assert match is not None
    assert match.observed == "仙-小渔"
    assert match.similarity == pytest.approx(2 / 3)
    assert match.passed_threshold is False


def test_rank_ocr_name_matches_is_stable_for_equal_scores() -> None:
    matches = rank_ocr_name_matches("甲乙", ["甲丙", "甲丁"], threshold=0.8)

    assert [match.index for match in matches] == [0, 1]
