"""Pure identity and evidence-cache contracts; no simulated game execution."""

import pytest

from backend.core.fanxiu.catalog.cultivation_documents import (
    CultivationDocumentAmbiguity,
    cultivation_document_evidence,
    cultivation_evidence_fingerprint,
    match_cultivation_inventory,
)


def test_identity_prefers_id_over_similar_or_renamed_name():
    rows = [{"id": "old-name", "fashion_id": 9, "name": "新名字"},
            {"id": "name-only", "name": "背饰·同名"}]
    assert match_cultivation_inventory(
        {"id": 10, "name": "背饰·同名", "linked_fashion_id": 9}, rows
    )["id"] == "old-name"


def test_name_fallback_is_exact_and_rejects_conflicting_id():
    card = {"id": 10, "name": "幻化·祥鸟", "linked_pet_id": 9}
    assert match_cultivation_inventory(card, [{"id": "legacy", "name": "祥鸟"}])["id"] == "legacy"
    assert match_cultivation_inventory(card, [{"id": "x", "name": "祥鸟王"}]) is None
    assert match_cultivation_inventory(card, [{"id": "x", "name": "祥鸟", "pet_id": 8}]) is None


def test_ambiguous_name_fails_closed():
    with pytest.raises(CultivationDocumentAmbiguity):
        match_cultivation_inventory({"id": 10, "name": "幻化·祥鸟"},
                                    [{"id": "a", "name": "祥鸟"}, {"id": "b", "name": "祥鸟"}])


def test_image_content_invalidates_fingerprint_but_path_does_not(tmp_path):
    image_path = tmp_path / "evidence.png"
    image_path.write_bytes(b"first image")
    note = {"id": "1", "content": '<p>5阶：</p><img src="/static/attachments/evidence.png">'}
    first = cultivation_document_evidence(note, attachments_dir=tmp_path)
    assert first["attachments"][0]["preceding_text"].strip() == "5阶："
    before = cultivation_evidence_fingerprint(first)
    first["attachments"][0]["local_path"] = "different-deployment/evidence.png"
    assert cultivation_evidence_fingerprint(first) == before
    image_path.write_bytes(b"revised image")
    after = cultivation_document_evidence(note, attachments_dir=tmp_path)
    assert cultivation_evidence_fingerprint(after) != before


def test_attachment_paths_cannot_escape_local_root(tmp_path):
    evidence = cultivation_document_evidence(
        {"content": '<img src="/static/attachments/../outside.png"><img src="https://example.org/x.png">'},
        attachments_dir=tmp_path)
    assert [item["status"] for item in evidence["attachments"]] == [
        "invalid_local_reference", "external_or_unsupported"]
