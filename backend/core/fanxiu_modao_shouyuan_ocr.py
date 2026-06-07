import re
import uuid
from datetime import date
from typing import Any

from backend.core.fanxiu_ocr_utils import _extract_ocr_line_entries, _join_ocr_line_entries, _sanitize_ocr_text


def _extract_first_int_from_text(value: str) -> int | None:
    match = re.search(r"\d+", _sanitize_ocr_text(value))
    if not match:
        return None
    return int(match.group(0))


def _normalize_modao_invasion_item_name(value: str) -> str:
    normalized = _sanitize_ocr_text(value)
    normalized = re.sub(r"(?:活动(?:内)?限购|限购).*$", "", normalized)
    normalized = re.sub(r"^(?:\d+折)?(?:\d+)?", "", normalized)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z]+", "", normalized)
    normalized = re.sub(r"[：:]+$", "", normalized)
    normalized = re.sub(r"^[·•]+|[·•]+$", "", normalized)
    return normalized.strip()


def _is_modao_invasion_non_item_line(value: str) -> bool:
    normalized = _sanitize_ocr_text(value)
    return any(
        token in normalized
        for token in (
            "兑换宝阁",
            "当前拥有位面魔晶",
            "活动期间累计位面魔晶",
            "规则",
        )
    )


def _parse_modao_invasion_header_line(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or "限购" not in joined or _is_modao_invasion_non_item_line(joined):
        return None

    prefix = ""
    purchase_limit = None
    discount_rate = None

    discount_match = re.search(r"(\d+)折", joined)
    if discount_match:
        discount_rate = int(discount_match.group(1))

    matched = re.search(r"^(.*?)(?:活动(?:内)?限购|限购)[:：]?\D*(\d+)", joined)
    if matched:
        prefix = matched.group(1)
        purchase_limit = int(matched.group(2))
    else:
        for index, fragment in enumerate(fragments):
            if "限购" not in fragment:
                continue
            prefix = "".join(fragments[:index]) or re.sub(r"(?:活动(?:内)?限购|限购).*$", "", joined)
            purchase_limit = _extract_first_int_from_text("".join(fragments[index:]))
            break

    name = _normalize_modao_invasion_item_name(prefix)
    if not name or purchase_limit is None:
        return None

    return {
        "name": name,
        "purchase_limit": purchase_limit,
        "discount_rate": discount_rate,
    }


def _extract_modao_invasion_effective_cost(value: str, *, discount_rate: int | None = None) -> int | None:
    normalized = _sanitize_ocr_text(value)
    if not normalized:
        return None

    numeric_groups = re.findall(r"\d+", normalized)
    if len(numeric_groups) >= 2:
        return int(numeric_groups[0])

    if not normalized.isdigit():
        return int(numeric_groups[0]) if numeric_groups else None

    if discount_rate is not None and 1 <= discount_rate <= 9:
        for split_index in range(1, len(normalized)):
            left_text = normalized[:split_index]
            right_text = normalized[split_index:]
            if not left_text or not right_text:
                continue
            left_value = int(left_text)
            right_value = int(right_text)
            if left_value <= 0 or right_value <= 0 or right_value < left_value:
                continue
            if left_value * 10 == right_value * discount_rate:
                return left_value

    return int(normalized)


def _parse_modao_invasion_cost_line(entries: list[dict[str, Any]], *, discount_rate: int | None = None) -> int | None:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or "限购" in joined or _is_modao_invasion_non_item_line(joined):
        return None

    seen_cost_prefix = False
    for fragment in fragments:
        if any(token in fragment for token in ("所需", "所", "需")):
            seen_cost_prefix = True
            remainder = re.sub(r"^.*?(?:所需|所|需)[:：]?", "", fragment)
            value = _extract_modao_invasion_effective_cost(remainder, discount_rate=discount_rate)
            if value is not None:
                return value
            continue

        value = _extract_modao_invasion_effective_cost(fragment, discount_rate=discount_rate)
        if seen_cost_prefix and value is not None:
            return value

    return _extract_modao_invasion_effective_cost(joined, discount_rate=discount_rate)


def _build_modao_invasion_exchange_items_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]

    header_rows: list[tuple[int, dict[str, Any]]] = []
    for index, group in enumerate(line_entries):
        parsed = _parse_modao_invasion_header_line(group)
        if parsed is not None:
            header_rows.append((index, parsed))

    imported_items: list[dict[str, Any]] = []
    for header_index, (line_index, header) in enumerate(header_rows):
        next_line_index = header_rows[header_index + 1][0] if header_index + 1 < len(header_rows) else len(line_entries)
        magic_crystal_cost = None
        for cost_index in range(line_index + 1, next_line_index):
            magic_crystal_cost = _parse_modao_invasion_cost_line(
                line_entries[cost_index],
                discount_rate=header.get("discount_rate"),
            )
            if magic_crystal_cost is not None:
                break

        if magic_crystal_cost is None:
            continue

        imported_items.append(
            {
                "id": str(uuid.uuid4()),
                "name": header["name"],
                "magic_crystal_cost": magic_crystal_cost,
                "purchase_limit": header["purchase_limit"],
            }
        )

    if not imported_items:
        raise ValueError("未能从截图中识别可导入的兑换条目")

    return imported_items, [line for line in lines if line]


def _looks_like_modao_invasion_personal_ranking_line(value: str) -> bool:
    normalized = _sanitize_ocr_text(value)
    return "除魔功" in normalized or "功勋" in normalized


def _extract_last_int_from_text(value: str) -> int | None:
    matches = re.findall(r"\d+", _sanitize_ocr_text(value))
    if not matches:
        return None
    return int(matches[-1])


def _normalize_modao_invasion_personal_ranking_name(value: str) -> str:
    normalized = _sanitize_ocr_text(value)
    normalized = re.sub(r"^\d+", "", normalized)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9&]+", "", normalized)
    normalized = re.sub(r"[：:]+$", "", normalized)
    return normalized.strip()


def _normalize_modao_invasion_personal_ranking_plane(value: str) -> str:
    normalized = _sanitize_ocr_text(value)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9]+", "", normalized)
    return normalized.strip()


def _parse_modao_invasion_personal_ranking_header_line(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or not _looks_like_modao_invasion_personal_ranking_line(joined):
        return None

    matched = re.search(r"^(?P<rank>\d+)?(?P<name>.*?)(?:除魔功勋|除魔功|功勋)[:：]?(?P<merit>\d+)\D*$", joined)
    rank = int(matched.group("rank")) if matched and matched.group("rank") else None
    name = _normalize_modao_invasion_personal_ranking_name(matched.group("name") if matched else "")
    merit = int(matched.group("merit")) if matched else None
    score_label_x = min(
        (float(entry.get("x", 0)) for entry in entries if _looks_like_modao_invasion_personal_ranking_line(str(entry.get("text")))),
        default=None,
    )

    if merit is None:
        merit = _extract_last_int_from_text(joined)
    if merit is None or merit <= 0:
        return None

    if rank is None:
        left_text = "".join(
            _sanitize_ocr_text(entry.get("text"))
            for entry in entries
            if _sanitize_ocr_text(entry.get("text"))
            and (score_label_x is None or float(entry.get("x", 0)) < score_label_x)
        )
        rank = _extract_first_int_from_text(left_text)
    if rank is None or rank <= 0:
        return None

    if not name:
        name_fragments: list[str] = []
        for index, entry in enumerate(entries):
            fragment = _sanitize_ocr_text(entry.get("text"))
            if not fragment:
                continue
            if score_label_x is not None and float(entry.get("x", 0)) >= score_label_x:
                break
            if index == 0:
                fragment = re.sub(r"^\d+", "", fragment)
            if fragment:
                name_fragments.append(fragment)
        name = _normalize_modao_invasion_personal_ranking_name("".join(name_fragments))

    if not name:
        return None

    return {
        "rank": rank,
        "name": name,
        "merit": merit,
    }


def _parse_modao_invasion_personal_ranking_plane_line(entries: list[dict[str, Any]]) -> str:
    fragments = [_sanitize_ocr_text(entry.get("text")) for entry in entries if _sanitize_ocr_text(entry.get("text"))]
    joined = "".join(fragments)
    if not joined or _looks_like_modao_invasion_personal_ranking_line(joined):
        return ""
    return _normalize_modao_invasion_personal_ranking_plane(joined)


def _build_modao_invasion_personal_rankings_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]

    header_rows: list[tuple[int, dict[str, Any]]] = []
    for index, group in enumerate(line_entries):
        parsed = _parse_modao_invasion_personal_ranking_header_line(group)
        if parsed is not None:
            header_rows.append((index, parsed))

    imported_items: list[dict[str, Any]] = []
    for header_index, (line_index, header) in enumerate(header_rows):
        next_line_index = header_rows[header_index + 1][0] if header_index + 1 < len(header_rows) else len(line_entries)
        plane = ""
        for plane_index in range(line_index + 1, next_line_index):
            plane = _parse_modao_invasion_personal_ranking_plane_line(line_entries[plane_index])
            if plane:
                break

        imported_items.append(
            {
                "id": str(uuid.uuid4()),
                "rank": header["rank"],
                "name": header["name"],
                "plane": plane,
                "merit": header["merit"],
            }
        )

    if not imported_items:
        raise ValueError("未能从截图中识别可导入的个人榜名次")

    return imported_items, [line for line in lines if line]


def _extract_shouyuan_exploration_search_count(lines: list[str]) -> int | None:
    counts: list[int] = []
    for line in lines:
        for matched in re.findall(r"(?:第)?(\d+)次探查", _sanitize_ocr_text(line)):
            counts.append(int(matched))
    return max(counts) if counts else None


def _extract_shouyuan_exploration_labeled_total(lines: list[str], keyword: str) -> int | None:
    for line in lines:
        normalized = _sanitize_ocr_text(line)
        if "总共" in normalized and keyword in normalized:
            value = _extract_last_int_from_text(normalized)
            if value is not None:
                return value
    return None


def _extract_shouyuan_exploration_beast_crystal(
    line_entries: list[list[dict[str, Any]]],
    *,
    treasure_line_index: int | None,
    score_line_index: int | None,
) -> int | None:
    if treasure_line_index is None:
        return None

    stop_index = score_line_index if score_line_index is not None else len(line_entries)
    for group in line_entries[treasure_line_index + 1:stop_index]:
        joined = _join_ocr_line_entries(group)
        if "积分" in joined or "功勋" in joined:
            break

        candidates: list[tuple[float, int]] = []
        for entry in group:
            text = _sanitize_ocr_text(entry.get("text"))
            for matched in re.findall(r"\d+", text):
                candidates.append((float(entry.get("x", 0)), int(matched)))
        if candidates:
            return min(candidates, key=lambda item: item[0])[1]

    return None


def _build_shouyuan_exploration_income_speed_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [_join_ocr_line_entries(group) for group in line_entries]

    treasure_line_index = None
    score_line_index = None
    for index, line in enumerate(lines):
        if "总共" in line and "宝物" in line:
            treasure_line_index = index
        if "总共" in line and "积分" in line:
            score_line_index = index

    search_count = _extract_shouyuan_exploration_search_count(lines)
    beast_crystal = _extract_shouyuan_exploration_beast_crystal(
        line_entries,
        treasure_line_index=treasure_line_index,
        score_line_index=score_line_index,
    )
    score = _extract_shouyuan_exploration_labeled_total(lines, "积分")
    merit = _extract_shouyuan_exploration_labeled_total(lines, "功勋")

    missing_fields = [
        label
        for label, value in (
            ("探查次数", search_count),
            ("兽晶", beast_crystal),
            ("积分", score),
            ("功勋", merit),
        )
        if value is None
    ]
    if missing_fields:
        raise ValueError(f"未能从截图中识别收益速度：{'、'.join(missing_fields)}")

    return {
        "id": str(uuid.uuid4()),
        "captured_date": date.today().isoformat(),
        "search_count": search_count,
        "beast_crystal": beast_crystal,
        "score": score,
        "merit": merit,
        "remark": "",
    }, [line for line in lines if line]
