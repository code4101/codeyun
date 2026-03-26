from __future__ import annotations

import shutil
import subprocess
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
MAX_PREVIEW_BYTES = 8_192
MAX_PREVIEW_LINES = 120


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
    command = ["git", *args]
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


def _parse_changed_files(status_output: str) -> list[dict[str, object]]:
    changed_files: list[dict[str, object]] = []
    for raw_line in status_output.splitlines():
        line = raw_line.rstrip()
        if not line or len(line) < 3:
            continue

        status = line[:2]
        path = line[3:].strip()
        changed_files.append(
            {
                "path": path,
                "status": status,
                "staged": status[0] not in {" ", "?", "!"},  # type: ignore[dict-item]
                "unstaged": status[1] not in {" ", "?"},  # type: ignore[dict-item]
                "untracked": status == "??",  # type: ignore[dict-item]
            }
        )
    return changed_files


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
    status_output = _run_git(repo_root, ["status", "--short"])
    status_lines = [line.rstrip() for line in status_output.splitlines() if line.strip()]
    changed_files = _parse_changed_files(status_output)
    diff_stat = _run_git(repo_root, ["--no-pager", "diff", "--stat", "--find-renames", "--no-color"], timeout=20, check=False)
    staged_diff_stat = _run_git(repo_root, ["--no-pager", "diff", "--cached", "--stat", "--find-renames", "--no-color"], timeout=20, check=False)

    return {
        "cwd": str(requested_cwd),
        "repo_root": str(repo_root),
        "branch": _parse_branch_name(branch_text, branch_status),
        "branch_status": branch_status,
        "clean": not status_lines,
        "status_lines": status_lines,
        "diff_stat": diff_stat,
        "staged_diff_stat": staged_diff_stat,
        "changed_files": changed_files,
    }


def _read_git_path_set(repo_root: Path, args: list[str]) -> set[str]:
    output = _run_git(repo_root, args, timeout=20, check=False)
    return {line.strip() for line in output.splitlines() if line.strip()}


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


def collect_git_commit_context(cwd: str, *, max_files: int = 8) -> dict[str, object]:
    inspect_payload = inspect_git_repository(cwd)
    if bool(inspect_payload["clean"]):
        raise GitToolError("当前工作区没有可提交的变更")

    repo_root = Path(str(inspect_payload["repo_root"]))
    unstaged_paths = _read_git_path_set(repo_root, ["diff", "--name-only"])
    staged_paths = _read_git_path_set(repo_root, ["diff", "--cached", "--name-only"])
    untracked_paths = _read_git_path_set(repo_root, ["ls-files", "--others", "--exclude-standard"])

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

    prompt_parts = ["\n".join(context_sections), "重点文件变更片段"]
    total_chars = len(prompt_parts[0])
    truncated = False

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
