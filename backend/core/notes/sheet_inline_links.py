from __future__ import annotations

import ast
import json
from typing import Any


def coerce_inline_cell_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text.startswith("{") or ("value" not in text and "link" not in text):
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return None
    return dict(parsed) if isinstance(parsed, dict) else None


def extract_inline_cell_value(value: Any) -> Any:
    cell = coerce_inline_cell_object(value)
    if cell is not None and "value" in cell:
        return cell.get("value")
    return value


def inline_cell_link(value: Any) -> dict[str, Any] | None:
    cell = coerce_inline_cell_object(value)
    if cell is None:
        return None
    link = cell.get("link")
    if not isinstance(link, dict):
        return None
    url = str(link.get("url") or "").strip()
    if not url:
        return None
    title = str(link.get("title") or "").strip()
    return {"url": url, **({"title": title} if title else {})}


def inline_cell_link_url(value: Any) -> str:
    return (inline_cell_link(value) or {}).get("url", "")


def with_inline_cell_link(value: Any, link: dict[str, Any]) -> Any:
    url = str(link.get("url") or "").strip()
    if not url:
        return extract_inline_cell_value(value)
    cell_value = extract_inline_cell_value(value)
    normalized_link = {"url": url}
    title = str(link.get("title") or "").strip()
    if title:
        normalized_link["title"] = title
    return {
        "value": "" if cell_value is None else cell_value,
        "link": normalized_link,
    }


def strip_link_from_meta_entry(value: Any) -> Any | None:
    if not isinstance(value, dict):
        return value
    next_value = dict(value)
    next_value.pop("link", None)
    return next_value or None


def strip_links_from_cell_meta(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    next_meta: dict[str, Any] = {}
    for key, entry in value.items():
        next_entry = strip_link_from_meta_entry(entry)
        if next_entry:
            next_meta[str(key)] = next_entry
    return next_meta


def strip_links_from_entity_cells(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    next_cells: dict[str, Any] = {}
    for row_id, row_cells in value.items():
        if not isinstance(row_cells, dict):
            continue
        next_row: dict[str, Any] = {}
        for column_id, entry in row_cells.items():
            next_entry = strip_link_from_meta_entry(entry)
            if next_entry:
                next_row[str(column_id)] = next_entry
        if next_row:
            next_cells[str(row_id)] = next_row
    return next_cells


def normalize_columns(document_json: dict[str, Any]) -> list[str]:
    columns = document_json.get("columns")
    if not isinstance(columns, list):
        return []
    return [str(column or "").strip() for column in columns]


def normalize_data_start_row(document_json: dict[str, Any]) -> int:
    try:
        return max(int(document_json.get("data_start_row") or 0), 0)
    except (TypeError, ValueError):
        return 0


def normalize_row(row: Any, column_count: int) -> list[Any]:
    if isinstance(row, list):
        normalized = list(row[:column_count])
    elif isinstance(row, dict):
        normalized = [row.get(str(index), "") for index in range(column_count)]
    else:
        normalized = []
    while len(normalized) < column_count:
        normalized.append("")
    return normalized


def _cell_meta_link(document_json: dict[str, Any], document_row: int, data_row: int, column_index: int) -> dict[str, Any] | None:
    cell_meta = document_json.get("cell_meta")
    if not isinstance(cell_meta, dict):
        return None
    for candidate_row in (document_row, data_row):
        if candidate_row == data_row and data_row < document_row:
            continue
        entry = cell_meta.get(f"{candidate_row}:{column_index}")
        link = inline_cell_link(entry)
        if link:
            return link
    return None


def _entity_cell_link(document_json: dict[str, Any], document_row: int, column_index: int) -> dict[str, Any] | None:
    entity_rows = document_json.get("entity_rows")
    entity_columns = document_json.get("entity_columns")
    entity_cells = document_json.get("entity_cells")
    if not isinstance(entity_rows, list) or not isinstance(entity_columns, list) or not isinstance(entity_cells, dict):
        return None
    if document_row < 0 or document_row >= len(entity_rows) or column_index < 0 or column_index >= len(entity_columns):
        return None
    entity_row = entity_rows[document_row]
    entity_column = entity_columns[column_index]
    row_id = str(entity_row.get("id") or "").strip() if isinstance(entity_row, dict) else ""
    column_id = str(entity_column.get("id") or "").strip() if isinstance(entity_column, dict) else ""
    if not row_id or not column_id:
        return None
    row_cells = entity_cells.get(row_id)
    if not isinstance(row_cells, dict):
        return None
    return inline_cell_link(row_cells.get(column_id))


def _legacy_link_for_cell(
    document_json: dict[str, Any],
    *,
    document_row: int,
    data_row: int,
    column_index: int,
) -> dict[str, Any] | None:
    return (
        _entity_cell_link(document_json, document_row, column_index)
        or _cell_meta_link(document_json, document_row, data_row, column_index)
    )


def canonicalize_sheet_document_inline_links(
    document_json: dict[str, Any],
    *,
    migrate_legacy_links: bool = True,
    strip_legacy_links: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    if not isinstance(document_json, dict):
        return document_json, {"inline": 0, "legacy": 0, "stripped_meta": 0, "changed": 0}

    columns = normalize_columns(document_json)
    rows = document_json.get("rows")
    if not columns or not isinstance(rows, list):
        return document_json, {
            "inline": 0,
            "legacy": 0,
            "stripped_meta": 0,
            "changed": 0,
        }

    column_count = len(columns)
    data_start_row = normalize_data_start_row(document_json)
    source_grid_rows = document_json.get("grid_rows")
    grid_rows = list(source_grid_rows) if isinstance(source_grid_rows, list) else []
    next_rows: list[Any] = []
    inline_count = 0
    legacy_count = 0
    changed = False

    for data_row, row in enumerate(rows):
        source_row = normalize_row(row, column_count)
        next_row = list(source_row)
        document_row = data_start_row + data_row
        grid_row = normalize_row(grid_rows[document_row], column_count) if document_row < len(grid_rows) else []
        for column_index, cell in enumerate(source_row):
            link = inline_cell_link(cell) or (inline_cell_link(grid_row[column_index]) if grid_row else None)
            if link:
                inline_count += 1
            elif migrate_legacy_links:
                link = _legacy_link_for_cell(
                    document_json,
                    document_row=document_row,
                    data_row=data_row,
                    column_index=column_index,
                )
                if link:
                    legacy_count += 1
            value = extract_inline_cell_value(cell)
            if (value in (None, "")) and grid_row:
                value = extract_inline_cell_value(grid_row[column_index])
            next_cell = with_inline_cell_link(value, link) if link else ("" if value is None else value)
            if next_cell != cell:
                changed = True
            next_row[column_index] = next_cell
        next_rows.append(next_row)

    next_grid_rows = None
    if isinstance(source_grid_rows, list):
        next_grid_rows = [normalize_row(row, column_count) for row in source_grid_rows]
        while len(next_grid_rows) < data_start_row + len(next_rows):
            next_grid_rows.append([""] * column_count)

        for row_index in range(min(data_start_row, len(next_grid_rows))):
            row = next_grid_rows[row_index]
            for column_index, cell in enumerate(row):
                link = inline_cell_link(cell)
                if link:
                    inline_count += 1
                elif migrate_legacy_links:
                    link = _legacy_link_for_cell(
                        document_json,
                        document_row=row_index,
                        data_row=row_index,
                        column_index=column_index,
                    )
                    if link:
                        legacy_count += 1
                value = extract_inline_cell_value(cell)
                next_cell = with_inline_cell_link(value, link) if link else ("" if value is None else value)
                if next_cell != cell:
                    changed = True
                row[column_index] = next_cell

        for data_row, row in enumerate(next_rows):
            document_row = data_start_row + data_row
            if next_grid_rows[document_row] != row:
                changed = True
            next_grid_rows[document_row] = row

    next_document = dict(document_json)
    next_document["rows"] = next_rows
    if next_grid_rows is not None:
        next_document["grid_rows"] = next_grid_rows

    stripped_meta = 0
    if strip_legacy_links:
        stripped_cell_meta = strip_links_from_cell_meta(next_document.get("cell_meta"))
        stripped_entity_cells = strip_links_from_entity_cells(next_document.get("entity_cells"))
        if stripped_cell_meta != next_document.get("cell_meta"):
            stripped_meta += 1
            changed = True
        if stripped_entity_cells != next_document.get("entity_cells"):
            stripped_meta += 1
            changed = True
        next_document["cell_meta"] = stripped_cell_meta
        next_document["entity_cells"] = stripped_entity_cells

    return next_document if changed else document_json, {
        "inline": inline_count,
        "legacy": legacy_count,
        "stripped_meta": stripped_meta,
        "changed": int(changed),
    }
