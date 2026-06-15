from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import scripts.verify_fanxiu_wiki_browser as browser
import scripts.verify_fanxiu_card_catalogs as card_catalogs
import scripts.verify_fanxiu_resource_links as resource_links
import scripts.verify_fanxiu_wiki_endpoints as endpoints
import scripts.verify_fanxiu_wiki_icons as icons
import scripts.verify_fanxiu_wiki_quality as quality
from backend.core.fanxiu.catalog.item_icon_quality import build_item_icon_quality_report, load_item_icon_quality_review


def _healthy_contact_sheet_path() -> str:
    path = Path(tempfile.gettempdir()) / "codeyun" / "fanxiu_item_icon_quality_test_contact_sheet.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    with Image.new("RGB", (2, 2), (255, 255, 255)) as image:
        image.save(path, format="PNG")
    return str(path)


def _args(**overrides):
    values = {
        "api_timeout": 15,
        "icon_timeout": 60,
        "wait_ms": 12000,
        "scroll_steps": 1,
        "core_scroll_steps": 1,
        "item_scroll_steps": 3,
        "mail_page_limit": 20,
        "resource_samples_per_type": 5,
        "local_auth_user": "admin",
        "item_type_label": ["云梦论剑", "时装"],
        "skip_browser": False,
        "skip_frontend": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _healthy_payload(name: str) -> dict:
    if name == "mail_records_final":
        name = "mail_records"
    payloads = {
        "mail_records": {
            "records": 2,
            "reward_items": 3,
            "reward_items_with_icon": 3,
            "reward_items_with_name": 3,
            "reward_items_with_item_id": 3,
            "with_content": 2,
            "icon_endpoint_failures": [],
            "missing_content_records": [],
            "weak_content_records": [],
            "malformed_content_records": [],
            "missing_reward_fields": [],
            "required_content_failures": [],
        },
        "wiki_icons": {
            "unique_icons": 3001,
            "icon_uses": 9000,
            "icon_quality_risk_counts_by_type": {
                "high_reuse_primary_icon": 10,
                "high_reuse_small_icon": 12,
            },
            "icon_quality_risk_counts_by_scope": {
                "item_catalog_only": 17,
                "cross_catalog": 5,
            },
            "icon_endpoint_failures": 0,
            "api_endpoint_errors": 0,
            "browser_broken_image_endpoint_failures": 0,
        },
        "item_icons": {
            "card_count": 10030,
            "unique_icon_count": 3295,
            "ok": 3295,
            "fail": 0,
            "alias_count": 3,
        },
        "item_icon_quality_report": {
            "item_count": 10030,
            "group_count": 21,
            "primary_group_count": 9,
            "small_group_count": 12,
            "high_priority_count": 10,
            "medium_priority_count": 6,
            "low_priority_count": 5,
            "candidate_group_count": 18,
            "candidate_icon_total": 24,
            "no_candidate_group_count": 3,
            "unresolved_no_candidate_group_count": 3,
            "no_candidate_review_status": "pending_manual_decision",
            "no_candidate_groups": [
                {
                    "field": "icon",
                    "icon": "icon2_item_9034",
                    "count": 65,
                    "review_priority": "high",
                    "sample": {"id": "1300605", "name": "灵材宝匣"},
                    "nearby_icon_count": 2,
                    "nearby_icons": [{"icon": "icon2_item_9033"}, {"icon": "icon2_item_9035"}],
                    "review_status": "pending_manual_decision",
                    "suggested_manual_action": "continue_static_asset_search",
                    "recommended_action": "主图主要用于“礼包宝匣”，但仍混有其他类型；优先检查非主类型样本是否误用。",
                    "remaining_risk": "No same-number or same-family exported asset candidate was found.",
                },
                {
                    "field": "icon",
                    "icon": "icon5_item_zw_9716",
                    "count": 50,
                    "review_priority": "high",
                    "sample": {"id": "5012047", "name": "芙莲神心诀"},
                    "nearby_icon_count": 3,
                    "nearby_icons": [{"icon": "icon5_item_9715"}, {"icon": "icon6_item_9717"}],
                    "review_status": "pending_manual_decision",
                    "suggested_manual_action": "continue_static_asset_search",
                    "recommended_action": "主图主要用于“功法”，但仍混有其他类型；优先检查非主类型样本是否误用。",
                    "remaining_risk": "No same-number or same-family exported asset candidate was found.",
                },
                {
                    "field": "icon",
                    "icon": "icon5_item_9503",
                    "count": 76,
                    "review_priority": "medium",
                    "sample": {"id": "20401001", "name": "攻击玉·陈巧倩"},
                    "nearby_icon_count": 2,
                    "nearby_icons": [{"icon": "icon5_item_9502"}, {"icon": "icon5_item_9504"}],
                    "review_status": "pending_manual_decision",
                    "suggested_manual_action": "review_exemption_candidate",
                    "recommended_action": "主图几乎只用于“材料”类型；可能是游戏配置的类型通用图，优先决定是否标记豁免。",
                    "remaining_risk": "No same-number or same-family exported asset candidate was found.",
                },
            ],
            "nearby_context_group_count": 3,
            "nearby_context_icon_total": 7,
            "no_candidate_contact_sheet_path": _healthy_contact_sheet_path(),
        },
        "wiki_endpoints": {
            "endpoint_count": 21,
            "failure_count": 0,
            "slow_count": 0,
            "auth_expected_count": 3,
        },
        "reverse_manifest": {
            "entry_count": 34,
            "raw_input_count": 13,
            "hashed_file_count": 26,
            "directory_summary_count": 8,
            "missing_count": 0,
            "manifest_digest": "0" * 64,
        },
        "card_catalogs": {
            "catalog_count": 6,
            "total_cards": 40000,
            "sample_count": 48,
            "details_ok": 40,
            "detail_failure_count": 0,
            "field_failure_count": 0,
            "icon_failure_count": 0,
        },
        "resource_links": {
            "resource_count": 39,
            "required_resource_count": 1,
            "required_resource_failure_count": 0,
            "mail_reward_resource_count": 8,
            "mail_reward_resource_failure_count": 0,
            "icon_review_resource_count": 8,
            "icon_review_resource_failure_count": 0,
            "loaded_icon_count": 34,
            "required_icon_count": 34,
            "failure_count": 0,
            "title_mismatch_count": 0,
            "icon_mismatch_count": 0,
        },
        "mail_browser": {
            "mail_row_count": 2,
            "mail_reward_image_count": 3,
            "mail_item_link_count": 3,
            "mail_content_button_count": 2,
            "request_failure_count": 0,
            "mail_broken_visible_reward_image_count": 0,
            "mail_missing_icon_slot_count": 0,
            "mail_empty_alt_image_count": 0,
            "mail_invalid_item_link_count": 0,
            "mail_required_content_check_count": 2,
            "mail_required_content_failure_count": 0,
            "mail_content_dialog_ok": True,
            "mail_item_link_navigation_ok": True,
        },
        "item_browser": {
            "item_row_icon_count": 10,
            "item_route_filter_count": 2,
            "item_route_filter_failure_count": 0,
            "item_route_clear_count": 1,
            "item_route_clear_failure_count": 0,
            "item_icon_review_count": 1,
            "item_icon_review_failure_count": 0,
            "item_icon_route_check_count": 2,
            "item_icon_route_check_failure_count": 0,
            "request_failure_count": 0,
            "broken_visible_image_count": 0,
            "hard_image_failure_count": 0,
            "item_row_icon_missing_count": 0,
            "item_row_icon_mismatch_count": 0,
            "item_row_icon_wait_timeout_count": 0,
            "item_row_missing_id_count": 0,
            "item_row_fallback_visible_count": 0,
        },
        "core_browser": {
            "observation_count": 5,
            "core_tab_count": 5,
            "core_tab_with_rows_count": 5,
            "core_tab_image_required_count": 4,
            "core_tab_with_visible_images_count": 4,
            "request_failure_count": 0,
            "broken_visible_image_count": 0,
            "hard_image_failure_count": 0,
        },
    }
    return payloads.get(name, {"ok": True})


def test_build_item_icon_quality_report_triages_primary_and_corner_icons(tmp_path):
    catalog_dir = tmp_path / "parsed_configs" / "item_catalog"
    catalog_dir.mkdir(parents=True)
    cards = []
    for index in range(50):
        cards.append(
            {
                "id": 1000 + index,
                "name": f"跨类道具{index}",
                "icon": "icon_item_0180",
                "small_icon": f"small_{index % 20}",
                "icon_reuse_count": 50,
                "small_icon_reuse_count": 3,
                "type_name": "材料" if index < 30 else "礼包宝匣",
                "type_sub_type_name": "材料 · 通用" if index < 30 else "礼包宝匣 · 活动",
                "quality_name": "黄色品质",
                "source_table": "Item",
            }
        )
    for index in range(50):
        cards.append(
            {
                "id": 2000 + index,
                "name": f"角标道具{index}",
                "icon": f"main_{index}",
                "small_icon": "common_corner_skill_0101",
                "icon_reuse_count": 1,
                "small_icon_reuse_count": 50,
                "type_name": "功法",
                "type_sub_type_name": "功法 · 通用",
                "quality_name": "紫色品质",
                "source_table": "Item",
            }
        )
    (catalog_dir / "item_catalog.json").write_text(
        json.dumps({"schema_version": 50, "stats": {}, "cards": cards}, ensure_ascii=False),
        encoding="utf-8",
    )
    icon_dir = tmp_path / "icons"
    icon_dir.mkdir()
    (icon_dir / "icon3_item_0180.png").write_bytes(b"png")
    (icon_dir / "common_corner_skill_0102.png").write_bytes(b"png")
    (icon_dir / "unrelated_item_9999.png").write_bytes(b"png")

    summary = build_item_icon_quality_report(export_root=tmp_path, threshold=50)

    assert summary["group_count"] == 2
    assert summary["primary_group_count"] == 1
    assert summary["small_group_count"] == 1
    assert summary["candidate_group_count"] == 1
    assert summary["candidate_icon_total"] == 1
    assert summary["no_candidate_group_count"] == 1
    assert summary["unresolved_no_candidate_group_count"] == 1
    assert summary["no_candidate_review_status"] == "pending_manual_decision"
    assert summary["no_candidate_groups"][0]["icon"] == "common_corner_skill_0101"
    assert summary["no_candidate_groups"][0]["review_status"] == "pending_manual_decision"
    assert summary["no_candidate_groups"][0]["suggested_manual_action"] == "manual_visual_compare"
    assert summary["no_candidate_groups"][0]["remaining_risk"]
    assert summary["no_candidate_groups"][0]["sample"]["id"] == "2000"
    assert summary["no_candidate_groups"][0]["nearby_icons"][0]["icon"] == "common_corner_skill_0102"
    assert summary["nearby_context_group_count"] == 1
    assert summary["nearby_context_icon_total"] == 1
    assert summary["no_candidate_contact_sheet_path"]
    assert (tmp_path / "parsed_configs" / "item_catalog" / "icon_quality_review" / "item_icon_no_candidate_contact_sheet_latest.png").is_file()
    report_path = tmp_path / "parsed_configs" / "item_catalog" / "icon_quality_review" / "item_icon_quality_review_latest.tsv"
    text = report_path.read_text(encoding="utf-8-sig")
    assert "icon_item_0180" in text
    assert "icon3_item_0180" in text
    assert "跨类型主图混用" in text
    assert "common_corner_skill_0101" in text
    assert "角标类小图标" in text
    markdown_path = tmp_path / "parsed_configs" / "item_catalog" / "icon_quality_review" / "item_icon_quality_review_latest.md"
    markdown_text = markdown_path.read_text(encoding="utf-8")
    assert "Groups without existing-asset candidates" in markdown_text
    assert "No Candidate Groups" in markdown_text
    assert "pending_manual_decision" in markdown_text
    assert "manual_visual_compare" in markdown_text
    assert "2000:角标道具0" in markdown_text
    assert "common_corner_skill_0102" in markdown_text
    assert "../../../icons/common_corner_skill_0101.png" in markdown_text
    assert "../../../icons/common_corner_skill_0102.png" in markdown_text
    assert "item_icon_no_candidate_contact_sheet_latest.png" in markdown_text
    loaded = load_item_icon_quality_review(export_root=tmp_path, threshold=50, rebuild_missing=False)
    primary_row = next(row for row in loaded["items"] if row["icon"] == "icon_item_0180")
    assert primary_row["representative_samples"][0]["id"] == "1000"
    assert primary_row["representative_samples"][0]["name"] == "跨类道具0"
    assert "samples_json" in primary_row
    assert primary_row["candidate_icons"][0]["icon"] == "icon3_item_0180"
    assert primary_row["candidate_icons"][0]["reason"]


def test_load_item_icon_quality_review_rebuilds_when_threshold_changes(tmp_path):
    catalog_dir = tmp_path / "parsed_configs" / "item_catalog"
    catalog_dir.mkdir(parents=True)
    cards = [
        {
            "id": index,
            "name": f"复用道具{index}",
            "icon": "shared_icon",
            "icon_reuse_count": 60,
            "type_name": "材料",
            "type_sub_type_name": "材料 · 通用",
            "quality_name": "黄色品质",
            "source_table": "Item",
        }
        for index in range(60)
    ]
    (catalog_dir / "item_catalog.json").write_text(
        json.dumps({"schema_version": 50, "stats": {}, "cards": cards}, ensure_ascii=False),
        encoding="utf-8",
    )

    first = load_item_icon_quality_review(export_root=tmp_path, threshold=50)
    second = load_item_icon_quality_review(export_root=tmp_path, threshold=70)

    assert first["summary"]["threshold"] == 50
    assert first["total"] == 1
    assert second["summary"]["threshold"] == 70
    assert second["total"] == 0


def test_icon_review_resource_samples_are_spread_across_groups():
    review_rows = [
        {
            "field": "small_icon",
            "icon": "group_a",
            "representative_samples": [{"id": f"a{index}"} for index in range(10)],
        },
        {
            "field": "icon",
            "icon": "group_b",
            "representative_samples": [{"id": f"b{index}"} for index in range(10)],
        },
        {
            "field": "small_icon",
            "icon": "group_c",
            "representative_samples": [{"id": f"c{index}"} for index in range(10)],
        },
    ]

    refs = resource_links._select_icon_review_sample_refs(review_rows, 5)

    assert [ref["item_id"] for ref in refs] == ["a0", "b0", "c0", "a1", "b1"]
    assert {ref["icon_review_group"] for ref in refs[:3]} == {
        "small_icon:group_a",
        "icon:group_b",
        "small_icon:group_c",
    }


def test_quality_steps_skip_browser_keeps_data_and_resource_gates():
    steps = quality._quality_steps(_args(skip_browser=True, skip_frontend=True))

    names = [name for name, _command, _timeout in steps]
    assert names == [
        "reverse_boundary",
        "reverse_manifest",
        "mail_records",
        "wiki_endpoints",
        "wiki_icons",
        "item_icons",
        "item_icon_quality_report",
        "card_catalogs",
        "resource_links",
    ]
    assert any("verify_fanxiu_mail_records.py" in part for _name, command, _timeout in steps for part in command)
    assert any("--full-items" in command for _name, command, _timeout in steps)
    assert any("verify_fanxiu_item_icons.py" in part for _name, command, _timeout in steps for part in command)
    assert any("build_fanxiu_item_icon_quality_report.py" in part for _name, command, _timeout in steps for part in command)
    resource_command = next(command for name, command, _timeout in steps if name == "resource_links")
    assert "--samples-per-type" in resource_command
    assert "5" in resource_command
    assert "--required-resource" in resource_command
    assert "item:3080008" in resource_command
    assert "--mail-reward-resource-samples" in resource_command
    assert "8" in resource_command
    assert "--icon-review-resource-samples" in resource_command
    assert "8" in resource_command


def test_quality_steps_include_frontend_typecheck_and_build_by_default():
    steps = quality._quality_steps(_args(skip_browser=True))

    names = [name for name, _command, _timeout in steps]
    assert names == [
        "reverse_boundary",
        "reverse_manifest",
        "mail_records",
        "wiki_endpoints",
        "wiki_icons",
        "item_icons",
        "item_icon_quality_report",
        "card_catalogs",
        "resource_links",
        "frontend_typecheck",
        "frontend_build",
    ]
    commands = {name: command for name, command, _timeout in steps}
    assert commands["frontend_typecheck"][1:] == ["run", "typecheck", "--prefix", "frontend"]
    assert commands["frontend_build"][1:] == ["run", "build", "--prefix", "frontend"]


def test_wiki_icon_quality_risks_distinguish_primary_and_small_icons():
    rows = icons._build_icon_quality_risk_rows(
        {
            "shared_primary": [
                icons.IconUse("item_catalog", str(index), f"主图标道具{index}", "icon", "shared_primary")
                for index in range(50)
            ],
            "shared_small": [
                icons.IconUse("item_catalog", str(index), f"小图标道具{index}", "small_icon", "shared_small")
                for index in range(50)
            ],
            "shared_head": [
                icons.IconUse("profile_catalog", str(index), f"头像道具{index}", "head_icon", "shared_head")
                for index in range(50)
            ],
            "cross_catalog_primary": [
                *[
                    icons.IconUse("item_catalog", str(index), f"道具目录{index}", "icon", "cross_catalog_primary")
                    for index in range(49)
                ],
                *[
                    icons.IconUse("gongfa_api_full", str(index), f"功法目录{index}", "icon", "cross_catalog_primary")
                    for index in range(5)
                ],
            ],
        },
        primary_reuse_threshold=50,
    )

    rows_by_icon = {row["icon"]: row for row in rows}
    risks_by_icon = {row["icon"]: row["risk"] for row in rows}
    assert risks_by_icon["shared_primary"] == "high_reuse_primary_icon"
    assert risks_by_icon["shared_small"] == "high_reuse_small_icon"
    assert risks_by_icon["shared_head"] == "high_reuse_head_icon"
    assert risks_by_icon["cross_catalog_primary"] == "high_reuse_primary_icon"
    assert rows_by_icon["cross_catalog_primary"]["source_scope"] == "cross_catalog"
    assert rows_by_icon["cross_catalog_primary"]["item_catalog_use_count"] == 49
    assert rows_by_icon["cross_catalog_primary"]["primary_use_count"] == 54
    assert rows_by_icon["cross_catalog_primary"]["sources"] == "gongfa_api_full,item_catalog"
    assert icons._count_icon_quality_risks_by_type(rows) == {
        "high_reuse_primary_icon": 2,
        "high_reuse_small_icon": 1,
        "high_reuse_head_icon": 1,
    }
    assert icons._count_icon_quality_risks_by_scope(rows) == {
        "cross_catalog": 1,
        "item_catalog_only": 2,
        "other_catalog_only": 1,
    }


def test_quality_steps_browser_checks_mail_and_each_requested_item_type():
    steps = quality._quality_steps(_args(item_type_label=["云梦论剑", "功法"]))

    names = [name for name, _command, _timeout in steps]
    assert names == [
        "reverse_boundary",
        "reverse_manifest",
        "mail_records",
        "wiki_endpoints",
        "wiki_icons",
        "item_icons",
        "item_icon_quality_report",
        "card_catalogs",
        "resource_links",
        "frontend_typecheck",
        "frontend_build",
        "mail_browser",
        "mail_records_final",
        "item_browser",
        "core_browser",
    ]
    mail_command = next(command for name, command, _timeout in steps if name == "mail_browser")
    assert "--mail-page-limit" in mail_command
    assert "20" in mail_command
    item_command = next(command for name, command, _timeout in steps if name == "item_browser")
    assert item_command.count("--item-type-label") == 2
    assert "云梦论剑" in item_command
    assert "功法" in item_command
    core_command = next(command for name, command, _timeout in steps if name == "core_browser")
    assert "gongfa" in core_command
    assert "activity" in core_command
    assert "doupotd" in core_command


def test_verify_quality_reports_failed_steps(monkeypatch):
    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 1 if name == "wiki_icons" else 0,
            "command": " ".join(command),
            "output": "" if name == "wiki_icons" else json.dumps(_healthy_payload(name), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert summary["failed_steps"] == ["wiki_icons"]
    assert summary["step_count"] == 9


def test_verify_quality_retries_transient_service_failure(monkeypatch):
    calls = {"wiki_icons": 0}

    def fake_run_step(name, command, *, timeout):
        if name == "wiki_icons":
            calls["wiki_icons"] += 1
            if calls["wiki_icons"] == 1:
                return {
                    "name": name,
                    "returncode": 1,
                    "command": " ".join(command),
                    "output": "Max retries exceeded: Failed to establish a new connection",
                }
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(_healthy_payload(name), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)
    monkeypatch.setattr(quality.time, "sleep", lambda _seconds: None)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))
    wiki_icons = next(row for row in summary["steps"] if row["name"] == "wiki_icons")

    assert summary["ok"] is True
    assert calls["wiki_icons"] == 2
    assert wiki_icons["retry_count"] == 1
    assert "Failed to establish a new connection" in wiki_icons["output"]


def test_verify_quality_retries_read_timeout_failure(monkeypatch):
    calls = {"wiki_endpoints": 0}

    def fake_run_step(name, command, *, timeout):
        if name == "wiki_endpoints":
            calls["wiki_endpoints"] += 1
            if calls["wiki_endpoints"] == 1:
                return {
                    "name": name,
                    "returncode": 1,
                    "command": " ".join(command),
                    "output": "ReadTimeout: HTTPConnectionPool read timeout=30",
                }
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(_healthy_payload(name), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)
    monkeypatch.setattr(quality.time, "sleep", lambda _seconds: None)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))
    wiki_endpoints = next(row for row in summary["steps"] if row["name"] == "wiki_endpoints")

    assert summary["ok"] is True
    assert calls["wiki_endpoints"] == 2
    assert wiki_endpoints["retry_count"] == 1


def test_verify_quality_does_not_retry_non_transient_failure(monkeypatch):
    calls = {"wiki_icons": 0}

    def fake_run_step(name, command, *, timeout):
        if name == "wiki_icons":
            calls["wiki_icons"] += 1
            return {
                "name": name,
                "returncode": 1,
                "command": " ".join(command),
                "output": "icon_endpoint_failures: missing sprite",
            }
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(_healthy_payload(name), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert calls["wiki_icons"] == 1
    assert summary["failed_steps"] == ["wiki_icons"]


def test_verify_quality_passes_when_mail_database_and_browser_counts_match(monkeypatch):
    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": f"log line\n{json.dumps(_healthy_payload(name), ensure_ascii=False)}\n",
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args())

    assert summary["ok"] is True
    assert summary["consistency_failures"] == []


def test_verify_quality_fails_when_mail_database_and_browser_counts_diverge(monkeypatch):
    payloads = {
        "mail_records": _healthy_payload("mail_records"),
        "mail_records_final": _healthy_payload("mail_records"),
        "mail_browser": {
            **_healthy_payload("mail_browser"),
            "mail_reward_image_count": 2,
        },
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": f"log line\n{json.dumps(payloads.get(name, {'ok': True}), ensure_ascii=False)}\n",
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args())

    assert summary["ok"] is False
    assert "cross_layer_consistency" in summary["failed_steps"]
    assert summary["consistency_failures"][0]["kind"] == "mail_reward_image_count_outside_snapshot_range"


def test_verify_quality_accepts_mail_growth_between_browser_and_final_snapshot(monkeypatch):
    payloads = {
        "mail_records": _healthy_payload("mail_records"),
        "mail_records_final": {
            **_healthy_payload("mail_records"),
            "records": 3,
            "reward_items": 4,
            "reward_items_with_icon": 4,
            "reward_items_with_name": 4,
            "reward_items_with_item_id": 4,
            "with_content": 3,
        },
        "mail_browser": _healthy_payload("mail_browser"),
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": f"log line\n{json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False)}\n",
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args())

    assert summary["ok"] is True
    assert summary["consistency_failures"] == []


def test_verify_quality_fails_when_mail_summary_loses_content(monkeypatch):
    payloads = {
        "mail_records": {
            **_healthy_payload("mail_records"),
            "with_content": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "mail_records"
    assert summary["summary_failures"][0]["field"] == "with_content"


def test_verify_quality_fails_when_mail_browser_required_content_check_fails(monkeypatch):
    payloads = {
        "mail_browser": {
            **_healthy_payload("mail_browser"),
            "mail_required_content_failure_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args())

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert any(
        row["step"] == "mail_browser" and row["field"] == "mail_required_content_failure_count"
        for row in summary["summary_failures"]
    )


def test_verify_quality_fails_when_item_icon_review_has_no_candidates(monkeypatch):
    payloads = {
        "item_icon_quality_report": {
            **_healthy_payload("item_icon_quality_report"),
            "candidate_group_count": 0,
            "candidate_icon_total": 0,
            "no_candidate_group_count": 21,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert any(
        row["step"] == "item_icon_quality_report" and row["field"] == "candidate_group_count"
        for row in summary["summary_failures"]
    )


def test_verify_quality_fails_when_item_icon_review_has_no_candidate_icons(monkeypatch):
    payloads = {
        "item_icon_quality_report": {
            **_healthy_payload("item_icon_quality_report"),
            "candidate_icon_total": 0,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert any(
        row["step"] == "item_icon_quality_report" and row["field"] == "candidate_icon_total"
        for row in summary["summary_failures"]
    )


def test_verify_quality_fails_when_item_icon_review_omits_no_candidate_details(monkeypatch):
    payloads = {
        "item_icon_quality_report": {
            **_healthy_payload("item_icon_quality_report"),
            "no_candidate_groups": [],
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert any(
        row["step"] == "item_icon_quality_report" and row["field"] == "no_candidate_groups"
        for row in summary["summary_failures"]
    )


def test_verify_quality_fails_when_item_icon_review_no_candidate_detail_lacks_sample(monkeypatch):
    payloads = {
        "item_icon_quality_report": {
            **_healthy_payload("item_icon_quality_report"),
            "no_candidate_groups": [
                {
                    "field": "icon",
                    "icon": "icon2_item_9034",
                    "review_priority": "high",
                    "sample": {},
                },
                *_healthy_payload("item_icon_quality_report")["no_candidate_groups"][1:],
            ],
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert any(
        row["step"] == "item_icon_quality_report" and row["field"] == "no_candidate_groups[0]"
        for row in summary["summary_failures"]
    )


def test_verify_quality_fails_when_item_icon_review_no_candidate_detail_lacks_status(monkeypatch):
    first_group = dict(_healthy_payload("item_icon_quality_report")["no_candidate_groups"][0])
    first_group.pop("review_status")
    first_group.pop("suggested_manual_action")
    first_group.pop("remaining_risk")
    payloads = {
        "item_icon_quality_report": {
            **_healthy_payload("item_icon_quality_report"),
            "no_candidate_groups": [
                first_group,
                *_healthy_payload("item_icon_quality_report")["no_candidate_groups"][1:],
            ],
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert any(
        row["step"] == "item_icon_quality_report" and row["field"] == "no_candidate_groups[0]"
        for row in summary["summary_failures"]
    )


def test_verify_quality_fails_when_item_icon_review_contact_sheet_is_missing(monkeypatch):
    payloads = {
        "item_icon_quality_report": {
            **_healthy_payload("item_icon_quality_report"),
            "no_candidate_contact_sheet_path": str(Path(tempfile.gettempdir()) / "codeyun" / "missing_contact_sheet.png"),
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert any(
        row["step"] == "item_icon_quality_report"
        and row["field"] == "no_candidate_contact_sheet_path"
        and row["kind"] == "contact_sheet_missing"
        for row in summary["summary_failures"]
    )


def test_verify_quality_fails_when_mail_summary_has_weak_content(monkeypatch):
    payloads = {
        "mail_records": {
            **_healthy_payload("mail_records"),
            "weak_content_records": [{"title": "丹道问鼎奖励", "content": "参数：数值=5"}],
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "mail_records"
    assert summary["summary_failures"][0]["field"] == "weak_content_records"


def test_verify_quality_fails_when_reverse_manifest_has_missing_entries(monkeypatch):
    payloads = {
        "reverse_manifest": {
            **_healthy_payload("reverse_manifest"),
            "missing_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "reverse_manifest"
    assert summary["summary_failures"][0]["field"] == "missing_count"


def test_verify_quality_fails_when_reverse_manifest_digest_is_invalid(monkeypatch):
    payloads = {
        "reverse_manifest": {
            **_healthy_payload("reverse_manifest"),
            "manifest_digest": "not-a-sha",
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "reverse_manifest"
    assert summary["summary_failures"][0]["field"] == "manifest_digest"


def test_verify_quality_fails_when_wiki_icon_summary_has_endpoint_failures(monkeypatch):
    payloads = {
        "wiki_icons": {
            **_healthy_payload("wiki_icons"),
            "icon_endpoint_failures": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "wiki_icons"
    assert summary["summary_failures"][0]["field"] == "icon_endpoint_failures"


def test_verify_quality_fails_when_item_icon_export_has_failures(monkeypatch):
    payloads = {
        "item_icons": {
            **_healthy_payload("item_icons"),
            "ok": 3294,
            "fail": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "item_icons"
    assert summary["summary_failures"][0]["field"] == "ok"


def test_verify_quality_fails_when_wiki_endpoint_summary_has_failures(monkeypatch):
    payloads = {
        "wiki_endpoints": {
            **_healthy_payload("wiki_endpoints"),
            "failure_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "wiki_endpoints"
    assert summary["summary_failures"][0]["field"] == "failure_count"


def test_verify_quality_fails_when_card_catalog_summary_has_icon_failures(monkeypatch):
    payloads = {
        "card_catalogs": {
            **_healthy_payload("card_catalogs"),
            "icon_failure_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "card_catalogs"
    assert summary["summary_failures"][0]["field"] == "icon_failure_count"


def test_verify_quality_fails_when_card_catalog_sampling_regresses(monkeypatch):
    payloads = {
        "card_catalogs": {
            **_healthy_payload("card_catalogs"),
            "sample_count": 24,
            "details_ok": 20,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "card_catalogs"
    assert summary["summary_failures"][0]["field"] == "sample_count"


def test_verify_quality_fails_when_resource_link_summary_has_failures(monkeypatch):
    payloads = {
        "resource_links": {
            **_healthy_payload("resource_links"),
            "failure_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "resource_links"
    assert summary["summary_failures"][0]["field"] == "failure_count"


def test_verify_quality_fails_when_mail_reward_resource_pages_are_not_checked(monkeypatch):
    payloads = {
        "resource_links": {
            **_healthy_payload("resource_links"),
            "mail_reward_resource_count": 0,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "resource_links"
    assert summary["summary_failures"][0]["field"] == "mail_reward_resource_count"


def test_verify_quality_fails_when_icon_review_resource_pages_are_not_checked(monkeypatch):
    payloads = {
        "resource_links": {
            **_healthy_payload("resource_links"),
            "icon_review_resource_count": 0,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_browser=True, skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "resource_links"
    assert summary["summary_failures"][0]["field"] == "icon_review_resource_count"


def test_verify_quality_fails_when_item_browser_summary_shows_visible_fallback(monkeypatch):
    payloads = {
        "item_browser": {
            **_healthy_payload("item_browser"),
            "item_row_fallback_visible_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "item_browser"
    assert summary["summary_failures"][0]["field"] == "item_row_fallback_visible_count"


def test_verify_quality_fails_when_item_browser_skips_route_filter_check(monkeypatch):
    payloads = {
        "item_browser": {
            **_healthy_payload("item_browser"),
            "item_route_filter_count": 0,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "item_browser"
    assert summary["summary_failures"][0]["field"] == "item_route_filter_count"


def test_verify_quality_fails_when_item_browser_skips_route_clear_check(monkeypatch):
    payloads = {
        "item_browser": {
            **_healthy_payload("item_browser"),
            "item_route_clear_count": 0,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "item_browser"
    assert summary["summary_failures"][0]["field"] == "item_route_clear_count"


def test_verify_quality_fails_when_item_browser_skips_icon_route_checks(monkeypatch):
    payloads = {
        "item_browser": {
            **_healthy_payload("item_browser"),
            "item_icon_route_check_count": 0,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "item_browser"
    assert summary["summary_failures"][0]["field"] == "item_icon_route_check_count"


def test_verify_quality_fails_when_item_browser_skips_icon_review(monkeypatch):
    payloads = {
        "item_browser": {
            **_healthy_payload("item_browser"),
            "item_icon_review_count": 0,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "item_browser"
    assert summary["summary_failures"][0]["field"] == "item_icon_review_count"


def test_verify_quality_fails_when_item_icon_review_browser_has_failures(monkeypatch):
    payloads = {
        "item_browser": {
            **_healthy_payload("item_browser"),
            "item_icon_review_failure_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "item_browser"
    assert summary["summary_failures"][0]["field"] == "item_icon_review_failure_count"


def test_verify_quality_fails_when_core_browser_summary_has_request_failures(monkeypatch):
    payloads = {
        "core_browser": {
            **_healthy_payload("core_browser"),
            "request_failure_count": 1,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "core_browser"
    assert summary["summary_failures"][0]["field"] == "request_failure_count"


def test_verify_quality_fails_when_core_browser_rows_are_missing(monkeypatch):
    payloads = {
        "core_browser": {
            **_healthy_payload("core_browser"),
            "core_tab_with_rows_count": 4,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "core_browser"
    assert summary["summary_failures"][0]["field"] == "core_tab_with_rows_count"


def test_verify_quality_fails_when_core_browser_visible_images_are_missing(monkeypatch):
    payloads = {
        "core_browser": {
            **_healthy_payload("core_browser"),
            "core_tab_with_visible_images_count": 3,
        }
    }

    def fake_run_step(name, command, *, timeout):
        return {
            "name": name,
            "returncode": 0,
            "command": " ".join(command),
            "output": json.dumps(payloads.get(name, _healthy_payload(name)), ensure_ascii=False),
        }

    monkeypatch.setattr(quality, "_run_command_step", fake_run_step)

    summary = quality.verify_quality(_args(skip_frontend=True))

    assert summary["ok"] is False
    assert "quality_summary" in summary["failed_steps"]
    assert summary["summary_failures"][0]["step"] == "core_browser"
    assert summary["summary_failures"][0]["field"] == "core_tab_with_visible_images_count"


def test_run_step_turns_timeout_into_failure(monkeypatch):
    def fail_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=3, output="partial log")

    monkeypatch.setattr(quality.subprocess, "run", fail_timeout)

    row = quality._run_step("browser", ["script.py"], timeout=3)

    assert row["returncode"] == 124
    assert row["timed_out"] is True
    assert "partial log" in row["output"]
    assert "timed out after 3s" in row["output"]


def test_item_icon_annotation_prefers_item_id_over_duplicate_title():
    rows = [{"index": 0, "item_id": "2", "title": "同名道具", "src": "/api/fanxiu/resources/icon?name=icon_b"}]
    expected = [
        {"id": "1", "title": "同名道具", "icon": "icon_a"},
        {"id": "2", "title": "同名道具", "icon": "icon_b"},
    ]

    annotated = browser._annotate_expected_item_icons(rows, expected)

    assert annotated[0]["expected_item_id"] == "2"
    assert annotated[0]["expected_icon"] == "icon_b"
    assert annotated[0]["expected_icon_match"] is True


def test_item_icon_annotation_falls_back_to_position_when_dom_id_missing():
    rows = [{"index": 1, "title": "第二个", "src": "/api/fanxiu/resources/icon?name=icon_2"}]
    expected = [
        {"id": "1", "title": "第一个", "icon": "icon_1"},
        {"id": "2", "title": "第二个", "icon": "icon_2"},
    ]

    annotated = browser._annotate_expected_item_icons(rows, expected)

    assert annotated[0]["expected_item_id"] == "2"
    assert annotated[0]["expected_icon_match"] is True


def test_wiki_icon_scan_paginates_required_catalogs(monkeypatch):
    calls = []

    def fake_fetch_page(_api_base, source, _path, params, _timeout):
        calls.append((source, params["offset"]))
        total = 3 if source == "gongfa_api_full" else 1
        if source == "gongfa_api_full":
            cards_by_offset = {
                0: [{"id": "1", "name": "a", "icon": "icon_a"}],
                1: [{"id": "2", "name": "b", "icon": "icon_b"}],
                2: [{"id": "3", "name": "c", "icon": "icon_c"}],
            }
            return cards_by_offset.get(params["offset"], []), total
        return [{"id": "x", "name": source, "icon": f"{source}_icon"}], total

    monkeypatch.setattr(icons, "_fetch_endpoint_page", fake_fetch_page)

    uses = icons._collect_api_page_icons("http://api", limit=1, timeout=1)
    by_source = {}
    for use in uses:
        by_source.setdefault(use.source, []).append(use.icon)

    assert by_source["gongfa_api_full"] == ["icon_a", "icon_b", "icon_c"]
    assert calls.count(("activity_api_page", 0)) == 1
    assert ("activity_api_page", 1) not in calls


def test_wiki_icon_endpoint_retries_transient_http_failure(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code, content_type, content):
            self.status_code = status_code
            self.headers = {"content-type": content_type}
            self.content = content
            self.text = content.decode("utf-8", errors="replace")

        def json(self):
            return {"detail": self.text}

    responses = [
        FakeResponse(500, "text/plain; charset=utf-8", b"Internal Server Error"),
        FakeResponse(200, "image/png", b"\x89PNG"),
    ]
    monkeypatch.setattr(icons, "_get_with_retry", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr(icons.time, "sleep", lambda _seconds: None)

    check = icons._check_icon_endpoint("http://api", "icon_lazy")

    assert check["ok"] is True
    assert check["status_code"] == 200


def test_wiki_icon_failed_checks_are_retried_after_cooldown(monkeypatch, tmp_path):
    args = argparse.Namespace(
        api_base="http://api",
        frontend_base="http://frontend",
        export_root=str(tmp_path),
        api_limit=10,
        api_timeout=1,
        max_icons=0,
        full_items=False,
        browser=False,
        chrome="chrome",
        browser_wait_ms=1,
    )
    monkeypatch.setattr(icons, "resolve_fanxiu_export_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(icons, "_collect_local_item_icons", lambda _export_root, _limit=None: [])
    monkeypatch.setattr(icons, "_collect_api_page_icons", lambda _api_base, _limit, _timeout: [icons.IconUse("api", "1", "a", "icon", "icon_a")])
    calls = {"icon_a": 0}

    def fake_check(_api_base, icon):
        calls[icon] += 1
        return {
            "icon": icon,
            "ok": calls[icon] >= 2,
            "status_code": 200 if calls[icon] >= 2 else "",
            "content_type": "image/png" if calls[icon] >= 2 else "",
            "size": 4 if calls[icon] >= 2 else 0,
            "detail": "" if calls[icon] >= 2 else "connection refused",
        }

    monkeypatch.setattr(icons, "_check_icon_endpoint", fake_check)
    monkeypatch.setattr(icons.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(icons.argparse.ArgumentParser, "parse_args", lambda _self: args)

    assert icons.main() == 0
    assert calls["icon_a"] == 2


def test_endpoint_required_field_failures_report_missing_values():
    data = {
        "items": [
            {"id": "1", "name": "ok", "icon": "icon_a"},
            {"id": "2", "name": "", "icon": "icon_b"},
            {"id": "3", "icon": "icon_c"},
        ]
    }

    failures = endpoints._required_field_failures(data, ("id", "name", "icon"))

    assert failures == [
        "row 1 (2) missing name",
        "row 2 (3) missing name",
    ]


def test_endpoint_required_field_failures_require_list_rows():
    failures = endpoints._required_field_failures({"total": 3}, ("id", "name"))

    assert failures == ["no list rows available for required fields id,name"]


def test_card_catalog_sample_offsets_cover_edges_and_middle():
    assert card_catalogs._sample_offsets(10, 4) == [0, 3, 6, 9]
    assert card_catalogs._sample_offsets(3, 8) == [0, 1, 2]
    assert card_catalogs._sample_offsets(0, 8) == []


def test_card_catalog_fetch_sample_rows_uses_offset_pages(monkeypatch):
    spec = card_catalogs.CatalogSpec("demo", "/cards", "/card", "id", ("id",))
    args = argparse.Namespace(detail_sample=3, api_timeout=1)
    fetched_offsets = []

    def fake_get_json(_url, *, params=None, timeout=30, attempts=3):
        fetched_offsets.append(params["offset"])
        return {"total": 10, "items": [{"id": str(params["offset"]), "name": f"row-{params['offset']}", "icon": "icon"}]}

    monkeypatch.setattr(card_catalogs, "_get_json", fake_get_json)

    rows = card_catalogs._fetch_sample_rows("http://api", spec, 10, [{"id": "0", "name": "row-0", "icon": "icon"}], args)

    assert [offset for offset, _row in rows] == [0, 4, 9]
    assert fetched_offsets == [4, 9]
