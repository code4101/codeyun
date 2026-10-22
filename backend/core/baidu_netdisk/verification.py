"""Read-only acceptance of a downloaded local directory.

:func:`verify_local` reads every file under ``root`` and records its SHA-256.
Levels only go up with evidence supplied by the caller:

* no expected manifest -> ``local_hash_recorded`` (a local fingerprint, not
  proof of where the bytes came from);
* expected file set with sizes, exactly matched -> ``structure_verified``;
* additionally a trusted 64-hex SHA-256 for every expected file, all matching
  -> ``content_verified``.  An empty expected file list matched by an empty
  directory stays ``structure_verified``: there is no trusted hash to check.

Missing, extra, size/hash mismatch, partial download artefacts, links/reparse
points or files changing during the read are failures; nothing is upgraded
automatically.  Nothing is created, moved, copied or deleted.  The returned
manifest keeps the caller's resource identity/version but never claims the
remote side was checked.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as stat_mod
from pathlib import Path

from .local_files import sha256_file, validate_file_name

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
# Same in-progress artefacts the official client leaves (see local_files).
_PARTIAL_RE = re.compile(r"\.baiduyun\.|\.downloading$|\.downloading\.cfg$", re.I)
_REPARSE = getattr(stat_mod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class _Unsafe(Exception):
    pass


def _is_link(st: os.stat_result) -> bool:
    return stat_mod.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _fact(st: os.stat_result) -> tuple[int, int, int]:
    return st.st_size, st.st_mtime_ns, st.st_ino


def _validate_rel(rel: object) -> str:
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        raise ValueError(f"rel 必须是非空相对 POSIX 路径：{rel!r}")
    for part in rel.split("/"):
        if part in {"", ".", ".."}:
            raise ValueError(f"rel 含空段、. 或 ..：{rel!r}")
        validate_file_name(part)  # Windows drive/stream/device/illegal chars
    return rel


def _parse_expected(manifest: object) -> tuple[dict[str, dict], dict]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("expected_manifest 须为 {files:[{rel,size,sha256?}]}")
    files: dict[str, dict] = {}
    folded: dict[str, str] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise ValueError(f"expected 项必须是对象：{item!r}")
        rel = _validate_rel(item.get("rel"))
        key = rel.casefold()
        if key in folded:
            raise ValueError(f"expected rel 重复或大小写冲突：{folded[key]!r} / {rel!r}")
        folded[key] = rel
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"expected size 必须是非负整数：{rel!r}")
        sha = item.get("sha256")
        if sha is not None:
            if not isinstance(sha, str) or not _SHA256_RE.match(sha):
                hint = "（疑似 MD5，不能当 SHA-256）" if isinstance(sha, str) and _MD5_RE.match(sha) else ""
                raise ValueError(f"expected sha256 必须是 64 位十六进制{hint}：{rel!r}")
            sha = sha.upper()
        files[rel] = {"size": size, "sha256": sha}
    identity = {}
    for name in ("resource_id", "resource_version"):
        value = manifest.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
            raise ValueError(f"{name} 必须是字符串或整数")
        identity[name] = value
    return files, identity


def _enumerate(root: Path) -> tuple[dict[str, tuple[int, int, int]], list[str]]:
    """Walk without following links; any link/reparse/special entry is unsafe."""
    found: dict[str, tuple[int, int, int]] = {}
    unsafe: list[str] = []
    stack = [(root, "")]
    while stack:
        current, prefix = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                rel = f"{prefix}{entry.name}"
                st = os.lstat(entry.path)  # DirEntry.stat has st_ino=0 on Windows
                if _is_link(st):
                    unsafe.append(f"链接或重解析点：{rel}")
                elif stat_mod.S_ISDIR(st.st_mode):
                    stack.append((Path(entry.path), rel + "/"))
                elif stat_mod.S_ISREG(st.st_mode):
                    found[rel] = _fact(st)
                else:
                    unsafe.append(f"非常规文件：{rel}")
    return found, unsafe


def _result(status: str, errors: list[str], **extra) -> dict:
    level = status if status in {"content_verified", "structure_verified", "local_hash_recorded"} else "none"
    base = {
        "status": status,
        "verification_level": level,
        "content_verified": status == "content_verified",
        "structure_verified": status in {"content_verified", "structure_verified"},
        "local_hash_recorded": level != "none",
        "manifest": None,
        "manifest_sha256": None,
        "errors": errors,
        "counts": {},
    }
    base.update(extra)
    return base


def verify_local(root: str | os.PathLike, expected_manifest: dict | None = None) -> dict:
    """Hash ``root`` read-only and grade it against ``expected_manifest`` (see module doc)."""
    if not isinstance(root, (str, os.PathLike)) or not os.fspath(root) or "\x00" in os.fspath(root):
        return _result("invalid_input", [f"root 必须是非空路径：{root!r}"])
    expected = identity = None
    if expected_manifest is not None:
        try:
            expected, identity = _parse_expected(expected_manifest)
        except ValueError as exc:
            return _result("invalid_input", [str(exc)])
    base = Path(os.fspath(root))
    try:
        root_st = os.lstat(base)
    except FileNotFoundError:
        return _result("not_found", [f"目录不存在：{base}"])
    except OSError as exc:
        return _result("invalid_input", [f"root 无法读取：{exc}"])
    if _is_link(root_st):
        return _result("unsafe", [f"root 是链接或重解析点：{base}"])
    if not stat_mod.S_ISDIR(root_st.st_mode):
        return _result("not_found", [f"root 不是目录：{base}"])

    try:
        before, unsafe = _enumerate(base)
    except OSError as exc:
        return _result("unstable", [f"枚举失败：{exc}"])
    if unsafe:
        return _result("unsafe", unsafe)
    errors: list[str] = []
    folded: dict[str, str] = {}
    for rel in before:
        if rel.casefold() in folded:
            errors.append(f"本地大小写冲突：{folded[rel.casefold()]} / {rel}")
        folded[rel.casefold()] = rel
    partial = sorted(rel for rel in before if _PARTIAL_RE.search(rel))
    errors += [f"未完成下载文件：{rel}" for rel in partial]

    hashes: dict[str, str] = {}
    unstable: list[str] = []
    for rel in sorted(before):
        path = base.joinpath(*rel.split("/"))
        try:
            pre = os.lstat(path)
            if _is_link(pre) or not stat_mod.S_ISREG(pre.st_mode) or _fact(pre) != before[rel]:
                unstable.append(f"读取前变化或变为链接：{rel}")  # never open what we would not hash
                continue
            digest = sha256_file(path)
            post = os.lstat(path)
        except OSError as exc:
            unstable.append(f"读取失败：{rel}：{exc}")
            continue
        if _is_link(post) or _fact(post) != before[rel]:
            unstable.append(f"读取期间变化：{rel}")
        hashes[rel] = digest
    try:
        after, unsafe_after = _enumerate(base)
    except OSError as exc:
        after, unsafe_after = None, [f"复核枚举失败：{exc}"]
    if after != before or unsafe_after:
        unstable.append("复核枚举与首次枚举不一致")
        unstable += unsafe_after

    files = [{"rel": rel, "size": before[rel][0], "sha256": hashes.get(rel)} for rel in sorted(before)]
    manifest = {
        "files": files,
        "resource_id": (identity or {}).get("resource_id"),
        "resource_version": (identity or {}).get("resource_version"),
        "hash_source": "local_read",
        "remote_verified": False,  # identity is the caller's claim; only local bytes were read
        "expected_provided": expected is not None,
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest().upper()
    counts = {"files": len(before), "partial": len(partial), "unstable": len(unstable)}
    if unstable:
        return _result("unstable", unstable + errors, manifest=manifest, manifest_sha256=manifest_sha256, counts=counts)

    if expected is None:
        status = "incomplete" if errors else "local_hash_recorded"
        return _result(status, errors, manifest=manifest, manifest_sha256=manifest_sha256, counts=counts)

    missing = sorted(set(expected) - set(before))
    extra = sorted(set(before) - set(expected))
    size_bad = sorted(rel for rel in expected if rel in before and before[rel][0] != expected[rel]["size"])
    hash_bad = sorted(rel for rel in expected if rel in hashes and expected[rel]["sha256"]
                      and hashes[rel] != expected[rel]["sha256"])
    no_sha = sorted(rel for rel in expected if not expected[rel]["sha256"])
    matched = [rel for rel in expected if rel in hashes and before[rel][0] == expected[rel]["size"]
               and expected[rel]["sha256"] and hashes[rel] == expected[rel]["sha256"]]
    errors += [f"缺失：{rel}" for rel in missing] + [f"多余：{rel}" for rel in extra if rel not in partial]
    errors += [f"大小不符：{rel}" for rel in size_bad] + [f"SHA-256 不符：{rel}" for rel in hash_bad]
    counts.update(expected=len(expected), missing=len(missing), extra=len(extra), size_mismatch=len(size_bad),
                  hash_mismatch=len(hash_bad), without_trusted_sha256=len(no_sha),
                  content_matched=len(matched))
    if errors:
        status = "hash_mismatch" if hash_bad and not (missing or extra or size_bad or partial) else "mismatch"
    else:
        status = "content_verified" if expected and not no_sha else "structure_verified"
    return _result(status, errors, manifest=manifest, manifest_sha256=manifest_sha256, counts=counts)
