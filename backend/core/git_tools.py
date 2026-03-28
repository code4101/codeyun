from __future__ import annotations

import shutil
import subprocess
from collections import defaultdict
from pathlib import Path


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

MAX_CONTEXT_CHARS = 18_000
MAX_FILE_SECTION_CHARS = 4_000
MAX_REDUCTION_UNIT_CHARS = 2_400
MAX_PREVIEW_BYTES = 8_192
MAX_PREVIEW_LINES = 120
SPLIT_RECOMMENDED_FILE_THRESHOLD = 40
SPLIT_RECOMMENDED_LINE_THRESHOLD = 8_000
OVERSIZED_FILE_THRESHOLD = 80
OVERSIZED_LINE_THRESHOLD = 20_000
MAX_SPLIT_GROUPS = 6
MAX_GROUP_SAMPLE_PATHS = 3
MAX_OVERVIEW_PATHS = 20
UNTRACKED_LINE_COUNT_CAP = OVERSIZED_LINE_THRESHOLD + 1_000


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
        completed = subprocess.run(
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
    estimated_changed_line_count = _estimate_changed_line_count(repo_root, untracked_paths=untracked_paths)
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
        "suggested_split_groups": _build_split_groups(changed_paths),
        **scope_summary,
    }


def _read_git_path_set(repo_root: Path, args: list[str]) -> set[str]:
    output = _run_git(repo_root, args, timeout=20, check=False)
    if "\0" in output:
        return {item.strip() for item in output.split("\0") if item.strip()}
    return {line.strip() for line in output.splitlines() if line.strip()}


def _parse_numstat_total(output: str) -> int:
    total = 0
    for raw_line in output.splitlines():
        parts = raw_line.strip().split("\t", 2)
        if len(parts) < 3:
            continue
        added_text, deleted_text, _ = parts
        if added_text.isdigit():
            total += int(added_text)
        if deleted_text.isdigit():
            total += int(deleted_text)
    return total


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


def _estimate_changed_line_count(repo_root: Path, *, untracked_paths: set[str]) -> int:
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
    return _parse_numstat_total(unstaged) + _parse_numstat_total(staged) + _estimate_untracked_line_count(repo_root, untracked_paths)


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
) -> tuple[str, bool]:
    args = ["--no-pager", "diff", "--unified=2", "--find-renames", "--no-color"]
    if staged:
        args.append("--cached")
    args.extend(["--", path])
    diff_text = _run_git(repo_root, args, timeout=20, check=False)
    if not diff_text.strip():
        return "", False
    limited, truncated = _limit_text(diff_text)
    title = "已暂存差异" if staged else "未暂存差异"
    return f"[{title}]\n{limited}", truncated


def _looks_binary(path: Path, data: bytes) -> bool:
    if path.suffix.lower() in BINARY_LIKE_SUFFIXES:
        return True
    return b"\x00" in data


def _build_untracked_preview(repo_root: Path, path: str) -> tuple[str, bool]:
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
    limited, was_truncated = _limit_text(preview_text)
    return f"[未跟踪文件预览]\n{limited}", truncated or was_truncated


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
        if line.startswith(("-", "*", "•")):
            line = line[1:].strip()
        normalized.append(line)
    return normalized


def format_git_commit_message(subject: str, body: list[str]) -> str:
    normalized_subject = (subject or "").strip()
    if not normalized_subject:
        raise GitToolError("提交标题不能为空")

    normalized_body = normalize_commit_body_lines(body)
    if not normalized_body:
        return normalized_subject

    bullet_lines = [f"- {line}" for line in normalized_body]
    return normalized_subject + "\n\n" + "\n".join(bullet_lines)


def create_git_commit(
    cwd: str,
    *,
    subject: str,
    body: list[str],
    add_all: bool = True,
) -> dict[str, object]:
    _, repo_root = _resolve_repo_root(cwd)
    if add_all:
        _run_git(repo_root, ["add", "-A"], timeout=30)

    post_add_inspect = inspect_git_repository(str(repo_root))
    if bool(post_add_inspect["clean"]):
        raise GitToolError("当前工作区没有可提交的变更")

    normalized_body = normalize_commit_body_lines(body)
    commit_args = ["commit", "-m", (subject or "").strip()]
    if normalized_body:
        commit_args.extend(["-m", "\n".join(f"- {line}" for line in normalized_body)])
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
