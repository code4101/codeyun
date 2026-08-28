from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FANXIU_ROOT = REPO_ROOT / "backend" / "core" / "fanxiu"
SCANNED_ROOTS = (
    FANXIU_ROOT / "activity",
    FANXIU_ROOT / "data_annotation" / "tasks",
    FANXIU_ROOT / "instrumentation",
    FANXIU_ROOT / "runtime_gui",
)
FANXIU_API = REPO_ROOT / "backend" / "api" / "fanxiu.py"
FRONTEND_FANXIU_API = REPO_ROOT / "frontend" / "src" / "api" / "fanxiu.ts"
LEGACY_YUNMENG_PAGE_ROOT = (
    REPO_ROOT / "frontend" / "src" / "standard" / "fanxiu" / "yunmeng-trial"
)

ACTIVITY_FAMILIES = (
    "beast_abyss",
    "magic_invasion",
    "tiandi_yiju",
    "xianyuan",
    "xutian",
    "yunmeng",
)

# Migration ratchet: existing cross-activity imports may be removed without
# changing this test, while any newly introduced edge or symbol fails.  Each
# entry should disappear after its shared capability moves to an
# activity-neutral module.
KNOWN_CROSS_ACTIVITY_IMPORT_DEBT: set[tuple[str, str, str]] = set()
KNOWN_LEGACY_YUNMENG_BACKEND_ROUTES = {
    "/activity-list/yunmeng-trial",
    "/activity-list/yunmeng-trial/{activity_id}/measurements",
    "/activity-list/yunmeng-trial/{activity_id}/measurements/collect",
    "/activity-list/yunmeng-trial/{activity_id}/priorities",
    "/activity-list/yunmeng-trial/{activity_id}/rankings",
    "/activity-list/yunmeng-trial/{activity_id}/shop-items/{goods_id}/lock",
}
KNOWN_LEGACY_YUNMENG_FRONTEND_ROUTES: set[str] = set()


def _activity_family(value: str) -> str | None:
    leaf = value.rsplit(".", 1)[-1]
    return next(
        (family for family in ACTIVITY_FAMILIES if leaf.startswith(family)),
        None,
    )


def _cross_activity_imports() -> set[tuple[str, str, str]]:
    imports: set[tuple[str, str, str]] = set()
    for root in SCANNED_ROOTS:
        for source_path in root.rglob("*.py"):
            source_family = _activity_family(source_path.stem)
            if source_family is None:
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            relative_source = source_path.relative_to(REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                target_family = _activity_family(node.module)
                if target_family is None or target_family == source_family:
                    continue
                imports.update(
                    (relative_source, node.module, alias.name)
                    for alias in node.names
                )
    return imports


def test_no_new_cross_activity_import_debt() -> None:
    current_debt = _cross_activity_imports()
    unexpected = current_debt - KNOWN_CROSS_ACTIVITY_IMPORT_DEBT

    assert not unexpected, (
        "活动专用模块之间出现了新的直接依赖；请把共享能力下沉到活动无关模块。"
        f"\nunexpected={sorted(unexpected)!r}"
    )


def test_gameplay_collectors_share_occurrence_ranking_merge() -> None:
    collector_paths = (
        FANXIU_ROOT / "activity" / "yunmeng_exchange.py",
        FANXIU_ROOT / "activity" / "magic_invasion.py",
        FANXIU_ROOT / "activity" / "beast_abyss.py",
    )
    for path in collector_paths:
        source = path.read_text(encoding="utf-8")
        assert "def _stored_ranking_rows(" not in source
        assert "load_stored_exchange_rankings(" not in source
        assert "merge_occurrence_rankings(" in source


def test_tiandi_yiju_occurrence_shop_has_one_registered_authority() -> None:
    from backend.core.fanxiu.activity.exchange_activity_registry import (
        resolve_registered_occurrence_shop,
    )

    occurrences = (
        (8090001, 1, 90000, 11),
        (8090004, 8, 90002, 13),
    )

    for activity_id, cross_count, base_id, currency_type in occurrences:
        shop = resolve_registered_occurrence_shop(
            activity_type="tiandi-yiju",
            activity_id=activity_id,
            cross_count=cross_count,
        )
        assert shop is not None
        assert (shop.base_id, shop.currency_type) == (base_id, currency_type)

    with pytest.raises(ValueError, match="活动与跨数不一致"):
        resolve_registered_occurrence_shop(
            activity_type="tiandi-yiju",
            activity_id=8090001,
            cross_count=8,
        )


def test_legacy_yunmeng_api_surface_cannot_expand() -> None:
    backend_tree = ast.parse(FANXIU_API.read_text(encoding="utf-8"))
    backend_routes = {
        node.value
        for node in ast.walk(backend_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/activity-list/yunmeng-trial")
    }
    frontend_source = FRONTEND_FANXIU_API.read_text(encoding="utf-8")
    frontend_routes = set(
        re.findall(
            r"""["'`](/fanxiu/activity-list/yunmeng-trial[^"'`]*)["'`]""",
            frontend_source,
        )
    )

    assert backend_routes <= KNOWN_LEGACY_YUNMENG_BACKEND_ROUTES
    assert frontend_routes <= KNOWN_LEGACY_YUNMENG_FRONTEND_ROUTES
    assert not (LEGACY_YUNMENG_PAGE_ROOT / "index.ts").exists()
    assert not (LEGACY_YUNMENG_PAGE_ROOT / "page.vue").exists()


def test_legacy_yunmeng_rows_have_explicit_unified_storage_targets() -> None:
    from backend.models import (
        FanxiuExchangeActivity,
        FanxiuExchangeActivityObservation,
        FanxiuExchangeRanking,
        FanxiuExchangeShopItem,
        FanxiuYunmengTrialActivity,
        FanxiuYunmengTrialMeasurement,
        FanxiuYunmengTrialRanking,
        FanxiuYunmengTrialShopItem,
    )

    metadata_fields = {"id", "activity_id", "created_at", "updated_at"}
    direct_pairs = (
        (FanxiuYunmengTrialActivity, FanxiuExchangeActivity),
        (FanxiuYunmengTrialShopItem, FanxiuExchangeShopItem),
        (FanxiuYunmengTrialRanking, FanxiuExchangeRanking),
    )
    for legacy_model, unified_model in direct_pairs:
        legacy_fields = set(legacy_model.model_fields) - metadata_fields
        assert legacy_fields <= set(unified_model.model_fields)

    measurement_fields = (
        set(FanxiuYunmengTrialMeasurement.model_fields) - metadata_fields
    )
    direct_observation_fields = {"captured_at"}
    payload_fields = {
        "score",
        "exchange_currency",
        "rank",
        "challenge_count_delta",
        "note",
        "source_kind",
        "evidence",
    }

    assert measurement_fields == direct_observation_fields | payload_fields
    assert direct_observation_fields <= set(
        FanxiuExchangeActivityObservation.model_fields
    )
    assert "payload" in FanxiuExchangeActivityObservation.model_fields
