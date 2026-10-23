"""External immutable manifests for pure downloaded material directories.

Evidence may live outside a payload so adding proof never changes the original
file set. Such manifests bind the exact material, absolute payload root, all
relative filenames, byte sizes and local SHA-256s. They prove local integrity,
not trusted remote hashes or completed content cleaning.
"""
from pathlib import Path

SCHEMA = "codeyun.skilldep_external_payload_manifest/v1"


def external_manifest_problem(content: dict, directory: Path, material_id: str | None,
                              size_bytes: int | None, status: str | None) -> str | None:
    """Return a refusal, or None after verifying the entire bound local payload."""
    if content.get("schema") != SCHEMA:
        return "manifest 不在记录目录内，且没有受支持的外部证据 schema"
    if not isinstance(content.get("material_id"), str) or not content["material_id"]:
        return "外部 manifest 缺少 material_id"
    if material_id is not None and content["material_id"] != material_id:
        return "外部 manifest 素材不匹配"
    root = content.get("root")
    if not isinstance(root, str) or not Path(root).is_absolute() or Path(root).resolve() != directory.resolve():
        return "外部 manifest root 与记录目录不匹配"
    if type(content.get("complete")) is not bool or status == "downloaded" and not content["complete"]:
        return "外部 manifest 没有证明整包完成"
    files = content.get("files")
    if not isinstance(files, list) or not files or any(
        not isinstance(f, dict) or not f.get("sha256") for f in files
    ):
        return "外部 manifest 必须列出每个文件及其 SHA-256"
    # Public filesystem-only verification refuses links, traversal, duplicate
    # paths, changed files, missing/extra entries and size/hash mismatches.
    from backend.core.baidu_netdisk.verification import verify_local
    try:
        result = verify_local(directory, {"files": files})
    except (ValueError, TypeError, OSError) as exc:
        return f"外部 manifest 校验失败：{type(exc).__name__}"
    if result["status"] != "content_verified":
        return f"外部 manifest 文件校验失败：{result['status']}"
    if size_bytes is not None and sum(f["size"] for f in result["manifest"]["files"]) != size_bytes:
        return "外部 manifest 总字节数与记录不符"
    return None
