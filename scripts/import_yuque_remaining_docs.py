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
from collections import Counter
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.import_yuque_journal import (
    BOOK_ID,
    CATEGORY_HINTS,
    TZ,
    USER_ID,
    MediaLocalizer,
    attachments_dir,
    content_html,
    custom_field_value,
    custom_fields_base,
    db_path,
    default_data_dir,
    get_json,
    insert_edge,
    insert_node,
    local_iso,
    node_path,
    plain_excerpt,
    safe_json_loads,
    timestamp_from_iso,
    timestamp_from_week,
    yuque_session,
)


IMPORT_NAME = "codex-cli-yuque-remaining"

VOLUME_STARTS = {
    "卷一": "1992-01-01",
    "卷二": "2008-08-01",
    "卷三": "2011-08-01",
    "卷四": "2015-08-01",
    "卷五": "2017-08-01",
    "卷六": "2020-07-01",
    "卷七": "2023-08-01",
    "卷八": "2025-01-01",
}


def default_output_dir() -> Path:
    return Path(os.environ["TEMP"]) / "codeyun_yuque_remaining_docs"


def build_catalog_maps(catalog: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_uuid = {node.get("uuid"): node for node in catalog if node.get("uuid")}
    children: dict[str, list[dict[str, Any]]] = {}
    for node in catalog:
        children.setdefault(node.get("parent_uuid") or "", []).append(node)
    return by_uuid, children


def doc_depth(node: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> int:
    depth = 0
    current = node
    seen: set[str] = set()
    while current.get("parent_uuid") and current.get("parent_uuid") in by_uuid and current.get("uuid") not in seen:
        seen.add(current.get("uuid"))
        current = by_uuid[current["parent_uuid"]]
        depth += 1
    return depth


def root_title(node: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> str:
    current = node
    seen: set[str] = set()
    while current.get("parent_uuid") and current.get("parent_uuid") in by_uuid and current.get("uuid") not in seen:
        seen.add(current.get("uuid"))
        current = by_uuid[current["parent_uuid"]]
    return (current.get("title") or "").strip()


def parent_doc_key(node: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> str:
    current = by_uuid.get(node.get("parent_uuid"))
    seen: set[str] = set()
    while current and current.get("uuid") not in seen:
        seen.add(current.get("uuid"))
        if current.get("type") == "DOC" and current.get("doc_id"):
            return f"{BOOK_ID}/{current.get('doc_id')}"
        current = by_uuid.get(current.get("parent_uuid"))
    return ""


def existing_source_keys(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    source_map: dict[str, sqlite3.Row] = {}
    for row in con.execute("select id,title,custom_fields from notenode where user_id=?", (USER_ID,)):
        fields = safe_json_loads(row["custom_fields"], [])
        for key in ("source_doc_key", "source_key"):
            value = custom_field_value(fields, key)
            if value:
                source_map[str(value)] = row
                if re.match(rf"^{BOOK_ID}/\d+", str(value)):
                    source_map[re.match(rf"^({BOOK_ID}/\d+)", str(value)).group(1)] = row  # type: ignore[union-attr]
        for match in re.findall(rf"{BOOK_ID}/\d+", row["custom_fields"] or ""):
            source_map.setdefault(match, row)
    return source_map


def infer_date(title: str, path: str, root: str, created_at: str | None = None) -> tuple[str | None, str]:
    text = f"{title} {path}"
    week = re.search(r"\bw(\d{6})\b", text, re.I)
    if week:
        code = week.group(1)
        return dt.date(2000 + int(code[:2]), int(code[2:4]), int(code[4:6])).isoformat(), code

    date8 = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text)
    if date8:
        return f"{date8.group(1)}-{date8.group(2)}-{date8.group(3)}", ""

    year_month = re.search(r"(20\d{2})[年./-]\s*(1[0-2]|0?[1-9])\s*月?", text)
    if year_month:
        return f"{year_month.group(1)}-{int(year_month.group(2)):02d}-01", ""

    year = re.search(r"(20\d{2})", text)
    if year:
        return f"{year.group(1)}-01-01", ""

    for prefix, value in VOLUME_STARTS.items():
        if root.startswith(prefix) or title.startswith(prefix):
            return value, ""

    if created_at:
        try:
            return dt.datetime.fromisoformat(created_at).date().isoformat(), ""
        except Exception:
            pass
    return None, ""


def classify_kind(title: str, path: str, depth: int, excerpt: str, has_media: bool, week: str) -> str:
    if depth == 0 and title.startswith("卷"):
        return "volume"
    if week and re.match(r"^w\d{6}", title.strip(), re.I):
        return "week_parent"
    if re.match(r"^ch\d+\b", title.strip(), re.I):
        return "section"
    if re.search(r"(20\d{2})年(1[0-2]|0?[1-9])月$", title.strip()):
        return "month_doc"
    if not excerpt and not has_media:
        return "container"
    if depth <= 1 and re.search(r"(19\d{2}|20\d{2})", title):
        return "year_root"
    return "doc"


def has_media_or_structure(html_body: str) -> bool:
    lowered = html_body.lower()
    return any(part in lowered for part in ("<img", "<table", "<pre", "<a "))


def prepare(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path(data_dir))
    con.row_factory = sqlite3.Row
    source_map = existing_source_keys(con)
    con.close()

    session = yuque_session()
    catalog = get_json(session, "https://www.yuque.com/api/catalog_nodes", book_id=BOOK_ID)
    by_uuid, _children = build_catalog_maps(catalog)

    missing_nodes = [
        node for node in catalog
        if node.get("type") == "DOC"
        and node.get("doc_id")
        and f"{BOOK_ID}/{node.get('doc_id')}" not in source_map
    ]
    docs: dict[str, dict[str, Any]] = {}
    for index, node in enumerate(missing_nodes, 1):
        doc_id = str(node.get("doc_id"))
        if index == 1 or index % 20 == 0 or index == len(missing_nodes):
            print(f"fetch docs {index}/{len(missing_nodes)} {doc_id} {node.get('title') or ''}")
        docs[doc_id] = get_json(session, f"https://www.yuque.com/api/docs/{doc_id}", book_id=BOOK_ID)

    candidates: list[dict[str, Any]] = []
    plan_items: list[dict[str, Any]] = []
    for node in sorted(missing_nodes, key=lambda item: (doc_depth(item, by_uuid), node_path(item, by_uuid))):
        doc_id = str(node.get("doc_id"))
        doc = docs[doc_id]
        html_body = content_html(doc)
        excerpt = plain_excerpt(html_body, 900)
        path = node_path(node, by_uuid)
        title = node.get("title") or doc.get("title") or doc_id
        depth = doc_depth(node, by_uuid)
        root = root_title(node, by_uuid)
        created_at = local_iso(doc.get("created_at"))
        inferred_date, week = infer_date(title, path, root, created_at)
        media = has_media_or_structure(html_body)
        kind = classify_kind(title, path, depth, excerpt, media, week)
        source_key = f"{BOOK_ID}/{doc_id}"
        content_fingerprint = hashlib.sha1((source_key + excerpt[:500] + str(len(html_body))).encode("utf-8")).hexdigest()[:16]
        item = {
            "kind": kind,
            "doc_id": doc_id,
            "source_key": source_key,
            "parent_source_key": parent_doc_key(node, by_uuid),
            "week": week,
            "title": title,
            "slug": node.get("url") or doc.get("slug"),
            "path": path,
            "root_title": root,
            "depth": depth,
            "created_at": created_at,
            "updated_at": local_iso(doc.get("updated_at") or doc.get("content_updated_at")),
            "published_at": local_iso(doc.get("published_at")),
            "date": inferred_date,
            "content": html_body,
            "excerpt": excerpt,
            "has_media": media,
            "fingerprint": content_fingerprint,
            "group_week": week,
        }
        candidates.append(item)
        plan_items.append({
            "id": candidate_plan_id(item),
            "kind": kind,
            "source_title": title,
            "path": path,
            "date": inferred_date or "",
            "has_media": media,
            "excerpt": excerpt[:700],
        })

    payload = {
        "book_id": BOOK_ID,
        "candidates": candidates,
    }
    (output_dir / "candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    (output_dir / "plan_input.json").write_text(json.dumps({"categories": CATEGORY_HINTS, "items": plan_items}, ensure_ascii=False, indent=2), "utf-8")
    write_plan_schema(output_dir)
    write_plan_batches(output_dir, plan_items, int(args.batch_size))

    print(json.dumps({
        "output_dir": str(output_dir),
        "candidates": len(candidates),
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
        prompt = f"""你是 CodeYun 星图笔记的语雀剩余文档导入助手。请根据输入 JSON，为每个待导入节点生成标题、分类 key、权重和一句内部备注。

硬性要求：
1. 必须为输入 items 中每一个 id 输出一条，不能漏、不能新增。
2. 输出必须严格匹配 schema：{{"items":[{{"id":"...","title":"...","category_key":"...","weight":0,"note":"..."}}]}}。
3. category_key 只能从 categories 的 key 中选择。
4. 权重策略：volume/year_root/section 可设 3；week_parent 固定 2；长文档、月度/团队总结、重要清单可设 1；普通日记、图片中转、目录容器默认 0。不要批量把普通条目设为 1。
5. title 必须使用输入 item 的 source_title 原样，不要概括、改写、补词；正文会使用语雀原文，note 只作内部备注。
6. 即使原始标题只是日期或容器名，也保留 source_title 原样；AI 只负责 category_key、weight 和 note。
7. 分类参考：pyxllib/工程/目录/代码/脚本 -> custom_mmxdyjjkxrsr；AI/模型/Trae/Codex/Gemini/DeepSeek -> custom_mmxdhqhnrgup；CodeYun/笔记系统 -> custom_mmx3qpfhinvh 或 custom_mmxdcghtzcw7；凡修/手游脚本 -> legacy_color_67c23a；考勤/退款/问卷/账单/账号/设备 -> legacy_color_e6a23c 或 custom_mmxbzxjy85x5；生活/学习/读书/游戏/照片 -> custom_mmxbzxjy85x5；不明确 -> general。

输入 JSON：
```json
{json.dumps(payload, ensure_ascii=False, indent=2)}
```
"""
        prompt_path.write_text(prompt, "utf-8")


def candidate_plan_id(item: dict[str, Any]) -> str:
    return f"remaining:{item['doc_id']}"


def validate_plan_file(input_path: Path, plan_path: Path) -> None:
    payload = json.loads(input_path.read_text("utf-8"))
    expected = {item["id"] for item in payload["items"]}
    plan = json.loads(plan_path.read_text("utf-8"))
    actual = {item.get("id") for item in plan.get("items", [])}
    if expected != actual:
        raise RuntimeError(f"{plan_path.name} id mismatch missing={sorted(expected - actual)[:8]} extra={sorted(actual - expected)[:8]}")


def run_plan(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    schema_path = output_dir / "plan_schema.json"
    batch_dir = output_dir / "batches"
    codex_bin = shutil.which("codex.cmd") or shutil.which("codex") or "codex"
    for prompt_path in sorted(batch_dir.glob("batch_*_prompt.md")):
        batch_no = prompt_path.stem.replace("_prompt", "")
        plan_path = batch_dir / f"{batch_no}_plan.json"
        stdout_path = batch_dir / f"{batch_no}_stdout.log"
        stderr_path = batch_dir / f"{batch_no}_stderr.log"
        if plan_path.exists() and not args.force:
            print(f"skip existing {plan_path.name}")
            continue
        cmd = [
            codex_bin, "exec",
            "-m", args.model,
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
            input=prompt_path.read_text("utf-8"),
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


def normalize_weight(kind: str, planned_weight: int) -> int:
    if kind in {"volume", "year_root", "section"}:
        return 3
    if kind == "week_parent":
        return 2
    if kind == "container":
        return 0
    return max(0, min(int(planned_weight), 1))


def source_kind(kind: str) -> str:
    return {
        "volume": "yuque_remaining_volume",
        "year_root": "yuque_remaining_year",
        "section": "yuque_remaining_section",
        "week_parent": "yuque_remaining_week",
        "month_doc": "yuque_remaining_month",
        "container": "yuque_remaining_container",
        "doc": "yuque_remaining_doc",
    }[kind]


def normalized_source_title(item: dict[str, Any], planned_title: str = "") -> str:
    return re.sub(r"\s+", " ", str(item.get("title") or planned_title or "")).strip() or "无标题"


def fields_for(item: dict[str, Any], kind: str, stats: dict[str, Any]) -> str:
    fields = safe_json_loads(custom_fields_base(item, kind, stats), [])
    extras = [
        ["source_parent_doc_key", "string", item.get("parent_source_key") or ""],
        ["source_root_title", "string", item.get("root_title") or ""],
        ["source_depth", "number", item.get("depth") or 0],
        ["source_has_media", "number", 1 if item.get("has_media") else 0],
    ]
    return json.dumps(fields + [row for row in extras if row[2] != ""], ensure_ascii=False)


def start_at_for(item: dict[str, Any]) -> float:
    if item.get("week"):
        return timestamp_from_week(str(item["week"]), 9)
    if item.get("date"):
        return timestamp_from_iso(None, str(item["date"]), 12)
    return timestamp_from_iso(item.get("published_at") or item.get("created_at"))


def import_candidates(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    import_name = args.import_name or IMPORT_NAME
    payload = json.loads((output_dir / "candidates.json").read_text("utf-8"))
    candidates = payload["candidates"]
    plans = load_plans(output_dir)
    missing_plan = [candidate_plan_id(item) for item in candidates if candidate_plan_id(item) not in plans]
    if missing_plan:
        raise RuntimeError(f"{len(missing_plan)} candidates do not have plan, sample: {missing_plan[:10]}")

    backup = Path(tempfile.gettempdir()) / f"codeyun_yuque_remaining_backup_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(db_path(data_dir), backup)

    session = yuque_session()
    media = MediaLocalizer(session, attachments_dir(data_dir))
    con = sqlite3.connect(db_path(data_dir), timeout=60)
    con.row_factory = sqlite3.Row
    source_map = existing_source_keys(con)
    node_by_source_key = {key: row["id"] for key, row in source_map.items()}

    inserted: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str]] = []
    edges = 0
    order = {"volume": 0, "year_root": 1, "section": 2, "container": 3, "week_parent": 4, "month_doc": 5, "doc": 6}
    for item in sorted(candidates, key=lambda obj: (int(obj.get("depth") or 0), order.get(obj.get("kind"), 9), obj.get("path") or "")):
        source_key = item["source_key"]
        if source_key in source_map:
            skipped.append((candidate_plan_id(item), "source_key_exists"))
            node_by_source_key[source_key] = source_map[source_key]["id"]
            continue
        item["source_import"] = import_name
        plan = plans[candidate_plan_id(item)]
        category = plan["category_key"] if plan["category_key"] in CATEGORY_HINTS else "general"
        title = normalized_source_title(item, plan.get("title") or "")
        item["title"] = title
        weight = normalize_weight(item["kind"], int(plan["weight"]))
        content, stats = media.rewrite(item.get("content") or "<p><br></p>")
        node_id = insert_node(con, title, content or "<p><br></p>", weight, start_at_for(item), category, fields_for(item, source_kind(item["kind"]), stats))
        node_by_source_key[source_key] = node_id
        source_map[source_key] = {"id": node_id, "title": title}  # type: ignore[assignment]
        inserted.append((candidate_plan_id(item), node_id, title))

        parent_key = item.get("parent_source_key") or ""
        if parent_key and insert_edge(con, node_by_source_key.get(parent_key), node_id):
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


def validate(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    payload = json.loads((output_dir / "candidates.json").read_text("utf-8"))
    planned_keys = {item["source_key"] for item in payload["candidates"]}
    attach_dir = attachments_dir(data_dir)
    con = sqlite3.connect(db_path(data_dir))
    con.row_factory = sqlite3.Row
    source_map = existing_source_keys(con)
    missing = sorted(key for key in planned_keys if key not in source_map)
    rows = con.execute(
        "select title,start_at,weight,content from notenode where user_id=? and custom_fields like ?",
        (USER_ID, f"%{IMPORT_NAME}%"),
    ).fetchall()
    local_refs = 0
    external_refs = []
    missing_files = []
    years = Counter()
    weights = Counter()
    for row in rows:
        try:
            years[dt.datetime.fromtimestamp(float(row["start_at"]), TZ).year] += 1
        except Exception:
            pass
        weights[int(row["weight"])] += 1
        soup = BeautifulSoup(row["content"] or "", "html.parser")
        for tag, attr in [(image, "src") for image in soup.find_all("img")] + [(link, "href") for link in soup.find_all("a")]:
            value = tag.get(attr) or ""
            if value.startswith("http://") or value.startswith("https://") or value.startswith("//"):
                external_refs.append((row["title"], value[:120]))
            if value.startswith("/static/attachments/"):
                local_refs += 1
                if not (attach_dir / Path(value).name).exists():
                    missing_files.append((row["title"], value))
    con.close()
    print(json.dumps({
        "planned_source_keys": len(planned_keys),
        "missing_source_key_count": len(missing),
        "missing_source_keys_sample": missing[:20],
        "imported_nodes": len(rows),
        "year_counts": dict(sorted(years.items())),
        "weight_distribution": dict(sorted(weights.items())),
        "local_refs": local_refs,
        "external_ref_count": len(external_refs),
        "external_refs_sample": external_refs[:10],
        "missing_file_count": len(missing_files),
        "missing_files_sample": missing_files[:10],
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--data-dir", default="")
    prepare_parser.add_argument("--output-dir", default="")
    prepare_parser.add_argument("--batch-size", type=int, default=28)
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

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--data-dir", default="")
    validate_parser.add_argument("--output-dir", default="")
    validate_parser.set_defaults(func=validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
