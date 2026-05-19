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
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

try:
    from resource_identity_sqlite import allocate_note_numeric_id, insert_note_edge, note_public_ref
except ImportError:  # pragma: no cover - supports package imports in tests/tools
    from scripts.resource_identity_sqlite import allocate_note_numeric_id, insert_note_edge, note_public_ref


USER_ID = 2
TZ = dt.timezone(dt.timedelta(hours=8))
IMPORT_NAME = "codex-cli-semantic-calendar-split-v1"
SEMANTIC_SOURCE_KIND = "semantic_calendar_item"
AGGREGATE_SOURCE_KINDS = {"calendar_table_cell", "yuque_legacy_day"}
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DATE_HEADING_RE = re.compile(r"^\s*\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*周[一二三四五六日天]\s*")
W_TITLE_PREFIX_RE = re.compile(r"^\s*w\d{6}\s*[:：]\s*", re.IGNORECASE)
ITEM_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:\d+[、.．]|[一二三四五六七八九十]+[、.．])\s*")
IMPORT_MARKUP_DEBRIS_RE = re.compile(r"\b(?:style|lang)\s*=", re.IGNORECASE)
RESOURCE_KEYWORD_RE = re.compile(
    r"(?:密码|注册码|用户码|账号|账户|密钥|license|serial|绑定邮箱|Google账户|Google账号)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_TOKEN_RE = re.compile(r"(?=[A-Za-z0-9+/=_@#$%^&*|~`?.-]{12,})(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9+/=_@#$%^&*|~`?.-]+")
CATEGORY_PALETTE_SETTING_KEY = f"note.category_palette.user.{USER_ID}"
MIGRATION_CATEGORY_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "key": "import_study",
        "label": "学业",
        "color": "#4C78A8",
        "description": "学校、课程、考试、竞赛、作业、学习计划、成绩和校园学习经历。",
    },
    {
        "key": "import_programming",
        "label": "编程/技术",
        "color": "#2C9BA7",
        "description": "编程语言、算法、软件工具、服务器、脚本、数据处理、技术项目和技术学习。",
    },
    {
        "key": "import_work",
        "label": "工作/项目",
        "color": "#A57C00",
        "description": "工作推进、项目管理、客户沟通、运营、交付、会议和职业事务。",
    },
    {
        "key": "import_life",
        "label": "生活后勤",
        "color": "#CCB38C",
        "description": "日常安排、家务、购物、证件、账号、生活琐事、居住和后勤处理。",
    },
    {
        "key": "import_media",
        "label": "阅读影音",
        "color": "#7E57C2",
        "description": "书籍、文章、电影、剧集、音乐、视频、课程材料和内容消费。",
    },
    {
        "key": "import_game",
        "label": "游戏娱乐",
        "color": "#7CB342",
        "description": "游戏、娱乐活动、休闲玩法、二次元内容和线上娱乐。",
    },
    {
        "key": "import_social",
        "label": "人际家庭",
        "color": "#F06292",
        "description": "同学、朋友、老师、家人、恋爱、人际关系、聚会和社交互动。",
    },
    {
        "key": "import_reflection",
        "label": "情绪反思",
        "color": "#8D6E63",
        "description": "心情、价值判断、自我观察、梦、反省、困惑、信念和心理状态。",
    },
    {
        "key": "import_finance",
        "label": "财务消费",
        "color": "#009688",
        "description": "收入、支出、账单、基金、投资、价格、套餐、缴费和消费决策。",
    },
    {
        "key": "import_health",
        "label": "健康作息",
        "color": "#E67E22",
        "description": "睡眠、身体状态、运动、饮食、疾病、心理健康和作息管理。",
    },
    {
        "key": "import_travel",
        "label": "出行活动",
        "color": "#5C6BC0",
        "description": "旅行、返乡、交通、爬山、外出、活动现场和地点移动。",
    },
    {
        "key": "import_archive",
        "label": "资料工具",
        "color": "#607D8B",
        "description": "资料整理、文档、收藏、链接、账号信息、工具清单和信息归档。",
    },
)
MIGRATION_CATEGORY_BY_KEY = {str(item["key"]): item for item in MIGRATION_CATEGORY_ITEMS}
DEFAULT_CATEGORY_KEY = "general"
GENERAL_CATEGORY_DESCRIPTION = "默认兜底分类。只有在确实无法归入任何更具体分类时使用。"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


@dataclass
class SourceRow:
    id: str
    numeric_id: int | None
    title: str
    content: str
    start_at: float
    weight: int
    custom_fields: str
    node_type: str
    note_types: str
    note_categories: str
    primary_category: str
    note_form: str
    private_level: int
    color: str | None
    fields: dict[str, Any]
    source_kind: str
    date: dt.date
    semantic_text: str
    normalized_text: str


def source_row_public_id(row: SourceRow) -> int | None:
    return int(row.numeric_id) if row.numeric_id is not None else None


@dataclass
class SourceGroup:
    group_id: str
    date: dt.date
    rows: list[SourceRow]
    text: str
    normalized_text: str
    metadata_row: SourceRow
    weight: int


@dataclass
class SemanticItem:
    title: str
    content: str
    primary_category: str | None = None
    weight: int | None = None


def default_data_dir() -> Path:
    return Path(os.environ.get("CODEYUN_DATA_DIR", r"D:\home\chenkunze\data\m2603codeyun\codepc_mf"))


def db_path(data_dir: Path) -> Path:
    return data_dir / "codeyun.db"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def codex_command() -> list[str]:
    repo_tools_node = repo_root() / "tools" / "node"
    candidates = [
        repo_tools_node / "codex.cmd",
        Path.home() / "scoop" / "apps" / "nodejs-lts" / "current" / "bin" / "codex.cmd",
        Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "codex.cmd",
        Path.home() / ".cargo" / "bin" / "codex.cmd",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        if candidate.parent == repo_tools_node:
            package_script = repo_tools_node / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            if not package_script.exists():
                continue
            return [os.fspath(candidate)]
        return [os.fspath(candidate)]
    resolved = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
    if resolved:
        resolved_path = Path(resolved)
        if resolved_path.parent == repo_tools_node:
            package_script = repo_tools_node / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            if not package_script.exists():
                resolved = ""
        if resolved:
            return [resolved]
    return ["codex"]


def safe_json_loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def custom_fields_map(raw: str | None) -> dict[str, Any]:
    rows = safe_json_loads(raw, [])
    if isinstance(rows, dict):
        return rows
    result: dict[str, Any] = {}
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, list) and len(item) >= 3:
            result[str(item[0])] = item[2]
        elif isinstance(item, dict) and item.get("key"):
            result[str(item["key"])] = item.get("value")
    return result


def custom_fields_rows(raw: str | None) -> list[list[Any]]:
    rows = safe_json_loads(raw, [])
    result: list[list[Any]] = []
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, list) and len(item) >= 3:
            result.append([str(item[0]), str(item[1] or "string"), item[2]])
        elif isinstance(item, dict) and item.get("key"):
            value_type = str(item.get("type") or "string")
            result.append([str(item["key"]), value_type, item.get("value")])
    return result


def normalize_palette_item(item: dict[str, Any], *, fallback_order: int) -> dict[str, Any] | None:
    key = str(item.get("key") or "").strip()
    if not key:
        return None
    label = str(item.get("label") or key).strip() or key
    color = str(item.get("color") or "").strip() or None
    try:
        order = int(item.get("order"))
    except (TypeError, ValueError):
        order = fallback_order
    normalized = {
        "key": key,
        "label": label,
        "color": color,
        "order": order,
        "builtin": bool(item.get("builtin", False)),
        "source": str(item.get("source") or "custom"),
        "generated_from_color": item.get("generated_from_color"),
    }
    description = str(item.get("description") or MIGRATION_CATEGORY_BY_KEY.get(key, {}).get("description") or "").strip()
    if description:
        normalized["description"] = description
    return normalized


def load_category_palette_items(con: sqlite3.Connection, *, include_import_categories: bool = True) -> list[dict[str, Any]]:
    row = con.execute("select value from appsetting where key=?", (CATEGORY_PALETTE_SETTING_KEY,)).fetchone()
    payload = safe_json_loads(row["value"], {}) if row else {}
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items if isinstance(raw_items, list) else []):
        if not isinstance(item, dict):
            continue
        normalized = normalize_palette_item(item, fallback_order=index * 10)
        if not normalized or normalized["key"] in seen:
            continue
        seen.add(str(normalized["key"]))
        items.append(normalized)

    if DEFAULT_CATEGORY_KEY not in seen:
        items.append(
            {
                "key": DEFAULT_CATEGORY_KEY,
                "label": "综合",
                "color": "#808080",
                "order": 0,
                "builtin": True,
                "source": "builtin",
                "generated_from_color": None,
                "description": GENERAL_CATEGORY_DESCRIPTION,
            }
        )
        seen.add(DEFAULT_CATEGORY_KEY)

    if include_import_categories:
        max_order = max([parse_int(item.get("order"), 0) for item in items], default=0)
        for offset, item in enumerate(MIGRATION_CATEGORY_ITEMS, start=1):
            key = str(item["key"])
            if key in seen:
                continue
            items.append(
                {
                    "key": key,
                    "label": str(item["label"]),
                    "color": str(item["color"]),
                    "order": max_order + offset * 10,
                    "builtin": False,
                    "source": "import",
                    "generated_from_color": None,
                    "description": str(item["description"]),
                }
            )
            seen.add(key)
    return sorted(items, key=lambda item: (parse_int(item.get("order"), 0), str(item.get("label") or "")))


def ensure_import_category_palette(con: sqlite3.Connection) -> bool:
    row = con.execute("select value from appsetting where key=?", (CATEGORY_PALETTE_SETTING_KEY,)).fetchone()
    payload = safe_json_loads(row["value"], {}) if row else {}
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    changed = False
    for index, item in enumerate(raw_items if isinstance(raw_items, list) else []):
        if not isinstance(item, dict):
            changed = True
            continue
        normalized = normalize_palette_item(item, fallback_order=index * 10)
        if not normalized:
            changed = True
            continue
        key = str(normalized["key"])
        if key in seen:
            changed = True
            continue
        if key == DEFAULT_CATEGORY_KEY and not normalized.get("description"):
            normalized["description"] = GENERAL_CATEGORY_DESCRIPTION
            changed = True
        seen.add(key)
        items.append(normalized)

    if DEFAULT_CATEGORY_KEY not in seen:
        items.append(
            {
                "key": DEFAULT_CATEGORY_KEY,
                "label": "综合",
                "color": "#808080",
                "order": 0,
                "builtin": True,
                "source": "builtin",
                "generated_from_color": None,
                "description": GENERAL_CATEGORY_DESCRIPTION,
            }
        )
        seen.add(DEFAULT_CATEGORY_KEY)
        changed = True

    max_order = max([parse_int(item.get("order"), 0) for item in items], default=0)
    for offset, item in enumerate(MIGRATION_CATEGORY_ITEMS, start=1):
        key = str(item["key"])
        if key in seen:
            continue
        items.append(
            {
                "key": key,
                "label": str(item["label"]),
                "color": str(item["color"]),
                "order": max_order + offset * 10,
                "builtin": False,
                "source": "import",
                "generated_from_color": None,
                "description": str(item["description"]),
            }
        )
        seen.add(key)
        changed = True

    if not changed:
        return False
    payload = {"items": sorted(items, key=lambda item: (parse_int(item.get("order"), 0), str(item.get("label") or "")))}
    now = time.time()
    if row:
        con.execute("update appsetting set value=?,updated_at=? where key=?", (json.dumps(payload, ensure_ascii=False), now, CATEGORY_PALETTE_SETTING_KEY))
    else:
        con.execute("insert into appsetting(key,value,updated_at) values (?,?,?)", (CATEGORY_PALETTE_SETTING_KEY, json.dumps(payload, ensure_ascii=False), now))
    return True


def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def strip_import_markup_debris(text: str) -> str:
    value = html.unescape(str(text or ""))
    has_debris = bool(IMPORT_MARKUP_DEBRIS_RE.search(value))
    if not has_debris:
        return value
    value = re.sub(r"\bstyle\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s>]+)\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\blang\s*=\s*[a-z]{2}(?:-[a-z0-9]+)?\s*>?", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\blang\s*=\s*…\s*:?", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:span|font|p|div|br)\s*>", "", value, flags=re.IGNORECASE)
    value = value.replace(">", "")
    return value


def normalize_calendar_source_noise(text: str) -> str:
    value = str(text or "")
    value = re.sub(
        r"^((?:\d{1,2}:\d{2}/){1,}\d{1,2}:\d{2})\s*[，,]?\s*(?:凌晨|早上|上午|中午|下午|傍晚|晚上|晚)?\s*[-—]+\s*(?=\d+[、.．])",
        r"\1\n",
        value,
    )
    return value


def plain_text_from_html(content: str) -> str:
    text = BeautifulSoup(content or "", "html.parser").get_text("\n")
    text = strip_import_markup_debris(text)
    text = normalize_calendar_source_noise(text)
    text = text.replace("\\n", "\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_calendar_heading(text: str) -> str:
    value = strip_import_markup_debris(text).strip()
    value = DATE_HEADING_RE.sub("", value).strip()
    value = W_TITLE_PREFIX_RE.sub("", value).strip()
    return value


def semantic_text_for_row(row: sqlite3.Row, fields: dict[str, Any]) -> str:
    content_text = strip_calendar_heading(plain_text_from_html(str(row["content"] or "")))
    title = strip_calendar_heading(str(row["title"] or "").strip())
    source_title = strip_calendar_heading(str(fields.get("source_title") or "").strip())
    candidates = [(content_text, 3), (source_title, 2), (title, 1)]

    def rank(candidate: tuple[str, int]) -> tuple[int, int, int]:
        value, source_priority = candidate
        clean = value.strip()
        if not clean:
            return (-1, 0, 0)
        ellipsis_penalty = -1 if "…" in clean else 0
        return (source_priority, ellipsis_penalty, len(clean))

    best = max(candidates, key=rank, default=("", 0))[0]
    return normalize_display_text(best)


def normalize_display_text(text: str) -> str:
    value = strip_import_markup_debris(text)
    value = normalize_calendar_source_noise(value)
    value = (value or "").replace("\\n", "\n").replace("\xa0", " ")
    value = re.sub(r"(\d+)\s*\n\s*([、.．])", r"\1\2", value)
    value = re.sub(r"([\u4e00-\u9fff])\s*\n\s*([A-Za-z0-9]{1,12})\s*\n\s*([\u4e00-\u9fff])", r"\1\2\3", value)
    value = re.sub(r"\n\s*([，。；：、！？,.!?;:])", r"\1", value)
    value = re.sub(r"([，。；：、！？,.!?;:])\s*\n", r"\1", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *([，。；：、！？,.!?;:]) *", r"\1", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip(" \n\t，,。；;")


def normalized_key_text(text: str) -> str:
    value = normalize_display_text(text)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，,。；;、]+$", "", value)
    return value


def compact_text(text: str, limit: int = 42) -> str:
    value = re.sub(r"\s+", " ", normalize_display_text(text)).strip(" ，,。；;")
    value = W_TITLE_PREFIX_RE.sub("", value).strip()
    value = ITEM_NUMBER_PREFIX_RE.sub("", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def normalize_generated_title(title: str, content: str, *, limit: int = 32) -> str:
    value = normalize_display_text(title)
    value = strip_calendar_heading(value)
    value = ITEM_NUMBER_PREFIX_RE.sub("", value).strip()
    value = re.sub(r"^(综合|杂项|多项事项|事项|日记|记录|总结)[：:\s]*", "", value, flags=re.IGNORECASE).strip()
    value = value.strip(" ：:，,。；;、")
    content_value = normalize_display_text(content)
    if not value or len(value) > 44 or normalized_key_text(value) == normalized_key_text(content_value):
        value = heuristic_title(content_value)
    return compact_text(value, limit)


def heuristic_title(text: str, limit: int = 32) -> str:
    value = normalize_display_text(text)
    value = strip_calendar_heading(value)
    value = ITEM_NUMBER_PREFIX_RE.sub("", value).strip()
    value = re.split(r"\n|。|；|;|！|!|？|\?", value, maxsplit=1)[0]
    value = re.split(r"，|,|、", value, maxsplit=1)[0] if len(value) > 18 else value
    return compact_text(value or text, limit)


def infer_category_key(text: str, *, fallback: str = DEFAULT_CATEGORY_KEY) -> str:
    value = normalize_display_text(text).lower()
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("import_programming", ("python", "pyxllib", "sql", "c++", "java", "latex", "tex", "acm", "算法", "代码", "脚本", "服务器", "接口", "模型", "编程", "数据库", "bug", "github", "git", "npm", "vue", "前端", "后端")),
        ("import_study", ("考试", "竞赛", "作业", "老师", "课程", "上课", "自习", "高考", "大学", "学校", "英语", "数学", "物理", "生物", "成绩", "学习")),
        ("import_finance", ("钱", "账单", "余额", "基金", "投资", "工资", "收入", "支出", "消费", "套餐", "缴费", "退款", "公积金")),
        ("import_health", ("睡", "困", "醒", "跑步", "运动", "病", "身体", "健康", "心理", "作息", "疲惫")),
        ("import_game", ("dnf", "3c", "游戏", "网吧", "仙剑", "轩辕剑", "魔兽", "dota", "lol")),
        ("import_media", ("电影", "视频", "音乐", "歌", "小说", "书", "文章", "博客", "阅读", "课程", "教材", "电视剧")),
        ("import_social", ("同学", "朋友", "老师", "父母", "家人", "聚会", "恋", "聊天", "沟通", "关系", "qq")),
        ("import_travel", ("回家", "出门", "爬山", "旅行", "坐车", "车站", "动车", "火车", "外出")),
        ("import_reflection", ("心情", "反思", "想法", "梦", "迷茫", "焦虑", "开心", "难过", "成长", "价值", "人生", "情绪")),
        ("import_archive", ("资料", "文档", "链接", "收藏", "账号", "密码", "整理", "清单", "工具")),
        ("import_work", ("工作", "客户", "会议", "汇报", "项目", "需求", "交付", "运营", "后勤")),
    ]
    for key, keywords in rules:
        if any(keyword.lower() in value for keyword in keywords):
            return key
    return fallback or DEFAULT_CATEGORY_KEY


def has_sensitive_resource_token(text: str) -> bool:
    value = normalize_display_text(text)
    if EMAIL_RE.search(value):
        return True
    if RESOURCE_KEYWORD_RE.search(value) and SECRET_TOKEN_RE.search(value):
        return True
    return False


def resource_title(text: str) -> str:
    value = normalize_display_text(text)
    if re.search(r"Google\s*(?:账户|账号)|(?:账户|账号).*Google", value, re.IGNORECASE):
        return "Google账号关联"
    if "注册码" in value or re.search(r"\blicense\b|\bserial\b", value, re.IGNORECASE):
        return "软件注册码"
    if "用户码" in value:
        return "软件用户码"
    if "密码" in value:
        return "账号密码资料"
    if "账号" in value or "账户" in value:
        return "账号资料"
    if EMAIL_RE.search(value):
        return "邮箱账号"
    return "资料记录"


def resource_single_item(group: SourceGroup) -> SemanticItem:
    category = "import_archive"
    if group.metadata_row.primary_category and group.metadata_row.primary_category != DEFAULT_CATEGORY_KEY:
        category = group.metadata_row.primary_category
    return SemanticItem(
        title=resource_title(group.text),
        content=group.text,
        primary_category=category,
        weight=max(group.weight, 1 if RESOURCE_KEYWORD_RE.search(group.text) else group.weight),
    )


def normalize_category_key(value: Any, allowed_keys: set[str], *, text: str, fallback: str = DEFAULT_CATEGORY_KEY) -> str:
    key = str(value or "").strip()
    if key in allowed_keys:
        return key
    inferred = infer_category_key(text, fallback=fallback)
    if inferred in allowed_keys:
        return inferred
    fallback_key = str(fallback or DEFAULT_CATEGORY_KEY).strip()
    if fallback_key in allowed_keys:
        return fallback_key
    return DEFAULT_CATEGORY_KEY


def date_from_row(row: sqlite3.Row, fields: dict[str, Any]) -> dt.date | None:
    source_date = str(fields.get("source_date") or "").strip()
    if source_date:
        try:
            return dt.date.fromisoformat(source_date[:10])
        except ValueError:
            pass
    try:
        return dt.datetime.fromtimestamp(float(row["start_at"]), TZ).date()
    except (TypeError, ValueError, OSError):
        return None


def source_rich_weight(row: SourceRow) -> int:
    return max(row.weight, parse_int(row.fields.get("source_rich_weight"), 0))


def metadata_rank(row: SourceRow) -> tuple[int, int, int]:
    category_bonus = 20 if row.primary_category and row.primary_category != "general" else 0
    legacy_bonus = 10 if row.source_kind == "yuque_legacy_day" else 0
    form_bonus = 2 if row.note_form == "note" else 0
    return (category_bonus + legacy_bonus + form_bonus, source_rich_weight(row), len(row.semantic_text))


def text_rank(row: SourceRow) -> tuple[int, int, int]:
    calendar_bonus = 8 if row.source_kind == "calendar_table_cell" else 0
    source_bonus = 3 if row.source_kind == "yuque_legacy_day" else 0
    clean_bonus = 2 if "\\n" not in row.semantic_text else 0
    return (calendar_bonus + source_bonus + clean_bonus, len(row.semantic_text), source_rich_weight(row))


def load_source_rows(
    con: sqlite3.Connection,
    *,
    year: int | None,
    include_hidden_sources: bool,
    include_week_context: bool,
) -> list[SourceRow]:
    rows = con.execute(
        """
        select id,numeric_id,title,content,start_at,weight,custom_fields,node_type,note_types,
               note_categories,primary_category,note_form,private_level,color
        from notenode
        where user_id=? and custom_fields like '%source_kind%'
        order by start_at,numeric_id,title
        """,
        (USER_ID,),
    ).fetchall()
    result: list[SourceRow] = []
    for row in rows:
        fields = custom_fields_map(row["custom_fields"])
        source_kind = str(fields.get("source_kind") or "")
        if source_kind not in AGGREGATE_SOURCE_KINDS:
            continue
        if (
            not include_week_context
            and str(fields.get("source_pattern") or "") == "daily_rows"
            and str(fields.get("source_column") or "").strip() == "周内容"
        ):
            continue
        if not include_hidden_sources and parse_int(row["private_level"], 0) > 0:
            continue
        day = date_from_row(row, fields)
        if not day:
            continue
        if year is not None and day.year != year:
            continue
        text = semantic_text_for_row(row, fields)
        normalized = normalized_key_text(text)
        if not normalized:
            continue
        result.append(
            SourceRow(
                id=str(row["id"]),
                numeric_id=int(row["numeric_id"]) if row["numeric_id"] is not None else None,
                title=str(row["title"] or ""),
                content=str(row["content"] or ""),
                start_at=float(row["start_at"] or 0),
                weight=parse_int(row["weight"], 0),
                custom_fields=str(row["custom_fields"] or "[]"),
                node_type=str(row["node_type"] or "general"),
                note_types=str(row["note_types"] or "[]"),
                note_categories=str(row["note_categories"] or "[]"),
                primary_category=str(row["primary_category"] or row["node_type"] or "general"),
                note_form=str(row["note_form"] or "note"),
                private_level=parse_int(row["private_level"], 0),
                color=row["color"],
                fields=fields,
                source_kind=source_kind,
                date=day,
                semantic_text=text,
                normalized_text=normalized,
            )
        )
    return result


def group_source_rows(rows: list[SourceRow], *, selected_ids: set[str]) -> list[SourceGroup]:
    by_key: dict[tuple[str, str], list[SourceRow]] = defaultdict(list)
    for row in rows:
        by_key[(row.date.isoformat(), row.normalized_text)].append(row)

    groups: list[SourceGroup] = []
    for (date_text, normalized), group_rows in by_key.items():
        if selected_ids:
            row_ids = {row.id for row in group_rows} | {str(row.numeric_id) for row in group_rows if row.numeric_id}
            if not (row_ids & selected_ids):
                continue
        best_text_row = max(group_rows, key=text_rank)
        metadata_row = max(group_rows, key=metadata_rank)
        digest = hashlib.sha1(f"{date_text}\n{normalized}".encode("utf-8")).hexdigest()[:16]
        groups.append(
            SourceGroup(
                group_id=digest,
                date=best_text_row.date,
                rows=group_rows,
                text=best_text_row.semantic_text,
                normalized_text=normalized,
                metadata_row=metadata_row,
                weight=max(source_rich_weight(row) for row in group_rows),
            )
        )
    return sorted(groups, key=lambda group: (group.date, group.text))


def build_codex_prompt(groups: list[SourceGroup], category_items: list[dict[str, Any]]) -> str:
    categories = [
        {
            "key": str(item.get("key") or "").strip(),
            "label": str(item.get("label") or "").strip(),
            "description": str(item.get("description") or MIGRATION_CATEGORY_BY_KEY.get(str(item.get("key") or ""), {}).get("description") or "").strip(),
        }
        for item in category_items
        if str(item.get("key") or "").strip()
    ]
    payload = {
        "categories": categories,
        "groups": [
            {
                "group_id": group.group_id,
                "date": group.date.isoformat(),
                "text": group.text,
                "source_rich_weight": group.weight,
                "source_category": group.metadata_row.primary_category,
                "source_context": [
                    {
                        "source_kind": row.source_kind,
                        "source_column": normalize_display_text(str(row.fields.get("source_column") or "")),
                        "source_title": normalize_display_text(str(row.fields.get("source_title") or row.title or "")),
                        "source_has_bold": parse_int(row.fields.get("source_has_bold"), 0),
                        "source_has_red": parse_int(row.fields.get("source_has_red"), 0),
                        "source_has_link": parse_int(row.fields.get("source_has_link"), 0),
                    }
                    for row in group.rows[:4]
                ],
            }
            for group in groups
        ]
    }
    return "\n".join(
        [
            "你在帮助 CodeYun 迁移旧日历表格笔记，把一个日期单元格里的内容拆成当天多个独立笔记节点。",
            "目标是“语义拆分”，不是按逗号、顿号、书名号或换行机械分割。",
            "",
            "判断规则：",
            "1. 只有当文本里确实包含多个相对独立的事件、想法、任务、成果、作品/活动记录时，才拆成多项。",
            "2. 不要拆开一个完整标题、书名、游戏名、时间表达、排名表达、人物名、短语内部枚举。",
            "3. 资源类记录要少拆或不拆：账号、密码、邮箱、注册码、license、用户码、链接清单、工具清单、资料索引等通常作为一个资料节点保留。",
            "4. 不要把账号、邮箱、注册码、密钥本身放进 title；title 只写资源对象，例如“Google账号关联”“软件注册码”“资料链接”。",
            "5. 保留原始意思和措辞，尽量少改写；可以去掉导入残留的字面量 \\n 和多余空格。",
            "6. 每个拆出的 item 都要能作为日历上的一个独立卡片标题和正文；正文保留原始信息，标题做关键词化提炼。",
            "7. title 必须短而具体，优先 4~16 个汉字或等长短语；不要带日期、不要带 wYYMMDD 前缀、不要照搬整句正文、不要写“事项1/拆分项/综合”。",
            "8. title 不要保留流水叙事和啰嗦词：想、准备、一些、相关、问题、工作、做了、整理一下、沟通工作等只在正文保留；标题提炼成对象+动作。",
            "9. primary_category 必须从输入 categories 的 key 中选择；除非完全没有更具体类别，否则不要用 general。",
            "10. weight 为 0~3 的整数。source_rich_weight 是源富文本强调的下限：普通 0，加粗/链接/轻微强调 1，红色/显著强调 2，红色加粗/极强权重 3。",
            "11. 如果无法确定可拆，就返回一项，但仍要给出精炼标题和合适分类。",
            "",
            "只输出 JSON 对象，不要 Markdown。格式：",
            '{"groups":[{"group_id":"...","items":[{"title":"...","content":"...","primary_category":"分类key","weight":0}]}]}',
            "",
            "输入：",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def parse_codex_json(content: str) -> dict[str, Any]:
    text = content.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("Codex CLI did not return a JSON object")
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise RuntimeError("Codex CLI JSON response must be an object")
    return payload


def call_codex(
    groups: list[SourceGroup],
    *,
    category_items: list[dict[str, Any]],
    model: str,
    timeout: int,
) -> dict[str, list[SemanticItem]]:
    prompt = build_codex_prompt(groups, category_items)
    allowed_category_keys = {str(item.get("key") or "").strip() for item in category_items if str(item.get("key") or "").strip()}
    with tempfile.TemporaryDirectory(prefix="codeyun-semantic-calendar-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.json"
        command = [
            *codex_command(),
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--ignore-rules",
            "--color",
            "never",
            "--json",
            "-C",
            os.fspath(repo_root()),
            "--output-last-message",
            os.fspath(output_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.fspath(repo_root()),
            timeout=timeout,
            check=False,
        )
        content = output_path.read_text("utf-8").strip() if output_path.exists() else ""
    if completed.returncode != 0:
        detail = "\n".join(line for line in [completed.stderr.strip(), completed.stdout.strip(), content] if line)
        raise RuntimeError(f"Codex CLI failed: {detail[-2000:]}")
    if not content:
        raise RuntimeError("Codex CLI returned no last message")

    payload = parse_codex_json(content)
    result: dict[str, list[SemanticItem]] = {}
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        raise RuntimeError("Codex CLI JSON missing groups array")
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        group_id = str(raw_group.get("group_id") or "").strip()
        items: list[SemanticItem] = []
        for raw_item in raw_group.get("items") or []:
            if not isinstance(raw_item, dict):
                continue
            content = normalize_display_text(str(raw_item.get("content") or ""))
            if not content:
                continue
            title = normalize_generated_title(str(raw_item.get("title") or ""), content)
            if not title:
                title = heuristic_title(content)
            category = normalize_category_key(
                raw_item.get("primary_category") or raw_item.get("category"),
                allowed_category_keys,
                text=f"{title}\n{content}",
            )
            weight = raw_item.get("weight")
            items.append(SemanticItem(title=title, content=content, primary_category=category, weight=parse_int(weight, -1)))
        if group_id and items:
            result[group_id] = items[:8]
    return result


def weekday_label(day: dt.date) -> str:
    return WEEKDAYS[day.weekday()]


def timestamp_for_date(day: dt.date) -> float:
    return dt.datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=TZ).timestamp()


def item_source_key(group: SourceGroup, index: int) -> str:
    return f"semantic-calendar-split:{group.group_id}:{index + 1}"


def existing_semantic_nodes(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = con.execute(
        """
        select id,numeric_id,title,custom_fields
        from notenode
        where user_id=? and custom_fields like ?
        """,
        (USER_ID, f"%{IMPORT_NAME}%"),
    ).fetchall()
    result: dict[str, sqlite3.Row] = {}
    for row in rows:
        key = custom_fields_map(row["custom_fields"]).get("source_key")
        if key:
            result[str(key)] = row
    return result


def note_categories(category: str) -> str:
    return json.dumps([{"key": category, "weight": 100}], ensure_ascii=False)


def content_html(group: SourceGroup, item: SemanticItem) -> str:
    escaped_heading = html.escape(f"{group.date.isoformat()} {weekday_label(group.date)}")
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", item.content) if part.strip()]
    body = [f"<h2>{escaped_heading}</h2>"]
    if not paragraphs:
        paragraphs = [item.content]
    for paragraph in paragraphs:
        body.append(
            '<p style="white-space:pre-wrap;line-height:1.65">'
            + html.escape(paragraph).replace("\n", "<br>")
            + "</p>"
        )
    return "\n".join(body)


def fields_for_item(group: SourceGroup, item: SemanticItem, index: int, split_count: int) -> str:
    parent_ids = [row.id for row in group.rows]
    parent_numeric_ids = [row.numeric_id for row in group.rows if row.numeric_id is not None]
    parent_source_keys = [str(row.fields.get("source_key") or "") for row in group.rows if row.fields.get("source_key")]
    rows: list[list[Any]] = [
        ["source", "string", "semantic-calendar-split"],
        ["source_import", "string", IMPORT_NAME],
        ["source_kind", "string", SEMANTIC_SOURCE_KIND],
        ["source_key", "string", item_source_key(group, index)],
        ["source_group_id", "string", group.group_id],
        ["source_date", "string", group.date.isoformat()],
        ["source_weekday", "string", weekday_label(group.date)],
        ["source_item_index", "number", index + 1],
        ["source_split_count", "number", split_count],
        ["source_parent_note_ids", "string", json.dumps(parent_ids, ensure_ascii=False)],
        ["source_parent_numeric_ids", "string", json.dumps(parent_numeric_ids, ensure_ascii=False)],
        ["source_parent_keys", "string", json.dumps(parent_source_keys, ensure_ascii=False)],
        ["source_original_text_hash", "string", hashlib.sha1(group.normalized_text.encode("utf-8")).hexdigest()],
        ["source_original_text", "string", group.text],
        ["source_generated_category", "string", item.primary_category or ""],
    ]
    return json.dumps(rows, ensure_ascii=False)


def insert_edge(con: sqlite3.Connection, source_id: str, target_id: str) -> bool:
    return insert_note_edge(
        con,
        user_id=USER_ID,
        source_id=source_id,
        target_id=target_id,
        edge_id=str(uuid.uuid4()),
    )


def note_payload(group: SourceGroup, item: SemanticItem, index: int, split_count: int) -> dict[str, Any]:
    meta = group.metadata_row
    category = item.primary_category or infer_category_key(item.content, fallback=meta.primary_category or meta.node_type or DEFAULT_CATEGORY_KEY)
    weight = item.weight if item.weight is not None and item.weight >= 0 else group.weight
    note_types = note_categories(category)
    note_categories_json = note_categories(category)
    return {
        "title": normalize_generated_title(item.title, item.content, limit=56),
        "content": content_html(group, item),
        "weight": max(0, min(3, int(weight))),
        "start_at": timestamp_for_date(group.date),
        "custom_fields": fields_for_item(group, item, index, split_count),
        "node_type": category,
        "note_types": note_types,
        "note_categories": note_categories_json,
        "primary_category": category,
        "note_form": "note",
        "private_level": 0,
        "color": meta.color,
    }


def insert_item(
    con: sqlite3.Connection,
    group: SourceGroup,
    item: SemanticItem,
    index: int,
    split_count: int,
) -> str:
    payload = note_payload(group, item, index, split_count)
    node_id = str(uuid.uuid4())
    numeric_id = allocate_note_numeric_id(con, node_id)
    now = time.time()
    con.execute(
        """
        insert into notenode(
            id,numeric_id,user_id,title,content,created_at,updated_at,weight,start_at,task_status,history,
            node_type,node_status,custom_fields,private_level,color,note_kind,weight_mode,
            note_types,note_categories,primary_category,note_form,lifecycle_stage,note_scene
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            node_id,
            numeric_id,
            USER_ID,
            payload["title"],
            payload["content"],
            now,
            now,
            payload["weight"],
            payload["start_at"],
            None,
            "[]",
            payload["node_type"],
            "done",
            payload["custom_fields"],
            payload["private_level"],
            payload["color"],
            "note",
            None,
            payload["note_types"],
            payload["note_categories"],
            payload["primary_category"],
            payload["note_form"],
            "done",
            "note",
        ),
    )
    return node_id


def update_item(
    con: sqlite3.Connection,
    existing_id: str,
    group: SourceGroup,
    item: SemanticItem,
    index: int,
    split_count: int,
) -> None:
    payload = note_payload(group, item, index, split_count)
    con.execute(
        """
        update notenode
        set title=?,content=?,updated_at=?,weight=?,start_at=?,custom_fields=?,
            node_type=?,note_types=?,note_categories=?,primary_category=?,note_form=?,
            private_level=?,color=?,node_status=?,lifecycle_stage=?,note_scene=?,note_kind=?
        where id=? and user_id=?
        """,
        (
            payload["title"],
            payload["content"],
            time.time(),
            payload["weight"],
            payload["start_at"],
            payload["custom_fields"],
            payload["node_type"],
            payload["note_types"],
            payload["note_categories"],
            payload["primary_category"],
            payload["note_form"],
            payload["private_level"],
            payload["color"],
            "done",
            "done",
            "note",
            "note",
            existing_id,
            USER_ID,
        ),
    )


def hide_or_delete_sources(con: sqlite3.Connection, groups: list[SourceGroup], source_action: str) -> int:
    touched = 0
    for group in groups:
        for row in group.rows:
            touched += hide_or_delete_row(con, row.id, source_action)
    return touched


def hide_or_delete_row(con: sqlite3.Connection, node_id: str, source_action: str) -> int:
    if source_action == "hide":
        cursor = con.execute(
            """
            update notenode
            set private_level=?,updated_at=?
            where user_id=? and id=? and private_level<=?
            """,
            (1, time.time(), USER_ID, node_id, 0),
        )
        return max(0, cursor.rowcount)
    if source_action == "delete":
        node_ref = note_public_ref(con, node_id)
        con.execute("delete from noteedge where user_id=? and (source_id=? or target_id=?)", (USER_ID, node_ref, node_ref))
        cursor = con.execute("delete from notenode where user_id=? and id=?", (USER_ID, node_id))
        return max(0, cursor.rowcount)
    return 0


def is_week_context_row(row: SourceRow) -> bool:
    return (
        row.source_kind == "calendar_table_cell"
        and str(row.fields.get("source_pattern") or "") == "daily_rows"
        and str(row.fields.get("source_column") or "").strip() == "周内容"
    )


def run(args: argparse.Namespace) -> None:
    if args.apply and args.year is None and not args.ids:
        raise SystemExit("For safety, --apply requires --year or --ids.")

    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    database = db_path(data_dir)
    con = sqlite3.connect(database, timeout=60)
    con.row_factory = sqlite3.Row
    category_items = load_category_palette_items(con, include_import_categories=True)

    selected_ids = {part.strip() for part in (args.ids or "").split(",") if part.strip()}
    if args.hide_week_context_only:
        rows = load_source_rows(
            con,
            year=args.year,
            include_hidden_sources=args.include_hidden_sources,
            include_week_context=True,
        )
        week_rows = [row for row in rows if is_week_context_row(row)]
        if selected_ids:
            week_rows = [
                row
                for row in week_rows
                if row.id in selected_ids or (row.numeric_id is not None and str(row.numeric_id) in selected_ids)
            ]
        summary = {
            "db": str(database),
            "dry_run": not args.apply,
            "year": args.year,
            "source_action": args.source_action,
            "week_context_rows": len(week_rows),
            "preview": [
                {
                    "date": row.date.isoformat(),
                    "source_id": source_row_public_id(row),
                    "title": row.title,
                    "text": row.semantic_text,
                }
                for row in week_rows[: args.preview]
            ],
        }
        if not args.apply:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            con.close()
            return
        backup = Path(args.backup) if args.backup else Path(tempfile.gettempdir()) / (
            f"codeyun_week_context_cleanup_before_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
        )
        shutil.copy2(database, backup)
        touched = sum(hide_or_delete_row(con, row.id, args.source_action) for row in week_rows)
        con.commit()
        con.close()
        summary.update({"backup": str(backup), "source_touched": touched})
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    rows = load_source_rows(
        con,
        year=args.year,
        include_hidden_sources=args.include_hidden_sources,
        include_week_context=args.include_week_context,
    )
    groups = group_source_rows(rows, selected_ids=selected_ids)
    if args.limit:
        groups = groups[: args.limit]

    semantic_by_group: dict[str, list[SemanticItem]] = {}
    resource_groups = {group.group_id for group in groups if has_sensitive_resource_token(group.text)}
    for group in groups:
        if group.group_id in resource_groups:
            semantic_by_group[group.group_id] = [resource_single_item(group)]
    if not args.skip_codex and groups:
        codex_groups = [group for group in groups if group.group_id not in resource_groups]
        for start in range(0, len(codex_groups), args.batch_size):
            batch = codex_groups[start : start + args.batch_size]
            print(
                f"Calling Codex for groups {start + 1}-{start + len(batch)} / {len(codex_groups)}...",
                file=sys.stderr,
                flush=True,
            )
            batch_result = call_codex(batch, category_items=category_items, model=args.model, timeout=args.codex_timeout)
            semantic_by_group.update(batch_result)

    fallback_groups = 0
    fallback_group_ids: set[str] = set()
    for group in groups:
        if group.group_id not in semantic_by_group:
            fallback_groups += 1
            fallback_group_ids.add(group.group_id)
            semantic_by_group[group.group_id] = [
                SemanticItem(
                    title=heuristic_title(group.text),
                    content=group.text,
                    primary_category=infer_category_key(group.text, fallback=group.metadata_row.primary_category or DEFAULT_CATEGORY_KEY),
                    weight=group.weight,
                )
            ]

    preview_groups: list[dict[str, Any]] = []
    materialized_groups: list[SourceGroup] = []
    for group in groups:
        items = semantic_by_group[group.group_id]
        original_titles = [row.title for row in group.rows]
        codex_generated = (
            not args.skip_codex
            and group.group_id in semantic_by_group
            and group.group_id not in fallback_group_ids
            and group.group_id not in resource_groups
        )
        should_materialize = (
            args.materialize_all
            or group.group_id in resource_groups
            or (codex_generated and args.materialize_codex_singletons)
            or len(items) != 1
            or len(group.rows) > 1
        )
        if should_materialize:
            materialized_groups.append(group)
        preview_groups.append(
            {
                "group_id": group.group_id,
                "date": group.date.isoformat(),
                "source_count": len(group.rows),
                "source_ids": [source_row_public_id(row) for row in group.rows],
                "source_kinds": [row.source_kind for row in group.rows],
                "source_titles": original_titles[:4],
                "original_text": group.text,
                "split_count": len(items),
                "items": [
                    {
                        "title": item.title,
                        "content": item.content,
                        "primary_category": item.primary_category,
                        "weight": item.weight,
                    }
                    for item in items
                ],
                "will_materialize": should_materialize,
                "will_replace_sources": bool(should_materialize and args.apply and args.source_action != "none"),
            }
        )

    summary: dict[str, Any] = {
        "db": str(database),
        "dry_run": not args.apply,
        "year": args.year,
        "source_rows": sum(len(group.rows) for group in groups),
        "groups": len(groups),
        "groups_to_materialize": len(materialized_groups),
        "resource_groups": len(resource_groups),
        "fallback_groups": fallback_groups,
        "source_action": args.source_action,
        "model": args.model if not args.skip_codex else None,
        "preview": preview_groups[: args.preview],
    }

    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        con.close()
        return

    backup = Path(args.backup) if args.backup else Path(tempfile.gettempdir()) / (
        f"codeyun_semantic_calendar_split_before_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db"
    )
    shutil.copy2(database, backup)
    palette_changed = ensure_import_category_palette(con)

    existing = existing_semantic_nodes(con)
    inserted = 0
    updated = 0
    edges = 0
    for group in materialized_groups:
        items = semantic_by_group[group.group_id]
        for index, item in enumerate(items):
            key = item_source_key(group, index)
            if key in existing:
                existing_id = str(existing[key]["id"])
                update_item(con, existing_id, group, item, index, len(items))
                target_id = existing_id
                updated += 1
            else:
                target_id = insert_item(con, group, item, index, len(items))
                inserted += 1
            for row in group.rows:
                if insert_edge(con, row.id, target_id):
                    edges += 1
    source_touched = hide_or_delete_sources(con, materialized_groups, args.source_action)
    con.commit()
    con.close()

    summary.update(
        {
            "backup": str(backup),
            "inserted": inserted,
            "updated": updated,
            "edges": edges,
            "source_touched": source_touched,
            "palette_changed": palette_changed,
        }
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Use Codex CLI to semantically split legacy calendar aggregate notes into daily item notes."
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--ids", default="", help="Comma-separated note UUIDs or numeric IDs; processes their duplicate groups.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--preview", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--codex-timeout", type=int, default=600)
    parser.add_argument("--skip-codex", action="store_true")
    parser.add_argument("--include-hidden-sources", action="store_true")
    parser.add_argument("--include-week-context", action="store_true")
    parser.add_argument("--materialize-all", action="store_true")
    parser.add_argument(
        "--materialize-codex-singletons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When Codex returns one item, still replace the source row so AI title/category/weight are used.",
    )
    parser.add_argument("--source-action", choices=["hide", "delete", "none"], default="hide")
    parser.add_argument("--hide-week-context-only", action="store_true")
    parser.add_argument("--backup", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
