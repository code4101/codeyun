"""Read-only evidence for :mod:`.operations`: what the filesystem proves about one ledger record.

Nothing here clicks, navigates, submits, moves, copies or deletes.  Inputs are
a normalised operation request (see ``operations.normalize_request``), the
ledger record found for it and filesystem facts (``load_manifest``,
``verify_local``).  Only a new file (or one new top-level folder) relative to
the persisted before-snapshot, matching the request's name, sizes and trusted
SHA-256, unchanged across two observations and with no conflicting target
counts as ``ready``.  Hashes computed here are reported as local facts; they
never become the remote expectation.

The public ``snapshot_dir``/``sha256_file`` follow links, so this module walks
and hashes with ``lstat`` only: a symlink, junction or other reparse point on
the download root's or target's path chain, or among the relevant candidates,
is reported as ``unsafe`` before anything is hashed and never becomes
``ready`` or ``verified``.  A hash is read through one handle whose identity
(device, inode, size, mtime) must match ``lstat`` before and after the read.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat as stat_mod
from pathlib import Path, PurePosixPath

from .local_files import FileFact, load_manifest
from .verification import verify_local

# Same in-progress artefacts and ``name(1).ext`` collision names the official client produces.
PARTIAL_RE = re.compile(r"\.baiduyun\.|\.downloading$|\.downloading\.cfg$", re.I)
_COPY_RE = re.compile(r"^(?P<stem>.*?)(?:\(\d+\))?$")
_REPARSE = getattr(stat_mod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def result(status: str, next_step: str, *, recoverable: bool = False, **evidence) -> dict:
    return {"status": status, "next": next_step, "recoverable": recoverable, "evidence": evidence}


def review(status: str, reason: str, **evidence) -> dict:
    return result(status, "manual_review_do_not_resubmit", reason=reason, **evidence)


def unsafe(paths: list[str]) -> dict:
    return review("unsafe", "link_or_reparse_point", paths=paths)


def same_logical_name(name: str, expected: str) -> bool:
    if name == expected:
        return True
    want, have = PurePosixPath(expected), PurePosixPath(name)
    return want.suffix.lower() == have.suffix.lower() and _COPY_RE.match(have.stem).group("stem") == want.stem


def same_logical_dir(top: str, expected: str) -> bool:
    return top == expected or _COPY_RE.match(top).group("stem") == expected


# -- link-safe filesystem facts -------------------------------------------------------

def is_link(st: os.stat_result) -> bool:
    return stat_mod.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _identity(st: os.stat_result) -> tuple[int, int, int, int]:
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def linked_component(path: Path) -> str | None:
    """First existing component of absolute ``path`` (drive root excluded) that is a link/reparse point."""
    for part in [*reversed(path.parents[:-1]), path]:
        try:
            st = os.lstat(part)
        except FileNotFoundError:
            return None
        if is_link(st):
            return os.fspath(part)
    return None


def safe_snapshot(root: Path) -> tuple[dict[str, FileFact], list[str]]:
    """``snapshot_dir`` without following links: (regular files, link/reparse/special entries not entered)."""
    facts: dict[str, FileFact] = {}
    links: list[str] = []
    stack = [(root, "")]
    while stack:
        current, prefix = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except FileNotFoundError:
            if not prefix:
                raise
            continue  # a sub folder vanished mid-walk; the stability re-walk decides
        for entry in entries:
            rel = prefix + entry.name
            try:
                st = os.lstat(entry.path)
            except FileNotFoundError:
                continue
            if is_link(st):
                links.append(rel)
            elif stat_mod.S_ISDIR(st.st_mode):
                stack.append((Path(entry.path), rel + "/"))
            elif stat_mod.S_ISREG(st.st_mode):
                facts[rel] = FileFact(rel, st.st_size, st.st_mtime_ns)
            else:
                links.append(rel)
    return facts, sorted(links)


def stable_hash(path: Path) -> tuple[str, os.stat_result] | str:
    """(SHA-256, lstat) of a regular non-link file read through one handle, else a status code."""
    try:
        pre = os.lstat(path)
    except FileNotFoundError:
        return "artifact_missing"
    except OSError:
        return "unreadable"
    if is_link(pre) or not stat_mod.S_ISREG(pre.st_mode):
        return "unsafe"
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            if _identity(os.fstat(handle.fileno())) != _identity(pre):
                return "unstable"
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        post = os.lstat(path)
    except FileNotFoundError:
        return "unstable"
    except OSError:
        return "unreadable"
    if is_link(post) or _identity(post) != _identity(pre):
        return "unstable"
    return digest.hexdigest().upper(), pre


def _unchanged(hashed: tuple[str, os.stat_result] | str, fact: FileFact) -> bool:
    return not isinstance(hashed, str) and (hashed[1].st_size, hashed[1].st_mtime_ns) == (fact.size, fact.mtime_ns)


def _target_conflict(target: Path, digest: str) -> str | None:
    """None when ``target`` is absent or already holds exactly ``digest``; else a status code."""
    if linked_component(target):
        return "unsafe"
    if not os.path.lexists(target):
        return None
    hashed = stable_hash(target)
    if isinstance(hashed, str):
        return hashed
    return None if hashed[0] == digest else "differs"


def verify_file(path: Path, size: int, sha256: str | None) -> dict:
    """Grade one collected file: content_verified needs the request's trusted SHA-256."""
    if linked_component(path):
        return {"status": "unsafe", "verification_level": "none"}
    hashed = stable_hash(path)
    if isinstance(hashed, str):
        return {"status": hashed, "verification_level": "none"}
    digest, pre = hashed
    facts = {"size": pre.st_size, "local_sha256": digest, "hash_source": "local_read"}
    if pre.st_size != size:
        return {"status": "size_mismatch", "verification_level": "none", **facts}
    if sha256 and digest != sha256:
        return {"status": "hash_mismatch", "verification_level": "none", **facts}
    level = "content_verified" if sha256 else "structure_verified"
    return {"status": level, "verification_level": level, **facts}


# -- observations ------------------------------------------------------------------

def _fresh(facts: dict[str, FileFact], before: dict) -> dict:
    return {rel: f for rel, f in facts.items() if before.get(rel) != f}


def _file_links(links: list[str], name: str) -> list[str]:
    return [r for r in links if "/" not in r and same_logical_name(PARTIAL_RE.split(r)[0], name)]


def observe_file(root: Path, before: dict, request: dict) -> dict:
    name, size, sha = request["name"], request["expected_size"], request["expected_sha256"]
    target = Path(request["local_dir"]) / name
    snapshot, links = safe_snapshot(root)
    if (linked := _file_links(links, name)):
        return unsafe(linked)
    if (chain := linked_component(target)):
        return unsafe([chain])
    fresh = _fresh(snapshot, before)
    partials = sorted(r for r in fresh if PARTIAL_RE.search(r)
                      and same_logical_name(PurePosixPath(PARTIAL_RE.split(r)[0]).name, name))
    candidates = [f for r, f in fresh.items() if not PARTIAL_RE.search(r) and "/" not in r
                  and same_logical_name(r, name)]
    if partials:
        return result("in_progress", "reconcile_again_later", partials=partials)
    sized = [f for f in candidates if f.size == size]
    if not sized:
        if os.path.lexists(target):  # moved by an earlier collect whose ledger update never landed
            return review("needs_review", "target_present_ledger_unverified", target_path=os.fspath(target),
                          target=verify_file(target, size, sha))
        return review("not_found", "no_new_download", other_sizes=sorted({f.size for f in candidates}))
    if len(sized) > 1:
        return review("ambiguous", "multiple_candidates", candidates=sorted(f.rel for f in sized))
    fact = sized[0]
    hashed = stable_hash(root / fact.rel)
    if hashed == "unsafe":
        return unsafe([fact.rel])
    again, links = safe_snapshot(root)
    if (linked := _file_links(links, name)):
        return unsafe(linked)
    if not _unchanged(hashed, fact) or again.get(fact.rel) != fact:
        return result("unstable", "reconcile_again_later", candidate=fact.rel)
    digest = hashed[0]
    evidence = {"candidate": fact.rel, "size": fact.size, "local_sha256": digest, "hash_source": "local_read",
                "expected_sha256": sha, "target_path": os.fspath(target)}
    if sha and digest != sha:
        return review("hash_mismatch", "candidate_hash_differs", **evidence)
    conflict = _target_conflict(target, digest)
    if conflict == "unsafe":
        return unsafe([os.fspath(target)])
    if conflict:
        return review("target_exists", "target_differs", target_check=conflict, **evidence)
    return result("ready", "recover_download", recoverable=True,
                  verification_level="content_verified" if sha else "structure_verified", **evidence)


def observe_dir(root: Path, before: dict, request: dict) -> dict:
    name, manifest = request["name"], request["expected_manifest"]
    expected = {f["rel"]: f for f in manifest["files"]}
    target_root = Path(request["local_dir"]) / name
    snapshot, links = safe_snapshot(root)
    if (linked := [r for r in links if same_logical_dir(r.partition("/")[0], name)]):
        return unsafe(linked)
    if (chain := linked_component(target_root)):
        return unsafe([chain])
    groups: dict[str, dict] = {}
    for rel, fact in _fresh(snapshot, before).items():
        top, _, inner = rel.partition("/")
        if inner and same_logical_dir(top, name):
            groups.setdefault(top, {})[inner] = fact
    if not groups:
        return review("not_found", "no_new_download")
    if len(groups) > 1:
        return review("ambiguous", "multiple_candidates", candidates=sorted(groups))
    top, files = next(iter(groups.items()))
    partials = sorted(r for r in files if PARTIAL_RE.search(r))
    done = {r: f for r, f in files.items() if not PARTIAL_RE.search(r)}
    missing = sorted(set(expected) - set(done))
    if partials or missing:
        return result("in_progress", "reconcile_again_later", source=top, partials=partials, missing=missing)
    extra = sorted(set(done) - set(expected))
    size_bad = sorted(r for r in expected if done[r].size != expected[r]["size"])
    if extra or size_bad:
        return review("mismatch", "file_set_differs", source=top, extra=extra, size_mismatch=size_bad)
    hashes: dict[str, str] = {}
    for r in sorted(done):
        hashed = stable_hash(root / top / r)
        if hashed == "unsafe":
            return unsafe([f"{top}/{r}"])
        if not _unchanged(hashed, done[r]):
            return result("unstable", "reconcile_again_later", source=top)
        hashes[r] = hashed[0]
    now, links = safe_snapshot(root / top)
    if links:
        return unsafe([f"{top}/{r}" for r in links])
    if {r: (f.size, f.mtime_ns) for r, f in now.items()} != {r: (f.size, f.mtime_ns) for r, f in done.items()}:
        return result("unstable", "reconcile_again_later", source=top)
    hash_bad = sorted(r for r in expected if expected[r]["sha256"] and hashes[r] != expected[r]["sha256"])
    if hash_bad:
        return review("hash_mismatch", "candidate_hash_differs", source=top, hash_mismatch=hash_bad)
    checks = {r: _target_conflict(target_root / r, hashes[r]) for r in sorted(done)}
    if (linked := [os.fspath(target_root / r) for r, c in checks.items() if c == "unsafe"]):
        return unsafe(linked)
    conflicts = [r for r, c in checks.items() if c]
    if conflicts:
        return review("target_exists", "target_differs", source=top, conflicts=conflicts)
    trusted = all(f["sha256"] for f in expected.values()) and bool(expected)
    return result("ready", "recover_download", recoverable=True, source=top, target_path=os.fspath(target_root),
                  files=len(done), verification_level="content_verified" if trusted else "structure_verified")


def verified_evidence(request: dict, facts: dict) -> dict:
    """A ``verified`` ledger record still needs its target re-read against the request."""
    target = facts.get("target_path")
    if not target:
        return review("needs_review", "verified_without_target")
    if (chain := linked_component(Path(target))):
        return unsafe([chain])
    if request["kind"] == "download":
        if facts.get("sha256") is None:
            return review("needs_review", "verified_without_local_hash", target_path=target)
        check = verify_file(Path(target), request["expected_size"], request["expected_sha256"])
        if check["status"] == "unsafe":
            return unsafe([target])
        if check["status"] not in {"content_verified", "structure_verified"}:
            return review("artifact_changed", check["status"], target_path=target, check=check)
        if check["local_sha256"] != str(facts["sha256"]).upper():
            return review("artifact_changed", "differs_from_collected", target_path=target, check=check)
        return result("verified", "done", target_path=target, check_status=check.pop("status"), **check)
    check = verify_local(target, request["expected_manifest"])
    if check["status"] == "unsafe":
        return unsafe([target])
    public = {k: check[k] for k in ("status", "verification_level", "manifest_sha256", "counts")}
    if request["expected_manifest"] is None:
        return result("needs_manifest", "supply_trusted_expected_manifest", target_path=target, check=public)
    if check["status"] not in {"content_verified", "structure_verified"}:
        return review("artifact_changed", check["status"], target_path=target, check=public)
    return result("verified", "done", target_path=target, verification_level=check["verification_level"], check=public)


def reconcile_record(request: dict, record: dict) -> dict:
    """Map one ledger record to read-only evidence; planned/unknown never becomes a retry."""
    state, facts = record.get("state"), record.get("facts") or {}
    if state == "verified":
        return verified_evidence(request, facts)
    if state in {"failed", "abandoned"}:
        status = "not_submitted" if facts.get("before_submit") else state
        return review(status, f"ledger_{state}", ledger_reason=str(facts.get("reason") or "")[:80] or None)
    if state == "planned":
        return review("planned", "submit_not_recorded")
    if request["kind"] == "download":
        if facts.get("expected_size") is not None and int(facts["expected_size"]) != request["expected_size"]:
            return review("needs_review", "ledger_expectation_conflict")
        recorded = (record.get("inputs") or {}).get("expected_sha256") or facts.get("expected_sha256")
        if recorded and request["expected_sha256"] and str(recorded).upper() != request["expected_sha256"]:
            return review("needs_review", "ledger_expectation_conflict")
    if not facts.get("manifest"):
        return review("needs_review", "manifest_missing")
    try:
        root, before = load_manifest(facts["manifest"])
    except (OSError, ValueError, KeyError, TypeError):
        return review("needs_review", "manifest_unreadable")
    if (chain := linked_component(root)):
        return unsafe([chain])
    try:
        if not stat_mod.S_ISDIR(os.lstat(root).st_mode):
            return review("needs_review", "download_root_missing")
    except FileNotFoundError:
        return review("needs_review", "download_root_missing")
    if request["kind"] == "download":
        return observe_file(root, before, request)
    if request["expected_manifest"] is None:
        return result("needs_manifest", "supply_trusted_expected_manifest")
    return observe_dir(root, before, request)
