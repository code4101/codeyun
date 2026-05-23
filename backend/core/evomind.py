from __future__ import annotations

import json
import re
import hashlib
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.ai_chat import OllamaClientError, chat_with_provider
from backend.core.codex_sessions import build_codex_overview, build_codex_thread_detail
from backend.core.settings import get_settings


EXPLICIT_LEARNING_PATTERNS = (
    re.compile(r"AI\s*请学习", re.IGNORECASE),
    re.compile(r"EvoMind.*(?:样例|案例|学习|沉淀)", re.IGNORECASE),
    re.compile(r"(?:可以|应该|适合).*(?:作为|当作).*(?:样例|案例|反面教材|正面教材)"),
    re.compile(r"(?:以后遇到|以后再遇到|以后这类|下次遇到|下次再遇到).*(?:不要|要|应该|必须|记住)"),
    re.compile(r"(?:记住|沉淀|写进|更新).*(?:skill|技能|AGENTS|docs|文档)"),
)

FRICTION_PATTERNS = (
    re.compile(r"(?:生气|发火|脾气|骂|烦|恼火|火大|崩溃)"),
    re.compile(r"(?:你又|又来|又是|反复|多次|一直|始终).*(?:错|犯|没懂|没听|没按|没理解|重复|试不出来)"),
    re.compile(r"(?:不是说了|我说过|都说了|刚说了|还这样|怎么还)"),
    re.compile(r"(?:垃圾|离谱|傻|蠢|妈的|他妈|卧槽|艹|操(?!作))"),
)

CORRECTION_PATTERNS = (
    re.compile(r"(?:不要|别|不能).*(?:复杂|冗余|重复|臃肿|多余|过度|废话)"),
    re.compile(r"(?:简洁|优雅|简单|低冗余|少冗余|克制)"),
    re.compile(r"(?:反面教材|正面教材|最终修改|最终改成|最后改成|对比)"),
    re.compile(r"(?:用户最终|我最终|我手动).*(?:修改|改出|删掉|调整)"),
)

CONCRETE_OPERATION_PATTERNS = (
    re.compile(r"https?://|localhost|[A-Za-z]:[\\/]|/api/|\.(?:py|ts|tsx|js|vue|md|json|xlsx?)\b", re.IGNORECASE),
    re.compile(r"<image>|截图|页面|按钮|右键|菜单|tab|sheet|表格|字段|列|行|单元格|列宽", re.IGNORECASE),
    re.compile(r"文件|目录|数据库|缓存|接口|日志|报错|测试|运行|命令|脚本|函数|代码|配置|token|设备"),
    re.compile(r"样式|排版|颜色|高度|宽度|加载|刷新|删除|重命名|筛选|排序|权限|鉴权"),
)

STRONG_OPERATION_PATTERN = re.compile(
    r"https?://|localhost|[A-Za-z]:[\\/]|/api/|<image>|截图|页面|按钮|右键|菜单|tab|sheet|表格|字段|列|行|单元格|列宽|"
    r"数据库|接口|日志|报错|测试|运行|脚本|函数|代码|配置|token|设备|样式|排版|颜色|高度|宽度|加载|刷新|重命名|筛选|排序|权限|鉴权",
    re.IGNORECASE,
)

META_LEARNING_PATTERN = re.compile(r"EvoMind|样例|案例池|学习素材|提示词|skill|AGENTS|docs", re.IGNORECASE)
ABSTRACT_DISCUSSION_PATTERN = re.compile(r"如何|方案|思路|机制|概念|设计|验证|捕捉|优化")
REFERENCE_TEXT_PATTERN = re.compile(r"你先看看这篇文章|阅读这篇文章|这篇文章[:：]|文章[:：]")

SOURCE_TEXT_LIMIT = 900
PATTERN_TEXT_LIMIT = 90
JWT_TOKEN_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]{8,}\.[A-Za-z0-9._-]{8,}\b")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?P<key>token|api[_-]?key|secret|password|passwd|pwd|authorization)\s*[:=]\s*(?P<value>[A-Za-z0-9._~+/=-]{8,})"
)
LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
EVOMIND_DEEPSEEK_SCAN_TIMEOUT_SECONDS = 240
EVOMIND_DEEPSEEK_DEFAULT_CANDIDATE_LIMIT = 40
EVOMIND_DEEPSEEK_PROPOSAL_TIMEOUT_SECONDS = 300
EVOMIND_DEEPSEEK_CASE_CARD_TIMEOUT_SECONDS = 300
EVOMIND_DEEPSEEK_PROVIDER_ID = "deepseek"
EVOMIND_DEEPSEEK_MODEL = "deepseek-v4-pro"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_CACHE_SCHEMA_VERSION = 1
SCAN_RULE_VERSION = "2026-05-23.deepseek-semantic-cache.v1"
SCAN_SIGNAL_TYPES = {
    "explicit_learning_marker",
    "friction",
    "repeated_correction",
    "final_artifact_delta",
}
DEEPSEEK_SCAN_INSTRUCTIONS = (
    "你是 EvoMind 的真实案例扫描器。你只能分析我提供的候选片段，不要读取文件、不要运行命令、不要改代码。\n"
    "目标：从 Codex 历史对话候选中找出真正值得沉淀为 skill/AGENTS/docs 的高价值操作案例。\n\n"
    "判定标准：\n"
    "1. 优先保留具体业务操作案例，例如 UI 修改、代码修复、文件/接口/表格/菜单/验证流程等。\n"
    "2. 丢弃纯语言讨论、抽象方案、只是在谈 EvoMind 概念但没有真实操作上下文的候选。\n"
    "3. 高价值案例应能看出：原始任务、AI 初始问题、用户纠正、最终可迁移范式。\n"
    "4. 如果 evidence_turns 存在，必须结合多轮证据链判断，不要只看四个摘要字段；多轮反复纠正通常比单轮更有学习价值。\n"
    "5. evidence_turns 每条消息的 label/kind 是精炼证据类型，例如任务、反面、纠正、关键、正向、背景；这些标签只辅助阅读，不限制证据条数。\n"
    "6. 不要机械复述候选字段，要提炼成后续 AI 可执行的规则。\n"
    "7. 不要编造来源，不要扩大到候选以外的事实。\n"
    "8. 每个候选都必须返回一条判定；不合格候选返回 keep=false 和 reject_reason。\n"
    "9. title 必须是这条案例学到的规则精髓，概括以后要写入 skill 的判断动作；不要写素材标题、线程标题、任务对象名或问题现象。\n"
    "10. anti_patterns / positive_patterns 必须是完整、可执行、可复用的模式描述，不得直接复制聊天口语碎片。\n"
    "11. 禁止输出“对，你感觉没错”“好像还是大啊”这类无语义原话；如果原话有价值，必须改写为失败模式或正向做法。\n"
)
DEEPSEEK_PROPOSAL_INSTRUCTIONS = (
    "你是 EvoMind 的 skill 优化提案生成器。你只能分析我提供的案例、提示词规则和 skill 摘要，不要读取文件、不要运行命令、不要改代码。\n"
    "目标：把真实案例中的失败模式和正向范式，转成可审查、可验证、可回滚的 skill/AGENTS/docs 优化提案。\n\n"
    "要求：\n"
    "1. skill 优化是核心目标；AGENTS.md 和 docs 只在规则明显属于项目级协作约定或设计说明时作为补充目标。\n"
    "2. 优先优化已有 skill，不要因为一个案例就建议新建大量 skill。\n"
    "3. 规则必须来自具体案例，不要写空泛方法论。\n"
    "4. 必须写清触发条件、适用范围、排除范围、风险和验证计划。\n"
    "5. 不要自动宣布应该写入正式文件，只生成 proposal。\n"
    "6. 输出必须是 JSON 对象，不要 markdown 包裹。\n"
)
DEEPSEEK_CASE_CARD_INSTRUCTIONS = (
    "你是 EvoMind 的案例卡结构化器。你只能分析我提供的案例素材，不要读取文件、不要运行命令、不要改代码。\n"
    "目标：把原始任务、AI 初始问题、用户纠正、最终范式整理成可学习的案例卡。\n\n"
    "最重要的要求：\n"
    "1. anti_patterns 是反面模式，必须描述 AI 失败做法或误判方式，不是复制用户原话。\n"
    "2. positive_patterns 是正向范式，必须描述以后遇到同类任务应该怎么做，不是复制最终回复碎片。\n"
    "3. inferred_rule 必须是一句完整规则，表达场景、判断、动作和避免事项。\n"
    "4. title 必须是规则精髓标题，用短语概括这条案例最终要沉淀到 skill 的判断原则；不要复用素材标题、线程标题或具体任务对象名。\n"
    "5. 如果 evidence_turns 存在，必须结合完整多轮证据链提炼规则，不要把案例理解成固定四轮对话。\n"
    "6. 删除所有聊天填充语和情绪原话，例如“对，你感觉没错”“好像还是大啊”“你还是大啊”。\n"
    "7. 不要输出空泛口号，例如“保持简洁优雅”；必须写成可执行判断。\n"
    "8. 只返回 JSON 对象，不要 markdown，不要解释。\n"
)


def _redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    text = JWT_TOKEN_RE.sub("[REDACTED_JWT]", text)
    text = SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group('key')}: [REDACTED]", text)
    return LONG_SECRET_RE.sub("[REDACTED_TOKEN]", text)


def _compact_text(value: Any, *, limit: int = SOURCE_TEXT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", _redact_sensitive_text(value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _safe_string_list(value: Any, *, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact_text(item, limit=180)
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _safe_evidence_turns(value: Any, *, limit: int = 16) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    turns: list[dict[str, Any]] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        text = _compact_text(item.get("text") or "", limit=1400)
        if not text:
            continue
        seq = item.get("seq")
        kind = _compact_text(item.get("kind") or "", limit=32)
        label = _compact_text(item.get("label") or "", limit=16)
        turns.append(
            {
                "seq": seq if isinstance(seq, int) else None,
                "role": _compact_text(item.get("role") or "unknown", limit=24),
                "kind": kind or "context",
                "label": label or "背景",
                "text": text,
                "timestamp": _compact_text(item.get("timestamp") or "", limit=80),
                "is_signal": bool(item.get("is_signal")),
            }
        )
    return turns


def _evidence_turn_kind(messages: list[dict[str, Any]], index: int, center_index: int, start: int, end: int) -> tuple[str, str]:
    role = str(messages[index].get("role") or "")
    if index == center_index:
        return "key", "关键"

    first_user_index = next(
        (turn_index for turn_index in range(start, end) if messages[turn_index].get("role") == "user"),
        None,
    )
    if role == "user":
        if index == first_user_index:
            return "task", "任务"
        if index < center_index:
            return "correction", "纠正"
        return "followup", "补充"
    if role == "assistant":
        if index < center_index:
            return "anti", "反面"
        return "positive", "正向"
    return "context", "背景"


def _build_evidence_turns(
    messages: list[dict[str, Any]],
    center_index: int,
    *,
    before: int = 8,
    after: int = 6,
) -> list[dict[str, Any]]:
    start = max(0, center_index - before)
    end = min(len(messages), center_index + after + 1)
    turns: list[dict[str, Any]] = []
    for index, message in enumerate(messages[start:end], start=start):
        kind, label = _evidence_turn_kind(messages, index, center_index, start, end)
        turns.append(
            {
                "seq": message.get("seq"),
                "role": message.get("role"),
                "kind": kind,
                "label": label,
                "text": message.get("text"),
                "timestamp": message.get("timestamp"),
                "is_signal": index == center_index,
            }
        )
    return _safe_evidence_turns(turns, limit=before + after + 1)


def _stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evomind_cache_path() -> Path:
    return get_settings().data_dir / "evomind" / "scan-cache.json"


def _evomind_pending_imports_path() -> Path:
    return get_settings().data_dir / "evomind" / "pending-imports.json"


def _skills_root_path() -> Path:
    return REPO_ROOT.parent / "skills"


def _scanner_rule_hash(scan_rule_text: str | None = None) -> str:
    return _stable_json_hash(
        {
            "schema": SCAN_CACHE_SCHEMA_VERSION,
            "version": SCAN_RULE_VERSION,
            "explicit_learning_patterns": [pattern.pattern for pattern in EXPLICIT_LEARNING_PATTERNS],
            "friction_patterns": [pattern.pattern for pattern in FRICTION_PATTERNS],
            "correction_patterns": [pattern.pattern for pattern in CORRECTION_PATTERNS],
            "concrete_operation_patterns": [pattern.pattern for pattern in CONCRETE_OPERATION_PATTERNS],
            "strong_operation_pattern": STRONG_OPERATION_PATTERN.pattern,
            "meta_learning_pattern": META_LEARNING_PATTERN.pattern,
            "abstract_discussion_pattern": ABSTRACT_DISCUSSION_PATTERN.pattern,
            "reference_text_pattern": REFERENCE_TEXT_PATTERN.pattern,
            "deepseek_instructions": DEEPSEEK_SCAN_INSTRUCTIONS,
            "scan_rule_text": _compact_text(scan_rule_text or "", limit=12000),
        }
    )


def _candidate_cache_key(candidate: dict[str, Any]) -> str:
    source = dict(candidate.get("source") or {})
    return _stable_json_hash(
        {
            "id": candidate.get("id"),
            "thread_id": source.get("thread_id"),
            "message_seq": source.get("message_seq"),
            "timestamp": source.get("timestamp"),
            "original_task": candidate.get("original_task"),
            "bad_attempt": candidate.get("bad_attempt"),
            "user_corrections": candidate.get("user_corrections"),
            "final_pattern": candidate.get("final_pattern"),
            "evidence_turns": candidate.get("evidence_turns"),
        }
    )


def _load_scan_cache(rule_hash: str, *, reset_cache: bool = False) -> tuple[dict[str, Any], bool]:
    path = _evomind_cache_path()
    if reset_cache:
        path.unlink(missing_ok=True)
        return {"items": {}}, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"items": {}}, False

    if payload.get("schema") != SCAN_CACHE_SCHEMA_VERSION:
        return {"items": {}}, True
    if payload.get("rule_hash") != rule_hash:
        return {"items": {}}, True
    items = payload.get("items")
    if not isinstance(items, dict):
        return {"items": {}}, True
    return {"items": items}, False


def _write_scan_cache(rule_hash: str, cache: dict[str, Any]) -> None:
    path = _evomind_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCAN_CACHE_SCHEMA_VERSION,
        "rule_hash": rule_hash,
        "rule_version": SCAN_RULE_VERSION,
        "updated_at": time.time(),
        "items": cache.get("items") if isinstance(cache.get("items"), dict) else {},
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def save_evomind_pending_imports(payload: dict[str, Any]) -> None:
    path = _evomind_pending_imports_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_evomind_pending_imports() -> dict[str, Any]:
    path = _evomind_pending_imports_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "root_dir": "",
            "total_threads": 0,
            "scanned_threads": 0,
            "skipped_threads": 0,
            "scanned_messages": 0,
            "heuristic_candidate_count": 0,
            "analysis_mode": "pending_import",
            "codex_cli_used": False,
            "codex_cli_invoked": False,
            "cache_hit_count": 0,
            "cache_miss_count": 0,
            "cache_rule_hash": "",
            "cache_rule_mismatch": False,
            "cache_reset": False,
            "items": [],
        }
    if not isinstance(payload.get("items"), list):
        payload["items"] = []
    return payload


def clear_evomind_pending_imports() -> None:
    _evomind_pending_imports_path().unlink(missing_ok=True)


def _extract_user_request_text(value: Any) -> str:
    text = str(value or "")
    marker = re.search(r"##\s*My request for Codex:\s*", text, flags=re.IGNORECASE)
    if marker:
        return text[marker.end() :].strip()
    return text.strip()


def _first_sentence(value: Any, *, limit: int = PATTERN_TEXT_LIMIT) -> str:
    text = _compact_text(value, limit=limit * 2)
    parts = re.split(r"[。！？!?；;\n]", text, maxsplit=1)
    return _compact_text(parts[0] if parts else text, limit=limit)


def _contains_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _operation_signal_score(*values: Any) -> int:
    text = "\n".join(str(value or "") for value in values)
    score = 0
    for pattern in CONCRETE_OPERATION_PATTERNS:
        if pattern.search(text):
            score += 22
    return score


def _has_strong_operation_signal(*values: Any) -> bool:
    return STRONG_OPERATION_PATTERN.search("\n".join(str(value or "") for value in values)) is not None


def _is_abstract_meta_only(text: str, *, thread: dict[str, Any], bad_attempt: str) -> bool:
    haystack = f"{thread.get('title') or ''}\n{text}\n{bad_attempt}"
    return META_LEARNING_PATTERN.search(haystack) is not None and _operation_signal_score(haystack) == 0


def _rank_case_score(
    *,
    base_score: int,
    thread: dict[str, Any],
    user_text: str,
    original_task: str,
    bad_attempt: str,
    final_pattern: str,
) -> int:
    primary_text = f"{thread.get('title') or ''}\n{original_task}\n{user_text}"
    primary_operation_score = _operation_signal_score(primary_text)
    support_operation_score = _operation_signal_score(bad_attempt, final_pattern)
    operation_score = primary_operation_score + (min(support_operation_score, 22) if primary_operation_score else 0)
    score = base_score + operation_score

    if _is_abstract_meta_only(user_text, thread=thread, bad_attempt=bad_attempt) or (
        META_LEARNING_PATTERN.search(primary_text) is not None and not _has_strong_operation_signal(primary_text)
    ):
        score -= 260
    if REFERENCE_TEXT_PATTERN.search(primary_text):
        score -= 220
    elif primary_operation_score < 44 and ABSTRACT_DISCUSSION_PATTERN.search(user_text):
        score -= 40
    if user_text.startswith("# Context from my IDE setup") or user_text.startswith("# Files mentioned"):
        score -= 60

    return max(0, score)


def _detect_signal(text: str) -> tuple[str | None, str, int]:
    if _should_skip_evomind_user_text(text):
        return None, "p2", 0

    score = 0
    signal_type: str | None = None

    if _contains_any(EXPLICIT_LEARNING_PATTERNS, text):
        signal_type = "explicit_learning_marker"
        score += 100
    if _contains_any(FRICTION_PATTERNS, text):
        signal_type = signal_type or "friction"
        score += 80
    if _contains_any(CORRECTION_PATTERNS, text):
        signal_type = signal_type or "repeated_correction"
        score += 55

    if "EvoMind" in text or "evomind" in text.lower():
        score += 20
    if "skill" in text.lower() or "AGENTS.md" in text or "docs" in text:
        score += 15

    if score >= 100:
        evidence_strength = "p0"
    elif score >= 70:
        evidence_strength = "p1"
    else:
        evidence_strength = "p2"

    return signal_type, evidence_strength, score


def _normalize_signal_type_filter(value: str | None) -> str | None:
    signal_type = re.sub(r"\s+", "", str(value or "")).strip()
    if not signal_type:
        return None
    if signal_type not in SCAN_SIGNAL_TYPES:
        raise ValueError(f"不支持的 EvoMind 信号类型：{signal_type}")
    return signal_type


def _should_skip_evomind_user_text(text: str) -> bool:
    compact = _compact_text(text, limit=3000)
    lowered = compact.lower()
    if compact.startswith("# AGENTS.md instructions") or "<INSTRUCTIONS>" in compact:
        return True
    if "你正在通过 CodeYun 调用本机 Codex CLI" in compact:
        return True
    if "<environment_context>" in compact or "<permissions instructions>" in compact:
        return True
    if lowered.startswith("knowledge cutoff:") or lowered.startswith("you are codex"):
        return True
    return False


def _find_previous(messages: list[dict[str, Any]], start_index: int, role: str) -> dict[str, Any] | None:
    for index in range(start_index - 1, -1, -1):
        if messages[index].get("role") == role:
            return messages[index]
    return None


def _find_next(messages: list[dict[str, Any]], start_index: int, role: str) -> dict[str, Any] | None:
    for index in range(start_index + 1, len(messages)):
        if messages[index].get("role") == role:
            return messages[index]
    return None


def _infer_patterns(signal_type: str, correction_text: str, bad_attempt_text: str) -> tuple[list[str], list[str], str]:
    combined = f"{correction_text}\n{bad_attempt_text}"
    anti_patterns: list[str] = []
    positive_patterns: list[str] = []

    if re.search(r"简洁|优雅|冗余|臃肿|复杂|重复|多余|过度", combined):
        anti_patterns.extend(
            [
                "把本可直接表达的任务包装成多层结构",
                "重复展示同一事实或解释过程",
                "为了显得完整而增加低频入口和常驻说明",
            ]
        )
        positive_patterns.extend(
            [
                "先确认用户当前依赖哪些信息做判断、下一步要执行什么动作",
                "在保留必要功能的前提下，删掉不服务当前判断的控件、概念和重复信息",
                "把解释和推导后置，主界面只保留决策依据和直接动作",
            ]
        )

    if signal_type == "explicit_learning_marker":
        anti_patterns.append("忽略用户明确要求沉淀的样例")
        positive_patterns.append("显式学习标记直接进入案例池，但仍需人审后才写入正式规则")

    if signal_type == "friction":
        anti_patterns.append("用户多次纠正后仍重复同类错误")
        positive_patterns.append("把情绪信号当作高召回痛点入口，再回溯失败模式和修正方向")

    correction_hint = _first_sentence(correction_text)
    if correction_hint and correction_hint not in positive_patterns:
        positive_patterns.append(correction_hint)

    bad_hint = _first_sentence(bad_attempt_text)
    if bad_hint and bad_hint not in anti_patterns:
        anti_patterns.append(bad_hint)

    if not anti_patterns:
        anti_patterns.append("AI 初始方案没有吸收用户在当前轮指出的偏好或边界")
    if not positive_patterns:
        positive_patterns.append("从用户纠正中提取可复用判断规则，并保留适用边界")

    inferred_rule = _build_inferred_rule(
        signal_type=signal_type,
        correction_text=correction_text,
        anti_patterns=anti_patterns,
        positive_patterns=positive_patterns,
    )
    return anti_patterns[:6], positive_patterns[:6], inferred_rule


def _build_inferred_rule(
    *,
    signal_type: str,
    correction_text: str,
    anti_patterns: list[str],
    positive_patterns: list[str],
) -> str:
    compact_correction = _compact_text(correction_text, limit=320)
    first_positive = next((item.rstrip("。") for item in positive_patterns if item.strip()), "")
    first_anti = next((item.rstrip("。") for item in anti_patterns if item.strip()), "")

    if re.search(r"简洁|优雅|冗余|臃肿|复杂|重复|多余|过度", correction_text):
        return (
            "当用户纠正界面或方案过度复杂、重复、冗余时，不要只做局部缩小或表面精简；"
            "先确认用户正在依赖哪些信息做判断、下一步要执行什么动作，再删掉不服务这个判断的控件、概念和重复信息。"
        )

    if signal_type == "explicit_learning_marker":
        return (
            "当用户明确标记某段真实操作可作为 EvoMind 样例时，先把它拆成原始任务、AI 初始问题、用户纠正和最终范式；"
            "只有能抽出具体失败模式和适用边界时，才生成 skill 或文档提案。"
        )

    if signal_type == "friction":
        return (
            "当用户出现明显高摩擦反馈时，不要记录情绪文本本身；"
            "应回溯上一轮 AI 做错了什么、用户实际要求改成什么，再把可重复的失败模式沉淀为案例。"
        )

    if first_positive and first_anti:
        return f"遇到同类任务时，先避免“{first_anti}”，再按“{first_positive}”执行。"
    if first_positive:
        return f"遇到同类任务时，优先按“{first_positive}”执行，并保留适用边界。"
    if compact_correction:
        return f"遇到同类任务时，先吸收用户这条纠正：“{_first_sentence(compact_correction, limit=180)}”，再抽取可复用做法。"
    return "遇到同类任务时，先定位 AI 初始问题和用户最终范式，再生成可验证、可审查的规则。"


def _infer_rule_title(signal_type: str, correction_text: str, inferred_rule: str) -> str:
    text = f"{correction_text}\n{inferred_rule}"
    if re.search(r"参照|对齐|一样|同屏|右栏", text) and re.search(r"密度|字号|行高|间距|排版|大|小|宽|高", text):
        return "先定参照，再统一密度"
    if re.search(r"简洁|优雅|冗余|臃肿|复杂|重复|多余|过度", text):
        return "围绕判断闭环压缩冗余"
    if signal_type == "explicit_learning_marker":
        return "显式学习先入池再人审"
    if signal_type == "friction":
        return "从高摩擦纠正回溯规则"
    if signal_type == "final_artifact_delta":
        return "用最终差异反推正向范式"
    seed = _first_sentence(inferred_rule, limit=24)
    return seed or "待提炼规则精髓"


def _build_case_candidate(
    *,
    root_dir: str,
    thread: dict[str, Any],
    messages: list[dict[str, Any]],
    user_index: int,
    signal_type: str,
    evidence_strength: str,
    score: int,
) -> dict[str, Any]:
    user_message = messages[user_index]
    user_text = _extract_user_request_text(user_message.get("text"))
    previous_user = _find_previous(messages, user_index, "user")
    previous_assistant = _find_previous(messages, user_index, "assistant")
    next_assistant = _find_next(messages, user_index, "assistant")

    bad_attempt = str(previous_assistant.get("text") or "") if previous_assistant else ""
    original_task = _extract_user_request_text(
        previous_user.get("text") if previous_user else thread.get("preview") or thread.get("title") or ""
    )
    final_pattern = str(next_assistant.get("text") if next_assistant else user_text)
    ranked_score = _rank_case_score(
        base_score=score,
        thread=thread,
        user_text=user_text,
        original_task=original_task,
        bad_attempt=bad_attempt,
        final_pattern=final_pattern,
    )
    anti_patterns, positive_patterns, inferred_rule = _infer_patterns(signal_type, user_text, bad_attempt)
    evidence_turns = _build_evidence_turns(messages, user_index)

    return {
        "id": f"real_{str(thread.get('id') or '')[:8]}_{user_message.get('seq')}",
        "title": _infer_rule_title(signal_type, user_text, inferred_rule),
        "domain": _infer_domain(thread, user_text),
        "signal_type": signal_type,
        "evidence_strength": evidence_strength,
        "friction_level": "high" if signal_type == "friction" or score >= 110 else "medium",
        "original_task": _compact_text(original_task),
        "bad_attempt": _compact_text(bad_attempt or "未定位到上一条助手回复"),
        "user_corrections": _compact_text(user_text),
        "final_pattern": _compact_text(final_pattern),
        "inferred_rule": inferred_rule,
        "anti_patterns": anti_patterns,
        "positive_patterns": positive_patterns,
        "evidence_turns": evidence_turns,
        "status": "captured",
        "source": {
            "root_dir": root_dir,
            "thread_id": thread.get("id"),
            "thread_title": thread.get("title"),
            "message_seq": user_message.get("seq"),
            "timestamp": user_message.get("timestamp"),
            "project_label": thread.get("project_label"),
            "workspace_root": thread.get("workspace_root"),
            "score": ranked_score,
            "base_score": score,
        },
    }


def _infer_domain(thread: dict[str, Any], text: str) -> str:
    haystack = f"{thread.get('title') or ''}\n{thread.get('project_label') or ''}\n{text}".lower()
    if any(keyword in haystack for keyword in ("ui", "前端", "界面", "页面", "控件", "菜单")):
        return "frontend_ui"
    if any(keyword in haystack for keyword in ("skill", "技能", "agents.md", "evomind", "提示词")):
        return "meta_learning"
    if any(keyword in haystack for keyword in ("api", "接口", "sdk")):
        return "api_design"
    if any(keyword in haystack for keyword in ("pytest", "测试", "验证")):
        return "verification"
    return str(thread.get("project_label") or "codex").strip() or "codex"


def _read_skill_metadata(skill_file: Path) -> dict[str, Any]:
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    frontmatter_match = re.match(r"^---\s*(.*?)\s*---", text, flags=re.DOTALL)
    frontmatter = frontmatter_match.group(1) if frontmatter_match else ""
    name_match = re.search(r'^name:\s*"?([^"\n]+)"?\s*$', frontmatter, flags=re.MULTILINE)
    description_match = re.search(r'^description:\s*"?([^"\n]+)"?\s*$', frontmatter, flags=re.MULTILINE)
    heading_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    name = (
        _compact_text(name_match.group(1), limit=80)
        if name_match
        else _compact_text(heading_match.group(1), limit=80)
        if heading_match
        else skill_file.parent.name
    )
    description = _compact_text(description_match.group(1), limit=220) if description_match else ""
    return {
        "name": name,
        "description": description,
        "path": str(skill_file),
        "relative_path": str(skill_file.relative_to(_skills_root_path())) if _skills_root_path() in skill_file.parents else str(skill_file.name),
        "excerpt": _compact_text(text, limit=6000),
    }


def _list_skill_candidates() -> list[dict[str, Any]]:
    root = _skills_root_path()
    if not root.exists():
        return []
    skill_files = [
        path
        for path in root.glob("*/SKILL.md")
        if path.parent.name not in {".system", "codex-primary-runtime"}
    ]
    return [_read_skill_metadata(path) for path in sorted(skill_files, key=lambda item: item.parent.name.lower())]


def _case_text(case: dict[str, Any]) -> str:
    return "\n".join(
        str(case.get(key) or "")
        for key in (
            "title",
            "domain",
            "original_task",
            "bad_attempt",
            "user_corrections",
            "final_pattern",
            "inferred_rule",
        )
    )


def _choose_skill_target(case: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    haystack = _case_text(case).lower()
    preferred_names: list[str] = []

    if any(keyword in haystack for keyword in ("frontend", "ui", "页面", "界面", "按钮", "菜单", "样式", "排版", "表格", "列宽")):
        preferred_names.extend(["前端UI规范", "设计品味"])
    if any(keyword in haystack for keyword in ("api", "接口", "sdk", "参数", "方法")):
        preferred_names.extend(["API架构师"])
    if any(keyword in haystack for keyword in ("pytest", "测试", "验证", "回放")):
        preferred_names.extend(["Pytest规范", "服务验证"])
    if any(keyword in haystack for keyword in ("python", ".py", "分层", "业务层", "功能层", "pyxllib")):
        preferred_names.extend(["pyxllib 分层", "优化Python代码"])
    if any(keyword in haystack for keyword in ("文档", "docs", "agents.md", "架构", "设计")):
        preferred_names.extend(["技术设计文档", "给AI生成文档"])
    if any(keyword in haystack for keyword in ("简洁", "优雅", "冗余", "臃肿", "品味")):
        preferred_names.extend(["设计品味", "前端UI规范"])

    by_name = {str(item.get("name") or item.get("relative_path")): item for item in candidates}
    for name in preferred_names:
        if name in by_name:
            return {**by_name[name], "status": "existing"}

    if candidates:
        fallback = by_name.get("设计品味") or candidates[0]
        return {**fallback, "status": "existing"}

    skill_name = "EvoMind沉淀规则"
    return {
        "name": skill_name,
        "description": "由 EvoMind 从真实案例沉淀的用户偏好和工作流规则。",
        "path": str(_skills_root_path() / skill_name / "SKILL.md"),
        "relative_path": f"{skill_name}/SKILL.md",
        "excerpt": "",
        "status": "new",
    }


def _target_context(target: str, case: dict[str, Any]) -> dict[str, Any]:
    target_kind = (target or "skill").strip().lower()
    candidates = _list_skill_candidates()
    if target_kind == "agents":
        return {
            "type": "agents",
            "name": "项目 AGENTS.md",
            "description": "当前 codeyun 仓库的项目级协作规则。",
            "path": str(REPO_ROOT / "AGENTS.md"),
            "relative_path": "AGENTS.md",
            "excerpt": _compact_text((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="replace") if (REPO_ROOT / "AGENTS.md").exists() else "", limit=6000),
            "status": "existing" if (REPO_ROOT / "AGENTS.md").exists() else "new",
            "candidates": candidates[:12],
        }
    if target_kind == "docs":
        doc_path = REPO_ROOT / "docs" / "EvoMind自发觉自迭代机制设计.md"
        return {
            "type": "docs",
            "name": "EvoMind 设计文档",
            "description": "EvoMind 自发觉自迭代机制的项目文档。",
            "path": str(doc_path),
            "relative_path": "docs/EvoMind自发觉自迭代机制设计.md",
            "excerpt": _compact_text(doc_path.read_text(encoding="utf-8", errors="replace") if doc_path.exists() else "", limit=6000),
            "status": "existing" if doc_path.exists() else "new",
            "candidates": candidates[:12],
        }
    chosen = _choose_skill_target(case, candidates)
    chosen["type"] = "skill"
    chosen["candidates"] = candidates[:12]
    return chosen


def _normalize_case_for_proposal(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _compact_text(case.get("id") or "case", limit=80),
        "title": _compact_text(case.get("title") or "未命名案例", limit=120),
        "domain": _compact_text(case.get("domain") or "codex", limit=80),
        "signal_type": _compact_text(case.get("signal_type") or case.get("signalType") or "repeated_correction", limit=60),
        "evidence_strength": _compact_text(case.get("evidence_strength") or case.get("evidenceStrength") or "p2", limit=12),
        "friction_level": _compact_text(case.get("friction_level") or case.get("frictionLevel") or "medium", limit=20),
        "original_task": _compact_text(case.get("original_task") or case.get("originalTask") or ""),
        "bad_attempt": _compact_text(case.get("bad_attempt") or case.get("badAttempt") or ""),
        "user_corrections": _compact_text(case.get("user_corrections") or case.get("userCorrections") or ""),
        "final_pattern": _compact_text(case.get("final_pattern") or case.get("finalPattern") or ""),
        "inferred_rule": _compact_text(case.get("inferred_rule") or case.get("inferredRule") or "", limit=320),
        "anti_patterns": _safe_string_list(case.get("anti_patterns") or case.get("antiPatterns") or []),
        "positive_patterns": _safe_string_list(case.get("positive_patterns") or case.get("positivePatterns") or []),
        "evidence_turns": _safe_evidence_turns(case.get("evidence_turns") or case.get("evidenceTurns") or []),
        "source": case.get("source") if isinstance(case.get("source"), dict) else {},
    }


def _normalize_case_card_payload(
    raw: dict[str, Any],
    *,
    case: dict[str, Any],
    generation_mode: str,
) -> dict[str, Any]:
    anti_patterns = _safe_string_list(raw.get("anti_patterns"), limit=8)
    positive_patterns = _safe_string_list(raw.get("positive_patterns"), limit=8)
    inferred_rule = _compact_text(raw.get("inferred_rule") or "", limit=700)
    if not anti_patterns or not positive_patterns or not inferred_rule:
        raise ValueError("DeepSeek 案例卡结果缺少反面模式、正向范式或一句话规则")

    return {
        "id": _compact_text(raw.get("id") or case.get("id") or "case", limit=120),
        "title": _compact_text(raw.get("title") or case.get("title") or "未命名案例", limit=120),
        "domain": _compact_text(raw.get("domain") or case.get("domain") or "codex", limit=80),
        "signal_type": _compact_text(raw.get("signal_type") or case.get("signal_type") or "repeated_correction", limit=60),
        "evidence_strength": _compact_text(raw.get("evidence_strength") or case.get("evidence_strength") or "p2", limit=12),
        "friction_level": _compact_text(raw.get("friction_level") or case.get("friction_level") or "medium", limit=20),
        "original_task": _compact_text(raw.get("original_task") or case.get("original_task") or ""),
        "bad_attempt": _compact_text(raw.get("bad_attempt") or case.get("bad_attempt") or ""),
        "user_corrections": _compact_text(raw.get("user_corrections") or case.get("user_corrections") or ""),
        "final_pattern": _compact_text(raw.get("final_pattern") or case.get("final_pattern") or ""),
        "inferred_rule": inferred_rule,
        "anti_patterns": anti_patterns,
        "positive_patterns": positive_patterns,
        "evidence_turns": _safe_evidence_turns(raw.get("evidence_turns") or case.get("evidence_turns") or []),
        "status": _compact_text(raw.get("status") or "captured", limit=40),
        "generation_mode": generation_mode,
    }


def _build_codex_cli_case_card_prompt(
    *,
    case: dict[str, Any],
    case_rule_text: str | None = None,
) -> str:
    payload = {
        "case": case,
        "case_rule_text": _compact_text(case_rule_text or "", limit=12000),
    }
    return (
        DEEPSEEK_CASE_CARD_INSTRUCTIONS
        + "\n"
        "返回 JSON 格式：\n"
        "{\n"
        '  "id": "案例 id",\n'
        '  "title": "规则精髓标题，不是素材标题",\n'
        '  "domain": "frontend_ui|api_design|verification|meta_learning|codex",\n'
        '  "signal_type": "explicit_learning_marker|friction|repeated_correction|final_artifact_delta",\n'
        '  "evidence_strength": "p0|p1|p2",\n'
        '  "friction_level": "high|medium|low",\n'
        '  "original_task": "原始业务任务",\n'
        '  "bad_attempt": "AI 初始问题/反面教材",\n'
        '  "user_corrections": "用户关键纠正",\n'
        '  "final_pattern": "最终正向范式",\n'
        '  "inferred_rule": "一句完整规则，包含场景、判断、动作和避免事项",\n'
        '  "anti_patterns": ["3-6 条反面模式，完整句子，不要聊天原话"],\n'
        '  "positive_patterns": ["3-6 条正向范式，完整句子，不要聊天原话"]\n'
        "}\n\n"
        "反面模式示例：不要写“好像还是大啊”，应改成“把用户要求的整体密度优化误解为只调局部字号”。\n"
        "正向范式示例：不要写“先识别用户真正参考什么”，应改成“先找出用户用于判断问题的同屏参照，再把目标区域的字号、间距和分组密度对齐到该参照”。\n\n"
        "标题示例：不要写“敏感信息右栏对齐新增文件密度”，应改成“先定参照，再统一密度”。\n\n"
        f"输入 JSON：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _derive_case_card_with_codex_cli(
    *,
    case: dict[str, Any],
    case_rule_text: str | None = None,
    timeout_seconds: int = EVOMIND_DEEPSEEK_CASE_CARD_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    prompt = _build_codex_cli_case_card_prompt(case=case, case_rule_text=case_rule_text)
    try:
        raw = _call_deepseek_json(
            prompt,
            system_prompt="你是 EvoMind 的案例卡结构化器，只返回 JSON。",
            timeout_seconds=timeout_seconds,
        )
        return _normalize_case_card_payload(raw, case=case, generation_mode="deepseek")
    except OllamaClientError as exc:
        raise RuntimeError(f"DeepSeek 案例卡生成失败：{exc}") from exc


def derive_evomind_case_card(
    *,
    case: dict[str, Any],
    case_rule_text: str | None = None,
) -> dict[str, Any]:
    """Use DeepSeek to turn case material into a readable, reviewable card."""

    normalized_case = _normalize_case_for_proposal(case)
    return _derive_case_card_with_codex_cli(case=normalized_case, case_rule_text=case_rule_text)


def _proposal_created_at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _proposal_id(case: dict[str, Any], target: dict[str, Any]) -> str:
    seed = _stable_json_hash(
        {
            "case_id": case.get("id"),
            "target": target.get("path"),
            "rule": case.get("inferred_rule"),
            "created_at": time.time(),
        }
    )
    return f"proposal_{str(case.get('id') or 'case')[:24]}_{seed[:8]}"


def _build_proposal_content(proposal: dict[str, Any], case: dict[str, Any]) -> str:
    evidence_lines = [
        f"- 信号：{case.get('signal_type') or 'unknown'}",
        f"- 强度：{str(case.get('evidence_strength') or 'p2').upper()}",
        f"- 用户纠正：{case.get('user_corrections') or '暂无'}",
    ]
    anti_lines = [f"- {item}" for item in proposal.get("anti_patterns", []) if str(item).strip()] or ["- 待补充"]
    positive_lines = [f"- {item}" for item in proposal.get("positive_patterns", []) if str(item).strip()] or ["- 待补充"]
    verification_lines = [f"- {item}" for item in proposal.get("verification_plan", []) if str(item).strip()] or ["- 使用历史原始请求做 baseline / candidate 回放。"]
    return "\n".join(
        [
            f"# {proposal.get('title') or case.get('title')}",
            "",
            f"目标文件：{proposal.get('target_path')}",
            f"目标状态：{proposal.get('target_status')}",
            "",
            "## 触发条件",
            str(proposal.get("trigger") or "遇到同类任务时先检查该案例暴露的失败模式。"),
            "",
            "## 证据",
            *evidence_lines,
            "",
            "## 反面模式",
            *anti_lines,
            "",
            "## 正向范式",
            *positive_lines,
            "",
            "## 建议规则",
            str(proposal.get("rule_text") or "待补充"),
            "",
            "## 适用范围",
            str(proposal.get("scope") or "当前案例相同或高度相似的任务。"),
            "",
            "## 排除范围",
            str(proposal.get("anti_scope") or "缺少具体操作对象、只有抽象观点的场景不直接套用。"),
            "",
            "## 风险",
            str(proposal.get("risk") or "可能把单次偏好过度泛化；需要人审和回放验证。"),
            "",
            "## 验证计划",
            *verification_lines,
        ]
    )


def _normalize_proposal_payload(
    raw: dict[str, Any],
    *,
    case: dict[str, Any],
    target: dict[str, Any],
    generation_mode: str,
    warning: str = "",
) -> dict[str, Any]:
    anti_patterns = _safe_string_list(raw.get("anti_patterns") or case.get("anti_patterns") or [], limit=8)
    positive_patterns = _safe_string_list(raw.get("positive_patterns") or case.get("positive_patterns") or [], limit=8)
    verification_plan = _safe_string_list(raw.get("verification_plan") or [], limit=8)
    if not verification_plan:
        verification_plan = [
            "使用历史原始请求做 baseline / candidate 回放。",
            "candidate 必须避开反面模式，并更接近最终范式。",
            "人工确认后才能写入正式规则文件。",
        ]
    proposal = {
        "id": _compact_text(raw.get("id") or _proposal_id(case, target), limit=120),
        "source_case_id": case.get("id") or "",
        "target_type": _compact_text(raw.get("target_type") or target.get("type") or "skill", limit=40),
        "target": _compact_text(raw.get("target") or target.get("name") or target.get("relative_path") or "skill", limit=160),
        "target_path": _compact_text(raw.get("target_path") or target.get("path") or "", limit=300),
        "target_status": _compact_text(raw.get("target_status") or target.get("status") or "existing", limit=40),
        "lifecycle": _compact_text(raw.get("lifecycle") or "candidate", limit=40),
        "title": _compact_text(raw.get("title") or f"沉淀规则：{case.get('title')}", limit=160),
        "trigger": _compact_text(raw.get("trigger") or "遇到同类任务时先检查案例中的反面模式和正向范式。", limit=400),
        "rule_text": _compact_text(raw.get("rule_text") or case.get("inferred_rule") or "待补充", limit=900),
        "scope": _compact_text(raw.get("scope") or f"{case.get('domain') or '当前领域'} 中与该案例相似的任务。", limit=500),
        "anti_scope": _compact_text(raw.get("anti_scope") or "只有抽象观点、缺少真实操作对象和前后对比的场景不直接套用。", limit=500),
        "risk": _compact_text(raw.get("risk") or "可能把单次偏好过度泛化；需要通过历史回放和人审确认适用边界。", limit=600),
        "anti_patterns": anti_patterns,
        "positive_patterns": positive_patterns,
        "verification_plan": verification_plan,
        "created_at": _compact_text(raw.get("created_at") or _proposal_created_at(), limit=80),
        "generation_mode": generation_mode,
        "warning": _compact_text(warning, limit=500),
    }
    proposal["content"] = _compact_text(raw.get("content") or _build_proposal_content(proposal, case), limit=12000)
    return proposal


def _build_heuristic_rule_text(case: dict[str, Any]) -> str:
    if case.get("inferred_rule"):
        return str(case["inferred_rule"])
    positive = [item for item in case.get("positive_patterns", []) if str(item).strip()]
    correction = str(case.get("user_corrections") or "").strip()
    if positive:
        return f"遇到同类任务时，先按正向范式执行：{positive[0]}"
    if correction:
        return f"遇到同类任务时，先吸收用户纠正：{_first_sentence(correction, limit=180)}"
    return "遇到同类任务时，先检查历史案例中的反面模式，再按最终范式组织输出或实现。"


def _generate_heuristic_proposal(
    *,
    case: dict[str, Any],
    target: dict[str, Any],
    generation_mode: str = "heuristic",
    warning: str = "",
) -> dict[str, Any]:
    raw = {
        "target": target.get("name") or target.get("relative_path"),
        "target_path": target.get("path"),
        "target_status": target.get("status"),
        "title": f"沉淀规则：{case.get('title')}",
        "trigger": f"当任务属于 {case.get('domain') or '当前领域'}，且出现与案例相似的失败模式、用户纠正或最终范式时触发。",
        "rule_text": _build_heuristic_rule_text(case),
        "scope": f"{case.get('domain') or '当前领域'} 中存在明确操作对象、AI 初始问题和用户纠正链路的同类任务。",
        "anti_scope": "纯语言讨论、只有抽象偏好、缺少 AI 初始问题和最终范式的场景，不应直接升级为正式 skill 规则。",
        "risk": "候选规则来自单个案例时可能过度泛化；需要至少用原始历史请求做一次 baseline / candidate 回放。",
        "anti_patterns": case.get("anti_patterns") or [],
        "positive_patterns": case.get("positive_patterns") or [],
        "verification_plan": [
            "用原始请求回放不加载规则的 baseline。",
            "加载候选规则后回放 candidate。",
            "比较 candidate 是否更接近用户最终范式，并确认没有新增冗余规则。",
        ],
    }
    return _normalize_proposal_payload(raw, case=case, target=target, generation_mode=generation_mode, warning=warning)


def _build_codex_cli_proposal_prompt(
    *,
    case: dict[str, Any],
    target: dict[str, Any],
    proposal_rule_text: str | None,
) -> str:
    candidates = [
        {
            "name": item.get("name"),
            "description": item.get("description"),
            "relative_path": item.get("relative_path"),
        }
        for item in target.get("candidates", [])
    ]
    payload = {
        "case": case,
        "target": {
            "type": target.get("type"),
            "name": target.get("name"),
            "description": target.get("description"),
            "path": target.get("path"),
            "relative_path": target.get("relative_path"),
            "status": target.get("status"),
            "excerpt": target.get("excerpt"),
        },
        "skill_candidates": candidates,
        "proposal_rule_text": _compact_text(proposal_rule_text or "", limit=12000),
    }
    return (
        DEEPSEEK_PROPOSAL_INSTRUCTIONS
        + "\n"
        "只返回 JSON，字段：\n"
        "{\n"
        '  "title": "提案标题",\n'
        '  "target": "目标 skill 或文件名称",\n'
        '  "target_type": "skill|agents|docs",\n'
        '  "target_path": "目标文件路径",\n'
        '  "target_status": "existing|new",\n'
        '  "trigger": "触发条件",\n'
        '  "rule_text": "建议写入的规则正文",\n'
        '  "scope": "适用范围",\n'
        '  "anti_scope": "排除范围",\n'
        '  "risk": "风险",\n'
        '  "anti_patterns": ["反面模式"],\n'
        '  "positive_patterns": ["正向范式"],\n'
        '  "verification_plan": ["验证步骤"],\n'
        '  "content": "完整 Markdown 提案"\n'
        "}\n\n"
        f"输入 JSON：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _generate_proposal_with_codex_cli(
    *,
    case: dict[str, Any],
    target: dict[str, Any],
    proposal_rule_text: str | None = None,
    timeout_seconds: int = EVOMIND_DEEPSEEK_PROPOSAL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    prompt = _build_codex_cli_proposal_prompt(case=case, target=target, proposal_rule_text=proposal_rule_text)
    try:
        raw = _call_deepseek_json(
            prompt,
            system_prompt="你是 EvoMind 的 skill 优化提案生成器，只返回 JSON。",
            timeout_seconds=timeout_seconds,
        )
        return _normalize_proposal_payload(raw, case=case, target=target, generation_mode="deepseek")
    except OllamaClientError as exc:
        raise RuntimeError(f"DeepSeek 提案生成失败：{exc}") from exc


def generate_evomind_rule_proposal(
    *,
    case: dict[str, Any],
    target: str = "skill",
    use_codex_cli: bool = True,
    proposal_rule_text: str | None = None,
) -> dict[str, Any]:
    """Create a reviewable rule proposal from a captured EvoMind case.

    This function deliberately stops at proposal generation. It does not write
    to skills, AGENTS.md, or docs; activation is a separate, human-reviewed step.
    """

    normalized_case = _normalize_case_for_proposal(case)
    target_context = _target_context(target, normalized_case)
    if use_codex_cli:
        try:
            return _generate_proposal_with_codex_cli(
                case=normalized_case,
                target=target_context,
                proposal_rule_text=proposal_rule_text,
            )
        except Exception as exc:
            return _generate_heuristic_proposal(
                case=normalized_case,
                target=target_context,
                generation_mode="heuristic_fallback",
                warning=f"DeepSeek 未完成，已退回本地提案：{exc}",
            )
    return _generate_heuristic_proposal(case=normalized_case, target=target_context)


def _call_deepseek_json(
    prompt: str,
    *,
    system_prompt: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    response = chat_with_provider(
        provider_id=EVOMIND_DEEPSEEK_PROVIDER_ID,
        model=EVOMIND_DEEPSEEK_MODEL,
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system_prompt,
        response_format="json",
        temperature=0.2,
        timeout_seconds=timeout_seconds,
    )
    return _extract_json_object(str(response.get("content") or ""))


def _extract_json_object(text: str) -> dict[str, Any]:
    compact = text.strip()
    if compact.startswith("```"):
        compact = re.sub(r"^```(?:json)?\s*", "", compact, flags=re.IGNORECASE)
        compact = re.sub(r"\s*```$", "", compact)
    try:
        parsed = json.loads(compact)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = compact.find("{")
    end = compact.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(compact[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("DeepSeek 未返回可解析的 JSON 对象")


def _build_codex_cli_scan_prompt(
    candidates: list[dict[str, Any]],
    *,
    max_cases: int,
    scan_rule_text: str | None = None,
) -> str:
    payload = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "domain": item.get("domain"),
            "signal_type": item.get("signal_type"),
            "evidence_strength": item.get("evidence_strength"),
            "friction_level": item.get("friction_level"),
            "original_task": item.get("original_task"),
            "bad_attempt": item.get("bad_attempt"),
            "user_corrections": item.get("user_corrections"),
            "final_pattern": item.get("final_pattern"),
            "heuristic_rule": item.get("inferred_rule"),
            "heuristic_anti_patterns": item.get("anti_patterns"),
            "heuristic_positive_patterns": item.get("positive_patterns"),
            "evidence_turns": item.get("evidence_turns"),
            "source": item.get("source"),
        }
        for item in candidates
    ]
    custom_rule_block = ""
    if scan_rule_text and scan_rule_text.strip():
        custom_rule_block = (
            "\n当前管理界面启用的案例捕捉规则：\n"
            f"{_compact_text(scan_rule_text, limit=12000)}\n"
        )

    return (
        DEEPSEEK_SCAN_INSTRUCTIONS
        + custom_rule_block
        + "\n"
        "只返回 JSON，不要 markdown，不要解释。格式：\n"
        "{\n"
        '  "items": [\n'
        "    {\n"
        '      "id": "原候选 id",\n'
        '      "keep": true,\n'
        '      "reject_reason": "",\n'
        '      "quality_score": 0,\n'
        '      "title": "规则精髓标题，不是素材标题",\n'
        '      "domain": "frontend_ui|api_design|verification|meta_learning|codex",\n'
        '      "signal_type": "explicit_learning_marker|friction|repeated_correction|final_artifact_delta",\n'
        '      "evidence_strength": "p0|p1|p2",\n'
        '      "friction_level": "high|medium|low",\n'
        '      "original_task": "原始业务任务",\n'
        '      "bad_attempt": "AI 初始问题/反面教材",\n'
        '      "user_corrections": "用户关键纠正",\n'
        '      "final_pattern": "最终正向范式",\n'
        '      "inferred_rule": "可沉淀规则",\n'
        '      "anti_patterns": ["反面模式"],\n'
        '      "positive_patterns": ["正向模式"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"必须为输入的每个候选都返回判定。keep=true 的案例按 quality_score 从高到低排序，最多 {max_cases} 条；keep=false 的候选也要返回 id、keep=false、reject_reason。\n"
        f"候选 JSON：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _merge_codex_cli_items(
    *,
    heuristic_candidates: list[dict[str, Any]],
    codex_payload: dict[str, Any],
    max_cases: int,
) -> list[dict[str, Any]]:
    decisions = _merge_codex_cli_decisions(
        heuristic_candidates=heuristic_candidates,
        codex_payload=codex_payload,
    )
    return [item for item in decisions.values() if item is not None][:max_cases]


def _merge_codex_cli_decisions(
    *,
    heuristic_candidates: list[dict[str, Any]],
    codex_payload: dict[str, Any],
) -> dict[str, dict[str, Any] | None]:
    by_id = {str(item.get("id") or ""): item for item in heuristic_candidates}
    decisions: dict[str, dict[str, Any] | None] = {}
    raw_items = codex_payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("DeepSeek JSON 缺少 items 数组")

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or "")
        base = by_id.get(item_id)
        if not base:
            continue
        if raw_item.get("keep") is False:
            decisions[item_id] = None
            continue

        item = dict(base)
        source = dict(item.get("source") or {})
        quality_score = max(0, min(100, int(raw_item.get("quality_score") or 0)))
        source["deepseek_quality_score"] = quality_score
        source["score"] = max(int(source.get("score") or 0), quality_score + 140)
        item["source"] = source

        for key, limit in (
            ("title", 80),
            ("domain", 40),
            ("signal_type", 40),
            ("evidence_strength", 8),
            ("friction_level", 16),
            ("original_task", SOURCE_TEXT_LIMIT),
            ("bad_attempt", SOURCE_TEXT_LIMIT),
            ("user_corrections", SOURCE_TEXT_LIMIT),
            ("final_pattern", SOURCE_TEXT_LIMIT),
            ("inferred_rule", 240),
        ):
            if raw_item.get(key):
                item[key] = _compact_text(raw_item.get(key), limit=limit)

        anti_patterns = _safe_string_list(raw_item.get("anti_patterns"))
        positive_patterns = _safe_string_list(raw_item.get("positive_patterns"))
        if anti_patterns:
            item["anti_patterns"] = anti_patterns
        if positive_patterns:
            item["positive_patterns"] = positive_patterns
        item["status"] = "captured"
        decisions[item_id] = item

    return decisions


def _refine_candidates_with_codex_cli(
    candidates: list[dict[str, Any]],
    *,
    max_cases: int,
    candidate_limit: int,
    timeout_seconds: int = EVOMIND_DEEPSEEK_SCAN_TIMEOUT_SECONDS,
    reset_cache: bool = False,
    scan_rule_text: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not candidates:
        return [], {
            "cache_hit_count": 0,
            "cache_miss_count": 0,
            "codex_cli_invoked": False,
            "rule_hash": _scanner_rule_hash(scan_rule_text),
            "cache_rule_mismatch": False,
            "cache_reset": reset_cache,
        }

    limited_candidates = candidates[: max(1, min(candidate_limit, len(candidates)))]
    rule_hash = _scanner_rule_hash(scan_rule_text)
    cache, cache_rule_mismatch = _load_scan_cache(rule_hash, reset_cache=reset_cache)
    cache_items = cache.setdefault("items", {})
    if not isinstance(cache_items, dict):
        cache_items = {}
        cache["items"] = cache_items

    candidate_by_id = {str(item.get("id") or ""): item for item in limited_candidates}
    cached_results: dict[str, dict[str, Any] | None] = {}
    uncached_candidates: list[dict[str, Any]] = []
    cache_hit_count = 0

    for candidate in limited_candidates:
        cache_key = _candidate_cache_key(candidate)
        cached = cache_items.get(cache_key)
        if isinstance(cached, dict) and cached.get("rule_hash") == rule_hash:
            cache_hit_count += 1
            item = cached.get("item")
            cached_results[str(candidate.get("id") or "")] = item if isinstance(item, dict) else None
        else:
            uncached_candidates.append(candidate)

    semantic_ai_invoked = False
    if uncached_candidates:
        prompt = _build_codex_cli_scan_prompt(
            uncached_candidates,
            max_cases=max_cases,
            scan_rule_text=scan_rule_text,
        )
        try:
            codex_payload = _call_deepseek_json(
                prompt,
                system_prompt="你是 EvoMind 的真实案例扫描器，只返回 JSON。",
                timeout_seconds=timeout_seconds,
            )
            fresh_decisions = _merge_codex_cli_decisions(
                heuristic_candidates=uncached_candidates,
                codex_payload=codex_payload,
            )
            for candidate in uncached_candidates:
                item_id = str(candidate.get("id") or "")
                item = fresh_decisions.get(item_id)
                cached_results[item_id] = item if isinstance(item, dict) else None
                cache_items[_candidate_cache_key(candidate)] = {
                    "rule_hash": rule_hash,
                    "candidate_id": item_id,
                    "keep": item is not None,
                    "item": item,
                    "cached_at": time.time(),
                }
            _write_scan_cache(rule_hash, cache)
            semantic_ai_invoked = True
        except OllamaClientError as exc:
            raise RuntimeError(f"DeepSeek 扫描失败：{exc}") from exc

    ordered_items = [
        cached_results.get(str(candidate.get("id") or ""))
        for candidate in limited_candidates
        if cached_results.get(str(candidate.get("id") or "")) is not None
    ][:max_cases]
    stats = {
        "cache_hit_count": cache_hit_count,
        "cache_miss_count": len(uncached_candidates),
        "codex_cli_invoked": semantic_ai_invoked,
        "deepseek_invoked": semantic_ai_invoked,
        "rule_hash": rule_hash,
        "cache_rule_mismatch": cache_rule_mismatch,
        "cache_reset": reset_cache,
    }
    return ordered_items, stats


def scan_evomind_cases_from_codex(
    *,
    session: Session,
    root_dir: str | None = None,
    max_threads: int = 120,
    max_cases: int = 40,
    min_score: int = 55,
    signal_type_filter: str | None = None,
    use_codex_cli: bool = False,
    codex_cli_limit: int = EVOMIND_DEEPSEEK_DEFAULT_CANDIDATE_LIMIT,
    reset_cache: bool = False,
    scan_rule_text: str | None = None,
) -> dict[str, Any]:
    """Read local Codex threads and extract high-value EvoMind case candidates.

    The first pass is intentionally heuristic and read-only. When
    ``use_codex_cli`` is true, the top candidates are passed to DeepSeek for
    semantic judgment and structured case extraction. The parameter name is
    kept for frontend compatibility.
    """

    effective_max_threads = max(1, min(int(max_threads or 120), 500))
    effective_max_cases = max(1, min(int(max_cases or 40), 120))
    effective_min_score = max(0, int(min_score or 0))
    effective_codex_cli_limit = max(1, min(int(codex_cli_limit or EVOMIND_DEEPSEEK_DEFAULT_CANDIDATE_LIMIT), 120))
    effective_signal_type_filter = _normalize_signal_type_filter(signal_type_filter)

    overview = build_codex_overview(
        root_dir,
        session=session,
        thread_offset=0,
        thread_limit=effective_max_threads,
    )
    root_dir_text = str(overview.get("root_dir") or "")
    threads = [
        thread
        for group in overview.get("groups", [])
        for thread in group.get("threads", [])
        if not thread.get("archived")
    ]

    candidate_collect_limit = min(max(effective_max_cases * 8, 80), 400)
    candidates: list[dict[str, Any]] = []
    scanned_threads = 0
    scanned_messages = 0
    skipped_threads = 0
    seen_signatures: set[str] = set()

    for thread in threads:
        if len(candidates) >= candidate_collect_limit:
            break
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            continue
        try:
            detail = build_codex_thread_detail(root_dir_text, thread_id, session=session)
        except Exception:
            skipped_threads += 1
            continue

        scanned_threads += 1
        messages = list(detail.get("messages") or [])
        for index, message in enumerate(messages):
            if message.get("role") != "user":
                continue
            scanned_messages += 1
            raw_text = str(message.get("text") or "")
            text = _extract_user_request_text(raw_text)
            signal_type, evidence_strength, score = _detect_signal(text)
            if not signal_type or score < effective_min_score:
                continue
            if effective_signal_type_filter and signal_type != effective_signal_type_filter:
                continue
            signature = re.sub(r"\s+", " ", text).strip().lower()[:500]
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            candidate = _build_case_candidate(
                root_dir=root_dir_text,
                thread=detail.get("thread") or thread,
                messages=messages,
                user_index=index,
                signal_type=signal_type,
                evidence_strength=evidence_strength,
                score=score,
            )
            if int(candidate.get("source", {}).get("score") or 0) < effective_min_score:
                continue
            candidates.append(candidate)
            if len(candidates) >= candidate_collect_limit:
                break

    candidates.sort(
        key=lambda item: (
            int(item.get("source", {}).get("score") or 0),
            str(item.get("source", {}).get("timestamp") or ""),
        ),
        reverse=True,
    )
    heuristic_candidate_count = len(candidates)
    codex_cli_used = False
    codex_cli_invoked = False
    cache_hit_count = 0
    cache_miss_count = 0
    rule_hash = _scanner_rule_hash(scan_rule_text)
    cache_rule_mismatch = False
    cache_reset = reset_cache
    final_items = candidates[:effective_max_cases]

    if use_codex_cli and candidates:
        final_items, refine_stats = _refine_candidates_with_codex_cli(
            candidates,
            max_cases=effective_max_cases,
            candidate_limit=effective_codex_cli_limit,
            reset_cache=reset_cache,
            scan_rule_text=scan_rule_text,
        )
        codex_cli_used = True
        codex_cli_invoked = bool(refine_stats.get("codex_cli_invoked"))
        cache_hit_count = int(refine_stats.get("cache_hit_count") or 0)
        cache_miss_count = int(refine_stats.get("cache_miss_count") or 0)
        rule_hash = str(refine_stats.get("rule_hash") or rule_hash)
        cache_rule_mismatch = bool(refine_stats.get("cache_rule_mismatch"))
        cache_reset = bool(refine_stats.get("cache_reset"))

    return {
        "root_dir": root_dir_text,
        "total_threads": int(overview.get("total_threads") or 0),
        "scanned_threads": scanned_threads,
        "skipped_threads": skipped_threads,
        "scanned_messages": scanned_messages,
        "heuristic_candidate_count": heuristic_candidate_count,
        "analysis_mode": "deepseek" if codex_cli_invoked else "deepseek_cache" if codex_cli_used else "heuristic",
        "codex_cli_used": codex_cli_used,
        "codex_cli_invoked": codex_cli_invoked,
        "cache_hit_count": cache_hit_count,
        "cache_miss_count": cache_miss_count,
        "cache_rule_hash": rule_hash,
        "cache_rule_mismatch": cache_rule_mismatch,
        "cache_reset": cache_reset,
        "items": final_items,
    }
