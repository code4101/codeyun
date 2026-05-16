from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from backend.core.storage_usage import collect_directory_usage


MiB = 1024 * 1024
GiB = 1024 * MiB

SOURCE_CACHE_DIRS = (
    ".codex-run",
    ".codex-logs",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".uv-cache",
    ".tmp-dev-observe",
    ".tmp_git_reduce_debug",
    "dist",
    "build",
    "htmlcov",
    "frontend/dist",
)
SOURCE_REBUILDABLE_DIRS = (
    ".venv",
    "frontend/node_modules",
)
SOURCE_DATA_DIRS = (
    "backend/data",
    "backend/static/uploads",
    "backend/static/attachments",
    "attachments",
    "uploads",
)
DATA_CACHE_DIRS = (
    "tmp",
    "temp",
    ".tmp",
    "cache",
    ".cache",
    "logs",
    "trash",
    ".trash",
)
DATA_SOURCE_MARKERS = (
    ".git",
    "backend",
    "frontend",
    "pyproject.toml",
    "package.json",
)
RESOURCE_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bmp",
    ".doc",
    ".docx",
    ".flac",
    ".gif",
    ".jpg",
    ".jpeg",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".wav",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
SOURCE_RESOURCE_SCAN_SKIP_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    ".codex-run",
    ".codex",
    ".codex-logs",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    "__pycache__",
}


@dataclass(slots=True)
class StorageHealthIssue:
    id: str
    severity: str
    title: str
    detail: str
    path: str = ""
    size_bytes: int = 0
    action_label: str = ""
    action_kind: str = "inspect"


@dataclass(slots=True)
class StorageSlimmingCandidate:
    id: str
    category: str
    title: str
    path: str
    logical_size_bytes: int
    allocated_size_bytes: int
    file_count: int = 0
    directory_count: int = 0
    risk: str = "review"
    cleanup_kind: str = "inspect"
    action_label: str = "查看"
    detail: str = ""


@dataclass(slots=True)
class StorageHealthReport:
    scope: str
    label: str
    root_path: str
    expected_role: str
    health_score: int = 100
    health_status: str = "healthy"
    issues: list[StorageHealthIssue] = field(default_factory=list)
    slimming_candidates: list[StorageSlimmingCandidate] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _path_str(path: str | Path) -> str:
    return os.fspath(_resolved(path))


def _child_path(root: Path, relative_path: str) -> Path:
    return root.joinpath(*relative_path.replace("\\", "/").split("/"))


def _relative_id(relative_path: str) -> str:
    return relative_path.replace("\\", "/").replace("/", "__").replace(".", "_").strip("_") or "root"


def _usage_for_path(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return collect_directory_usage(path, top_limit=0).to_dict()


def _candidate_from_usage(
    *,
    candidate_id: str,
    category: str,
    title: str,
    usage: dict[str, Any],
    risk: str,
    cleanup_kind: str,
    action_label: str,
    detail: str,
) -> StorageSlimmingCandidate:
    return StorageSlimmingCandidate(
        id=candidate_id,
        category=category,
        title=title,
        path=str(usage.get("root_path") or ""),
        logical_size_bytes=int(usage.get("logical_size_bytes") or 0),
        allocated_size_bytes=int(usage.get("allocated_size_bytes") or 0),
        file_count=int(usage.get("file_count") or 0),
        directory_count=int(usage.get("directory_count") or 0),
        risk=risk,
        cleanup_kind=cleanup_kind,
        action_label=action_label,
        detail=detail,
    )


def _add_candidate_for_path(
    candidates: list[StorageSlimmingCandidate],
    *,
    root: Path,
    relative_path: str,
    category: str,
    title: str,
    risk: str,
    cleanup_kind: str,
    action_label: str,
    detail: str,
) -> StorageSlimmingCandidate | None:
    path = _child_path(root, relative_path)
    usage = _usage_for_path(path)
    if usage is None:
        return None
    candidate = _candidate_from_usage(
        candidate_id=f"{category}:{_relative_id(relative_path)}",
        category=category,
        title=title,
        usage=usage,
        risk=risk,
        cleanup_kind=cleanup_kind,
        action_label=action_label,
        detail=detail,
    )
    candidates.append(candidate)
    return candidate


def _issue(
    *,
    issue_id: str,
    severity: str,
    title: str,
    detail: str,
    path: str = "",
    size_bytes: int = 0,
    action_label: str = "",
    action_kind: str = "inspect",
) -> StorageHealthIssue:
    return StorageHealthIssue(
        id=issue_id,
        severity=severity,
        title=title,
        detail=detail,
        path=path,
        size_bytes=size_bytes,
        action_label=action_label,
        action_kind=action_kind,
    )


def _score_for_issues(issues: Iterable[StorageHealthIssue]) -> int:
    penalty_by_severity = {
        "critical": 30,
        "warning": 14,
        "info": 4,
    }
    score = 100
    for item in issues:
        score -= penalty_by_severity.get(item.severity, 0)
    return max(0, min(100, score))


def _status_for_score(score: int) -> str:
    if score >= 90:
        return "healthy"
    if score >= 70:
        return "attention"
    return "problem"


def _is_relative_to(path: Path, parent: Path | None) -> bool:
    if parent is None:
        return False
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _collect_large_resource_files(root: Path, *, minimum_size: int = 10 * MiB) -> tuple[int, int, list[Path]]:
    total_size = 0
    total_count = 0
    largest: list[tuple[int, Path]] = []

    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [
            name
            for name in dir_names
            if name not in SOURCE_RESOURCE_SCAN_SKIP_NAMES
        ]
        current = Path(current_root)
        if "frontend" in current.parts and "dsp-calc" in current.parts:
            dir_names[:] = []
            continue

        for file_name in file_names:
            suffix = Path(file_name).suffix.lower()
            if suffix not in RESOURCE_EXTENSIONS:
                continue
            path = current / file_name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < minimum_size:
                continue
            total_count += 1
            total_size += size
            largest.append((size, path))

    largest.sort(key=lambda item: item[0], reverse=True)
    return total_count, total_size, [path for _, path in largest[:10]]


def _add_source_health_rules(
    report: StorageHealthReport,
    *,
    root: Path,
    data_workspace_path: Path | None,
) -> None:
    for relative_path in SOURCE_DATA_DIRS:
        candidate = _add_candidate_for_path(
            report.slimming_candidates,
            root=root,
            relative_path=relative_path,
            category="misplaced_data",
            title=f"源码目录内的数据目录：{relative_path}",
            risk="review",
            cleanup_kind="move_to_data_workspace",
            action_label="迁到数据工作区",
            detail="源码目录不应承载附件、上传文件或运行数据；迁移前需要确认引用路径和服务配置。",
        )
        if candidate is not None:
            destination = os.fspath(data_workspace_path) if data_workspace_path is not None else "数据工作区"
            report.issues.append(_issue(
                issue_id=f"source_data_dir:{_relative_id(relative_path)}",
                severity="critical",
                title="源码目录混入数据资源",
                detail=f"{relative_path} 应迁到 {destination}，避免仓库体积和备份边界失控。",
                path=candidate.path,
                size_bytes=candidate.allocated_size_bytes,
                action_label="迁移",
                action_kind="move_to_data_workspace",
            ))

    for relative_path in SOURCE_CACHE_DIRS:
        candidate = _add_candidate_for_path(
            report.slimming_candidates,
            root=root,
            relative_path=relative_path,
            category="generated_cache",
            title=f"可重建产物：{relative_path}",
            risk="low",
            cleanup_kind="delete_rebuildable",
            action_label="确认后删除",
            detail="这类目录通常由运行、测试或构建过程生成；清理前确认没有正在运行的任务依赖它。",
        )
        if candidate is not None and candidate.allocated_size_bytes >= 256 * MiB:
            report.issues.append(_issue(
                issue_id=f"source_cache:{_relative_id(relative_path)}",
                severity="warning",
                title="源码目录存在较大的运行产物",
                detail=f"{relative_path} 占用偏大，可以作为首批瘦身对象。",
                path=candidate.path,
                size_bytes=candidate.allocated_size_bytes,
                action_label="清理",
                action_kind="delete_rebuildable",
            ))

    for relative_path in SOURCE_REBUILDABLE_DIRS:
        candidate = _add_candidate_for_path(
            report.slimming_candidates,
            root=root,
            relative_path=relative_path,
            category="dependency_cache",
            title=f"依赖目录：{relative_path}",
            risk="low",
            cleanup_kind="delete_and_reinstall",
            action_label="需要时重建",
            detail="依赖目录会让源码工作区变大，但通常可通过依赖安装命令重建；不应提交或当作业务数据备份。",
        )
        if candidate is not None and candidate.allocated_size_bytes >= 512 * MiB:
            report.issues.append(_issue(
                issue_id=f"source_dependency:{_relative_id(relative_path)}",
                severity="info",
                title="依赖目录占用较大",
                detail=f"{relative_path} 是可重建依赖，不是附件或业务数据；磁盘紧张时可清理后重装。",
                path=candidate.path,
                size_bytes=candidate.allocated_size_bytes,
                action_label="重建",
                action_kind="delete_and_reinstall",
            ))

    log_candidates = []
    for pattern in ("*.log", "backend/*.log", "frontend/*.log"):
        log_candidates.extend(root.glob(pattern))
    for log_path in sorted({path.resolve(strict=False) for path in log_candidates}):
        usage = _usage_for_path(log_path)
        if usage is None:
            continue
        report.slimming_candidates.append(_candidate_from_usage(
            candidate_id=f"log_file:{_relative_id(os.fspath(log_path.relative_to(root)))}",
            category="log_file",
            title=f"日志文件：{log_path.name}",
            usage=usage,
            risk="low",
            cleanup_kind="delete_log",
            action_label="确认后删除",
            detail="日志文件可用于排障；确认近期不需要追溯后可以清理。",
        ))

    resource_count, resource_size, largest_paths = _collect_large_resource_files(root)
    if resource_count:
        largest_display = ", ".join(path.name for path in largest_paths[:3])
        report.slimming_candidates.append(StorageSlimmingCandidate(
            id="misplaced_resource_files:large_resources",
            category="misplaced_resource_files",
            title="源码目录内可疑大资源文件",
            path=os.fspath(root),
            logical_size_bytes=resource_size,
            allocated_size_bytes=resource_size,
            file_count=resource_count,
            risk="review",
            cleanup_kind="inspect_and_move",
            action_label="逐项确认",
            detail=f"发现 {resource_count} 个较大的媒体、文档或压缩包；较大的文件包括 {largest_display}。",
        ))
        report.issues.append(_issue(
            issue_id="source_large_resources",
            severity="warning",
            title="源码目录存在可疑大资源文件",
            detail="源码目录原则上只放源码、配置和必要静态资产；运行附件和资料包应转入数据工作区。",
            path=os.fspath(root),
            size_bytes=resource_size,
            action_label="检查",
            action_kind="inspect_and_move",
        ))


def _add_data_workspace_health_rules(
    report: StorageHealthReport,
    *,
    root: Path,
    attachments_dir: Path | None,
) -> None:
    for marker in DATA_SOURCE_MARKERS:
        marker_path = _child_path(root, marker)
        if not marker_path.exists():
            continue
        usage = _usage_for_path(marker_path)
        size = int((usage or {}).get("allocated_size_bytes") or 0)
        report.issues.append(_issue(
            issue_id=f"data_source_marker:{_relative_id(marker)}",
            severity="warning",
            title="数据工作区混入源码结构",
            detail=f"{marker} 看起来像项目源码内容，建议确认是否放错位置。",
            path=os.fspath(marker_path.resolve(strict=False)),
            size_bytes=size,
            action_label="检查",
            action_kind="inspect",
        ))
        if usage is not None:
            report.slimming_candidates.append(_candidate_from_usage(
                candidate_id=f"misplaced_source:{_relative_id(marker)}",
                category="misplaced_source",
                title=f"疑似源码内容：{marker}",
                usage=usage,
                risk="review",
                cleanup_kind="inspect",
                action_label="检查",
                detail="数据工作区应主要承载运行数据、附件和备份；源码目录应回到项目仓库。",
            ))

    for relative_path in DATA_CACHE_DIRS:
        candidate = _add_candidate_for_path(
            report.slimming_candidates,
            root=root,
            relative_path=relative_path,
            category="data_cache",
            title=f"数据工作区缓存：{relative_path}",
            risk="review",
            cleanup_kind="delete_cache",
            action_label="确认后删除",
            detail="缓存、临时文件和回收站可以成为瘦身对象；删除前确认没有任务正在使用。",
        )
        if candidate is not None and candidate.allocated_size_bytes >= 256 * MiB:
            report.issues.append(_issue(
                issue_id=f"data_cache:{_relative_id(relative_path)}",
                severity="warning",
                title="数据工作区缓存占用较大",
                detail=f"{relative_path} 占用偏大，建议按用途确认是否可清理。",
                path=candidate.path,
                size_bytes=candidate.allocated_size_bytes,
                action_label="清理",
                action_kind="delete_cache",
            ))

    backups = _add_candidate_for_path(
        report.slimming_candidates,
        root=root,
        relative_path="backups",
        category="backup",
        title="备份目录",
        risk="review",
        cleanup_kind="review_backup_retention",
        action_label="检查保留策略",
        detail="备份属于数据工作区的合理内容，但应有保留策略，避免长期无限增长。",
    )
    if backups is not None and backups.allocated_size_bytes >= 1 * GiB:
        report.issues.append(_issue(
            issue_id="data_backups_large",
            severity="info",
            title="备份目录占用较大",
            detail="建议保留最近可恢复版本，把旧备份迁移到归档盘或删除。",
            path=backups.path,
            size_bytes=backups.allocated_size_bytes,
            action_label="归档",
            action_kind="review_backup_retention",
        ))

    if attachments_dir is not None and _is_relative_to(attachments_dir, root):
        usage = _usage_for_path(attachments_dir)
        if usage is not None:
            report.slimming_candidates.append(_candidate_from_usage(
                candidate_id="attachments:active_attachment_store",
                category="attachments",
                title="当前附件目录",
                usage=usage,
                risk="review",
                cleanup_kind="optimize_orphans",
                action_label="查孤儿和大文件",
                detail="附件目录属于数据工作区，但仍需要配合孤儿文件、大文件和图片压缩做持续瘦身。",
            ))


def build_storage_health_report(
    *,
    scope: str,
    label: str,
    root_path: str | Path,
    usage: dict[str, Any] | None = None,
    data_workspace_path: str | Path | None = None,
    attachments_dir: str | Path | None = None,
) -> StorageHealthReport:
    root = _resolved(root_path)
    data_workspace = _resolved(data_workspace_path) if data_workspace_path is not None else None
    attachments = _resolved(attachments_dir) if attachments_dir is not None else None
    normalized_scope = (scope or "").strip().lower().replace("-", "_")
    expected_role = (
        "运行数据、附件和备份的真实工作区"
        if normalized_scope == "data_workspace"
        else "项目源码、配置、依赖锁文件和可重建开发依赖"
    )
    report = StorageHealthReport(
        scope=normalized_scope,
        label=label,
        root_path=_path_str(root),
        expected_role=expected_role,
    )

    if usage and int(usage.get("inaccessible_count") or 0) > 0:
        report.issues.append(_issue(
            issue_id="inaccessible_entries",
            severity="warning",
            title="存在无法统计的目录项",
            detail="部分文件或目录无法访问，当前占用和瘦身候选可能不完整。",
            path=os.fspath(root),
            size_bytes=0,
            action_label="检查权限",
            action_kind="inspect",
        ))

    if normalized_scope == "source_dir":
        _add_source_health_rules(
            report,
            root=root,
            data_workspace_path=data_workspace,
        )
    else:
        _add_data_workspace_health_rules(
            report,
            root=root,
            attachments_dir=attachments,
        )

    report.slimming_candidates.sort(
        key=lambda item: (
            -item.allocated_size_bytes,
            item.risk,
            item.title,
        )
    )
    report.health_score = _score_for_issues(report.issues)
    report.health_status = _status_for_score(report.health_score)
    return report
