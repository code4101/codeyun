"""Long native material rows must have a unique exact prefix in the full listing."""
import pytest

from backend.core.baidu_netdisk.client import canonical_entries


def listing(names):
    return {"status": "listed", "path": "/materials", "count": len(names),
            "entries": [{"id": str(i), "name": name, "path": "/materials/" + name,
                         "is_dir": True} for i, name in enumerate(names)]}


def rows(names):
    return [{"name": name, "kind": "dir", "size": None} for name in names]


@pytest.mark.parametrize("ellipsis", ["...", "…"])
def test_long_material_rows_pair_without_depending_on_provider_order(ellipsis):
    names = ["ISO 审核资料_sd-134c6e696574", "评审手册_sd-e1e221845b0f"]
    observed = ["评审手册sd-e1e221845" + ellipsis, "ISO审核资料sd-134c6" + ellipsis]
    result, reason = canonical_entries("/materials", rows(observed), listing(names))
    assert reason == "ok"
    assert [entry["name"] for entry in result] == list(reversed(names))


@pytest.mark.parametrize("observed", ["ISO审核资料sd-134c6", "ISO审核资料sd-134c...",
                                      "ISO审核资料sd-134c7...", "ISO审核材料sd-134c6...",
                                      "ISO审核资料...", "ISO审核资料sd-134c6.."])
def test_missing_ellipsis_short_id_or_any_prefix_mismatch_is_refused(observed):
    assert canonical_entries("/materials", rows([observed]),
                             listing(["ISO审核资料_sd-134c6e696574"])) == (None, "unpaired_row")


def test_prefix_collision_refused_even_after_one_candidate_was_consumed():
    names = ["ISO审核资料_sd-134c6e696574", "ISO审核资料_sd-134c6abcdef0"]
    assert canonical_entries("/materials", rows([names[0], "ISO审核资料sd-134c6..."]),
                             listing(names)) == (None, "unpaired_row")


def test_two_native_rows_cannot_reuse_one_provider_entry():
    names = ["ISO审核资料_sd-134c6e696574", "其他_sd-abcdef123456"]
    assert canonical_entries("/materials", rows(["ISO审核资料sd-134c6..."] * 2),
                             listing(names)) == (None, "unpaired_row")


def test_elision_keeps_kind_check_and_exact_parent_check():
    observed = rows(["ISO审核资料sd-134c6..."])
    observed[0]["kind"] = "file"
    provider = listing(["ISO审核资料_sd-134c6e696574"])
    assert canonical_entries("/materials", observed, provider) == (None, "kind_mismatch")
    assert canonical_entries("/other", observed, provider) == (None, "provider_path_mismatch")
