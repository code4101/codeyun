from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


DEFAULT_ITEM_CATALOG = Path("parsed_configs/item_catalog/item_catalog.json")
DEFAULT_OUTPUT_DIR = Path("parsed_configs/item_catalog/icon_quality_review")
DEFAULT_ICON_DIR = Path("icons")


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown_icon_preview(icon: str, *, size: int = 32) -> str:
    name = str(icon or "").strip()
    if not name:
        return "-"
    escaped = name.replace('"', "&quot;")
    return f'<img src="../../../icons/{escaped}.png" width="{size}" height="{size}" alt="{escaped}"><br>`{name}`'


def _load_icon_for_sheet(icon_dir: Path, icon: str, size: int):
    try:
        from PIL import Image
    except Exception:
        return None
    path = icon_dir / f"{icon}.png"
    if not path.is_file():
        return None
    try:
        image = Image.open(path).convert("RGBA")
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        return image
    except Exception:
        return None


def _write_no_candidate_contact_sheet(output_dir: Path, export_root: Path, no_candidate_groups: list[dict[str, Any]]) -> str:
    if not no_candidate_groups:
        return ""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return ""

    icon_dir = export_root / DEFAULT_ICON_DIR
    if not icon_dir.is_dir():
        return ""

    cell_w = 112
    row_h = 162
    icon_size = 64
    label_h = 34
    margin = 12
    title_h = 34
    preview_count = 6
    cols = 1 + preview_count
    width = margin * 2 + cols * cell_w
    height = margin * 2 + title_h + len(no_candidate_groups) * row_h
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    draw.text((margin, margin), "Fanxiu item icon no-candidate review sheet", fill=(20, 20, 20), font=font)
    y = margin + title_h
    for group in no_candidate_groups:
        draw.rectangle((margin, y, width - margin, y + row_h - 8), outline=(210, 210, 210), fill=(248, 248, 248))
        sample = group.get("sample") if isinstance(group.get("sample"), dict) else {}
        sample_label = f"sample {sample.get('id') or ''}".strip()
        if sample_label:
            draw.text((margin + 8, y + 6), sample_label, fill=(80, 80, 80), font=font)
        icons = [str(group.get("icon") or "").strip()]
        icons.extend(str(item.get("icon") or "").strip() for item in group.get("nearby_icons") or [])
        icons = [icon for icon in icons if icon][:cols]
        for index, icon in enumerate(icons):
            x = margin + index * cell_w
            icon_y = y + 30
            draw.rectangle((x + 8, icon_y, x + 8 + icon_size, icon_y + icon_size), outline=(220, 220, 220), fill=(255, 255, 255))
            image = _load_icon_for_sheet(icon_dir, icon, icon_size)
            if image is not None:
                sheet.paste(image, (x + 8 + (icon_size - image.width) // 2, icon_y + (icon_size - image.height) // 2), image)
            label = icon[:18]
            draw.text((x + 8, y + 102), label, fill=(30, 30, 30), font=font)
            if index == 0:
                draw.text((x + 8, y + 102 + label_h), "target", fill=(170, 30, 30), font=font)
            else:
                draw.text((x + 8, y + 102 + label_h), f"nearby {index}", fill=(70, 70, 70), font=font)
        y += row_h

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "item_icon_no_candidate_contact_sheet_latest.png"
    sheet.save(path)
    return str(path)


def _counter_summary(counter: Counter[str], *, limit: int = 8) -> str:
    return "；".join(f"{name}{count}" for name, count in counter.most_common(limit) if name)


def _sample_labels(cards: list[dict[str, Any]], *, limit: int = 10) -> str:
    labels: list[str] = []
    for card in cards:
        label = f"{card.get('id')}:{card.get('name')}".strip(":")
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return " | ".join(labels)


def _sample_cards(cards: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        item_id = str(card.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        samples.append(
            {
                "id": item_id,
                "name": str(card.get("name") or "").strip(),
                "resource_type": "item",
                "resource_href": f"/fanxiu-resource/item/{item_id}",
                "type_name": str(card.get("type_name") or "").strip(),
                "sub_type_name": str(card.get("sub_type_name") or "").strip(),
                "type_sub_type_name": str(card.get("type_sub_type_name") or "").strip(),
                "quality_name": str(card.get("quality_name") or "").strip(),
                "icon": str(card.get("icon") or "").strip(),
                "small_icon": str(card.get("small_icon") or "").strip(),
            }
        )
        if len(samples) >= limit:
            break
    return samples


_NAME_PREFIX_RE = re.compile(r"[（(【\[-]|\d")
_ICON_NUMBER_RE = re.compile(r"(?P<number>\d{3,})(?!.*\d)")


def _available_icon_names(export_root: Path) -> list[str]:
    icon_dir = export_root / DEFAULT_ICON_DIR
    if not icon_dir.is_dir():
        return []
    return sorted(path.stem for path in icon_dir.glob("*.png") if path.is_file())


def _icon_number_token(icon: str) -> str:
    match = _ICON_NUMBER_RE.search(str(icon or ""))
    return match.group("number") if match else ""


def _icon_family_tokens(icon: str) -> set[str]:
    text = str(icon or "").lower()
    tokens = {part for part in re.split(r"[^0-9a-z]+", text) if part}
    number = _icon_number_token(text)
    if number:
        tokens.add(number)
    return tokens


def _candidate_icon_score(target: str, candidate: str, field: str) -> tuple[int, str]:
    target_number = _icon_number_token(target)
    candidate_number = _icon_number_token(candidate)
    score = 0
    reasons: list[str] = []
    if target_number and candidate_number == target_number:
        score += 100
        reasons.append("同编号")
    target_tokens = _icon_family_tokens(target)
    candidate_tokens = _icon_family_tokens(candidate)
    overlap = sorted((target_tokens & candidate_tokens) - {target_number})
    if overlap:
        score += min(30, len(overlap) * 10)
        reasons.append("同族")
    if field == "small_icon" and candidate.startswith(("icon", "fashionicon")):
        score += 12
        reasons.append("可作主图候选")
    if field == "icon" and candidate.startswith(("icon3_", "icon5_", "icon8_")):
        score += 8
        reasons.append("同道具图变体")
    if candidate.startswith("common_corner_"):
        score -= 40
    if candidate.startswith("mainui_icon_"):
        score -= 10
    return score, "、".join(reasons)


def _candidate_icons_for(icon: str, field: str, available_icons: list[str], *, limit: int = 8) -> list[dict[str, Any]]:
    icon = str(icon or "").strip()
    if not icon:
        return []
    rows: list[dict[str, Any]] = []
    for candidate in available_icons:
        if candidate == icon:
            continue
        score, reason = _candidate_icon_score(icon, candidate, field)
        if score < 80:
            continue
        rows.append({"icon": candidate, "score": score, "reason": reason})
    rows.sort(key=lambda row: (-int(row["score"]), str(row["icon"])))
    return rows[:limit]


def _icon_prefix_before_number(icon: str) -> str:
    text = str(icon or "").lower()
    match = _ICON_NUMBER_RE.search(text)
    return text[: match.start()] if match else text


def _normalized_icon_prefix(prefix: str) -> str:
    return re.sub(r"_?zw_?", "_", str(prefix or "").lower()).strip("_")


def _nearby_icons_for(icon: str, available_icons: list[str], *, limit: int = 12, max_delta: int = 6) -> list[dict[str, Any]]:
    icon = str(icon or "").strip()
    target_number = _icon_number_token(icon)
    if not icon or not target_number:
        return []
    target_value = int(target_number)
    target_prefix = _icon_prefix_before_number(icon)
    normalized_target_prefix = _normalized_icon_prefix(target_prefix)
    rows: list[dict[str, Any]] = []
    for candidate in available_icons:
        if candidate == icon:
            continue
        candidate_number = _icon_number_token(candidate)
        if not candidate_number:
            continue
        delta = abs(int(candidate_number) - target_value)
        if delta <= 0 or delta > max_delta:
            continue
        candidate_prefix = _icon_prefix_before_number(candidate)
        normalized_candidate_prefix = _normalized_icon_prefix(candidate_prefix)
        reason = ""
        if candidate_prefix == target_prefix:
            reason = "同前缀近邻"
        elif normalized_candidate_prefix == normalized_target_prefix:
            reason = "去zw后同前缀近邻"
        elif candidate.startswith(target_prefix.split("_", 1)[0] + "_item_") and "_item_" in target_prefix:
            reason = "同item图集近邻"
        if not reason:
            continue
        rows.append({"icon": candidate, "number_delta": delta, "reason": reason})
    rows.sort(key=lambda row: (int(row["number_delta"]), str(row["icon"])))
    return rows[:limit]


def _name_prefix(name: str) -> str:
    normalized = str(name or "").strip()
    if not normalized:
        return ""
    if "·" in normalized:
        return normalized.split("·", 1)[0]
    match = _NAME_PREFIX_RE.search(normalized)
    if match:
        return normalized[: match.start()].strip()
    return normalized[:4]


def _dominant(counter: Counter[str], total: int) -> tuple[str, int, float]:
    if not counter or total <= 0:
        return "", 0, 0.0
    name, count = counter.most_common(1)[0]
    return name, count, count / total


def _group_review_hint(
    field: str,
    icon: str,
    total: int,
    type_counter: Counter[str],
    _sub_type_counter: Counter[str],
    companion_counter: Counter[str],
) -> tuple[str, str, str]:
    dominant_type, dominant_count, dominant_ratio = _dominant(type_counter, total)
    type_count = len(type_counter)
    companion_count = len(companion_counter)
    icon_lower = icon.lower()

    if field == "small_icon":
        if icon_lower.startswith("common_corner_"):
            return "low", "角标类小图标", "小图标像角标/品质/技能角标，通常不应替换主图；优先确认 UI 是否按主图+角标组合展示。"
        if icon_lower.startswith("mainui_icon_zw_"):
            return "high" if type_count >= 4 else "medium", "跨类型小图标", "小图标跨多个道具类型复用；优先判断它是否只是系统分类角标，不要当作缺主图自动替换。"
        return "medium", "小图标高复用", "小图标被大量复用；需要结合主图和类型判断是否为正常角标。"

    if dominant_ratio >= 0.95:
        return "medium", "单一类型通用主图", f"主图几乎只用于“{dominant_type}”类型；可能是游戏配置的类型通用图，优先决定是否标记豁免。"
    if type_count >= 4 or companion_count >= 12:
        return "high", "跨类型主图混用", "主图跨多个类型或伴随小图标高度分散；优先回查子类/详情源，寻找更具体图标或确认配置缺口。"
    if dominant_count:
        return "high", "主类型混用主图", f"主图主要用于“{dominant_type}”，但仍混有其他类型；优先检查非主类型样本是否误用。"
    return "medium", "主图高复用", "主图被大量复用；需要人工复核是否是通用配置图。"


def _suggested_manual_action_for_no_candidate(row: dict[str, Any]) -> str:
    hint = str(row.get("review_hint") or "")
    priority = str(row.get("review_priority") or "")
    if "单一类型通用" in hint:
        return "review_exemption_candidate"
    if priority == "high":
        return "continue_static_asset_search"
    return "manual_visual_compare"


def _build_group_row(field: str, icon: str, cards: list[dict[str, Any]], threshold: int, available_icons: list[str]) -> dict[str, Any]:
    sorted_cards = sorted(cards, key=lambda card: (str(card.get("type_name") or ""), str(card.get("sub_type_name") or ""), int(card.get("id") or 0)))
    total = len(sorted_cards)
    type_counter = Counter(str(card.get("type_name") or "类型未知") for card in sorted_cards)
    sub_type_counter = Counter(str(card.get("type_sub_type_name") or card.get("sub_type_name") or "子类未知") for card in sorted_cards)
    quality_counter = Counter(str(card.get("quality_name") or "品质未知") for card in sorted_cards)
    source_counter = Counter(str(card.get("source_table") or "来源未知") for card in sorted_cards)
    name_prefix_counter = Counter(_name_prefix(str(card.get("name") or "")) for card in sorted_cards if _name_prefix(str(card.get("name") or "")))
    companion_field = "small_icon" if field == "icon" else "icon"
    companion_counter = Counter(str(card.get(companion_field) or "") for card in sorted_cards if str(card.get(companion_field) or "").strip())
    dominant_type, dominant_type_count, dominant_type_ratio = _dominant(type_counter, total)
    review_priority, review_hint, recommended_action = _group_review_hint(field, icon, total, type_counter, sub_type_counter, companion_counter)
    risk = "high_reuse_primary_icon" if field == "icon" else "high_reuse_small_icon"
    representative_samples = _sample_cards(sorted_cards)
    candidate_icons = _candidate_icons_for(icon, field, available_icons)
    nearby_icons = _nearby_icons_for(icon, available_icons)
    return {
        "field": field,
        "icon": icon,
        "risk": risk,
        "count": total,
        "threshold": threshold,
        "review_priority": review_priority,
        "review_hint": review_hint,
        "recommended_action": recommended_action,
        "dominant_type": dominant_type,
        "dominant_type_count": dominant_type_count,
        "dominant_type_ratio": f"{dominant_type_ratio:.3f}",
        "type_count": len(type_counter),
        "type_summary": _counter_summary(type_counter),
        "sub_type_count": len(sub_type_counter),
        "sub_type_summary": _counter_summary(sub_type_counter),
        "quality_summary": _counter_summary(quality_counter),
        "source_summary": _counter_summary(source_counter),
        "name_prefix_count": len(name_prefix_counter),
        "name_prefix_summary": _counter_summary(name_prefix_counter),
        "companion_field": companion_field,
        "companion_icon_count": len(companion_counter),
        "companion_icon_summary": _counter_summary(companion_counter),
        "samples": _sample_labels(sorted_cards),
        "samples_json": json.dumps(representative_samples, ensure_ascii=False, separators=(",", ":")),
        "representative_samples": representative_samples,
        "candidate_icon_count": len(candidate_icons),
        "candidate_icons": candidate_icons,
        "candidate_icons_json": json.dumps(candidate_icons, ensure_ascii=False, separators=(",", ":")),
        "nearby_icon_count": len(nearby_icons),
        "nearby_icons": nearby_icons,
        "nearby_icons_json": json.dumps(nearby_icons, ensure_ascii=False, separators=(",", ":")),
    }


def _build_review_rows(cards: list[dict[str, Any]], threshold: int, available_icons: list[str] | None = None) -> list[dict[str, Any]]:
    available_icons = available_icons or []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        icon = str(card.get("icon") or "").strip()
        if icon and int(card.get("icon_reuse_count") or 0) >= threshold:
            grouped[("icon", icon)].append(card)
        small_icon = str(card.get("small_icon") or "").strip()
        if small_icon and int(card.get("small_icon_reuse_count") or 0) >= threshold:
            grouped[("small_icon", small_icon)].append(card)

    rows = [
        _build_group_row(field, icon, group_cards, threshold, available_icons)
        for (field, icon), group_cards in grouped.items()
        if len(group_cards) >= threshold
    ]
    priority_order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda row: (priority_order.get(str(row["review_priority"]), 9), -int(row["count"]), str(row["field"]), str(row["icon"])))
    return rows


def _review_fieldnames() -> list[str]:
    return [
        "field",
        "icon",
        "risk",
        "count",
        "threshold",
        "review_priority",
        "review_hint",
        "recommended_action",
        "dominant_type",
        "dominant_type_count",
        "dominant_type_ratio",
        "type_count",
        "type_summary",
        "sub_type_count",
        "sub_type_summary",
        "quality_summary",
        "source_summary",
        "name_prefix_count",
        "name_prefix_summary",
        "companion_field",
        "companion_icon_count",
        "companion_icon_summary",
        "samples",
        "samples_json",
        "candidate_icon_count",
        "candidate_icons_json",
        "nearby_icon_count",
        "nearby_icons_json",
    ]


def _summary_for_rows(rows: list[dict[str, Any]], cards: list[dict[str, Any]], catalog_path: Path, output_dir: Path, threshold: int) -> dict[str, Any]:
    priority_counts = Counter(str(row["review_priority"]) for row in rows)
    field_counts = Counter(str(row["field"]) for row in rows)
    candidate_group_count = sum(1 for row in rows if int(row.get("candidate_icon_count") or 0) > 0)
    candidate_icon_total = sum(int(row.get("candidate_icon_count") or 0) for row in rows)
    nearby_context_group_count = sum(1 for row in rows if int(row.get("nearby_icon_count") or 0) > 0)
    nearby_context_icon_total = sum(int(row.get("nearby_icon_count") or 0) for row in rows)
    no_candidate_groups = [
        {
            "field": row["field"],
            "icon": row["icon"],
            "count": row["count"],
            "review_priority": row["review_priority"],
            "review_hint": row["review_hint"],
            "type_summary": row["type_summary"],
            "sample": (row.get("representative_samples") or [{}])[0],
            "nearby_icon_count": row.get("nearby_icon_count", 0),
            "nearby_icons": row.get("nearby_icons") or [],
            "review_status": "pending_manual_decision",
            "suggested_manual_action": _suggested_manual_action_for_no_candidate(row),
            "recommended_action": row.get("recommended_action") or "",
            "remaining_risk": "No same-number or same-family exported asset candidate was found; keep as a pending manual replacement or exemption decision.",
        }
        for row in rows
        if int(row.get("candidate_icon_count") or 0) <= 0
    ]
    unresolved_no_candidate_group_count = sum(
        1 for row in no_candidate_groups if row.get("review_status") == "pending_manual_decision"
    )
    return {
        "catalog_path": str(catalog_path),
        "output_dir": str(output_dir),
        "threshold": threshold,
        "item_count": len(cards),
        "group_count": len(rows),
        "primary_group_count": field_counts.get("icon", 0),
        "small_group_count": field_counts.get("small_icon", 0),
        "high_priority_count": priority_counts.get("high", 0),
        "medium_priority_count": priority_counts.get("medium", 0),
        "low_priority_count": priority_counts.get("low", 0),
        "candidate_group_count": candidate_group_count,
        "candidate_icon_total": candidate_icon_total,
        "no_candidate_group_count": max(0, len(rows) - candidate_group_count),
        "unresolved_no_candidate_group_count": unresolved_no_candidate_group_count,
        "no_candidate_review_status": "pending_manual_decision" if unresolved_no_candidate_group_count else "none",
        "nearby_context_group_count": nearby_context_group_count,
        "nearby_context_icon_total": nearby_context_icon_total,
        "no_candidate_groups": no_candidate_groups[:12],
        "top_groups": [
            {
                "field": row["field"],
                "icon": row["icon"],
                "count": row["count"],
                "review_priority": row["review_priority"],
                "review_hint": row["review_hint"],
                "type_summary": row["type_summary"],
                "candidate_icon_count": row.get("candidate_icon_count", 0),
                "nearby_icon_count": row.get("nearby_icon_count", 0),
            }
            for row in rows[:8]
        ],
    }


def build_item_icon_quality_report(*, export_root: str | Path | None = None, threshold: int = 50) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    catalog_path = root / DEFAULT_ITEM_CATALOG
    if not catalog_path.is_file():
        raise FileNotFoundError(f"item catalog not found: {catalog_path}")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    cards = [card for card in catalog.get("cards") or [] if isinstance(card, dict)]
    rows = _build_review_rows(cards, threshold, _available_icon_names(root))
    output_dir = root / DEFAULT_OUTPUT_DIR
    _write_tsv(output_dir / "item_icon_quality_review_latest.tsv", rows, _review_fieldnames())
    summary = _summary_for_rows(rows, cards, catalog_path, output_dir, threshold)
    contact_sheet = _write_no_candidate_contact_sheet(output_dir, root, summary.get("no_candidate_groups") or [])
    if contact_sheet:
        summary["no_candidate_contact_sheet_path"] = contact_sheet
    (output_dir / "summary_latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown_report(output_dir / "item_icon_quality_review_latest.md", rows, summary)
    return summary


def load_item_icon_quality_review(
    *,
    export_root: str | Path | None = None,
    threshold: int = 50,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / DEFAULT_OUTPUT_DIR
    summary_path = output_dir / "summary_latest.json"
    rows_path = output_dir / "item_icon_quality_review_latest.tsv"
    needs_rebuild = not summary_path.is_file() or not rows_path.is_file()
    if not needs_rebuild and rebuild_missing:
        try:
            current_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            needs_rebuild = True
        else:
            needs_rebuild = int(current_summary.get("threshold") or 0) != int(threshold)
    if rebuild_missing and needs_rebuild:
        build_item_icon_quality_report(export_root=root, threshold=threshold)
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    rows: list[dict[str, Any]] = []
    if rows_path.is_file():
        with rows_path.open("r", encoding="utf-8-sig", newline="") as f:
            for raw_row in csv.DictReader(f, delimiter="\t"):
                row = dict(raw_row)
                try:
                    samples = json.loads(str(row.get("samples_json") or "[]"))
                except json.JSONDecodeError:
                    samples = []
                row["representative_samples"] = samples if isinstance(samples, list) else []
                try:
                    candidates = json.loads(str(row.get("candidate_icons_json") or "[]"))
                except json.JSONDecodeError:
                    candidates = []
                row["candidate_icons"] = candidates if isinstance(candidates, list) else []
                try:
                    nearby = json.loads(str(row.get("nearby_icons_json") or "[]"))
                except json.JSONDecodeError:
                    nearby = []
                row["nearby_icons"] = nearby if isinstance(nearby, list) else []
                rows.append(row)
    return {
        "summary": summary,
        "items": rows,
        "total": len(rows),
        "output_dir": str(output_dir),
    }


def _write_markdown_report(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# Fanxiu Item Icon Quality Review",
        "",
        "This report groups item icons that are heavily reused in `item_catalog`. It is a triage report, not an automatic replacement plan.",
        "",
        f"- Item count: `{summary['item_count']}`",
        f"- Reuse threshold: `{summary['threshold']}`",
        f"- Groups: `{summary['group_count']}` (`primary={summary['primary_group_count']}`, `small={summary['small_group_count']}`)",
        f"- Priority: `high={summary['high_priority_count']}`, `medium={summary['medium_priority_count']}`, `low={summary['low_priority_count']}`",
        f"- Existing-asset candidate groups: `{summary.get('candidate_group_count', 0)}`",
        f"- Existing-asset candidates: `{summary.get('candidate_icon_total', 0)}`",
        f"- Groups without existing-asset candidates: `{summary.get('no_candidate_group_count', 0)}`",
        f"- Nearby icon context: `{summary.get('nearby_context_icon_total', 0)}` icons across `{summary.get('nearby_context_group_count', 0)}` groups",
        "",
        "| Priority | Field | Icon | Count | Candidates | Hint | Types | Recommended action |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows[:40]:
        action = str(row["recommended_action"]).replace("|", "/")
        types = str(row["type_summary"]).replace("|", "/")
        candidates = ", ".join(str(item.get("icon") or "") for item in row.get("candidate_icons") or []) or "-"
        lines.append(
            f"| {row['review_priority']} | {row['field']} | `{row['icon']}` | {row['count']} | {candidates} | {row['review_hint']} | {types} | {action} |"
        )
    no_candidate_groups = summary.get("no_candidate_groups") or []
    if no_candidate_groups:
        lines.extend(
            [
                "",
                "## No Candidate Groups",
                "",
                f"Contact sheet: `{summary.get('no_candidate_contact_sheet_path') or '-'}`",
                "",
                "| Priority | Status | Suggested action | Field | Target | Count | Nearby preview | Nearby names | Hint | Sample | Types | Remaining risk |",
                "| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in no_candidate_groups:
            sample = row.get("sample") if isinstance(row.get("sample"), dict) else {}
            sample_label = f"{sample.get('id')}:{sample.get('name')}".strip(":") if sample else "-"
            types = str(row.get("type_summary") or "").replace("|", "/")
            remaining_risk = str(row.get("remaining_risk") or "").replace("|", "/")
            suggested_action = str(row.get("suggested_manual_action") or "").replace("|", "/")
            nearby_icons = row.get("nearby_icons") or []
            nearby_names = ", ".join(str(item.get("icon") or "") for item in nearby_icons) or "-"
            nearby_preview = "<br>".join(_markdown_icon_preview(str(item.get("icon") or ""), size=28) for item in nearby_icons[:4]) or "-"
            lines.append(
                f"| {row.get('review_priority')} | {row.get('review_status') or '-'} | {suggested_action} | {row.get('field')} | {_markdown_icon_preview(str(row.get('icon') or ''))} | {row.get('count')} | {nearby_preview} | {nearby_names} | {row.get('review_hint')} | {sample_label} | {types} | {remaining_risk} |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
