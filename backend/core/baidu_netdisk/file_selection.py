"""Pure validators for exact native file selection in the file dialog (no UI access, no I/O).

The file dialog ellipsizes long names in its list pixels, so a file source is never picked from OCR: the
dialog is navigated to the source's parent through the filename edit control, the one list row whose full
native UIA ``ListItemControl`` name equals the source name exactly is clicked, and the edit control's exact
UIA value is read back.  These helpers judge geometry, provenance and values only; every refusal is a
:class:`StepFailed` ``reason`` with counts, never the UIA text or the source path.  Observed UI text is
compared as a string, never looked up on disk.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping

from .client import FILE_NAME_LABEL_RE, StepFailed

FILE_NAME_CONTROL_TYPES = ("EditControl",)
LIST_ROW_CONTROL_TYPES = ("ListItemControl",)  # explicit opt-in of the public core read: full row names
UNSAFE_PATH_CHARS = frozenset('"<>|?*')


def _cy(box: Mapping) -> float:
    return box["y"] + box["h"] / 2


def _fresh_read(read: Mapping, refuse, *, window_id: str, width: int, height: int, not_before: float) -> None:
    """Same window, UIA client-pixel provenance, frame size, taken after the frame and untruncated."""
    if read.get("window_id") != window_id:
        raise refuse("wrong_window")
    if read.get("source") != "uia" or read.get("coordinate_space") != "window_client_pixels":
        raise refuse("wrong_provenance")
    if read.get("width") != width or read.get("height") != height:
        raise refuse("frame_size_mismatch")
    read_at = read.get("read_at")
    if isinstance(read_at, bool) or not isinstance(read_at, (int, float)) or not read_at >= not_before:
        raise refuse("stale_read")
    if read.get("truncated") is not False:
        raise refuse("truncated_read")


def _inside(control: Mapping, bounds: list[float]) -> bool:
    bx, by, bw, bh = bounds
    x, y, w, h = control.get("bbox") or (math.nan,) * 4
    return bx <= x + w / 2 < bx + bw and by <= y + h / 2 < by + bh and w > 0 and h > 0


def directory_text(resolved: str) -> str:
    """The unquoted parent directory of an absolute file path plus one separator (the legacy navigation text).

    The whole file path is first checked by :func:`typed_path`, so the directory carries no control,
    quote or wildcard character."""
    typed_path(resolved)
    text = str(resolved).replace("/", "\\")
    parent = text[:text.rindex("\\")].rstrip("\\")
    if len(parent) < 2:
        raise StepFailed("source_path_not_typable", reason="no_parent_directory")
    return parent + "\\"


def list_bounds(band: list[float], width: int) -> list[float]:
    """Client-pixel ``[x, y, w, h]`` of the dialog area above the filename band (the file list lives there)."""
    top = float(band[1])
    if not math.isfinite(top) or top < 8 or width < 8:
        raise StepFailed("file_list_band_invalid", reason="degenerate_band")
    return [0.0, 0.0, float(width), top]


def list_row(read: Mapping, *, window_id: str, width: int, height: int, not_before: float, bounds: list[float],
             name: str) -> dict:
    """The single ``ListItemControl`` in ``bounds`` of a fresh, untruncated bounded UIA read of ``window_id``
    whose full native name equals ``name`` exactly (no case folding, trimming, substring or ellipsis match)."""
    def refuse(reason: str, **counts: object) -> StepFailed:
        return StepFailed("source_row_invalid", reason=reason, **counts)

    _fresh_read(read, refuse, window_id=window_id, width=width, height=height, not_before=not_before)
    if not isinstance(name, str) or not name:
        raise refuse("no_name")
    rows = [c for c in read.get("controls") or [] if c.get("type") == "ListItemControl" and _inside(c, bounds)]
    exact = [c for c in rows if c.get("name") == name]
    if len(exact) != 1:
        raise refuse("row_not_unique", count=len(exact), rows=len(rows))
    return exact[0]


def typed_path(resolved: str) -> str:
    """The quoted text typed for an absolute Windows file path; refuses anything that is not one plain path.

    Control characters (a typed newline would act as Enter) and dialog wildcard/quote characters are refused.
    """
    text = str(resolved)
    drive = len(text) > 3 and text[0].isalpha() and text[1:3] in (":\\", ":/")
    unc = text.startswith("\\\\") and len(text) > 4
    if not (drive or unc) or text != text.strip() or text.endswith(("\\", "/")):
        raise StepFailed("source_path_not_typable", reason="not_absolute_file_path")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise StepFailed("source_path_not_typable", reason="control_character")
    if any(c in UNSAFE_PATH_CHARS for c in text[2:]):  # skip the drive colon; '?' also refuses \\?\ prefixes
        raise StepFailed("source_path_not_typable", reason="reserved_character")
    return f'"{text}"'


def filename_band(confirm: Mapping, lines: Iterable[Mapping], width: int, height: int) -> list[float]:
    """Client-pixel ``[x, y, w, h]`` query band of the filename row, derived from the confirm button.

    The confirm button sits on the filename row.  Vertically: its centre +- its height, cut halfway to the
    nearest other OCR row above or below (so the file-type row never falls inside).  Horizontally: from the
    client's left edge to the confirm button's left edge.  The OCR filename label is not used: frames may drop
    it or merge it with the field text; the native control name is the authoritative association.
    """
    cy, half = _cy(confirm), float(confirm["h"])
    for line in lines:
        gap = abs(_cy(line) - cy)
        if line is not confirm and gap >= max(confirm["h"], 8) * .6:  # another row, not the confirm's own row
            half = min(half, gap / 2)
    x0, x1 = 0.0, min(float(width), float(confirm["x"]))
    y0, y1 = max(0.0, cy - half), min(float(height), cy + half)
    if not all(math.isfinite(v) for v in (x0, x1, y0, y1)) or x1 - x0 < 8 or y1 - y0 < 4:
        raise StepFailed("file_name_band_invalid", reason="degenerate_band")
    return [x0, y0, x1 - x0, y1 - y0]


def filename_control(read: Mapping, *, window_id: str, width: int, height: int, not_before: float,
                     band: list[float]) -> dict:
    """The single editable Edit control in ``band`` of a fresh, untruncated bounded UIA read of ``window_id``,
    whose native name is the filename label (:data:`FILE_NAME_LABEL_RE`); blank or other names refuse."""
    def refuse(reason: str, **counts: object) -> StepFailed:
        return StepFailed("file_name_control_invalid", reason=reason, **counts)

    _fresh_read(read, refuse, window_id=window_id, width=width, height=height, not_before=not_before)
    editable = [c for c in read.get("controls") or [] if c.get("type") == "EditControl"
                and c.get("is_readonly") is False and isinstance(c.get("value"), str) and _inside(c, band)]
    if len(editable) != 1:
        raise refuse("editable_control_not_unique", count=len(editable))
    name = editable[0].get("name")
    if not isinstance(name, str) or not FILE_NAME_LABEL_RE.fullmatch(name):  # the name itself is never reported
        raise refuse("control_name_mismatch")
    return editable[0]


def path_value(value: object, *expected: str) -> None:
    """The observed value must be exactly one of ``expected`` (one optional pair of surrounding quotes only)."""
    def refuse(reason: str) -> StepFailed:
        return StepFailed("file_name_value_mismatch", reason=reason)

    if not isinstance(value, str):
        raise refuse("no_value")
    text = value
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    if '"' in text:
        raise refuse("multiple_or_malformed_paths")
    if text not in expected:
        raise refuse("value_differs")


__all__ = ["FILE_NAME_CONTROL_TYPES", "LIST_ROW_CONTROL_TYPES", "directory_text", "filename_band",
           "filename_control", "list_bounds", "list_row", "path_value", "typed_path"]
