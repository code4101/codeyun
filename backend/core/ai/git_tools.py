from __future__ import annotations

import re
import shutil
import subprocess
from collections import defaultdict
from datetime import date, timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from backend.core.runtime.process_launcher import run_quiet


LOCKFILE_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
}

GENERATED_PATH_PARTS = {
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    "node_modules",
    "__pycache__",
}

TEXT_LIKE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

BINARY_LIKE_SUFFIXES = {
    ".7z",
    ".avi",
    ".bin",
    ".db",
    ".dll",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".rar",
    ".so",
    ".tar",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}

CLEAR_IGNORE_DIR_PARTS = {
    ".codex",
    ".codex_tmp",
    ".codex-dev-logs",
    ".codex-logs",
    ".codex-run",
    ".codex-run-logs",
    ".codex-runlogs",
    ".codex-runtime-logs",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".turbo",
    ".next",
    ".nuxt",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".codeyun-state",
    "logs",
    "log",
    "tmp",
    "temp",
}

CLEAR_IGNORE_DIR_PREFIXES = (
    ".tmp",
    ".temp",
    "tmp-",
    "tmp_",
    "temp-",
    "temp_",
)

CLEAR_IGNORE_FILENAMES = {
    ".ds_store",
    "@automationlog.txt",
    "thumbs.db",
}

CLEAR_IGNORE_SUFFIXES = {
    ".cache",
    ".log",
    ".pid",
    ".pyc",
    ".pyo",
    ".temp",
    ".tmp",
}

WARNING_DATA_SUFFIXES = {
    ".7z",
    ".bak",
    ".bin",
    ".csv",
    ".db",
    ".feather",
    ".gz",
    ".joblib",
    ".npy",
    ".npz",
    ".parquet",
    ".pkl",
    ".rar",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tsv",
    ".xls",
    ".xlsx",
    ".zip",
}

MAX_CONTEXT_CHARS = 18_000
MAX_FILE_SECTION_CHARS = 4_000
MAX_FILE_PREVIEW_CHARS = 24_000
MAX_REDUCTION_UNIT_CHARS = 2_400
MAX_PREVIEW_BYTES = 8_192
MAX_PREVIEW_LINES = 120
MAX_SENSITIVE_SCAN_BYTES = 256 * 1024
MAX_SENSITIVE_ISSUES_PER_FILE = 5
PRECHECK_CONTEXT_RADIUS = 2
PRECHECK_MAX_PREVIEW_LINE_LENGTH = 240
SPLIT_RECOMMENDED_FILE_THRESHOLD = 40
SPLIT_RECOMMENDED_LINE_THRESHOLD = 8_000
OVERSIZED_FILE_THRESHOLD = 80
OVERSIZED_LINE_THRESHOLD = 20_000
MAX_SPLIT_GROUPS = 6
MAX_GROUP_SAMPLE_PATHS = 3
MAX_OVERVIEW_PATHS = 20
UNTRACKED_LINE_COUNT_CAP = OVERSIZED_LINE_THRESHOLD + 1_000
DEFAULT_HISTORY_WINDOW_DAYS = 180
MIN_HISTORY_WINDOW_DAYS = 7
MAX_HISTORY_WINDOW_DAYS = 365 * 5
ALL_HISTORY_WINDOW_DAYS = 0
GIT_HISTORY_LOG_TIMEOUT = 60
GIT_HISTORY_MARKER = "__CODEYUN_HISTORY__"
LARGE_FILE_WARNING_BYTES = 1_000_000
LOCAL_ARTIFACT_ROOT_DIRS = {
    ".codex",
    ".codex_tmp",
    ".codex-dev-logs",
    ".codex-logs",
    ".codex-run",
    ".codex-run-logs",
    ".codex-runlogs",
    ".codex-runtime-logs",
    "tmp",
    "tmp_mask_debug",
}
LOCAL_ARTIFACT_ROOT_PREFIXES = (
    "tmp-",
    "tmp_",
)
LOCAL_ARTIFACT_ROOT_FILE_PATTERNS = (
    ".codex-dev-*.log",
    ".codex-vite-*.log",
    ".dev_*.log",
    "*.log",
    "tmp_*.png",
    "tmp_*.jpg",
    "tmp_*.jpeg",
    "tmp_*.json",
    "tmp_*.txt",
    "bc_*.db",
    "@automationlog.txt",
)
COMMIT_BODY_BULLET_PREFIX_RE = re.compile(r"^[-*•]\s*")
COMMIT_BODY_NUMBER_PREFIX_RE = re.compile(r"^(?:\d{1,2}[、）)]\s*|\d{1,2}[.．](?!\d)\s*)")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")
OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")
AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
JWT_TOKEN_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]{8,}\.[A-Za-z0-9._-]{8,}\b")
URL_CREDENTIAL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]{1,64}:[^@\s]{3,128}@")
AUTH_HEADER_RE = re.compile(r"\bAuthorization\b\s*[:=]\s*[\"']?(?:Bearer|Basic)\s+[A-Za-z0-9._\-+/=]{12,}")
GENERIC_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<lhs>['\"\w.\-\[\]]*"
    r"(?P<keyword>password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|authorization)"
    r"['\"\w.\-\[\]]*)\s*(?:=|:)\s*(?P<quote>[\"']?)(?P<value>[^\"'\s,#}{]{4,}|.+?)(?P=quote)(?:\s*(?:,|\#|//|/\*).*)?$",
    re.IGNORECASE,
)
PLACEHOLDER_VALUE_HINTS = {
    "",
    "***",
    "******",
    "changeme",
    "example",
    "fake",
    "none",
    "null",
    "placeholder",
    "sample",
    "test",
    "token",
    "your-key",
    "your-secret",
}


class GitToolError(RuntimeError):
    """Raised when a git helper request cannot be fulfilled."""


def _ensure_git_available() -> None:
    if not shutil.which("git"):
        raise GitToolError("当前设备未安装 git，或 git 不在 PATH 中")


def _normalize_cwd(cwd: str) -> Path:
    normalized = (cwd or "").strip()
    if not normalized:
        raise GitToolError("项目目录不能为空")

    path = Path(normalized).expanduser()
    if not path.exists():
        raise GitToolError(f"项目目录不存在：{path}")
    if not path.is_dir():
        raise GitToolError(f"项目目录不是文件夹：{path}")
    return path.resolve()


def _run_git(
    cwd: Path,
    args: list[str],
    *,
    timeout: int = 15,
    check: bool = True,
) -> str:
    _ensure_git_available()
    command = ["git", "-c", "core.quotepath=false", *args]
    try:
        completed = run_quiet(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitToolError(f"Git 命令执行超时：{' '.join(command)}") from exc
    except OSError as exc:
        raise GitToolError(f"执行 Git 命令失败：{exc}") from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if check and completed.returncode != 0:
        detail = stderr.strip() or stdout.strip() or f"Git 命令失败，退出码 {completed.returncode}"
        raise GitToolError(detail)
    return stdout.rstrip()


def _resolve_repo_root(cwd: str) -> tuple[Path, Path]:
    requested_cwd = _normalize_cwd(cwd)
    repo_root_text = _run_git(requested_cwd, ["rev-parse", "--show-toplevel"])
    repo_root = Path(repo_root_text.strip()).resolve()
    return requested_cwd, repo_root


def _read_git_name_status_map(repo_root: Path, args: list[str]) -> dict[str, str]:
    output = _run_git(repo_root, [*args, "-z"], timeout=20, check=False)
    if not output:
        return {}

    tokens = [item for item in output.split("\0") if item]
    parsed: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        status_token = tokens[index].strip()
        index += 1
        if not status_token:
            continue

        status_code = status_token[0]
        if status_code in {"R", "C"}:
            if index + 1 >= len(tokens):
                break
            index += 1  # old path
            path = tokens[index].strip()
            index += 1
        else:
            if index >= len(tokens):
                break
            path = tokens[index].strip()
            index += 1

        if path:
            parsed[path] = status_code
    return parsed


def _build_changed_files(
    *,
    staged_statuses: dict[str, str],
    unstaged_statuses: dict[str, str],
    untracked_paths: set[str],
) -> list[dict[str, object]]:
    changed_files: list[dict[str, object]] = []
    changed_paths = sorted(set(staged_statuses) | set(unstaged_statuses) | set(untracked_paths))

    for path in changed_paths:
        if path in untracked_paths and path not in staged_statuses and path not in unstaged_statuses:
            status = "??"
            staged = False
            unstaged = False
            untracked = True
        else:
            staged_status = staged_statuses.get(path, " ")
            unstaged_status = unstaged_statuses.get(path, " ")
            status = f"{staged_status}{unstaged_status}"
            staged = path in staged_statuses
            unstaged = path in unstaged_statuses
            untracked = False

        changed_files.append(
            {
                "path": path,
                "status": status,
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
            }
        )
    return changed_files


def _format_status_lines(changed_files: list[dict[str, object]]) -> list[str]:
    return [
        f"{str(item.get('status') or '??')} {str(item.get('path') or '').strip()}".rstrip()
        for item in changed_files
        if str(item.get("path") or "").strip()
    ]


def _parse_branch_name(branch_text: str, branch_status: str) -> str:
    branch = (branch_text or "").strip()
    if branch and branch != "HEAD":
        return branch

    if branch_status.startswith("## "):
        candidate = branch_status[3:].split("...", 1)[0].strip()
        candidate = candidate.split(" ", 1)[0].strip()
        if candidate:
            return candidate
    return "HEAD"


def _is_new_changed_file(item: dict[str, object]) -> bool:
    status = str(item.get("status") or "").upper()
    return bool(item.get("untracked")) or "A" in status


def _is_deleted_changed_file(item: dict[str, object]) -> bool:
    status = str(item.get("status") or "").upper()
    return not bool(item.get("untracked")) and "D" in status and "A" not in status


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _is_probably_binary_file(path: Path) -> bool:
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return False
    return _looks_binary(path, data)


def _build_ignore_suggestion(path: str, *, matched_dir_part: Optional[str] = None) -> str:
    pure_path = Path(path)
    if matched_dir_part:
        prefix_parts: list[str] = []
        for part in pure_path.parts:
            prefix_parts.append(part)
            if part.lower() == matched_dir_part:
                return "/".join(prefix_parts) + "/"
    return path


def _is_env_candidate(name_lower: str) -> bool:
    if name_lower == ".env":
        return True
    if not name_lower.startswith(".env."):
        return False
    return not (
        name_lower.endswith(".example")
        or name_lower.endswith(".sample")
        or name_lower.endswith(".template")
        or name_lower.endswith(".local.example")
    )


def _match_clear_ignore_dir_part(path: Path) -> Optional[str]:
    for index, part in enumerate(item.lower() for item in path.parts[:-1]):
        if part in {"log", "logs"} and index != 0:
            continue
        if part in CLEAR_IGNORE_DIR_PARTS:
            return part
        if any(part.startswith(prefix) for prefix in CLEAR_IGNORE_DIR_PREFIXES):
            return part
    return None


def _is_local_artifact_path(path: str) -> bool:
    pure_path = Path((path or "").replace("\\", "/"))
    parts = [part.lower() for part in pure_path.parts if part and part != "."]
    if not parts:
        return False

    first_part = parts[0]
    if first_part in LOCAL_ARTIFACT_ROOT_DIRS:
        return True
    if any(first_part.startswith(prefix) for prefix in LOCAL_ARTIFACT_ROOT_PREFIXES):
        return True

    if len(parts) == 1:
        name = pure_path.name.lower()
        return any(fnmatch(name, pattern) for pattern in LOCAL_ARTIFACT_ROOT_FILE_PATTERNS)

    return False


def _build_local_artifact_issue(item: dict[str, object]) -> Optional[dict[str, object]]:
    if _is_deleted_changed_file(item):
        return None

    path = str(item.get("path") or "").strip()
    if not path or not _is_local_artifact_path(path):
        return None

    return {
        "issue_type": "local_artifact",
        "severity": "error",
        "blocking": True,
        "path": path,
        "line": None,
        "message": "疑似 Codex/调试/运行时本地产物，不应进入提交。",
        "suggestion": _build_ignore_suggestion(path),
    }


def _build_ignore_candidate_issue(repo_root: Path, item: dict[str, object]) -> Optional[dict[str, object]]:
    if not _is_new_changed_file(item):
        return None

    path = str(item.get("path") or "").strip()
    if not path:
        return None

    pure_path = Path(path)
    name_lower = pure_path.name.lower()
    suffix = pure_path.suffix.lower()
    file_path = (repo_root / pure_path).resolve()

    if _is_env_candidate(name_lower):
        return {
            "issue_type": "ignore_candidate",
            "severity": "error",
            "blocking": True,
            "path": path,
            "line": None,
            "message": "疑似本地环境配置文件，通常不应直接提交。",
            "suggestion": _build_ignore_suggestion(path),
        }

    matched_dir_part = _match_clear_ignore_dir_part(pure_path)
    if matched_dir_part:
        return {
            "issue_type": "ignore_candidate",
            "severity": "error",
            "blocking": True,
            "path": path,
            "line": None,
            "message": "疑似本地生成目录、缓存目录或日志目录产物，建议加入 .gitignore。",
            "suggestion": _build_ignore_suggestion(path, matched_dir_part=matched_dir_part),
        }

    if name_lower in CLEAR_IGNORE_FILENAMES:
        return {
            "issue_type": "ignore_candidate",
            "severity": "error",
            "blocking": True,
            "path": path,
            "line": None,
            "message": "疑似系统临时文件，通常不应进入版本库。",
            "suggestion": pure_path.name,
        }

    if suffix in CLEAR_IGNORE_SUFFIXES:
        return {
            "issue_type": "ignore_candidate",
            "severity": "error",
            "blocking": True,
            "path": path,
            "line": None,
            "message": "疑似日志、缓存或临时文件，建议加入 .gitignore。",
            "suggestion": f"*{suffix}",
        }

    try:
        file_size = file_path.stat().st_size
    except OSError:
        file_size = 0

    if suffix in WARNING_DATA_SUFFIXES and file_size >= 128 * 1024:
        return {
            "issue_type": "ignore_candidate",
            "severity": "warning",
            "blocking": False,
            "path": path,
            "line": None,
            "message": f"新文件像数据产物或归档文件，体积约 {_format_file_size(file_size)}，请确认是否真的需要提交。",
            "suggestion": _build_ignore_suggestion(path),
        }

    if file_size >= LARGE_FILE_WARNING_BYTES and _is_probably_binary_file(file_path):
        return {
            "issue_type": "ignore_candidate",
            "severity": "warning",
            "blocking": False,
            "path": path,
            "line": None,
            "message": f"新二进制文件体积约 {_format_file_size(file_size)}，请确认不是误提交的构建产物或数据文件。",
            "suggestion": _build_ignore_suggestion(path),
        }

    return None


def _extract_added_lines_from_patch(patch_text: str) -> list[tuple[Optional[int], str]]:
    added_lines: list[tuple[Optional[int], str]] = []
    current_new_line: Optional[int] = None

    for raw_line in patch_text.splitlines():
        if raw_line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw_line)
            current_new_line = int(match.group(1)) if match else None
            continue
        if raw_line.startswith("+++ ") or raw_line.startswith("--- "):
            continue
        if raw_line.startswith("+"):
            added_lines.append((current_new_line, raw_line[1:]))
            if current_new_line is not None:
                current_new_line += 1
            continue
        if raw_line.startswith(" "):
            if current_new_line is not None:
                current_new_line += 1
            continue
        if raw_line.startswith("-"):
            continue

    return added_lines


def _read_text_lines_for_scan(file_path: Path) -> list[tuple[Optional[int], str]]:
    try:
        raw = file_path.read_bytes()
    except OSError:
        return []

    if not raw or _looks_binary(file_path, raw[:4096]):
        return []

    text = raw[:MAX_SENSITIVE_SCAN_BYTES].decode("utf-8", errors="replace")
    return [(index, line) for index, line in enumerate(text.splitlines(), start=1)]


def _prepare_precheck_preview_text(text: str) -> str:
    if len(text) > PRECHECK_MAX_PREVIEW_LINE_LENGTH:
        return text[: PRECHECK_MAX_PREVIEW_LINE_LENGTH - 1] + "…"
    return text


def _build_precheck_context_lines(
    preview_lines: list[tuple[Optional[int], str]],
    *,
    line_number: Optional[int],
    matched_content: Optional[str] = None,
) -> list[dict[str, object]]:
    if line_number is None:
        return []

    target_index: Optional[int] = None
    for index, (current_line_number, _) in enumerate(preview_lines):
        if current_line_number == line_number:
            target_index = index
            break

    if target_index is None:
        if matched_content is None:
            return []
        return [
            {
                "line_number": line_number,
                "text": _prepare_precheck_preview_text(matched_content),
                "is_match": True,
            }
        ]

    start = max(0, target_index - PRECHECK_CONTEXT_RADIUS)
    end = min(len(preview_lines), target_index + PRECHECK_CONTEXT_RADIUS + 1)
    context_lines: list[dict[str, object]] = []
    for current_line_number, content in preview_lines[start:end]:
        context_lines.append(
            {
                "line_number": current_line_number,
                "text": _prepare_precheck_preview_text(content),
                "is_match": current_line_number == line_number,
            }
        )
    return context_lines


def _normalize_secret_value(value: str) -> str:
    return value.strip().strip("\"'").strip(",").strip()


def _looks_like_placeholder_secret(value: str) -> bool:
    normalized = _normalize_secret_value(value).lower()
    if normalized in PLACEHOLDER_VALUE_HINTS:
        return True
    if not normalized:
        return True
    if normalized.startswith("${") or normalized.startswith("{{") or normalized.startswith("<"):
        return True
    if normalized.endswith("}") and (normalized.startswith("${") or normalized.startswith("{{")):
        return True
    if normalized.endswith(">") and normalized.startswith("<"):
        return True
    if re.fullmatch(r"[*xX•-]{4,}", normalized):
        return True
    return any(hint in normalized for hint in ("example", "sample", "dummy", "placeholder", "your_", "your-"))


def _should_flag_generic_secret(keyword: str, raw_value: str) -> bool:
    value = _normalize_secret_value(raw_value)
    if _looks_like_placeholder_secret(value):
        return False
    if len(value) < 4:
        return False
    if any(char.isspace() for char in value):
        return False

    lowered_keyword = keyword.lower()
    if lowered_keyword in {"password", "passwd", "pwd"}:
        return len(value) >= 4
    if lowered_keyword in {"authorization"}:
        return len(value) >= 12
    return len(value) >= 8


def _build_sensitive_issue(
    *,
    path: str,
    line: Optional[int],
    message: str,
    context_lines: Optional[list[dict[str, object]]] = None,
) -> dict[str, object]:
    return {
        "issue_type": "sensitive_content",
        "severity": "warning",
        "blocking": False,
        "path": path,
        "line": line,
        "message": message,
        "suggestion": "",
        "context_lines": context_lines or [],
    }


def _collect_sensitive_scan_lines(
    repo_root: Path,
    item: dict[str, object],
    *,
    add_all: Optional[bool],
) -> list[tuple[Optional[int], str]]:
    path = str(item.get("path") or "").strip()
    if not path:
        return []

    file_path = (repo_root / path).resolve()
    is_new_file = _is_new_changed_file(item)
    use_worktree_scope = add_all is not False

    if is_new_file and use_worktree_scope:
        return _read_text_lines_for_scan(file_path)

    if use_worktree_scope:
        diff_text = _run_git(
            repo_root,
            ["--no-pager", "diff", "HEAD", "--unified=0", "--no-color", "--", path],
            timeout=20,
            check=False,
        )
    else:
        if not bool(item.get("staged")):
            return []
        diff_text = _run_git(
            repo_root,
            ["--no-pager", "diff", "--cached", "HEAD", "--unified=0", "--no-color", "--", path],
            timeout=20,
            check=False,
        )

    return _extract_added_lines_from_patch(diff_text)


def _scan_sensitive_content(
    repo_root: Path,
    item: dict[str, object],
    *,
    add_all: Optional[bool],
) -> list[dict[str, object]]:
    path = str(item.get("path") or "").strip()
    if not path:
        return []

    file_path = (repo_root / path).resolve()
    preview_lines = _read_text_lines_for_scan(file_path)
    issues: list[dict[str, object]] = []
    for line_number, content in _collect_sensitive_scan_lines(repo_root, item, add_all=add_all):
        stripped = content.strip()
        if not stripped:
            continue

        context_lines = _build_precheck_context_lines(
            preview_lines,
            line_number=line_number,
            matched_content=content,
        )

        if PRIVATE_KEY_RE.search(content):
            issues.append(
                _build_sensitive_issue(
                    path=path,
                    line=line_number,
                    message="疑似提交了私钥内容。",
                    context_lines=context_lines,
                )
            )
        elif OPENAI_KEY_RE.search(content):
            issues.append(
                _build_sensitive_issue(
                    path=path,
                    line=line_number,
                    message="疑似包含 OpenAI 风格访问密钥。",
                    context_lines=context_lines,
                )
            )
        elif AWS_ACCESS_KEY_RE.search(content):
            issues.append(
                _build_sensitive_issue(
                    path=path,
                    line=line_number,
                    message="疑似包含 AWS Access Key。",
                    context_lines=context_lines,
                )
            )
        elif GITHUB_TOKEN_RE.search(content):
            issues.append(
                _build_sensitive_issue(
                    path=path,
                    line=line_number,
                    message="疑似包含 GitHub Token。",
                    context_lines=context_lines,
                )
            )
        elif URL_CREDENTIAL_RE.search(content):
            issues.append(
                _build_sensitive_issue(
                    path=path,
                    line=line_number,
                    message="疑似在连接串里明文携带账号密码。",
                    context_lines=context_lines,
                )
            )
        elif AUTH_HEADER_RE.search(content):
            issues.append(
                _build_sensitive_issue(
                    path=path,
                    line=line_number,
                    message="疑似包含明文 Authorization 凭证。",
                    context_lines=context_lines,
                )
            )
        elif JWT_TOKEN_RE.search(content):
            issues.append(
                _build_sensitive_issue(
                    path=path,
                    line=line_number,
                    message="疑似包含完整 JWT Token。",
                    context_lines=context_lines,
                )
            )
        else:
            match = GENERIC_SECRET_ASSIGNMENT_RE.search(content)
            if match and _should_flag_generic_secret(str(match.group("keyword") or ""), str(match.group("value") or "")):
                issues.append(
                    _build_sensitive_issue(
                        path=path,
                        line=line_number,
                        message=f"疑似存在明文 {match.group('keyword')} 配置。",
                        context_lines=context_lines,
                    )
                )

        if len(issues) >= MAX_SENSITIVE_ISSUES_PER_FILE:
            break

    return issues


def _precheck_issue_sort_key(issue: dict[str, object]) -> tuple[int, str, int]:
    severity = str(issue.get("severity") or "")
    line_number = int(issue.get("line") or 0)
    return (0 if severity == "error" else 1, str(issue.get("path") or ""), line_number)


def _build_precheck_report(
    repo_root: Path,
    changed_files: list[dict[str, object]],
    *,
    add_all: Optional[bool] = None,
) -> dict[str, object]:
    checked_items: list[dict[str, object]] = []
    for item in changed_files:
        if not isinstance(item, dict):
            continue
        if add_all is False and not bool(item.get("staged")):
            continue
        checked_items.append(item)

    issues: list[dict[str, object]] = []
    seen_issue_keys: set[tuple[str, str, int, str]] = set()
    for item in checked_items:
        local_artifact_issue = _build_local_artifact_issue(item)
        has_local_artifact_issue = local_artifact_issue is not None
        if local_artifact_issue is not None:
            key = (
                str(local_artifact_issue.get("issue_type") or ""),
                str(local_artifact_issue.get("path") or ""),
                int(local_artifact_issue.get("line") or 0),
                str(local_artifact_issue.get("message") or ""),
            )
            if key not in seen_issue_keys:
                issues.append(local_artifact_issue)
                seen_issue_keys.add(key)

        ignore_issue = None if has_local_artifact_issue else _build_ignore_candidate_issue(repo_root, item)
        if ignore_issue is not None:
            key = (
                str(ignore_issue.get("issue_type") or ""),
                str(ignore_issue.get("path") or ""),
                int(ignore_issue.get("line") or 0),
                str(ignore_issue.get("message") or ""),
            )
            if key not in seen_issue_keys:
                issues.append(ignore_issue)
                seen_issue_keys.add(key)

        for sensitive_issue in _scan_sensitive_content(repo_root, item, add_all=add_all):
            key = (
                str(sensitive_issue.get("issue_type") or ""),
                str(sensitive_issue.get("path") or ""),
                int(sensitive_issue.get("line") or 0),
                str(sensitive_issue.get("message") or ""),
            )
            if key not in seen_issue_keys:
                issues.append(sensitive_issue)
                seen_issue_keys.add(key)

    issues.sort(key=_precheck_issue_sort_key)
    warning_count = sum(1 for issue in issues if str(issue.get("severity") or "") == "warning")
    error_count = sum(1 for issue in issues if str(issue.get("severity") or "") == "error")
    blocking_issue_count = sum(1 for issue in issues if bool(issue.get("blocking")))
    return {
        "checked_file_count": len(checked_items),
        "issue_count": len(issues),
        "warning_count": warning_count,
        "error_count": error_count,
        "blocking_issue_count": blocking_issue_count,
        "has_blocking_issues": blocking_issue_count > 0,
        "issues": issues,
    }


def _raise_for_blocking_precheck_issues(repo_root: Path, changed_files: list[dict[str, object]], *, add_all: bool) -> None:
    report = _build_precheck_report(repo_root, changed_files, add_all=add_all)
    if not report["has_blocking_issues"]:
        return

    blocking_issues = [issue for issue in report["issues"] if bool(issue.get("blocking"))]
    preview = "；".join(
        f"{issue['path']}：{issue['message']}"
        for issue in blocking_issues[:3]
    )
    raise GitToolError(
        f"提交前预检未通过，发现 {report['blocking_issue_count']} 条阻断项。"
        f"{preview}"
    )


def inspect_git_repository(cwd: str) -> dict[str, object]:
    requested_cwd, repo_root = _resolve_repo_root(cwd)
    branch_status_output = _run_git(repo_root, ["status", "--short", "--branch"])
    branch_status_lines = branch_status_output.splitlines()
    branch_status = branch_status_lines[0].strip() if branch_status_lines else ""

    branch_text = _run_git(repo_root, ["symbolic-ref", "--short", "HEAD"], check=False)
    staged_statuses = _read_git_name_status_map(repo_root, ["diff", "--cached", "--name-status", "--find-renames", "--no-color"])
    unstaged_statuses = _read_git_name_status_map(repo_root, ["diff", "--name-status", "--find-renames", "--no-color"])
    untracked_paths = _read_git_path_set(repo_root, ["ls-files", "--others", "--exclude-standard", "-z"])
    changed_files = _build_changed_files(
        staged_statuses=staged_statuses,
        unstaged_statuses=unstaged_statuses,
        untracked_paths=untracked_paths,
    )
    status_lines = _format_status_lines(changed_files)
    changed_paths = [
        str(item["path"])
        for item in changed_files
        if isinstance(item.get("path"), str)
    ]
    diff_stat = _run_git(repo_root, ["--no-pager", "diff", "--stat", "--find-renames", "--no-color"], timeout=20, check=False)
    staged_diff_stat = _run_git(repo_root, ["--no-pager", "diff", "--cached", "--stat", "--find-renames", "--no-color"], timeout=20, check=False)
    added_line_count, deleted_line_count = _estimate_changed_line_counts(repo_root, untracked_paths=untracked_paths)
    estimated_changed_line_count = added_line_count + deleted_line_count
    scope_summary = _assess_commit_scope(len(changed_files), estimated_changed_line_count)

    return {
        "cwd": str(requested_cwd),
        "repo_root": str(repo_root),
        "branch": _parse_branch_name(branch_text, branch_status),
        "branch_status": branch_status,
        "clean": not changed_files,
        "status_lines": status_lines,
        "diff_stat": diff_stat,
        "staged_diff_stat": staged_diff_stat,
        "changed_files": changed_files,
        "added_line_count": added_line_count,
        "deleted_line_count": deleted_line_count,
        "suggested_split_groups": _build_split_groups(changed_paths),
        "precheck": _build_precheck_report(repo_root, changed_files),
        **scope_summary,
    }


def _normalize_history_window_days(days: int) -> int | None:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_WINDOW_DAYS
    if value <= ALL_HISTORY_WINDOW_DAYS:
        return None
    return max(MIN_HISTORY_WINDOW_DAYS, min(MAX_HISTORY_WINDOW_DAYS, value))


def _read_first_commit_date(repo_root: Path) -> date | None:
    output = _run_git(
        repo_root,
        [
            "log",
            "--max-parents=0",
            "--date=short",
            "--pretty=format:%cd",
            "HEAD",
        ],
        timeout=GIT_HISTORY_LOG_TIMEOUT,
        check=False,
    ).strip()
    if not output:
        return None
    try:
        return date.fromisoformat(output)
    except ValueError:
        return None


def _build_empty_history_points(start_date: date, end_date: date) -> list[dict[str, object]]:
    cursor = start_date
    items: list[dict[str, object]] = []
    while cursor <= end_date:
        items.append(
            {
                "date": cursor.isoformat(),
                "added_line_count": 0,
                "deleted_line_count": 0,
                "commit_count": 0,
            }
        )
        cursor += timedelta(days=1)
    return items


def _collect_git_history_points(
    repo_root: Path,
    *,
    start_date: date,
    end_date: date,
    since_date: date | None = None,
) -> list[dict[str, object]]:
    points = _build_empty_history_points(start_date, end_date)
    point_map = {str(item["date"]): item for item in points}
    log_args = [
        "log",
        "--no-merges",
        "--date=short",
        f"--pretty=format:{GIT_HISTORY_MARKER}%x09%cd",
        "--numstat",
        "HEAD",
    ]
    if since_date is not None:
        log_args.insert(2, f"--since={since_date.isoformat()}")
    output = _run_git(
        repo_root,
        log_args,
        timeout=GIT_HISTORY_LOG_TIMEOUT,
        check=False,
    )

    active_date = ""
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith(f"{GIT_HISTORY_MARKER}\t"):
            active_date = line.split("\t", 1)[1].strip()
            point = point_map.get(active_date)
            if point is not None:
                point["commit_count"] = int(point["commit_count"]) + 1
            continue
        if not active_date:
            continue
        point = point_map.get(active_date)
        if point is None:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        added_text, deleted_text, _ = parts
        if added_text.isdigit():
            point["added_line_count"] = int(point["added_line_count"]) + int(added_text)
        if deleted_text.isdigit():
            point["deleted_line_count"] = int(point["deleted_line_count"]) + int(deleted_text)
    return points


def collect_git_history_stats(cwd: str, *, days: int = DEFAULT_HISTORY_WINDOW_DAYS) -> dict[str, object]:
    requested_cwd, repo_root = _resolve_repo_root(cwd)
    normalized_days = _normalize_history_window_days(days)
    end_date = date.today()
    if normalized_days is None:
        start_date = _read_first_commit_date(repo_root) or end_date
    else:
        start_date = end_date - timedelta(days=normalized_days - 1)
    branch_status_output = _run_git(repo_root, ["status", "--short", "--branch"])
    branch_status_lines = branch_status_output.splitlines()
    branch_status = branch_status_lines[0].strip() if branch_status_lines else ""
    branch_text = _run_git(repo_root, ["symbolic-ref", "--short", "HEAD"], check=False)
    points = _collect_git_history_points(
        repo_root,
        start_date=start_date,
        end_date=end_date,
        since_date=start_date if normalized_days is not None else None,
    )

    total_added_line_count = sum(int(item["added_line_count"]) for item in points)
    total_deleted_line_count = sum(int(item["deleted_line_count"]) for item in points)
    total_commit_count = sum(int(item["commit_count"]) for item in points)
    covered_days = (end_date - start_date).days + 1

    return {
        "cwd": str(requested_cwd),
        "repo_root": str(repo_root),
        "branch": _parse_branch_name(branch_text, branch_status),
        "days": covered_days,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_added_line_count": total_added_line_count,
        "total_deleted_line_count": total_deleted_line_count,
        "total_commit_count": total_commit_count,
        "points": points,
    }


def _read_git_path_set(repo_root: Path, args: list[str]) -> set[str]:
    output = _run_git(repo_root, args, timeout=20, check=False)
    if "\0" in output:
        return {item.strip() for item in output.split("\0") if item.strip()}
    return {line.strip() for line in output.splitlines() if line.strip()}


def _parse_numstat_counts(output: str) -> tuple[int, int]:
    added_total = 0
    deleted_total = 0
    for raw_line in output.splitlines():
        parts = raw_line.strip().split("\t", 2)
        if len(parts) < 3:
            continue
        added_text, deleted_text, _ = parts
        if added_text.isdigit():
            added_total += int(added_text)
        if deleted_text.isdigit():
            deleted_total += int(deleted_text)
    return added_total, deleted_total


def _estimate_text_file_line_count(path: Path, *, limit: int) -> int:
    try:
        with path.open("rb") as handle:
            line_count = 0
            saw_content = False
            last_byte = b""
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                if b"\x00" in chunk:
                    return 0
                saw_content = True
                line_count += chunk.count(b"\n")
                last_byte = chunk[-1:]
                if line_count >= limit:
                    return limit
    except OSError:
        return 0

    if saw_content and last_byte not in {b"", b"\n"}:
        line_count += 1
    return min(line_count, limit)


def _estimate_untracked_line_count(repo_root: Path, untracked_paths: set[str]) -> int:
    total = 0
    for relative_path in sorted(untracked_paths):
        remaining = UNTRACKED_LINE_COUNT_CAP - total
        if remaining <= 0:
            break
        total += _estimate_text_file_line_count((repo_root / relative_path).resolve(), limit=remaining)
    return total


def _estimate_changed_line_counts(repo_root: Path, *, untracked_paths: set[str]) -> tuple[int, int]:
    unstaged = _run_git(
        repo_root,
        ["diff", "--numstat", "--find-renames", "--no-color"],
        timeout=20,
        check=False,
    )
    staged = _run_git(
        repo_root,
        ["diff", "--cached", "--numstat", "--find-renames", "--no-color"],
        timeout=20,
        check=False,
    )
    unstaged_added, unstaged_deleted = _parse_numstat_counts(unstaged)
    staged_added, staged_deleted = _parse_numstat_counts(staged)
    return (
        unstaged_added + staged_added + _estimate_untracked_line_count(repo_root, untracked_paths),
        unstaged_deleted + staged_deleted,
    )


def _group_changed_path(path: str) -> str:
    pure_path = Path(path)
    parts = pure_path.parts
    if len(parts) <= 1:
        return "(仓库根目录)"
    if parts[0].startswith(".") and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _build_split_groups(changed_paths: list[str]) -> list[dict[str, object]]:
    grouped_counts: dict[str, int] = defaultdict(int)
    grouped_samples: dict[str, list[str]] = defaultdict(list)

    for path in changed_paths:
        label = _group_changed_path(path)
        grouped_counts[label] += 1
        if len(grouped_samples[label]) < MAX_GROUP_SAMPLE_PATHS:
            grouped_samples[label].append(path)

    ranked = sorted(grouped_counts.items(), key=lambda item: (-item[1], item[0]))[:MAX_SPLIT_GROUPS]
    return [
        {
            "label": label,
            "file_count": file_count,
            "sample_paths": grouped_samples[label],
        }
        for label, file_count in ranked
    ]


def _build_split_reason(changed_file_count: int, estimated_changed_line_count: int, *, oversized: bool) -> str:
    level_text = "已经超出单次 AI 总结的安全范围" if oversized else "规模较大，建议拆成多次提交"
    return (
        f"本次改动约 {changed_file_count} 个文件，估算 {estimated_changed_line_count} 行变更，"
        f"{level_text}。"
    )


def _assess_commit_scope(changed_file_count: int, estimated_changed_line_count: int) -> dict[str, object]:
    oversized = (
        changed_file_count >= OVERSIZED_FILE_THRESHOLD
        or estimated_changed_line_count >= OVERSIZED_LINE_THRESHOLD
    )
    split_recommended = oversized or (
        changed_file_count >= SPLIT_RECOMMENDED_FILE_THRESHOLD
        or estimated_changed_line_count >= SPLIT_RECOMMENDED_LINE_THRESHOLD
    )
    split_reason = (
        _build_split_reason(
            changed_file_count,
            estimated_changed_line_count,
            oversized=oversized,
        )
        if split_recommended
        else ""
    )
    return {
        "changed_file_count": changed_file_count,
        "estimated_changed_line_count": estimated_changed_line_count,
        "split_recommended": split_recommended,
        "split_reason": split_reason,
        "oversized": oversized,
    }


def _is_generated_path(path: str) -> bool:
    pure_path = Path(path)
    name = pure_path.name.lower()
    if name in LOCKFILE_NAMES:
        return True
    return any(part.lower() in GENERATED_PATH_PARTS for part in pure_path.parts)


def _score_changed_path(path: str) -> tuple[int, int, str]:
    pure_path = Path(path)
    suffix = pure_path.suffix.lower()
    name = pure_path.name.lower()
    score = 0

    if suffix in TEXT_LIKE_SUFFIXES:
        score += 4
    if suffix in {".md", ".py", ".ts", ".tsx", ".js", ".jsx", ".vue"}:
        score += 2
    if "test" in pure_path.parts or name.startswith("test_"):
        score += 1
    if _is_generated_path(path):
        score -= 5
    if suffix in BINARY_LIKE_SUFFIXES:
        score -= 4

    return (-score, len(path), path)


def _limit_text(value: str, max_chars: int = MAX_FILE_SECTION_CHARS) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars].rstrip() + "\n...<截断>", True


def _build_diff_section(
    repo_root: Path,
    path: str,
    *,
    staged: bool,
    max_chars: int = MAX_FILE_SECTION_CHARS,
) -> tuple[str, bool]:
    args = ["--no-pager", "diff", "--unified=2", "--find-renames", "--no-color"]
    if staged:
        args.append("--cached")
    args.extend(["--", path])
    diff_text = _run_git(repo_root, args, timeout=20, check=False)
    if not diff_text.strip():
        return "", False
    limited, truncated = _limit_text(diff_text, max_chars=max_chars)
    title = "已暂存差异" if staged else "未暂存差异"
    return f"[{title}]\n{limited}", truncated


def _looks_binary(path: Path, data: bytes) -> bool:
    if path.suffix.lower() in BINARY_LIKE_SUFFIXES:
        return True
    return b"\x00" in data


def _build_untracked_preview(
    repo_root: Path,
    path: str,
    *,
    max_chars: int = MAX_FILE_SECTION_CHARS,
) -> tuple[str, bool]:
    file_path = (repo_root / path).resolve()
    if not file_path.exists() or not file_path.is_file():
        return "[未跟踪文件预览]\n<文件不存在或不是普通文件>", False

    try:
        data = file_path.read_bytes()[:MAX_PREVIEW_BYTES]
    except OSError:
        return "[未跟踪文件预览]\n<文件无法读取>", False

    if _looks_binary(file_path, data):
        return "[未跟踪文件预览]\n<二进制文件，跳过内容预览>", False

    preview_text = data.decode("utf-8", errors="replace")
    preview_lines = preview_text.splitlines()
    truncated = False
    if len(preview_lines) > MAX_PREVIEW_LINES:
        preview_text = "\n".join(preview_lines[:MAX_PREVIEW_LINES]) + "\n...<截断>"
        truncated = True
    limited, was_truncated = _limit_text(preview_text, max_chars=max_chars)
    return f"[未跟踪文件预览]\n{limited}", truncated or was_truncated


def _strip_preview_heading(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("[") and lines[0].endswith("]"):
        return "\n".join(lines[1:])
    return text


def _summarize_file_status(path: str, *, staged_paths: set[str], unstaged_paths: set[str], untracked_paths: set[str]) -> str:
    if path in untracked_paths:
        return "未跟踪"
    if path in staged_paths and path in unstaged_paths:
        return "已暂存 + 未暂存"
    if path in staged_paths:
        return "已暂存"
    if path in unstaged_paths:
        return "未暂存"
    return "未知状态"


def _build_reduction_unit_section(
    repo_root: Path,
    path: str,
    *,
    staged_paths: set[str],
    unstaged_paths: set[str],
    untracked_paths: set[str],
) -> tuple[str, bool]:
    section_lines = [
        f"文件路径: {path}",
        f"所属分组: {_group_changed_path(path)}",
        f"状态: {_summarize_file_status(path, staged_paths=staged_paths, unstaged_paths=unstaged_paths, untracked_paths=untracked_paths)}",
    ]

    truncated = False
    if path in unstaged_paths:
        diff_section, section_truncated = _build_diff_section(repo_root, path, staged=False)
        if diff_section:
            section_lines.append(diff_section)
            truncated = truncated or section_truncated
    if path in staged_paths:
        diff_section, section_truncated = _build_diff_section(repo_root, path, staged=True)
        if diff_section:
            section_lines.append(diff_section)
            truncated = truncated or section_truncated
    if path in untracked_paths:
        preview_section, section_truncated = _build_untracked_preview(repo_root, path)
        section_lines.append(preview_section)
        truncated = truncated or section_truncated

    limited, was_truncated = _limit_text("\n".join(section_lines).strip(), max_chars=MAX_REDUCTION_UNIT_CHARS)
    return limited, truncated or was_truncated


def collect_git_reduction_source_units(cwd: str) -> dict[str, object]:
    inspect_payload = inspect_git_repository(cwd)
    if bool(inspect_payload["clean"]):
        raise GitToolError("当前工作区没有可提交的变更")

    repo_root = Path(str(inspect_payload["repo_root"]))
    unstaged_paths = _read_git_path_set(repo_root, ["diff", "--name-only", "-z"])
    staged_paths = _read_git_path_set(repo_root, ["diff", "--cached", "--name-only", "-z"])
    untracked_paths = _read_git_path_set(repo_root, ["ls-files", "--others", "--exclude-standard", "-z"])

    candidate_paths = sorted(unstaged_paths | staged_paths | untracked_paths, key=_score_changed_path)
    if not candidate_paths:
        candidate_paths = [str(item["path"]) for item in inspect_payload["changed_files"]]  # type: ignore[index]

    source_units: list[dict[str, object]] = []
    truncated_count = 0
    for path in candidate_paths:
        content, truncated = _build_reduction_unit_section(
            repo_root,
            path,
            staged_paths=staged_paths,
            unstaged_paths=unstaged_paths,
            untracked_paths=untracked_paths,
        )
        if not content.strip():
            continue
        if truncated:
            truncated_count += 1
        source_units.append(
            {
                "unit_id": path,
                "path": path,
                "group": _group_changed_path(path),
                "content": content,
                "truncated": truncated,
            }
        )

    return {
        **inspect_payload,
        "source_units": source_units,
        "source_unit_count": len(source_units),
        "source_unit_truncated_count": truncated_count,
    }


def collect_git_file_diff(cwd: str, path: str) -> dict[str, object]:
    inspect_payload = inspect_git_repository(cwd)
    requested_path = (path or "").strip().replace("\\", "/")
    if not requested_path:
        raise GitToolError("文件路径不能为空")

    changed_files = inspect_payload.get("changed_files") or []
    selected_file = next(
        (
            item
            for item in changed_files
            if isinstance(item, dict) and str(item.get("path") or "").strip() == requested_path
        ),
        None,
    )
    if not isinstance(selected_file, dict):
        raise GitToolError("指定文件不在当前工作区改动列表中")

    repo_root = Path(str(inspect_payload["repo_root"]))
    sections: list[dict[str, object]] = []
    truncated = False

    if bool(selected_file.get("unstaged")):
        section_content, section_truncated = _build_diff_section(
            repo_root,
            requested_path,
            staged=False,
            max_chars=MAX_FILE_PREVIEW_CHARS,
        )
        if section_content:
            sections.append(
                {
                    "kind": "unstaged",
                    "title": "未暂存差异",
                    "content": _strip_preview_heading(section_content),
                    "truncated": section_truncated,
                }
            )
            truncated = truncated or section_truncated

    if bool(selected_file.get("staged")):
        section_content, section_truncated = _build_diff_section(
            repo_root,
            requested_path,
            staged=True,
            max_chars=MAX_FILE_PREVIEW_CHARS,
        )
        if section_content:
            sections.append(
                {
                    "kind": "staged",
                    "title": "已暂存差异",
                    "content": _strip_preview_heading(section_content),
                    "truncated": section_truncated,
                }
            )
            truncated = truncated or section_truncated

    if bool(selected_file.get("untracked")):
        section_content, section_truncated = _build_untracked_preview(
            repo_root,
            requested_path,
            max_chars=MAX_FILE_PREVIEW_CHARS,
        )
        if section_content:
            sections.append(
                {
                    "kind": "untracked",
                    "title": "未跟踪文件预览",
                    "content": _strip_preview_heading(section_content),
                    "truncated": section_truncated,
                }
            )
            truncated = truncated or section_truncated

    if not sections:
        sections.append(
            {
                "kind": "empty",
                "title": "当前文件没有可展示的差异",
                "content": "",
                "truncated": False,
            }
        )

    return {
        "cwd": str(inspect_payload["cwd"]),
        "repo_root": str(inspect_payload["repo_root"]),
        "branch": str(inspect_payload["branch"]),
        "path": requested_path,
        "status": str(selected_file.get("status") or ""),
        "staged": bool(selected_file.get("staged")),
        "unstaged": bool(selected_file.get("unstaged")),
        "untracked": bool(selected_file.get("untracked")),
        "truncated": truncated,
        "sections": sections,
    }


def collect_git_commit_context(cwd: str, *, max_files: int = 8) -> dict[str, object]:
    inspect_payload = inspect_git_repository(cwd)
    if bool(inspect_payload["clean"]):
        raise GitToolError("当前工作区没有可提交的变更")

    repo_root = Path(str(inspect_payload["repo_root"]))
    unstaged_paths = _read_git_path_set(repo_root, ["diff", "--name-only", "-z"])
    staged_paths = _read_git_path_set(repo_root, ["diff", "--cached", "--name-only", "-z"])
    untracked_paths = _read_git_path_set(repo_root, ["ls-files", "--others", "--exclude-standard", "-z"])

    candidate_paths = sorted(unstaged_paths | staged_paths | untracked_paths, key=_score_changed_path)
    if not candidate_paths:
        candidate_paths = [str(item["path"]) for item in inspect_payload["changed_files"]]  # type: ignore[index]

    selected_paths = candidate_paths[: max(1, max_files)]
    omitted_path_count = max(0, len(candidate_paths) - len(selected_paths))

    context_sections = [
        "仓库概览",
        f"- 仓库根目录: {inspect_payload['repo_root']}",
        f"- 当前目录: {inspect_payload['cwd']}",
        f"- 当前分支: {inspect_payload['branch']}",
    ]
    branch_status = str(inspect_payload.get("branch_status") or "").strip()
    if branch_status:
        context_sections.append(f"- 分支状态: {branch_status}")

    context_sections.append("- 工作区状态:")
    context_sections.extend([f"  {line}" for line in inspect_payload["status_lines"]])  # type: ignore[index]
    diff_stat = str(inspect_payload.get("diff_stat") or "").strip() or "(无未暂存 diff 统计)"
    staged_diff_stat = str(inspect_payload.get("staged_diff_stat") or "").strip() or "(无已暂存 diff 统计)"
    context_sections.append("- 未暂存 diff 统计:")
    context_sections.append(diff_stat)
    context_sections.append("- 已暂存 diff 统计:")
    context_sections.append(staged_diff_stat)

    if bool(inspect_payload.get("split_recommended")):
        context_sections.append("- 提交规模提示:")
        context_sections.append(str(inspect_payload.get("split_reason") or "建议拆分提交"))

    split_groups = inspect_payload.get("suggested_split_groups") or []
    if isinstance(split_groups, list) and split_groups:
        context_sections.append("- 建议拆分分组:")
        for group in split_groups:
            if not isinstance(group, dict):
                continue
            label = str(group.get("label") or "").strip()
            file_count = int(group.get("file_count") or 0)
            sample_paths = [
                str(item).strip()
                for item in (group.get("sample_paths") or [])
                if str(item).strip()
            ]
            sample_text = f"（例如: {', '.join(sample_paths)}）" if sample_paths else ""
            context_sections.append(f"  - {label}: {file_count} 个文件{sample_text}")

    prompt_parts = ["\n".join(context_sections), "重点文件变更片段"]
    total_chars = len(prompt_parts[0])
    truncated = False

    if bool(inspect_payload.get("oversized")):
        overview_paths = candidate_paths[: max(1, min(len(candidate_paths), MAX_OVERVIEW_PATHS))]
        omitted_path_count = max(0, len(candidate_paths) - len(overview_paths))
        if overview_paths:
            prompt_parts.append("### 代表性文件路径\n" + "\n".join(f"- {path}" for path in overview_paths))
        if omitted_path_count > 0:
            prompt_parts.append(f"### 其余文件\n还有 {omitted_path_count} 个文件未展开，请优先判断是否应该拆分提交。")
        return {
            **inspect_payload,
            "prompt_context": "\n\n".join(prompt_parts).strip(),
            "selected_paths": overview_paths,
            "omitted_path_count": omitted_path_count,
            "context_truncated": True,
            "context_mode": "overview_only",
        }

    for path in selected_paths:
        file_sections = [f"### 文件: {path}"]
        if path in unstaged_paths:
            diff_section, section_truncated = _build_diff_section(repo_root, path, staged=False)
            if diff_section:
                file_sections.append(diff_section)
                truncated = truncated or section_truncated
        if path in staged_paths:
            diff_section, section_truncated = _build_diff_section(repo_root, path, staged=True)
            if diff_section:
                file_sections.append(diff_section)
                truncated = truncated or section_truncated
        if path in untracked_paths:
            preview_section, section_truncated = _build_untracked_preview(repo_root, path)
            file_sections.append(preview_section)
            truncated = truncated or section_truncated

        section_text = "\n".join(file_sections).strip()
        if not section_text:
            continue
        limited_section, section_truncated = _limit_text(section_text)
        truncated = truncated or section_truncated
        if total_chars + len(limited_section) > MAX_CONTEXT_CHARS:
            truncated = True
            omitted_path_count = max(omitted_path_count, len(selected_paths) - selected_paths.index(path))
            break
        prompt_parts.append(limited_section)
        total_chars += len(limited_section)

    if omitted_path_count > 0:
        prompt_parts.append(f"### 其余文件\n还有 {omitted_path_count} 个文件未展开，请结合 diff 统计一起概括。")

    return {
        **inspect_payload,
        "prompt_context": "\n\n".join(prompt_parts).strip(),
        "selected_paths": selected_paths,
        "omitted_path_count": omitted_path_count,
        "context_truncated": truncated,
        "context_mode": "sampled",
    }


def normalize_commit_body_lines(body: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in body:
        line = str(item or "").strip()
        if not line:
            continue
        line = COMMIT_BODY_BULLET_PREFIX_RE.sub("", line)
        line = COMMIT_BODY_NUMBER_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        normalized.append(line)
    return normalized


def _format_commit_body_lines(body: list[str]) -> list[str]:
    return [f"{index}、{line}" for index, line in enumerate(body, start=1)]


def format_git_commit_message(subject: str, body: list[str]) -> str:
    normalized_subject = (subject or "").strip()
    if not normalized_subject:
        raise GitToolError("提交标题不能为空")

    normalized_body = normalize_commit_body_lines(body)
    if not normalized_body:
        return normalized_subject

    numbered_lines = _format_commit_body_lines(normalized_body)
    return normalized_subject + "\n\n" + "\n".join(numbered_lines)


def create_git_commit(
    cwd: str,
    *,
    subject: str,
    body: list[str],
    add_all: bool = True,
) -> dict[str, object]:
    _, repo_root = _resolve_repo_root(cwd)
    preflight_inspect = inspect_git_repository(str(repo_root))
    if bool(preflight_inspect["clean"]):
        raise GitToolError("当前工作区没有可提交的变更")

    _raise_for_blocking_precheck_issues(
        repo_root,
        list(preflight_inspect.get("changed_files") or []),
        add_all=add_all,
    )

    if add_all:
        _run_git(repo_root, ["add", "-A"], timeout=30)

    post_add_inspect = inspect_git_repository(str(repo_root))
    if bool(post_add_inspect["clean"]):
        raise GitToolError("当前工作区没有可提交的变更")

    normalized_body = normalize_commit_body_lines(body)
    commit_args = ["commit", "-m", (subject or "").strip()]
    if normalized_body:
        commit_args.extend(["-m", "\n".join(_format_commit_body_lines(normalized_body))])
    _run_git(repo_root, commit_args, timeout=60)

    commit_hash = _run_git(repo_root, ["rev-parse", "HEAD"])
    short_hash = _run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    final_inspect = inspect_git_repository(str(repo_root))
    return {
        "cwd": str(repo_root),
        "repo_root": str(repo_root),
        "branch": final_inspect["branch"],
        "commit_hash": commit_hash.strip(),
        "short_hash": short_hash.strip(),
        "summary": (subject or "").strip(),
        "full_message": format_git_commit_message(subject, normalized_body),
        "clean": final_inspect["clean"],
        "status_lines": final_inspect["status_lines"],
    }
