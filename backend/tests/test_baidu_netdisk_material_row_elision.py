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


@pytest.mark.parametrize("native_name,provider_name", [
    ("课题资料(幼儿园到大学)sd-0c4258d910fd", "课题资料（幼儿园到大学）_sd-0c4258d910fd"),
    ("课题资料（幼儿园到大学）sd-0c4258d910fd", "课题资料(幼儿园到大学)_sd-0c4258d910fd"),
])
def test_parenthesis_ocr_alias_keeps_exact_provider_name(native_name, provider_name):
    result, reason = canonical_entries("/materials", rows([native_name]), listing([provider_name]))
    assert reason == "ok"
    assert result[0]["name"] == provider_name


def test_parenthesis_alias_collision_is_refused_before_pairing():
    names = ["资料（甲）_sd-abcdef123456", "资料(甲)_sd-abcdef123456"]
    assert canonical_entries("/materials", rows(names), listing(names)) == (None, "provider_name_collision")


def test_parenthesis_alias_does_not_fold_other_punctuation():
    assert canonical_entries("/materials", rows(["资料[甲]sd-abcdef123456"]),
                             listing(["资料（甲）_sd-abcdef123456"])) == (None, "unpaired_row")


def test_measured_axure_parent_rows_pair_without_general_name_matching():
    names = ["Axure 后台管理系统原型（CRM、ERP、CMS、OA、TMS、WMS）_sd-53eae0a6cd46",
             "Axure 高保真原型模板与交互元件（App、Web）_sd-aa7aa11ed6ec"]
    native = ["Axure 高保真原型模板与交互元件(App. Web） sd-aa7aa11ed6ec",
              "Axure 后台管理系统原型(CRM、ERP、CMS、OA、TMS、WMS) sd-53eae0a6.."]
    result, reason = canonical_entries("/materials", rows(native), listing(names))
    assert reason == "ok"
    assert [r["name"] for r in result] == list(reversed(names))


def test_ascii_label_separator_alias_collisions_are_rejected():
    names = ["原型（App、Web）_sd-abcdef123456", "原型(App.Web)_sd-abcdef123456"]
    assert canonical_entries("/materials", rows(names), listing(names)) == (None, "provider_name_collision")


@pytest.mark.parametrize("native", ["资料sd-abcdef1..", "错字sd-abcdef12..", "资料sd-abcdef13.."])
def test_two_dot_clip_keeps_strict_prefix_and_eight_hex_minimum(native):
    assert canonical_entries("/materials", rows([native]),
                             listing(["资料_sd-abcdef123456"])) == (None, "unpaired_row")
