"""Local filesystem side of official-client transfers.

The official client downloads into its configured directory without asking for
a path.  Callers snapshot that directory before submitting, then call
:func:`collect_download` repeatedly (one observation per call) until it
reports a terminal status.  A file is collected only when it is new relative
to the snapshot, matches the expected name and size, is unchanged between two
observations, and matches the expected SHA-256 when one is given.  Name and
size alone only make a candidate; content identity needs a hash.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

# Official client in-progress artefacts, e.g. ``a.zip.baiduyun.p.downloading``.
_PARTIAL_RE = re.compile(r"\.baiduyun\.|\.downloading$|\.downloading\.cfg$", re.I)
# The client renames collisions to ``name(1).ext``.
_COPY_SUFFIX_RE = re.compile(r"^(?P<stem>.*?)(?:\(\d+\))?$")


@dataclass(frozen=True)
class FileFact:
    rel: str
    size: int
    mtime_ns: int

    @property
    def token(self) -> str:
        return f"{self.rel}|{self.size}|{self.mtime_ns}"


@dataclass
class CollectResult:
    """``status`` is terminal for collected/hash_mismatch/ambiguous/target_exists."""

    status: str
    next: str
    candidate: FileFact | None = None
    stable_token: str | None = None
    target_path: str | None = None
    sha256: str | None = None
    partials: list[str] = field(default_factory=list)
    # already_collected: the new client copy left in the download dir; same
    # SHA-256 as the target.  The caller decides whether to remove it.
    duplicate_source_path: str | None = None
    target_sha256: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["candidate"] = asdict(self.candidate) if self.candidate else None
        return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def snapshot_dir(root: str | os.PathLike) -> dict[str, FileFact]:
    base = Path(root)
    if not base.is_dir():
        raise FileNotFoundError(f"下载目录不存在：{base}")
    facts: dict[str, FileFact] = {}
    for path in base.rglob("*"):
        try:
            if path.is_file():
                stat = path.stat()
                rel = path.relative_to(base).as_posix()
                facts[rel] = FileFact(rel, stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue  # vanished or locked during the walk
    return facts


def write_manifest(path: str | os.PathLike, root: str | os.PathLike, facts: dict[str, FileFact]) -> None:
    payload = {"root": os.fspath(Path(root).resolve()), "files": [asdict(f) for f in facts.values()]}
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_manifest(path: str | os.PathLike) -> tuple[Path, dict[str, FileFact]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    files = {item["rel"]: FileFact(**item) for item in payload["files"]}
    return Path(payload["root"]), files


def _same_logical_name(rel: str, expected_name: str) -> bool:
    name = PurePosixPath(rel).name
    if name == expected_name:
        return True
    want, have = PurePosixPath(expected_name), PurePosixPath(name)
    if want.suffix.lower() != have.suffix.lower():
        return False
    return _COPY_SUFFIX_RE.match(have.stem).group("stem") == want.stem


def collect_download(
    *,
    download_root: str | os.PathLike,
    before: dict[str, FileFact],
    expected_name: str,
    expected_size: int,
    target_dir: str | os.PathLike,
    expected_sha256: str | None = None,
    stable_token: str | None = None,
) -> CollectResult:
    """One observation step; never resubmits, never overwrites."""

    validate_file_name(expected_name)
    root = Path(download_root)
    after = snapshot_dir(root)
    fresh = [f for rel, f in after.items() if before.get(rel) != f]
    partials = [f.rel for f in fresh if _PARTIAL_RE.search(f.rel) and _same_logical_name(
        _PARTIAL_RE.split(f.rel)[0], expected_name)]
    candidates = [
        f for f in fresh
        if not _PARTIAL_RE.search(f.rel) and f.size == expected_size and _same_logical_name(f.rel, expected_name)
    ]
    if not candidates:
        status = "in_progress" if partials else "not_found"
        return CollectResult(status, "observe_again_then_read_client_tasks", partials=partials)
    if len(candidates) > 1:
        return CollectResult("ambiguous", "manual_review", partials=partials)
    candidate = candidates[0]
    if stable_token != candidate.token:
        return CollectResult("unstable", "observe_again_with_stable_token", candidate, candidate.token)

    source = root / candidate.rel
    digest = sha256_file(source)
    if expected_sha256 and digest != expected_sha256.upper():
        return CollectResult("hash_mismatch", "manual_review", candidate, candidate.token, sha256=digest)

    target = Path(target_dir) / expected_name
    if target.resolve().parent != Path(target_dir).resolve():
        raise ValueError(f"目标路径越出业务目录：{target}")
    if target.exists():
        target_digest = sha256_file(target) if target.is_file() else None
        if target_digest == digest:
            return CollectResult("already_collected", "caller_may_remove_duplicate_source", candidate, candidate.token,
                                 os.fspath(target), digest, duplicate_source_path=os.fspath(source),
                                 target_sha256=target_digest)
        return CollectResult("target_exists", "manual_review", candidate, candidate.token, os.fspath(target), digest,
                             target_sha256=target_digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    _move_no_overwrite(source, target, digest)
    return CollectResult("collected", "done", candidate, candidate.token, os.fspath(target), digest)


# Windows: drive prefixes ("C:x"), streams ("a:s"), wildcards and device names
# would resolve outside, or beside, the business directory.
_FILE_NAME_BAD = re.compile(r'[\\/\x00-\x1f<>:"|?*]')
_WINDOWS_DEVICE_RE = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.I)


def validate_file_name(name: str) -> str:
    """A single plain file name that stays inside its directory on Windows and POSIX."""

    value = str(name or "")
    if not value or value in {".", ".."} or _FILE_NAME_BAD.search(value) or value != value.strip(" ") \
            or value.endswith(".") or _WINDOWS_DEVICE_RE.match(value) or len(value) > 255:
        raise ValueError(f"expected_name 必须是单个合法文件名：{name!r}")
    return value


def _move_no_overwrite(source: Path, target: Path, digest: str) -> None:
    try:
        os.rename(source, target)  # Windows rename refuses to replace an existing file
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
    with open(source, "rb") as src, open(target, "xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    if sha256_file(target) != digest:
        target.unlink()
        raise OSError("跨卷复制后哈希不一致，已撤销目标副本")
    source.unlink()


_REMOTE_BAD = re.compile(r"[\\\x00-\x1f<>:\"|?*]")


def validate_remote_dir(remote_dir: str) -> str:
    """Normalise an absolute netdisk directory such as ``/CodeYun自动化测试/run-1``."""

    value = str(remote_dir or "").strip()
    parts = [p for p in value.split("/") if p]
    if not value.startswith("/") or _REMOTE_BAD.search(value) or any(p in {".", ".."} for p in parts):
        raise ValueError(f"网盘目录必须是不含 .. 和非法字符的绝对路径：{remote_dir!r}")
    return "/" + "/".join(parts)


def describe_upload_source(path: str | os.PathLike, remote_dir: str) -> dict:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"上传源不是文件：{source}")
    remote = validate_remote_dir(remote_dir)
    return {
        "local_path": os.fspath(source.resolve()),
        "name": source.name,
        "size": source.stat().st_size,
        "sha256": sha256_file(source),
        "remote_dir": remote,
        "remote_path": f"{remote.rstrip('/')}/{source.name}",
    }
