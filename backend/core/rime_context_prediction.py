from __future__ import annotations

from collections import Counter, defaultdict
import csv
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
import uuid

import jieba
from pypinyin import Style, lazy_pinyin


SNAPSHOT_FILE = "context_prediction_snapshot.tsv"
RUNTIME_FILE = "context_prediction_runtime.tsv"
HOT_FILE = "context_prediction_hot.tsv"
COUNTS_FILE = "context_prediction_model_counts.tsv"
SEED_FILE = "context_prediction.tsv"
PENDING_FILE = "context_prediction_pending.tsv"
HISTORY_FILE = "context_prediction_history.log"
HISTORY_ARTICLE_FILE = "context_prediction_history_article.txt"
HISTORY_ARTICLE_META_FILE = "context_prediction_history_article.json"
HTML_REPORT_FILE = "docs/context_prediction_tree.html"
ARTICLE_MANIFEST_FILE = "context_prediction_articles.json"
ARTICLE_CONTENT_DIR = "context_prediction_articles"
ARTICLE_CONTRIBUTIONS_FILE = "context_prediction_article_counts.tsv"
DELETED_CANDIDATES_FILE = "context_prediction_deleted_candidates.tsv"
ARCHIVE_DIR = "context_prediction_archives"
REFRESH_META_FILE = "context_prediction_refresh_meta.json"
ENGLISH_DICT_FILE = "codeyun_english.dict.yaml"
ENGLISH_BASE_DICT_FILE = "codeyun_english_base.dict.yaml"
ENGLISH_LEARNED_DICT_FILE = "codeyun_english_learned.dict.yaml"
ENGLISH_SCHEMA_FILE = "codeyun_english.schema.yaml"

ARTICLE_EXTRACTOR_VERSION = 2
MAX_CONTEXT_TOKENS = 4
MAX_ARTICLE_CHARS = 1_000_000
DEFAULT_TOPK_PER_KEY = 20
DEFAULT_RUNTIME_ROW_LIMIT = 5000
DEFAULT_HOT_MIN_WEIGHT = 1.0
DEFAULT_ENGLISH_LEARNED_ROW_LIMIT = 5000
DEFAULT_HISTORY_ARTICLE_LIMIT = 20000
DEFAULT_HISTORY_ARTICLE_PAGE_SIZE = 2000
HISTORY_PARAGRAPH_GAP_SECONDS = 5 * 60
HISTORY_PHRASE_GAP_SECONDS = 3
HISTORY_PHRASE_MAX_EVENTS = 4
HISTORY_PHRASE_MAX_CHARS = 8
HISTORY_PHRASE_WEIGHT = 0.65
DEFAULT_LINT_ISSUE_LIMIT = 200
MAX_LINT_AI_CHARS = 8000

_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_SENTENCE_SPLIT_RE = re.compile(r"[\r\n。！？!?；;]+")
_HISTORY_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_REPEATED_PUNCT_RE = re.compile(r"([，。！？!?、；;：:])\1+")
_LOWER_LATIN_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_/-])([a-z]{6,})(?![A-Za-z0-9_/-])")
_ENGLISH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9+#._-]{1,31})(?![A-Za-z0-9_])")
_REPEATED_CJK_CHUNK_RE = re.compile(r"([\u3400-\u9fff]{2,6})\1+")

_COMMON_TEXT_CORRECTIONS: tuple[tuple[str, str, str], ...] = (
    ("才复合", "才符合", "这里表达“满足条件/一致”时通常应写“符合”。"),
    ("不复合", "不符合", "这里表达“不满足条件”时通常应写“不符合”。"),
    ("复合降序", "符合降序", "这里表达排序结果符合预期，通常应写“符合”。"),
    ("复合要求", "符合要求", "这里表达满足要求，通常应写“符合要求”。"),
    ("因该", "应该", "常见错别字，“应该”表示应当。"),
    ("以经", "已经", "常见错别字，“已经”表示事情完成或发生。"),
    ("必竟", "毕竟", "常见错别字，“毕竟”表示终究、到底。"),
    ("帐号", "账号", "当前技术产品文案里通常写“账号”。"),
)

_LATIN_TOKEN_ALLOWLIST = {
    "android",
    "backend",
    "chrome",
    "codecli",
    "codex",
    "codeyun",
    "context",
    "deepseek",
    "element",
    "fastapi",
    "fallback",
    "frontend",
    "github",
    "javascript",
    "localhost",
    "openai",
    "ollama",
    "playwright",
    "prediction",
    "python",
    "rime",
    "typescript",
    "windows",
    "xlsx",
}

_PINYIN_SYLLABLES = {
    "a", "ai", "an", "ang", "ao",
    "ba", "bai", "ban", "bang", "bao", "bei", "ben", "beng", "bi", "bian", "biao", "bie", "bin", "bing", "bo", "bu",
    "ca", "cai", "can", "cang", "cao", "ce", "cen", "ceng", "cha", "chai", "chan", "chang", "chao", "che", "chen", "cheng", "chi", "chong", "chou", "chu", "chua", "chuai", "chuan", "chuang", "chui", "chun", "chuo", "ci", "cong", "cou", "cu", "cuan", "cui", "cun", "cuo",
    "da", "dai", "dan", "dang", "dao", "de", "dei", "den", "deng", "di", "dia", "dian", "diao", "die", "ding", "diu", "dong", "dou", "du", "duan", "dui", "dun", "duo",
    "e", "ei", "en", "eng", "er",
    "fa", "fan", "fang", "fei", "fen", "feng", "fo", "fou", "fu",
    "ga", "gai", "gan", "gang", "gao", "ge", "gei", "gen", "geng", "gong", "gou", "gu", "gua", "guai", "guan", "guang", "gui", "gun", "guo",
    "ha", "hai", "han", "hang", "hao", "he", "hei", "hen", "heng", "hong", "hou", "hu", "hua", "huai", "huan", "huang", "hui", "hun", "huo",
    "ji", "jia", "jian", "jiang", "jiao", "jie", "jin", "jing", "jiong", "jiu", "ju", "juan", "jue", "jun",
    "ka", "kai", "kan", "kang", "kao", "ke", "ken", "keng", "kong", "kou", "ku", "kua", "kuai", "kuan", "kuang", "kui", "kun", "kuo",
    "la", "lai", "lan", "lang", "lao", "le", "lei", "leng", "li", "lia", "lian", "liang", "liao", "lie", "lin", "ling", "liu", "lo", "long", "lou", "lu", "luan", "lue", "lun", "luo", "lv", "lve",
    "ma", "mai", "man", "mang", "mao", "me", "mei", "men", "meng", "mi", "mian", "miao", "mie", "min", "ming", "miu", "mo", "mou", "mu",
    "na", "nai", "nan", "nang", "nao", "ne", "nei", "nen", "neng", "ni", "nian", "niang", "niao", "nie", "nin", "ning", "niu", "nong", "nou", "nu", "nuan", "nue", "nun", "nuo", "nv", "nve",
    "o", "ou",
    "pa", "pai", "pan", "pang", "pao", "pei", "pen", "peng", "pi", "pian", "piao", "pie", "pin", "ping", "po", "pou", "pu",
    "qi", "qia", "qian", "qiang", "qiao", "qie", "qin", "qing", "qiong", "qiu", "qu", "quan", "que", "qun",
    "ran", "rang", "rao", "re", "ren", "reng", "ri", "rong", "rou", "ru", "ruan", "rui", "run", "ruo",
    "sa", "sai", "san", "sang", "sao", "se", "sen", "seng", "sha", "shai", "shan", "shang", "shao", "she", "shei", "shen", "sheng", "shi", "shou", "shu", "shua", "shuai", "shuan", "shuang", "shui", "shun", "shuo", "si", "song", "sou", "su", "suan", "sui", "sun", "suo",
    "ta", "tai", "tan", "tang", "tao", "te", "teng", "ti", "tian", "tiao", "tie", "ting", "tong", "tou", "tu", "tuan", "tui", "tun", "tuo",
    "wa", "wai", "wan", "wang", "wei", "wen", "weng", "wo", "wu",
    "xi", "xia", "xian", "xiang", "xiao", "xie", "xin", "xing", "xiong", "xiu", "xu", "xuan", "xue", "xun",
    "ya", "yan", "yang", "yao", "ye", "yi", "yin", "ying", "yo", "yong", "you", "yu", "yuan", "yue", "yun",
    "za", "zai", "zan", "zang", "zao", "ze", "zei", "zen", "zeng", "zha", "zhai", "zhan", "zhang", "zhao", "zhe", "zhei", "zhen", "zheng", "zhi", "zhong", "zhou", "zhu", "zhua", "zhuai", "zhuan", "zhuang", "zhui", "zhun", "zhuo", "zi", "zong", "zou", "zu", "zuan", "zui", "zun", "zuo",
}

_BASE_ENGLISH_TERMS: tuple[tuple[str, str, int], ...] = (
    ("ChatGPT", "chatgpt", 900),
    ("OpenAI", "openai", 880),
    ("Codex", "codex", 860),
    ("CodeYun", "codeyun", 850),
    ("Rime", "rime", 840),
    ("Weasel", "weasel", 830),
    ("Windows", "windows", 820),
    ("Chrome", "chrome", 810),
    ("GitHub", "github", 800),
    ("Python", "python", 790),
    ("JavaScript", "javascript", 780),
    ("TypeScript", "typescript", 770),
    ("Node.js", "nodejs", 760),
    ("Vue", "vue", 750),
    ("React", "react", 740),
    ("HTML", "html", 730),
    ("CSS", "css", 720),
    ("JSON", "json", 710),
    ("YAML", "yaml", 700),
    ("Markdown", "markdown", 690),
    ("PowerShell", "powershell", 680),
    ("CLI", "cli", 670),
    ("API", "api", 660),
    ("HTTP", "http", 650),
    ("HTTPS", "https", 640),
    ("URL", "url", 630),
    ("SQL", "sql", 620),
    ("SQLite", "sqlite", 610),
    ("Docker", "docker", 600),
    ("Git", "git", 590),
    ("npm", "npm", 580),
    ("pnpm", "pnpm", 570),
    ("uv", "uv", 560),
    ("pytest", "pytest", 550),
    ("Playwright", "playwright", 540),
    ("FastAPI", "fastapi", 530),
    ("fallback", "fallback", 525),
    ("backend", "backend", 520),
    ("frontend", "frontend", 510),
    ("model", "model", 500),
    ("data", "data", 490),
    ("index", "index", 480),
    ("cache", "cache", 470),
    ("config", "config", 460),
    ("runtime", "runtime", 450),
    ("context", "context", 440),
    ("prompt", "prompt", 430),
    ("token", "token", 420),
    ("server", "server", 410),
    ("client", "client", 400),
    ("debug", "debug", 390),
    ("test", "test", 380),
    ("build", "build", 370),
    ("deploy", "deploy", 360),
    ("sync", "sync", 350),
    ("xlsx", "xlsx", 345),
    ("update", "update", 340),
    ("import", "import", 330),
    ("export", "export", 320),
    ("function", "function", 310),
    ("class", "class", 300),
    ("object", "object", 290),
    ("string", "string", 280),
    ("array", "array", 270),
    ("error", "error", 260),
    ("warning", "warning", 250),
    ("message", "message", 240),
    ("button", "button", 230),
    ("page", "page", 220),
    ("input", "input", 210),
    ("output", "output", 200),
    ("history", "history", 190),
    ("article", "article", 180),
    ("candidate", "candidate", 170),
    ("prediction", "prediction", 160),
    ("the", "the", 120),
    ("and", "and", 110),
    ("for", "for", 100),
    ("with", "with", 90),
    ("from", "from", 80),
)
_BASE_ENGLISH_CODES = {code for _candidate, code, _weight in _BASE_ENGLISH_TERMS}
_KNOWN_ENGLISH_CODES = _LATIN_TOKEN_ALLOWLIST | _BASE_ENGLISH_CODES
_COMMON_DOMAIN_SUFFIXES = {"com", "cn", "net", "org", "io", "dev", "app", "ai", "me", "cc"}


jieba.setLogLevel(50)


class RimeContextPredictionError(ValueError):
    pass


def _resolve_rime_dir() -> Path | None:
    configured = os.environ.get("CODEYUN_RIME_USER_DIR")
    if configured:
        return Path(configured).expanduser()

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Rime"

    if os.name == "nt":
        return Path.home() / "AppData" / "Roaming" / "Rime"

    return None


def _file_info(rime_dir: Path | None, relative_path: str) -> dict[str, Any]:
    path = (rime_dir / relative_path) if rime_dir else None
    exists = bool(path and path.exists())
    stat = path.stat() if exists and path else None
    return {
        "key": relative_path,
        "path": str(path) if path else None,
        "exists": exists,
        "size": stat.st_size if stat else 0,
        "modified_at": stat.st_mtime if stat else None,
    }


def _source_file_signature(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "size": 0, "mtime_ns": 0}
    stat = path.stat()
    return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _prediction_source_fingerprint(rime_dir: Path) -> str:
    source_files = [
        SEED_FILE,
        PENDING_FILE,
        HISTORY_FILE,
        HISTORY_ARTICLE_FILE,
        HISTORY_ARTICLE_META_FILE,
        ARTICLE_MANIFEST_FILE,
        ARTICLE_CONTRIBUTIONS_FILE,
        DELETED_CANDIDATES_FILE,
    ]
    payload: dict[str, Any] = {
        "version": ARTICLE_EXTRACTOR_VERSION,
        "topk": DEFAULT_TOPK_PER_KEY,
        "runtime_limit": DEFAULT_RUNTIME_ROW_LIMIT,
        "hot_min_weight": DEFAULT_HOT_MIN_WEIGHT,
        "hot_file": HOT_FILE,
        "files": {
            relative_path: _source_file_signature(rime_dir / relative_path)
            for relative_path in source_files
        },
        "articles": {},
    }

    article_dir = rime_dir / ARTICLE_CONTENT_DIR
    if article_dir.exists():
        payload["articles"] = {
            f"{ARTICLE_CONTENT_DIR}/{path.name}": _source_file_signature(path)
            for path in sorted(article_dir.glob("*.txt"), key=lambda item: item.name)
        }

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_refresh_meta(rime_dir: Path) -> dict[str, Any]:
    path = rime_dir / REFRESH_META_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_refresh_meta(rime_dir: Path, fingerprint: str | None = None) -> None:
    path = rime_dir / REFRESH_META_FILE
    payload = {
        "version": 1,
        "source_fingerprint": fingerprint or _prediction_source_fingerprint(rime_dir),
        "updated_at": time.time(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _prediction_outputs_exist(rime_dir: Path) -> bool:
    return (
        (rime_dir / SNAPSHOT_FILE).exists()
        and (rime_dir / RUNTIME_FILE).exists()
        and (rime_dir / HOT_FILE).exists()
    )


def _tracked_files(rime_dir: Path | None) -> list[dict[str, Any]]:
    return [
        _file_info(rime_dir, item)
        for item in [
            SNAPSHOT_FILE,
            RUNTIME_FILE,
            HOT_FILE,
            COUNTS_FILE,
            SEED_FILE,
            PENDING_FILE,
            HISTORY_FILE,
            HISTORY_ARTICLE_FILE,
            HISTORY_ARTICLE_META_FILE,
            ARTICLE_MANIFEST_FILE,
            ARTICLE_CONTRIBUTIONS_FILE,
            DELETED_CANDIDATES_FILE,
            REFRESH_META_FILE,
            ENGLISH_DICT_FILE,
            ENGLISH_BASE_DICT_FILE,
            ENGLISH_LEARNED_DICT_FILE,
            ENGLISH_SCHEMA_FILE,
            HTML_REPORT_FILE,
        ]
    ]


def _clean_tsv_field(value: Any) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _format_weight(value: float) -> str:
    return f"{float(value):g}"


def _read_prediction_rows(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields:
                continue
            first = (fields[0] or "").strip()
            if not first or first.startswith("#"):
                continue
            if len(fields) < 4:
                continue

            context = first
            prefix = (fields[1] or "").strip()
            candidate = (fields[2] or "").strip()
            if not prefix or not candidate:
                continue
            try:
                weight = float((fields[3] or "0").strip())
            except ValueError:
                weight = 0.0
            comment = (fields[4] or "").strip() if len(fields) >= 5 else ""
            rows.append(
                {
                    "context": context,
                    "prefix": prefix,
                    "candidate": candidate,
                    "weight": weight,
                    "comment": comment,
                }
            )
            if limit and len(rows) >= limit:
                break
    return rows


def _write_prediction_rows_file(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n")
        for row in rows:
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(row.get("context") or ""),
                        _clean_tsv_field(row.get("prefix") or ""),
                        _clean_tsv_field(row.get("candidate") or ""),
                        _format_weight(float(row.get("weight") or 0)),
                        _clean_tsv_field(row.get("comment") or ""),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _read_count_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 4:
                continue
            try:
                weight = float((fields[3] or "0").strip())
            except ValueError:
                continue
            rows.append(
                {
                    "context": (fields[0] or "").strip(),
                    "prefix": (fields[1] or "").strip(),
                    "candidate": (fields[2] or "").strip(),
                    "weight": weight,
                    "comment": (fields[5] or "输入历史").strip() if len(fields) >= 6 else "输入历史",
                }
            )
    return rows


def _read_count_entries(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 4:
                continue
            context, prefix, candidate = _candidate_key(fields[0], fields[1], fields[2])
            if not context or not prefix or not candidate:
                continue
            try:
                count = float((fields[3] or "0").strip())
            except ValueError:
                continue
            entries[(context, prefix, candidate)] = {
                "count": count,
                "last_seen": (fields[4] or "").strip() if len(fields) >= 5 else "",
                "comment": (fields[5] or "输入历史").strip() if len(fields) >= 6 else "输入历史",
            }
    return entries


def _write_count_entries(rime_dir: Path, entries: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    path = rime_dir / COUNTS_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tcount\tlast_seen\tcomment\n")
        for (context, prefix, candidate), entry in sorted(entries.items()):
            fh.write(
                "\t".join(
                    [
                        context,
                        prefix,
                        candidate,
                        _format_weight(float(entry.get("count") or 0)),
                        _clean_tsv_field(entry.get("last_seen") or ""),
                        _clean_tsv_field(entry.get("comment") or "输入历史"),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _default_article_manifest() -> dict[str, Any]:
    return {"version": 1, "articles": []}


def _read_article_manifest(rime_dir: Path) -> dict[str, Any]:
    path = rime_dir / ARTICLE_MANIFEST_FILE
    if not path.exists():
        return _default_article_manifest()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_article_manifest()
    if not isinstance(payload, dict):
        return _default_article_manifest()
    articles = payload.get("articles")
    if not isinstance(articles, list):
        payload["articles"] = []
    payload.setdefault("version", 1)
    return payload


def _write_article_manifest(rime_dir: Path, manifest: dict[str, Any]) -> None:
    path = rime_dir / ARTICLE_MANIFEST_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _article_content_path(rime_dir: Path, article: dict[str, Any]) -> Path:
    relative = str(article.get("content_path") or "")
    if relative:
        return rime_dir / relative
    return rime_dir / ARTICLE_CONTENT_DIR / f"{article['id']}.txt"


def _read_article_contributions(rime_dir: Path) -> list[dict[str, Any]]:
    path = rime_dir / ARTICLE_CONTRIBUTIONS_FILE
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 5:
                continue
            try:
                weight = float((fields[4] or "0").strip())
            except ValueError:
                continue
            rows.append(
                {
                    "source_id": (fields[0] or "").strip(),
                    "context": (fields[1] or "").strip(),
                    "prefix": (fields[2] or "").strip(),
                    "candidate": (fields[3] or "").strip(),
                    "weight": weight,
                }
            )
    return rows


def _write_article_contributions(rime_dir: Path, rows: list[dict[str, Any]]) -> None:
    path = rime_dir / ARTICLE_CONTRIBUTIONS_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# source_id\tcontext_key\tpinyin_prefix\tcandidate\tcount\n")
        for row in sorted(rows, key=lambda item: (item["source_id"], item["context"], item["prefix"], item["candidate"])):
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(row["source_id"]),
                        _clean_tsv_field(row["context"]),
                        _clean_tsv_field(row["prefix"]),
                        _clean_tsv_field(row["candidate"]),
                        _format_weight(float(row["weight"])),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _candidate_key(context: Any, prefix: Any, candidate: Any) -> tuple[str, str, str]:
    return (
        _clean_tsv_field(context),
        _clean_tsv_field(prefix),
        _clean_tsv_field(candidate),
    )


def _normalize_candidate_key(context: Any, prefix: Any, candidate: Any) -> tuple[str, str, str]:
    key = _candidate_key(context, prefix, candidate)
    if not all(key):
        raise RimeContextPredictionError("前文片段、当前拼音和候选词都不能为空。")
    return key


def _read_deleted_candidate_rows(rime_dir: Path) -> list[dict[str, Any]]:
    path = rime_dir / DELETED_CANDIDATES_FILE
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 3:
                continue
            context, prefix, candidate = _candidate_key(fields[0], fields[1], fields[2])
            if not context or not prefix or not candidate:
                continue
            deleted_at = 0.0
            if len(fields) >= 4:
                try:
                    deleted_at = float((fields[3] or "0").strip())
                except ValueError:
                    deleted_at = 0.0
            rows.append(
                {
                    "context": context,
                    "prefix": prefix,
                    "candidate": candidate,
                    "deleted_at": deleted_at,
                }
            )
    return rows


def _read_deleted_candidate_keys(rime_dir: Path) -> set[tuple[str, str, str]]:
    return {
        (row["context"], row["prefix"], row["candidate"])
        for row in _read_deleted_candidate_rows(rime_dir)
    }


def _is_manual_rule_comment(comment: Any) -> bool:
    return _clean_tsv_field(comment) == "手动规则"


def _write_deleted_candidate_rows(rime_dir: Path, rows: list[dict[str, Any]]) -> None:
    path = rime_dir / DELETED_CANDIDATES_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tdeleted_at\n")
        for row in sorted(rows, key=lambda item: (item["context"], item["prefix"], item["candidate"])):
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(row["context"]),
                        _clean_tsv_field(row["prefix"]),
                        _clean_tsv_field(row["candidate"]),
                        _format_weight(float(row.get("deleted_at") or 0)),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _rotate_pending_events(rime_dir: Path) -> Path | None:
    pending_path = rime_dir / PENDING_FILE
    if not pending_path.exists() or pending_path.stat().st_size == 0:
        return None
    archive_dir = rime_dir / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    processing_path = archive_dir / f"context_prediction_pending.{int(time.time())}.processing.tsv"
    os.replace(pending_path, processing_path)
    return processing_path


def _fold_pending_events(rime_dir: Path) -> dict[str, Any]:
    processing_path = _rotate_pending_events(rime_dir)
    if not processing_path:
        return {"pending_rows": 0}

    entries = _read_count_entries(rime_dir / COUNTS_FILE)
    seen_at = time.strftime("%Y-%m-%d %H:%M:%S")
    folded_rows = 0
    try:
        with processing_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t")
            for fields in reader:
                if not fields or (fields[0] or "").lstrip().startswith("#") or len(fields) < 4:
                    continue
                context, prefix, candidate = _candidate_key(fields[0], fields[1], fields[2])
                if not context or not prefix or not candidate:
                    continue
                try:
                    weight = float((fields[3] or "1").strip())
                except ValueError:
                    weight = 1.0
                comment = (fields[4] or "自学习").strip() if len(fields) >= 5 else "自学习"
                entry = entries.setdefault(
                    (context, prefix, candidate),
                    {"count": 0.0, "last_seen": "", "comment": comment or "自学习"},
                )
                entry["count"] = float(entry.get("count") or 0) + weight
                entry["last_seen"] = seen_at
                if comment:
                    entry["comment"] = comment
                folded_rows += 1
    finally:
        processing_path.unlink(missing_ok=True)

    _write_count_entries(rime_dir, entries)
    return {"pending_rows": folded_rows, "count_entries": len(entries)}


def _discard_pending_events(rime_dir: Path) -> int:
    processing_path = _rotate_pending_events(rime_dir)
    if not processing_path:
        return 0
    pending_rows = _count_data_rows(processing_path)
    processing_path.unlink(missing_ok=True)
    return pending_rows


def _parse_history_timestamp(value: str) -> float | None:
    text = (value or "").strip()
    if not _HISTORY_TIMESTAMP_RE.match(text):
        return None
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _parse_history_event_fields(fields: list[str]) -> dict[str, Any] | None:
    if not fields:
        return None
    first = (fields[0] or "").strip()
    if not first or first.startswith("#"):
        return None
    timestamp = first if _HISTORY_TIMESTAMP_RE.match(first) else ""
    text = ""
    if len(fields) >= 2:
        text = fields[-1] or ""
    elif not timestamp:
        text = fields[0] or ""
    if not text:
        return None
    return {
        "timestamp": timestamp,
        "time": _parse_history_timestamp(timestamp) if timestamp else None,
        "text": text,
    }


def _iter_history_events(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            event = _parse_history_event_fields(fields)
            if event:
                yield event


def _read_history_events(path: Path) -> list[dict[str, Any]]:
    return list(_iter_history_events(path))


def _read_history_event_page(path: Path, *, page: int, page_size: int) -> dict[str, Any]:
    normalized_page_size = max(1, min(int(page_size or DEFAULT_HISTORY_ARTICLE_PAGE_SIZE), 5000))
    total = sum(1 for _ in _iter_history_events(path))
    total_pages = max(1, (total + normalized_page_size - 1) // normalized_page_size)
    normalized_page = max(1, min(int(page or 1), total_pages))

    end_index = max(0, total - (normalized_page - 1) * normalized_page_size)
    start_index = max(1, end_index - normalized_page_size + 1) if end_index else 0

    events: list[dict[str, Any]] = []
    if total:
        for index, event in enumerate(_iter_history_events(path), start=1):
            if index < start_index:
                continue
            if index > end_index:
                break
            events.append(event)

    return {
        "events": events,
        "pagination": {
            "page": normalized_page,
            "page_size": normalized_page_size,
            "total": total,
            "total_pages": total_pages if total else 0,
            "start_index": start_index if events else 0,
            "end_index": end_index if events else 0,
            "has_prev": normalized_page > 1 and total > 0,
            "has_next": normalized_page < total_pages and total > 0,
        },
    }


def _count_data_rows(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields:
                continue
            first = (fields[0] or "").strip()
            if first and not first.startswith("#"):
                count += 1
    return count


def _history_events_to_article(events: list[dict[str, Any]]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    last_time: float | None = None
    last_date = ""

    for event in events:
        text = str(event.get("text") or "")
        if not text:
            continue
        timestamp = str(event.get("timestamp") or "")
        current_time = event.get("time") if isinstance(event.get("time"), (int, float)) else None
        current_date = timestamp[:10] if timestamp else ""
        should_break = bool(
            current
            and (
                (last_time is not None and current_time is not None and current_time - last_time >= HISTORY_PARAGRAPH_GAP_SECONDS)
                or (last_date and current_date and current_date != last_date)
            )
        )
        if should_break:
            paragraphs.append("".join(current))
            current = []
        current.append(text)
        if current_time is not None:
            last_time = float(current_time)
        if current_date:
            last_date = current_date

    if current:
        paragraphs.append("".join(current))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _history_event_phrase_token(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    if not text or text.startswith("<"):
        return ""
    parts = _CJK_RE.findall(text)
    if not parts:
        return ""
    token = "".join(parts)
    if token != text or len(token) > 16:
        return ""
    return token


def _extract_history_phrase_contributions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从连续输入事件中提取最终文本合成词条。

    :param list events: 输入历史事件，元素至少包含 text/time/timestamp。
    :return list: 可合并进预测索引的贡献行。

    >>> _extract_history_phrase_contributions([
    ...     {"text": "设", "time": 1.0},
    ...     {"text": "计", "time": 2.0},
    ... ])[0]["candidate"]
    '设计'
    """

    counts: dict[tuple[str, str, str], float] = defaultdict(float)
    segment: list[str] = []
    last_time: float | None = None

    def flush_segment() -> None:
        nonlocal segment
        for start in range(len(segment)):
            if start > 0 and len(segment[start]) == 1 and len(segment[start - 1]) == 1:
                continue
            phrase = ""
            for end in range(start, min(len(segment), start + HISTORY_PHRASE_MAX_EVENTS)):
                phrase += segment[end]
                if end == start:
                    continue
                if len(phrase) < 2:
                    continue
                if len(phrase) > HISTORY_PHRASE_MAX_CHARS:
                    break
                prefix = _token_to_pinyin(phrase)
                if prefix:
                    counts[("__global", prefix, phrase)] += HISTORY_PHRASE_WEIGHT
        segment = []

    for event in events:
        token = _history_event_phrase_token(event)
        current_time = event.get("time") if isinstance(event.get("time"), (int, float)) else None
        if (
            not token
            or (
                last_time is not None
                and current_time is not None
                and float(current_time) - last_time > HISTORY_PHRASE_GAP_SECONDS
            )
        ):
            flush_segment()
        if token:
            segment.append(token)
            if current_time is not None:
                last_time = float(current_time)
        else:
            last_time = None

    flush_segment()
    return [
        {
            "source_id": "input_history_phrase",
            "context": context,
            "prefix": prefix,
            "candidate": candidate,
            "weight": weight,
        }
        for (context, prefix, candidate), weight in counts.items()
    ]


def _history_article_path(rime_dir: Path) -> Path:
    return rime_dir / HISTORY_ARTICLE_FILE


def _history_article_meta_path(rime_dir: Path) -> Path:
    return rime_dir / HISTORY_ARTICLE_META_FILE


def _read_history_article_meta(rime_dir: Path) -> dict[str, Any]:
    path = _history_article_meta_path(rime_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_history_article_meta(rime_dir: Path, meta: dict[str, Any]) -> None:
    path = _history_article_meta_path(rime_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _history_article_content_from_events(
    events: list[dict[str, Any]],
    *,
    saved_event_count: int = 0,
    saved_content: str = "",
) -> tuple[str, int]:
    content = saved_content
    appended_event_count = 0
    if saved_event_count < 0:
        saved_event_count = 0
    if saved_event_count < len(events):
        appended_events = events[saved_event_count:]
        suffix = _history_events_to_article(appended_events)
        if suffix:
            content = f"{content.rstrip()}\n\n{suffix}" if content.strip() else suffix
            appended_event_count = len(appended_events)
    return content, appended_event_count


def _resolve_history_article_content(
    rime_dir: Path,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    history_events = events if events is not None else _read_history_events(rime_dir / HISTORY_FILE)
    article_path = _history_article_path(rime_dir)
    meta = _read_history_article_meta(rime_dir)
    edited = article_path.exists()

    if edited:
        saved_content = article_path.read_text(encoding="utf-8")
        saved_event_count = int(meta.get("history_event_count") or 0)
        content, appended_event_count = _history_article_content_from_events(
            history_events,
            saved_event_count=saved_event_count,
            saved_content=saved_content,
        )
    else:
        content = _history_events_to_article(history_events)
        appended_event_count = 0

    return {
        "content": content,
        "events": history_events,
        "edited": edited,
        "saved_at": float(meta.get("saved_at") or 0),
        "base_event_count": int(meta.get("history_event_count") or 0),
        "appended_event_count": appended_event_count,
    }


def save_rime_context_prediction_history_article(content: str) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    text = _normalize_article_text(content)
    events = _read_history_events(rime_dir / HISTORY_FILE)
    now = time.time()
    article_path = _history_article_path(rime_dir)
    tmp = article_path.with_suffix(article_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, article_path)
    history_path = rime_dir / HISTORY_FILE
    stat = history_path.stat() if history_path.exists() else None
    _write_history_article_meta(
        rime_dir,
        {
            "version": 1,
            "saved_at": now,
            "history_event_count": len(events),
            "history_size": stat.st_size if stat else 0,
            "history_modified_at": stat.st_mtime if stat else None,
            "content_hash": _content_hash(text),
        },
    )
    return collect_rime_context_prediction_history_article()


def _rebuild_count_entries_from_history(rime_dir: Path) -> dict[str, Any]:
    history_article = _resolve_history_article_content(rime_dir)
    events = history_article["events"]
    content = str(history_article["content"] or "")
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not events or not _CJK_RE.search(content):
        _write_count_entries(rime_dir, entries)
        return {
            "history_events": len(events),
            "history_chars": len(content),
            "history_contributions": 0,
            "count_entries": 0,
        }

    last_seen = str(events[-1].get("timestamp") or "") or time.strftime("%Y-%m-%d %H:%M:%S")

    def add_history_row(row: dict[str, Any], comment: str) -> None:
        context, prefix, candidate = _candidate_key(row["context"], row["prefix"], row["candidate"])
        if not context or not prefix or not candidate:
            return
        entry = entries.setdefault(
            (context, prefix, candidate),
            {"count": 0.0, "last_seen": last_seen, "comment": comment},
        )
        entry["count"] = float(entry.get("count") or 0) + float(row.get("weight") or 0)
        entry["last_seen"] = last_seen
        if comment and str(entry.get("comment") or "") != "输入历史":
            entry["comment"] = comment

    for row in _extract_article_contributions("input_history", content):
        add_history_row(row, "输入历史")

    synthetic_rows = _extract_history_phrase_contributions(events)
    for row in synthetic_rows:
        add_history_row(row, "输入历史合成")

    _write_count_entries(rime_dir, entries)
    return {
        "history_events": len(events),
        "history_chars": len(content),
        "history_contributions": sum(float(item.get("count") or 0) for item in entries.values()),
        "history_phrase_contributions": sum(float(row.get("weight") or 0) for row in synthetic_rows),
        "count_entries": len(entries),
        "history_article_edited": bool(history_article["edited"]),
    }


def _can_rebuild_from_history(rime_dir: Path) -> bool:
    path = rime_dir / HISTORY_FILE
    return path.exists() and path.stat().st_size > 0


def rebuild_rime_context_prediction_from_history(rime_dir: Path | None = None) -> dict[str, Any]:
    target_dir = rime_dir or _ensure_writable_rime_dir()
    history_result = _rebuild_count_entries_from_history(target_dir)
    pending_rows = _discard_pending_events(target_dir)
    snapshot_result = rebuild_rime_context_prediction_snapshot(target_dir)
    return {
        **history_result,
        **snapshot_result,
        "pending_rows": pending_rows,
        "source": HISTORY_FILE,
    }


def make_rime_context_prediction_history_unavailable(
    *,
    status: str,
    message: str,
    rime_dir: str | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "message": message,
        "rime_dir": rime_dir,
        "source": None,
        "source_path": None,
        "updated_at": None,
        "files": files or [],
        "summary": {
            "entry_count": 0,
            "char_count": 0,
            "paragraph_count": 0,
            "first_seen": "",
            "last_seen": "",
            "pending_row_count": 0,
            "model_count_row_count": 0,
            "truncated": False,
            "limit": 0,
            "edited": False,
            "saved_at": 0,
            "base_event_count": 0,
            "appended_event_count": 0,
        },
        "pagination": None,
        "content": "",
    }


def collect_rime_context_prediction_history_article(
    limit: int | None = DEFAULT_HISTORY_ARTICLE_LIMIT,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    rime_dir = _resolve_rime_dir()
    files = _tracked_files(rime_dir)

    if not rime_dir:
        return make_rime_context_prediction_history_unavailable(
            status="unsupported_platform",
            message="当前系统没有可识别的 Rime 用户目录位置。",
            files=files,
        )

    if not rime_dir.exists():
        return make_rime_context_prediction_history_unavailable(
            status="rime_missing",
            message="该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。",
            rime_dir=str(rime_dir),
            files=files,
        )

    history_path = rime_dir / HISTORY_FILE
    if not history_path.exists():
        return make_rime_context_prediction_history_unavailable(
            status="history_missing",
            message="没有发现输入历史日志。当前预测索引可能只有聚合计数，不能无损还原成文章。",
            rime_dir=str(rime_dir),
            files=files,
        )

    try:
        if page is not None or page_size is not None:
            page_payload = _read_history_event_page(
                history_path,
                page=int(page or 1),
                page_size=int(page_size or DEFAULT_HISTORY_ARTICLE_PAGE_SIZE),
            )
            events = page_payload["events"]
            pagination = page_payload["pagination"]
            meta = _read_history_article_meta(rime_dir)
            history_article = {
                "content": _history_events_to_article(events),
                "events": events,
                "edited": _history_article_path(rime_dir).exists(),
                "saved_at": float(meta.get("saved_at") or 0),
                "base_event_count": int(meta.get("history_event_count") or 0),
                "appended_event_count": 0,
            }
            all_event_count = int(pagination["total"])
            truncated = bool(all_event_count > len(events))
        else:
            all_events = _read_history_events(history_path)
            normalized_limit = int(limit or 0)
            truncated = bool(normalized_limit > 0 and len(all_events) > normalized_limit)
            events = all_events[-normalized_limit:] if truncated else all_events
            history_article = _resolve_history_article_content(rime_dir, events)
            all_event_count = len(events)
            pagination = None
    except OSError as exc:
        return make_rime_context_prediction_history_unavailable(
            status="read_error",
            message=f"读取输入历史日志失败：{exc}",
            rime_dir=str(rime_dir),
            files=files,
        )

    normalized_limit = int(pagination["page_size"]) if pagination else int(limit or 0)
    content = str(history_article["content"] or "")
    stat = history_path.stat()
    status = "ready" if events else "empty"
    message = (
        "已读取输入历史修订稿。"
        if history_article["edited"]
        else "已读取输入历史并还原为文章。"
    ) if events else "输入历史日志存在，但暂时没有可展示记录。"
    return {
        "available": bool(events),
        "status": status,
        "message": message,
        "rime_dir": str(rime_dir),
        "source": HISTORY_FILE,
        "source_path": str(history_path),
        "updated_at": stat.st_mtime,
        "files": files,
        "summary": {
            "entry_count": all_event_count,
            "char_count": len(content),
            "paragraph_count": len([item for item in content.split("\n\n") if item]),
            "first_seen": str(events[0].get("timestamp") or "") if events else "",
            "last_seen": str(events[-1].get("timestamp") or "") if events else "",
            "pending_row_count": _count_data_rows(rime_dir / PENDING_FILE),
            "model_count_row_count": _count_data_rows(rime_dir / COUNTS_FILE),
            "truncated": truncated,
            "limit": normalized_limit,
            "edited": bool(history_article["edited"]),
            "saved_at": float(history_article["saved_at"] or 0),
            "base_event_count": int(history_article["base_event_count"] or 0),
            "appended_event_count": int(history_article["appended_event_count"] or 0),
        },
        "pagination": pagination,
        "content": content,
    }


def make_rime_context_prediction_lint_unavailable(
    *,
    status: str,
    message: str,
    rime_dir: str | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "message": message,
        "rime_dir": rime_dir,
        "files": files or [],
        "summary": {
            "source_count": 0,
            "issue_count": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "rule_count": 0,
            "ai_count": 0,
        },
        "issues": [],
    }


def _looks_like_pinyin_token(value: str) -> bool:
    token = value.lower().replace("ü", "v")
    if not token:
        return False
    memo: dict[int, bool] = {len(token): True}

    def can_split(index: int) -> bool:
        if index in memo:
            return memo[index]
        for end in range(min(len(token), index + 6), index, -1):
            if token[index:end] in _PINYIN_SYLLABLES and can_split(end):
                memo[index] = True
                return True
        memo[index] = False
        return False

    return can_split(0)


def _lint_issue_excerpt(content: str, start: int, end: int, width: int = 72) -> str:
    left = max(0, start - width // 2)
    right = min(len(content), end + width // 2)
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(content) else ""
    return prefix + content[left:right].replace("\n", " ") + suffix


def _line_offsets(content: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    offset = 0
    for line_no, raw_line in enumerate(content.splitlines(keepends=True), start=1):
        line = raw_line.rstrip("\r\n")
        rows.append((line_no, offset, line))
        offset += len(raw_line)
    if content and not rows:
        rows.append((1, 0, content))
    return rows


def _lint_issue(
    source: dict[str, Any],
    *,
    rule: str,
    issue_type: str,
    severity: str,
    line: int,
    column: int,
    start: int,
    end: int,
    text: str,
    message: str,
    suggestion: str = "",
    confidence: float = 0.8,
) -> dict[str, Any]:
    content = str(source.get("content") or "")
    identity = "\t".join(
        [
            str(source.get("source_type") or ""),
            str(source.get("source_id") or ""),
            rule,
            str(start),
            text,
            suggestion,
        ]
    )
    return {
        "id": hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16],
        "source_type": str(source.get("source_type") or ""),
        "source_id": str(source.get("source_id") or ""),
        "source_title": str(source.get("source_title") or ""),
        "source_enabled": bool(source.get("source_enabled", True)),
        "rule": rule,
        "type": issue_type,
        "severity": severity,
        "line": line,
        "column": column,
        "span_start": start,
        "span_end": end,
        "text": text,
        "message": message,
        "suggestion": suggestion,
        "confidence": float(confidence),
        "excerpt": _lint_issue_excerpt(content, start, end),
    }


def _add_lint_issue(issues: list[dict[str, Any]], seen: set[str], issue: dict[str, Any]) -> None:
    issue_id = str(issue.get("id") or "")
    if not issue_id or issue_id in seen:
        return
    seen.add(issue_id)
    issues.append(issue)


def _scan_text_lint_rules(source: dict[str, Any]) -> list[dict[str, Any]]:
    content = str(source.get("content") or "")
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()

    for line_no, line_offset, line in _line_offsets(content):
        for wrong, suggestion, message in _COMMON_TEXT_CORRECTIONS:
            start_at = 0
            while True:
                index = line.find(wrong, start_at)
                if index < 0:
                    break
                start = line_offset + index
                _add_lint_issue(
                    issues,
                    seen,
                    _lint_issue(
                        source,
                        rule="common_correction",
                        issue_type="疑似错别字",
                        severity="medium",
                        line=line_no,
                        column=index + 1,
                        start=start,
                        end=start + len(wrong),
                        text=wrong,
                        message=message,
                        suggestion=suggestion,
                        confidence=0.92,
                    ),
                )
                start_at = index + len(wrong)

        for match in _REPEATED_PUNCT_RE.finditer(line):
            text = match.group(0)
            start = line_offset + match.start()
            _add_lint_issue(
                issues,
                seen,
                _lint_issue(
                    source,
                    rule="repeated_punctuation",
                    issue_type="标点异常",
                    severity="low",
                    line=line_no,
                    column=match.start() + 1,
                    start=start,
                    end=start + len(text),
                    text=text,
                    message="连续重复标点通常会污染语料统计，建议确认是否需要保留。",
                    suggestion=text[0],
                    confidence=0.86,
                ),
            )

        for match in _REPEATED_CJK_CHUNK_RE.finditer(line):
            text = match.group(0)
            chunk = match.group(1)
            if text == chunk:
                continue
            start = line_offset + match.start()
            _add_lint_issue(
                issues,
                seen,
                _lint_issue(
                    source,
                    rule="repeated_cjk_chunk",
                    issue_type="重复片段",
                    severity="low",
                    line=line_no,
                    column=match.start() + 1,
                    start=start,
                    end=start + len(text),
                    text=text,
                    message="发现连续重复的中文片段，可能是误输入或复制残留。",
                    suggestion=chunk,
                    confidence=0.72,
                ),
            )

        for match in _LOWER_LATIN_TOKEN_RE.finditer(line):
            token = match.group(1)
            normalized = token.lower()
            if normalized in _LATIN_TOKEN_ALLOWLIST:
                continue
            pinyin_like = _looks_like_pinyin_token(normalized)
            start = line_offset + match.start(1)
            _add_lint_issue(
                issues,
                seen,
                _lint_issue(
                    source,
                    rule="latin_or_pinyin_residue",
                    issue_type="异常片段",
                    severity="medium" if pinyin_like else "low",
                    line=line_no,
                    column=match.start(1) + 1,
                    start=start,
                    end=start + len(token),
                    text=token,
                    message=(
                        "这段小写字母很像未上屏的拼音残留。"
                        if pinyin_like
                        else "这段小写字母出现在中文语料中，建议确认是术语、代码还是误输入残留。"
                    ),
                    suggestion="",
                    confidence=0.78 if pinyin_like else 0.58,
                ),
            )

    return issues


def _collect_lint_sources(
    rime_dir: Path,
    *,
    source: str,
    history_limit: int,
) -> list[dict[str, Any]]:
    normalized_source = (source or "all").strip().lower()
    sources: list[dict[str, Any]] = []

    if normalized_source in {"all", "history"}:
        history_path = rime_dir / HISTORY_FILE
        if history_path.exists():
            events = _read_history_events(history_path)
            if history_limit > 0 and len(events) > history_limit:
                events = events[-history_limit:]
            history_article = _resolve_history_article_content(rime_dir, events)
            content = str(history_article.get("content") or "").strip()
            if content:
                sources.append(
                    {
                        "source_type": "history",
                        "source_id": "history",
                        "source_title": "输入历史",
                        "source_enabled": True,
                        "content": content,
                    }
                )

    if normalized_source in {"all", "articles"}:
        manifest = _read_article_manifest(rime_dir)
        for article in manifest.get("articles", []):
            if not isinstance(article, dict):
                continue
            content_path = _article_content_path(rime_dir, article)
            try:
                content = content_path.read_text(encoding="utf-8").strip()
            except OSError:
                content = ""
            if not content:
                continue
            sources.append(
                {
                    "source_type": "article",
                    "source_id": str(article.get("id") or ""),
                    "source_title": str(article.get("title") or "未命名文章"),
                    "source_enabled": bool(article.get("enabled", True)),
                    "content": content,
                }
            )

    return sources


def _severity_rank(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


def _normalize_lint_source(value: str) -> str:
    normalized = (value or "all").strip().lower()
    return normalized if normalized in {"all", "history", "articles"} else "all"


def _normalize_lint_mode(value: str) -> str:
    normalized = (value or "rules").strip().lower()
    return normalized if normalized in {"rules", "ai"} else "rules"


def _strip_json_code_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return text[first : last + 1]
    return text


def _build_ai_lint_prompt(sources: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    remaining = MAX_LINT_AI_CHARS
    for source in sources:
        if remaining <= 0:
            break
        content = str(source.get("content") or "")
        if not content:
            continue
        lines = []
        for line_no, _, line in _line_offsets(content):
            if not line.strip():
                continue
            lines.append(f"{line_no}: {line}")
            if sum(len(item) for item in lines) >= remaining:
                break
        chunk = "\n".join(lines)
        if len(chunk) > remaining:
            chunk = chunk[:remaining]
        remaining -= len(chunk)
        chunks.append(
            f"### source_id={source.get('source_id')} source_type={source.get('source_type')} title={source.get('source_title')}\n{chunk}"
        )
    joined = "\n\n".join(chunks)
    return (
        "请对下面中文语料做高置信校对，只找明显错别字、词语误用、助词误用、残留拼音或明显不通顺的片段。"
        "忽略口语表达、产品名、代码、路径、英文技术词。只输出 JSON，不要解释。\n"
        "JSON 格式：{\"issues\":[{\"source_id\":\"...\",\"line\":1,\"text\":\"原文片段\",\"suggestion\":\"建议写法\","
        "\"type\":\"疑似错别字|助词混用|异常片段|语句不通\",\"severity\":\"high|medium|low\","
        "\"reason\":\"简短原因\",\"confidence\":0.0}]}\n\n"
        f"{joined}"
    )


def _collect_ai_lint_issues(sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if not sources:
        return [], ""
    try:
        from backend.core.ai_chat import (  # Local import keeps the rule checker lightweight.
            AiProviderConfig,
            CODEX_CLI_DEFAULT_COMMAND,
            CODEX_CLI_DEFAULT_MODEL,
            OllamaClientError,
            chat_with_provider,
        )
    except ImportError as exc:
        return [], f"AI 校对模块不可用：{exc}"

    provider = AiProviderConfig(
        id="rime-codex-cli",
        label="Codex CLI",
        kind="codex_cli",
        base_url=os.environ.get("CODEYUN_RIME_LINT_CODEX_COMMAND", CODEX_CLI_DEFAULT_COMMAND),
        default_model=os.environ.get("CODEYUN_RIME_LINT_MODEL", CODEX_CLI_DEFAULT_MODEL),
        timeout_seconds=float(os.environ.get("CODEYUN_RIME_LINT_TIMEOUT", "120")),
        api_key="",
        supports_stream=False,
        supports_vision=False,
        requires_api_key=False,
        configured=True,
        models=(os.environ.get("CODEYUN_RIME_LINT_MODEL", CODEX_CLI_DEFAULT_MODEL),),
        workspace_dir=str(_resolve_rime_dir() or Path.cwd()),
        session_id="",
    )

    source_by_id = {str(item.get("source_id") or ""): item for item in sources}
    try:
        result = chat_with_provider(
            provider_id=provider.id,
            extra_providers=(provider,),
            messages=[{"role": "user", "content": _build_ai_lint_prompt(sources)}],
            model=provider.default_model,
            system_prompt="你是中文文本校对助手。只返回 JSON。",
            response_format={"type": "json_object"},
            timeout_seconds=provider.timeout_seconds,
        )
        payload = json.loads(_strip_json_code_fence(str(result.get("content") or "")))
    except (OllamaClientError, json.JSONDecodeError, OSError, ValueError) as exc:
        return [], f"AI 检查失败：{exc}"

    raw_issues = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(raw_issues, list):
        return [], "AI 检查没有返回 issues 列表。"

    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("source_id") or "")
        source = source_by_id.get(source_id)
        if not source:
            continue
        content = str(source.get("content") or "")
        line_no = int(raw.get("line") or 1)
        text = str(raw.get("text") or "").strip()
        suggestion = str(raw.get("suggestion") or "").strip()
        if not text:
            continue
        line_rows = _line_offsets(content)
        line_row = next((item for item in line_rows if item[0] == line_no), line_rows[0] if line_rows else (1, 0, content))
        column_index = max(0, line_row[2].find(text))
        start = line_row[1] + column_index
        severity = str(raw.get("severity") or "medium").lower()
        if severity not in {"high", "medium", "low"}:
            severity = "medium"
        confidence = raw.get("confidence")
        try:
            normalized_confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            normalized_confidence = 0.7
        _add_lint_issue(
            issues,
            seen,
            _lint_issue(
                source,
                rule="ai_codex",
                issue_type=str(raw.get("type") or "AI校对"),
                severity=severity,
                line=line_row[0],
                column=column_index + 1,
                start=start,
                end=start + len(text),
                text=text,
                message=str(raw.get("reason") or "AI 判断这里可能需要校对。"),
                suggestion=suggestion,
                confidence=normalized_confidence,
            ),
        )
    return issues, ""


def collect_rime_context_prediction_lint(
    *,
    source: str = "all",
    mode: str = "rules",
    limit: int = DEFAULT_LINT_ISSUE_LIMIT,
    history_limit: int = DEFAULT_HISTORY_ARTICLE_LIMIT,
) -> dict[str, Any]:
    rime_dir = _resolve_rime_dir()
    files = _tracked_files(rime_dir)
    normalized_source = _normalize_lint_source(source)
    normalized_mode = _normalize_lint_mode(mode)
    normalized_limit = max(1, min(int(limit or DEFAULT_LINT_ISSUE_LIMIT), 1000))

    if not rime_dir:
        return make_rime_context_prediction_lint_unavailable(
            status="unsupported_platform",
            message="当前系统没有可识别的 Rime 用户目录位置。",
            files=files,
        )

    if not rime_dir.exists():
        return make_rime_context_prediction_lint_unavailable(
            status="rime_missing",
            message="该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。",
            rime_dir=str(rime_dir),
            files=files,
        )

    try:
        sources = _collect_lint_sources(rime_dir, source=normalized_source, history_limit=int(history_limit or 0))
    except OSError as exc:
        return make_rime_context_prediction_lint_unavailable(
            status="read_error",
            message=f"读取语料失败：{exc}",
            rime_dir=str(rime_dir),
            files=files,
        )

    issues: list[dict[str, Any]] = []
    for item in sources:
        issues.extend(_scan_text_lint_rules(item))

    ai_message = ""
    if normalized_mode == "ai":
        ai_issues, ai_message = _collect_ai_lint_issues(sources)
        issues.extend(ai_issues)

    deduped: dict[str, dict[str, Any]] = {}
    for issue in issues:
        key = str(issue.get("id") or "")
        if key and key not in deduped:
            deduped[key] = issue
    sorted_issues = sorted(
        deduped.values(),
        key=lambda item: (
            _severity_rank(str(item.get("severity") or "")),
            str(item.get("source_type") or ""),
            str(item.get("source_title") or ""),
            int(item.get("line") or 0),
            int(item.get("column") or 0),
        ),
    )
    limited_issues = sorted_issues[:normalized_limit]
    high_count = sum(1 for item in sorted_issues if item.get("severity") == "high")
    medium_count = sum(1 for item in sorted_issues if item.get("severity") == "medium")
    low_count = sum(1 for item in sorted_issues if item.get("severity") == "low")
    rule_count = sum(1 for item in sorted_issues if item.get("rule") != "ai_codex")
    ai_count = sum(1 for item in sorted_issues if item.get("rule") == "ai_codex")
    status = "ready" if sources else "empty"
    message = "已完成语料检查。"
    if not sources:
        message = "没有可检查的输入历史或导入文章。"
    elif ai_message:
        message = f"{message}{ai_message}"

    return {
        "available": bool(sources),
        "status": status,
        "message": message,
        "rime_dir": str(rime_dir),
        "files": files,
        "summary": {
            "source_count": len(sources),
            "issue_count": len(sorted_issues),
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "rule_count": rule_count,
            "ai_count": ai_count,
        },
        "issues": limited_issues,
    }


def _normalize_article_text(content: str) -> str:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise RimeContextPredictionError("文章内容不能为空。")
    if len(text) > MAX_ARTICLE_CHARS:
        raise RimeContextPredictionError(f"文章内容过长，当前上限是 {MAX_ARTICLE_CHARS} 个字符。")
    return text


def _normalize_article_title(title: str | None, content: str) -> str:
    value = (title or "").strip()
    if not value:
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        value = first_line[:30].strip()
    return value or "未命名文章"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _token_to_pinyin(token: str) -> str:
    parts = lazy_pinyin(token, style=Style.NORMAL, strict=False, errors="ignore")
    return "".join(parts).replace("ü", "v").replace("u:", "v").strip().lower()


def _normalize_english_code(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    special_codes = {
        "c++": "cplusplus",
        "c#": "csharp",
        "f#": "fsharp",
    }
    if lowered in special_codes:
        return special_codes[lowered]
    lowered = lowered.replace("++", "plusplus").replace("#", "sharp")
    code = re.sub(r"[^a-z0-9]+", "", lowered)
    if not code or code.isdigit() or len(code) > 40:
        return ""
    return code


@lru_cache(maxsize=8192)
def _looks_like_pinyin_sequence(code: str) -> bool:
    text = str(code or "").lower()
    if not text or not text.isalpha():
        return False
    reachable = [False] * (len(text) + 1)
    reachable[0] = True
    for end in range(1, len(text) + 1):
        for start in range(max(0, end - 6), end):
            if reachable[start] and text[start:end] in _PINYIN_SYLLABLES:
                reachable[end] = True
                break
    return reachable[-1]


def _is_valid_domain_token(candidate: str) -> bool:
    text = str(candidate or "").strip().lower()
    if "." not in text:
        return False
    parts = [part for part in text.split(".") if part]
    if len(parts) < 2:
        return False
    suffix = parts[-1]
    if suffix not in _COMMON_DOMAIN_SUFFIXES:
        return False
    return all(re.fullmatch(r"[a-z0-9-]{1,63}", part) for part in parts)


def _is_plain_lower_unknown_english(candidate: str, code: str) -> bool:
    return (
        bool(candidate)
        and candidate.islower()
        and candidate.isalpha()
        and code not in _KNOWN_ENGLISH_CODES
    )


def _should_skip_english_token(candidate: str, code: str) -> bool:
    if not candidate or not code:
        return True
    normalized = candidate.strip()
    if len(code) < 3 and code not in {"ai", "api", "ui", "ux", "js", "ts", "uv", "go", "csharp", "fsharp"}:
        return True
    lower = normalized.lower()
    if "." in normalized:
        return not _is_valid_domain_token(normalized)
    if lower in _LATIN_TOKEN_ALLOWLIST or code in _KNOWN_ENGLISH_CODES:
        return False
    has_upper = any(char.isupper() for char in normalized)
    has_digit_or_symbol = any(char.isdigit() or char in "+#._-" for char in normalized)
    if not has_upper and not has_digit_or_symbol and _looks_like_pinyin_sequence(code):
        return True
    return False


def _english_surface_rank(candidate: str) -> tuple[int, int, int, str]:
    return (
        sum(1 for char in candidate if char.isupper()),
        sum(1 for char in candidate if char in "+#._-"),
        -len(candidate),
        candidate,
    )


def _collect_english_entries_from_texts(
    texts: list[str],
    *,
    limit: int = DEFAULT_ENGLISH_LEARNED_ROW_LIMIT,
) -> list[dict[str, Any]]:
    surface_counts_by_code: dict[str, Counter[str]] = defaultdict(Counter)
    total_counts: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for raw in _ENGLISH_TOKEN_RE.findall(text):
            candidate = raw.strip("._-")
            code = _normalize_english_code(candidate)
            if _should_skip_english_token(candidate, code):
                continue
            surface_counts_by_code[code][candidate] += 1
            total_counts[code] += 1

    entries: list[dict[str, Any]] = []
    for code, count in total_counts.items():
        candidate_counts = surface_counts_by_code[code]
        candidate = max(candidate_counts, key=lambda item: (candidate_counts[item], _english_surface_rank(item)))
        if _is_plain_lower_unknown_english(candidate, code) and int(count) < 2:
            continue
        weight = min(9999, 100 + int(count) * 20)
        entries.append({"candidate": candidate, "code": code, "count": int(count), "weight": weight})

    entries.sort(key=lambda item: (-int(item["count"]), str(item["code"]), str(item["candidate"])))
    return entries[: max(1, int(limit or DEFAULT_ENGLISH_LEARNED_ROW_LIMIT))]


def _collect_english_source_texts(rime_dir: Path) -> list[str]:
    texts: list[str] = []
    history_article = _resolve_history_article_content(rime_dir)
    history_content = str(history_article.get("content") or "").strip()
    if history_content:
        texts.append(history_content)

    manifest = _read_article_manifest(rime_dir)
    for article in manifest.get("articles", []):
        if not isinstance(article, dict) or not article.get("enabled", True):
            continue
        path = _article_content_path(rime_dir, article)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if content.strip():
            texts.append(content)
    return texts


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _rime_dict_header(name: str) -> str:
    return (
        "# Rime dictionary\n"
        "# encoding: utf-8\n"
        "---\n"
        f"name: {name}\n"
        'version: "1"\n'
        "sort: by_weight\n"
        "use_preset_vocabulary: false\n"
    )


def _ensure_english_dict_shell(rime_dir: Path) -> None:
    path = rime_dir / ENGLISH_DICT_FILE
    text = (
        _rime_dict_header("codeyun_english")
        + "import_tables:\n"
        + "  - codeyun_english_base\n"
        + "  - codeyun_english_learned\n"
        + "...\n"
    )
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        existing = ""
    if "import_tables:" not in existing or "codeyun_english_learned" not in existing:
        _write_text_atomic(path, text)


def _ensure_english_schema(rime_dir: Path) -> None:
    path = rime_dir / ENGLISH_SCHEMA_FILE
    if path.exists():
        return
    text = (
        "schema:\n"
        "  schema_id: codeyun_english\n"
        "  name: CodeYun English\n"
        '  version: "1"\n'
        "  author:\n"
        "    - CodeYun\n"
        "  description: CodeYun generated English dictionary.\n"
        "\n"
        "engine:\n"
        "  processors:\n"
        "    - ascii_composer\n"
        "    - recognizer\n"
        "    - key_binder\n"
        "    - speller\n"
        "    - selector\n"
        "    - navigator\n"
        "    - express_editor\n"
        "  segmentors:\n"
        "    - ascii_segmentor\n"
        "    - abc_segmentor\n"
        "    - fallback_segmentor\n"
        "  translators:\n"
        "    - table_translator\n"
        "  filters:\n"
        "    - uniquifier\n"
        "\n"
        "translator:\n"
        "  dictionary: codeyun_english\n"
        "  enable_completion: true\n"
        "  enable_sentence: false\n"
        "  enable_encoder: false\n"
        "  enable_user_dict: false\n"
    )
    _write_text_atomic(path, text)


def _ensure_english_base_dict(rime_dir: Path) -> None:
    path = rime_dir / ENGLISH_BASE_DICT_FILE
    if path.exists():
        return
    rows = [
        f"{candidate}\t{code}\t{weight}"
        for candidate, code, weight in sorted(_BASE_ENGLISH_TERMS, key=lambda item: (-item[2], item[1], item[0]))
    ]
    _write_text_atomic(path, _rime_dict_header("codeyun_english_base") + "...\n" + "\n".join(rows) + "\n")


def _write_english_learned_dict(rime_dir: Path, entries: list[dict[str, Any]]) -> None:
    rows = [
        f"{_clean_tsv_field(item['candidate'])}\t{_clean_tsv_field(item['code'])}\t{int(item['weight'])}"
        for item in entries
    ]
    body = "\n".join(rows)
    text = _rime_dict_header("codeyun_english_learned") + "...\n" + (body + "\n" if body else "")
    _write_text_atomic(rime_dir / ENGLISH_LEARNED_DICT_FILE, text)


def refresh_rime_english_dictionary(rime_dir: Path | None = None) -> dict[str, Any]:
    target_dir = rime_dir or _ensure_writable_rime_dir()
    _ensure_english_dict_shell(target_dir)
    _ensure_english_schema(target_dir)
    _ensure_english_base_dict(target_dir)
    entries = _collect_english_entries_from_texts(_collect_english_source_texts(target_dir))
    _write_english_learned_dict(target_dir, entries)
    return {
        "english_learned_rows": len(entries),
        "english_base_rows": len(_BASE_ENGLISH_TERMS),
    }


def _article_sentence_tokens(text: str) -> list[list[str]]:
    sentences: list[list[str]] = []
    for chunk in _SENTENCE_SPLIT_RE.split(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        tokens: list[str] = []
        for word in jieba.cut(chunk, cut_all=False):
            for match in _CJK_RE.findall(word):
                token = match.strip()
                if token:
                    tokens.append(token[:16])
        if tokens:
            sentences.append(tokens)
    return sentences


def _extract_article_contributions(article_id: str, text: str) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], float] = defaultdict(float)
    for tokens in _article_sentence_tokens(text):
        for index, candidate in enumerate(tokens):
            prefix = _token_to_pinyin(candidate)
            if not prefix:
                continue
            counts[("__global", prefix, candidate)] += 1.0
            max_len = min(MAX_CONTEXT_TOKENS, index)
            for length in range(1, max_len + 1):
                context = " ".join(tokens[index - length : index])
                counts[(context, prefix, candidate)] += 1.0

    return [
        {
            "source_id": article_id,
            "context": context,
            "prefix": prefix,
            "candidate": candidate,
            "weight": weight,
        }
        for (context, prefix, candidate), weight in counts.items()
    ]


def _article_to_payload(article: dict[str, Any]) -> dict[str, Any]:
    source_type = str(article.get("source_type") or "imported_article")
    source_label = str(article.get("source_label") or "")
    if not source_label:
        source_label = "输入历史" if source_type == "device_history" else "导入文章"
    return {
        "id": str(article.get("id") or ""),
        "title": str(article.get("title") or "未命名文章"),
        "enabled": bool(article.get("enabled", True)),
        "source_type": source_type,
        "source_key": str(article.get("source_key") or ""),
        "source_label": source_label,
        "status": str(article.get("status") or "ready"),
        "row_count": int(article.get("row_count") or 0),
        "char_count": int(article.get("char_count") or 0),
        "content_hash": str(article.get("content_hash") or ""),
        "extractor_version": int(article.get("extractor_version") or ARTICLE_EXTRACTOR_VERSION),
        "created_at": float(article.get("created_at") or 0),
        "updated_at": float(article.get("updated_at") or 0),
        "processed_at": float(article.get("processed_at") or 0),
    }


def make_rime_context_prediction_articles_unavailable(
    *,
    status: str,
    message: str,
    rime_dir: str | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "message": message,
        "rime_dir": rime_dir,
        "files": files or [],
        "summary": {
            "article_count": 0,
            "enabled_count": 0,
            "contribution_count": 0,
        },
        "articles": [],
    }


def _article_sources_response(rime_dir: Path, manifest: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
    articles = [_article_to_payload(article) for article in manifest.get("articles", []) if isinstance(article, dict)]
    articles.sort(key=lambda item: item["updated_at"], reverse=True)
    return {
        "available": True,
        "status": "ready",
        "message": "已读取导入文章清单。",
        "rime_dir": str(rime_dir),
        "files": files,
        "summary": {
            "article_count": len(articles),
            "enabled_count": sum(1 for article in articles if article["enabled"]),
            "contribution_count": sum(article["row_count"] for article in articles if article["enabled"]),
        },
        "articles": articles,
    }


def _ensure_writable_rime_dir() -> Path:
    rime_dir = _resolve_rime_dir()
    if not rime_dir:
        raise RimeContextPredictionError("当前系统没有可识别的 Rime 用户目录位置。")
    if not rime_dir.exists():
        raise RimeContextPredictionError("该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。")
    return rime_dir


def _merge_snapshot_row(
    rows_by_key: dict[tuple[str, str], dict[str, dict[str, Any]]],
    context: str,
    prefix: str,
    candidate: str,
    weight: float,
    comment: str,
    deleted_keys: set[tuple[str, str, str]] | None = None,
    *,
    replace_existing: bool = False,
    lock_entry: bool = False,
) -> None:
    context = _clean_tsv_field(context)
    prefix = _clean_tsv_field(prefix)
    candidate = _clean_tsv_field(candidate)
    if not context or not prefix or not candidate:
        return
    if deleted_keys and (context, prefix, candidate) in deleted_keys:
        return
    key = (context, prefix)
    candidate_map = rows_by_key[key]
    existing = candidate_map.get(candidate)
    if replace_existing:
        candidate_map[candidate] = {
            "weight": float(weight),
            "comment": comment or (existing or {}).get("comment") or "",
            "locked": lock_entry or bool((existing or {}).get("locked")),
        }
        return
    if existing and existing.get("locked"):
        return
    if existing:
        existing["weight"] = float(existing.get("weight") or 0) + float(weight)
        if comment:
            existing["comment"] = comment
        return
    candidate_map[candidate] = {
        "weight": float(weight),
        "comment": comment or "",
        "locked": lock_entry,
    }


def _write_prediction_snapshot(rime_dir: Path, rows: list[tuple[str, str, str, float, str]]) -> None:
    path = rime_dir / SNAPSHOT_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n")
        for context, prefix, candidate, weight, comment in rows:
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(context),
                        _clean_tsv_field(prefix),
                        _clean_tsv_field(candidate),
                        _format_weight(weight),
                        _clean_tsv_field(comment),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)


def _context_token_count(context: str) -> int:
    if context == "__global":
        return 0
    return len([part for part in str(context or "").split(" ") if part])


def _collect_runtime_rows(
    rows: list[tuple[str, str, str, float, str]],
    *,
    limit: int = DEFAULT_RUNTIME_ROW_LIMIT,
) -> list[tuple[str, str, str, float, str]]:
    selected: list[tuple[str, str, str, float, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        context, prefix, candidate, _weight, _comment = row
        if context == "__global":
            selected.append(row)
            seen.add((context, prefix, candidate))

    remaining = [row for row in rows if (row[0], row[1], row[2]) not in seen]
    remaining.sort(key=lambda row: (-row[3], _context_token_count(row[0]), row[0], row[1], row[2]))
    for row in remaining:
        if len(selected) >= limit:
            break
        selected.append(row)

    selected.sort(key=lambda row: (0 if row[0] == "__global" else 1, row[0], row[1], -row[3], row[2]))
    return selected


def _write_prediction_runtime(rime_dir: Path, rows: list[tuple[str, str, str, float, str]]) -> int:
    runtime_rows = _collect_runtime_rows(rows)
    path = rime_dir / RUNTIME_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n")
        for context, prefix, candidate, weight, comment in runtime_rows:
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(context),
                        _clean_tsv_field(prefix),
                        _clean_tsv_field(candidate),
                        _format_weight(weight),
                        _clean_tsv_field(comment),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)
    return len(runtime_rows)


def _write_prediction_hot(rime_dir: Path, rows: list[tuple[str, str, str, float, str]]) -> int:
    hot_rows = [row for row in rows if row[0] == "__global" and float(row[3] or 0) >= DEFAULT_HOT_MIN_WEIGHT]
    hot_rows.sort(key=lambda row: (row[1], -row[3], row[2]))
    path = rime_dir / HOT_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# context_key\tpinyin_prefix\tcandidate\tweight\tcomment\n")
        for context, prefix, candidate, weight, comment in hot_rows:
            fh.write(
                "\t".join(
                    [
                        _clean_tsv_field(context),
                        _clean_tsv_field(prefix),
                        _clean_tsv_field(candidate),
                        _format_weight(weight),
                        _clean_tsv_field(comment),
                    ]
                )
                + "\n"
            )
    os.replace(tmp, path)
    return len(hot_rows)


def rebuild_rime_context_prediction_snapshot(
    rime_dir: Path | None = None,
    *,
    allow_snapshot_fallback: bool = False,
) -> dict[str, Any]:
    target_dir = rime_dir or _ensure_writable_rime_dir()
    manifest = _read_article_manifest(target_dir)
    deleted_keys = _read_deleted_candidate_keys(target_dir)
    enabled_ids = {
        str(article.get("id"))
        for article in manifest.get("articles", [])
        if isinstance(article, dict) and article.get("enabled", True)
    }
    article_rows = _read_article_contributions(target_dir)

    rows_by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    merged_source_rows = 0
    for row in _read_prediction_rows(target_dir / SEED_FILE):
        merged_source_rows += 1
        _merge_snapshot_row(
            rows_by_key,
            row["context"],
            row["prefix"],
            row["candidate"],
            row["weight"],
            row.get("comment") or "手动规则",
            deleted_keys,
            replace_existing=_is_manual_rule_comment(row.get("comment")),
            lock_entry=_is_manual_rule_comment(row.get("comment")),
        )
    for row in _read_count_rows(target_dir / COUNTS_FILE):
        merged_source_rows += 1
        _merge_snapshot_row(
            rows_by_key,
            row["context"],
            row["prefix"],
            row["candidate"],
            row["weight"],
            row.get("comment") or "输入历史",
            deleted_keys,
        )
    for row in article_rows:
        if row["source_id"] in enabled_ids:
            merged_source_rows += 1
            _merge_snapshot_row(
                rows_by_key,
                row["context"],
                row["prefix"],
                row["candidate"],
                row["weight"],
                "导入文章",
                deleted_keys,
            )

    if allow_snapshot_fallback and not merged_source_rows:
        for row in _read_prediction_rows(target_dir / SNAPSHOT_FILE):
            _merge_snapshot_row(
                rows_by_key,
                row["context"],
                row["prefix"],
                row["candidate"],
                row["weight"],
                row.get("comment") or "",
                deleted_keys,
            )

    output: list[tuple[str, str, str, float, str]] = []
    for (context, prefix), candidate_map in rows_by_key.items():
        ranked = sorted(candidate_map.items(), key=lambda item: (-float(item[1].get("weight") or 0), item[0]))
        for candidate, entry in ranked[:DEFAULT_TOPK_PER_KEY]:
            output.append((context, prefix, candidate, float(entry.get("weight") or 0), str(entry.get("comment") or "")))
    output.sort(key=lambda row: (row[0], row[1], -row[3], row[2]))
    _write_prediction_snapshot(target_dir, output)
    runtime_rows = _write_prediction_runtime(target_dir, output)
    hot_rows = _write_prediction_hot(target_dir, output)
    english_result = refresh_rime_english_dictionary(target_dir)
    _write_refresh_meta(target_dir)
    return {
        "snapshot_rows": len(output),
        "runtime_rows": runtime_rows,
        "hot_rows": hot_rows,
        "enabled_article_count": len(enabled_ids),
        **english_result,
    }


def delete_rime_context_prediction_candidate(
    *,
    context: str,
    prefix: str,
    candidate: str,
) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    key = _normalize_candidate_key(context, prefix, candidate)
    rows = _read_deleted_candidate_rows(rime_dir)
    if not any((row["context"], row["prefix"], row["candidate"]) == key for row in rows):
        rows.append(
            {
                "context": key[0],
                "prefix": key[1],
                "candidate": key[2],
                "deleted_at": time.time(),
            }
        )
        _write_deleted_candidate_rows(rime_dir, rows)
    rebuild_rime_context_prediction_snapshot(rime_dir, allow_snapshot_fallback=True)
    return collect_rime_context_prediction_tree()


def update_rime_context_prediction_candidate(
    *,
    original_context: str | None,
    original_prefix: str | None,
    original_candidate: str | None,
    context: str,
    prefix: str,
    candidate: str,
    weight: float,
) -> dict[str, Any]:
    if float(weight) <= 0:
        raise RimeContextPredictionError("权重必须大于 0。")

    rime_dir = _ensure_writable_rime_dir()
    target_key = _normalize_candidate_key(context, prefix, candidate)
    original_key = None
    if original_context and original_prefix and original_candidate:
        original_key = _normalize_candidate_key(original_context, original_prefix, original_candidate)

    seed_path = rime_dir / SEED_FILE
    seed_rows = _read_prediction_rows(seed_path)
    next_seed_rows: list[dict[str, Any]] = []
    for row in seed_rows:
        row_key = _candidate_key(row.get("context"), row.get("prefix"), row.get("candidate"))
        is_manual_row = _is_manual_rule_comment(row.get("comment"))
        if is_manual_row and row_key in {key for key in [original_key, target_key] if key is not None}:
            continue
        next_seed_rows.append(row)
    next_seed_rows.append(
        {
            "context": target_key[0],
            "prefix": target_key[1],
            "candidate": target_key[2],
            "weight": float(weight),
            "comment": "手动规则",
        }
    )
    _write_prediction_rows_file(seed_path, next_seed_rows)

    deleted_rows = _read_deleted_candidate_rows(rime_dir)
    next_deleted_rows = [
        row for row in deleted_rows
        if (row["context"], row["prefix"], row["candidate"]) != target_key
    ]
    if original_key and original_key != target_key:
        if not any((row["context"], row["prefix"], row["candidate"]) == original_key for row in next_deleted_rows):
            next_deleted_rows.append(
                {
                    "context": original_key[0],
                    "prefix": original_key[1],
                    "candidate": original_key[2],
                    "deleted_at": time.time(),
                }
            )
    if len(next_deleted_rows) != len(deleted_rows) or (original_key and original_key != target_key):
        _write_deleted_candidate_rows(rime_dir, next_deleted_rows)

    rebuild_rime_context_prediction_snapshot(rime_dir, allow_snapshot_fallback=True)
    payload = collect_rime_context_prediction_tree()
    payload["message"] = "已更新候选词手动规则。"
    return payload


def refresh_rime_context_prediction_tree(
    *,
    force: bool = False,
    limit: int | None = 50000,
    source: str | None = "snapshot",
) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    source_fingerprint = _prediction_source_fingerprint(rime_dir)
    refresh_meta = _read_refresh_meta(rime_dir)
    if (
        not force
        and _prediction_outputs_exist(rime_dir)
        and refresh_meta.get("source_fingerprint") == source_fingerprint
    ):
        payload = collect_rime_context_prediction_tree(limit=limit, source=source)
        payload["message"] = "预测索引已是最新，源数据没有变化，已跳过重建。"
        return payload

    if _can_rebuild_from_history(rime_dir):
        refresh_result = rebuild_rime_context_prediction_from_history(rime_dir)
    else:
        refresh_result = _fold_pending_events(rime_dir)
        refresh_result.update(rebuild_rime_context_prediction_snapshot(rime_dir))
    payload = collect_rime_context_prediction_tree(limit=limit, source=source)
    if refresh_result.get("source") == HISTORY_FILE:
        source_label = "输入历史修订稿" if refresh_result.get("history_article_edited") else "输入历史"
        payload["message"] = (
            f"已从{source_label}重建预测索引："
            f"{int(refresh_result.get('history_events') or 0)} 条输入事件，"
            f"{int(refresh_result.get('count_entries') or 0)} 条索引记录，"
            f"{int(refresh_result.get('english_learned_rows') or 0)} 个英文学习词。"
        )
    else:
        payload["message"] = (
            f"已合并增量输入并重建预测索引："
            f"{int(refresh_result.get('pending_rows') or 0)} 条待合并记录，"
            f"{int(refresh_result.get('english_learned_rows') or 0)} 个英文学习词。"
        )
    return payload


def collect_rime_context_prediction_articles() -> dict[str, Any]:
    rime_dir = _resolve_rime_dir()
    files = _tracked_files(rime_dir)

    if not rime_dir:
        return make_rime_context_prediction_articles_unavailable(
            status="unsupported_platform",
            message="当前系统没有可识别的 Rime 用户目录位置。",
            files=files,
        )

    if not rime_dir.exists():
        return make_rime_context_prediction_articles_unavailable(
            status="rime_missing",
            message="该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。",
            rime_dir=str(rime_dir),
            files=files,
        )

    manifest = _read_article_manifest(rime_dir)
    return _article_sources_response(rime_dir, manifest, files)


def _upsert_article_contributions(rime_dir: Path, article: dict[str, Any], content: str) -> None:
    existing = [row for row in _read_article_contributions(rime_dir) if row["source_id"] != article["id"]]
    rows = _extract_article_contributions(str(article["id"]), content)
    article["row_count"] = len(rows)
    article["status"] = "ready"
    article["processed_at"] = time.time()
    article["extractor_version"] = ARTICLE_EXTRACTOR_VERSION
    _write_article_contributions(rime_dir, existing + rows)


def import_rime_context_prediction_article(
    *,
    title: str | None,
    content: str,
    enabled: bool = True,
    source_type: str = "imported_article",
    source_key: str | None = None,
    source_label: str | None = None,
) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    text = _normalize_article_text(content)
    now = time.time()
    digest = _content_hash(text)
    normalized_source_type = _clean_tsv_field(source_type or "imported_article") or "imported_article"
    normalized_source_key = _clean_tsv_field(source_key or "")
    normalized_source_label = _clean_tsv_field(source_label or "")
    manifest = _read_article_manifest(rime_dir)
    articles = manifest.setdefault("articles", [])

    target_article = None
    if normalized_source_key:
        for article in articles:
            if (
                isinstance(article, dict)
                and str(article.get("source_type") or "imported_article") == normalized_source_type
                and str(article.get("source_key") or "") == normalized_source_key
            ):
                target_article = article
                break
    else:
        for article in articles:
            if (
                isinstance(article, dict)
                and str(article.get("source_type") or "imported_article") == "imported_article"
                and article.get("content_hash") == digest
            ):
                target_article = article
                break

    if target_article is None:
        article_id = uuid.uuid4().hex[:16]
        content_dir = rime_dir / ARTICLE_CONTENT_DIR
        content_dir.mkdir(parents=True, exist_ok=True)
        content_path = content_dir / f"{article_id}.txt"
        content_path.write_text(text, encoding="utf-8")
        target_article = {
            "id": article_id,
            "title": _normalize_article_title(title, text),
            "enabled": bool(enabled),
            "source_type": normalized_source_type,
            "source_key": normalized_source_key,
            "source_label": normalized_source_label,
            "content_hash": digest,
            "content_path": f"{ARTICLE_CONTENT_DIR}/{article_id}.txt",
            "char_count": len(text),
            "row_count": 0,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "processed_at": 0,
            "extractor_version": ARTICLE_EXTRACTOR_VERSION,
        }
        articles.append(target_article)
        _upsert_article_contributions(rime_dir, target_article, text)
    else:
        old_digest = str(target_article.get("content_hash") or "")
        next_title = _normalize_article_title(title, text)
        next_enabled = bool(enabled)
        content_path = _article_content_path(rime_dir, target_article)
        if (
            old_digest == digest
            and str(target_article.get("title") or "") == next_title
            and bool(target_article.get("enabled", True)) == next_enabled
            and str(target_article.get("source_type") or "imported_article") == normalized_source_type
            and str(target_article.get("source_key") or "") == normalized_source_key
            and str(target_article.get("source_label") or "") == normalized_source_label
            and int(target_article.get("extractor_version") or 0) == ARTICLE_EXTRACTOR_VERSION
            and str(target_article.get("status") or "") == "ready"
            and content_path.exists()
        ):
            return _article_sources_response(rime_dir, manifest, _tracked_files(rime_dir))

        target_article["title"] = next_title
        target_article["enabled"] = bool(enabled)
        target_article["source_type"] = normalized_source_type
        target_article["source_key"] = normalized_source_key
        target_article["source_label"] = normalized_source_label
        target_article["content_hash"] = digest
        target_article["updated_at"] = now
        target_article["char_count"] = len(text)
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(text, encoding="utf-8")
        if (
            old_digest != digest
            or target_article.get("extractor_version") != ARTICLE_EXTRACTOR_VERSION
            or not int(target_article.get("row_count") or 0)
        ):
            _upsert_article_contributions(rime_dir, target_article, text)

    _write_article_manifest(rime_dir, manifest)
    rebuild_rime_context_prediction_snapshot(rime_dir)
    return _article_sources_response(rime_dir, manifest, _tracked_files(rime_dir))


def update_rime_context_prediction_article(
    article_id: str,
    *,
    enabled: bool | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    manifest = _read_article_manifest(rime_dir)
    article = next(
        (
            item
            for item in manifest.get("articles", [])
            if isinstance(item, dict) and str(item.get("id") or "") == article_id
        ),
        None,
    )
    if not article:
        raise RimeContextPredictionError("未找到这篇导入文章。")

    if enabled is not None:
        article["enabled"] = bool(enabled)
    if title is not None and title.strip():
        article["title"] = title.strip()
    article["updated_at"] = time.time()
    _write_article_manifest(rime_dir, manifest)
    rebuild_rime_context_prediction_snapshot(rime_dir)
    return _article_sources_response(rime_dir, manifest, _tracked_files(rime_dir))


def delete_rime_context_prediction_article(article_id: str) -> dict[str, Any]:
    rime_dir = _ensure_writable_rime_dir()
    manifest = _read_article_manifest(rime_dir)
    articles = manifest.get("articles", [])
    kept_articles = []
    removed: dict[str, Any] | None = None
    for article in articles:
        if isinstance(article, dict) and str(article.get("id") or "") == article_id:
            removed = article
        else:
            kept_articles.append(article)
    if removed is None:
        raise RimeContextPredictionError("未找到这篇导入文章。")

    manifest["articles"] = kept_articles
    _write_article_manifest(rime_dir, manifest)
    _write_article_contributions(
        rime_dir,
        [row for row in _read_article_contributions(rime_dir) if row["source_id"] != article_id],
    )
    content_path = _article_content_path(rime_dir, removed)
    content_path.unlink(missing_ok=True)
    rebuild_rime_context_prediction_snapshot(rime_dir)
    return _article_sources_response(rime_dir, manifest, _tracked_files(rime_dir))


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    contexts = {str(row.get("context") or "") for row in rows}
    prefixes = {(str(row.get("context") or ""), str(row.get("prefix") or "")) for row in rows}
    candidates = {
        (
            str(row.get("context") or ""),
            str(row.get("prefix") or ""),
            str(row.get("candidate") or ""),
        )
        for row in rows
    }
    return {
        "row_count": len(rows),
        "context_count": len(contexts),
        "prefix_count": len(prefixes),
        "candidate_count": len(candidates),
    }


def make_rime_context_prediction_unavailable(
    *,
    status: str,
    message: str,
    rime_dir: str | None = None,
    files: list[dict[str, Any]] | None = None,
    source_kind: str = "snapshot",
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "message": message,
        "rime_dir": rime_dir,
        "source_kind": source_kind,
        "source": None,
        "source_path": None,
        "updated_at": None,
        "files": files or [],
        "summary": {
            "row_count": 0,
            "context_count": 0,
            "prefix_count": 0,
            "candidate_count": 0,
        },
        "rows": [],
    }


def _normalize_prediction_tree_source(source: str | None) -> str:
    value = str(source or "").strip().lower()
    if value in {"snapshot", "hot", "seed"}:
        return value
    return "snapshot"


def _prediction_tree_source_candidates(rime_dir: Path, source_kind: str) -> list[tuple[str, Path]]:
    if source_kind == "hot":
        return [(HOT_FILE, rime_dir / HOT_FILE)]
    if source_kind == "seed":
        return [(SEED_FILE, rime_dir / SEED_FILE)]
    return [
        (SNAPSHOT_FILE, rime_dir / SNAPSHOT_FILE),
        (COUNTS_FILE, rime_dir / COUNTS_FILE),
        (SEED_FILE, rime_dir / SEED_FILE),
    ]


def collect_rime_context_prediction_tree(
    limit: int | None = 50000,
    *,
    source: str | None = "snapshot",
) -> dict[str, Any]:
    rime_dir = _resolve_rime_dir()
    files = _tracked_files(rime_dir)
    source_kind = _normalize_prediction_tree_source(source)

    if not rime_dir:
        return make_rime_context_prediction_unavailable(
            status="unsupported_platform",
            message="当前系统没有可识别的 Rime 用户目录位置。",
            files=files,
            source_kind=source_kind,
        )

    if not rime_dir.exists():
        return make_rime_context_prediction_unavailable(
            status="rime_missing",
            message="该设备未发现 Rime 用户目录，可能没有安装小狼毫或尚未启动过 Rime。",
            rime_dir=str(rime_dir),
            files=files,
            source_kind=source_kind,
        )

    source_candidates = _prediction_tree_source_candidates(rime_dir, source_kind)
    source_name = None
    source_path = None
    rows: list[dict[str, Any]] = []
    read_error = None

    for name, path in source_candidates:
        if not path.exists():
            continue
        source_name = name
        source_path = path
        try:
            rows = _read_prediction_rows(path, limit=limit)
        except OSError as exc:
            read_error = str(exc)
            rows = []
        break

    if not source_path:
        return make_rime_context_prediction_unavailable(
            status="extension_missing",
            message="该设备已发现 Rime 用户目录，但没有上下文预测扩展的数据文件。",
            rime_dir=str(rime_dir),
            files=files,
            source_kind=source_kind,
        )

    if read_error:
        return make_rime_context_prediction_unavailable(
            status="read_error",
            message=f"读取上下文预测索引失败：{read_error}",
            rime_dir=str(rime_dir),
            files=files,
            source_kind=source_kind,
        )

    summary = _summarize_rows(rows)
    status = "ready" if rows else "empty"
    message = "已读取上下文预测索引。" if rows else "上下文预测索引文件存在，但暂时没有可展示记录。"
    stat = source_path.stat()
    return {
        "available": bool(rows),
        "status": status,
        "message": message,
        "rime_dir": str(rime_dir),
        "source_kind": source_kind,
        "source": source_name,
        "source_path": str(source_path),
        "updated_at": stat.st_mtime,
        "files": files,
        "summary": summary,
        "rows": rows,
    }
