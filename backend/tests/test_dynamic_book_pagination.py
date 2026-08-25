from backend.core.library.dynamic_book_pagination import (
    dynamic_book_html_character_count,
    dynamic_book_html_page_count,
    dynamic_book_page_at,
    dynamic_book_page_count,
    estimate_image_equivalent_characters,
)


def test_dynamic_book_uses_one_page_per_thousand_characters() -> None:
    assert dynamic_book_page_count(0) == 1
    assert dynamic_book_page_count(999) == 1
    assert dynamic_book_page_count(1000) == 1
    assert dynamic_book_page_count(1001) == 2
    assert dynamic_book_page_count("字" * 2501) == 3


def test_dynamic_book_character_offsets_map_to_the_same_page_rule() -> None:
    assert dynamic_book_page_at(0) == 1
    assert dynamic_book_page_at(999) == 1
    assert dynamic_book_page_at(1000) == 2


def test_image_weight_uses_dimensions_without_overweighting_icons() -> None:
    assert estimate_image_equivalent_characters(9, 13) == 1
    assert estimate_image_equivalent_characters(32, 32) == 4
    assert estimate_image_equivalent_characters(100, 100) == 39
    assert estimate_image_equivalent_characters(690, 500) == 592
    assert estimate_image_equivalent_characters(4000, 1000) == 1000


def test_html_page_count_includes_image_equivalent_characters() -> None:
    html = '<p>字</p><img width="690" height="500"><p>文</p>'
    assert dynamic_book_html_character_count(html) == 594
    assert dynamic_book_html_page_count(html) == 1
    assert dynamic_book_html_page_count(html + '<p>' + "字" * 500 + "</p>") == 2


def test_inline_footnote_images_do_not_use_full_illustration_fallback() -> None:
    html = (
        '<p>' + "字" * 300
        + '<sup><img class="duokan-footnote" alt="note1"></sup>'
        + '<sup><img class="duokan-footnote" alt="note2"></sup></p>'
    )
    assert dynamic_book_html_character_count(html) == 302
    assert dynamic_book_html_page_count(html) == 1
