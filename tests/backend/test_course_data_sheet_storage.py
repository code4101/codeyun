from __future__ import annotations

from backend.core.attendance.course_data_sheet_storage import (
    set_grid_cell_inline_link,
    set_grid_cell_inline_links,
)


def test_set_grid_cell_inline_links_updates_multiple_cells_in_one_document_pass() -> None:
    document = {
        "columns": ["A", "B", "C"],
        "grid_rows": [["A", "B", "C"]],
        "cell_meta": {},
    }

    next_document, changed_count = set_grid_cell_inline_links(
        document,
        row_index=0,
        links=[
            (1, "https://example.com/b"),
            (2, "https://example.com/c"),
        ],
    )

    assert changed_count == 2
    assert next_document["grid_rows"][0][1]["link"]["url"] == "https://example.com/b"
    assert next_document["grid_rows"][0][2]["link"]["url"] == "https://example.com/c"
    assert next_document["cell_meta"]["0:1"]["link"]["url"] == "https://example.com/b"
    assert next_document["cell_meta"]["0:2"]["link"]["url"] == "https://example.com/c"


def test_set_grid_cell_inline_link_keeps_boolean_changed_contract() -> None:
    document = {
        "columns": ["A", "B"],
        "grid_rows": [["A", "B"]],
        "cell_meta": {},
    }

    next_document, changed = set_grid_cell_inline_link(
        document,
        row_index=0,
        column_index=1,
        url="https://example.com/b",
    )

    assert changed is True
    same_document, same_changed = set_grid_cell_inline_link(
        next_document,
        row_index=0,
        column_index=1,
        url="https://example.com/b",
    )
    assert same_changed is False
    assert same_document == next_document
