from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import requests
import win32crypt
from bs4 import BeautifulSoup, NavigableString, Tag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.yuque_html import normalize_legacy_yuque_lake_html


BOOK_ID = "24363220"
USER_ID = 2
TZ = dt.timezone(dt.timedelta(hours=8))
IMPORT_SOURCE = "yuque-desktop-cache"
CATEGORY_HINTS = {
    "custom_mmxdyjjkxrsr": "pyxllib",
    "custom_mmxdhqhnrgup": "AI",
    "custom_mmxdcghtzcw7": "CodeYun/综合",
    "custom_mmx3qpfhinvh": "CodeYun/笔记",
    "custom_mmxbzxjy85x5": "后勤",
    "legacy_color_e6a23c": "考勤",
    "legacy_color_67c23a": "凡修",
    "legacy_color_8e44ad": "重点",
    "legacy_color_f56c6c": "docx2json",
    "project": "项目",
    "task": "任务",
    "bug": "缺陷",
    "general": "综合",
}


def default_data_dir() -> Path:
    return Path(os.environ.get("CODEYUN_DATA_DIR", r"D:\home\chenkunze\data\m2603codeyun\codepc_mf"))


def default_output_dir(year: int) -> Path:
    return Path(os.environ["TEMP"]) / f"codeyun_yuque_{year}_full"


def db_path(data_dir: Path) -> Path:
    return data_dir / "codeyun.db"


def attachments_dir(data_dir: Path) -> Path:
    return data_dir / "attachments"


def decrypt_yuque_cookies() -> list[tuple[str, str, str, str, bool]]:
    root = Path(os.environ["APPDATA"]) / "yuque-desktop"
    local_state = root / "Local State"
    cookie_db = root / "Network" / "Cookies"
    state = json.loads(local_state.read_text("utf-8"))
    encrypted_key = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]
    key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]

    tmp = Path(tempfile.gettempdir()) / f"yuque_cookies_{uuid.uuid4().hex}.sqlite"
    shutil.copy2(cookie_db, tmp)
    con = sqlite3.connect(tmp)
    rows = con.execute(
        "select host_key,name,path,encrypted_value,value,is_secure "
        "from cookies where host_key like '%yuque.com%'"
    ).fetchall()
    con.close()
    tmp.unlink(missing_ok=True)

    cookies: list[tuple[str, str, str, str, bool]] = []
    for host, name, path, encrypted_value, value, is_secure in rows:
        cookie_value = value or ""
        if not cookie_value and encrypted_value:
            data = bytes(encrypted_value)
            try:
                if data.startswith((b"v10", b"v11")):
                    cookie_value = AESGCM(key).decrypt(data[3:15], data[15:], None).decode("utf-8")
                else:
                    cookie_value = win32crypt.CryptUnprotectData(data, None, None, None, 0)[1].decode("utf-8")
            except Exception:
                cookie_value = ""
        if cookie_value:
            cookies.append((host, name, cookie_value, path or "/", bool(is_secure)))
    return cookies


def yuque_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/122 Safari/537.36 YuqueDesktop"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.yuque.com/dashboard/books/{BOOK_ID}",
    })
    for host, name, value, path, secure in decrypt_yuque_cookies():
        session.cookies.set(name, value, domain=host, path=path, secure=secure)
    return session


def get_json(session: requests.Session, url: str, **params: Any) -> Any:
    response = session.get(url, params=params, timeout=40)
    if response.status_code != 200:
        raise RuntimeError(f"{response.status_code} {url}: {response.text[:300]}")
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"Yuque API failed: {payload}")
    return payload.get("data", payload)


def local_iso(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).astimezone(TZ).isoformat()
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(TZ).isoformat()
    except Exception:
        return str(value)


def timestamp_from_iso(value: Any, fallback_date: str | None = None, hour: int = 12) -> float:
    if value:
        try:
            return dt.datetime.fromisoformat(str(value)).astimezone(TZ).timestamp()
        except Exception:
            pass
    if fallback_date:
        y, m, d = map(int, fallback_date.split("-"))
        return dt.datetime(y, m, d, hour, 0, 0, tzinfo=TZ).timestamp()
    return time.time()


def timestamp_from_week(week: str, hour: int = 9) -> float:
    return dt.datetime(
        2000 + int(week[:2]),
        int(week[2:4]),
        int(week[4:6]),
        hour,
        0,
        0,
        tzinfo=TZ,
    ).timestamp()


def safe_json_loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def custom_field_value(custom_fields: list[Any], key: str) -> Any:
    for item in custom_fields:
        if isinstance(item, list) and len(item) >= 3 and item[0] == key:
            return item[2]
        if isinstance(item, dict) and item.get("key") == key:
            return item.get("value")
    return None


def database_facts(path: Path) -> dict[str, Any]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    existing_doc_keys: set[str] = set()
    existing_fingerprints: set[str] = set()
    titles: dict[str, list[str]] = {}
    date_counts: dict[str, int] = {}
    for row in con.execute("select id,title,start_at,custom_fields from notenode"):
        text = row["custom_fields"] or ""
        for doc_id in re.findall(r"24363220/(\d+)", text):
            existing_doc_keys.add(f"{BOOK_ID}/{doc_id}")
        fields = safe_json_loads(text, [])
        fingerprint = custom_field_value(fields, "source_fingerprint")
        if fingerprint:
            existing_fingerprints.add(str(fingerprint))
        titles.setdefault(row["title"], []).append(row["id"])
        try:
            if row["start_at"]:
                day = dt.datetime.fromtimestamp(float(row["start_at"]), TZ).date().isoformat()
                date_counts[day] = date_counts.get(day, 0) + 1
        except Exception:
            pass
    con.close()
    return {
        "existing_doc_keys": existing_doc_keys,
        "existing_fingerprints": existing_fingerprints,
        "titles": titles,
        "date_counts": date_counts,
    }


def decode_card_value(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    text = value[5:] if value.startswith("data:") else value
    text = urllib.parse.unquote(text)
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        if "," in text:
            try:
                payload = json.loads(text.split(",", 1)[1])
                return payload if isinstance(payload, dict) else {}
            except Exception:
                pass
    return {}


def normalize_cards(soup: BeautifulSoup) -> BeautifulSoup:
    for card in list(soup.find_all("card")):
        name = (card.get("name") or card.get("data-card-name") or "").lower()
        data = decode_card_value(card.get("value") or card.get("data-card-value"))

        if name == "codeblock" or data.get("code") is not None:
            pre = soup.new_tag("pre")
            code = soup.new_tag("code")
            lang = data.get("mode") or data.get("language") or ""
            if lang:
                code["class"] = f"language-{lang}"
            code.string = data.get("code") or ""
            pre.append(code)
            card.replace_with(pre)
            continue

        if name in {"file", "attachment", "localfile"} or data.get("download_url") or data.get("fileName"):
            href = data.get("src") or data.get("url") or data.get("download_url") or data.get("href")
            label = data.get("name") or data.get("filename") or data.get("fileName") or data.get("title") or "attachment"
            if href:
                link = soup.new_tag("a")
                link["href"] = href
                link["target"] = "_blank"
                link.string = str(label)
                card.replace_with(link)
                continue

        if name == "image" or data.get("src"):
            src = data.get("src") or data.get("url") or data.get("href")
            alt = data.get("name") or data.get("filename") or data.get("alt") or "image.png"
            if src:
                img = soup.new_tag("img")
                img["src"] = src
                img["data-href"] = src
                img["alt"] = str(alt)
                if data.get("width"):
                    img["width"] = str(data["width"])
                if data.get("height"):
                    img["height"] = str(data["height"])
                card.replace_with(img)
                continue

        label = data.get("name") or data.get("title") or name or "yuque-card"
        span = soup.new_tag("span")
        span.string = f"[{label}]"
        card.replace_with(span)
    return soup


def content_html(doc: dict[str, Any]) -> str:
    raw = doc.get("content") or doc.get("body") or doc.get("body_html") or doc.get("html") or ""
    soup = BeautifulSoup(raw, "html.parser")
    normalize_cards(soup)
    return normalize_legacy_yuque_lake_html(str(soup))


def text_of(node: Any) -> str:
    if not node:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def plain_excerpt(html_text: str, max_len: int = 700) -> str:
    return text_of(BeautifulSoup(html_text or "", "html.parser"))[:max_len]


DAY_RE = re.compile(r"(?P<day>\d{6}|\d{4})\s*周(?P<weekday>[一二三四五六日天])")
NUM_RE = re.compile(r"^\s*([0-9０-９]+)[、,.，．]\s*(.+)")


def parse_day_date(day_text: str, year_prefix: str) -> str | None:
    match = DAY_RE.search(day_text or "")
    if not match:
        return None
    raw = match.group("day")
    if len(raw) == 4:
        raw = year_prefix + raw
    try:
        return dt.date(2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6])).isoformat()
    except ValueError:
        return None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def element_html(parts: list[Any]) -> str:
    return "".join(str(part) for part in parts).strip()


def remove_duplicate_first_paragraph(parts: list[Any], summary: str) -> list[Any]:
    if not parts:
        return parts
    soup = BeautifulSoup(element_html(parts), "html.parser")
    first: Tag | None = None
    for child in soup.contents:
        if isinstance(child, Tag) and text_of(child):
            first = child
            break
    if first and clean_text(text_of(first)) == clean_text(summary):
        first.extract()
        return [BeautifulSoup(str(soup), "html.parser")]
    return parts


def parse_week_doc(doc: dict[str, Any], week: str) -> tuple[str, list[dict[str, Any]], str]:
    html_doc = content_html(doc)
    soup = BeautifulSoup(html_doc, "html.parser")
    parent_parts: list[Any] = []
    in_diary = False

    for element in soup.find_all(["h1", "h2", "h3", "p", "ul", "ol", "blockquote", "pre", "table", "img"], recursive=True):
        if element.name == "h1" and "日记" in text_of(element):
            in_diary = True
            continue
        if not in_diary and (text_of(element) or element.find("img") or element.name in {"table", "pre"}):
            parent_parts.append(copy.copy(element))

    entries: list[dict[str, Any]] = []
    day_headings = [h for h in soup.find_all(["h2", "h3"]) if DAY_RE.search(text_of(h))]
    for heading in day_headings:
        day_title = text_of(heading)
        day_date = parse_day_date(day_title, week[:2])
        siblings: list[Any] = []
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"h1", "h2", "h3"}:
                break
            if isinstance(sibling, NavigableString) and not str(sibling).strip():
                continue
            siblings.append(copy.copy(sibling))

        section = BeautifulSoup(element_html(siblings), "html.parser")
        details_list = section.find_all("details")
        if details_list:
            for index, details in enumerate(details_list, 1):
                summary = clean_text(text_of(details.find("summary"))) or f"{day_title}条目{index}"
                body_parts: list[Any] = []
                for child in list(details.contents):
                    if isinstance(child, Tag) and child.name == "summary":
                        continue
                    if isinstance(child, NavigableString) and not str(child).strip():
                        continue
                    body_parts.append(child)
                body_parts = remove_duplicate_first_paragraph(body_parts, summary)
                body = f"<p>{html.escape(summary)}</p>" + element_html(body_parts)
                fingerprint = hashlib.sha1(
                    (week + day_title + summary + text_of(BeautifulSoup(body, "html.parser"))).encode("utf-8")
                ).hexdigest()[:16]
                entries.append({
                    "day_title": day_title,
                    "date": day_date,
                    "index": index,
                    "raw_title": summary,
                    "content": body,
                    "fingerprint": fingerprint,
                    "text": text_of(BeautifulSoup(body, "html.parser")),
                })
            continue

        index = 0
        for element in section.find_all(["p", "li"], recursive=True):
            text = clean_text(text_of(element))
            if not text or not NUM_RE.match(text):
                continue
            index += 1
            body = str(element)
            fingerprint = hashlib.sha1((week + day_title + text).encode("utf-8")).hexdigest()[:16]
            entries.append({
                "day_title": day_title,
                "date": day_date,
                "index": index,
                "raw_title": text,
                "content": body,
                "fingerprint": fingerprint,
                "text": text,
            })
    return element_html(parent_parts), entries, html_doc


def node_path(node: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> str:
    parts = [node.get("title") or ""]
    current = by_uuid.get(node.get("parent_uuid"))
    seen: set[str] = set()
    while current and current.get("uuid") not in seen:
        seen.add(current.get("uuid"))
        parts.append(current.get("title") or "")
        current = by_uuid.get(current.get("parent_uuid"))
    return " / ".join(reversed([part for part in parts if part]))


def descendants(node: dict[str, Any], children: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    stack = list(children.get(node.get("uuid"), []))
    while stack:
        item = stack.pop(0)
        result.append(item)
        stack[0:0] = children.get(item.get("uuid"), [])
    return result


def ancestor_chapter(node: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    current = by_uuid.get(node.get("parent_uuid"))
    seen: set[str] = set()
    while current and current.get("uuid") not in seen:
        seen.add(current.get("uuid"))
        title = (current.get("title") or "").strip()
        if re.match(r"^ch\d+\b", title, re.I):
            return current
        current = by_uuid.get(current.get("parent_uuid"))
    return None


def prepare(args: argparse.Namespace) -> None:
    year = int(args.year)
    year_prefix = str(year)[-2:]
    week_re = re.compile(rf"^w({year_prefix}\d{{4}})")
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(year)
    output_dir.mkdir(parents=True, exist_ok=True)

    facts = database_facts(db_path(data_dir))
    existing_doc_keys: set[str] = facts["existing_doc_keys"]
    existing_fingerprints: set[str] = facts["existing_fingerprints"]
    titles: dict[str, list[str]] = facts["titles"]
    date_counts: dict[str, int] = facts["date_counts"]

    session = yuque_session()
    catalog = get_json(session, "https://www.yuque.com/api/catalog_nodes", book_id=BOOK_ID)
    by_uuid = {node.get("uuid"): node for node in catalog if node.get("uuid")}
    children: dict[str, list[dict[str, Any]]] = {}
    for node in catalog:
        children.setdefault(node.get("parent_uuid") or "", []).append(node)

    weeks = sorted(
        [
            node for node in catalog
            if node.get("type") == "DOC" and week_re.match((node.get("title") or "").strip())
        ],
        key=lambda node: week_re.match((node.get("title") or "").strip()).group(1),
    )
    week_codes = [week_re.match((node.get("title") or "").strip()).group(1) for node in weeks]

    child_docs: list[tuple[str, dict[str, Any]]] = []
    chapters_by_uuid: dict[str, dict[str, Any]] = {}
    chapter_first_week: dict[str, str] = {}
    chapter_by_week: dict[str, dict[str, Any]] = {}
    for week, week_code in zip(weeks, week_codes):
        chapter = ancestor_chapter(week, by_uuid)
        if chapter:
            chapters_by_uuid[chapter["uuid"]] = chapter
            chapter_first_week.setdefault(chapter["uuid"], week_code)
            chapter_by_week[week_code] = chapter
        for child in descendants(week, children):
            if child.get("type") == "DOC" and child.get("doc_id"):
                child_docs.append((week_code, child))

    fetch_ids: set[str] = set()
    for chapter in chapters_by_uuid.values():
        doc_id = str(chapter.get("doc_id"))
        if doc_id and f"{BOOK_ID}/{doc_id}" not in existing_doc_keys:
            fetch_ids.add(doc_id)
    for week in weeks:
        doc_id = str(week.get("doc_id"))
        if doc_id and f"{BOOK_ID}/{doc_id}" not in existing_doc_keys:
            fetch_ids.add(doc_id)
    for _, child in child_docs:
        doc_id = str(child.get("doc_id"))
        if doc_id and f"{BOOK_ID}/{doc_id}" not in existing_doc_keys:
            fetch_ids.add(doc_id)

    docs: dict[str, dict[str, Any]] = {}
    sorted_fetch_ids = sorted(fetch_ids, key=int)
    for index, doc_id in enumerate(sorted_fetch_ids, 1):
        if index == 1 or index % 20 == 0 or index == len(sorted_fetch_ids):
            print(f"fetch docs {index}/{len(sorted_fetch_ids)}")
        docs[doc_id] = get_json(session, f"https://www.yuque.com/api/docs/{doc_id}", book_id=BOOK_ID)

    child_candidates: list[dict[str, Any]] = []
    child_dates: set[str] = set()
    for week_code, child in child_docs:
        doc_id = str(child.get("doc_id"))
        source_key = f"{BOOK_ID}/{doc_id}"
        if source_key in existing_doc_keys or doc_id not in docs:
            continue
        doc = docs[doc_id]
        html_body = content_html(doc)
        excerpt = plain_excerpt(html_body, 900)
        if not excerpt and "<img" not in html_body and "<pre" not in html_body:
            continue
        local_time = local_iso(doc.get("published_at") or doc.get("created_at"))
        date = None
        try:
            date = dt.datetime.fromisoformat(local_time).date().isoformat() if local_time else None
        except Exception:
            pass
        if date:
            child_dates.add(date)
        child_candidates.append({
            "kind": "child_doc",
            "doc_id": doc_id,
            "source_key": source_key,
            "week": week_code,
            "chapter_doc_id": str(chapter_by_week.get(week_code, {}).get("doc_id") or ""),
            "title": child.get("title") or doc.get("title") or "",
            "slug": child.get("url") or doc.get("slug"),
            "path": node_path(child, by_uuid),
            "created_at": local_iso(doc.get("created_at")),
            "updated_at": local_iso(doc.get("updated_at") or doc.get("content_updated_at")),
            "published_at": local_time,
            "date": date,
            "content": html_body,
            "excerpt": excerpt,
            "group_week": week_code,
        })

    candidates: list[dict[str, Any]] = []
    plan_items: list[dict[str, Any]] = []

    for chapter in sorted(chapters_by_uuid.values(), key=lambda item: chapter_first_week.get(item["uuid"], "")):
        doc_id = str(chapter.get("doc_id"))
        source_key = f"{BOOK_ID}/{doc_id}"
        if source_key in existing_doc_keys or doc_id not in docs:
            continue
        doc = docs[doc_id]
        html_body = content_html(doc)
        first_week = chapter_first_week.get(chapter["uuid"], "")
        item = {
            "kind": "chapter",
            "doc_id": doc_id,
            "source_key": source_key,
            "week": first_week,
            "title": chapter.get("title") or doc.get("title") or "",
            "slug": chapter.get("url") or doc.get("slug"),
            "path": node_path(chapter, by_uuid),
            "created_at": local_iso(doc.get("created_at")),
            "updated_at": local_iso(doc.get("updated_at") or doc.get("content_updated_at")),
            "content": html_body,
            "excerpt": plain_excerpt(html_body, 700),
            "group_week": first_week,
        }
        candidates.append(item)
        plan_items.append({
            "id": f"chapter:{doc_id}",
            "kind": "chapter",
            "week": first_week,
            "source_title": item["title"],
            "path": item["path"],
            "excerpt": item["excerpt"],
        })

    for week, week_code in zip(weeks, week_codes):
        doc_id = str(week.get("doc_id"))
        source_key = f"{BOOK_ID}/{doc_id}"
        if doc_id not in docs:
            continue
        doc = docs[doc_id]
        parent_html, entries, full_html = parse_week_doc(doc, week_code)
        title = week.get("title") or doc.get("title") or f"w{week_code}"
        existing_ids = titles.get(title, [])
        parent_kind = "week_update" if existing_ids else "week_parent"
        if source_key not in existing_doc_keys:
            item = {
                "kind": parent_kind,
                "doc_id": doc_id,
                "source_key": source_key,
                "week": week_code,
                "chapter_doc_id": str(chapter_by_week.get(week_code, {}).get("doc_id") or ""),
                "title": title,
                "slug": week.get("url") or doc.get("slug"),
                "path": node_path(week, by_uuid),
                "created_at": local_iso(doc.get("created_at")),
                "updated_at": local_iso(doc.get("updated_at") or doc.get("content_updated_at")),
                "content": parent_html,
                "existing_ids": existing_ids,
                "entry_count": len(entries),
                "full_excerpt": plain_excerpt(full_html, 900),
                "group_week": week_code,
            }
            candidates.append(item)
            plan_items.append({
                "id": f"week:{doc_id}",
                "kind": parent_kind,
                "week": week_code,
                "source_title": title,
                "path": item["path"],
                "entry_count": len(entries),
                "excerpt": plain_excerpt(parent_html, 450) or plain_excerpt(full_html, 450),
            })

        entries_by_day: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
        for entry in entries:
            if entry["fingerprint"] in existing_fingerprints:
                continue
            entries_by_day.setdefault((entry["date"], entry["day_title"]), []).append(entry)

        for (date, day_title), day_entries in entries_by_day.items():
            has_other = bool(date and (date_counts.get(date, 0) > 0 or date in child_dates))
            if has_other:
                fingerprint = hashlib.sha1(
                    (week_code + day_title + "".join(entry["fingerprint"] for entry in day_entries)).encode("utf-8")
                ).hexdigest()[:16]
                if fingerprint in existing_fingerprints:
                    continue
                content = f"<h3>{html.escape(day_title)}</h3>" + "".join(entry["content"] for entry in day_entries)
                text = plain_excerpt(content, 900)
                candidates.append({
                    "kind": "day_group",
                    "doc_id": doc_id,
                    "source_key": source_key,
                    "week": week_code,
                    "chapter_doc_id": str(chapter_by_week.get(week_code, {}).get("doc_id") or ""),
                    "date": date,
                    "day_title": day_title,
                    "entries": day_entries,
                    "fingerprint": fingerprint,
                    "content": content,
                    "text": text,
                    "group_week": week_code,
                })
                plan_items.append({
                    "id": f"daygroup:{doc_id}:{fingerprint}",
                    "kind": "day_group",
                    "week": week_code,
                    "date": date,
                    "source_title": f"语雀日志 {day_title}",
                    "entry_count": len(day_entries),
                    "excerpt": text[:700],
                })
            else:
                for entry in day_entries:
                    candidates.append({
                        "kind": "day_entry",
                        "doc_id": doc_id,
                        "source_key": source_key,
                        "week": week_code,
                        "chapter_doc_id": str(chapter_by_week.get(week_code, {}).get("doc_id") or ""),
                        **entry,
                        "group_week": week_code,
                    })
                    plan_items.append({
                        "id": f"day:{doc_id}:{entry['fingerprint']}",
                        "kind": "day_entry",
                        "week": week_code,
                        "date": entry["date"],
                        "source_title": entry["raw_title"],
                        "excerpt": entry["text"][:700],
                    })

    candidates.extend(child_candidates)
    for item in child_candidates:
        plan_items.append({
            "id": f"child:{item['doc_id']}",
            "kind": "child_doc",
            "week": item["week"],
            "date": item["date"] or item["published_at"],
            "source_title": item["title"],
            "path": item["path"],
            "excerpt": item["excerpt"][:800],
        })

    (output_dir / "candidates.json").write_text(json.dumps({
        "book_id": BOOK_ID,
        "year": year,
        "weeks": [week.get("title") for week in weeks],
        "fetched_doc_ids": sorted_fetch_ids,
        "candidates": candidates,
    }, ensure_ascii=False, indent=2), "utf-8")
    (output_dir / "plan_input.json").write_text(json.dumps({
        "categories": CATEGORY_HINTS,
        "items": plan_items,
    }, ensure_ascii=False, indent=2), "utf-8")

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

    write_plan_batches(output_dir, year, week_codes, plan_items, int(args.batch_size))

    print(json.dumps({
        "output_dir": str(output_dir),
        "weeks": len(weeks),
        "child_docs": len(child_docs),
        "chapters": len(chapters_by_uuid),
        "fetched_docs": len(sorted_fetch_ids),
        "candidate_kinds": Counter(item["kind"] for item in candidates),
        "plan_items": len(plan_items),
    }, ensure_ascii=False, indent=2, default=dict))


def write_plan_batches(output_dir: Path, year: int, week_codes: list[str], plan_items: list[dict[str, Any]], batch_size: int) -> None:
    batch_dir = output_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    unique_weeks = list(dict.fromkeys(week_codes))
    for old in batch_dir.glob("batch_*_prompt.md"):
        old.unlink()
    for old in batch_dir.glob("batch_*_input.json"):
        old.unlink()

    for index in range(0, len(unique_weeks), batch_size):
        batch_weeks = set(unique_weeks[index:index + batch_size])
        batch_items = [
            item for item in plan_items
            if item.get("week") in batch_weeks
        ]
        batch_no = index // batch_size + 1
        payload = {
            "categories": CATEGORY_HINTS,
            "items": batch_items,
        }
        input_path = batch_dir / f"batch_{batch_no:03d}_input.json"
        prompt_path = batch_dir / f"batch_{batch_no:03d}_prompt.md"
        input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        prompt = f"""你是 CodeYun 星图笔记的语雀导入分类、权重助手。请根据下面 JSON，为每个待导入节点生成标题、分类 key、权重和一句内部备注。

硬性要求：
1. 必须为输入 items 中每一个 id 输出一条，不能漏、不能新增。
2. 输出必须严格匹配 schema：{{"items":[{{"id":"...","title":"...","category_key":"...","weight":0,"note":"..."}}]}}。
3. category_key 只能从 categories 的 key 中选择。
4. 权重策略：chapter 节点固定 3；week_parent/week_update 固定 2；真正章节/方法论/长文档可设 1；普通日记条目默认 0；除 chapter 外不要输出 3，也不要批量设 1。
5. title 必须使用输入 item 的 source_title 原样，不要概括、改写、补词或清理编号；正文会直接使用语雀原文，这里的 note 不会写入正文。不要在 title/note 里写“Codex 分析”“AI 判断”等措辞。
6. 日记条目、子文档、周节点和 chapter 都按 source_title 原样作为 title；如果 source_title 为空，才使用最保守的原始标题兜底。
7. AI 只负责 category_key、weight 和 note，不负责重命名。
8. 分类参考：pyxllib/工程/目录/代码/脚本 -> custom_mmxdyjjkxrsr；AI/模型/Trae/Codex/Gemini/DeepSeek -> custom_mmxdhqhnrgup；CodeYun/笔记系统 -> custom_mmx3qpfhinvh 或 custom_mmxdcghtzcw7；凡修/手游脚本 -> legacy_color_67c23a 或 custom_mmxdyjjkxrsr；考勤/退款/问卷 -> legacy_color_e6a23c；设备/生活/采购/游戏 -> custom_mmxbzxjy85x5；不明确 -> general。

输入 JSON：
```json
{json.dumps(payload, ensure_ascii=False, indent=2)}
```
"""
        prompt_path.write_text(prompt, "utf-8")


def load_plans(output_dir: Path) -> dict[str, dict[str, Any]]:
    plans: dict[str, dict[str, Any]] = {}
    for path in sorted((output_dir / "batches").glob("batch_*_plan.json")):
        payload = json.loads(path.read_text("utf-8"))
        for item in payload.get("items", []):
            plans[item["id"]] = item
    direct_plan = output_dir / "plan.json"
    if direct_plan.exists():
        payload = json.loads(direct_plan.read_text("utf-8"))
        for item in payload.get("items", []):
            plans[item["id"]] = item
    return plans


def candidate_plan_id(item: dict[str, Any]) -> str:
    kind = item["kind"]
    if kind == "chapter":
        return f"chapter:{item['doc_id']}"
    if kind in {"week_parent", "week_update"}:
        return f"week:{item['doc_id']}"
    if kind == "day_group":
        return f"daygroup:{item['doc_id']}:{item['fingerprint']}"
    if kind == "day_entry":
        return f"day:{item['doc_id']}:{item['fingerprint']}"
    if kind == "child_doc":
        return f"child:{item['doc_id']}"
    raise ValueError(kind)


def is_http_url(url: str | None) -> bool:
    return bool(url and (url.startswith("http://") or url.startswith("https://") or url.startswith("//")))


def absolute_url(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(absolute_url(url)).netloc.lower()
    except Exception:
        return ""


def should_localize(url: str | None, tag: str) -> bool:
    if not is_http_url(url):
        return False
    host = host_of(url or "")
    if tag == "img":
        return True
    return any(part in host for part in ["nlark.com", "yuque.com", "alipayobjects.com"])


def extension_from_response(content_type: str, url: str, fallback: str | None) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix and len(suffix) <= 12:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
        if guessed:
            return guessed
    if fallback:
        fallback_suffix = Path(str(fallback)).suffix.lower()
        if fallback_suffix:
            return fallback_suffix
    return ".bin"


def safe_download_name(name: str, extension: str) -> str:
    text = (name or "").strip() or f"attachment{extension}"
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    if not Path(text).suffix and extension:
        text += extension
    return text[:160]


class MediaLocalizer:
    def __init__(self, session: requests.Session, attach_dir: Path):
        self.session = session
        self.attach_dir = attach_dir
        self.attach_dir.mkdir(parents=True, exist_ok=True)
        self.totals = {"images": 0, "attachments": 0, "downloaded": 0, "reused": 0, "failed": []}

    def localize_asset(self, url: str, label: str | None, as_image: bool) -> tuple[str, str]:
        url = absolute_url(url).rstrip(")）]】>,，。")
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]
        for existing in self.attach_dir.glob(f"yuque_{digest}.*"):
            self.totals["reused"] += 1
            return f"/static/attachments/{existing.name}", existing.name

        try:
            response = self.session.get(url, timeout=80, stream=True)
        except requests.exceptions.SSLError:
            response = self.session.get(url, timeout=80, stream=True, verify=False)
        if response.status_code != 200:
            raise RuntimeError(f"{response.status_code} {url[:180]}")
        extension = extension_from_response(response.headers.get("content-type", ""), url, label)
        if as_image and extension == ".bin":
            extension = ".png"
        filename = f"yuque_{digest}{extension}"
        target = self.attach_dir / filename
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    stream.write(chunk)
        tmp.replace(target)
        self.totals["downloaded"] += 1
        return f"/static/attachments/{filename}", filename

    def rewrite(self, content: str) -> tuple[str, dict[str, Any]]:
        stats = {"images": 0, "attachments": 0, "failed": []}
        soup = BeautifulSoup(content or "", "html.parser")
        for image in soup.find_all("img"):
            src = image.get("src") or image.get("data-href")
            if should_localize(src, "img"):
                try:
                    local_url, _ = self.localize_asset(src, image.get("alt") or "image.png", True)
                    image["src"] = local_url
                    image["data-href"] = local_url
                    stats["images"] += 1
                    self.totals["images"] += 1
                except Exception as exc:
                    message = f"img {src}: {exc}"
                    stats["failed"].append(message)
                    self.totals["failed"].append(message)

        for link in soup.find_all("a"):
            href = link.get("href")
            if not should_localize(href, "a"):
                continue
            host = host_of(href or "")
            path = urllib.parse.urlparse(absolute_url(href or "")).path.lower()
            text = link.get_text(" ", strip=True)
            has_file_ext = bool(re.search(
                r"\.(zip|docx?|xlsx?|pptx?|pdf|png|jpe?g|gif|webp|7z|rar|txt|py|json|yml|yaml)$",
                path,
            ) or re.search(
                r"\.(zip|docx?|xlsx?|pptx?|pdf|png|jpe?g|gif|webp|7z|rar|txt|py|json|yml|yaml)$",
                text.lower(),
            ))
            if "nlark.com" not in host and not has_file_ext:
                continue
            try:
                local_url, filename = self.localize_asset(href, text or "attachment", False)
                link["href"] = local_url
                link["target"] = "_blank"
                link["rel"] = "noopener noreferrer"
                link["download"] = safe_download_name(text, Path(filename).suffix)
                link["data-codeyun-attachment"] = "true"
                if not text:
                    link.string = link["download"]
                stats["attachments"] += 1
                self.totals["attachments"] += 1
            except Exception as exc:
                message = f"a {href}: {exc}"
                stats["failed"].append(message)
                self.totals["failed"].append(message)
        return str(soup), stats


def note_categories(category: str) -> str:
    return json.dumps([{"key": category, "weight": 100}], ensure_ascii=False)


def custom_fields_base(item: dict[str, Any], source_kind: str, stats: dict[str, Any]) -> str:
    fields: list[list[Any]] = [
        ["source", "string", IMPORT_SOURCE],
        ["source_import", "string", item.get("source_import") or "codex-cli-yuque-journal"],
        ["source_kind", "string", source_kind],
        ["source_doc_key", "string", item["source_key"]],
        ["source_week", "string", item.get("week") or ""],
    ]
    suffix = f"/{item['fingerprint']}" if item.get("fingerprint") else ""
    fields.append(["source_key", "string", item["source_key"] + suffix])
    for key in ["title", "day_title", "date", "slug", "path", "created_at", "updated_at", "published_at", "chapter_doc_id"]:
        if item.get(key):
            fields.append([f"source_{key}", "string", str(item[key])])
    if source_kind == "yuque_day_group":
        fields.append(["source_entry_fingerprints", "string", ",".join(entry.get("fingerprint", "") for entry in item.get("entries", []))])
        fields.append(["source_entry_indexes", "string", ",".join(str(entry.get("index", "")) for entry in item.get("entries", []))])
    if item.get("fingerprint"):
        fields.append(["source_fingerprint", "string", item["fingerprint"]])
    fields.extend([
        ["source_images", "number", stats.get("images", 0)],
        ["source_attachments", "number", stats.get("attachments", 0)],
        ["source_asset_import", "string", "local-static-attachments"],
    ])
    return json.dumps(fields, ensure_ascii=False)


def merge_custom_fields(existing: str | None, new_fields_json: str) -> str:
    existing_fields = safe_json_loads(existing, [])
    new_fields = safe_json_loads(new_fields_json, [])
    new_keys = {item[0] for item in new_fields if isinstance(item, list) and item}
    kept = []
    for item in existing_fields:
        key = item[0] if isinstance(item, list) and item else item.get("key") if isinstance(item, dict) else None
        if key not in new_keys:
            kept.append(item)
    return json.dumps(kept + new_fields, ensure_ascii=False)


def comparable_title(title: str) -> str:
    text = (title or "").lower()
    return re.sub(r"[\s:：/\\()（）+\-_,，。]+", "", text)


def find_existing_chapter_by_title(con: sqlite3.Connection, title: str) -> sqlite3.Row | None:
    target = comparable_title(title)
    if not target:
        return None
    for row in con.execute("select id,title,custom_fields from notenode where title like 'ch%'"):
        fields = row["custom_fields"] or ""
        if IMPORT_SOURCE in fields:
            continue
        if comparable_title(row["title"] or "") == target:
            return row
    return None


def source_title_for_item(item: dict[str, Any]) -> str:
    kind = item.get("kind")
    if kind == "day_entry":
        return clean_text(item.get("raw_title") or item.get("title") or "")
    if kind == "day_group":
        return clean_text(f"语雀日志 {item.get('day_title') or ''}")
    return clean_text(item.get("title") or "")


def normalized_plan_title(kind: str, title: str, original_title: str = "") -> str:
    text = clean_text(original_title) or clean_text(title)
    if kind in {"week_parent", "week_update"}:
        match = re.match(r"^(w\d{6})\s+([^:：].*)$", text)
        if match:
            return f"{match.group(1)}: {match.group(2).strip()}"
    return text


def normalized_weight(kind: str, planned_weight: int) -> int:
    if kind == "chapter":
        return 3
    if kind in {"week_parent", "week_update"}:
        return 2
    return max(0, min(int(planned_weight), 1))


def insert_node(
    con: sqlite3.Connection,
    title: str,
    content: str,
    weight: int,
    start_at: float,
    category: str,
    custom_fields: str,
) -> str:
    node_id = str(uuid.uuid4())
    now = time.time()
    cats = note_categories(category)
    con.execute(
        """
        insert into notenode(
            id,user_id,title,content,created_at,updated_at,weight,start_at,task_status,history,
            node_type,node_status,custom_fields,private_level,color,note_kind,weight_mode,
            note_types,note_categories,primary_category,note_form,lifecycle_stage,note_scene
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            node_id, USER_ID, title, content, now, now, weight, start_at, None, "[]",
            category, "done", custom_fields, 0, None, "note", None,
            cats, cats, category, "note", "done", "note",
        ),
    )
    return node_id


def edge_exists(con: sqlite3.Connection, source_id: str, target_id: str) -> bool:
    return con.execute(
        "select 1 from noteedge where user_id=? and source_id=? and target_id=? limit 1",
        (USER_ID, source_id, target_id),
    ).fetchone() is not None


def insert_edge(con: sqlite3.Connection, source_id: str | None, target_id: str | None) -> bool:
    if not source_id or not target_id or edge_exists(con, source_id, target_id):
        return False
    con.execute(
        "insert into noteedge(id,user_id,source_id,target_id,label,created_at) values (?,?,?,?,?,?)",
        (str(uuid.uuid4()), USER_ID, source_id, target_id, None, time.time()),
    )
    return True


def source_doc_exists(con: sqlite3.Connection, source_key: str) -> sqlite3.Row | None:
    return con.execute(
        "select id,title from notenode where custom_fields like ? limit 1",
        (f"%{source_key}%",),
    ).fetchone()


def source_fingerprint_exists(con: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return con.execute(
        "select id,title from notenode where custom_fields like ? limit 1",
        (f"%source_fingerprint%{fingerprint}%",),
    ).fetchone()


def import_candidates(args: argparse.Namespace) -> None:
    year = int(args.year)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(year)
    import_name = args.import_name or f"codex-cli-yuque-{year}"
    candidates_payload = json.loads((output_dir / "candidates.json").read_text("utf-8"))
    candidates = candidates_payload["candidates"]
    plans = load_plans(output_dir)
    importable = [item for item in candidates if candidate_plan_id(item) in plans]
    missing_plan = [candidate_plan_id(item) for item in candidates if candidate_plan_id(item) not in plans]
    if missing_plan:
        raise RuntimeError(f"{len(missing_plan)} candidates do not have plan, sample: {missing_plan[:10]}")

    backup = Path(os.environ["TEMP"]) / f"codeyun_yuque_{year}_backup_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(db_path(data_dir), backup)

    session = yuque_session()
    media = MediaLocalizer(session, attachments_dir(data_dir))
    con = sqlite3.connect(db_path(data_dir), timeout=60)
    con.row_factory = sqlite3.Row

    parent_by_chapter_doc: dict[str, str] = {}
    parent_by_week: dict[str, str] = {}
    for row in con.execute("select id,title,custom_fields from notenode"):
        title = row["title"] or ""
        match = re.match(r"w(\d{6})", title)
        if match:
            parent_by_week.setdefault(match.group(1), row["id"])
        for doc_id in re.findall(r"24363220/(\d+)", row["custom_fields"] or ""):
            if "source_kind" in (row["custom_fields"] or "") and "yuque_chapter" in (row["custom_fields"] or ""):
                parent_by_chapter_doc[doc_id] = row["id"]

    inserted: list[tuple[str, str, str]] = []
    updated: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str]] = []
    edges = 0

    order = {"chapter": 0, "week_parent": 1, "week_update": 1, "day_group": 2, "day_entry": 2, "child_doc": 3}
    for item in sorted(importable, key=lambda obj: (order.get(obj["kind"], 9), obj.get("week") or "", obj.get("date") or "", candidate_plan_id(obj))):
        item["source_import"] = import_name
        plan = plans[candidate_plan_id(item)]
        category = plan["category_key"] if plan["category_key"] in CATEGORY_HINTS else "general"
        kind = item["kind"]
        weight = normalized_weight(kind, int(plan["weight"]))
        title = normalized_plan_title(kind, plan["title"], source_title_for_item(item))
        if title:
            item["title"] = title

        if kind in {"chapter", "week_parent", "week_update", "child_doc"} and source_doc_exists(con, item["source_key"]):
            skipped.append((candidate_plan_id(item), "source_doc_exists"))
            continue
        if kind in {"day_entry", "day_group"} and item.get("fingerprint") and source_fingerprint_exists(con, item["fingerprint"]):
            skipped.append((candidate_plan_id(item), "fingerprint_exists"))
            continue

        content, stats = media.rewrite(item.get("content") or "<p><br></p>")
        if kind == "chapter":
            same_chapter = find_existing_chapter_by_title(con, title)
            if same_chapter:
                cats = note_categories(category)
                con.execute(
                    "update notenode set updated_at=?,weight=?,node_type=?,custom_fields=?,"
                    "note_types=?,note_categories=?,primary_category=? where id=?",
                    (
                        time.time(), weight, category,
                        merge_custom_fields(same_chapter["custom_fields"], custom_fields_base(item, "yuque_chapter", stats)),
                        cats, cats, category, same_chapter["id"],
                    ),
                )
                parent_by_chapter_doc[item["doc_id"]] = same_chapter["id"]
                updated.append((candidate_plan_id(item), same_chapter["id"], same_chapter["title"]))
                continue
            start_at = timestamp_from_week(item["week"], 8) if item.get("week") else timestamp_from_iso(item.get("created_at"))
            node_id = insert_node(
                con, title, content or "<p><br></p>", weight, start_at, category,
                custom_fields_base(item, "yuque_chapter", stats),
            )
            parent_by_chapter_doc[item["doc_id"]] = node_id
            inserted.append((candidate_plan_id(item), node_id, title))
            continue

        if kind == "week_update":
            target_id = (item.get("existing_ids") or [None])[0] or parent_by_week.get(item["week"])
            if not target_id:
                skipped.append((candidate_plan_id(item), "week_update_no_target"))
                continue
            cats = note_categories(category)
            row = con.execute("select custom_fields from notenode where id=?", (target_id,)).fetchone()
            con.execute(
                "update notenode set title=?,content=?,updated_at=?,weight=?,node_type=?,custom_fields=?,"
                "note_types=?,note_categories=?,primary_category=? where id=?",
                (
                    title, content or "<p><br></p>", time.time(), weight, category,
                    merge_custom_fields(row["custom_fields"], custom_fields_base(item, "yuque_week", stats)),
                    cats, cats, category, target_id,
                ),
            )
            parent_by_week[item["week"]] = target_id
            if insert_edge(con, parent_by_chapter_doc.get(item.get("chapter_doc_id") or ""), target_id):
                edges += 1
            updated.append((candidate_plan_id(item), target_id, title))
            continue

        if kind == "week_parent":
            same = con.execute("select id,custom_fields from notenode where title=? limit 1", (title,)).fetchone()
            if same:
                cats = note_categories(category)
                con.execute(
                    "update notenode set title=?,content=?,updated_at=?,weight=?,node_type=?,custom_fields=?,"
                    "note_types=?,note_categories=?,primary_category=? where id=?",
                    (
                        title, content or "<p><br></p>", time.time(), weight, category,
                        merge_custom_fields(same["custom_fields"], custom_fields_base(item, "yuque_week", stats)),
                        cats, cats, category, same["id"],
                    ),
                )
                node_id = same["id"]
                updated.append((candidate_plan_id(item), node_id, title))
            else:
                node_id = insert_node(
                    con, title, content or "<p><br></p>", weight,
                    timestamp_from_week(item["week"], 9), category,
                    custom_fields_base(item, "yuque_week", stats),
                )
                inserted.append((candidate_plan_id(item), node_id, title))
            parent_by_week[item["week"]] = node_id
            if insert_edge(con, parent_by_chapter_doc.get(item.get("chapter_doc_id") or ""), node_id):
                edges += 1
            continue

        if kind in {"day_entry", "day_group"}:
            source_kind = "yuque_day_group" if kind == "day_group" else "yuque_day"
            node_id = insert_node(
                con, title, content, weight,
                timestamp_from_iso(None, item.get("date"), 12), category,
                custom_fields_base(item, source_kind, stats),
            )
            inserted.append((candidate_plan_id(item), node_id, title))
            if insert_edge(con, parent_by_week.get(item["week"]), node_id):
                edges += 1
            continue

        if kind == "child_doc":
            node_id = insert_node(
                con, title, content, weight,
                timestamp_from_iso(item.get("published_at") or item.get("created_at"), item.get("date"), 12),
                category,
                custom_fields_base(item, "yuque_child", stats),
            )
            inserted.append((candidate_plan_id(item), node_id, title))
            if insert_edge(con, parent_by_week.get(item["week"]), node_id):
                edges += 1
            continue

    con.commit()
    con.close()
    summary = {
        "backup": str(backup),
        "inserted": len(inserted),
        "updated": len(updated),
        "skipped": len(skipped),
        "edges": edges,
        "media": media.totals,
        "inserted_titles_sample": [item[2] for item in inserted[:30]],
        "updated_titles_sample": [item[2] for item in updated[:30]],
        "skipped_sample": skipped[:20],
    }
    (output_dir / "import_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def validate(args: argparse.Namespace) -> None:
    year = int(args.year)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(year)
    candidates_payload = json.loads((output_dir / "candidates.json").read_text("utf-8"))
    candidates = candidates_payload["candidates"]
    planned_ids = {item["source_key"] for item in candidates if item["kind"] in {"chapter", "week_parent", "week_update", "child_doc"}}
    db = db_path(data_dir)
    attach_dir = attachments_dir(data_dir)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    missing_doc_keys = []
    for source_key in sorted(planned_ids):
        count = con.execute("select count(*) from notenode where custom_fields like ?", (f"%{source_key}%",)).fetchone()[0]
        if count == 0:
            missing_doc_keys.append(source_key)

    imported_rows = con.execute(
        "select title,content from notenode where custom_fields like ?",
        (f"%codex-cli-yuque-{year}%",),
    ).fetchall()
    external_images = []
    missing_files = []
    local_refs = 0
    for row in imported_rows:
        soup = BeautifulSoup(row["content"] or "", "html.parser")
        for image in soup.find_all("img"):
            src = image.get("src") or ""
            if is_http_url(src):
                external_images.append((row["title"], src[:120]))
            if src.startswith("/static/attachments/"):
                local_refs += 1
                if not (attach_dir / Path(src).name).exists():
                    missing_files.append((row["title"], src))
        for link in soup.find_all("a"):
            href = link.get("href") or ""
            if href.startswith("/static/attachments/"):
                local_refs += 1
                if not (attach_dir / Path(href).name).exists():
                    missing_files.append((row["title"], href))

    weights = con.execute(
        "select weight,count(*) c from notenode where custom_fields like ? group by weight order by weight",
        (f"%codex-cli-yuque-{year}%",),
    ).fetchall()
    con.close()
    print(json.dumps({
        "planned_doc_keys": len(planned_ids),
        "missing_doc_keys": missing_doc_keys[:20],
        "missing_doc_key_count": len(missing_doc_keys),
        "imported_nodes": len(imported_rows),
        "external_images": external_images[:10],
        "external_image_count": len(external_images),
        "local_refs": local_refs,
        "missing_files": missing_files[:10],
        "missing_file_count": len(missing_files),
        "weight_distribution": [dict(row) for row in weights],
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--year", type=int, required=True)
    prepare_parser.add_argument("--batch-size", type=int, default=4)
    prepare_parser.add_argument("--data-dir", default="")
    prepare_parser.add_argument("--output-dir", default="")
    prepare_parser.set_defaults(func=prepare)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--year", type=int, required=True)
    import_parser.add_argument("--data-dir", default="")
    import_parser.add_argument("--output-dir", default="")
    import_parser.add_argument("--import-name", default="")
    import_parser.set_defaults(func=import_candidates)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--year", type=int, required=True)
    validate_parser.add_argument("--data-dir", default="")
    validate_parser.add_argument("--output-dir", default="")
    validate_parser.set_defaults(func=validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
