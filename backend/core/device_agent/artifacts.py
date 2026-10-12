"""设备代理交付附件：冻结文件内容，仅按轮次和附件 ID 下载。

模型报告 attachments=[{"path": "绝对路径"}]；工程复制并计算校验和。
下载端不接受调用方提供的本机路径。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import uuid

from backend.core.settings import get_settings

MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def artifact_directory(turn_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", turn_id):
        raise ValueError("无效轮次 ID")
    return get_settings().data_dir / "device-agent" / "artifacts" / turn_id


def publish_attachments(turn_id: str, attachments: list) -> list[dict]:
    """快照最多十个实际文件；缺失或超限直接失败，不伪造交付。"""
    if len(attachments) > 10:
        raise ValueError("单轮最多交付十个附件")
    prepared = []
    for item in attachments:
        source = Path(str(item.get("path") or "")) if isinstance(item, dict) else Path("")
        if not source.is_absolute() or not source.is_file():
            raise ValueError("交付附件必须是存在的绝对文件路径")
        if source.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("单个附件不能超过 32 MiB")
        with source.open("rb") as stream:
            content = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError("单个附件不能超过 32 MiB")
        prepared.append((source.name, content))
    result = []
    for name, content in prepared:
        artifact_id = uuid.uuid4().hex
        target = artifact_directory(turn_id) / artifact_id
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        result.append({"id": artifact_id, "filename": name, "size_bytes": len(content),
                       "sha256": hashlib.sha256(content).hexdigest(),
                       "download_path": f"/api/device-agent/turns/{turn_id}/attachments/{artifact_id}"})
    return result


def resolve_attachment(turn: dict, artifact_id: str) -> tuple[Path, dict]:
    """仅允许下载此轮报告中已发布的快照。"""
    if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
        raise FileNotFoundError("附件不存在")
    for item in turn.get("result_report", {}).get("attachments", []):
        if item.get("id") == artifact_id:
            path = artifact_directory(turn["id"]) / artifact_id
            if path.is_file():
                return path, item
    raise FileNotFoundError("附件不存在")
