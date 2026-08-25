from __future__ import annotations

from functools import lru_cache
import hashlib
from html import escape
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from backend.core.fanxiu.catalog.apk_static import (
    build_fanxiu_apk_dex_login_body_probe,
    build_fanxiu_apk_dex_login_payload_shape_probe,
    build_fanxiu_apk_dex_login_surface_probe,
    build_fanxiu_apk_download_config_report,
    build_fanxiu_apk_gamelogin_bridge_probe,
    build_fanxiu_apk_il2cpp_binary_boundary_probe,
    build_fanxiu_apk_jadx_login_source_probe,
    build_fanxiu_apk_jadx_sq_plugin_core_probe,
    build_fanxiu_apk_jadx_sy37_endpoint_usage_probe,
    build_fanxiu_apk_jadx_sy37_login_account_probe,
    build_fanxiu_apk_jadx_sy37_login_response_surface_probe,
    build_fanxiu_apk_jadx_sy37_request_signing_probe,
    build_fanxiu_apk_jadx_sy37_url_catalog_probe,
    build_fanxiu_apk_jadx_sy37_url_update_probe,
    build_fanxiu_apk_jadx_sy37_wrapper_endpoint_probe,
    build_fanxiu_apk_login_server_flow_probe,
    build_fanxiu_apk_manifest_probe,
    build_fanxiu_apk_network_stack_probe,
    build_fanxiu_apk_phonehelper_login_context_probe,
    build_fanxiu_apk_runtime_entry_report,
    build_fanxiu_apk_static_index,
    build_fanxiu_apk_unity_login_receiver_probe,
    build_fanxiu_cpp2il_fileutil_post_loader_probe,
    build_fanxiu_cpp2il_gamelogin_serverlist_bridge_probe,
    build_fanxiu_cpp2il_login_lua_bridge_probe,
    build_fanxiu_cpp2il_socket_proto_bridge_probe,
    build_fanxiu_cpp2il_socket_receive_dispatch_probe,
    build_fanxiu_login_account_sign_source_probe,
    build_fanxiu_login_token_to_socket_handoff_probe,
    build_fanxiu_lua_serverlist_response_flow_probe,
    build_fanxiu_resource_manifest_diff_report,
    build_fanxiu_resource_package_report,
    build_fanxiu_taptap_download_dat_package_probe,
)
from backend.core.fanxiu.catalog.audio import (
    build_fanxiu_wwise_audio_catalog,
    build_fanxiu_wwise_mp3_export,
    load_fanxiu_wwise_mp3_manifest,
    resolve_fanxiu_audio_media_path,
)
from backend.core.fanxiu.catalog.il2cpp_metadata import (
    build_fanxiu_il2cpp_gameplay_symbol_report,
    build_fanxiu_il2cpp_hot_update_report,
    build_fanxiu_il2cpp_metadata_probe,
)
from backend.core.fanxiu.runtime.download_bridge import (
    build_fanxiu_il2cpp_download_inventory,
    build_fanxiu_lua_download_bridge_report,
)
from backend.core.settings import get_settings
from backend.core.fanxiu.catalog.hot_update import (
    build_fanxiu_bluestarsea_authority_boundary_probe,
    build_fanxiu_bluestarsea_catalog_probe,
    build_fanxiu_bluestarsea_faze_effect_probe,
    build_fanxiu_bluestarsea_model_state_probe,
    build_fanxiu_bluestarsea_open_red_dot_probe,
    build_fanxiu_bluestarsea_plan_reward_probe,
    build_fanxiu_bluestarsea_protocol_semantics_probe,
    build_fanxiu_bluestarsea_progression_probe,
    build_fanxiu_bluestarsea_purify_energy_probe,
    build_fanxiu_bluestarsea_runtime_probe,
    build_fanxiu_bluestarsea_star_evolution_probe,
    build_fanxiu_bluestarsea_support_config_probe,
    build_fanxiu_bluestarsea_tree_faze_usage_probe,
    build_fanxiu_blld_authority_boundary_probe,
    build_fanxiu_blld_combat_mechanics_probe,
    build_fanxiu_blld_finish_flow_probe,
    build_fanxiu_blld_level_catalog_probe,
    build_fanxiu_blld_protocol_semantics_probe,
    build_fanxiu_blld_reward_catalog_probe,
    build_fanxiu_blld_runtime_probe,
    build_fanxiu_faze_authority_boundary_probe,
    build_fanxiu_faze_effect_catalog_probe,
    build_fanxiu_faze_effect_lua_usage_probe,
    build_fanxiu_faze_effect_update_event_probe,
    build_fanxiu_faze_protocol_semantics_probe,
    build_fanxiu_faze_source_semantics_probe,
    build_fanxiu_gongfa_special_faze_catalog_probe,
    build_fanxiu_gongfa_special_faze_attr_key_index_probe,
    build_fanxiu_gongfa_special_faze_effect_type_index_probe,
    build_fanxiu_gongfa_special_faze_focus_probe,
    build_fanxiu_gongfa_special_faze_param_item_ref_probe,
    build_fanxiu_gongfa_special_faze_param_shape_index_probe,
    build_fanxiu_gongfa_special_faze_payload_summary_probe,
    build_fanxiu_gongfa_special_faze_reason_probe,
    build_fanxiu_gongfa_special_faze_reason_reuse_index_probe,
    build_fanxiu_gongfa_special_faze_reason_reuse_probe,
    build_fanxiu_gongfa_homemake_detail_renderer_probe,
    build_fanxiu_gongfa_homemake_detail_renderer_sample_probe,
    build_fanxiu_gongfa_homemake_detail_view_probe,
    build_fanxiu_gongfa_homemake_buff_field_semantics_probe,
    build_fanxiu_gongfa_homemake_buff_combat_result_probe,
    build_fanxiu_gongfa_homemake_buff_result_correlation_probe,
    build_fanxiu_gongfa_homemake_cpp2il_buff_result_symbol_probe,
    build_fanxiu_gongfa_homemake_buff_parameter_semantics_probe,
    build_fanxiu_gongfa_homemake_mechanism_ownership_probe,
    build_fanxiu_gongfa_homemake_mechanism_formula_surface_probe,
    build_fanxiu_gongfa_homemake_mechanism_result_producer_probe,
    build_fanxiu_gongfa_homemake_nonfunnel_buff_boundary_probe,
    build_fanxiu_buff_change_result_decoder_probe,
    build_fanxiu_buff_state_decoder_probe,
    build_fanxiu_fight_result_family_decoder_probe,
    build_fanxiu_socket_primitive_decoder_probe,
    build_fanxiu_typed_pool_runtime_observation_probe,
    build_fanxiu_socket_raw_decoder_probe,
    build_fanxiu_socket_compressed_int_codec_probe,
    build_fanxiu_combat_formula_authority_contrast_probe,
    build_fanxiu_cpp2il_main_combat_formula_surface_probe,
    get_fanxiu_gongfa_homemake_buff_parameter_semantics,
    build_fanxiu_gongfa_homemake_learn_teach_probe,
    build_fanxiu_gongfa_homemake_lifecycle_probe,
    build_fanxiu_gongfa_homemake_mutation_ops_probe,
    build_fanxiu_gongfa_homemake_page_list_probe,
    build_fanxiu_gongfa_homemake_record_grid_light_probe,
    build_fanxiu_gongfa_homemake_renderer_source_selection_probe,
    build_fanxiu_gongfa_homemake_share_probe,
    build_fanxiu_gongfa_homemake_share_href_probe,
    build_fanxiu_gongfa_homemake_share_href_prefab_probe,
    build_fanxiu_gongfa_homemake_share_href_registration_gap_probe,
    build_fanxiu_gongfa_homemake_share_ui_probe,
    build_fanxiu_gongfa_homemake_fazelevel_name_match_boundary_probe,
    build_fanxiu_gongfa_homemake_fazelevel_skill_ownership_probe,
    build_fanxiu_gongfa_homemake_side_feature_semantics_probe,
    build_fanxiu_gongfa_homemake_stage_star_timeline_boundary_probe,
    build_fanxiu_gongfa_homemake_stage_star_timeline_config_probe,
    build_fanxiu_gongfa_homemake_timeline_hurt_projection_probe,
    build_fanxiu_gongfa_homemake_skillcastbridge_boundary_probe,
    build_fanxiu_gongfa_homemake_static_renderer_coverage_probe,
    build_fanxiu_gongfa_homemake_xianshu_battle_state_usage_probe,
    build_fanxiu_gongfa_homemake_xianshu_cast_ack_consumer_probe,
    build_fanxiu_gongfa_homemake_xianshu_cast_request_boundary_probe,
    build_fanxiu_gongfa_homemake_xianshu_formula_catalog_probe,
    build_fanxiu_gongfa_homemake_xianshu_formula_usage_probe,
    build_fanxiu_gongfa_homemake_xianshu_static_gap_probe,
    get_fanxiu_gongfa_homemake_xianshu_formula_catalog,
    build_fanxiu_gongfa_program_equip_probe,
    build_fanxiu_gongfa_protocol_semantics_probe,
    build_fanxiu_gongfa_upgrade_times_flow_probe,
    build_fanxiu_gongfa_view_snapshot_probe,
    build_fanxiu_hot_update_feature_probe,
    build_fanxiu_hot_update_lscripts_report,
    query_fanxiu_gongfa_special_faze_catalog,
    render_fanxiu_gongfa_homemake_static_detail,
)
from backend.core.fanxiu.catalog.item import (
    get_fanxiu_item_card,
    load_fanxiu_item_runtime_index,
    search_fanxiu_item_cards,
)
from backend.core.fanxiu.catalog.item_icon_quality import load_item_icon_quality_review
from backend.core.fanxiu.catalog.xianqiao import build_fanxiu_xianqiao_mechanics
from backend.core.fanxiu.catalog.doupotd import (
    build_fanxiu_doupotd_buff_effect_probe,
    build_fanxiu_doupotd_buff_class_semantics_probe,
    build_fanxiu_doupotd_buff_class_flow_probe,
    build_fanxiu_doupotd_buff_authority_boundary_probe,
    build_fanxiu_doupotd_effect_gameplayer_summary_probe,
    build_fanxiu_doupotd_gameplayer_result_probe,
    build_fanxiu_doupotd_monster_drop_resolution_probe,
    build_fanxiu_doupotd_pvp_report_global_lua_surface_probe,
    build_fanxiu_doupotd_pvp_report_gap_probe,
    build_fanxiu_doupotd_pvp_report_native_lua_bridge_boundary_probe,
    build_fanxiu_doupotd_pvp_report_native_symbol_gap_probe,
    build_fanxiu_doupotd_pvp_report_netlogic_family_probe,
    build_fanxiu_doupotd_pvp_report_lua_binding_boundary_probe,
    build_fanxiu_doupotd_pvp_report_trigger_lifecycle_probe,
    build_fanxiu_doupotd_pvp_report_trigger_base_dynamic_gap_probe,
    build_fanxiu_doupotd_pvp_report_trigger_delta_probe,
    build_fanxiu_doupotd_pvp_report_raw_export_coverage_probe,
    build_fanxiu_doupotd_pvp_report_scene_payload_probe,
    build_fanxiu_doupotd_pvp_report_sender_alias_gap_probe,
    build_fanxiu_doupotd_pvp_report_shape_alias_probe,
    build_fanxiu_doupotd_reward_config_probe,
    build_fanxiu_doupotd_reward_result_resolution_probe,
    build_fanxiu_doupotd_catalog,
    build_fanxiu_doupotd_skill_timeline_probe,
    build_fanxiu_doupotd_store_bag_visual_probe,
    get_fanxiu_doupotd_reward_config,
    get_fanxiu_doupotd_partner_card,
    search_fanxiu_doupotd_reward_configs,
    search_fanxiu_doupotd_partner_cards,
)
from backend.core.fanxiu.catalog.digitdoor import (
    build_fanxiu_digitdoor_activity_end_probe,
    build_fanxiu_digitdoor_buff_class_formula_probe,
    build_fanxiu_digitdoor_buff_effect_usage_probe,
    build_fanxiu_digitdoor_catalog,
    build_fanxiu_digitdoor_combat_attribute_consumer_probe,
    build_fanxiu_digitdoor_door_customized_type_semantics_probe,
    build_fanxiu_digitdoor_door_gain_buff_flow_probe,
    build_fanxiu_digitdoor_door_refresh_projection_probe,
    build_fanxiu_digitdoor_gameplayer_cpp2il_consumer_probe,
    build_fanxiu_digitdoor_gameplayer_settlement_probe,
    build_fanxiu_digitdoor_info_snapshot_probe,
    build_fanxiu_digitdoor_monster_effect_class_flow_probe,
    build_fanxiu_digitdoor_monster_refresh_probe,
    build_fanxiu_digitdoor_monster_refresh_point_attribute_projection_probe,
    build_fanxiu_digitdoor_monster_refresh_point_latent_field_probe,
    build_fanxiu_digitdoor_monster_refresh_point_value_projection_probe,
    build_fanxiu_digitdoor_monster_skill_data_accessor_probe,
    build_fanxiu_digitdoor_monster_skill_buff_formula_probe,
    build_fanxiu_digitdoor_monster_skill_buff_link_probe,
    build_fanxiu_digitdoor_monster_skill_timeline_probe,
    build_fanxiu_digitdoor_monster_skill_value_projection_probe,
    build_fanxiu_digitdoor_partner_attribute_formatter_probe,
    build_fanxiu_digitdoor_pvp_balance_probe,
    build_fanxiu_digitdoor_pvp_report_acceptance_gap_probe,
    build_fanxiu_digitdoor_pvp_report_list_lifecycle_probe,
    build_fanxiu_digitdoor_pvp_report_attr_snapshot_probe,
    build_fanxiu_digitdoor_pvp_winreduce_gap_probe,
    build_fanxiu_digitdoor_pvp_winner_projection_probe,
    build_fanxiu_digitdoor_reward_marker_ui_probe,
    build_fanxiu_digitdoor_reward_marker_semantics_probe,
    build_fanxiu_digitdoor_reward_result_resolution_probe,
    build_fanxiu_digitdoor_readyfight_cpp2il_consumer_probe,
    build_fanxiu_digitdoor_readyfight_partnerlist_probe,
    build_fanxiu_digitdoor_readyfight_request_levelid_probe,
    build_fanxiu_digitdoor_readyfight_skilllist_consumer_probe,
    build_fanxiu_digitdoor_readyfight_skilllist_shape_probe,
    build_fanxiu_digitdoor_report_gmbattle_probe,
    build_fanxiu_digitdoor_skip_level_probe,
    build_fanxiu_digitdoor_skill_enhance_application_probe,
    build_fanxiu_digitdoor_skill_enhance_effect_id_namespace_probe,
    build_fanxiu_digitdoor_skill_enhance_effect_usage_probe,
    build_fanxiu_digitdoor_startgame_cpp2il_consumer_probe,
    build_fanxiu_digitdoor_startgame_response_boundary_probe,
    build_fanxiu_digitdoor_startgame_skillvos_shape_probe,
    build_fanxiu_digitdoor_unlock_state_probe,
    build_fanxiu_digitdoor_uplevel_state_probe,
    get_fanxiu_digitdoor_character_card,
    get_fanxiu_digitdoor_enhance_group,
    get_fanxiu_digitdoor_level_config,
    search_fanxiu_digitdoor_character_cards,
    search_fanxiu_digitdoor_enhance_groups,
    search_fanxiu_digitdoor_level_configs,
    build_fanxiu_pvp_report_family_reuse_probe,
)
from backend.core.fanxiu.catalog.activity import (
    get_fanxiu_activity_card,
    search_fanxiu_activity_cards,
)
from backend.core.fanxiu.catalog.gongfa import (
    build_fanxiu_gongfa_catalog,
    get_fanxiu_gongfa_card,
    load_fanxiu_gongfa_runtime_index,
    search_fanxiu_gongfa_cards,
)
from backend.core.fanxiu.catalog.game_luaconfig import (
    build_fanxiu_gongfa_feature_probe,
    build_fanxiu_lingjie_feature_catalog,
    build_fanxiu_special_gongfa_feature_probe,
    get_fanxiu_lingjie_feature_card,
    search_fanxiu_lingjie_feature_cards,
)
from backend.core.fanxiu.catalog.lua_config import build_fanxiu_lua_config_batch_report, build_fanxiu_lua_config_report
from backend.core.fanxiu.catalog.lua_logic_index import (
    build_fanxiu_lingjie_gongfa_runtime_report,
    build_fanxiu_lua_logic_index,
)
from backend.core.fanxiu.catalog.lua_packet_index import (
    build_fanxiu_lua_lscript_module_netlogic_flow_probe,
    build_fanxiu_lua_lscript_module_protocol_schema_probe,
    build_fanxiu_lua_lscript_module_surface_probe,
    build_fanxiu_lua_lscript_surface_inventory_probe,
    build_fanxiu_lua_raw_lscript_export_coverage_probe,
    build_fanxiu_lua_raw_lscript_missing_export_probe,
    build_fanxiu_lua_login_finish_post_sync_probe,
    build_fanxiu_lua_login_post_sync_cpp2il_manager_surface_probe,
    build_fanxiu_lua_login_post_sync_manager_source_gap_probe,
    build_fanxiu_lua_login_post_sync_handler_probe,
    build_fanxiu_lua_login_post_sync_lua_loader_boundary_probe,
    build_fanxiu_lua_login_post_sync_protocol_family_probe,
    build_fanxiu_lua_login_post_sync_raw_lscript_bundle_gap_probe,
    build_fanxiu_lua_login_post_sync_raw_lscript_handler_closure_probe,
    build_fanxiu_lua_login_post_sync_unresolved_handler_gap_probe,
    build_fanxiu_lua_login_socket_response_flow_probe,
    build_fanxiu_lua_login_socket_send_flow_probe,
    build_fanxiu_lua_sm_login_nested_vo_depth2_probe,
    build_fanxiu_lua_sm_login_nested_vo_probe,
)
from backend.core.fanxiu.catalog.protocol_semantics import load_fanxiu_protocol_semantics
from backend.core.fanxiu.catalog.resources import (
    FanxiuResourceError,
    build_fanxiu_resource_summary,
    export_fanxiu_unity_text_assets,
    export_fanxiu_unity_textures,
    extract_fanxiu_wwise_wems,
    inspect_fanxiu_unity_bundle,
    inspect_fanxiu_wwise_bank,
    list_fanxiu_unity_bundles,
    resolve_fanxiu_export_root,
    resolve_fanxiu_sprite_icon_path,
)
from backend.core.fanxiu.catalog.visual import (
    build_fanxiu_static_visual_catalog,
    load_fanxiu_static_visual_manifest,
    resolve_fanxiu_visual_media_path,
    search_fanxiu_static_visual_by_image,
)
from backend.core.fanxiu.catalog.asset import (
    ASSET_PREVIEW_CACHE_VERSION,
    build_fanxiu_static_asset_preview,
    build_fanxiu_static_asset_catalog,
    build_fanxiu_static_asset_preview_manifest,
    load_fanxiu_static_asset_manifest,
    resolve_fanxiu_static_asset_preview_media_path,
)
from backend.core.fanxiu.catalog.wiki import (
    build_fanxiu_wiki_catalog,
    get_fanxiu_wiki_text_entry,
    resolve_fanxiu_wiki_media_path,
    search_fanxiu_wiki_gallery,
    search_fanxiu_wiki_texts,
)
from backend.core.access.feature_access_guard import require_feature_access_dependency


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


class FanxiuStaticVisualCatalogRequest(BaseModel):
    resource_root: str | None = None
    apk_root: str | None = None
    export_root: str | None = None
    export_target_images: bool = True
    include_apk_images: bool = True
    build_usage_index: bool = True
    max_export_images: int | None = Field(default=None, ge=0, le=20000)
    max_usage_rows: int = Field(default=200000, ge=0, le=1000000)


class FanxiuWikiLinkTargetsRequest(BaseModel):
    texts: list[str] = Field(default_factory=list)
    limit: int = Field(default=200, ge=1, le=1000)
    export_root: str | None = None


class FanxiuStaticAssetCatalogRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None
    source_kinds: list[str] | None = None
    max_files: int | None = Field(default=None, ge=0, le=100000)
    verify_unity: bool = False
    parse_unity_objects: bool = False
    max_parse_files: int | None = Field(default=None, ge=0, le=100000)


class FanxiuWwiseAudioCatalogRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuWwiseMp3ExportRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None
    vgmstream_cli: str | None = None
    ffmpeg_path: str | None = None
    max_banks: int | None = Field(default=None, ge=0, le=10000)
    max_entries: int | None = Field(default=None, ge=0, le=100000)
    overwrite: bool = False
    mp3_quality: int = Field(default=4, ge=0, le=9)


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


class FanxiuApkManifestProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkNetworkStackProbeRequest(BaseModel):
    apk_root: str | None = None
    resource_root: str | None = None
    export_root: str | None = None
    max_rows: int = Field(default=1000, ge=10, le=5000)


class FanxiuApkLoginServerFlowProbeRequest(BaseModel):
    apk_root: str | None = None
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuApkDexLoginSurfaceProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None
    max_rows: int = Field(default=600, ge=50, le=3000)


class FanxiuApkDexLoginBodyProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkDexLoginPayloadShapeProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxLoginSourceProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSqPluginCoreProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSy37LoginAccountProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSy37EndpointUsageProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSy37LoginResponseSurfaceProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSy37RequestSigningProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSy37UrlCatalogProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSy37UrlUpdateProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkJadxSy37WrapperEndpointProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkUnityLoginReceiverProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkPhoneHelperLoginContextProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuApkIl2CppBinaryBoundaryProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuCpp2IlLoginLuaBridgeProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuCpp2IlGameLoginServerListBridgeProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuCpp2IlFileUtilPostLoaderProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuCpp2IlSocketProtoBridgeProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuCpp2IlSocketReceiveDispatchProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLoginTokenToSocketHandoffProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLoginAccountSignSourceProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaServerListResponseFlowProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginSocketSendFlowProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginSocketResponseFlowProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginFinishPostSyncProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaRawLscriptExportCoverageProbeRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuLuaRawLscriptMissingExportProbeRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None
    status: list[str] = Field(default_factory=lambda: ["missing_export_by_hash"])
    group_prefix: str | None = None
    module_contains: str | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)
    order_by: str = "path"
    dry_run: bool = False
    refresh_coverage: bool = True


class FanxiuLuaLscriptSurfaceInventoryProbeRequest(BaseModel):
    export_root: str | None = None
    max_asset_rows: int | None = Field(default=None, ge=1, le=100000)


class FanxiuLuaLscriptModuleSurfaceProbeRequest(BaseModel):
    export_root: str | None = None
    module: str = Field(min_length=1)
    group: str = "gamesystem/game"
    max_files: int | None = Field(default=None, ge=1, le=10000)
    max_marker_rows: int = Field(default=5000, ge=0, le=100000)


class FanxiuLuaLscriptModuleNetLogicFlowProbeRequest(BaseModel):
    export_root: str | None = None
    module: str = Field(min_length=1)
    group: str = "gamesystem/game"
    max_functions: int | None = Field(default=None, ge=1, le=1000)


class FanxiuLuaLscriptModuleProtocolSchemaProbeRequest(BaseModel):
    export_root: str | None = None
    module: str = Field(min_length=1)
    group: str = "gamesystem/game"




class FanxiuLuaLoginPostSyncCpp2IlManagerSurfaceProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginPostSyncHandlerProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginPostSyncLuaLoaderBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginPostSyncManagerSourceGapProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginPostSyncProtocolFamilyProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaLoginPostSyncRawLscriptBundleGapProbeRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuLuaLoginPostSyncRawLscriptHandlerClosureProbeRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuLuaLoginPostSyncUnresolvedHandlerGapProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaSmLoginNestedVoProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuLuaSmLoginNestedVoDepth2ProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuApkGameLoginBridgeProbeRequest(BaseModel):
    apk_root: str | None = None
    export_root: str | None = None


class FanxiuLuaDownloadBridgeReportRequest(BaseModel):
    export_root: str | None = None


class FanxiuIl2CppDownloadInventoryRequest(BaseModel):
    export_root: str | None = None


class FanxiuResourcePackageReportRequest(BaseModel):
    apk_root: str | None = None
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuTapTapDownloadDatPackageProbeRequest(BaseModel):
    download_path: str | None = None
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


class FanxiuHotUpdateBlueStarSeaPlanRewardProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaProgressionProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaStarEvolutionProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaFazeEffectProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaTreeFazeUsageProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaAuthorityBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlueStarSeaProtocolSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldRuntimeProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldAuthorityBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldProtocolSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldFinishFlowProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldRewardCatalogProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldCombatMechanicsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBlldLevelCatalogProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateFazeAuthorityBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateFazeProtocolSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaProtocolSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaUpgradeTimesFlowProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeLifecycleProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeLearnTeachProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeRecordGridLightProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeMutationOpsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakePageListProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeShareProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeShareUiProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeShareHrefProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeShareHrefPrefabProbeRequest(BaseModel):
    resource_root: str | None = None
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeShareHrefRegistrationGapProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeDetailViewProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeDetailRendererProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeDetailRendererSampleProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeRendererSourceSelectionProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeStaticRendererCoverageProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeXianShuStaticGapProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeXianShuFormulaCatalogProbeRequest(BaseModel):
    export_root: str | None = None
    star: int | None = None


class FanxiuHotUpdateGongfaHomeMakeXianShuFormulaUsageProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeXianShuBattleStateUsageProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeXianShuCastRequestBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeXianShuCastAckConsumerProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeSkillCastBridgeBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeStageStarTimelineBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeStageStarTimelineConfigProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeTimelineHurtProjectionProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeSideFeatureSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeFazeLevelNameMatchBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeFazeLevelSkillOwnershipProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeBuffFieldSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeBuffCombatResultProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeBuffResultCorrelationProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeCpp2IlBuffResultSymbolProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeBuffParameterSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaHomeMakeMechanismOwnershipProbeRequest(BaseModel):
    export_root: str | None = None
    buff_id: str | int | None = None


class FanxiuHotUpdateGongfaHomeMakeMechanismFormulaSurfaceProbeRequest(BaseModel):
    export_root: str | None = None
    buff_id: str | int | None = None
    star: int | None = None
    jie: int | None = None




class FanxiuHotUpdateGongfaHomeMakeMechanismResultProducerProbeRequest(BaseModel):
    export_root: str | None = None
    buff_id: str | int | None = None


class FanxiuHotUpdateGongfaHomeMakeNonFunnelBuffBoundaryProbeRequest(BaseModel):
    export_root: str | None = None
    buff_id: str | int | None = None


class FanxiuHotUpdateFightResultFamilyDecoderProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBuffChangeResultDecoderProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateBuffStateDecoderProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateSocketPrimitiveDecoderProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateTypedPoolRuntimeObservationProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateSocketRawDecoderProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateSocketCompressedIntCodecProbeRequest(BaseModel):
    export_root: str | None = None






class FanxiuHotUpdateCombatFormulaAuthorityContrastProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateCpp2IlMainCombatFormulaSurfaceProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaViewSnapshotProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaProgramEquipProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateFazeEffectCatalogProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateFazeEffectUpdateEventProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateFazeEffectLuaUsageProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaSpecialFazeFocusProbeRequest(BaseModel):
    export_root: str | None = None
    gongfa_id: str | int | None = None
    query: str = ""


class FanxiuHotUpdateGongfaSpecialFazeCatalogProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaSpecialFazeEffectTypeIndexProbeRequest(BaseModel):
    export_root: str | None = None
    min_stage_count: int = Field(default=1, ge=1)


class FanxiuHotUpdateGongfaSpecialFazeAttrKeyIndexProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaSpecialFazeParamShapeIndexProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaSpecialFazeParamItemRefProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaSpecialFazePayloadSummaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuHotUpdateGongfaSpecialFazeRuntimeSampleAuditProbeRequest(BaseModel):
    export_root: str | None = None
    focus_effect_type: str | int = "804"


class FanxiuHotUpdateGongfaSpecialFazeReasonProbeRequest(BaseModel):
    export_root: str | None = None
    gongfa_id: str | int | None = None
    query: str = ""


class FanxiuHotUpdateGongfaSpecialFazeReasonReuseProbeRequest(BaseModel):
    export_root: str | None = None
    reason: str | int = "1265"


class FanxiuHotUpdateGongfaSpecialFazeReasonReuseIndexProbeRequest(BaseModel):
    export_root: str | None = None
    min_gongfa_count: int = Field(default=2, ge=1)


class FanxiuHotUpdateFazeSourceSemanticsProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuIl2CppMetadataProbeRequest(BaseModel):
    metadata_path: str | None = None
    apk_root: str | None = None
    export_root: str | None = None
    keyword_hit_limit: int = Field(default=30000, ge=100, le=100000)


class FanxiuIl2CppGameplaySymbolReportRequest(BaseModel):
    metadata_path: str | None = None
    apk_root: str | None = None
    export_root: str | None = None
    keywords: list[str] | None = None
    string_keywords: list[str] | None = None
    row_limit: int = Field(default=1000, ge=10, le=10000)


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


class FanxiuDoupoTDCatalogRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    card_compose_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDoupoTDSkillTimelineProbeRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDoupoTDBuffEffectProbeRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDoupoTDBuffClassSemanticsProbeRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDoupoTDBuffClassFlowProbeRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None
    buff_classes: list[str] | None = None


class FanxiuDoupoTDBuffAuthorityBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDEffectGamePlayerSummaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDGamePlayerResultProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportGapProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportGlobalLuaSurfaceProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportScenePayloadProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportNativeSymbolGapProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportNativeLuaBridgeBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportNetLogicFamilyProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportLuaBindingBoundaryProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportRawExportCoverageProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportShapeAliasProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportSenderAliasGapProbeRequest(BaseModel):
    export_root: str | None = None










































































class FanxiuDoupoTDPvpReportTriggerLifecycleProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportTriggerBaseDynamicGapProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDPvpReportTriggerDeltaProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDoupoTDRewardConfigProbeRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDoupoTDMonsterDropResolutionProbeRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    drop_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDoupoTDStoreBagVisualProbeRequest(BaseModel):
    tower_defense_config_dir: str | None = None
    drop_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDoupoTDRewardResultResolutionProbeRequest(BaseModel):
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorCatalogRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorSkillEnhanceEffectUsageProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorSkillEnhanceApplicationProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorSkillEnhanceEffectIdNamespaceProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorReadyFightSkillListConsumerProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorReadyFightSkillListShapeProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorReadyFightCpp2IlConsumerProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorReadyFightRuntimeSampleProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDigitDoorReadyFightRequestLevelIdProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorReadyFightPartnerListProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorStartGameResponseBoundaryProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorStartGameSkillVosShapeProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorStartGameRuntimeSampleProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDigitDoorStartGameCpp2IlConsumerProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDigitDoorPartnerAttributeFormatterProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorCombatAttributeConsumerProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorGamePlayerSettlementProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorGamePlayerRuntimeSampleProbeRequest(BaseModel):
    export_root: str | None = None


class FanxiuDigitDoorGamePlayerCpp2IlConsumerProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorInfoSnapshotProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorUpLevelStateProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorUnlockStateProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorSkipLevelProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorActivityEndProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorReportGMBattleProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorPvpBalanceProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorPvpReportAttrSnapshotProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorPvpReportAcceptanceGapProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorPvpReportListLifecycleProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorPvpWinreduceGapProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorPvpWinnerProjectionProbeRequest(BaseModel):
    digitdoor_logic_dir: str | None = None
    export_root: str | None = None


class FanxiuPvpReportFamilyReuseProbeRequest(BaseModel):
    export_root: str | None = None




class FanxiuDigitDoorBuffEffectUsageProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorBuffClassFormulaProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorRewardResultResolutionProbeRequest(BaseModel):
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorRewardMarkerSemanticsProbeRequest(BaseModel):
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorRewardMarkerUiProbeRequest(BaseModel):
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterRefreshProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorDoorRefreshProjectionProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorDoorGainBuffFlowProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorDoorCustomizedTypeSemanticsProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterSkillTimelineProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterEffectClassFlowProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None
    effect_classes: list[str] | None = None


class FanxiuDigitDoorMonsterRefreshPointValueProjectionProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterRefreshPointAttributeProjectionProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterRefreshPointLatentFieldProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterSkillDataAccessorProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterSkillValueProjectionProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterSkillBuffLinkProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


class FanxiuDigitDoorMonsterSkillBuffFormulaProbeRequest(BaseModel):
    digitdoor_config_dir: str | None = None
    digitdoor_logic_dir: str | None = None
    lang_path: str | None = None
    export_root: str | None = None


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




class FanxiuLingjieGongfaRuntimeReportRequest(BaseModel):
    source_dir: str | None = None
    packet_index_dir: str | None = None
    apk_root: str | None = None
    export_root: str | None = None


def _run_resource_operation(func, *args, **kwargs) -> dict[str, Any]:
    try:
        return func(*args, **kwargs)
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _clean_link_alias(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


FANXIU_WIKI_LINK_ALIAS_BLACKLIST = {"攻击"}


def _strip_link_preview_rich_tags(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return (
        text.replace("<color=#017077>", "")
        .replace("<color=#193970>", "")
        .replace("<color=#2a4b10>", "")
        .replace("<color=#3e147d>", "")
        .replace("<color=#73123a>", "")
        .replace("<color=#864c00>", "")
        .replace("<color=#9e1e09>", "")
        .replace("</color>", "")
    )


def _compact_link_preview(value: Any, limit: int = 1200) -> str:
    text = " ".join(_strip_link_preview_rich_tags(value).replace("\u3000", " ").split())
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _clean_link_preview(value: Any, limit: int = 1200) -> str:
    text = _strip_link_preview_rich_tags(value)
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ").strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _same_link_preview(left: Any, right: Any) -> bool:
    left_text = _compact_link_preview(left)
    return bool(left_text) and left_text == _compact_link_preview(right)


def _first_gongfa_skill_preview(card: dict[str, Any]) -> str:
    for skill in card.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        preview = _clean_link_preview(skill.get("describe") or skill.get("effect_describe") or skill.get("additional_describe"))
        if preview:
            return preview
    return ""


def _gongfa_link_preview(card: dict[str, Any]) -> str:
    return _clean_link_preview(card.get("description") or card.get("description_rich")) or _first_gongfa_skill_preview(card)


def _gongfa_link_effect_text_preview(card: dict[str, Any]) -> str:
    effect = _first_gongfa_skill_preview(card)
    return "" if _same_link_preview(effect, card.get("description") or card.get("description_rich")) else effect


def _item_link_preview(card: dict[str, Any]) -> str:
    return _clean_link_preview(card.get("description") or card.get("effect_description"))


def _item_link_effect_text_preview(card: dict[str, Any]) -> str:
    effect = _clean_link_preview(card.get("effect_description"))
    return "" if _same_link_preview(effect, card.get("description")) else effect


def _item_link_effect_preview(card: dict[str, Any]) -> str:
    return _clean_link_preview(card.get("show_effect"))


def _item_link_reward_preview(card: dict[str, Any]) -> str:
    rewards: list[dict[str, Any]] = []
    for reward in card.get("optional_gift_rewards") or []:
        if not isinstance(reward, dict):
            continue
        rewards.append(
            {
                "id": reward.get("id"),
                "name": reward.get("name"),
                "count": reward.get("count"),
                "icon": reward.get("icon"),
                "description": _clean_link_preview(reward.get("description"), limit=160),
            }
        )
    return json.dumps(rewards[:20], ensure_ascii=False, separators=(",", ":")) if rewards else ""


def _add_link_alias(
    rows: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    *,
    alias: Any,
    tab: str,
    object_id: Any,
    title: Any,
    kind: str,
    priority: int = 0,
    preview: Any = "",
    effect_text_preview: Any = "",
    effect_preview: Any = "",
    reward_preview: Any = "",
) -> None:
    text = _clean_link_alias(alias)
    object_id_text = str(object_id or "").strip()
    if len(text) < 2 or text in FANXIU_WIKI_LINK_ALIAS_BLACKLIST or not object_id_text:
        return
    key = (text, tab, object_id_text)
    if key in seen:
        return
    seen.add(key)
    rows.append(
        {
            "alias": text,
            "tab": tab,
            "id": object_id_text,
            "title": _clean_link_alias(title) or text,
            "preview": _clean_link_preview(preview),
            "effect_text_preview": _clean_link_preview(effect_text_preview),
            "effect_preview": _clean_link_preview(effect_preview),
            "reward_preview": str(reward_preview or ""),
            "kind": kind,
            "priority": priority,
        }
    )


def _build_fanxiu_wiki_link_index_uncached(export_root: str | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    gongfa_index = load_fanxiu_gongfa_runtime_index(export_root=export_root)
    gongfa_cards = [card for card in (gongfa_index.get("catalog") or {}).get("cards") or [] if isinstance(card, dict)]
    for card in gongfa_cards:
        card_id = card.get("id")
        name = _clean_link_alias(card.get("name"))
        card_preview = _gongfa_link_preview(card)
        card_effect_text_preview = _gongfa_link_effect_text_preview(card)
        _add_link_alias(
            rows,
            seen,
            alias=name,
            tab="gongfa",
            object_id=card_id,
            title=name,
            kind="gongfa",
            priority=80,
            preview=card_preview,
            effect_text_preview=card_effect_text_preview,
        )
        for prefix in (card.get("quality_family_name"), card.get("quality_grade_name")):
            prefix_text = _clean_link_alias(prefix)
            if prefix_text and name and not name.startswith(f"{prefix_text}·"):
                _add_link_alias(
                    rows,
                    seen,
                    alias=f"{prefix_text}·{name}",
                    tab="gongfa",
                    object_id=card_id,
                    title=name,
                    kind="gongfa_alias",
                    priority=95,
                    preview=card_preview,
                    effect_text_preview=card_effect_text_preview,
                )
        progression = card.get("progression") or {}
        if isinstance(progression, dict):
            for progression_rows in progression.values():
                if not isinstance(progression_rows, list):
                    continue
                for row in progression_rows:
                    if not isinstance(row, dict):
                        continue
                    faze_resource = row.get("faze_resource")
                    if not isinstance(faze_resource, dict):
                        continue
                    for alias in (faze_resource.get("name"), faze_resource.get("head_name")):
                        _add_link_alias(
                            rows,
                            seen,
                            alias=alias,
                            tab="gongfa",
                            object_id=card_id,
                            title=name,
                            kind="faze_resource",
                            priority=100,
                            preview=faze_resource.get("tip_str") or card_preview,
                        )

    item_index = load_fanxiu_item_runtime_index(export_root=export_root)
    item_cards = [card for card in (item_index.get("catalog") or {}).get("cards") or [] if isinstance(card, dict)]
    for card in item_cards:
        name = _clean_link_alias(card.get("name"))
        _add_link_alias(
            rows,
            seen,
            alias=name,
            tab="item",
            object_id=card.get("id"),
            title=name,
            kind="item",
            priority=70,
            preview=_item_link_preview(card),
            effect_text_preview=_item_link_effect_text_preview(card),
            effect_preview=_item_link_effect_preview(card),
            reward_preview=_item_link_reward_preview(card),
        )

    rows.sort(key=lambda item: (-len(str(item.get("alias") or "")), -int(item.get("priority") or 0), str(item.get("alias") or "")))
    return {"items": rows, "total": len(rows)}


def _wiki_link_index_source_key(export_root: str | None = None) -> tuple[str, int, int, int, int]:
    root = resolve_fanxiu_export_root(export_root)
    source_paths = [
        root / "parsed_configs" / "gongfa_catalog" / "gongfa_catalog.json",
        root / "parsed_configs" / "item_catalog" / "item_catalog.json",
    ]
    values: list[int] = []
    for path in source_paths:
        try:
            stat = path.stat()
        except OSError:
            values.extend([0, 0])
        else:
            values.extend([stat.st_mtime_ns, stat.st_size])
    return (str(root), *values)


def _fanxiu_wiki_link_index_cache_path(
    export_root_text: str,
    gongfa_mtime_ns: int,
    gongfa_size: int,
    item_mtime_ns: int,
    item_size: int,
) -> Path:
    digest = hashlib.sha1(
        f"{export_root_text}|{gongfa_mtime_ns}|{gongfa_size}|{item_mtime_ns}|{item_size}".encode("utf-8")
    ).hexdigest()[:16]
    return get_settings().data_dir / "fanxiu" / "wiki-link-index" / f"{digest}.json"


def _read_fanxiu_wiki_link_index_disk_cache(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    total = payload.get("total")
    return {
        "items": items,
        "total": int(total) if isinstance(total, int) else len(items),
    }


def _write_fanxiu_wiki_link_index_disk_cache(path: Path, payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("items"), list):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        return


@lru_cache(maxsize=4)
def _build_fanxiu_wiki_link_index_cached(
    export_root_text: str,
    gongfa_mtime_ns: int,
    gongfa_size: int,
    item_mtime_ns: int,
    item_size: int,
) -> dict[str, Any]:
    cache_path = _fanxiu_wiki_link_index_cache_path(
        export_root_text,
        gongfa_mtime_ns,
        gongfa_size,
        item_mtime_ns,
        item_size,
    )
    cached = _read_fanxiu_wiki_link_index_disk_cache(cache_path)
    if cached is not None:
        return cached
    payload = _build_fanxiu_wiki_link_index_uncached(export_root=export_root_text)
    _write_fanxiu_wiki_link_index_disk_cache(cache_path, payload)
    return payload


def build_fanxiu_wiki_link_index(export_root: str | None = None) -> dict[str, Any]:
    return _build_fanxiu_wiki_link_index_cached(*_wiki_link_index_source_key(export_root))


def build_fanxiu_wiki_link_targets(
    *,
    texts: list[str],
    limit: int = 200,
    export_root: str | None = None,
) -> dict[str, Any]:
    joined_text = "\n".join(str(text or "") for text in texts)
    if not joined_text.strip():
        return {"items": [], "total": 0, "source_total": build_fanxiu_wiki_link_index(export_root).get("total", 0)}
    index = build_fanxiu_wiki_link_index(export_root)
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for item in index.get("items") or []:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias") or "").strip()
        if not alias or alias not in joined_text:
            continue
        key = (alias, str(item.get("tab") or ""), str(item.get("id") or ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
        if len(rows) >= limit:
            break
    return {"items": rows, "total": len(rows), "source_total": index.get("total", 0)}


@router.get("/resources/summary")
def get_fanxiu_resource_summary(resource_root: str | None = Query(default=None)) -> dict[str, Any]:
    return _run_resource_operation(build_fanxiu_resource_summary, resource_root=resource_root)


@router.get("/resources/wiki/catalog")
def get_fanxiu_resource_wiki_catalog(export_root: str | None = Query(default=None)) -> dict[str, Any]:
    return _run_resource_operation(build_fanxiu_wiki_catalog, export_root=export_root)


@router.get("/resources/wiki/link-index")
def get_fanxiu_resource_wiki_link_index(export_root: str | None = Query(default=None)) -> dict[str, Any]:
    return _run_resource_operation(build_fanxiu_wiki_link_index, export_root=export_root)


@router.post("/resources/wiki/link-targets")
def get_fanxiu_resource_wiki_link_targets(req: FanxiuWikiLinkTargetsRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_wiki_link_targets,
        texts=req.texts,
        limit=req.limit,
        export_root=req.export_root,
    )


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


@router.get("/resources/protocol-semantics")
def get_fanxiu_protocol_semantics(
    feature: str = Query(default="bluestarsea"),
    query: str = Query(default=""),
    role: str = Query(default=""),
    operation: str = Query(default=""),
    limit: int = Query(default=300, ge=1, le=2000),
    edge_limit: int = Query(default=300, ge=1, le=3000),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        load_fanxiu_protocol_semantics,
        feature=feature,
        query=query,
        role=role,
        operation=operation,
        limit=limit,
        edge_limit=edge_limit,
        export_root=export_root,
    )


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


@router.get("/resources/gongfa/homemake-static-detail")
def get_fanxiu_gongfa_homemake_static_detail(
    gongfa_id: str = Query(min_length=1),
    star: int = Query(default=1, ge=1),
    jie: int = Query(default=1, ge=1),
    pin: int = Query(default=1, ge=1),
    include_inactive: bool = Query(default=True),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        render_fanxiu_gongfa_homemake_static_detail,
        gongfa_id,
        star=star,
        jie=jie,
        pin=pin,
        include_inactive=include_inactive,
        export_root=export_root,
    )


@router.get("/resources/gongfa/homemake-buff-parameter-semantics")
def get_fanxiu_gongfa_homemake_buff_parameter_semantics_endpoint(
    gongfa_id: str | None = Query(default=None),
    query: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_gongfa_homemake_buff_parameter_semantics,
        gongfa_id=gongfa_id,
        query=query,
        limit=limit,
        export_root=export_root,
    )


@router.get("/resources/gongfa/homemake-xianshu-formula-catalog")
def get_fanxiu_gongfa_homemake_xianshu_formula_catalog_endpoint(
    gongfa_id: str | None = Query(default=None),
    query: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    star: int = Query(default=1, ge=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_gongfa_homemake_xianshu_formula_catalog,
        gongfa_id=gongfa_id,
        query=query,
        limit=limit,
        star=star,
        export_root=export_root,
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


@router.get("/resources/doupotd/partner-cards")
def get_fanxiu_doupotd_partner_cards(
    query: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_doupotd_partner_cards,
        query=query,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/doupotd/partner-card")
def get_fanxiu_doupotd_partner_card_detail(
    partner_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_doupotd_partner_card,
        partner_id,
        export_root=export_root,
    )


@router.get("/resources/doupotd/reward-configs")
def get_fanxiu_doupotd_reward_configs(
    query: str = Query(default=""),
    source_table: str = Query(default=""),
    stage: str = Query(default=""),
    item_id: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_doupotd_reward_configs,
        query=query,
        source_table=source_table,
        stage=stage,
        item_id=item_id,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/doupotd/reward-config")
def get_fanxiu_doupotd_reward_config_detail(
    source_table: str = Query(min_length=1),
    config_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_doupotd_reward_config,
        source_table=source_table,
        config_id=config_id,
        export_root=export_root,
    )


@router.get("/resources/xianqiao/mechanics")
def get_fanxiu_xianqiao_mechanics(
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_xianqiao_mechanics,
        export_root=export_root,
    )


@router.get("/resources/items/cards")
def get_fanxiu_item_cards(
    query: str = Query(default=""),
    quality_name: str = Query(default=""),
    type_key: str = Query(default=""),
    sub_type_key: str = Query(default=""),
    icon_quality: str = Query(default=""),
    icon_name: str = Query(default=""),
    small_icon_quality: str = Query(default=""),
    small_icon_name: str = Query(default=""),
    sort_by: str = Query(default="default"),
    sort_order: str = Query(default="asc"),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_facets: bool = Query(default=True),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_item_cards,
        query=query,
        quality_name=quality_name,
        type_key=type_key,
        sub_type_key=sub_type_key,
        icon_quality=icon_quality,
        icon_name=icon_name,
        small_icon_quality=small_icon_quality,
        small_icon_name=small_icon_name,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        include_facets=include_facets,
        export_root=export_root,
        rebuild_missing=False,
    )


def _get_fanxiu_item_cards_by_ids(item_ids: str) -> dict[str, Any]:
    requested_ids = list(dict.fromkeys(part.strip() for part in item_ids.split(",") if part.strip()))[:200]
    cards: list[dict[str, Any]] = []
    missing: list[str] = []
    catalog_path = ""
    for item_id in requested_ids:
        try:
            result = get_fanxiu_item_card(item_id, rebuild_missing=False)
        except FanxiuResourceError:
            missing.append(item_id)
            continue
        catalog_path = str(result.get("catalog_path") or catalog_path)
        card = result.get("card")
        if isinstance(card, dict):
            cards.append(card)
        else:
            missing.append(item_id)
    return {
        "catalog_path": catalog_path,
        "cards": cards,
        "missing": missing,
    }


@router.get("/resources/items/cards/by-ids")
def get_fanxiu_item_cards_by_ids(
    item_ids: str = Query(min_length=1),
) -> dict[str, Any]:
    return _run_resource_operation(_get_fanxiu_item_cards_by_ids, item_ids=item_ids)


@router.get("/resources/items/card")
def get_fanxiu_item_card_detail(
    item_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_item_card,
        item_id,
        export_root=export_root,
        rebuild_missing=False,
    )


@router.get("/resources/items/icon-quality-review")
def get_fanxiu_item_icon_quality_review(
    threshold: int = Query(default=50, ge=1, le=1000),
    rebuild_missing: bool = Query(default=True),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        load_item_icon_quality_review,
        threshold=threshold,
        rebuild_missing=rebuild_missing,
        export_root=export_root,
    )


@router.get("/resources/digitdoor/character-cards")
def get_fanxiu_digitdoor_character_cards(
    query: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_digitdoor_character_cards,
        query=query,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/digitdoor/character-card")
def get_fanxiu_digitdoor_character_card_detail(
    character_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_digitdoor_character_card,
        character_id,
        export_root=export_root,
    )


@router.get("/resources/digitdoor/level-configs")
def get_fanxiu_digitdoor_level_configs(
    query: str = Query(default=""),
    stage: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_digitdoor_level_configs,
        query=query,
        stage=stage,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/digitdoor/level-config")
def get_fanxiu_digitdoor_level_config_detail(
    level_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_digitdoor_level_config,
        level_id,
        export_root=export_root,
    )


@router.get("/resources/digitdoor/enhance-groups")
def get_fanxiu_digitdoor_enhance_groups(
    query: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_digitdoor_enhance_groups,
        query=query,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.get("/resources/digitdoor/enhance-group")
def get_fanxiu_digitdoor_enhance_group_detail(
    group_id: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_digitdoor_enhance_group,
        group_id,
        export_root=export_root,
    )


@router.get("/resources/activities/cards")
def get_fanxiu_activity_cards(
    query: str = Query(default=""),
    kind_key: str = Query(default=""),
    time_kind: str = Query(default=""),
    activity_type: str = Query(default=""),
    server_scope: str = Query(default=""),
    sort_by: str = Query(default="default"),
    sort_order: str = Query(default="asc"),
    limit: int = Query(default=80, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    item_view: str = Query(default="default"),
    include_facets: bool = Query(default=True),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        search_fanxiu_activity_cards,
        query=query,
        kind_key=kind_key,
        time_kind=time_kind,
        activity_type=activity_type,
        server_scope=server_scope,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        item_view=item_view,
        include_facets=include_facets,
        export_root=export_root,
    )


@router.get("/resources/activities/card")
def get_fanxiu_activity_card_detail(
    activity_id: str = Query(min_length=1),
    server_scope: str = Query(default=""),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        get_fanxiu_activity_card,
        activity_id,
        server_scope=server_scope,
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


@router.post("/resources/visual/static-catalog")
def post_fanxiu_static_visual_catalog(req: FanxiuStaticVisualCatalogRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_static_visual_catalog,
        resource_root=req.resource_root,
        apk_root=req.apk_root,
        export_root=req.export_root,
        export_target_images=req.export_target_images,
        include_apk_images=req.include_apk_images,
        build_usage_index=req.build_usage_index,
        max_export_images=req.max_export_images,
        max_usage_rows=req.max_usage_rows,
    )


@router.get("/resources/visual/manifest")
def get_fanxiu_static_visual_manifest(
    export_root: str | None = Query(default=None),
    query: str | None = Query(default=None),
    category: str | None = Query(default=None),
    asset_group: str | None = Query(default=None),
    source_kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        result = load_fanxiu_static_visual_manifest(
            export_root=export_root,
            query=query,
            category=category,
            asset_group=asset_group,
            source_kind=source_kind,
            limit=limit,
            offset=offset,
        )
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for row in result["rows"]:
        media_path = str(row.get("media_path") or "")
        if not media_path:
            continue
        media_url = f"/api/fanxiu/resources/visual/media?path={quote(media_path, safe='')}"
        if export_root:
            media_url = f"{media_url}&export_root={quote(export_root, safe='')}"
        row["media_url"] = media_url
    return result


@router.post("/resources/visual/similarity")
async def post_fanxiu_static_visual_similarity(
    export_root: str | None = Query(default=None),
    query: str | None = Query(default=None),
    category: str | None = Query(default=None),
    asset_group: str | None = Query(default=None),
    source_kind: str | None = Query(default=None),
    limit: int = Query(default=80, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    max_prefilter: int = Query(default=600, ge=20, le=5000),
    image: UploadFile = File(...),
) -> dict[str, Any]:
    data = await image.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="上传图片过大，请使用 12MB 以内的图片")
    try:
        result = search_fanxiu_static_visual_by_image(
            image_bytes=data,
            export_root=export_root,
            query=query,
            category=category,
            asset_group=asset_group,
            source_kind=source_kind,
            limit=limit,
            offset=offset,
            max_prefilter=max_prefilter,
        )
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for row in result["rows"]:
        media_path = str(row.get("media_path") or "")
        if not media_path:
            continue
        media_url = f"/api/fanxiu/resources/visual/media?path={quote(media_path, safe='')}"
        if export_root:
            media_url = f"{media_url}&export_root={quote(export_root, safe='')}"
        row["media_url"] = media_url
    return result


@router.get("/resources/visual/media")
def get_fanxiu_static_visual_media(
    path: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> FileResponse:
    try:
        media_path = resolve_fanxiu_visual_media_path(path, export_root=export_root)
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(media_path)


@router.post("/resources/asset/static-catalog")
def post_fanxiu_static_asset_catalog(req: FanxiuStaticAssetCatalogRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_static_asset_catalog,
        resource_root=req.resource_root,
        export_root=req.export_root,
        source_kinds=req.source_kinds,
        max_files=req.max_files,
        verify_unity=req.verify_unity,
        parse_unity_objects=req.parse_unity_objects,
        max_parse_files=req.max_parse_files,
    )


@router.get("/resources/asset/manifest")
def get_fanxiu_static_asset_manifest(
    resource_root: str | None = Query(default=None),
    export_root: str | None = Query(default=None),
    query: str | None = Query(default=None),
    catalog_view: str | None = Query(default=None),
    asset_group: str | None = Query(default=None),
    source_kind: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        result = load_fanxiu_static_asset_manifest(
            resource_root=resource_root,
            export_root=export_root,
            query=query,
            catalog_view=catalog_view,
            asset_group=asset_group,
            source_kind=source_kind,
            category=category,
            limit=limit,
            offset=offset,
        )
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for row in result["rows"]:
        visual_paths = [
            item.strip()
            for item in str(row.get("semantic_visual_media_paths") or "").split("|")
            if item.strip()
        ]
        if row.get("semantic_id"):
            visual_urls = []
            for visual_path in visual_paths[:18]:
                media_url = f"/api/fanxiu/resources/visual/media?path={quote(visual_path, safe='')}"
                if export_root:
                    media_url = f"{media_url}&export_root={quote(export_root, safe='')}"
                visual_urls.append(media_url)
            row["semantic_visual_media_urls"] = visual_urls
            if visual_urls:
                row["preview_url"] = visual_urls[0]
            continue
        relative_path = str(row.get("relative_path") or "")
        if not relative_path:
            continue
        preview_url = (
            f"/api/fanxiu/resources/asset/preview?path={quote(relative_path, safe='')}"
            f"&preview_v={quote(ASSET_PREVIEW_CACHE_VERSION, safe='')}"
        )
        preview_manifest_url = (
            f"/api/fanxiu/resources/asset/preview-manifest?path={quote(relative_path, safe='')}"
            f"&preview_v={quote(ASSET_PREVIEW_CACHE_VERSION, safe='')}"
        )
        if resource_root:
            preview_url = f"{preview_url}&resource_root={quote(resource_root, safe='')}"
            preview_manifest_url = f"{preview_manifest_url}&resource_root={quote(resource_root, safe='')}"
        if export_root:
            preview_url = f"{preview_url}&export_root={quote(export_root, safe='')}"
            preview_manifest_url = f"{preview_manifest_url}&export_root={quote(export_root, safe='')}"
        row["preview_url"] = preview_url
        row["preview_manifest_url"] = preview_manifest_url
    return result


@router.get("/resources/asset/preview")
def get_fanxiu_static_asset_preview(
    path: str = Query(min_length=1),
    resource_root: str | None = Query(default=None),
    export_root: str | None = Query(default=None),
    force: bool = Query(default=False),
) -> FileResponse:
    try:
        result = build_fanxiu_static_asset_preview(
            path,
            resource_root=resource_root,
            export_root=export_root,
            force=force,
        )
        media_path = resolve_fanxiu_static_asset_preview_media_path(
            str(result["preview_media_path"]),
            export_root=export_root,
        )
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = "image/svg+xml" if media_path.suffix.lower() == ".svg" else "image/png"
    return FileResponse(media_path, media_type=media_type)


@router.get("/resources/asset/preview-manifest")
def get_fanxiu_static_asset_preview_manifest(
    path: str = Query(min_length=1),
    resource_root: str | None = Query(default=None),
    export_root: str | None = Query(default=None),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        result = build_fanxiu_static_asset_preview_manifest(
            path,
            resource_root=resource_root,
            export_root=export_root,
            force=force,
        )
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    for item in result.get("items", []):
        media_path = str(item.get("media_path") or "")
        if not media_path:
            continue
        media_url = (
            f"/api/fanxiu/resources/asset/preview-media?path={quote(media_path, safe='')}"
            f"&preview_v={quote(ASSET_PREVIEW_CACHE_VERSION, safe='')}"
        )
        if export_root:
            media_url = f"{media_url}&export_root={quote(export_root, safe='')}"
        item["media_url"] = media_url
    return result


@router.get("/resources/asset/preview-media")
def get_fanxiu_static_asset_preview_media(
    path: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> FileResponse:
    try:
        media_path = resolve_fanxiu_static_asset_preview_media_path(path, export_root=export_root)
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = "image/svg+xml" if media_path.suffix.lower() == ".svg" else "image/png"
    return FileResponse(media_path, media_type=media_type)


@router.post("/resources/wwise/audio-catalog")
def post_fanxiu_wwise_audio_catalog(req: FanxiuWwiseAudioCatalogRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_wwise_audio_catalog,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/wwise/mp3-export")
def post_fanxiu_wwise_mp3_export(req: FanxiuWwiseMp3ExportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_wwise_mp3_export,
        resource_root=req.resource_root,
        export_root=req.export_root,
        vgmstream_cli=req.vgmstream_cli,
        ffmpeg_path=req.ffmpeg_path,
        max_banks=req.max_banks,
        max_entries=req.max_entries,
        overwrite=req.overwrite,
        mp3_quality=req.mp3_quality,
    )


@router.get("/resources/wwise/mp3-manifest")
def get_fanxiu_wwise_mp3_manifest(
    export_root: str | None = Query(default=None),
    query: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        result = load_fanxiu_wwise_mp3_manifest(
            export_root=export_root,
            query=query,
            kind=kind,
            limit=limit,
            offset=offset,
        )
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for row in result["rows"]:
        relative_path = str(row.get("relative_mp3_path") or "")
        if not relative_path:
            continue
        media_url = f"/api/fanxiu/resources/audio/media?path={quote(relative_path, safe='')}"
        player_title = f"{str(row.get('source_bank') or '').rsplit('/', 1)[-1].replace('.bnk', '')} / {row.get('wem_id') or row.get('entry_index') or 'wem'}"
        player_url = f"/api/fanxiu/resources/audio/player?path={quote(relative_path, safe='')}&title={quote(player_title, safe='')}"
        if export_root:
            media_url = f"{media_url}&export_root={quote(export_root, safe='')}"
            player_url = f"{player_url}&export_root={quote(export_root, safe='')}"
        row["media_url"] = media_url
        row["player_url"] = player_url
    return result


@router.get("/resources/audio/media")
def get_fanxiu_audio_media(
    path: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
) -> FileResponse:
    try:
        media_path = resolve_fanxiu_audio_media_path(path, export_root=export_root)
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(media_path, media_type="audio/mpeg")


@router.get("/resources/audio/player", response_class=HTMLResponse)
def get_fanxiu_audio_player(
    path: str = Query(min_length=1),
    export_root: str | None = Query(default=None),
    title: str | None = Query(default=None),
) -> HTMLResponse:
    try:
        resolve_fanxiu_audio_media_path(path, export_root=export_root)
    except FanxiuResourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    media_url = f"/api/fanxiu/resources/audio/media?path={quote(path, safe='')}"
    if export_root:
        media_url = f"{media_url}&export_root={quote(export_root, safe='')}"
    display_title = (title or path.rsplit("/", 1)[-1]).strip() or "Fanxiu audio"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(display_title)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --fg: #f6f7fb;
      --muted: #9aa4b2;
      --line: #2a3240;
      --accent: #27c2d4;
      --accent-soft: rgba(39, 194, 212, 0.18);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 36px;
      color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #050608;
    }}
    main {{
      width: min(1040px, 92vw);
      display: grid;
      gap: 18px;
    }}
    h1 {{
      margin: 0;
      overflow-wrap: anywhere;
      font-size: clamp(22px, 4vw, 42px);
      line-height: 1.15;
      letter-spacing: 0;
    }}
    .path {{
      overflow-wrap: anywhere;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .player {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 18px;
      align-items: center;
      padding: 24px 28px;
      background: #111722;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.34);
    }}
    button {{
      width: 72px;
      height: 72px;
      border: 0;
      border-radius: 50%;
      color: #06262c;
      font-size: 18px;
      font-weight: 800;
      background: var(--accent);
      cursor: pointer;
    }}
    .timeline {{
      min-width: 0;
      display: grid;
      gap: 12px;
    }}
    .time-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      font-size: 18px;
    }}
    input[type="range"] {{
      width: 100%;
      height: 34px;
      margin: 0;
      accent-color: var(--accent);
      cursor: pointer;
    }}
    .volume {{
      width: 128px;
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .volume input[type="range"] {{
      height: 24px;
    }}
    @media (max-width: 720px) {{
      body {{ padding: 18px; }}
      .player {{ grid-template-columns: 1fr; }}
      button {{ width: 64px; height: 64px; }}
      .volume {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(display_title)}</h1>
    <div class="path">{escape(path)}</div>
    <section class="player">
      <button id="toggle" type="button">播放</button>
      <div class="timeline">
        <div class="time-row">
          <span id="current">0:00</span>
          <span id="duration">0:00</span>
        </div>
        <input id="progress" type="range" min="0" max="1000" value="0" step="1" aria-label="播放进度">
      </div>
      <label class="volume">
        音量
        <input id="volume" type="range" min="0" max="1" value="1" step="0.01" aria-label="音量">
      </label>
      <audio id="audio" preload="metadata" src="{escape(media_url)}"></audio>
    </section>
  </main>
  <script>
    const audio = document.getElementById('audio');
    const toggle = document.getElementById('toggle');
    const progress = document.getElementById('progress');
    const current = document.getElementById('current');
    const duration = document.getElementById('duration');
    const volume = document.getElementById('volume');

    function fmt(value) {{
      if (!Number.isFinite(value) || value < 0) return '0:00';
      const minutes = Math.floor(value / 60);
      const seconds = Math.floor(value % 60);
      return `${{minutes}}:${{String(seconds).padStart(2, '0')}}`;
    }}
    function sync() {{
      current.textContent = fmt(audio.currentTime);
      duration.textContent = fmt(audio.duration);
      progress.value = Number.isFinite(audio.duration) && audio.duration > 0
        ? String(Math.round(audio.currentTime / audio.duration * 1000))
        : '0';
      toggle.textContent = audio.paused ? '播放' : '暂停';
    }}
    toggle.addEventListener('click', () => {{
      if (audio.paused) audio.play();
      else audio.pause();
    }});
    progress.addEventListener('input', () => {{
      if (Number.isFinite(audio.duration) && audio.duration > 0) {{
        audio.currentTime = Number(progress.value) / 1000 * audio.duration;
      }}
    }});
    volume.addEventListener('input', () => {{
      audio.volume = Number(volume.value);
    }});
    window.addEventListener('keydown', event => {{
      if (event.code !== 'Space') return;
      event.preventDefault();
      toggle.click();
    }});
    audio.addEventListener('loadedmetadata', sync);
    audio.addEventListener('timeupdate', sync);
    audio.addEventListener('play', sync);
    audio.addEventListener('pause', sync);
    audio.addEventListener('ended', sync);
    sync();
  </script>
</body>
</html>"""
    return HTMLResponse(html)


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


@router.post("/resources/apk/manifest-probe")
def post_fanxiu_apk_manifest_probe(req: FanxiuApkManifestProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_manifest_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/network-stack-probe")
def post_fanxiu_apk_network_stack_probe(req: FanxiuApkNetworkStackProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_network_stack_probe,
        apk_root=req.apk_root,
        resource_root=req.resource_root,
        export_root=req.export_root,
        max_rows=req.max_rows,
    )


@router.post("/resources/apk/login-server-flow-probe")
def post_fanxiu_apk_login_server_flow_probe(req: FanxiuApkLoginServerFlowProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_login_server_flow_probe,
        apk_root=req.apk_root,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/dex-login-surface-probe")
def post_fanxiu_apk_dex_login_surface_probe(req: FanxiuApkDexLoginSurfaceProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_dex_login_surface_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
        max_rows=req.max_rows,
    )


@router.post("/resources/apk/dex-login-body-probe")
def post_fanxiu_apk_dex_login_body_probe(req: FanxiuApkDexLoginBodyProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_dex_login_body_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/dex-login-payload-shape-probe")
def post_fanxiu_apk_dex_login_payload_shape_probe(
    req: FanxiuApkDexLoginPayloadShapeProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_dex_login_payload_shape_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-login-source-probe")
def post_fanxiu_apk_jadx_login_source_probe(req: FanxiuApkJadxLoginSourceProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_login_source_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sq-plugin-core-probe")
def post_fanxiu_apk_jadx_sq_plugin_core_probe(req: FanxiuApkJadxSqPluginCoreProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sq_plugin_core_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sy37-login-account-probe")
def post_fanxiu_apk_jadx_sy37_login_account_probe(
    req: FanxiuApkJadxSy37LoginAccountProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sy37_login_account_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sy37-request-signing-probe")
def post_fanxiu_apk_jadx_sy37_request_signing_probe(
    req: FanxiuApkJadxSy37RequestSigningProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sy37_request_signing_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sy37-endpoint-usage-probe")
def post_fanxiu_apk_jadx_sy37_endpoint_usage_probe(
    req: FanxiuApkJadxSy37EndpointUsageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sy37_endpoint_usage_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sy37-login-response-surface-probe")
def post_fanxiu_apk_jadx_sy37_login_response_surface_probe(
    req: FanxiuApkJadxSy37LoginResponseSurfaceProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sy37_login_response_surface_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sy37-url-catalog-probe")
def post_fanxiu_apk_jadx_sy37_url_catalog_probe(
    req: FanxiuApkJadxSy37UrlCatalogProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sy37_url_catalog_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sy37-url-update-probe")
def post_fanxiu_apk_jadx_sy37_url_update_probe(
    req: FanxiuApkJadxSy37UrlUpdateProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sy37_url_update_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/jadx-sy37-wrapper-endpoint-probe")
def post_fanxiu_apk_jadx_sy37_wrapper_endpoint_probe(
    req: FanxiuApkJadxSy37WrapperEndpointProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_jadx_sy37_wrapper_endpoint_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/unity-login-receiver-probe")
def post_fanxiu_apk_unity_login_receiver_probe(req: FanxiuApkUnityLoginReceiverProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_unity_login_receiver_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/phonehelper-login-context-probe")
def post_fanxiu_apk_phonehelper_login_context_probe(req: FanxiuApkPhoneHelperLoginContextProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_phonehelper_login_context_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/il2cpp-binary-boundary-probe")
def post_fanxiu_apk_il2cpp_binary_boundary_probe(req: FanxiuApkIl2CppBinaryBoundaryProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_il2cpp_binary_boundary_probe,
        apk_root=req.apk_root,
        export_root=req.export_root,
    )


@router.post("/resources/apk/cpp2il-login-lua-bridge-probe")
def post_fanxiu_cpp2il_login_lua_bridge_probe(req: FanxiuCpp2IlLoginLuaBridgeProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_cpp2il_login_lua_bridge_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/cpp2il-gamelogin-serverlist-bridge-probe")
def post_fanxiu_cpp2il_gamelogin_serverlist_bridge_probe(
    req: FanxiuCpp2IlGameLoginServerListBridgeProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_cpp2il_gamelogin_serverlist_bridge_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/cpp2il-fileutil-post-loader-probe")
def post_fanxiu_cpp2il_fileutil_post_loader_probe(
    req: FanxiuCpp2IlFileUtilPostLoaderProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_cpp2il_fileutil_post_loader_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/cpp2il-socket-proto-bridge-probe")
def post_fanxiu_cpp2il_socket_proto_bridge_probe(
    req: FanxiuCpp2IlSocketProtoBridgeProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_cpp2il_socket_proto_bridge_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/cpp2il-socket-receive-dispatch-probe")
def post_fanxiu_cpp2il_socket_receive_dispatch_probe(
    req: FanxiuCpp2IlSocketReceiveDispatchProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_cpp2il_socket_receive_dispatch_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/login-token-to-socket-handoff-probe")
def post_fanxiu_login_token_to_socket_handoff_probe(
    req: FanxiuLoginTokenToSocketHandoffProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_login_token_to_socket_handoff_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/login-account-sign-source-probe")
def post_fanxiu_login_account_sign_source_probe(
    req: FanxiuLoginAccountSignSourceProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_login_account_sign_source_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/lua-serverlist-response-flow-probe")
def post_fanxiu_lua_serverlist_response_flow_probe(
    req: FanxiuLuaServerListResponseFlowProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_serverlist_response_flow_probe,
        export_root=req.export_root,
    )


@router.post("/resources/apk/gamelogin-bridge-probe")
def post_fanxiu_apk_gamelogin_bridge_probe(req: FanxiuApkGameLoginBridgeProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_apk_gamelogin_bridge_probe,
        apk_root=req.apk_root,
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


@router.post("/resources/apk/taptap-download-dat-package-probe")
def post_fanxiu_taptap_download_dat_package_probe(req: FanxiuTapTapDownloadDatPackageProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_taptap_download_dat_package_probe,
        download_path=req.download_path,
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


@router.post("/resources/hot-update/bluestarsea-plan-reward-probe")
def post_fanxiu_hot_update_bluestarsea_plan_reward_probe(
    req: FanxiuHotUpdateBlueStarSeaPlanRewardProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_plan_reward_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-progression-probe")
def post_fanxiu_hot_update_bluestarsea_progression_probe(
    req: FanxiuHotUpdateBlueStarSeaProgressionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_progression_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-star-evolution-probe")
def post_fanxiu_hot_update_bluestarsea_star_evolution_probe(
    req: FanxiuHotUpdateBlueStarSeaStarEvolutionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_star_evolution_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-faze-effect-probe")
def post_fanxiu_hot_update_bluestarsea_faze_effect_probe(
    req: FanxiuHotUpdateBlueStarSeaFazeEffectProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_faze_effect_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-tree-faze-usage-probe")
def post_fanxiu_hot_update_bluestarsea_tree_faze_usage_probe(
    req: FanxiuHotUpdateBlueStarSeaTreeFazeUsageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_tree_faze_usage_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-authority-boundary-probe")
def post_fanxiu_hot_update_bluestarsea_authority_boundary_probe(
    req: FanxiuHotUpdateBlueStarSeaAuthorityBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_authority_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/bluestarsea-protocol-semantics-probe")
def post_fanxiu_hot_update_bluestarsea_protocol_semantics_probe(
    req: FanxiuHotUpdateBlueStarSeaProtocolSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_bluestarsea_protocol_semantics_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-runtime-probe")
def post_fanxiu_hot_update_blld_runtime_probe(req: FanxiuHotUpdateBlldRuntimeProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_runtime_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-authority-boundary-probe")
def post_fanxiu_hot_update_blld_authority_boundary_probe(
    req: FanxiuHotUpdateBlldAuthorityBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_authority_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/blld-protocol-semantics-probe")
def post_fanxiu_hot_update_blld_protocol_semantics_probe(
    req: FanxiuHotUpdateBlldProtocolSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_blld_protocol_semantics_probe,
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


@router.post("/resources/hot-update/faze-authority-boundary-probe")
def post_fanxiu_hot_update_faze_authority_boundary_probe(
    req: FanxiuHotUpdateFazeAuthorityBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_faze_authority_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/faze-protocol-semantics-probe")
def post_fanxiu_hot_update_faze_protocol_semantics_probe(
    req: FanxiuHotUpdateFazeProtocolSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_faze_protocol_semantics_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-protocol-semantics-probe")
def post_fanxiu_hot_update_gongfa_protocol_semantics_probe(
    req: FanxiuHotUpdateGongfaProtocolSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_protocol_semantics_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-upgrade-times-flow-probe")
def post_fanxiu_hot_update_gongfa_upgrade_times_flow_probe(
    req: FanxiuHotUpdateGongfaUpgradeTimesFlowProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_upgrade_times_flow_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-lifecycle-probe")
def post_fanxiu_hot_update_gongfa_homemake_lifecycle_probe(
    req: FanxiuHotUpdateGongfaHomeMakeLifecycleProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_lifecycle_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-learn-teach-probe")
def post_fanxiu_hot_update_gongfa_homemake_learn_teach_probe(
    req: FanxiuHotUpdateGongfaHomeMakeLearnTeachProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_learn_teach_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-record-grid-light-probe")
def post_fanxiu_hot_update_gongfa_homemake_record_grid_light_probe(
    req: FanxiuHotUpdateGongfaHomeMakeRecordGridLightProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_record_grid_light_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-mutation-ops-probe")
def post_fanxiu_hot_update_gongfa_homemake_mutation_ops_probe(
    req: FanxiuHotUpdateGongfaHomeMakeMutationOpsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_mutation_ops_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-page-list-probe")
def post_fanxiu_hot_update_gongfa_homemake_page_list_probe(
    req: FanxiuHotUpdateGongfaHomeMakePageListProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_page_list_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-share-probe")
def post_fanxiu_hot_update_gongfa_homemake_share_probe(
    req: FanxiuHotUpdateGongfaHomeMakeShareProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_share_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-share-ui-probe")
def post_fanxiu_hot_update_gongfa_homemake_share_ui_probe(
    req: FanxiuHotUpdateGongfaHomeMakeShareUiProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_share_ui_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-share-href-probe")
def post_fanxiu_hot_update_gongfa_homemake_share_href_probe(
    req: FanxiuHotUpdateGongfaHomeMakeShareHrefProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_share_href_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-share-href-prefab-probe")
def post_fanxiu_hot_update_gongfa_homemake_share_href_prefab_probe(
    req: FanxiuHotUpdateGongfaHomeMakeShareHrefPrefabProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_share_href_prefab_probe,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-share-href-registration-gap-probe")
def post_fanxiu_hot_update_gongfa_homemake_share_href_registration_gap_probe(
    req: FanxiuHotUpdateGongfaHomeMakeShareHrefRegistrationGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_share_href_registration_gap_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-detail-view-probe")
def post_fanxiu_hot_update_gongfa_homemake_detail_view_probe(
    req: FanxiuHotUpdateGongfaHomeMakeDetailViewProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_detail_view_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-detail-renderer-probe")
def post_fanxiu_hot_update_gongfa_homemake_detail_renderer_probe(
    req: FanxiuHotUpdateGongfaHomeMakeDetailRendererProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_detail_renderer_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-detail-renderer-sample-probe")
def post_fanxiu_hot_update_gongfa_homemake_detail_renderer_sample_probe(
    req: FanxiuHotUpdateGongfaHomeMakeDetailRendererSampleProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_detail_renderer_sample_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-renderer-source-selection-probe")
def post_fanxiu_hot_update_gongfa_homemake_renderer_source_selection_probe(
    req: FanxiuHotUpdateGongfaHomeMakeRendererSourceSelectionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_renderer_source_selection_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-static-renderer-coverage-probe")
def post_fanxiu_hot_update_gongfa_homemake_static_renderer_coverage_probe(
    req: FanxiuHotUpdateGongfaHomeMakeStaticRendererCoverageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_static_renderer_coverage_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-xianshu-static-gap-probe")
def post_fanxiu_hot_update_gongfa_homemake_xianshu_static_gap_probe(
    req: FanxiuHotUpdateGongfaHomeMakeXianShuStaticGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_xianshu_static_gap_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-xianshu-formula-catalog-probe")
def post_fanxiu_hot_update_gongfa_homemake_xianshu_formula_catalog_probe(
    req: FanxiuHotUpdateGongfaHomeMakeXianShuFormulaCatalogProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_xianshu_formula_catalog_probe,
        export_root=req.export_root,
        star=req.star or 1,
    )


@router.post("/resources/hot-update/gongfa-homemake-xianshu-formula-usage-probe")
def post_fanxiu_hot_update_gongfa_homemake_xianshu_formula_usage_probe(
    req: FanxiuHotUpdateGongfaHomeMakeXianShuFormulaUsageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_xianshu_formula_usage_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-xianshu-battle-state-usage-probe")
def post_fanxiu_hot_update_gongfa_homemake_xianshu_battle_state_usage_probe(
    req: FanxiuHotUpdateGongfaHomeMakeXianShuBattleStateUsageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_xianshu_battle_state_usage_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-xianshu-cast-request-boundary-probe")
def post_fanxiu_hot_update_gongfa_homemake_xianshu_cast_request_boundary_probe(
    req: FanxiuHotUpdateGongfaHomeMakeXianShuCastRequestBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_xianshu_cast_request_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-xianshu-cast-ack-consumer-probe")
def post_fanxiu_hot_update_gongfa_homemake_xianshu_cast_ack_consumer_probe(
    req: FanxiuHotUpdateGongfaHomeMakeXianShuCastAckConsumerProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_xianshu_cast_ack_consumer_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-skillcastbridge-boundary-probe")
def post_fanxiu_hot_update_gongfa_homemake_skillcastbridge_boundary_probe(
    req: FanxiuHotUpdateGongfaHomeMakeSkillCastBridgeBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_skillcastbridge_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-stage-star-timeline-boundary-probe")
def post_fanxiu_hot_update_gongfa_homemake_stage_star_timeline_boundary_probe(
    req: FanxiuHotUpdateGongfaHomeMakeStageStarTimelineBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_stage_star_timeline_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-stage-star-timeline-config-probe")
def post_fanxiu_hot_update_gongfa_homemake_stage_star_timeline_config_probe(
    req: FanxiuHotUpdateGongfaHomeMakeStageStarTimelineConfigProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_stage_star_timeline_config_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-timeline-hurt-projection-probe")
def post_fanxiu_hot_update_gongfa_homemake_timeline_hurt_projection_probe(
    req: FanxiuHotUpdateGongfaHomeMakeTimelineHurtProjectionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_timeline_hurt_projection_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/fight-result-family-decoder-probe")
def post_fanxiu_hot_update_fight_result_family_decoder_probe(
    req: FanxiuHotUpdateFightResultFamilyDecoderProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_fight_result_family_decoder_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/buff-change-result-decoder-probe")
def post_fanxiu_hot_update_buff_change_result_decoder_probe(
    req: FanxiuHotUpdateBuffChangeResultDecoderProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_buff_change_result_decoder_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/buff-state-decoder-probe")
def post_fanxiu_hot_update_buff_state_decoder_probe(
    req: FanxiuHotUpdateBuffStateDecoderProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_buff_state_decoder_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/socket-primitive-decoder-probe")
def post_fanxiu_hot_update_socket_primitive_decoder_probe(
    req: FanxiuHotUpdateSocketPrimitiveDecoderProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_socket_primitive_decoder_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/typed-pool-runtime-observation-probe")
def post_fanxiu_hot_update_typed_pool_runtime_observation_probe(
    req: FanxiuHotUpdateTypedPoolRuntimeObservationProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_typed_pool_runtime_observation_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/socket-raw-decoder-probe")
def post_fanxiu_hot_update_socket_raw_decoder_probe(
    req: FanxiuHotUpdateSocketRawDecoderProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_socket_raw_decoder_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/socket-compressed-int-codec-probe")
def post_fanxiu_hot_update_socket_compressed_int_codec_probe(
    req: FanxiuHotUpdateSocketCompressedIntCodecProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_socket_compressed_int_codec_probe,
        export_root=req.export_root,
    )




@router.post("/resources/hot-update/combat-formula-authority-contrast-probe")
def post_fanxiu_hot_update_combat_formula_authority_contrast_probe(
    req: FanxiuHotUpdateCombatFormulaAuthorityContrastProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_combat_formula_authority_contrast_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/cpp2il-main-combat-formula-surface-probe")
def post_fanxiu_hot_update_cpp2il_main_combat_formula_surface_probe(
    req: FanxiuHotUpdateCpp2IlMainCombatFormulaSurfaceProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_cpp2il_main_combat_formula_surface_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-side-feature-semantics-probe")
def post_fanxiu_hot_update_gongfa_homemake_side_feature_semantics_probe(
    req: FanxiuHotUpdateGongfaHomeMakeSideFeatureSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_side_feature_semantics_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-fazelevel-name-match-boundary-probe")
def post_fanxiu_hot_update_gongfa_homemake_fazelevel_name_match_boundary_probe(
    req: FanxiuHotUpdateGongfaHomeMakeFazeLevelNameMatchBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_fazelevel_name_match_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-fazelevel-skill-ownership-probe")
def post_fanxiu_hot_update_gongfa_homemake_fazelevel_skill_ownership_probe(
    req: FanxiuHotUpdateGongfaHomeMakeFazeLevelSkillOwnershipProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_fazelevel_skill_ownership_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-buff-field-semantics-probe")
def post_fanxiu_hot_update_gongfa_homemake_buff_field_semantics_probe(
    req: FanxiuHotUpdateGongfaHomeMakeBuffFieldSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_buff_field_semantics_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-buff-combat-result-probe")
def post_fanxiu_hot_update_gongfa_homemake_buff_combat_result_probe(
    req: FanxiuHotUpdateGongfaHomeMakeBuffCombatResultProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_buff_combat_result_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-buff-result-correlation-probe")
def post_fanxiu_hot_update_gongfa_homemake_buff_result_correlation_probe(
    req: FanxiuHotUpdateGongfaHomeMakeBuffResultCorrelationProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_buff_result_correlation_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-cpp2il-buff-result-symbol-probe")
def post_fanxiu_hot_update_gongfa_homemake_cpp2il_buff_result_symbol_probe(
    req: FanxiuHotUpdateGongfaHomeMakeCpp2IlBuffResultSymbolProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_cpp2il_buff_result_symbol_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-buff-parameter-semantics-probe")
def post_fanxiu_hot_update_gongfa_homemake_buff_parameter_semantics_probe(
    req: FanxiuHotUpdateGongfaHomeMakeBuffParameterSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_buff_parameter_semantics_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-homemake-mechanism-ownership-probe")
def post_fanxiu_hot_update_gongfa_homemake_mechanism_ownership_probe(
    req: FanxiuHotUpdateGongfaHomeMakeMechanismOwnershipProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_mechanism_ownership_probe,
        export_root=req.export_root,
        buff_id=req.buff_id or "386001010",
    )


@router.post("/resources/hot-update/gongfa-homemake-mechanism-formula-surface-probe")
def post_fanxiu_hot_update_gongfa_homemake_mechanism_formula_surface_probe(
    req: FanxiuHotUpdateGongfaHomeMakeMechanismFormulaSurfaceProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_mechanism_formula_surface_probe,
        export_root=req.export_root,
        buff_id=req.buff_id or "386001010",
        star=req.star or 1,
        jie=req.jie or 1,
    )




@router.post("/resources/hot-update/gongfa-homemake-mechanism-result-producer-probe")
def post_fanxiu_hot_update_gongfa_homemake_mechanism_result_producer_probe(
    req: FanxiuHotUpdateGongfaHomeMakeMechanismResultProducerProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_mechanism_result_producer_probe,
        export_root=req.export_root,
        buff_id=req.buff_id or "386001010",
    )


@router.post("/resources/hot-update/gongfa-homemake-nonfunnel-buff-boundary-probe")
def post_fanxiu_hot_update_gongfa_homemake_nonfunnel_buff_boundary_probe(
    req: FanxiuHotUpdateGongfaHomeMakeNonFunnelBuffBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_homemake_nonfunnel_buff_boundary_probe,
        export_root=req.export_root,
        buff_id=req.buff_id or "385002010",
    )


@router.post("/resources/hot-update/gongfa-view-snapshot-probe")
def post_fanxiu_hot_update_gongfa_view_snapshot_probe(
    req: FanxiuHotUpdateGongfaViewSnapshotProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_view_snapshot_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-program-equip-probe")
def post_fanxiu_hot_update_gongfa_program_equip_probe(
    req: FanxiuHotUpdateGongfaProgramEquipProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_program_equip_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/faze-effect-catalog-probe")
def post_fanxiu_hot_update_faze_effect_catalog_probe(
    req: FanxiuHotUpdateFazeEffectCatalogProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_faze_effect_catalog_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/faze-effect-update-event-probe")
def post_fanxiu_hot_update_faze_effect_update_event_probe(
    req: FanxiuHotUpdateFazeEffectUpdateEventProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_faze_effect_update_event_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/faze-effect-lua-usage-probe")
def post_fanxiu_hot_update_faze_effect_lua_usage_probe(
    req: FanxiuHotUpdateFazeEffectLuaUsageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_faze_effect_lua_usage_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-focus-probe")
def post_fanxiu_hot_update_gongfa_special_faze_focus_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeFocusProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_focus_probe,
        gongfa_id=req.gongfa_id,
        query=req.query,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-catalog-probe")
def post_fanxiu_hot_update_gongfa_special_faze_catalog_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeCatalogProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_catalog_probe,
        export_root=req.export_root,
    )


@router.get("/resources/hot-update/gongfa-special-faze-catalog")
def get_fanxiu_hot_update_gongfa_special_faze_catalog(
    query: str = Query(default=""),
    gid: str | None = Query(default=None),
    effect_type: str = Query(default=""),
    reason: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    export_root: str | None = Query(default=None),
) -> dict[str, Any]:
    return _run_resource_operation(
        query_fanxiu_gongfa_special_faze_catalog,
        query=query,
        gid=gid,
        effect_type=effect_type,
        reason=reason,
        limit=limit,
        offset=offset,
        export_root=export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-effect-type-index-probe")
def post_fanxiu_hot_update_gongfa_special_faze_effect_type_index_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeEffectTypeIndexProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_effect_type_index_probe,
        min_stage_count=req.min_stage_count,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-attr-key-index-probe")
def post_fanxiu_hot_update_gongfa_special_faze_attr_key_index_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeAttrKeyIndexProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_attr_key_index_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-param-shape-index-probe")
def post_fanxiu_hot_update_gongfa_special_faze_param_shape_index_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeParamShapeIndexProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_param_shape_index_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-param-item-ref-probe")
def post_fanxiu_hot_update_gongfa_special_faze_param_item_ref_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeParamItemRefProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_param_item_ref_probe,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-payload-summary-probe")
def post_fanxiu_hot_update_gongfa_special_faze_payload_summary_probe(
    req: FanxiuHotUpdateGongfaSpecialFazePayloadSummaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_payload_summary_probe,
        export_root=req.export_root,
    )




@router.post("/resources/hot-update/gongfa-special-faze-reason-probe")
def post_fanxiu_hot_update_gongfa_special_faze_reason_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeReasonProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_reason_probe,
        gongfa_id=req.gongfa_id,
        query=req.query,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-reason-reuse-probe")
def post_fanxiu_hot_update_gongfa_special_faze_reason_reuse_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeReasonReuseProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_reason_reuse_probe,
        reason=req.reason,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/gongfa-special-faze-reason-reuse-index-probe")
def post_fanxiu_hot_update_gongfa_special_faze_reason_reuse_index_probe(
    req: FanxiuHotUpdateGongfaSpecialFazeReasonReuseIndexProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_gongfa_special_faze_reason_reuse_index_probe,
        min_gongfa_count=req.min_gongfa_count,
        export_root=req.export_root,
    )


@router.post("/resources/hot-update/faze-source-semantics-probe")
def post_fanxiu_hot_update_faze_source_semantics_probe(
    req: FanxiuHotUpdateFazeSourceSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_faze_source_semantics_probe,
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


@router.post("/resources/apk/il2cpp-gameplay-symbol-report")
def post_fanxiu_il2cpp_gameplay_symbol_report(req: FanxiuIl2CppGameplaySymbolReportRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_il2cpp_gameplay_symbol_report,
        metadata_path=req.metadata_path,
        apk_root=req.apk_root,
        export_root=req.export_root,
        keywords=req.keywords,
        string_keywords=req.string_keywords,
        row_limit=req.row_limit,
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


@router.post("/resources/doupotd/catalog")
def post_fanxiu_doupotd_catalog(req: FanxiuDoupoTDCatalogRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_catalog,
        tower_defense_config_dir=req.tower_defense_config_dir,
        card_compose_config_dir=req.card_compose_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/catalog")
def post_fanxiu_digitdoor_catalog(req: FanxiuDigitDoorCatalogRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_catalog,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/skill-enhance-effect-usage-probe")
def post_fanxiu_digitdoor_skill_enhance_effect_usage_probe(
    req: FanxiuDigitDoorSkillEnhanceEffectUsageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_skill_enhance_effect_usage_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/skill-enhance-application-probe")
def post_fanxiu_digitdoor_skill_enhance_application_probe(
    req: FanxiuDigitDoorSkillEnhanceApplicationProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_skill_enhance_application_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/skill-enhance-effect-id-namespace-probe")
def post_fanxiu_digitdoor_skill_enhance_effect_id_namespace_probe(
    req: FanxiuDigitDoorSkillEnhanceEffectIdNamespaceProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_skill_enhance_effect_id_namespace_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/readyfight-skilllist-consumer-probe")
def post_fanxiu_digitdoor_readyfight_skilllist_consumer_probe(
    req: FanxiuDigitDoorReadyFightSkillListConsumerProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_readyfight_skilllist_consumer_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/readyfight-skilllist-shape-probe")
def post_fanxiu_digitdoor_readyfight_skilllist_shape_probe(
    req: FanxiuDigitDoorReadyFightSkillListShapeProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_readyfight_skilllist_shape_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/readyfight-cpp2il-consumer-probe")
def post_fanxiu_digitdoor_readyfight_cpp2il_consumer_probe(
    req: FanxiuDigitDoorReadyFightCpp2IlConsumerProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_readyfight_cpp2il_consumer_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )




@router.post("/resources/digitdoor/readyfight-request-levelid-probe")
def post_fanxiu_digitdoor_readyfight_request_levelid_probe(
    req: FanxiuDigitDoorReadyFightRequestLevelIdProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_readyfight_request_levelid_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/readyfight-partnerlist-probe")
def post_fanxiu_digitdoor_readyfight_partnerlist_probe(
    req: FanxiuDigitDoorReadyFightPartnerListProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_readyfight_partnerlist_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/startgame-response-boundary-probe")
def post_fanxiu_digitdoor_startgame_response_boundary_probe(
    req: FanxiuDigitDoorStartGameResponseBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_startgame_response_boundary_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/startgame-skillvos-shape-probe")
def post_fanxiu_digitdoor_startgame_skillvos_shape_probe(
    req: FanxiuDigitDoorStartGameSkillVosShapeProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_startgame_skillvos_shape_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )




@router.post("/resources/digitdoor/startgame-cpp2il-consumer-probe")
def post_fanxiu_digitdoor_startgame_cpp2il_consumer_probe(
    req: FanxiuDigitDoorStartGameCpp2IlConsumerProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_startgame_cpp2il_consumer_probe,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/partner-attribute-formatter-probe")
def post_fanxiu_digitdoor_partner_attribute_formatter_probe(
    req: FanxiuDigitDoorPartnerAttributeFormatterProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_partner_attribute_formatter_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/combat-attribute-consumer-probe")
def post_fanxiu_digitdoor_combat_attribute_consumer_probe(
    req: FanxiuDigitDoorCombatAttributeConsumerProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_combat_attribute_consumer_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/gameplayer-settlement-probe")
def post_fanxiu_digitdoor_gameplayer_settlement_probe(
    req: FanxiuDigitDoorGamePlayerSettlementProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_gameplayer_settlement_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )




@router.post("/resources/digitdoor/gameplayer-cpp2il-consumer-probe")
def post_fanxiu_digitdoor_gameplayer_cpp2il_consumer_probe(
    req: FanxiuDigitDoorGamePlayerCpp2IlConsumerProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_gameplayer_cpp2il_consumer_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/info-snapshot-probe")
def post_fanxiu_digitdoor_info_snapshot_probe(
    req: FanxiuDigitDoorInfoSnapshotProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_info_snapshot_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/uplevel-state-probe")
def post_fanxiu_digitdoor_uplevel_state_probe(
    req: FanxiuDigitDoorUpLevelStateProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_uplevel_state_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/unlock-state-probe")
def post_fanxiu_digitdoor_unlock_state_probe(
    req: FanxiuDigitDoorUnlockStateProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_unlock_state_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/skip-level-probe")
def post_fanxiu_digitdoor_skip_level_probe(
    req: FanxiuDigitDoorSkipLevelProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_skip_level_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/activity-end-probe")
def post_fanxiu_digitdoor_activity_end_probe(
    req: FanxiuDigitDoorActivityEndProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_activity_end_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/report-gmbattle-probe")
def post_fanxiu_digitdoor_report_gmbattle_probe(
    req: FanxiuDigitDoorReportGMBattleProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_report_gmbattle_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/pvp-balance-probe")
def post_fanxiu_digitdoor_pvp_balance_probe(
    req: FanxiuDigitDoorPvpBalanceProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_pvp_balance_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/pvp-report-attr-snapshot-probe")
def post_fanxiu_digitdoor_pvp_report_attr_snapshot_probe(
    req: FanxiuDigitDoorPvpReportAttrSnapshotProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_pvp_report_attr_snapshot_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/pvp-report-acceptance-gap-probe")
def post_fanxiu_digitdoor_pvp_report_acceptance_gap_probe(
    req: FanxiuDigitDoorPvpReportAcceptanceGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_pvp_report_acceptance_gap_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/pvp-report-list-lifecycle-probe")
def post_fanxiu_digitdoor_pvp_report_list_lifecycle_probe(
    req: FanxiuDigitDoorPvpReportListLifecycleProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_pvp_report_list_lifecycle_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/pvp-winreduce-gap-probe")
def post_fanxiu_digitdoor_pvp_winreduce_gap_probe(
    req: FanxiuDigitDoorPvpWinreduceGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_pvp_winreduce_gap_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/pvp-winner-projection-probe")
def post_fanxiu_digitdoor_pvp_winner_projection_probe(
    req: FanxiuDigitDoorPvpWinnerProjectionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_pvp_winner_projection_probe,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/pvp-report-family-reuse-probe")
def post_fanxiu_pvp_report_family_reuse_probe(
    req: FanxiuPvpReportFamilyReuseProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_pvp_report_family_reuse_probe,
        export_root=req.export_root,
    )




@router.post("/resources/digitdoor/buff-effect-usage-probe")
def post_fanxiu_digitdoor_buff_effect_usage_probe(
    req: FanxiuDigitDoorBuffEffectUsageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_buff_effect_usage_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/buff-class-formula-probe")
def post_fanxiu_digitdoor_buff_class_formula_probe(
    req: FanxiuDigitDoorBuffClassFormulaProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_buff_class_formula_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/reward-result-resolution-probe")
def post_fanxiu_digitdoor_reward_result_resolution_probe(
    req: FanxiuDigitDoorRewardResultResolutionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_reward_result_resolution_probe,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/reward-marker-semantics-probe")
def post_fanxiu_digitdoor_reward_marker_semantics_probe(
    req: FanxiuDigitDoorRewardMarkerSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_reward_marker_semantics_probe,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/reward-marker-ui-probe")
def post_fanxiu_digitdoor_reward_marker_ui_probe(
    req: FanxiuDigitDoorRewardMarkerUiProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_reward_marker_ui_probe,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-refresh-probe")
def post_fanxiu_digitdoor_monster_refresh_probe(
    req: FanxiuDigitDoorMonsterRefreshProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_refresh_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/door-refresh-projection-probe")
def post_fanxiu_digitdoor_door_refresh_projection_probe(
    req: FanxiuDigitDoorDoorRefreshProjectionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_door_refresh_projection_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/door-gain-buff-flow-probe")
def post_fanxiu_digitdoor_door_gain_buff_flow_probe(
    req: FanxiuDigitDoorDoorGainBuffFlowProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_door_gain_buff_flow_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/door-customized-type-semantics-probe")
def post_fanxiu_digitdoor_door_customized_type_semantics_probe(
    req: FanxiuDigitDoorDoorCustomizedTypeSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_door_customized_type_semantics_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-skill-timeline-probe")
def post_fanxiu_digitdoor_monster_skill_timeline_probe(
    req: FanxiuDigitDoorMonsterSkillTimelineProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_skill_timeline_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-effect-class-flow-probe")
def post_fanxiu_digitdoor_monster_effect_class_flow_probe(
    req: FanxiuDigitDoorMonsterEffectClassFlowProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_effect_class_flow_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
        effect_classes=req.effect_classes,
    )


@router.post("/resources/digitdoor/monster-refresh-point-value-projection-probe")
def post_fanxiu_digitdoor_monster_refresh_point_value_projection_probe(
    req: FanxiuDigitDoorMonsterRefreshPointValueProjectionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_refresh_point_value_projection_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-refresh-point-attribute-projection-probe")
def post_fanxiu_digitdoor_monster_refresh_point_attribute_projection_probe(
    req: FanxiuDigitDoorMonsterRefreshPointAttributeProjectionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_refresh_point_attribute_projection_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-refresh-point-latent-field-probe")
def post_fanxiu_digitdoor_monster_refresh_point_latent_field_probe(
    req: FanxiuDigitDoorMonsterRefreshPointLatentFieldProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_refresh_point_latent_field_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-skill-data-accessor-probe")
def post_fanxiu_digitdoor_monster_skill_data_accessor_probe(
    req: FanxiuDigitDoorMonsterSkillDataAccessorProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_skill_data_accessor_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-skill-value-projection-probe")
def post_fanxiu_digitdoor_monster_skill_value_projection_probe(
    req: FanxiuDigitDoorMonsterSkillValueProjectionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_skill_value_projection_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-skill-buff-link-probe")
def post_fanxiu_digitdoor_monster_skill_buff_link_probe(
    req: FanxiuDigitDoorMonsterSkillBuffLinkProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_skill_buff_link_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/digitdoor/monster-skill-buff-formula-probe")
def post_fanxiu_digitdoor_monster_skill_buff_formula_probe(
    req: FanxiuDigitDoorMonsterSkillBuffFormulaProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_digitdoor_monster_skill_buff_formula_probe,
        digitdoor_config_dir=req.digitdoor_config_dir,
        digitdoor_logic_dir=req.digitdoor_logic_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/skill-timeline-probe")
def post_fanxiu_doupotd_skill_timeline_probe(req: FanxiuDoupoTDSkillTimelineProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_skill_timeline_probe,
        tower_defense_config_dir=req.tower_defense_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/buff-effect-probe")
def post_fanxiu_doupotd_buff_effect_probe(req: FanxiuDoupoTDBuffEffectProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_buff_effect_probe,
        tower_defense_config_dir=req.tower_defense_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/buff-class-semantics-probe")
def post_fanxiu_doupotd_buff_class_semantics_probe(
    req: FanxiuDoupoTDBuffClassSemanticsProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_buff_class_semantics_probe,
        tower_defense_config_dir=req.tower_defense_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/buff-class-flow-probe")
def post_fanxiu_doupotd_buff_class_flow_probe(
    req: FanxiuDoupoTDBuffClassFlowProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_buff_class_flow_probe,
        tower_defense_config_dir=req.tower_defense_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
        buff_classes=req.buff_classes,
    )


@router.post("/resources/doupotd/buff-authority-boundary-probe")
def post_fanxiu_doupotd_buff_authority_boundary_probe(
    req: FanxiuDoupoTDBuffAuthorityBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_buff_authority_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/effect-gameplayer-summary-probe")
def post_fanxiu_doupotd_effect_gameplayer_summary_probe(
    req: FanxiuDoupoTDEffectGamePlayerSummaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_effect_gameplayer_summary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/gameplayer-result-probe")
def post_fanxiu_doupotd_gameplayer_result_probe(
    req: FanxiuDoupoTDGamePlayerResultProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_gameplayer_result_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-gap-probe")
def post_fanxiu_doupotd_pvp_report_gap_probe(
    req: FanxiuDoupoTDPvpReportGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_gap_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-global-lua-surface-probe")
def post_fanxiu_doupotd_pvp_report_global_lua_surface_probe(
    req: FanxiuDoupoTDPvpReportGlobalLuaSurfaceProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_global_lua_surface_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-scene-payload-probe")
def post_fanxiu_doupotd_pvp_report_scene_payload_probe(
    req: FanxiuDoupoTDPvpReportScenePayloadProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_scene_payload_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-native-symbol-gap-probe")
def post_fanxiu_doupotd_pvp_report_native_symbol_gap_probe(
    req: FanxiuDoupoTDPvpReportNativeSymbolGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_native_symbol_gap_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-native-lua-bridge-boundary-probe")
def post_fanxiu_doupotd_pvp_report_native_lua_bridge_boundary_probe(
    req: FanxiuDoupoTDPvpReportNativeLuaBridgeBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_native_lua_bridge_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-netlogic-family-probe")
def post_fanxiu_doupotd_pvp_report_netlogic_family_probe(
    req: FanxiuDoupoTDPvpReportNetLogicFamilyProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_netlogic_family_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-lua-binding-boundary-probe")
def post_fanxiu_doupotd_pvp_report_lua_binding_boundary_probe(
    req: FanxiuDoupoTDPvpReportLuaBindingBoundaryProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_lua_binding_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-raw-export-coverage-probe")
def post_fanxiu_doupotd_pvp_report_raw_export_coverage_probe(
    req: FanxiuDoupoTDPvpReportRawExportCoverageProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_raw_export_coverage_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-shape-alias-probe")
def post_fanxiu_doupotd_pvp_report_shape_alias_probe(
    req: FanxiuDoupoTDPvpReportShapeAliasProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_shape_alias_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-sender-alias-gap-probe")
def post_fanxiu_doupotd_pvp_report_sender_alias_gap_probe(
    req: FanxiuDoupoTDPvpReportSenderAliasGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_sender_alias_gap_probe,
        export_root=req.export_root,
    )










































































@router.post("/resources/doupotd/pvp-report-trigger-lifecycle-probe")
def post_fanxiu_doupotd_pvp_report_trigger_lifecycle_probe(
    req: FanxiuDoupoTDPvpReportTriggerLifecycleProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_trigger_lifecycle_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-trigger-base-dynamic-gap-probe")
def post_fanxiu_doupotd_pvp_report_trigger_base_dynamic_gap_probe(
    req: FanxiuDoupoTDPvpReportTriggerBaseDynamicGapProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_trigger_base_dynamic_gap_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/pvp-report-trigger-delta-probe")
def post_fanxiu_doupotd_pvp_report_trigger_delta_probe(
    req: FanxiuDoupoTDPvpReportTriggerDeltaProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_pvp_report_trigger_delta_probe,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/reward-config-probe")
def post_fanxiu_doupotd_reward_config_probe(
    req: FanxiuDoupoTDRewardConfigProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_reward_config_probe,
        tower_defense_config_dir=req.tower_defense_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/monster-drop-resolution-probe")
def post_fanxiu_doupotd_monster_drop_resolution_probe(
    req: FanxiuDoupoTDMonsterDropResolutionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_monster_drop_resolution_probe,
        tower_defense_config_dir=req.tower_defense_config_dir,
        drop_config_dir=req.drop_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/store-bag-visual-probe")
def post_fanxiu_doupotd_store_bag_visual_probe(
    req: FanxiuDoupoTDStoreBagVisualProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_store_bag_visual_probe,
        tower_defense_config_dir=req.tower_defense_config_dir,
        drop_config_dir=req.drop_config_dir,
        lang_path=req.lang_path,
        export_root=req.export_root,
    )


@router.post("/resources/doupotd/reward-result-resolution-probe")
def post_fanxiu_doupotd_reward_result_resolution_probe(
    req: FanxiuDoupoTDRewardResultResolutionProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_doupotd_reward_result_resolution_probe,
        lang_path=req.lang_path,
        export_root=req.export_root,
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




@router.post("/resources/lua/login-socket-send-flow-probe")
def post_fanxiu_lua_login_socket_send_flow_probe(req: FanxiuLuaLoginSocketSendFlowProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_socket_send_flow_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-socket-response-flow-probe")
def post_fanxiu_lua_login_socket_response_flow_probe(req: FanxiuLuaLoginSocketResponseFlowProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_socket_response_flow_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-finish-post-sync-probe")
def post_fanxiu_lua_login_finish_post_sync_probe(req: FanxiuLuaLoginFinishPostSyncProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_finish_post_sync_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/raw-lscript-export-coverage-probe")
def post_fanxiu_lua_raw_lscript_export_coverage_probe(req: FanxiuLuaRawLscriptExportCoverageProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_raw_lscript_export_coverage_probe,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/lua/raw-lscript-missing-export-probe")
def post_fanxiu_lua_raw_lscript_missing_export_probe(req: FanxiuLuaRawLscriptMissingExportProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_raw_lscript_missing_export_probe,
        resource_root=req.resource_root,
        export_root=req.export_root,
        status=req.status,
        group_prefix=req.group_prefix,
        module_contains=req.module_contains,
        limit=req.limit,
        order_by=req.order_by,
        dry_run=req.dry_run,
        refresh_coverage=req.refresh_coverage,
    )


@router.post("/resources/lua/lscript-surface-inventory-probe")
def post_fanxiu_lua_lscript_surface_inventory_probe(req: FanxiuLuaLscriptSurfaceInventoryProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_lscript_surface_inventory_probe,
        export_root=req.export_root,
        max_asset_rows=req.max_asset_rows,
    )


@router.post("/resources/lua/lscript-module-surface-probe")
def post_fanxiu_lua_lscript_module_surface_probe(req: FanxiuLuaLscriptModuleSurfaceProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_lscript_module_surface_probe,
        export_root=req.export_root,
        module=req.module,
        group=req.group,
        max_files=req.max_files,
        max_marker_rows=req.max_marker_rows,
    )


@router.post("/resources/lua/lscript-module-netlogic-flow-probe")
def post_fanxiu_lua_lscript_module_netlogic_flow_probe(req: FanxiuLuaLscriptModuleNetLogicFlowProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_lscript_module_netlogic_flow_probe,
        export_root=req.export_root,
        module=req.module,
        group=req.group,
        max_functions=req.max_functions,
    )


@router.post("/resources/lua/lscript-module-protocol-schema-probe")
def post_fanxiu_lua_lscript_module_protocol_schema_probe(req: FanxiuLuaLscriptModuleProtocolSchemaProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_lscript_module_protocol_schema_probe,
        export_root=req.export_root,
        module=req.module,
        group=req.group,
    )




@router.post("/resources/lua/login-post-sync-cpp2il-manager-surface-probe")
def post_fanxiu_lua_login_post_sync_cpp2il_manager_surface_probe(req: FanxiuLuaLoginPostSyncCpp2IlManagerSurfaceProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_cpp2il_manager_surface_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-post-sync-handler-probe")
def post_fanxiu_lua_login_post_sync_handler_probe(req: FanxiuLuaLoginPostSyncHandlerProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_handler_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-post-sync-lua-loader-boundary-probe")
def post_fanxiu_lua_login_post_sync_lua_loader_boundary_probe(req: FanxiuLuaLoginPostSyncLuaLoaderBoundaryProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_lua_loader_boundary_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-post-sync-manager-source-gap-probe")
def post_fanxiu_lua_login_post_sync_manager_source_gap_probe(req: FanxiuLuaLoginPostSyncManagerSourceGapProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_manager_source_gap_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-post-sync-protocol-family-probe")
def post_fanxiu_lua_login_post_sync_protocol_family_probe(req: FanxiuLuaLoginPostSyncProtocolFamilyProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_protocol_family_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-post-sync-raw-lscript-bundle-gap-probe")
def post_fanxiu_lua_login_post_sync_raw_lscript_bundle_gap_probe(req: FanxiuLuaLoginPostSyncRawLscriptBundleGapProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_raw_lscript_bundle_gap_probe,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-post-sync-raw-lscript-handler-closure-probe")
def post_fanxiu_lua_login_post_sync_raw_lscript_handler_closure_probe(
    req: FanxiuLuaLoginPostSyncRawLscriptHandlerClosureProbeRequest,
) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_raw_lscript_handler_closure_probe,
        resource_root=req.resource_root,
        export_root=req.export_root,
    )


@router.post("/resources/lua/login-post-sync-unresolved-handler-gap-probe")
def post_fanxiu_lua_login_post_sync_unresolved_handler_gap_probe(req: FanxiuLuaLoginPostSyncUnresolvedHandlerGapProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_login_post_sync_unresolved_handler_gap_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/sm-login-nested-vo-probe")
def post_fanxiu_lua_sm_login_nested_vo_probe(req: FanxiuLuaSmLoginNestedVoProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_sm_login_nested_vo_probe,
        export_root=req.export_root,
    )


@router.post("/resources/lua/sm-login-nested-vo-depth2-probe")
def post_fanxiu_lua_sm_login_nested_vo_depth2_probe(req: FanxiuLuaSmLoginNestedVoDepth2ProbeRequest) -> dict[str, Any]:
    return _run_resource_operation(
        build_fanxiu_lua_sm_login_nested_vo_depth2_probe,
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
