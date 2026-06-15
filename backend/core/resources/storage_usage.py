from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DirectoryUsageEntry:
    name: str
    path: str
    is_dir: bool
    logical_size_bytes: int = 0
    allocated_size_bytes: int = 0
    file_count: int = 0
    directory_count: int = 0
    symlink_count: int = 0
    inaccessible_count: int = 0
    modified_at: float | None = None


@dataclass(slots=True)
class DirectoryUsageSummary:
    root_path: str
    logical_size_bytes: int = 0
    allocated_size_bytes: int = 0
    file_count: int = 0
    directory_count: int = 0
    symlink_count: int = 0
    inaccessible_count: int = 0
    top_entries: list[DirectoryUsageEntry] = field(default_factory=list)
    scan_started_at: float = 0
    elapsed_ms: int = 0
    source: str = "filesystem_scan"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _UsageTotals:
    logical_size_bytes: int = 0
    allocated_size_bytes: int = 0
    file_count: int = 0
    directory_count: int = 0
    symlink_count: int = 0
    inaccessible_count: int = 0
    modified_at: float | None = None

    def add(self, other: "_UsageTotals") -> None:
        self.logical_size_bytes += other.logical_size_bytes
        self.allocated_size_bytes += other.allocated_size_bytes
        self.file_count += other.file_count
        self.directory_count += other.directory_count
        self.symlink_count += other.symlink_count
        self.inaccessible_count += other.inaccessible_count
        if other.modified_at is not None:
            self.modified_at = max(self.modified_at or 0, other.modified_at)

    def touch(self, modified_at: float | None) -> None:
        if modified_at is not None:
            self.modified_at = max(self.modified_at or 0, modified_at)


_WINDOWS_KERNEL32: Any | None = None
_WINDOWS_KERNEL32_UNAVAILABLE = False
_WINDOWS_CLUSTER_SIZE_BY_ROOT: dict[str, int | None] = {}
_INVALID_FILE_SIZE = 0xFFFFFFFF
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_FILE_ATTRIBUTE_SPARSE_FILE = 0x00000200
_FILE_ATTRIBUTE_COMPRESSED = 0x00000800


def _get_windows_kernel32() -> Any | None:
    global _WINDOWS_KERNEL32, _WINDOWS_KERNEL32_UNAVAILABLE

    if os.name != "nt" or _WINDOWS_KERNEL32_UNAVAILABLE:
        return None
    if _WINDOWS_KERNEL32 is not None:
        return _WINDOWS_KERNEL32

    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCompressedFileSizeW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetCompressedFileSizeW.restype = ctypes.c_ulong
        kernel32.GetDiskFreeSpaceW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetDiskFreeSpaceW.restype = ctypes.c_bool
        kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetFileAttributesW.restype = ctypes.c_ulong
    except Exception:
        _WINDOWS_KERNEL32_UNAVAILABLE = True
        return None

    _WINDOWS_KERNEL32 = kernel32
    return kernel32


def _windows_compressed_file_size(path: Path) -> int | None:
    kernel32 = _get_windows_kernel32()
    if kernel32 is None:
        return None

    try:
        import ctypes

        ctypes.set_last_error(0)
        high_size = ctypes.c_ulong(0)
        low_size = kernel32.GetCompressedFileSizeW(os.fspath(path), ctypes.byref(high_size))
        last_error = ctypes.get_last_error()
        if low_size == _INVALID_FILE_SIZE and last_error != 0:
            return None
        return int((high_size.value << 32) + low_size)
    except Exception:
        return None


def _windows_cluster_size(path: Path) -> int | None:
    kernel32 = _get_windows_kernel32()
    if kernel32 is None:
        return None

    root_name = Path(path).anchor
    if not root_name:
        return None
    if root_name in _WINDOWS_CLUSTER_SIZE_BY_ROOT:
        return _WINDOWS_CLUSTER_SIZE_BY_ROOT[root_name]

    try:
        import ctypes

        sectors_per_cluster = ctypes.c_ulong(0)
        bytes_per_sector = ctypes.c_ulong(0)
        free_clusters = ctypes.c_ulong(0)
        total_clusters = ctypes.c_ulong(0)
        ok = kernel32.GetDiskFreeSpaceW(
            root_name,
            ctypes.byref(sectors_per_cluster),
            ctypes.byref(bytes_per_sector),
            ctypes.byref(free_clusters),
            ctypes.byref(total_clusters),
        )
        if not ok:
            _WINDOWS_CLUSTER_SIZE_BY_ROOT[root_name] = None
            return None
        cluster_size = int(sectors_per_cluster.value * bytes_per_sector.value)
        _WINDOWS_CLUSTER_SIZE_BY_ROOT[root_name] = cluster_size if cluster_size > 0 else None
        return _WINDOWS_CLUSTER_SIZE_BY_ROOT[root_name]
    except Exception:
        _WINDOWS_CLUSTER_SIZE_BY_ROOT[root_name] = None
        return None


def _windows_file_attributes(path: Path) -> int | None:
    kernel32 = _get_windows_kernel32()
    if kernel32 is None:
        return None

    try:
        attributes = int(kernel32.GetFileAttributesW(os.fspath(path)))
    except Exception:
        return None
    if attributes == _INVALID_FILE_ATTRIBUTES:
        return None
    return attributes


def _windows_allocated_size(path: Path, stat_result: os.stat_result) -> int | None:
    if os.name != "nt":
        return None

    logical_size = int(stat_result.st_size)
    if logical_size <= 0:
        return 0

    attributes = _windows_file_attributes(path)
    if attributes is not None and attributes & (_FILE_ATTRIBUTE_COMPRESSED | _FILE_ATTRIBUTE_SPARSE_FILE):
        compressed_size = _windows_compressed_file_size(path)
        if compressed_size is not None:
            return compressed_size

    cluster_size = _windows_cluster_size(path)
    if cluster_size:
        return ((logical_size + cluster_size - 1) // cluster_size) * cluster_size

    return _windows_compressed_file_size(path)


def _allocated_size(path: Path, stat_result: os.stat_result) -> int:
    windows_size = _windows_allocated_size(path, stat_result)
    if windows_size is not None:
        return windows_size

    block_count = getattr(stat_result, "st_blocks", None)
    if isinstance(block_count, int) and block_count > 0:
        return int(block_count * 512)

    return int(stat_result.st_size)


def _file_size_should_count(stat_result: os.stat_result, seen_file_ids: set[tuple[int, int]]) -> bool:
    inode = int(getattr(stat_result, "st_ino", 0) or 0)
    device = int(getattr(stat_result, "st_dev", 0) or 0)
    link_count = int(getattr(stat_result, "st_nlink", 1) or 1)
    if link_count <= 1 or inode == 0:
        return True

    file_id = (device, inode)
    if file_id in seen_file_ids:
        return False
    seen_file_ids.add(file_id)
    return True


def _identity_key(stat_result: os.stat_result) -> tuple[int, int] | None:
    inode = int(getattr(stat_result, "st_ino", 0) or 0)
    device = int(getattr(stat_result, "st_dev", 0) or 0)
    if inode == 0:
        return None
    return (device, inode)


def _scan_entry(
    entry: os.DirEntry[str],
    seen_file_ids: set[tuple[int, int]],
    seen_dir_ids: set[tuple[int, int]],
) -> _UsageTotals:
    totals = _UsageTotals()
    entry_path = Path(entry.path)

    try:
        stat_result = entry.stat(follow_symlinks=False)
        is_symlink = entry.is_symlink()
        is_file = entry.is_file(follow_symlinks=False)
        is_dir = entry.is_dir(follow_symlinks=False)
    except OSError:
        totals.inaccessible_count += 1
        return totals

    totals.touch(float(stat_result.st_mtime))

    if is_symlink:
        totals.symlink_count += 1
        return totals

    if is_file:
        totals.file_count += 1
        if _file_size_should_count(stat_result, seen_file_ids):
            totals.logical_size_bytes += int(stat_result.st_size)
            totals.allocated_size_bytes += _allocated_size(entry_path, stat_result)
        return totals

    if not is_dir:
        return totals

    totals.directory_count += 1
    dir_id = _identity_key(stat_result)
    if dir_id is not None:
        if dir_id in seen_dir_ids:
            return totals
        seen_dir_ids.add(dir_id)

    try:
        with os.scandir(entry_path) as children:
            for child in children:
                totals.add(_scan_entry(child, seen_file_ids, seen_dir_ids))
    except OSError:
        totals.inaccessible_count += 1

    return totals


def _entry_from_totals(entry: os.DirEntry[str], totals: _UsageTotals) -> DirectoryUsageEntry:
    try:
        is_dir = entry.is_dir(follow_symlinks=False)
    except OSError:
        is_dir = False
    return DirectoryUsageEntry(
        name=entry.name,
        path=os.fspath(Path(entry.path).resolve(strict=False)),
        is_dir=is_dir,
        logical_size_bytes=totals.logical_size_bytes,
        allocated_size_bytes=totals.allocated_size_bytes,
        file_count=totals.file_count,
        directory_count=totals.directory_count,
        symlink_count=totals.symlink_count,
        inaccessible_count=totals.inaccessible_count,
        modified_at=totals.modified_at,
    )


def _modified_at_seconds(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return float(value / 1000)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _entry_from_treesize_item(item: dict[str, Any]) -> DirectoryUsageEntry | None:
    name = str(item.get("name") or "")
    if not name:
        return None

    is_dir = bool(item.get("is_dir"))
    size_value = _int_or_none(item.get("recursive_total_bytes") if is_dir else item.get("size"))
    if size_value is None:
        return None

    recursive_file_count = _int_or_none(item.get("recursive_file_count"))
    return DirectoryUsageEntry(
        name=name,
        path=str(item.get("path") or ""),
        is_dir=is_dir,
        logical_size_bytes=size_value,
        allocated_size_bytes=size_value,
        file_count=recursive_file_count if is_dir and recursive_file_count is not None else (0 if is_dir else 1),
        directory_count=1 if is_dir else 0,
        modified_at=_modified_at_seconds(item.get("modified_at")),
    )


def _collect_directory_usage_from_treesize(
    root_path: str | Path,
    *,
    top_limit: int,
    session: Any = None,
) -> DirectoryUsageSummary | None:
    root = Path(root_path).expanduser().resolve(strict=False)
    if not root.is_dir():
        return None

    started_at = time.time()
    try:
        from backend.api.filesystem import list_directory_items

        listing = list_directory_items(
            absolute_path=os.fspath(root),
            sort_program=None,
            session=session,
        )
    except Exception:
        return None

    entries: list[DirectoryUsageEntry] = []
    logical_size_bytes = 0
    file_count = 0
    directory_count = 0
    latest_modified_at: float | None = None

    for item in listing.get("items") or []:
        entry = _entry_from_treesize_item(item)
        if entry is None:
            return None
        entries.append(entry)
        logical_size_bytes += entry.logical_size_bytes
        file_count += entry.file_count
        directory_count += 1 if entry.is_dir else 0
        if entry.modified_at is not None:
            latest_modified_at = max(latest_modified_at or 0, entry.modified_at)

    entries.sort(
        key=lambda item: (
            -item.allocated_size_bytes,
            -item.logical_size_bytes,
            -item.file_count,
            item.name.lower(),
        )
    )

    normalized_top_limit = max(0, int(top_limit or 0))
    return DirectoryUsageSummary(
        root_path=os.fspath(root),
        logical_size_bytes=logical_size_bytes,
        allocated_size_bytes=logical_size_bytes,
        file_count=file_count,
        directory_count=directory_count,
        top_entries=entries[:normalized_top_limit],
        scan_started_at=started_at,
        elapsed_ms=int((time.time() - started_at) * 1000),
        source="treesize",
    )


def _collect_directory_usage_from_filesystem(root_path: str | Path, *, top_limit: int = 20) -> DirectoryUsageSummary:
    started_at = time.time()
    root = Path(root_path).expanduser().resolve(strict=False)
    normalized_top_limit = max(0, int(top_limit or 0))
    seen_file_ids: set[tuple[int, int]] = set()
    seen_dir_ids: set[tuple[int, int]] = set()
    totals = _UsageTotals()
    entries: list[DirectoryUsageEntry] = []

    try:
        root_stat = root.stat()
    except OSError:
        return DirectoryUsageSummary(
            root_path=os.fspath(root),
            inaccessible_count=1,
            scan_started_at=started_at,
            elapsed_ms=int((time.time() - started_at) * 1000),
        )

    if root.is_file():
        file_totals = _UsageTotals(
            logical_size_bytes=int(root_stat.st_size),
            allocated_size_bytes=_allocated_size(root, root_stat),
            file_count=1,
            modified_at=float(root_stat.st_mtime),
        )
        totals.add(file_totals)
    else:
        totals.touch(float(root_stat.st_mtime))
        root_dir_id = _identity_key(root_stat)
        if root_dir_id is not None:
            seen_dir_ids.add(root_dir_id)
        try:
            with os.scandir(root) as children:
                for child in children:
                    child_totals = _scan_entry(child, seen_file_ids, seen_dir_ids)
                    totals.add(child_totals)
                    entries.append(_entry_from_totals(child, child_totals))
        except OSError:
            totals.inaccessible_count += 1

    entries.sort(
        key=lambda item: (
            -item.allocated_size_bytes,
            -item.logical_size_bytes,
            -item.file_count,
            item.name.lower(),
        )
    )

    return DirectoryUsageSummary(
        root_path=os.fspath(root),
        logical_size_bytes=totals.logical_size_bytes,
        allocated_size_bytes=totals.allocated_size_bytes,
        file_count=totals.file_count,
        directory_count=totals.directory_count,
        symlink_count=totals.symlink_count,
        inaccessible_count=totals.inaccessible_count,
        top_entries=entries[:normalized_top_limit],
        scan_started_at=started_at,
        elapsed_ms=int((time.time() - started_at) * 1000),
    )


def collect_directory_usage(
    root_path: str | Path,
    *,
    top_limit: int = 20,
    session: Any = None,
    prefer_treesize: bool = True,
) -> DirectoryUsageSummary:
    if prefer_treesize:
        treesize_summary = _collect_directory_usage_from_treesize(
            root_path,
            top_limit=top_limit,
            session=session,
        )
        if treesize_summary is not None:
            return treesize_summary

    return _collect_directory_usage_from_filesystem(root_path, top_limit=top_limit)
