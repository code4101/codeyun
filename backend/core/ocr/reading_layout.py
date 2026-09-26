"""Conservative reading blocks inferred from OCR geometry, not font metadata.

Raw lines/characters remain authoritative. These derived blocks can be rebuilt
without recognition. Heights estimate visible glyph size, not the original font
family or typographic em size. Ambiguous side-by-side boxes stay separate.
"""
from statistics import median
import re

LAYOUT_VERSION = 6


def normalized_font_scale(height: float, body: float) -> float:
    """Quantize noisy OCR sizes; the body band is deliberately wider than others.

    Relative units avoid dependence on scan DPI. Within ±15% of the body median,
    all lines use one body size. Other sizes snap to 0.05-em bands instead of
    exposing continuously varying recognition measurements to typography.
    """
    ratio = height / body
    if .85 <= ratio <= 1.15:
        return 1.0
    return round(max(.55, min(2.2, int(ratio / .05 + .5) * .05)), 2)


def build_reading_layout(lines: list[dict], geometry: dict) -> dict:
    """Join wrapped prose using indent, line gap, size and trailing whitespace.

    Preserve source IDs and bounding boxes for later selection/annotation. Do not
    infer semantic headings solely from short text, or fabricate missing text.
    """
    rows = [line for line in lines if line.get("text", "").strip() and line.get("h", 0) > 0]
    if not rows:
        return {"version": LAYOUT_VERSION, "blocks": [], "text": ""}
    body = median([r["h"] for r in rows if len(r["text"]) >= 12] or [r["h"] for r in rows])
    prose = [r for r in rows if .75 * body <= r["h"] <= 1.25 * body and len(r["text"]) >= 12]
    left = median(sorted(r["x"] for r in prose)[:max(1, len(prose) // 2)]) if prose else min(r["x"] for r in rows)
    right = median([r["x"] + r["w"] for r in prose]) if prose else max(r["x"] + r["w"] for r in rows)
    gaps = [b["y"] - a["y"] - a["h"] for a, b in zip(prose, prose[1:])
            if 0 <= b["y"] - a["y"] - a["h"] <= body]
    gap = median(gaps) if gaps else body * .4
    page_height = geometry.get("height", 0)
    pt_per_px = geometry.get("page_height_pt", 0) / page_height if page_height else 1
    blocks = []
    previous = None
    for index, row in enumerate(rows):
        height_ratio = row["h"] / body
        short = row["w"] < (right - left) * .7
        center = abs(row["x"] + row["w"] / 2 - (left + right) / 2) < body * 1.5
        margin = short and (not center or height_ratio < 1.1) and (row["y"] + row["h"] < page_height * .15 or row["y"] > page_height * .92)
        # Small section headings can use the same font size as prose. Require
        # centering and whitespace on both sides, not a particular numbering scheme.
        before = row["y"] - previous["y"] - previous["h"] if previous else 0
        following = rows[index + 1] if index + 1 < len(rows) else None
        after = following["y"] - row["y"] - row["h"] if following else 0
        isolated_center = (short and center and previous is not None and following is not None
                           and before > max(gap * 1.8, body * .8)
                           and after > max(gap * 1.8, body * .8))
        kind = "marginal" if margin else "heading" if short and (height_ratio >= 1.3 or isolated_center) else "paragraph"
        indent = max(0, row["x"] - left) / body
        delta = row["y"] - previous["y"] - previous["h"] if previous else 0
        separate = (not previous or kind != "paragraph" or blocks[-1]["kind"] != "paragraph"
                    or delta < -.2 * body or delta > max(gap * 1.8, gap + .65 * body)
                    # A size fluctuation alone is not a paragraph boundary. A
                    # sustained, substantial size change can mark a note block.
                    or (abs(row["h"] - previous["h"]) > body * .25
                        and (delta > gap * 1.3 or (following is not None
                             and abs(following["h"] - row["h"]) < body * .1)))
                    or abs(row["x"] - previous["x"]) > body * 3
                    or (indent >= .8 and row["x"] - previous["x"] > body * .6)
                    or previous["x"] + previous["w"] < right - body * 2
                    or bool(re.match(r"^(?:[•●▪]|\d+[.)、])\s*", row["text"])))
        if separate:
            # Preserve meaningful size differences (footnotes, quotations, headings).
            # OCR box jitter around body size should not produce varying prose fonts.
            ratio = normalized_font_scale(row["h"], body)
            blocks.append({"id": row["line_id"], "kind": kind, "text": row["text"].strip(),
                "line_ids": [row["line_id"]], "box": [row[k] for k in ("x", "y", "w", "h")],
                "font_size_estimate_pt": round(row["h"] * pt_per_px, 2),
                "font_scale": round(ratio, 2), "align": "center" if center and kind == "heading" else "right" if kind == "marginal" and row["x"] > (left + right) / 2 else "justify",
                "indent_em": (2 if indent >= 1.5 else 1) if kind == "paragraph" and .8 <= indent <= 3 else 0,
                "space_before_em": round(min(2, max(.65, delta / body)), 2) if blocks else 0})
        else:
            block = blocks[-1]
            text = row["text"].strip()
            # Latin word boundaries need a space; CJK wrapped lines do not.
            separator = " " if re.search(r"[A-Za-z0-9,;:]$", block["text"]) and re.match(r"[A-Za-z0-9]", text) else ""
            block["text"] += separator + text
            block["line_ids"].append(row["line_id"])
            x, y, w, h = block["box"]
            end_x, end_y = max(x + w, row["x"] + row["w"]), max(y + h, row["y"] + row["h"])
            x, y = min(x, row["x"]), min(y, row["y"])
            block["box"] = [x, y, end_x - x, end_y - y]
        previous = row
    by_id = {row["line_id"]: row for row in rows}
    for block in blocks:
        # The complete paragraph, not its first line, determines typography.
        representative = median(by_id[id]["h"] for id in block["line_ids"])
        block["font_scale"] = normalized_font_scale(representative, body)
        block["font_size_estimate_pt"] = round(representative * pt_per_px, 2)
    return {"version": LAYOUT_VERSION, "blocks": blocks, "text": "\n\n".join(b["text"] for b in blocks)}
