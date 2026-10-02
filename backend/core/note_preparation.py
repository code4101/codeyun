"""Read-only source snapshots and durable preparation packets.

This boundary calls the existing note/PG public read handlers. It never edits a
source or schedules a business action. Immutable snapshots and per-owner packets
live outside the repository; decisions mean review labels, never execution.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import BaseModel, Field

from backend.core.settings import get_settings

SCHEMA_VERSION = 2


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()


def automatic_note(fields: list) -> bool:
    """Known import provenance is excluded; untagged notes remain unconfirmed manual content."""
    keys = {str(item[0]) for item in fields if isinstance(item, list) and item}
    return any(key.startswith(("__codex_", "__wechat_", "wechat_daily_", "__ruanyf_")) for key in keys)


def plate_text(value) -> str:
    """Plate projection reads text leaves, never media URLs or binary payloads."""
    if isinstance(value, list):
        return "\n".join(filter(None, (plate_text(item) for item in value)))
    if not isinstance(value, dict):
        return ""
    kind = value.get("type", "")
    if kind in {"img", "image", "video", "audio", "file", "media_embed"}:
        return f"[{kind}]"
    if kind == "equation":
        return str(value.get("texExpression", ""))
    return str(value.get("text", "")) + plate_text(value.get("children", []))


def graph_texts(objects: dict) -> list[dict]:
    """Keep authored text, details and immediate graph neighbours, excluding binary assets."""
    nodes = {}
    for key, obj in objects.items():
        if key.startswith("@") or not isinstance(obj, dict):
            continue
        text = str(obj.get("text") or "").strip()
        if obj.get("_") == "UrlNode":
            text = str(obj.get("title") or "")
            url = str(obj.get("url") or "")
            if re.match(r"^https?://", url):
                text += "\n" + url
        elif obj.get("_") == "LatexNode":
            text = str(obj.get("latexSource") or "")
        elif obj.get("_") == "ReferenceBlockNode":
            text = " / ".join(str(obj.get(key) or "") for key in ("fileName", "sectionName")).strip(" / ")
        details = plate_text(obj.get("details") or [])
        if details:
            text += "\n" + details
        if text.strip():
            nodes[key] = {"object_id": key, "text": text.strip(), "neighbours": []}
    def refs(value):
        if isinstance(value, dict):
            if set(value) == {"$graphRef"}:
                yield value["$graphRef"]
            else:
                for item in value.values():
                    yield from refs(item)
        elif isinstance(value, list):
            for item in value:
                yield from refs(item)
    for obj in objects.values():
        if not isinstance(obj, dict):
            continue
        related = list(dict.fromkeys(refs(obj)))
        for left in related:
            if left in nodes:
                for right in related:
                    if right != left and right in nodes and right not in nodes[left]["neighbours"]:
                        nodes[left]["neighbours"].append(right)
    return list(nodes.values())


class PreparationPacket(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    intent: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    confidence: Literal["explicit", "inferred", "uncertain"] = "inferred"
    research: str = Field(default="", max_length=100000)
    questions: list[str] = Field(default_factory=list, max_length=30)
    dependencies: list[str] = Field(default_factory=list, max_length=50)
    references: list[str] = Field(default_factory=list, max_length=100)
    session_id: str = ""
    model: str = ""


class PreparationStore:
    """Atomic, process-safe storage scoped by numeric owner ID.

    Snapshots have content identities. Packet identities are stable for the same
    title and evidence, while research revisions are kept for later comparison.
    All mutations acquire one owner's file lock, so API and CLI can coexist.
    """
    def __init__(self, owner_id: int, root: Path | None = None):
        if owner_id < 1:
            raise ValueError("必须指定有效用户")
        self.root = (root or get_settings().data_dir / "note-preparation") / str(owner_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.root / "store.lock"), timeout=30)

    def read(self, name: str, default=None):
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def write(self, name: str, value):
        path = self.root / name
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def cache_snapshot(self, sources: list[dict], coverage: dict) -> dict:
        identity = digest({"schema": SCHEMA_VERSION, "sources": sources, "coverage": coverage})
        name = f"snapshot-{identity}.json"
        with self.lock:
            snapshot = self.read(name)
            if snapshot is None:
                snapshot = {"id": identity, "schema": SCHEMA_VERSION, "created_at": time.time(),
                            "sources": sources, "coverage": coverage}
                self.write(name, snapshot)
            self.write("latest.json", {"snapshot_id": identity})
        return snapshot

    def snapshot(self, identity: str | None = None) -> dict | None:
        if identity is None:
            identity = (self.read("latest.json", {}) or {}).get("snapshot_id")
        if identity is None:
            return None
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("无效快照")
        return self.read(f"snapshot-{identity}.json")

    def save_packet(self, snapshot_id: str, packet: PreparationPacket) -> dict:
        snapshot = self.snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError("来源快照不存在")
        available = {source["id"] for source in snapshot["sources"]}
        if not set(packet.evidence_ids) <= available:
            raise ValueError("研究包引用了不存在的原文证据")
        identity = digest({"title": packet.title, "evidence": sorted(set(packet.evidence_ids))})[:24]
        with self.lock:
            packets = self.read("packets.json", {})
            previous = packets.get(identity, {})
            revision = {**packet.model_dump(), "snapshot_id": snapshot_id, "created_at": time.time()}
            if previous and all(previous.get(key) == value for key, value in packet.model_dump().items()) and previous.get("snapshot_id") == snapshot_id:
                return previous
            item = {**revision, "id": identity, "decision": previous.get("decision", "pending"),
                    "human_note": previous.get("human_note", ""),
                    "history": previous.get("history", []) + ([{k: v for k, v in previous.items() if k != "history"}] if previous else [])}
            packets[identity] = item
            self.write("packets.json", packets)
        return item

    def overview(self) -> dict:
        with self.lock:
            snapshot = self.snapshot()
            packets = list(self.read("packets.json", {}).values())
            runs = self.read("runs.json", [])
        latest = {source["id"]: source["hash"] for source in (snapshot or {}).get("sources", [])}
        # Read each distinct snapshot once, not once per packet (large diaries).
        snapshot_hashes = {(snapshot or {}).get("id"): latest}
        for packet in packets:
            if packet["snapshot_id"] not in snapshot_hashes:
                old = self.snapshot(packet["snapshot_id"]) or {}
                snapshot_hashes[packet["snapshot_id"]] = {source["id"]: source["hash"] for source in old.get("sources", [])}
            hashes = snapshot_hashes[packet["snapshot_id"]]
            packet["stale"] = any(latest.get(key) != hashes.get(key) for key in packet["evidence_ids"])
        summary = {**snapshot, "sources": [], "source_count": len(snapshot["sources"])} if snapshot else None
        return {"snapshot": summary, "packets": sorted(packets, key=lambda item: item["created_at"], reverse=True), "runs": runs}

    def decision(self, packet_id: str, decision: str, note: str) -> dict:
        if decision not in {"pending", "interested", "deferred", "dismissed"}:
            raise ValueError("无效审核标签")
        with self.lock:
            packets = self.read("packets.json", {})
            if packet_id not in packets:
                raise KeyError(packet_id)
            packets[packet_id].update(decision=decision, human_note=note, reviewed_at=time.time())
            self.write("packets.json", packets)
            return packets[packet_id]

    def record_run(self, run: dict):
        with self.lock:
            runs = self.read("runs.json", [])
            runs.append({**run, "recorded_at": time.time()})
            self.write("runs.json", runs)


async def collect_sources(session, user, *, note_limit: int = 5000) -> tuple[list[dict], dict]:
    """Read authored material through public handlers; record coverage and failures.

    Notes with known generated provenance are excluded. Missing provenance only
    means 'manual or unknown', not a proven assertion of human authorship.
    Only owned PG files are included; shared files are outside this research scope.
    """
    from backend.api.project_graph_files import list_entries, read_entry
    from backend.api.notes import read_notes, read_note
    from backend.core.project_graph.codec import import_prg
    sources, errors = [], []
    graph_count = 0
    for entry in list_entries(session=session, user=user)["entries"]:
        if entry["kind"] != "document" or entry["ownerId"] != user.id:
            continue
        graph_count += 1
        try:
            document = await read_entry(entry["id"], session=session, user=user)
            objects = import_prg(base64.b64decode(document["content"])) if document["content"] else {}
            texts = graph_texts(objects)
            by_id = {item["object_id"]: item["text"] for item in texts}
            for node in texts:
                source = {"id": f"pg:{entry['id']}:{node['object_id']}", "kind": "pg",
                          "resource_id": entry["id"], "object_id": node["object_id"],
                          "title": entry["title"], "text": node["text"],
                          "context": [by_id[key] for key in node["neighbours"]],
                          "revision": document["revision"], "updated_at": document["updatedAt"],
                          "url": f"/notes/project-graph?doc={entry['id']}", "provenance": "authored-graph"}
                source["hash"] = digest({"text": source["text"], "context": source["context"]})
                sources.append(source)
        except Exception as exc:
            errors.append({"source": f"pg:{entry['id']}", "error": str(exc)[:1000]})
    scanned, excluded, exhausted = 0, 0, False
    for offset in range(0, note_limit, 128):
        batch = read_notes(skip=offset, limit=min(128, note_limit-offset), created_start=None,
                           created_end=None, updated_start=None, updated_end=None,
                           current_user=user, session=session)
        scanned += len(batch)
        for metadata in batch:
            if automatic_note(metadata.get("custom_fields", [])):
                excluded += 1
                continue
            try:
                note = read_note(str(metadata["id"]), current_user=user, session=session)
                if note.get("format_type") == "html":
                    text = plain_text(note["content"])
                elif note.get("format_type") == "plate":
                    text = plate_text(json.loads(note["content"]))
                else:
                    text = note["content"]
                source = {"id": f"note:{note['id']}", "kind": "note", "resource_id": note["id"],
                          "title": note["title"], "text": text, "context": [],
                          "revision": note.get("version"), "updated_at": note["updated_at"],
                          "url": f"/doc/{note['id']}", "provenance": "manual-or-unknown"}
                source["hash"] = digest({"title": source["title"], "text": source["text"]})
                sources.append(source)
            except Exception as exc:
                errors.append({"source": f"note:{metadata['id']}", "error": str(exc)[:1000]})
        if len(batch) < min(128, note_limit-offset):
            exhausted = True
            break
    sources.sort(key=lambda item: item["id"])
    return sources, {"graphs": graph_count, "note_scanned": scanned, "automatic_excluded": excluded,
                     "note_scan_complete": exhausted, "note_limit": note_limit, "errors": errors}
