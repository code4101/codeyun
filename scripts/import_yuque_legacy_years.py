from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.import_yuque_journal import (
    BOOK_ID,
    CATEGORY_HINTS,
    IMPORT_SOURCE,
    TZ,
    USER_ID,
    MediaLocalizer,
    attachments_dir,
    content_html,
    custom_field_value,
    custom_fields_base,
    db_path,
    default_data_dir,
    edge_exists,
    get_json,
    insert_edge,
    insert_node,
    local_iso,
    node_path,
    note_categories,
    plain_excerpt,
    safe_json_loads,
    text_of,
    timestamp_from_iso,
    timestamp_from_week,
    yuque_session,
)
from backend.core.yuque_html import normalize_legacy_yuque_lake_html


LEGACY_ROOT_DOC_IDS = [
    "90041302",  # 2011 莫失莫忘
    "16327335",  # 2011 学习委员
    "16327331",  # 2012 c语言
    "16327325",  # 2013 c++
    "16327313",  # 2014 ACM算法竞赛
    "16327295",  # 2015 铁塔
]

YEAR_RANGE = range(2011, 2016)
WEEK_RE = re.compile(r"\bw(\d{6})\b", re.I)
DATE8_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
CH_RE = re.compile(r"\b(ch\d+)\b\s*(.*)", re.I)
WEEK_SUMMARY_LABELS = {"周内容", "内容", "主题"}
WEEKDAY_OFFSETS = {
    "周一": 0,
    "星期一": 0,
    "周二": 1,
    "星期二": 1,
    "周三": 2,
    "星期三": 2,
    "周四": 3,
    "星期四": 3,
    "周五": 4,
    "星期五": 4,
    "周六": 5,
    "星期六": 5,
    "周日": 6,
    "周天": 6,
    "星期日": 6,
    "星期天": 6,
}


def default_output_dir() -> Path:
    return Path(os.environ["TEMP"]) / "codeyun_yuque_2011_2015_legacy_full"


def clean_cell_text(value: str) -> str:
    text = (value or "").replace("\u200b", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_anchor(value: str) -> str:
    text = re.sub(r"\s+", "-", value.strip().lower())
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "", text)
    return text[:80] or hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def local_datetime_from_date(date_text: str, hour: int = 12) -> float:
    year, month, day = map(int, date_text.split("-"))
    return dt.datetime(year, month, day, hour, 0, 0, tzinfo=TZ).timestamp()


def monday_week_code(date_text: str) -> str | None:
    try:
        value = dt.date.fromisoformat(date_text)
    except ValueError:
        return None
    monday = value - dt.timedelta(days=value.weekday())
    return f"{monday.year % 100:02d}{monday.month:02d}{monday.day:02d}"


def infer_date_from_title(title: str, fallback_year: int | None = None) -> str | None:
    text = title or ""
    if fallback_year == 2011 and "高考成绩" in text:
        return "2011-06-25"
    if fallback_year == 2011 and "同学录" in text:
        return "2011-05-02"

    match = WEEK_RE.search(text)
    if match:
        week = match.group(1)
        try:
            return dt.date(2000 + int(week[:2]), int(week[2:4]), int(week[4:6])).isoformat()
        except ValueError:
            pass

    match = DATE8_RE.search(text)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            pass

    match = re.search(r"(?<!\d)(20\d{2})[-./年](\d{1,2})(?:[-./月](\d{1,2}))?", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or 1)
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            pass

    if fallback_year:
        match = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text)
        if match:
            month, day = map(int, match.groups())
            try:
                return dt.date(fallback_year, month, day).isoformat()
            except ValueError:
                pass
        if str(fallback_year) in text:
            return f"{fallback_year}-01-01"
    return None


def infer_child_date(title: str, excerpt: str, fallback_year: int | None = None) -> str | None:
    title_date = infer_date_from_title(title, fallback_year)
    if title_date:
        return title_date
    excerpt_date = infer_date_from_title(excerpt[:1000], fallback_year)
    if excerpt_date:
        return excerpt_date
    if fallback_year:
        return f"{fallback_year}-01-01"
    return None


def year_from_week(week: str) -> int:
    return 2000 + int(week[:2])


def is_legacy_week(week: str) -> bool:
    return year_from_week(week) in YEAR_RANGE


def table_context(table: Tag) -> tuple[str, list[str]]:
    heading = ""
    current = table
    while current:
        current = current.find_previous(["h1", "h2", "h3"])
        if not current:
            break
        title = clean_cell_text(text_of(current))
        if CH_RE.search(title):
            heading = title
            break
        if title and not heading:
            heading = title
            break

    first_row = table.find("tr")
    headers: list[str] = []
    if first_row:
        headers = [clean_cell_text(text_of(cell)) for cell in first_row.find_all(["td", "th"])]
        if headers and CH_RE.search(headers[0]):
            heading = headers[0]
    return heading, headers


def cell_body_html(cell: Tag) -> str:
    body = "".join(str(child) for child in cell.contents).strip()
    body = body.replace("\u200b", "")
    return body.strip()


def clean_fragment_html(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    normalized = normalize_legacy_yuque_lake_html(text).strip()
    return "" if normalized == "<p><br></p>" else normalized


def render_week_row_table(week: str, headers: list[str], cell_htmls: list[str]) -> str:
    width = max(len(headers), len(cell_htmls), 1)
    header_parts: list[str] = []
    body_parts: list[str] = []
    for index in range(width):
        label = clean_cell_text(headers[index]) if index < len(headers) else f"字段{index}"
        header_parts.append(f"<th>{html.escape(label or f'字段{index}')}</th>")
        body_parts.append(f"<td>{cell_htmls[index] if index < len(cell_htmls) else ''}</td>")
    return (
        f"<h2>w{week}</h2>"
        "<table><thead><tr>"
        + "".join(header_parts)
        + "</tr></thead><tbody><tr>"
        + "".join(body_parts)
        + "</tr></tbody></table>"
    )


def date_from_weekday(week: str, label: str) -> str:
    monday = dt.date(2000 + int(week[:2]), int(week[2:4]), int(week[4:6]))
    return (monday + dt.timedelta(days=WEEKDAY_OFFSETS[label])).isoformat()


def week_row_items(headers: list[str], cells: list[Tag]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for index, cell in enumerate(cells[1:], 1):
        label = clean_cell_text(headers[index]) if index < len(headers) else f"字段{index}"
        value_text = clean_cell_text(text_of(cell))
        value_html = clean_fragment_html(cell_body_html(cell))
        if not value_text and not cell.find(["img", "a", "pre", "code"]):
            continue
        items.append({
            "label": label or f"字段{index}",
            "text": value_text,
            "html": value_html or html.escape(value_text),
        })
    return items


def render_week_summary_content(week: str, summary_item: dict[str, str] | None) -> str:
    if not summary_item:
        return f"<h2>w{week}</h2>"
    return render_week_row_table(
        week,
        ["周 ID", summary_item["label"]],
        [html.escape(f"w{week}"), summary_item["html"]],
    )


def week_summary_from_items(items: list[dict[str, str]]) -> str:
    summary_item = next((item for item in items if item["label"] in WEEK_SUMMARY_LABELS and item["text"]), None)
    if not summary_item:
        return ""
    summary = clean_cell_text(summary_item["text"])
    if len(summary) > 60:
        summary = summary[:57].rstrip() + "..."
    return summary


def legacy_items_from_week_content(content: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(content or "", "html.parser")
    table = soup.find("table")
    if table:
        rows = table.find_all("tr")
        if len(rows) >= 2:
            headers = [clean_cell_text(text_of(cell)) for cell in rows[0].find_all(["td", "th"])]
            values = rows[1].find_all(["td", "th"])
            items: list[dict[str, str]] = []
            for index, cell in enumerate(values[1:], 1):
                label = headers[index] if index < len(headers) else f"字段{index}"
                value_text = clean_cell_text(text_of(cell))
                value_html = clean_fragment_html(cell_body_html(cell))
                if value_text or value_html:
                    items.append({"label": label or f"字段{index}", "text": value_text, "html": value_html or html.escape(value_text)})
            return items

    items = []
    for index, li in enumerate(soup.find_all("li"), 1):
        strong = li.find("strong")
        label = clean_cell_text(text_of(strong)) if strong else f"字段{index}"
        if strong:
            strong.extract()
        value_html = clean_fragment_html("".join(str(child) for child in li.contents).strip())
        value_text = clean_cell_text(text_of(li))
        if value_html or value_text:
            items.append({"label": label, "text": value_text, "html": value_html or html.escape(value_text)})
    return items


def legacy_day_candidates_from_items(
    *,
    doc_id: str,
    week: str,
    items: list[dict[str, str]],
    root_doc_id: str,
    section_source_key: str,
) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    for item in items:
        label = item["label"]
        if label not in WEEKDAY_OFFSETS:
            continue
        text = item["text"]
        value_html = item["html"]
        if not text and not BeautifulSoup(value_html, "html.parser").find(["img", "a", "pre", "code"]):
            continue
        date = date_from_weekday(week, label)
        fingerprint = hashlib.sha1((doc_id + week + label + text + value_html).encode("utf-8")).hexdigest()[:16]
        title = text or f"w{week} {label}"
        days.append({
            "kind": "legacy_day",
            "doc_id": f"{doc_id}-w{week}-{label}",
            "source_key": f"{BOOK_ID}/{doc_id}#w{week}/{label}",
            "week": week,
            "date": date,
            "day_title": label,
            "title": title,
            "content": f"<h3>{html.escape(date)} {html.escape(label)}</h3>{value_html}",
            "excerpt": text[:900],
            "fingerprint": fingerprint,
            "root_doc_id": root_doc_id,
            "section_source_key": section_source_key,
            "group_week": week,
        })
    return days


def legacy_day_candidates_from_week_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    week = item.get("week") or ""
    if not week:
        return []
    root_doc_id = item.get("root_doc_id") or str(item.get("doc_id") or "").split("-w", 1)[0]
    days = legacy_day_candidates_from_items(
        doc_id=str(root_doc_id),
        week=str(week),
        items=legacy_items_from_week_content(item.get("content") or ""),
        root_doc_id=str(root_doc_id),
        section_source_key=item.get("section_source_key") or "",
    )
    for day in days:
        day["parent_source_key"] = item.get("source_key") or f"{BOOK_ID}/{root_doc_id}#w{week}"
        day["source_import"] = item.get("source_import") or "codex-cli-yuque-legacy-2011-2015"
    return days


def week_row_content(week: str, headers: list[str], cells: list[Tag]) -> tuple[str, str, str]:
    items = week_row_items(headers, cells)
    summary_item = next((item for item in items if item["label"] in WEEK_SUMMARY_LABELS and item["text"]), None)
    return week_summary_from_items(items), render_week_summary_content(week, summary_item), " ".join(item["text"] for item in items)


def split_table_doc(doc: dict[str, Any], doc_id: str, root_title: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str]:
    html_body = content_html(doc)
    soup = BeautifulSoup(html_body, "html.parser")
    sections: list[dict[str, Any]] = []
    weeks: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    section_by_title: dict[str, dict[str, Any]] = {}

    def get_section(title: str, first_week: str | None = None) -> dict[str, Any] | None:
        title = clean_cell_text(title)
        if not title or not CH_RE.search(title):
            return None
        if title in section_by_title:
            if first_week and not section_by_title[title].get("week"):
                section_by_title[title]["week"] = first_week
            return section_by_title[title]
        anchor = safe_anchor(title)
        item = {
            "kind": "section",
            "doc_id": f"{doc_id}-{anchor}",
            "source_key": f"{BOOK_ID}/{doc_id}#{anchor}",
            "week": first_week or "",
            "title": title,
            "content": f"<h1>{html.escape(title)}</h1><p>来自《{html.escape(root_title)}》的章节标题。</p>",
            "excerpt": title,
            "root_doc_id": doc_id,
            "group_week": first_week or "",
        }
        section_by_title[title] = item
        sections.append(item)
        return item

    for table in soup.find_all("table"):
        section_title, headers = table_context(table)
        current_section = get_section(section_title)
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            row_text = clean_cell_text(text_of(row))
            match = WEEK_RE.search(row_text)
            if not match:
                continue
            week = match.group(1)
            if not is_legacy_week(week):
                continue
            summary, body, text = week_row_content(week, headers, cells)
            if not text and not BeautifulSoup(body, "html.parser").find(["img", "a", "pre", "code"]):
                continue
            if current_section and not current_section.get("week"):
                current_section["week"] = week
                current_section["group_week"] = week
            row_items = week_row_items(headers, cells)
            fingerprint = hashlib.sha1((doc_id + week + text).encode("utf-8")).hexdigest()[:16]
            section_source_key = current_section["source_key"] if current_section else ""
            weeks.append({
                "kind": "week_parent",
                "doc_id": f"{doc_id}-w{week}",
                "source_key": f"{BOOK_ID}/{doc_id}#w{week}",
                "week": week,
                "title": f"w{week}: {summary}" if summary else f"w{week}",
                "content": body,
                "excerpt": text[:900],
                "fingerprint": fingerprint,
                "root_doc_id": doc_id,
                "section_source_key": section_source_key,
                "group_week": week,
            })
            days.extend(legacy_day_candidates_from_items(
                doc_id=doc_id,
                week=week,
                items=row_items,
                root_doc_id=doc_id,
                section_source_key=section_source_key,
            ))
    return sections, weeks, days, html_body


def build_catalog_maps(catalog: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    by_uuid = {node.get("uuid"): node for node in catalog if node.get("uuid")}
    by_doc_id = {str(node.get("doc_id")): node for node in catalog if node.get("doc_id")}
    children: dict[str, list[dict[str, Any]]] = {}
    for node in catalog:
        children.setdefault(node.get("parent_uuid") or "", []).append(node)
    return by_uuid, children, by_doc_id


def descendants(node: dict[str, Any], children: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    stack = list(children.get(node.get("uuid"), []))
    while stack:
        child = stack.pop(0)
        result.append(child)
        stack[0:0] = children.get(child.get("uuid"), [])
    return result


def root_year_for_title(title: str, fallback: int | None = None) -> int | None:
    match = re.search(r"(20\d{2})", title or "")
    if match:
        return int(match.group(1))
    return fallback


def make_chapter_content(title: str, html_body: str, week_count: int, section_count: int) -> str:
    soup = BeautifulSoup(html_body, "html.parser")
    for table in soup.find_all("table"):
        table.decompose()
    intro = str(soup).strip()
    if plain_excerpt(intro, 80):
        return intro + f"<p>原表格已拆分为 {week_count} 个周节点、{section_count} 个章节节点。</p>"
    return (
        f"<h1>{html.escape(title)}</h1>"
        f"<p>语雀年度表已拆分为 {week_count} 个周节点、{section_count} 个章节节点。</p>"
    )


def prepare(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    session = yuque_session()
    catalog = get_json(session, "https://www.yuque.com/api/catalog_nodes", book_id=BOOK_ID)
    by_uuid, children, by_doc_id = build_catalog_maps(catalog)

    selected_doc_ids: set[str] = set(LEGACY_ROOT_DOC_IDS)
    for root_id in LEGACY_ROOT_DOC_IDS:
        root = by_doc_id.get(root_id)
        if not root:
            continue
        for child in descendants(root, children):
            if child.get("type") == "DOC" and child.get("doc_id"):
                selected_doc_ids.add(str(child.get("doc_id")))

    docs: dict[str, dict[str, Any]] = {}
    for index, doc_id in enumerate(sorted(selected_doc_ids, key=int), 1):
        if index == 1 or index % 10 == 0 or index == len(selected_doc_ids):
            print(f"fetch docs {index}/{len(selected_doc_ids)}")
        docs[doc_id] = get_json(session, f"https://www.yuque.com/api/docs/{doc_id}", book_id=BOOK_ID)

    candidates: list[dict[str, Any]] = []
    plan_items: list[dict[str, Any]] = []
    root_source_by_doc: dict[str, str] = {}
    weeks_by_code: dict[str, dict[str, Any]] = {}
    weeks_by_root_code: dict[tuple[str, str], dict[str, Any]] = {}

    for root_id in LEGACY_ROOT_DOC_IDS:
        node = by_doc_id[root_id]
        doc = docs[root_id]
        title = node.get("title") or doc.get("title") or root_id
        sections, weeks, days, html_body = split_table_doc(doc, root_id, title)
        first_week = weeks[0]["week"] if weeks else ""
        root_source_key = f"{BOOK_ID}/{root_id}"
        root_source_by_doc[root_id] = root_source_key
        chapter = {
            "kind": "chapter",
            "doc_id": root_id,
            "source_key": root_source_key,
            "week": first_week,
            "title": title,
            "slug": node.get("url") or doc.get("slug"),
            "path": node_path(node, by_uuid),
            "created_at": local_iso(doc.get("created_at")),
            "updated_at": local_iso(doc.get("updated_at") or doc.get("content_updated_at")),
            "content": make_chapter_content(title, html_body, len(weeks), len(sections)),
            "excerpt": plain_excerpt(html_body, 900),
            "group_week": first_week,
        }
        candidates.append(chapter)
        plan_items.append({
            "id": candidate_plan_id(chapter),
            "kind": "chapter",
            "week": first_week,
            "source_title": title,
            "path": chapter["path"],
            "excerpt": chapter["excerpt"][:800],
        })

        for section in sections:
            section["parent_source_key"] = root_source_key
            candidates.append(section)
            plan_items.append({
                "id": candidate_plan_id(section),
                "kind": "section",
                "week": section.get("week") or first_week,
                "source_title": section["title"],
                "path": chapter["path"],
                "excerpt": section["excerpt"],
            })

        for week in weeks:
            week["parent_source_key"] = week.get("section_source_key") or root_source_key
            candidates.append(week)
            weeks_by_code.setdefault(week["week"], week)
            weeks_by_root_code.setdefault((root_id, week["week"]), week)
            plan_items.append({
                "id": candidate_plan_id(week),
                "kind": "week_parent",
                "week": week["week"],
                "source_title": week["title"],
                "path": chapter["path"],
                "excerpt": week["excerpt"][:800],
            })

        for day in days:
            day["parent_source_key"] = day.get("source_key", "").rsplit("/", 1)[0] or root_source_key
            candidates.append(day)
            plan_items.append({
                "id": candidate_plan_id(day),
                "kind": "legacy_day",
                "week": day["week"],
                "date": day["date"],
                "source_title": day["title"],
                "path": chapter["path"],
                "excerpt": day["excerpt"][:800],
            })

    root_by_descendant: dict[str, str] = {}
    for root_id in LEGACY_ROOT_DOC_IDS:
        root = by_doc_id[root_id]
        root_by_descendant[root_id] = root_id
        for child in descendants(root, children):
            if child.get("type") == "DOC" and child.get("doc_id"):
                root_by_descendant[str(child.get("doc_id"))] = root_id

    for doc_id in sorted(selected_doc_ids - set(LEGACY_ROOT_DOC_IDS), key=int):
        node = by_doc_id.get(doc_id)
        doc = docs.get(doc_id)
        if not node or not doc:
            continue
        html_body = content_html(doc)
        excerpt = plain_excerpt(html_body, 900)
        if not excerpt and "<img" not in html_body and "<a" not in html_body and "<pre" not in html_body:
            continue
        root_id = root_by_descendant.get(doc_id)
        root_title = (by_doc_id.get(root_id or "") or {}).get("title") or ""
        fallback_year = root_year_for_title(root_title)
        title = node.get("title") or doc.get("title") or doc_id
        inferred_date = infer_child_date(title, excerpt, fallback_year)
        source_key = f"{BOOK_ID}/{doc_id}"
        week = monday_week_code(inferred_date) if inferred_date else ""
        parent_source_key = ""
        root_week = weeks_by_root_code.get((root_id or "", week or "")) if week else None
        if root_week:
            parent_source_key = root_week["source_key"]
        elif root_id:
            parent_source_key = root_source_by_doc.get(root_id, "")
        item = {
            "kind": "child_doc",
            "doc_id": doc_id,
            "source_key": source_key,
            "week": week or "",
            "title": title,
            "slug": node.get("url") or doc.get("slug"),
            "path": node_path(node, by_uuid),
            "created_at": local_iso(doc.get("created_at")),
            "updated_at": local_iso(doc.get("updated_at") or doc.get("content_updated_at")),
            "published_at": local_iso(doc.get("published_at")),
            "date": inferred_date,
            "content": html_body,
            "excerpt": excerpt,
            "parent_source_key": parent_source_key,
            "group_week": week or "",
        }
        candidates.append(item)
        plan_items.append({
            "id": candidate_plan_id(item),
            "kind": "child_doc",
            "week": week or "",
            "date": inferred_date or item["published_at"] or item["created_at"],
            "source_title": title,
            "path": item["path"],
            "excerpt": excerpt[:800],
        })

    payload = {
        "book_id": BOOK_ID,
        "years": [2011, 2012, 2013, 2014, 2015],
        "root_doc_ids": LEGACY_ROOT_DOC_IDS,
        "fetched_doc_ids": sorted(selected_doc_ids, key=int),
        "candidates": candidates,
    }
    (output_dir / "candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    (output_dir / "plan_input.json").write_text(json.dumps({
        "categories": CATEGORY_HINTS,
        "items": plan_items,
    }, ensure_ascii=False, indent=2), "utf-8")
    write_plan_schema(output_dir)
    write_plan_batches(output_dir, plan_items, int(args.batch_size))

    print(json.dumps({
        "output_dir": str(output_dir),
        "fetched_docs": len(selected_doc_ids),
        "candidate_kinds": Counter(item["kind"] for item in candidates),
        "plan_items": len(plan_items),
    }, ensure_ascii=False, indent=2, default=dict))


def write_plan_schema(output_dir: Path) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "title", "category_key", "weight", "note"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "category_key": {"type": "string"},
                        "weight": {"type": "integer", "minimum": 0, "maximum": 3},
                        "note": {"type": "string"},
                    },
                },
            },
        },
    }
    (output_dir / "plan_schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), "utf-8")


def write_plan_batches(output_dir: Path, plan_items: list[dict[str, Any]], batch_size: int) -> None:
    batch_dir = output_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    for old in list(batch_dir.glob("batch_*_input.json")) + list(batch_dir.glob("batch_*_prompt.md")):
        old.unlink()

    for offset in range(0, len(plan_items), batch_size):
        items = plan_items[offset:offset + batch_size]
        batch_no = offset // batch_size + 1
        payload = {"categories": CATEGORY_HINTS, "items": items}
        input_path = batch_dir / f"batch_{batch_no:03d}_input.json"
        prompt_path = batch_dir / f"batch_{batch_no:03d}_prompt.md"
        input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        prompt = f"""你是 CodeYun 星图笔记的语雀旧年度导入助手。请根据输入 JSON，为每个节点生成标题、分类 key、权重和一句内部备注。

硬性要求：
1. 必须为输入 items 中每一个 id 输出一条，不能漏、不能新增。
2. 输出必须严格匹配 schema：{{"items":[{{"id":"...","title":"...","category_key":"...","weight":0,"note":"..."}}]}}。
3. category_key 只能从 categories 的 key 中选择。
4. 权重策略：chapter/section 固定 3；week_parent 固定 2；较重要的独立子文档可设 1；legacy_day 和普通图片/摘录/日记子文档默认 0。不要批量把普通条目设为 1。
5. title 不要出现“Codex”“AI分析”“导入判断”等元信息；正文会使用语雀原文，note 只作内部备注。
6. week_parent 和 legacy_day 标题必须使用输入的 source_title 原样，不要概括、改写、补词；section 保留 ch 编号；chapter 保留年份主题。
7. 旧年度数据横跨高中、大学、C/C++、ACM、铁塔等主题，分类尽量结合内容：编程/算法/工程 -> custom_mmxdyjjkxrsr；AI/模型 -> custom_mmxdhqhnrgup；笔记系统 -> custom_mmx3qpfhinvh；生活/学习/读书/游戏/照片 -> custom_mmxbzxjy85x5；不明确 -> general。

输入 JSON：
```json
{json.dumps(payload, ensure_ascii=False, indent=2)}
```
"""
        prompt_path.write_text(prompt, "utf-8")


def candidate_plan_id(item: dict[str, Any]) -> str:
    kind = item["kind"]
    if kind in {"chapter", "section", "week_parent", "legacy_day", "child_doc"}:
        return f"{kind}:{item['doc_id']}"
    raise ValueError(kind)


def run_plan(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    schema_path = output_dir / "plan_schema.json"
    batch_dir = output_dir / "batches"
    model = args.model
    codex_bin = shutil.which("codex.cmd") or shutil.which("codex") or "codex"

    for prompt_path in sorted(batch_dir.glob("batch_*_prompt.md")):
        batch_no = prompt_path.stem.replace("_prompt", "")
        plan_path = batch_dir / f"{batch_no}_plan.json"
        stdout_path = batch_dir / f"{batch_no}_stdout.log"
        stderr_path = batch_dir / f"{batch_no}_stderr.log"
        if plan_path.exists() and not args.force:
            print(f"skip existing {plan_path.name}")
            continue
        prompt = prompt_path.read_text("utf-8")
        cmd = [
            codex_bin, "exec",
            "-m", model,
            "--ephemeral",
            "--sandbox", "read-only",
            "-C", str(Path.cwd()),
            "--output-schema", str(schema_path),
            "-o", str(plan_path),
            "-",
        ]
        print(f"run codex {prompt_path.name}")
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=int(args.timeout),
        )
        stdout_path.write_text(proc.stdout or "", "utf-8")
        stderr_path.write_text(proc.stderr or "", "utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"codex failed for {prompt_path.name}: {proc.returncode}, see {stderr_path}")
        validate_plan_file(prompt_path.with_name(prompt_path.name.replace("_prompt.md", "_input.json")), plan_path)


def validate_plan_file(input_path: Path, plan_path: Path) -> None:
    payload = json.loads(input_path.read_text("utf-8"))
    expected = {item["id"] for item in payload["items"]}
    plan = json.loads(plan_path.read_text("utf-8"))
    actual = {item.get("id") for item in plan.get("items", [])}
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(f"{plan_path.name} id mismatch missing={missing[:8]} extra={extra[:8]}")


def load_plans(output_dir: Path) -> dict[str, dict[str, Any]]:
    plans: dict[str, dict[str, Any]] = {}
    for path in sorted((output_dir / "batches").glob("batch_*_plan.json")):
        payload = json.loads(path.read_text("utf-8"))
        for item in payload.get("items", []):
            plans[item["id"]] = item
    return plans


def build_existing_maps(con: sqlite3.Connection) -> tuple[dict[str, sqlite3.Row], dict[str, sqlite3.Row]]:
    source_map: dict[str, sqlite3.Row] = {}
    fingerprint_map: dict[str, sqlite3.Row] = {}
    for row in con.execute("select id,title,custom_fields from notenode where user_id=?", (USER_ID,)):
        fields = safe_json_loads(row["custom_fields"], [])
        for key in ("source_doc_key", "source_key"):
            value = custom_field_value(fields, key)
            if value:
                source_map.setdefault(str(value), row)
        fingerprint = custom_field_value(fields, "source_fingerprint")
        if fingerprint:
            fingerprint_map.setdefault(str(fingerprint), row)
    return source_map, fingerprint_map


def normalized_title(kind: str, planned_title: str, original_title: str) -> str:
    if kind in {"week_parent", "legacy_day"}:
        title = clean_cell_text(original_title) or clean_cell_text(planned_title)
    else:
        title = clean_cell_text(planned_title) or original_title
    if kind == "week_parent":
        match = re.match(r"^(w\d{6})\s+([^:：].*)$", title)
        if match:
            title = f"{match.group(1)}: {match.group(2).strip()}"
    return title


def normalized_weight(kind: str, planned_weight: int) -> int:
    if kind in {"chapter", "section"}:
        return 3
    if kind == "week_parent":
        return 2
    return max(0, min(int(planned_weight), 1))


def normalized_week_node_content(week: str, content: str) -> str:
    items = legacy_items_from_week_content(content)
    summary_item = next((item for item in items if item["label"] in WEEK_SUMMARY_LABELS and item["text"]), None)
    return render_week_summary_content(week, summary_item)


def set_custom_field_value(fields_json: str | None, key: str, field_type: str, value: Any) -> str:
    fields = safe_json_loads(fields_json, [])
    for item in fields:
        if isinstance(item, list) and item and item[0] == key:
            if len(item) >= 3:
                item[1] = field_type
                item[2] = value
            return json.dumps(fields, ensure_ascii=False)
    fields.append([key, field_type, value])
    return json.dumps(fields, ensure_ascii=False)


def source_kind(kind: str) -> str:
    return {
        "chapter": "yuque_legacy_chapter",
        "section": "yuque_legacy_section",
        "week_parent": "yuque_legacy_week",
        "legacy_day": "yuque_legacy_day",
        "child_doc": "yuque_legacy_child",
    }[kind]


def import_candidates(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    import_name = args.import_name or "codex-cli-yuque-legacy-2011-2015"
    payload = json.loads((output_dir / "candidates.json").read_text("utf-8"))
    candidates = payload["candidates"]
    plans = load_plans(output_dir)
    missing_plan = [candidate_plan_id(item) for item in candidates if candidate_plan_id(item) not in plans]
    if missing_plan:
        raise RuntimeError(f"{len(missing_plan)} candidates do not have plan, sample: {missing_plan[:10]}")

    backup = Path(tempfile.gettempdir()) / f"codeyun_yuque_legacy_2011_2015_backup_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(db_path(data_dir), backup)

    con = sqlite3.connect(db_path(data_dir), timeout=60)
    con.row_factory = sqlite3.Row
    session = yuque_session()
    media = MediaLocalizer(session, attachments_dir(data_dir), con)
    source_map, fingerprint_map = build_existing_maps(con)

    node_by_source_key = {key: row["id"] for key, row in source_map.items()}
    inserted: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str]] = []
    edges = 0

    order = {"chapter": 0, "section": 1, "week_parent": 2, "legacy_day": 3, "child_doc": 4}
    for item in sorted(candidates, key=lambda obj: (order.get(obj["kind"], 9), obj.get("week") or "", obj.get("date") or "", candidate_plan_id(obj))):
        plan = plans[candidate_plan_id(item)]
        item["source_import"] = import_name
        if item["source_key"] in source_map:
            skipped.append((candidate_plan_id(item), "source_key_exists"))
            node_by_source_key[item["source_key"]] = source_map[item["source_key"]]["id"]
            continue
        if item.get("fingerprint") and item["fingerprint"] in fingerprint_map:
            skipped.append((candidate_plan_id(item), "fingerprint_exists"))
            continue

        category = plan["category_key"] if plan["category_key"] in CATEGORY_HINTS else "general"
        title = normalized_title(item["kind"], plan["title"], item.get("title") or "")
        weight = normalized_weight(item["kind"], int(plan["weight"]))
        content, stats = media.rewrite(item.get("content") or "<p><br></p>")
        fields = custom_fields_base(item, source_kind(item["kind"]), stats)

        if item["kind"] == "week_parent":
            start_at = timestamp_from_week(item["week"], 9)
        elif item.get("date"):
            start_at = local_datetime_from_date(item["date"], 12)
        elif item.get("week"):
            start_at = timestamp_from_week(item["week"], 8)
        else:
            start_at = timestamp_from_iso(item.get("published_at") or item.get("created_at"))

        node_id = insert_node(con, title, content or "<p><br></p>", weight, start_at, category, fields)
        node_by_source_key[item["source_key"]] = node_id
        source_map[item["source_key"]] = {"id": node_id, "title": title}  # type: ignore[assignment]
        inserted.append((candidate_plan_id(item), node_id, title))

        parent_source_key = item.get("parent_source_key") or ""
        if parent_source_key and insert_edge(con, node_by_source_key.get(parent_source_key), node_id):
            edges += 1

    con.commit()
    con.close()
    summary = {
        "backup": str(backup),
        "inserted": len(inserted),
        "skipped": len(skipped),
        "edges": edges,
        "media": media.totals,
        "inserted_titles_sample": [item[2] for item in inserted[:40]],
        "skipped_sample": skipped[:30],
    }
    (output_dir / "import_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def repair_week_nodes(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    payload = json.loads((output_dir / "candidates.json").read_text("utf-8"))
    candidates = payload["candidates"]

    repairs: dict[str, tuple[str, str]] = {}
    day_candidates: dict[str, dict[str, Any]] = {}
    for item in candidates:
        if item.get("kind") != "week_parent":
            continue
        source_key = item["source_key"]
        week = item.get("week") or ""
        summary = week_summary_from_items(legacy_items_from_week_content(item.get("content") or ""))
        title = f"w{week}: {summary}" if summary else f"w{week}"
        content = normalized_week_node_content(week, item.get("content") or "")
        repairs[source_key] = (title, content)
        for day in legacy_day_candidates_from_week_item(item):
            day_candidates.setdefault(day["source_key"], day)

    db = db_path(data_dir)
    con = sqlite3.connect(db, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        source_map, _ = build_existing_maps(con)
        node_by_source_key = {key: row["id"] for key, row in source_map.items()}
        week_updates: list[tuple[str, str, str, str, str, str]] = []
        day_updates: list[tuple[str, str, str, str, str]] = []
        day_inserts: list[dict[str, Any]] = []
        missing: list[str] = []
        edge_inserts = 0

        for source_key, (fallback_title, content) in repairs.items():
            mapped = source_map.get(source_key)
            if not mapped:
                missing.append(source_key)
                continue
            row = con.execute(
                "select id,title,content,custom_fields from notenode where id=? and user_id=?",
                (mapped["id"], USER_ID),
            ).fetchone()
            if not row or "yuque_legacy_week" not in (row["custom_fields"] or ""):
                continue
            title = normalized_title("week_parent", "", fallback_title)
            custom_fields = set_custom_field_value(row["custom_fields"], "source_title", "string", title)
            if row["title"] != title or (row["content"] or "") != content or (row["custom_fields"] or "") != custom_fields:
                week_updates.append((row["id"], row["title"] or "", title, content, custom_fields, source_key))

        for source_key, item in day_candidates.items():
            parent_source_key = item.get("parent_source_key") or ""
            parent_id = node_by_source_key.get(parent_source_key)
            mapped = source_map.get(source_key)
            title = normalized_title("legacy_day", "", item.get("title") or "")
            content = item.get("content") or "<p><br></p>"
            fields = custom_fields_base(item, "yuque_legacy_day", {"images": 0, "attachments": 0})
            if mapped:
                row = con.execute(
                    "select id,title,content,custom_fields from notenode where id=? and user_id=?",
                    (mapped["id"], USER_ID),
                ).fetchone()
                if row and "yuque_legacy_day" in (row["custom_fields"] or ""):
                    if row["title"] != title or (row["content"] or "") != content or (row["custom_fields"] or "") != fields:
                        day_updates.append((row["id"], row["title"] or "", title, content, fields))
                    if parent_id and not edge_exists(con, parent_id, row["id"]):
                        edge_inserts += 1
                continue
            item["_parent_id"] = parent_id
            day_inserts.append(item)
            if parent_id:
                edge_inserts += 1

        summary: dict[str, Any] = {
            "database": str(db),
            "dry_run": bool(args.dry_run),
            "candidate_week_nodes": len(repairs),
            "candidate_day_nodes": len(day_candidates),
            "week_updates": len(week_updates),
            "day_updates": len(day_updates),
            "day_inserts": len(day_inserts),
            "edge_inserts": edge_inserts,
            "missing_count": len(missing),
            "missing_sample": missing[:20],
            "updates_sample": [
                {"source_key": source_key, "old_title": old_title, "new_title": new_title}
                for _node_id, old_title, new_title, _content, _fields, source_key in week_updates[:20]
            ],
            "day_insert_sample": [
                {"source_key": item["source_key"], "date": item.get("date"), "title": item.get("title")}
                for item in day_inserts[:20]
            ],
        }
        if args.dry_run or not (week_updates or day_updates or day_inserts or edge_inserts):
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        backup = Path(tempfile.gettempdir()) / f"codeyun_yuque_legacy_calendar_repair_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(db, backup)
        now = time.time()
        for node_id, _old_title, title, content, custom_fields, _source_key in week_updates:
            con.execute(
                "update notenode set title=?,content=?,custom_fields=?,updated_at=? where id=?",
                (title, content, custom_fields, now, node_id),
            )
        for node_id, _old_title, title, content, custom_fields in day_updates:
            con.execute(
                "update notenode set title=?,content=?,custom_fields=?,updated_at=? where id=?",
                (title, content, custom_fields, now, node_id),
            )
        inserted_days = 0
        inserted_edges = 0
        for item in day_inserts:
            parent_id = item.pop("_parent_id", None)
            parent_row = con.execute("select primary_category,node_type from notenode where id=?", (parent_id,)).fetchone() if parent_id else None
            category = (parent_row["primary_category"] or parent_row["node_type"]) if parent_row else "general"
            node_id = insert_node(
                con,
                normalized_title("legacy_day", "", item.get("title") or ""),
                item.get("content") or "<p><br></p>",
                0,
                local_datetime_from_date(item["date"], 12),
                category,
                custom_fields_base(item, "yuque_legacy_day", {"images": 0, "attachments": 0}),
            )
            inserted_days += 1
            node_by_source_key[item["source_key"]] = node_id
            if parent_id and insert_edge(con, parent_id, node_id):
                inserted_edges += 1
        for source_key, item in day_candidates.items():
            mapped = source_map.get(source_key)
            parent_id = node_by_source_key.get(item.get("parent_source_key") or "")
            if mapped and parent_id and insert_edge(con, parent_id, mapped["id"]):
                inserted_edges += 1
        con.commit()
        summary["backup"] = str(backup)
        summary["updated_weeks"] = len(week_updates)
        summary["updated_days"] = len(day_updates)
        summary["inserted_days"] = inserted_days
        summary["inserted_edges"] = inserted_edges
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        con.close()


def validate(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    payload = json.loads((output_dir / "candidates.json").read_text("utf-8"))
    planned_source_keys = {item["source_key"] for item in payload["candidates"]}
    db = db_path(data_dir)
    attach_dir = attachments_dir(data_dir)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    source_map, _ = build_existing_maps(con)
    missing = sorted(key for key in planned_source_keys if key not in source_map)

    imported_rows = con.execute(
        "select title,start_at,weight,content from notenode where user_id=? and custom_fields like ?",
        (USER_ID, "%codex-cli-yuque-legacy-2011-2015%"),
    ).fetchall()
    year_counts: dict[str, int] = {}
    external_images = []
    missing_files = []
    local_refs = 0
    for row in imported_rows:
        try:
            year = dt.datetime.fromtimestamp(float(row["start_at"]), TZ).strftime("%Y")
            year_counts[year] = year_counts.get(year, 0) + 1
        except Exception:
            pass
        soup = BeautifulSoup(row["content"] or "", "html.parser")
        for tag, attr in [(image, "src") for image in soup.find_all("img")] + [(link, "href") for link in soup.find_all("a")]:
            value = tag.get(attr) or ""
            if value.startswith("http://") or value.startswith("https://") or value.startswith("//"):
                external_images.append((row["title"], value[:140]))
            if value.startswith("/static/attachments/"):
                local_refs += 1
                if not (attach_dir / Path(value).name).exists():
                    missing_files.append((row["title"], value))

    weights = con.execute(
        "select weight,count(*) c from notenode where user_id=? and custom_fields like ? group by weight order by weight",
        (USER_ID, "%codex-cli-yuque-legacy-2011-2015%"),
    ).fetchall()
    con.close()
    print(json.dumps({
        "planned_source_keys": len(planned_source_keys),
        "missing_source_key_count": len(missing),
        "missing_source_keys_sample": missing[:20],
        "imported_nodes": len(imported_rows),
        "year_counts": year_counts,
        "weight_distribution": [dict(row) for row in weights],
        "local_refs": local_refs,
        "external_ref_count": len(external_images),
        "external_refs_sample": external_images[:10],
        "missing_file_count": len(missing_files),
        "missing_files_sample": missing_files[:10],
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--output-dir", default="")
    prepare_parser.add_argument("--batch-size", type=int, default=36)
    prepare_parser.set_defaults(func=prepare)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--output-dir", default="")
    plan_parser.add_argument("--model", default="gpt-5.5")
    plan_parser.add_argument("--timeout", type=int, default=1200)
    plan_parser.add_argument("--force", action="store_true")
    plan_parser.set_defaults(func=run_plan)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--data-dir", default="")
    import_parser.add_argument("--output-dir", default="")
    import_parser.add_argument("--import-name", default="")
    import_parser.set_defaults(func=import_candidates)

    repair_parser = subparsers.add_parser("repair-week-nodes")
    repair_parser.add_argument("--data-dir", default="")
    repair_parser.add_argument("--output-dir", default="")
    repair_parser.add_argument("--dry-run", action="store_true")
    repair_parser.set_defaults(func=repair_week_nodes)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--data-dir", default="")
    validate_parser.add_argument("--output-dir", default="")
    validate_parser.set_defaults(func=validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
