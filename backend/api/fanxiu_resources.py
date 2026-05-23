from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.core.fanxiu_apk_static import (
    build_fanxiu_apk_download_config_report,
    build_fanxiu_apk_runtime_entry_report,
    build_fanxiu_apk_static_index,
    build_fanxiu_resource_manifest_diff_report,
    build_fanxiu_resource_package_report,
)
from backend.core.fanxiu_il2cpp_metadata import (
    build_fanxiu_il2cpp_hot_update_report,
    build_fanxiu_il2cpp_metadata_probe,
)
from backend.core.fanxiu_download_bridge import (
    build_fanxiu_il2cpp_download_inventory,
    build_fanxiu_lua_download_bridge_report,
)
from backend.core.fanxiu_hot_update import (
    build_fanxiu_bluestarsea_catalog_probe,
    build_fanxiu_bluestarsea_model_state_probe,
    build_fanxiu_bluestarsea_open_red_dot_probe,
    build_fanxiu_bluestarsea_purify_energy_probe,
    build_fanxiu_bluestarsea_runtime_probe,
    build_fanxiu_bluestarsea_support_config_probe,
    build_fanxiu_blld_combat_mechanics_probe,
    build_fanxiu_blld_finish_flow_probe,
    build_fanxiu_blld_level_catalog_probe,
    build_fanxiu_blld_reward_catalog_probe,
    build_fanxiu_blld_runtime_probe,
    build_fanxiu_hot_update_feature_probe,
    build_fanxiu_hot_update_lscripts_report,
)
from backend.core.fanxiu_item_catalog import (
    get_fanxiu_item_card,
    search_fanxiu_item_cards,
)
from backend.core.fanxiu_gongfa_catalog import (
    build_fanxiu_gongfa_catalog,
    get_fanxiu_gongfa_card,
    search_fanxiu_gongfa_cards,
)
from backend.core.fanxiu_game_luaconfig import (
    build_fanxiu_gongfa_feature_probe,
    build_fanxiu_lingjie_feature_catalog,
    build_fanxiu_special_gongfa_feature_probe,
    get_fanxiu_lingjie_feature_card,
    search_fanxiu_lingjie_feature_cards,
)
from backend.core.fanxiu_lua_config import build_fanxiu_lua_config_batch_report, build_fanxiu_lua_config_report
from backend.core.fanxiu_lua_logic_index import (
    build_fanxiu_lingjie_gongfa_runtime_report,
    build_fanxiu_lua_logic_index,
)
from backend.core.fanxiu_lua_packet_index import build_fanxiu_lua_packet_index
from backend.core.fanxiu_resources import (
    FanxiuResourceError,
    build_fanxiu_resource_summary,
    export_fanxiu_unity_text_assets,
    export_fanxiu_unity_textures,
    extract_fanxiu_wwise_wems,
    inspect_fanxiu_unity_bundle,
    inspect_fanxiu_wwise_bank,
    list_fanxiu_unity_bundles,
    resolve_fanxiu_sprite_icon_path,
)
from backend.core.fanxiu_wiki import (
    build_fanxiu_wiki_catalog,
    get_fanxiu_wiki_text_entry,
    resolve_fanxiu_wiki_media_path,
    search_fanxiu_wiki_gallery,
    search_fanxiu_wiki_texts,
)
from backend.core.fanxiu_wiki_user_fields import save_fanxiu_wiki_user_fields
from backend.core.feature_access_guard import require_feature_access_dependency


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("fanxiu"))],
)


class FanxiuResourcePathRequest(BaseModel):
    path: str = Field(min_length=1, description="资源根目录下的相对路径，或位于资源根目录内的绝对路径")
    resource_root: str | None = None


class FanxiuUnityInspectRequest(FanxiuResourcePathRequest):
    max_objects: int = Field(default=100, ge=0, le=500)


class FanxiuUnityTextureExportRequest(FanxiuResourcePathRequest):
    export_root: str | None = None
    max_textures: int | None = Field(default=None, ge=1, le=1000)


class FanxiuUnityTextAssetExportRequest(FanxiuResourcePathRequest):
    export_root: str | None = None
    max_assets: int | None = Field(default=None, ge=1, le=5000)


class FanxiuWwiseExtractRequest(FanxiuResourcePathRequest):
    export_root: str | None = None
    max_entries: int | None = Field(default=None, ge=1, le=5000)


class FanxiuApkStaticIndexRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None
    keyword_hit_limit: int = Field(default=30000, ge=100, le=100000)


class FanxiuApkRuntimeEntryReportRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None
    max_rows: int = Field(default=500, ge=10, le=5000)


class FanxiuApkDownloadConfigReportRequest(BaseModel):
    apk_root: str | None = None
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuLuaDownloadBridgeReportRequest(BaseModel):
    export_root: str | None = None


class FanxiuIl2CppDownloadInventoryRequest(BaseModel):
    export_root: str | None = None


class FanxiuResourcePackageReportRequest(BaseModel):
    apk_root: str | None = None
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuResourceManifestDiffReportRequest(BaseModel):
    apk_root: str | None = None
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuHotUpdateLscriptsReportRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None
    max_bundles: int | None = Field(default=None, ge=1, le=1000)


class FanxiuHotUpdateFeatureProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaCatalogProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaRuntimeProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaModelStateProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaSupportConfigProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaOpenRedDotProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaPurifyEnergyProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldRuntimeProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldFinishFlowProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldRewardCatalogProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldCombatMechanicsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldLevelCatalogProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuIl2CppMetadataProbeRequest(BaseModel):
    metadata_path: str | None = None
    apk_root: str | None = None
    export_root: str | None = None
    keyword_hit_limit: int = Field(default=30000, ge=100, le=100000)


class FanxiuIl2CppHotUpdateReportRequest(BaseModel):
    metadata_path: str | None = None
    apk_root: str | None = None
    export_root: str | None = None
    type_limit: int = Field(default=500, ge=10, le=5000)
    string_limit: int = Field(default=500, ge=10, le=5000)


class FanxiuLuaConfigReportRequest(BaseModel):
    config_path: str = Field(min_length=1)
    lang_path: str | None = None
    export_root: str | None = None
    max_preview_rows: int = Field(default=5000, ge=1, le=50000)


class FanxiuLuaConfigBatchReportRequest(BaseModel):
    config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None
    max_preview_rows: int = Field(default=5000, ge=1, le=50000)


class FanxiuGongfaCatalogRequest(BaseModel):
    gongfa_rows_path: str | None = None
    skill_rows_path: str | None = None
    export_root: str | None = None


class FanxiuGongfaFeatureProbeRequest(BaseModel):
    lingjie_rows_path: str | None = None
    config_dir: str | None = None
    item_rows_path: str | None = None
    export_root: str | None = None


class FanxiuLingjieFeatureCatalogRequest(BaseModel):
    feature_base_rows_path: str | None = None
    main_feature_rows_path: str | None = None
    main_feature_pin_rows_path: str | None = None
    side_feature_jie_rows_path: str | None = None
    side_feature_pin_rows_path: str | None = None
    lingjie_gongfa_jie_rows_path: str | None = None
    lingjie_gongfa_star_rows_path: str | None = None
    gongfa_rows_path: str | None = None
    item_rows_path: str | None = None
    export_root: str | None = None


class FanxiuSpecialGongfaFeatureProbeRequest(BaseModel):
    special_rows_path: str | None = None
    gongfa_rows_path: str | None = None
    skill_rows_path: str | None = None
    star_rows_path: str | None = None
    upgrade_rows_path: str | None = None
    faze_effect_rows_path: str | None = None
    faze_resource_rows_path: str | None = None
    item_rows_path: str | None = None
    config_dir: str | None = None
    export_root: str | None = None


class FanxiuLuaLogicIndexRequest(BaseModel):
    source_dir: str | None = None
    export_root: str | None = None


class FanxiuLuaPacketIndexRequest(BaseModel):
    source_dir: str | None = None
    export_root: str | None = None


class FanxiuLingjieGongfaRuntimeReportRequest(BaseModel):
    source_dir: str | None = None
    packet_index_dir: str | None = None
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuWikiUserFieldsRequest(BaseModel):
    note: str = Field(default="", max_length=20000)
    source: str = Field(default="", max_length=20000)


def _run_resource_operation(func, *args, **kwargs) -> dict[str, Any]:
    try:
        return func(*args, **kwargs)
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/resources/summary")
def get_fanxiu_resource_summary(resource_root: str | None = Query(default=None)) -> dict[str, Any]:
    return _run_resource_operation(build_fanxiu_resource_summary, resource_root=resource_root)


@router.get("/resources/wiki/catalog")
def get_fanxiu_resource_wiki_catalog(export_root: str | None = Query(default=None)) -> dict[str, Any]:
    return _run_resource_operation(build_fanxiu_wiki_catalog, export_root=export_root)


@router.get("/resources/wiki/texts")
def get_fanxiu_resource_wiki_texts(
    query: str = Query(default=""),
    asset: str = Query(default="all"),
    category: str = Query(default="all"),
    display_kind: str = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_wiki_texts,
        query=query,
        asset=asset,
        category=category,
        display_kind=display_kind,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/wiki/text")
def get_fanxiu_resource_wiki_text(
    asset: str = Query(min_length=1),
    key: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_wiki_text_entry,
        asset=asset,
        key=key,
        export_root=export_root,
    )


@router.get("/resources/wiki/gallery")
def get_fanxiu_resource_wiki_gallery(
    query: str = Query(default=""),
    kind: str = Query(default="all"),
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_wiki_gallery,
        query=query,
        kind=kind,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/wiki/media")
def get_fanxiu_resource_wiki_media(
    path: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> FileResponse:
    media_path = _run_resource_operation(resolve_fanxiu_wiki_media_path, path, export_root=export_root)
    return FileResponse(media_path)


@router.get("/resources/icon")
def get_fanxiu_resource_icon(
    name: str = Query(min_length=1),
    resource_root: str | None = Query(default=None),
    export_root: str | None = Query(default=None),
) -> FileResponse:
    try:
        icon_path = resolve_fanxiu_sprite_icon_path(name, resource_root=resource_root, export_root=export_root)
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(icon_path)


@router.get("/resources/gongfa/cards")
def get_fanxiu_gongfa_cards(
    query: str = Query(default=""),
    quality_name: str = Query(default=""),
    quality_grade_name: str = Query(default=""),
    quality_family_name: str = Query(default=""),
    skill_type_name: str = Query(default=""),
    sort_by: str = Query(default="default"),
    sort_order: str = Query(default="asc"),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_gongfa_cards,
        query=query,
        quality_name=quality_name,
        quality_grade_name=quality_grade_name,
        quality_family_name=quality_family_name,
        skill_type_name=skill_type_name,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/gongfa/card")
def get_fanxiu_gongfa_card_detail(
    gongfa_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_gongfa_card,
        gongfa_id,
        export_root=export_root,
    )


@router.put("/resources/gongfa/user-fields")
def put_fanxiu_gongfa_user_fields(
    req: FanxiuWikiUserFieldsRequest,
    gongfa_id: str = Query(min_length=1),
) -> dict[str, Any]:
    return _run_resource_operation(
        save_fanxiu_wiki_user_fields,
        "gongfa",
        gongfa_id,
        note=req.note,
        source=req.source,
    )


@router.put("/resources/wiki/user-fields")
def put_fanxiu_wiki_user_fields(
    req: FanxiuWikiUserFieldsRequest,
    object_type: str = Query(min_length=1),
    object_id: str = Query(min_length=1),
) -> dict[str, Any]:
    return _run_resource_operation(
        save_fanxiu_wiki_user_fields,
        object_type,
        object_id,
        note=req.note,
        source=req.source,
    )


@router.get("/resources/gongfa/lingjie-feature-cards")
def get_fanxiu_lingjie_feature_cards(
    query: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_lingjie_feature_cards,
        query=query,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/gongfa/lingjie-feature-card")
def get_fanxiu_lingjie_feature_card_detail(
    gongfa_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_lingjie_feature_card,
        gongfa_id,
        export_root=export_root,
    )


@router.get("/resources/items/cards")
def get_fanxiu_item_cards(
    query: str = Query(default=""),
    quality_name: str = Query(default=""),
    type_key: str = Query(default=""),
    sub_type_key: str = Query(default=""),
    sort_by: str = Query(default="default"),
    sort_order: str = Query(default="asc"),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_item_cards,
        query=query,
        quality_name=quality_name,
        type_key=type_key,
        sub_type_key=sub_type_key,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/items/card")
def get_fanxiu_item_card_detail(
    item_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_item_card,
        item_id,
        export_root=export_root,
    )


@router.get("/resources/unity-bundles")
def get_fanxiu_unity_bundles(
    resource_root: str | None = Query(default=None),
    subdir: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    scan_limit: int = Query(default=5000, ge=1, le=50000),
    inspect_objects: bool = Query(default=False),
    max_objects: int = Query(default=30, ge=0, le=200),
) -> dict[str, Any]:
    return _run_resource_operation(
        list_fanxiu_unity_bundles,
        resource_root=resource_root,
        subdir=subdir,
        limit=limit,
        scan_limit=scan_limit,
        inspect_objects=inspect_objects,
        max_objects=max_objects,
    )


@router.post("/resources/unity/inspect")
def post_fanxiu_unity_inspect(req: FanxiuUnityInspectRequest) -> dict[str, Any]:
    return _run_resource_operation(
        inspect_fanxiu_unity_bundle,
        req.path,
        resource_root=req.resource_root,
        max_objects=req.max_objects,
    )


@router.post("/resources/unity/export-textures")
def post_fanxiu_unity_export_textures(req: FanxiuUnityTextureExportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        export_fanxiu_unity_textures,
        req.path,
        resource_root=req.resource_root,
        export_root=req.export_root,
        max_textures=req.max_textures,
    )


@router.post("/resources/unity/export-text-assets")
def post_fanxiu_unity_export_text_assets(req: FanxiuUnityTextAssetExportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        export_fanxiu_unity_text_assets,
        req.path,
        resource_root=req.resource_root,
        export_root=req.export_root,
        max_assets=req.max_assets,
    )


@router.post("/resources/wwise/inspect")
def post_fanxiu_wwise_inspect(req: FanxiuResourcePathRequest) -> dict[str, Any]:
    return _run_resource_operation(
        inspect_fanxiu_wwise_bank,
        req.path,
        resource_root=req.resource_root,
    )


@router.post("/resources/wwise/extract-wems")
def post_fanxiu_wwise_extract_wems(req: FanxiuWwiseExtractRequest) -> dict[str, Any]:
    return _run_resource_operation(
        extract_fanxiu_wwise_wems,
        req.path,
        resource_root=req.resource_root,
        export_root=req.export_root,
        max_entries=req.max_entries,
    )


@router.post("/resources/apk/static-index")
def post_fanxiu_apk_static_index(req: FanxiuApkStaticIndexRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_static_index,
        apk_root=req.apk_root,
        export_root=req.export_root,
        keyword_hit_limit=req.keyword_hit_limit,
    )


@router.post("/resources/apk/runtime-entry-report")
def post_fanxiu_apk_runtime_entry_report(req: FanxiuApkRuntimeEntryReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_runtime_entry_report,
        apk_root=req.apk_root,
        export_root=req.export_root,
        max_rows=req.max_rows,
    )


@router.post("/resources/apk/download-config-report")
def post_fanxiu_apk_download_config_report(req: FanxiuApkDownloadConfigReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_download_config_report,
        apk_root=req.apk_root,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/lua/download-bridge-report")
def post_fanxiu_lua_download_bridge_report(req: FanxiuLuaDownloadBridgeReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_download_bridge_report,
        export_root=req.export_root,
    )


@router.post("/resources/apk/il2cpp-download-inventory")
def post_fanxiu_il2cpp_download_inventory(req: FanxiuIl2CppDownloadInventoryRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_il2cpp_download_inventory,
        export_root=req.export_root,
    )


@router.post("/resources/apk/resource-package-report")
def post_fanxiu_resource_package_report(req: FanxiuResourcePackageReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_resource_package_report,
        apk_root=req.apk_root,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/resource-manifest-diff-report")
def post_fanxiu_resource_manifest_diff_report(req: FanxiuResourceManifestDiffReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_resource_manifest_diff_report,
        apk_root=req.apk_root,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/lscripts-report")
def post_fanxiu_hot_update_lscripts_report(req: FanxiuHotUpdateLscriptsReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_hot_update_lscripts_report,
        resource_root=req.resource_root,
        export_root=req.export_root,
        max_bundles=req.max_bundles,
    )


@router.post("/resources/hot-update/feature-probe")
def post_fanxiu_hot_update_feature_probe(req: FanxiuHotUpdateFeatureProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_hot_update_feature_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-catalog-probe")
def post_fanxiu_hot_update_bluestarsea_catalog_probe(req: FanxiuHotUpdateBlueStarSeaCatalogProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_catalog_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-runtime-probe")
def post_fanxiu_hot_update_bluestarsea_runtime_probe(req: FanxiuHotUpdateBlueStarSeaRuntimeProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_runtime_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-model-state-probe")
def post_fanxiu_hot_update_bluestarsea_model_state_probe(req: FanxiuHotUpdateBlueStarSeaModelStateProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_model_state_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-support-config-probe")
def post_fanxiu_hot_update_bluestarsea_support_config_probe(
    req: FanxiuHotUpdateBlueStarSeaSupportConfigProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_support_config_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-open-red-dot-probe")
def post_fanxiu_hot_update_bluestarsea_open_red_dot_probe(
    req: FanxiuHotUpdateBlueStarSeaOpenRedDotProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_open_red_dot_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-purify-energy-probe")
def post_fanxiu_hot_update_bluestarsea_purify_energy_probe(
    req: FanxiuHotUpdateBlueStarSeaPurifyEnergyProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_purify_energy_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-runtime-probe")
def post_fanxiu_hot_update_blld_runtime_probe(req: FanxiuHotUpdateBlldRuntimeProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_runtime_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-finish-flow-probe")
def post_fanxiu_hot_update_blld_finish_flow_probe(req: FanxiuHotUpdateBlldFinishFlowProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_finish_flow_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-reward-catalog-probe")
def post_fanxiu_hot_update_blld_reward_catalog_probe(req: FanxiuHotUpdateBlldRewardCatalogProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_reward_catalog_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-combat-mechanics-probe")
def post_fanxiu_hot_update_blld_combat_mechanics_probe(
    req: FanxiuHotUpdateBlldCombatMechanicsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_combat_mechanics_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-level-catalog-probe")
def post_fanxiu_hot_update_blld_level_catalog_probe(req: FanxiuHotUpdateBlldLevelCatalogProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_level_catalog_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/il2cpp-metadata-probe")
def post_fanxiu_il2cpp_metadata_probe(req: FanxiuIl2CppMetadataProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_il2cpp_metadata_probe,
        metadata_path=req.metadata_path,
        apk_root=req.apk_root,
        export_root=req.export_root,
        keyword_hit_limit=req.keyword_hit_limit,
    )


@router.post("/resources/apk/il2cpp-hot-update-report")
def post_fanxiu_il2cpp_hot_update_report(req: FanxiuIl2CppHotUpdateReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_il2cpp_hot_update_report,
        metadata_path=req.metadata_path,
        apk_root=req.apk_root,
        export_root=req.export_root,
        type_limit=req.type_limit,
        string_limit=req.string_limit,
    )


@router.post("/resources/lua-config/report")
def post_fanxiu_lua_config_report(req: FanxiuLuaConfigReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_config_report,
        req.config_path,
        lang_path=req.lang_path,
        export_root=req.export_root,
        max_preview_rows=req.max_preview_rows,
    )


@router.post("/resources/lua-config/batch-report")
def post_fanxiu_lua_config_batch_report(req: FanxiuLuaConfigBatchReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_config_batch_report,
        config_dir=req.config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
        max_preview_rows=req.max_preview_rows,
    )


@router.post("/resources/gongfa/catalog")
def post_fanxiu_gongfa_catalog(req: FanxiuGongfaCatalogRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_catalog,
        gongfa_rows_path=req.gongfa_rows_path,
        skill_rows_path=req.skill_rows_path,
        export_root=req.export_root,
    )


@router.post("/resources/gongfa/feature-probe")
def post_fanxiu_gongfa_feature_probe(req: FanxiuGongfaFeatureProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_feature_probe,
        lingjie_rows_path=req.lingjie_rows_path,
        config_dir=req.config_dir,
        item_rows_path=req.item_rows_path,
        export_root=req.export_root,
    )


@router.post("/resources/gongfa/lingjie-feature-catalog")
def post_fanxiu_lingjie_feature_catalog(req: FanxiuLingjieFeatureCatalogRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lingjie_feature_catalog,
        feature_base_rows_path=req.feature_base_rows_path,
        main_feature_rows_path=req.main_feature_rows_path,
        main_feature_pin_rows_path=req.main_feature_pin_rows_path,
        side_feature_jie_rows_path=req.side_feature_jie_rows_path,
        side_feature_pin_rows_path=req.side_feature_pin_rows_path,
        lingjie_gongfa_jie_rows_path=req.lingjie_gongfa_jie_rows_path,
        lingjie_gongfa_star_rows_path=req.lingjie_gongfa_star_rows_path,
        gongfa_rows_path=req.gongfa_rows_path,
        item_rows_path=req.item_rows_path,
        export_root=req.export_root,
    )


@router.post("/resources/gongfa/special-feature-probe")
def post_fanxiu_special_gongfa_feature_probe(req: FanxiuSpecialGongfaFeatureProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_special_gongfa_feature_probe,
        special_rows_path=req.special_rows_path,
        gongfa_rows_path=req.gongfa_rows_path,
        skill_rows_path=req.skill_rows_path,
        star_rows_path=req.star_rows_path,
        upgrade_rows_path=req.upgrade_rows_path,
        faze_effect_rows_path=req.faze_effect_rows_path,
        faze_resource_rows_path=req.faze_resource_rows_path,
        item_rows_path=req.item_rows_path,
        config_dir=req.config_dir,
        export_root=req.export_root,
    )


@router.post("/resources/lua-logic-index")
def post_fanxiu_lua_logic_index(req: FanxiuLuaLogicIndexRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_logic_index,
        source_dir=req.source_dir,
        export_root=req.export_root,
    )


@router.post("/resources/lua-packet-index")
def post_fanxiu_lua_packet_index(req: FanxiuLuaPacketIndexRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_packet_index,
        source_dir=req.source_dir,
        export_root=req.export_root,
    )


@router.post("/resources/gongfa/lingjie-runtime-report")
def post_fanxiu_lingjie_gongfa_runtime_report(req: FanxiuLingjieGongfaRuntimeReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lingjie_gongfa_runtime_report,
        source_dir=req.source_dir,
        packet_index_dir=req.packet_index_dir,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )
