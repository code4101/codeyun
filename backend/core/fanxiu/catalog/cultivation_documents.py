"""Read-only cultivation evidence: item identity -> inventory -> user documents.

This entry never collects Runtime, rebuilds catalogs, creates users, or writes
notes. Stored ranks are deliberately excluded: callers must obtain current
progression from Runtime. Images remain evidence, not automatically asserted
costs/effects. A missing document or cost is unknown, never zero.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from sqlmodel import Session


class CultivationDocumentAmbiguity(ValueError):
    """More than one inventory identity/document matches the requested item."""


def normalize_cultivation_name(value: Any) -> str:
    """Exact names after whitespace/typographic normalization and a type prefix.

    Only presentation prefixes are removed. No substring, fuzzy OCR, suffix or
    character substitution is allowed: similar names can be different items.
    """
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))
    return re.sub(r"^(?:时装|武器|头饰|背饰|御器|幻化|法宝|功法)[·・•]", "", text)


def match_cultivation_inventory(
    card: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Resolve IDs first, then an exact normalized name; ambiguity fails closed."""
    requested = str(card.get("id") or "")
    links = {
        "fashion_id": card.get("linked_fashion_id"),
        "pet_id": card.get("linked_pet_id"),
        "talisman_id": card.get("linked_talisman_id"),
        "book_id": card.get("linked_gongfa_id"),
    }
    by_id = [row for row in rows if (
        requested and any(str(row.get(key) or "") == requested
                          for key in ("item_id", "catalog_item_id", "runtime_item_id"))
    ) or any(value is not None and str(row.get(key) or "") == str(value)
             for key, value in links.items())]
    name = normalize_cultivation_name(card.get("name"))
    matches = by_id or [row for row in rows if name and
                       normalize_cultivation_name(row.get("name")) == name and
                       not any(value is not None and row.get(key) is not None
                               and str(row[key]) != str(value)
                               for key, value in links.items())]
    if len(matches) > 1:
        raise CultivationDocumentAmbiguity(
            f"道具 {requested} 对应多个库存条目："
            + ", ".join(str(row.get("id")) for row in matches)
        )
    return dict(matches[0]) if matches else None


class _EvidenceHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.images: list[dict[str, str]] = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            values = dict(attrs)
            self.images.append({"url": str(values.get("src") or ""),
                                "preceding_text": "".join(self.text)[-240:]})
        elif tag in ("p", "br", "li", "h1", "h2", "h3"):
            self.text.append("\n")


def cultivation_document_evidence(
    note: Mapping[str, Any], *, attachments_dir: str | Path
) -> dict[str, Any]:
    """Return HTML and referenced attachment hashes without downloading anything.

    Only /static/attachments/<filename> URLs resolve to local files. Missing or
    remote references remain explicit. Hashes include image bytes so replacing
    an attachment invalidates a planner cache even if note HTML is unchanged.
    """
    html = str(note.get("content") or "")
    parsed = _EvidenceHTML()
    parsed.feed(html)
    base = Path(attachments_dir).resolve()
    attachments: list[dict[str, Any]] = []
    for reference in parsed.images:
        attachment: dict[str, Any] = dict(reference)
        url = urlsplit(reference["url"])
        path = unquote(url.path)
        prefix = "/static/attachments/"
        if url.scheme or url.netloc or not path.startswith(prefix):
            attachment["status"] = "external_or_unsupported"
        else:
            filename = path[len(prefix):]
            resolved = (base / filename).resolve()
            if not filename or resolved.parent != base:
                attachment["status"] = "invalid_local_reference"
            elif not resolved.is_file():
                attachment.update(status="missing", local_path=str(resolved))
            else:
                with resolved.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                attachment.update(status="available", local_path=str(resolved),
                                  sha256=digest, size_bytes=resolved.stat().st_size)
        attachments.append(attachment)
    return {"note_id": str(note.get("numeric_id") or note.get("id") or ""),
            "title": str(note.get("title") or ""), "html": html,
            "plain_text": "".join(parsed.text).strip(), "attachments": attachments}


def cultivation_evidence_fingerprint(bundle: Mapping[str, Any]) -> str:
    """Hash semantic evidence, independent of timestamps and deployment paths."""
    omitted = {"fingerprint", "local_path", "catalog_path", "source_path",
               "updated_at", "captured_at", "size_bytes"}

    def stable(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: stable(item) for key, item in value.items() if key not in omitted}
        if isinstance(value, (list, tuple)):
            return [stable(item) for item in value]
        return value

    encoded = json.dumps(stable(bundle), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_cultivation_document_bundle(
    item_id: int | str, *, session: Session | None = None,
    owner_user_id: int | None = None, export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Read catalog, related component documents and local attachment evidence.

    Supports fashion, pets, talismans, gongfa and nested catalog reward groups.
    Inventory is used only to resolve identity/note references, never as live
    progression. IDs outrank exact normalized names; ambiguous matches raise
    CultivationDocumentAmbiguity. Missing evidence is returned explicitly.

    An optional existing Session/owner ID supports callers with an established
    application context. Otherwise public account listing resolves the existing
    Fanxiu owner (it does not invoke the auto-creating get_fanxiu_user endpoint).
    The returned bundle includes no inferred upgrade costs or reward quantities:
    catalog reward rows are relations whose selection semantics may need a
    separate gameplay contract. Fingerprint changes with note/image contents.
    """
    if session is None:
        from backend.db import get_session
        generator = get_session()
        local_session = next(generator)
        try:
            return load_cultivation_document_bundle(
                item_id, session=local_session, owner_user_id=owner_user_id,
                export_root=export_root)
        finally:
            generator.close()

    from backend.core.fanxiu.catalog.inventory import (
        load_magic_treasure_hall, load_spirit_beast_hall, load_wardrobe_hall,
    )
    from backend.core.fanxiu.catalog.inventory_snapshot_store import load_inventory_hall_snapshot
    from backend.core.fanxiu.catalog.item import get_fanxiu_item_card
    from backend.core.notes.refs import load_notes_by_refs
    from backend.core.settings import get_settings

    if owner_user_id is None:
        from backend.api.admin import list_accounts
        owners = [user for user in list_accounts(session=session)
                  if user.username == "凡修手游"]
        if len(owners) > 1:
            raise CultivationDocumentAmbiguity("凡修文档所属账号不唯一")
        owner_user_id = owners[0].id if owners else None

    def rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [dict(row) for value in payload.values() if isinstance(value, list)
                for row in value if isinstance(row, dict) and row.get("name")]

    halls = []
    for key, loader in (("wardrobe_hall", load_wardrobe_hall),
                        ("spirit_beast_hall", load_spirit_beast_hall),
                        ("magic_treasure_hall", load_magic_treasure_hall)):
        halls.append((key, rows(load_inventory_hall_snapshot(session, key) or {}), rows(loader())))
    halls.append(("gongfa_atlas", rows(load_inventory_hall_snapshot(session, "gongfa_atlas") or {}), []))
    catalog_cache: dict[str, dict[str, Any]] = {}

    def collect(requested: str, ancestors: tuple[str, ...] = ()) -> dict[str, Any]:
        if requested in ancestors or len(ancestors) >= 8:
            raise ValueError(f"道具组件关系循环或嵌套超过8层：{(*ancestors, requested)}")
        if requested not in catalog_cache:
            catalog_cache[requested] = get_fanxiu_item_card(
                requested, export_root=export_root, rebuild_missing=False)
        result = catalog_cache[requested]
        card = result["card"]
        identities = []
        note_refs: set[str] = set()
        primary_rows = [{**row, "_document_hall": key} for key, primary, _ in halls for row in primary]
        legacy_rows = [{**row, "_document_hall": key} for key, _, legacy in halls for row in legacy]
        match = match_cultivation_inventory(card, primary_rows)
        fallback = match_cultivation_inventory(
            card, [row for row in legacy_rows if not match or row["_document_hall"] == match["_document_hall"]]
        ) if not match or not match.get("note_id") else None
        if match is None:
            match = fallback
        if match is not None:
            note_id = str(match.get("note_id") or (fallback or {}).get("note_id") or "")
            identities.append({"hall": match["_document_hall"], "inventory_id": str(match.get("id") or ""),
                               "name": match.get("name"), "note_id": note_id or None})
            if note_id:
                note_refs.add(note_id)
        documents = []
        missing_notes = []
        resolved_notes = load_notes_by_refs(session, owner_user_id, note_refs) if owner_user_id is not None else {}
        for ref in sorted(note_refs):
            note = resolved_notes.get(ref)
            if note is None:
                missing_notes.append(ref)
            else:
                documents.append(cultivation_document_evidence(
                    note.model_dump(), attachments_dir=get_settings().attachments_dir))
        components = []
        for reward in card.get("optional_gift_rewards") or []:
            if reward.get("id") is not None:
                components.append({"item_id": reward["id"], "catalog_count": reward.get("count"),
                                   "selection_condition": reward.get("show_condition"),
                                   "bundle": collect(str(reward["id"]), (*ancestors, requested))})
        evidence = {
            "schema_version": 1, "item_id": card["id"], "name": card.get("name"),
            "catalog_path": result.get("catalog_path"),
            "catalog": {key: card[key] for key in (
                "description", "effect_description", "effect_details", "progression",
                "effect_value", "linked_fashion_id", "linked_pet_id", "linked_talisman_id",
                "linked_gongfa_id", "optional_gift_group_id") if key in card},
            "inventory_identity": identities[0] if identities else None,
            "documents": documents, "missing_note_ids": missing_notes,
            "document_status": "available" if documents else "not_found",
            "components": components,
            "component_grant_mode": "unresolved" if components else None,
            "upgrade_cost_status": "requires_structured_progression_or_document_extraction",
        }
        evidence["fingerprint"] = cultivation_evidence_fingerprint(evidence)
        return evidence

    return collect(str(item_id))
