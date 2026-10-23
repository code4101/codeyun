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
import stat
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


def superseded_partial_siblings(files: dict[str, FileFact], expected_sizes: dict[str, int],
                               *, root: Path | None = None) -> list[str]:
    """Identify retained client artifacts superseded by newer final files.

    Only the measured official ``.baiduyun.p.downloading`` suffix qualifies.
    Both files must be fresh candidates of this operation, match the exact
    expected size. The final must be newer, or the artifact must be proven
    small (at most 1 MiB), stable, entirely zero-filled preallocation while
    its final counterpart contains actual data. Callers still hash and
    stability-check every final file and preserve the artifacts in staging;
    Missing/wrong-sized/unproven counterparts and other partial forms still block.
    Partial bytes are never promoted to final payloads or trusted source hashes.
    """
    suffix = '.baiduyun.p.downloading'
    superseded = []
    for rel, partial in files.items():
        if not rel.endswith(suffix):
            continue
        final_rel = rel[:-len(suffix)]
        final = files.get(final_rel)
        size = expected_sizes.get(final_rel)
        if not (final and size is not None and final.size == partial.size == size):
            continue
        empty_preallocation = False
        if root is not None and 0 < size <= 1024 * 1024:
            path = root / rel
            before = path.lstat()
            if stat.S_ISREG(before.st_mode) and not (getattr(before, 'st_file_attributes', 0) & 0x400):
                data = path.read_bytes()
                after = path.lstat()
                identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
                if identity(before) == identity(after) and (after.st_size, after.st_mtime_ns) == (partial.size, partial.mtime_ns):
                    empty_preallocation = len(data) == size and not any(data) and any((root / final_rel).read_bytes())
        if final.mtime_ns > partial.mtime_ns or empty_preallocation:
            superseded.append(rel)
    return sorted(superseded)


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


# ---- directory transfers ---------------------------------------------------------

# Client list size cell, e.g. ``14B`` / ``1.07KB`` / ``2.3M``; folders show ``-``.
_SIZE_LABEL_RE = re.compile(r"^(\d+(?:\.(\d+))?)\s*([KMGT]?)B?$", re.I)


def size_label_matches(label: object, size: int) -> bool:
    """A rounded list size label covers ``size`` within one display step; unparseable is a mismatch."""
    match = _SIZE_LABEL_RE.match(re.sub(r"\s+", "", str(label or "")))
    if not match:
        return False
    shown, decimals, unit = float(match.group(1)), len(match.group(2) or ""), match.group(3).upper()
    scale = 1024 ** "_KMGT".index(unit) if unit else 1
    return abs(size / scale - shown) <= (10 ** -decimals if unit else 0)  # bytes are exact


def fold_name(name: str) -> str:
    """OCR of the client list drops or spaces out '_'; remote listings compare without whitespace and '_'."""
    return re.sub(r"[\s_]+", "", str(name))


def describe_upload_tree(path: str | os.PathLike, remote_parent: str) -> dict:
    """Local directory -> files (rel, size), dirs, and a digest of that shape (no content hashes)."""
    root = Path(path)
    if not root.is_dir():
        raise NotADirectoryError(f"上传源不是目录：{root}")
    root = root.resolve()
    validate_file_name(root.name)
    parent = validate_remote_dir(remote_parent)
    files, dirs = [], []
    for item in sorted(root.rglob("*")):
        rel = item.relative_to(root).as_posix()
        for part in rel.split("/"):
            validate_file_name(part)
        if item.is_dir():
            dirs.append(rel)
        elif item.is_file():
            files.append({"rel": rel, "size": item.stat().st_size})
    digest = hashlib.sha256(json.dumps([[f["rel"], f["size"]] for f in files] + dirs, ensure_ascii=False)
                            .encode("utf-8")).hexdigest()[:24]
    return {"local_dir": os.fspath(root), "name": root.name, "remote_parent": parent,
            "remote_path": f"{parent.rstrip('/')}/{root.name}", "files": files, "dirs": dirs, "tree_digest": digest}


@dataclass
class DirCollectResult:
    """``status`` is terminal for collected/already_collected/mismatch/ambiguous/target_exists."""

    status: str
    next: str
    stable_token: str | None = None
    source_dir: str | None = None
    target_dir: str | None = None
    files: list[dict] = field(default_factory=list)
    partials: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    size_mismatch: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _same_logical_dir(top: str, expected: str) -> bool:
    return top == expected or _COPY_SUFFIX_RE.match(top).group("stem") == expected


def collect_download_dir(
    *,
    download_root: str | os.PathLike,
    before: dict[str, FileFact],
    expected_name: str,
    expected_files: dict[str, str],
    target_dir: str | os.PathLike,
    stable_token: str | None = None,
) -> DirCollectResult:
    """One observation step for a downloaded folder; never resubmits, never overwrites.

    ``expected_files`` maps remote relative paths (OCR names, compared under
    :func:`fold_name`) to their list size labels.  The folder is collected into
    ``target_dir/expected_name`` only when exactly one new top-level folder
    (``name`` or the client's ``name(1)``) holds exactly those files with
    matching sizes, without unresolved partials, unchanged since the previous step, and
    no existing target file differs.  Files already at the target with the
    same SHA-256 are left in the download dir and reported as duplicates.
    """
    validate_file_name(expected_name)
    root = Path(download_root)
    after = snapshot_dir(root)
    fresh = [f for rel, f in after.items() if before.get(rel) != f and "/" in rel]
    groups: dict[str, list[FileFact]] = {}
    for f in fresh:
        top = f.rel.split("/", 1)[0]
        if _same_logical_dir(top, expected_name):
            groups.setdefault(top, []).append(f)
    if not groups:
        return DirCollectResult("not_found", "observe_again_then_read_client_tasks")
    if len(groups) > 1:
        return DirCollectResult("ambiguous", "manual_review", unexpected=sorted(groups))
    top, facts = next(iter(groups.items()))
    inner_facts = {f.rel.split('/', 1)[1]: f for f in facts}
    # Exact expected paths only; OCR name folding cannot authorize this exception.
    expected_sizes = {rel: int(label) for rel, label in expected_files.items() if str(label).isdigit()}
    superseded = superseded_partial_siblings(inner_facts, expected_sizes, root=root / top)
    partials = sorted(f.rel for f in facts if _PARTIAL_RE.search(f.rel)
                      and f.rel.split('/', 1)[1] not in superseded)
    done = {f.rel.split("/", 1)[1]: f for f in facts if not _PARTIAL_RE.search(f.rel)}
    want = {fold_name(rel): (rel, label) for rel, label in expected_files.items()}
    have = {fold_name(rel): rel for rel in done}
    missing = sorted(want[k][0] for k in want.keys() - have.keys())
    unexpected = sorted(have[k] for k in have.keys() - want.keys())
    if partials or missing:
        return DirCollectResult("in_progress", "observe_again_then_read_client_tasks", source_dir=os.fspath(root / top),
                                partials=partials, missing=missing, unexpected=unexpected)
    mismatch = sorted(have[k] for k in want if not size_label_matches(want[k][1], done[have[k]].size))
    if unexpected or mismatch:
        return DirCollectResult("mismatch", "manual_review", source_dir=os.fspath(root / top), unexpected=unexpected,
                                size_mismatch=mismatch)
    stable_facts = [*done.values(), *(inner_facts[r] for r in superseded)]
    token = hashlib.sha256("\n".join(sorted(f.token for f in stable_facts)).encode("utf-8")).hexdigest()[:24]
    if stable_token != token:
        return DirCollectResult("unstable", "observe_again_with_stable_token", token, os.fspath(root / top))

    target_root = Path(target_dir) / expected_name
    if target_root.resolve().parent != Path(target_dir).resolve():
        raise ValueError(f"目标路径越出业务目录：{target_root}")
    plan, conflicts, duplicates = [], [], []
    for rel in sorted(done):
        source, target = root / top / rel, target_root / rel
        digest = sha256_file(source)
        if target.exists():
            (duplicates if target.is_file() and sha256_file(target) == digest else conflicts).append(rel)
        else:
            plan.append((rel, source, target, digest))
    if conflicts:  # decided before moving anything
        return DirCollectResult("target_exists", "manual_review", token, os.fspath(root / top), os.fspath(target_root),
                                conflicts=conflicts, duplicates=duplicates)
    files = []
    for rel, source, target, digest in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        _move_no_overwrite(source, target, digest)
        files.append({"rel": rel, "size": target.stat().st_size, "sha256": digest})
    status = "collected" if plan else "already_collected"
    return DirCollectResult(status, "done" if plan else "caller_may_remove_duplicate_source", token,
                            os.fspath(root / top), os.fspath(target_root), files=files, duplicates=duplicates)
