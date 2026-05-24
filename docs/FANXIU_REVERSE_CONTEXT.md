# Fanxiu Reverse Context

> Last Updated: 2026-05-24
> Audience: future Codex/agent sessions working on 凡人修仙传手游 static/resource/protocol analysis in this CodeYun workspace.

This document is the handoff map for the current Fanxiu reverse-analysis work. Keep it high-density and practical: paths, tools, proven chains, current boundaries, and where to update things next.

## Scope

Current work is focused on static analysis, resource indexing, local catalog/wiki generation, and protocol/authority-boundary understanding. Do not treat this document as an instruction to modify APKs, inject code, bypass auth, or automate unfair gameplay.

When continuing this line of work:

- Put generated analysis outputs under the existing export root.
- Prefer adding reusable backend logic in CodeYun over one-off scripts.
- Keep reports reproducible and summarize major new findings in the runtime notes file.
- Do not copy account tokens, live credentials, or private identifiers into docs.

## Local Paths

Primary local artifacts:

| Purpose | Path |
| --- | --- |
| APK unpacked root | `D:\TapTap\Support\android_emulator\games\308550\apk\1023295_unpacked` |
| IL2CPP binary | `D:\TapTap\Support\android_emulator\games\308550\apk\1023295_unpacked\lib\arm64-v8a\libil2cpp.so` |
| IL2CPP metadata | `D:\TapTap\Support\android_emulator\games\308550\apk\1023295_unpacked\assets\bin\Data\Managed\Metadata\global-metadata.dat` |
| Downloaded game resource root | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_game_files` |
| Unified analysis/export root | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports` |
| Long-running reverse notes | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\parsed_configs\gongfa_catalog\gongfa_runtime_notes.md` |

Keep new generated files inside `frxx_analysis_exports` unless the user explicitly asks otherwise.

## Installed Tools

Cpp2IL is already downloaded and verified locally:

| Tool | Path |
| --- | --- |
| Cpp2IL exe | `D:\tools\Cpp2IL\2022.1.0-pre-release.21\Cpp2IL-2022.1.0-pre-release.21-Windows.exe` |
| Stripped CodeReg plugin | `D:\tools\Cpp2IL\2022.1.0-pre-release.21\Plugins\Cpp2IL.Plugin.StrippedCodeRegSupport.dll` |

Unity version used for the current APK/resources: `2019.4.41f2`.

Cpp2IL outputs already generated:

| Output | Path |
| --- | --- |
| Diffable C# | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_2022_1_pre21_arm64_diffable_cs` |
| ISIL dump | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_2022_1_pre21_arm64_isil` |
| Cpp2IL login receiver report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_login_receiver_report.md` |
| Cpp2IL login-to-Lua bridge report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_login_lua_bridge_report.md` |
| Cpp2IL GameLogin server-list bridge report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_gamelogin_serverlist_bridge_report.md` |
| Cpp2IL FileUtil post loader report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_fileutil_post_loader_report.md` |
| Cpp2IL socket/proto bridge report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_socket_proto_bridge_report.md` |
| Cpp2IL socket receive/dispatch report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\cpp2il_socket_receive_dispatch_report.md` |
| Lua server-list response flow report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\lua_serverlist_response_flow_report.md` |
| Lua login socket send flow report | `D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\apk_static_index\lua_login_socket_send_flow_report.md` |

Cpp2IL found metadata version `24.5`, ELF AArch64, CodeRegistration `0x2F1B810`, MetadataRegistration `0x2F1C210`, and mapped 66734 method pointers.

## CodeYun Implementation Map

Backend routes are mounted through `backend/api/fanxiu_resources.py`. Most reverse-analysis endpoints are `POST /api/fanxiu/resources/...` wrappers around backend core builders.

Important backend modules:

| File | Responsibility |
| --- | --- |
| `backend/core/fanxiu_resources.py` | Resource roots, Unity bundle inspection/export, Wwise bank probing. |
| `backend/core/fanxiu_apk_static.py` | APK/Dex/Manifest/network/login/static reports. This is the main APK-side static-analysis module. |
| `backend/core/fanxiu_il2cpp_metadata.py` | Metadata-only IL2CPP index/probes before method bodies are recovered. |
| `backend/core/fanxiu_download_bridge.py` | Lua download bridge and IL2CPP download inventory reports. |
| `backend/core/fanxiu_hot_update.py` | Hot-update Lua/config probes for BLLD, BlueStarSea, Faze, authority boundaries. |
| `backend/core/fanxiu_game_luaconfig.py` | Parsed game config/catalog work, especially Gongfa/Lingjie feature catalogs. |
| `backend/core/fanxiu_gongfa_catalog.py` | Structured Gongfa/wiki card catalog and rich text sections. |
| `backend/core/fanxiu_item_catalog.py` | Item card/search catalog. |
| `backend/core/fanxiu_lua_logic_index.py` | Lua logic/runtime indexing reports. |
| `backend/core/fanxiu_lua_packet_index.py` | Lua protocol packet indexing. |
| `backend/core/fanxiu_protocol_semantics.py` | Shared loader for feature protocol semantics TSV/edge TSV data; backs the CodeYun protocol semantics page. |

Frontend surfaces:

| File/Route | Role |
| --- | --- |
| `frontend/src/standard/fanxiu/wiki/page.vue` | Main Fanxiu wiki/catalog UI. Current priority is readable item/Gongfa text, rich-text style rendering, grouped duplicate entries. |
| `frontend/src/standard/fanxiu/packet-capture/page.vue` | Local packet-capture helper UI. |
| `frontend/src/standard/fanxiu/protocol-semantics/page.vue` (`/fanxiu/protocol-semantics`) | Protocol semantics inspector for BlueStarSea/BLLD/Faze/Gongfa packet rows and semantic edges. Supports URL initialization with `?feature=gongfa&q=GongFaUpgrade`. |
| `frontend/src/standard/fanxiu/index.ts` | Fanxiu page route registration. Remember side-menu visibility is controlled separately by `frontend/src/layout/MainLayout.vue` and `frontend/src/features/access/permissionRegistry.json`. |

Main regression test:

```powershell
uv run pytest backend/tests/test_fanxiu_resources.py -q
```

Last known backend result after adding the Gongfa protocol semantics, `GongFaUpgradeTimes`, `GongFaHomeMake` lifecycle, `GongFaView` snapshot, `GongFaSaveProgram/XinFaPutUp`, `GongFaHomeMakeLearn/Teach`, `GongFaHomeMakeRecord/Grid/LightUp`, `GongFaHomeMake` mutation-ops, `GongFaHomeMakePageList/HMFilterVO`, `GongFaHomeMakeShareVO/chat share`, `GongFaHomeMake share UI`, `GongFaHomeMake share href`, `GongFaHomeMake share href prefab`, `GongFaHomeMake share href registration gap`, `GongFaHomeMake detail view render`, `GongFaHomeMake detail renderer/templates`, `GongFaHomeMake detail renderer sample`, `GongFaHomeMake renderer source selection`, static `GongFaHomeMake` renderer API work, CodeYun wiki static renderer UI integration, static renderer coverage audit, XianShu static gap probe/main-skill fallback work, side-feature semantics tracing, buff field semantics tracing, buff combat-result ownership tracing, buff result correlation tracing, Cpp2IL buff-result symbol tracing, BuffResource parameter semantics tracing, wiki-facing `仙书机制` grouping, all-XianShu mechanism overview UI, the `386001010 洞微剑气` single-mechanism ownership drill-down, XianShu formula display-surface recovery/catalog, XianShu formula usage/authority-boundary tracing, XianShu battle-side state-consumer tracing, XianShu cast-request boundary tracing, XianShu cast-ack consumer tracing, SkillCastBridge geometry/target-selection boundary tracing, Stage/Star timeline boundary tracing, Stage/Star timeline config resolution, timeline hurt/display projection tracing, Fight Result packet-family decoder mapping, socket primitive typed-pool decoder mapping, typed-pool runtime observation planning, raw socket decoder outline mapping, compressed-int candidate codec derivation, capture fixture codec calibration, combat formula authority contrast, Cpp2IL main-combat formula-surface scan, the FUNNEL result packet field drill-down, the FUNNEL result producer/write-surface drill-down, and wiki-facing `仙书公式` catalog integration: `uv run pytest backend/tests/test_fanxiu_resources.py -q` is `105 passed`. Current Fanxiu resource route count is `136`.

Frontend verification after adding the static `GongFaHomeMake` renderer panel, wiki-facing `仙书机制` grouping, and all-XianShu mechanism overview to `/fanxiu/wiki`:

```powershell
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

Latest UI smoke screenshots:

```text
D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\ui_checks\wiki_homemake_buff_semantics.png
D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\ui_checks\wiki_homemake_buff_semantics_filter.png
D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\ui_checks\wiki_homemake_buff_relation_jump_synced.png
D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\ui_checks\wiki_homemake_buff_overview_filter.png
D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\ui_checks\wiki_homemake_buff_overview_jump.png
D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\ui_checks\wiki_homemake_xianshu_formula_panel.png
```

## Output Directory Map

Under `frxx_analysis_exports`:

| Directory | Meaning |
| --- | --- |
| `apk_static_index` | APK, manifest, network, login, IL2CPP, Cpp2IL, hot-update report markdown/TSV/JSON outputs. |
| `parsed_configs` | Extracted Lua/config tables and derived catalogs. |
| `parsed_configs/gongfa_catalog` | Gongfa object catalog, runtime notes, rich-text section outputs. |
| `parsed_configs/item_catalog` | Item text/card catalog. |
| `parsed_configs/lingjie_feature_catalog` | Lingjie/Gongfa feature relationship and runtime reports. |
| `parsed_configs/lua_packet_index` | Lua packet/protocol catalog: packet ids, read/write field order, MessagePool registrations, handler mapping. |
| `icons` | Exported/curated sprite/icon assets used by the wiki UI. |
| `previews`, `by_source`, `indexes` | Resource previews and source indexes from earlier Unity/resource exploration. |

Private/runtime state probe artifacts:

| Artifact | Path / Meaning |
| --- | --- |
| Private state snapshot | `private_state_probe/` mirrors `/data/data/com.frxxcrjpwssc3.ggws` via read-only ADB/root observation. Treat raw files as local-only sensitive material. |
| Redacted private state summaries | `private_state_probe/analysis/*.redacted.json` plus `analysis/README.md`; use these for agent-facing context instead of raw SharedPreferences/cache values. |
| Live value probe | `private_state_probe_live_115709/analysis/probe_115709_findings.md`; target value `115709` was not found in private files, ordinary writable memory, or the short TCP sample using common encodings. |
| Short TCP live sample | `private_state_probe_live_115709/analysis/tcp_probe_115709.summary.json`; only `857` bytes / estimated `10` packets, no useful FightResult-family evidence. |

Private-state conclusion: local private files mostly expose SDK, push, analytics, Bugly, downloader, Unity playerprefs, and Volley cache state. They do not expose complete backpack/Gongfa/task progress. If ownership or live numeric state is needed later, prefer a carefully scoped runtime packet/log/UI observation plan with redaction, not raw private-state dumping.

Most important human-readable report rollups:

- `apk_static_index/apk_download_config_report.md`
- `apk_static_index/resource_manifest_diff_report.md`
- `apk_static_index/hot_update_lscripts_report.md`
- `apk_static_index/apk_network_stack_report.md`
- `apk_static_index/lua_socket_connect_flow_report.md`
- `apk_static_index/apk_login_server_flow_report.md`
- `apk_static_index/socket_login_packet_schema.md`
- `apk_static_index/apk_dex_login_body_report.md`
- `apk_static_index/apk_unity_login_receiver_report.md`
- `apk_static_index/apk_phonehelper_login_context_report.md`
- `apk_static_index/apk_il2cpp_binary_boundary_report.md`
- `apk_static_index/cpp2il_login_receiver_report.md`
- `apk_static_index/cpp2il_login_lua_bridge_report.md`
- `apk_static_index/cpp2il_gamelogin_serverlist_bridge_report.md`
- `apk_static_index/cpp2il_fileutil_post_loader_report.md`
- `apk_static_index/cpp2il_socket_proto_bridge_report.md`
- `apk_static_index/cpp2il_socket_receive_dispatch_report.md`
- `apk_static_index/lua_serverlist_response_flow_report.md`
- `apk_static_index/lua_login_socket_send_flow_report.md`
- `apk_static_index/hot_update_bluestarsea_protocol_semantics_report.md`
- `apk_static_index/hot_update_blld_protocol_semantics_report.md`
- `apk_static_index/hot_update_faze_protocol_semantics_report.md`
- `apk_static_index/hot_update_gongfa_protocol_semantics_report.md`
- `apk_static_index/hot_update_gongfa_upgrade_times_flow_report.md`
- `apk_static_index/hot_update_gongfa_homemake_lifecycle_report.md`
- `apk_static_index/hot_update_gongfa_homemake_learn_teach_report.md`
- `apk_static_index/hot_update_gongfa_homemake_record_grid_light_report.md`
- `apk_static_index/hot_update_gongfa_homemake_mutation_ops_report.md`
- `apk_static_index/hot_update_gongfa_homemake_page_list_report.md`
- `apk_static_index/hot_update_gongfa_homemake_share_report.md`
- `apk_static_index/hot_update_gongfa_homemake_share_ui_report.md`
- `apk_static_index/hot_update_gongfa_homemake_share_href_report.md`
- `apk_static_index/hot_update_gongfa_homemake_share_href_prefab_report.md`
- `apk_static_index/hot_update_gongfa_homemake_share_href_registration_gap_report.md`
- `apk_static_index/hot_update_gongfa_homemake_detail_view_report.md`
- `apk_static_index/hot_update_gongfa_homemake_detail_renderer_report.md`
- `apk_static_index/hot_update_gongfa_homemake_detail_renderer_sample_report.md`
- `apk_static_index/hot_update_gongfa_homemake_renderer_source_selection_report.md`
- `apk_static_index/hot_update_gongfa_homemake_static_renderer_coverage_report.md`
- `apk_static_index/hot_update_gongfa_homemake_xianshu_static_gap_report.md`
- `apk_static_index/hot_update_gongfa_homemake_side_feature_semantics_report.md`
- `apk_static_index/hot_update_gongfa_homemake_buff_field_semantics_report.md`
- `apk_static_index/hot_update_gongfa_homemake_buff_combat_result_report.md`
- `apk_static_index/hot_update_gongfa_homemake_buff_result_correlation_report.md`
- `apk_static_index/hot_update_gongfa_homemake_cpp2il_buff_result_symbol_report.md`
- `apk_static_index/hot_update_gongfa_homemake_buff_parameter_semantics_report.md`
- `apk_static_index/hot_update_gongfa_homemake_mechanism_ownership_report.md`
- `apk_static_index/hot_update_gongfa_homemake_mechanism_formula_surface_report.md`
- `apk_static_index/hot_update_gongfa_homemake_xianshu_formula_catalog_report.md`
- `apk_static_index/hot_update_gongfa_homemake_xianshu_formula_usage_report.md`
- `apk_static_index/hot_update_gongfa_homemake_xianshu_battle_state_usage_report.md`
- `apk_static_index/hot_update_gongfa_homemake_xianshu_cast_request_boundary_report.md`
- `apk_static_index/hot_update_gongfa_homemake_xianshu_cast_ack_consumer_report.md`
- `apk_static_index/hot_update_gongfa_homemake_skillcastbridge_boundary_report.md`
- `apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_report.md`
- `apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_config_report.md`
- `apk_static_index/hot_update_gongfa_homemake_timeline_hurt_projection_report.md`
- `apk_static_index/hot_update_fight_result_family_decoder_report.md`
- `apk_static_index/hot_update_socket_primitive_decoder_report.md`
- `apk_static_index/hot_update_typed_pool_runtime_observation_report.md`
- `apk_static_index/hot_update_socket_raw_decoder_report.md`
- `apk_static_index/hot_update_socket_compressed_int_codec_report.md`
- `apk_static_index/hot_update_socket_capture_fixture_codec_calibration_report.md`
- `apk_static_index/hot_update_combat_formula_authority_contrast_report.md`
- `apk_static_index/hot_update_cpp2il_main_combat_formula_surface_report.md`
- `apk_static_index/hot_update_gongfa_homemake_mechanism_result_packet_report.md`
- `apk_static_index/hot_update_gongfa_homemake_mechanism_result_producer_report.md`
- `apk_static_index/hot_update_gongfa_view_snapshot_report.md`
- `apk_static_index/hot_update_gongfa_program_equip_report.md`

## Current Mental Model

High-level architecture discovered so far:

1. TapTap/MuMu/Android side launches a Unity IL2CPP game wrapped by Java SDK/channel code.
2. Initial config and resources are HTTP/HTTPS downloads from `akbing.com` hosts.
3. The actual game business protocol is not mostly browser-like GET/POST. After server selection, it uses binary socket packets through Lua/Unity bridge code.
4. Hot-update Lua/resources carry most gameplay-specific logic and table data. APK metadata mostly exposes bridge types and method names.
5. IL2CPP method bodies require pairing `global-metadata.dat` with `libil2cpp.so`; metadata alone is not enough.
6. Cpp2IL is now usable locally and can recover enough ISIL to follow selected method bodies.
7. The server-list HTTP response-to-socket target transition is now closed on the Lua side.
8. The first socket login packet send path is now closed on the Lua side through `SocketBridge.F_Send`.
9. The IL2CPP send bridge after `SocketBridge.F_Send` is now mapped through `SocketManager.F_Send`, `ByteSocket.F_Send`, `LusuoStreamQuick`, and `Socket.BeginSend`.
10. Self-created Gongfa chat-share href resolution is closed at the generic TextEx/Lua callback and HyperLinkMgr consumer levels, but the chat-label static click entry is not closed. PrefabBinder confirms `labelContent` is a TextEx prefab variable and does not serialize a callback; the follow-up registration-gap probe also rules out `LuaUIText:SetText`, `ChatContentBase.DoUpdateContent`, visible `ChatLabelChatCell` Lua, and direct exported Lua `TextExBridge.AddHyperClickListener` bypasses. Current conservative interpretation: chat `<href>` markup can be display/copy/measurement markup unless runtime observation proves the message body is actually clickable.
11. Self-created Gongfa detail rendering is now closed in readable Lua. `OpenCreateSkillDetailView` normalizes direct `GongFaHomeMakeVO` vs external `GongFaHomeMakeShareVO.homeMakeVO`, branches to `CreateSkillDetailView` or `XianShuCreateSkillDetailView` by `skillCommonVO.xianEffectMap`, then renders card header fields plus `GetMainDes`, `effectMap`, and optional `xianEffectMap` rows.
12. Detail-page text is a renderer composition, not a single table field. The current static map joins localization templates such as `GongFa_LingJie_100/101/102/106/131/132`, config schemas such as `FeatureBase/MainFeaturePin/SideFeatureJie/SideFeaturePin/XianjieGongfaStar/Quality`, and runtime state such as `star/jie/pin/tongxuan` from the local VO or share snapshot.
13. A concrete static sample renderer now exists. It can synthesize a VO-shaped detail text from parsed config rows; the true run currently renders `千锋聚灵剑` into five sample rows: base main description, active/inactive main effect, and active/inactive side effect.
14. Renderer source selection is resolved for the next CodeYun step: use `static_gongfa_catalog` first. The current export has no real runtime/share `GongFaHomeMakeVO` JSON payloads, but it has complete enough static catalog/config/template data plus a VO-shaped sample renderer, so frontend wiki progress is not blocked by runtime capture.
15. The first wiki-facing backend renderer path now exists and is wired into the CodeYun wiki UI. `GET /api/fanxiu/resources/gongfa/homemake-static-detail?gongfa_id=306101` returns static rendered detail rows with `rich_text/plain_text`, source tables, config keys, and active/inactive states. The true run on `千锋聚灵剑` returns 5 rows with `include_inactive=true`; the frontend detail panel calls it with `include_inactive=false`, showing 3 current/base rows by default.
16. Static renderer coverage is now audited across all 453 `Gongfa` rows. Current result: `ready=52`, `partial=6`, `zero_rows=395`. Interpretation: this renderer is primarily for the `LingjieGongfa_*` self-created/new-system subset; the 395 zero-row entries are mostly ordinary/older Gongfa systems and should not be forced through this renderer. The 6 partial entries `400101..400106` are confirmed XianShu/仙书 branch rows: their main skill text can fall back to `GongfaSkill.describe`, while their side rows come from `SideFeatureJie.name/feature/param`.
17. `SideFeatureJie.feature` is not direct display text. Current static trace over the 120 XianShu side-feature rows finds 114 candidate semantics through `BuffResource.id` prefix matches and 6 stronger name-side candidates through `FazeLevel.descript` (`须弥芥子`). These are useful mechanism hints, but should still be labeled candidate semantics until buff/skill parameter meanings are decoded.
18. `BuffResource` explains display lifecycle and partial timing for XianShu side-feature candidates, but not full combat formulas. Current true run expands 135 candidate buff rows: `duration+periodic=133`, `duration_only=2`, `buff_type empty=134`, `FUNNEL=1`. Lua evidence shows `Buff.InitData` reads `type/durationType/layer`, `Buff.Update` expires time-based buffs, and `UserBuffItem` only shows countdown when `durationType==0`.
19. Buff combat result ownership is now mapped on the Lua side. True run: 135 candidate buff rows, 55 unique buff ids, 8 flow evidence rows. `BuffVO.configId` ties runtime state to `BuffResource.id`, but computed damage/recovery arrives through `SM_BuffChangeHpAndMp.resultVOs -> BuffResultVO.damage/damageView/recoverHp/recoverMp/fightEffect -> EntityFightView.AddBuffResult -> HurtData`. Readable Lua does not compute the buff damage formula from `BuffResource`.
20. Buff result correlation is now checked and currently stops at a clear boundary. True run: 135 correlation rows, 6 field-usage evidence rows, and 23 `FightCastEffect` enum values. `BuffResultVO` has no `configId`; `id/modelId` are read from the packet but not consumed by visible Lua callsites, while `fightEffect` is a display bitmask. Static Lua cannot precisely link one `BuffResultVO` result back to a candidate `BuffResource.id`.
21. Cpp2IL does not expose this buff-result ownership layer under the Lua packet names. True run over Cpp2IL diffable C#, ISIL, and metadata TSVs finds `0` business hits for `BuffResultVO/SM_BuffChangeHpAndMp/BuffResult/fightEffect/BuffVO`; `modelId` hits are generic model/plot/render APIs, and `configId` only hits generic config names such as `s_SoundConfigID`.
22. BuffResource parameter semantics are now grouped. True run: 135 candidate rows collapse into 55 exact semantic groups; `value/getBuff/buffmodified/removeBuff/buffMutex/stateEffect/viewSkillEffect` are all empty, and only `buffContinued` is populated once (`316104001`). That token links to `Renjie-GongfaJie:316104001`, `GongfaSkill.jieId` rows for `破妄剑意`, and `316104001.lua`, so this is an external skill/timeline context link, not a general formula table. Current static conclusion: `BuffResource` is good for display lifecycle, timing, grouping, and some cross-table context; exact per-hit damage ownership still needs runtime packet samples or deeper combat-native evidence.
23. The first single-mechanism ownership drill-down is now closed for `386001010 洞微剑气` from `400101 须弥感应篇 / 【洞微剑天】(专属)`. `SideFeatureJie.feature -> BuffResource.id` closes to `386001010/386001011`; `386001010` is the only `type=FUNNEL` candidate and carries `duration=16s`, `periodic=1s`, `layer=1`, `relationType=7`, and `buffContinued=316104001`. For this FUNNEL path, ownership is stronger than generic `BuffResultVO`: `SM_FightCastFunnel` and `SM_FightResultFunnel` both carry `buffId`, `FightNetLogic` resolves `EntityMgr:GetFunnelView(msg.buffId)`, and `FunnelSkillActor:SetSM_FightResult4RunTimeSkill` forwards the server result to the running skill. The result is now statically attributable to the buff/funnel instance, but damage values still come from `SM_FightResultFunnel` server packets. Cpp2IL has `62` Funnel presentation/track/bridge hits and `0` business-like packet/formula hits.
24. `SM_FightResultFunnel` result schema is now drilled down. True run: 33 packet field rows, 6 flow rows, 16 `HurtData` mapping rows, and 10 `FightResultVO` numeric/display fields. `SM_FightResultFunnel` adds only `buffId`; it then inherits common `SM_FightResult(casterId,lockId,skillId,results,delayTime)`, where `results` is a `FightResultVO` list. `FightResultVO` carries `targetId/fightEffect/damage/damageView/mpAddDamage/mpAddDamageView/damageTimes/recoverHp/damageReflect/mpDamageAbsorb`, with no `buffId/configId`. `SkillBase:SetSM_FightResult` maps those server fields into `HurtData:SetData`, splitting display values by timeline hurt percent. This confirms the client projects server numbers into visuals; it still does not compute the underlying formula.
25. The `SM_FightResultFunnel.results` producer/write surface is now checked. True run: 22 focused Lua surface hits, 9 check rows, 0 potential client producer hits, 4 server-to-client registration/handler hits, 16 client consumer hits, 2 generated serializer hits, and 0 Cpp2IL named producer hits. No visible Lua match exists for `GetMessageFromPools(_SM_FightResult...)`, `F_SendMsg(_SM_FightResult...)`, or `FightResultVO.new()`. The only writes are generated packet serializers such as `writeList(self.results)`, which are not send evidence by themselves. Current interpretation: this result list is server-produced and client-consumed; formula hunting should move to server-adjacent/native evidence or runtime packet observation, not another pass over this Lua packet writer layer.
26. XianShu display formula recovery is now separated from runtime formula ownership. True run for `386001010 洞微剑气`: `XianjieGongfaStar.lua` parses to 2043 rows, its featureGroup `3000101` has 51 star rows, and star 1 contributes `[4000,0,0]`; `SideFeatureJie:300001` contributes `[0,5,500]`, producing combined display parameters `[4000,5,500]`. The rendered client-visible text says the skill adds `4000%` attack spirit damage, the funnel lowers spirit parry by `5%`, and the funnel deals `500%` attack spirit damage per second. This is a tooltip/detail formula surface, not proof that the client computes the authoritative result values.
27. The XianShu display formula has also been generalized into a catalog. True run with `star=1` outputs `2000` catalog rows across `40` XianShu featureGroups, with `440` rows carrying BuffResource prefix candidates. Each row combines one `SideFeatureJie` jie row with the matching `XianjieGongfaStar.describe` template and star parameters, producing rendered plain text suitable for a wiki formula index. It remains display-layer evidence only.
28. The XianShu formula usage surface is now separated from `xianEffectMap` runtime state consumption. True run outputs `112` Lua/index usage rows: `GongfahomemakeData` is the only real `LingjieGongfa_XianjieGongfaStar` config loader/cache owner, `GongfahomemakeModel` exposes lookup by `featureGroup/star`, and UI/manager code renders detail text through `GetMainDes`. `xianEffectMap` is read from protocol VO payloads and consumed by UI/battle helpers as state, but no visible battle/fight/message layer directly reads `XianjieGongfaStar` formula config to produce result numbers. Cpp2IL formula-term hits remain `0`, so this strengthens the display-surface/not-runtime-authority boundary.
29. The battle-side `xianEffectMap` consumer is now explained. True run outputs `7` curated flow rows and `140` surface rows: `SkillMgr.IsSkillConflict` is called from manual/auto skill release guards, converts the self-created Gongfa's `effectMap/xianEffectMap` through `GongFaNewMgr.GetGongFaIdArrCompare`, and then calls `GongfahomemakeMgr.GetHaveSameEffect/CompareGongFaIdArr` for duplicate/equipped-effect conflict checks. This is identity/slot comparison state, not formula evaluation; battle/fight/message direct formula-config hits remain `0`.
30. The self-made/XianShu cast request boundary is now checked. True run outputs `5` flow rows and `64` packet-field rows: `UserSkillActor.ReleaseSkill4User` passes `skillVo.jie/star/makeId` into `FightNetLogic.CM_FightBySkill`, but `FightNetLogic` uses them for local `ReleaseSkillExecute(... stage, star, makeId)` pre-play/tip context before sending concrete `CM_FightByTarget/Dir/Position/Targets` packets. Those client request packets write only `casterId`, `skillId`, target/dir/pos/move/curr context, and `selectTargetIds`; no `makeId/jie/star/xianEffectMap/param/featureGroup` request fields are visible. `SM_FightCast` server-to-client carries `jie/star`. Current conclusion: the client sends a cast intent plus targeting context, not the authoritative formula/state payload.
31. The server cast-ack consumer chain is also closed. True run outputs `7` flow rows and `22` packet-field rows: `SM_FightCast` is registered in `FightNetLogic`, carries `skillId/jie/star/cdTime/attackPerSecond/fightCastVO`, and `SM_FightCastFun` forwards the message to `FightMgr.EntityFightCast`. `FightMgr` then routes `msg.jie/msg.star` into `OnEntityCast` or `OnUserCast`; other-entity casts preserve them through `DoSkillAction.InitData(curSkillStage/curSkillStar)` or direct `ReleaseSkillExecute`. The cast-ack/consumer chain still has no `makeId`, reinforcing the split between local self-made-name display and server-provided stage/star presentation context.
32. The native `SkillCastBridge` boundary under cast requests is now checked. True run outputs `5` flow rows, `100` Lua surface rows, `35` Cpp2IL surface rows, and `0` formula/state term hits. Lua `SkillCastBridge.lua` delegates to `LuaBridge.Skill.SkillCastBridge`; `FightNetLogic.SendFightMessage` uses `Rectangle/Circle/Sector/LineCastAll` to compute candidate `targetIds`, then `CM_CheckFightByTargets` filters them and sends `CM_FightByTargets.selectTargetIds`. Cpp2IL exposes `Int64[]` cast methods plus `Collider/Raycast/Vector3` temp fields. There are no `XianjieGongfaStar/xianEffectMap/GongFaHomeMake/FightResult/BuffResource/makeId` hits inside this bridge surface, so this layer is geometry/target selection, not formula authority.
33. `Stage/Star` after the server cast ack are now traced through the playback boundary. True run outputs `6` flow rows, `58` focused surface rows, `4` timeline hit-timing rows, and `0` nearby formula/authority terms. Chain: `FightMgr.ReleaseSkillExecute(jie,star) -> _TempSkillParam.Stage/Star -> StateSkill.Enter -> tParam.stage/star -> SkillActor.ReleaseSkill -> SkillBase.Start -> UpdateTimelineData -> SkillConfig.GetTimelineIdBySkillId`. `PreLoadMgr` also uses `skillVo.jie/star` for timeline asset preload, and `SkillBase` loads `q_hurt_events/Cfg_Hurts`, so stage/star can select presentation timeline and hit-frame timing. Current conservative boundary: stage/star are timeline/presentation and hit-track selectors, not visible numeric combat formula authority.
34. `SkillConfig.GetTimelineIdBySkillId` is now resolved into concrete config rows. True run outputs `6` rule rows, `10685` skill-timeline resolution rows, `1195` unique timeline ids, `1189` existing timeline Lua files, `6` missing timeline files, and `1174` decoded timeline files with track clips. Runtime selector: positive `star` wins and maps `1/2/3/4` to `jian/xian/mo/sha_timelineId`; without positive star, `stage >= Skill_ConfigValue.CHANGE_TIMELINE_MIXRANK` (`6`) uses `maxRankTimelineId`, otherwise `timelineId`. The timeline files expose effect/sound resources and hit-frame metadata, not numeric formula authority.
35. Timeline hurt events are now traced through the client projection layer. True run outputs `6` flow rows, `33` focused surface rows, `80` hit-frame samples, and `0` formula-authority hits in the focused excerpts. `q_hurt_events` supplies hit time, split percent, trajectory flag, and trajectory index; `SkillBase.SetSM_FightResult` consumes server `FightResultVO` values and multiplies display/numeric values by timeline percent before creating `HurtData`; `HurtFrameVo` then executes those projected rows by playback time or trajectory. Current boundary: this explains hit-frame timing, multi-hit display splitting, and floating-number projection, not the authoritative combat formula.
36. The result-packet family now has a decoder/consumer map for runtime samples. True run outputs `4` family rows, `21` schema rows, and `9` handler rows. Packet ids: `SM_FightResult=60005`, `SM_FightResultTalisman=60041`, `SM_FightResultFunnel=60054`, `SM_FightResultPet=60055`. Talisman/Pet/Funnel inherit the common `SM_FightResult` payload; only Funnel reads `buffId` before the common fields. Common fields are `casterId/lockId/skillId/results/delayTime`, and `results` is a `FightResultVO` list with 10 value/display fields. This is the schema to use if privacy-filtered runtime packet samples are collected later.
37. Socket primitive decoding is now mapped to typed pools. True run outputs `9` primitive rules, `5` flow rows, and `14/14` evidence rows. Generated Lua packet classes do not directly read raw TCP bytes: `BaseMessage.readInt/readByte/readShort/readBool` consume `SocketPoolData.GetCurIntVal`, `readLong` consumes two `GetCurUIntVal` values and constructs `LusuoLong.new(low, high)`, and float/double/string use their own typed pools. C#/IL2CPP `PoolMessageManage` reads the raw stream through `LusuoStreamQuick`, fills `CCSocketPoolData` lists, then `CsCallLuaMgr.ReceiveSocketMessage` passes typed lists into Lua `SocketManager.ReceiveSocketMessage`. Practical implication: runtime observation should prefer the typed pool bridge first; a raw TCP decoder still needs the lower `LusuoStreamQuick/PoolMessageManage` layer.
38. A non-invasive typed-pool runtime observation plan is now explicit. True run outputs `4` capture points, `7` reconstruction steps, `8` sample-shape fields, and `5` privacy rules using the existing FightResult schema and primitive-pool rules. If runtime observation is attempted later, start at `CsCallLuaMgr.ReceiveSocketMessage` or Lua `SocketManager.ReceiveSocketMessage`, allowlist only `60005/60041/60054/60055`, replay `BaseMessage` primitive reads over the typed pools, and hash runtime actor ids while never persisting account tokens, SDK login payloads, server sessions, or full raw socket streams.
39. A raw socket decoder outline is now mapped below the typed-pool layer. True run outputs `6` frame rows, `7` primitive rows, and `12/12` evidence rows. Static Cpp2IL/ISIL evidence supports: send path writes `WriteNoCompress(totalLength) -> headerStream(sn, proId) -> bodyStream -> Socket.BeginSend`; receive path reads a 4-byte outer header with `ByteUtil.ReadInt(..., IsLittleEndian=false)`, builds `LusuoStreamQuick(..., isMsgCompress)`, then performs three leading `ReadInt` calls before `PoolMessageManage.IsLuaMessage/PoolMessageManage.read`. `ReadInt` branches to `ByteUtil.ReadIntCompress` unless uncompressed/forced; `ReadBigString` and `ReadBigStringByte` are length-prefixed. Exact compressed-int byte reconstruction should still be calibrated with a small packet fixture because Cpp2IL leaves part of `ByteUtil.ReadIntCompress` as invalid ARM instructions.
40. The compressed int path now has an executable candidate codec. True run outputs `5` codec rules, `16` roundtrip samples, and `5/5` evidence rows. The candidate is standard signed int32 zigzag plus 7-bit varint chunks: `u=((value<<1)^(value>>31))&0xffffffff`, continuation bytes use `0x80`, and decode is `(u>>1) ^ -(u&1)`. This matches `WriteIntCompress`, `revertSign32`, and `ReadInt` branch evidence, and all generated samples roundtrip. It is still labeled candidate, not final live-wire proof, until checked against a captured/fixture packet byte sequence.
41. An existing decoded TCP fixture now calibrates the compressed-int body-length model. The selected fixture is `tcp_captures/frxx_tcp_20260524_224855.codeyun_decoded.json`; true run outputs `86` frame rows, `60` protocol-count rows, `6` sensitive-key rows, and `86/86` frame body-length matches. Calibration rule: `frame_len == len(encode_int_compress(sn)) + len(encode_int_compress(pro_id)) + payload_len`. This confirms the candidate codec against real local capture metadata without exporting parsed payload values. The fixture contains no `SM_FightResult` family frames, so it calibrates socket framing/codec only, not combat result semantics.
42. A combat formula authority contrast report now prevents over-generalizing from one mode to another. True run outputs `4` contrast rows and `5` evidence rows. It confirms `BLLD` exposes a real local mini-game formula surface (`329` formula evidence rows and `BLLDFightComponent:AddDamageResult`), while `GongFaHomeMake`/main fight evidence still points to server-produced `SM_FightResult*` values consumed and projected by the client. Timeline `q_hurt_events` remain timing/percent projection metadata, and the current TCP fixture has `0` FightResult-family frames.
43. Cpp2IL main-combat formula-surface scan is now also covered. True run outputs `6` role rows and `5` formula-term hit rows, with `0` strong hits outside known geometry/debug surfaces. The visible Cpp2IL roles are `BattleMgr` state/presentation speed, `SkillCastBridge` target-selection geometry and range debug, `SkillBaseConfig.q_hurt_events`, `HurtEventTrack/HurtEventData`, and `HurtScriptTable` timeline metadata. The only formula-like hits are `ShowSkillDamageRangeDebug`, not damage calculation. This strengthens the current boundary: main combat numbers remain best treated as server result values projected by the client.

Important network/domain facts:

- Bootstrap/config: `https://prod-config-frxxz.akbing.com/config/android`
- CDN/resource roots include `https://cdn-frxxz.akbing.com/...`, `https://cdn-frxxz2.akbing.com/...`, `https://clientapp-frxxz.akbing.com/...`
- Login/server-list endpoint: `https://prod-login-frxxz.akbing.com/game/server`
- Server check endpoint: `https://prod-login-frxxz.akbing.com/game/check_server`
- Manifest has `usesCleartextTraffic=true`; network security config trusts `user/system` certs. This matters for local proxy/capture, but socket payloads still need separate parsing.

## Proven Login Chain

Static/Dex side:

```text
FlameUnityActivity.SDKLogin
  -> SQwanCore.login
  -> FlameUnityActivity$7.onSuccess(Bundle)
  -> Bundle token/gid/pid
  -> builds "1__token__gid__pid..."
  -> SDKCallback.OnSDKLoginData
  -> UnityPlayer.UnitySendMessage("GameEnter", "OnReceiveLogin", data)
```

Unity/IL2CPP side:

```text
PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin(data)
  -> PhoneReceiver.PhoneMsgReceiver.OnReceiveLoginData(data)
  -> split by "__"
  -> status = Int32.Parse(parts[0])
```

Success path reconstructed from Cpp2IL ISIL:

```text
parts[1] -> LoginToken -> AccountInfo.V_LoginToken
parts[2] -> GameId     -> AccountInfo.V_GameId
parts[3] -> Pid        -> AccountInfo.V_PId
parts[4] -> Uid        -> AccountInfo.V_UId
parts[5] -> TimeStamp

CsCallLuaMgr.GetLoginTokenSucceed(LoginToken, GameId, Pid, Uid, TimeStamp)
```

Cpp2IL-to-Lua bridge now confirmed:

```text
CsCallLuaMgr.GetLoginTokenSucceed(LoginToken, GameId, Pid, Uid, TimeStamp)
  -> _LoginMgr["GetLoginTokenSucceed"](LoginToken, GameId, Pid, Uid, TimeStamp)
  -> LoginMgr.Inst_get():LoginCheck(LoginToken, GameId, ChannelId, Uid, Timestamp)
  -> LoginMgr:GetSDKServerInfo(Pid, Token)
  -> GetServerInfo:GetSDKServerList(Pid, Token)
```

Failure path:

```text
GameLoginBridge.SendClickStateDefine(36)
PhoneHelper.F_UploadThinkingLaunchProcess(1102, ...)
Debuger.LogError(...)
```

## Proven Server/Socket Chain

Formal server-list path:

```text
GetSDKServerList
  -> GameLoginBridge.F_GetServerList(callbackId, jsonData, isZip)
  -> prod-login-frxxz.akbing.com/game/server
  -> LoginData/LoginServer
  -> IntoGame(sd.V_Host, sd.V_Port)
  -> LoginMgr:SetServerData(domain, port, ...)
  -> SocketManager:F_InitSocketCon(curServerItem.domain, curServerItem.port, ...)
  -> SocketBridge.F_Connect
```

Cpp2IL now confirms the HTTP bridge details for the server-list leg:

```text
GameLoginBridge.F_GetServerList(callbackId, jsonData, isZip)
  -> GameInitSettingModel.F_GetSettingValue(ServerListUrl = 8)
  -> String.Format(settingUrl, jsonData, isZip)
  -> FileUtil.F_LoadFilePost(url, jsonData, successDelegate, errorDelegate)
  -> CoroutineManager.StartCoroutine(...)
  -> success: gzip bytes + UtilCompress.DecompressFromGzip + UTF8, or DownloadHandler.get_text
  -> CallBackManager.CallStringDelegate(callbackId, responseText)
```

The production config evidence remains `https://prod-login-frxxz.akbing.com/game/server`; the APK also carries older/default `frxxz-test1.eyugame.com` URL config entries.

Cpp2IL also confirms the shared `FileUtil.F_LoadFilePost` loader shape:

```text
F_LoadFilePost(url, postData, finishFunc, error)
  -> Encoding.UTF8.GetBytes(postData)
  -> new UnityWebRequest(url, method)
  -> UploadHandlerRaw(bytes)
  -> CertificateHandler()
  -> SetRequestHeader(header, value)
  -> DownloadHandlerBuffer()
  -> SendWebRequest()
  -> success: finishFunc.Invoke(downloadHandler)
  -> error/timeout: error.Invoke(request.error), Dispose(), optional retry
```

The exact request-header name/value are not resolved by the current ISIL report. Keep that as a small evidence gap unless a later string-usage mapper ties the metadata string literal to the `SetRequestHeader` operands.

Lua now confirms the response-side handoff into the socket target:

```text
CallBackManager.CallStringDelegate(callbackId, responseText)
  -> GetServerInfo callback(jsonData)
  -> LuaEventMgr.RaiseEvent(GET_SERVER_LIST_SUCCEED, jsonData)
  -> ServerGroupListView.F_ServerListUpdateFun(jsonStr)
  -> LoginModel.SetServerListData(jsonStr)
  -> LuaUtil.decode(jsonStr, typeof(LoginData))
  -> LoginData.FillData(data).ServerInfo(data.servers)
  -> LoginData.ServerVoInfo: LoginServer.FillData(server)
  -> LoginServer.V_Host = data.host / V_Port = data.port
  -> WinLogin or AutoIntoGame passes V_Host/V_Port into LoginMgr.IntoGame
  -> EnterGameInfo.StartEnter_1 -> SetServerData + SocketConnect
  -> SocketBridge.F_Connect(pIp, pPort, ...)
```

The effective server-list schema is `{code, data}` optional wrapper, with `data.servers[]` carrying `host`, `port`, `id`, `server`, `name`, `group`, and related display/state fields. The local fallback emits the same `data.servers` shape.

Login socket packet schema already observed:

- `CM_Login = 20001`
- `SM_Login = 20002`
- `CM_ReLogin = 20009`
- `SM_ReLogin = 20010`
- `CM_ProtoHash = 20013`
- `SM_ProtoHash = 20014`

`CM_Login` fields include `account/serverId/pid/cid/gid/device/devId/pushToken/bundleId/bundleVersion/location/channelPackage/signTime/sign`.

Lua send-side packet flow now confirmed:

```text
SocketConnected / EnterGameInfo.StartEnter_2
  -> LoginNetLogic.CM_ProtoHashFun()
  -> CM_ProtoHash(20013) via SocketManager.F_SendMsg
  -> SM_ProtoHash(20014): hash/version
  -> LoginMgr.ContinueLogin(msg)
  -> EnterGameInfo.StartEnter_3(msg) checks hash/version
  -> EnterGameInfo.ContinueLogin()
  -> LoginNetLogic.CM_LoginFun(curServerItem...)
  -> CM_Login(20001).writing()
  -> BaseMessage.write -> LusuoStreamWarp -> ProtoBridge
  -> SocketManager.DoSendMsg -> LuaSocket.F_Send(pid, sn)
  -> SocketBridge.F_Send(pid, isMainSocket, sn)
```

`CM_Login` wire order is: `account:String`, `serverId:Int`, `pid:String`, `cid:String`, `gid:String`, `device:String`, `devId:String`, `pushToken:String`, `bundleId:String`, `bundleVersion:String`, `location:String`, `channelPackage:Int`, `signTime:Int`, `sign:String`. `CM_ReLogin(20009)` is the relogin branch with `account/pid/serverId/token`.

Cpp2IL now confirms the native send-frame handoff:

```text
ProtoBridge.WriteInt / WriteBigString
  -> shared LusuoStreamQuick write buffer
  -> SocketBridge.F_Send(proId, isMainSocket, sn)
  -> SocketManager.F_Send(proId, isMainSocket, sn)
  -> ByteSocket.F_Send(proId, sn)
  -> header stream: WriteInt(sn), WriteInt(proId)
  -> packet stream: WriteNoCompress(body_length + 12), header stream, body stream
  -> Socket.BeginSend(packet_buffer, 0, packet_length, ...)
```

`WriteBigString` is length-prefixed UTF-8 bytes. `WriteInt` is controlled by `LusuoStreamQuick.isCompress`; the compressed branch has varint/zig-zag style operations, which is serialization compression, not proof of cryptographic encryption. Treat the `body_length + 12` frame shape as a strong static hypothesis for send-frame construction; receive-side parsing now confirms the length-head/body/dispatch architecture, but exact runtime byte widths still need a packet sample if we want a final wire spec.

Cpp2IL now confirms the native receive-frame and dispatch handoff:

```text
Socket.Receive(4-byte length head)
  -> ByteUtil.ReadInt(head)
  -> Socket.Receive(body bytes)
  -> ByteSocket.ReadPackage()
  -> LusuoStreamQuick(buffer, length, littleEndian=false, isMsgCompress)
  -> ReadInt(...) packet header fields
  -> PoolMessageManage.IsLuaMessage(proId)
  -> PoolMessageManage.read(proId, stream)
  -> CSMessagePool.F_GetMessage(proId)
  -> packet.reading(...)
  -> CSMessagePool.F_SendHandler(proId, message)
  -> Lua MessagePool handler / LoginNetLogic callback
```

Concrete login receive examples are pinned: `SM_Login=20002`, `SM_ProtoHash=20014`, and `LoginNetLogic` registers both handlers. The exact semantic names/order of the `ReadPackage` header integers are still not final; avoid over-naming them until a tighter probe or live capture confirms the fields.

The Lua protocol catalog has been expanded from a simple packet index into a usable static protocol map:

```text
parsed_configs/lua_packet_index/packets.tsv
parsed_configs/lua_packet_index/packet_fields.tsv          # legacy read-field table
parsed_configs/lua_packet_index/packet_wire_fields.tsv     # read/write field order
parsed_configs/lua_packet_index/packet_registrations.tsv   # MessagePool.F_Register sites and handlers
parsed_configs/lua_packet_index/protocol_catalog.tsv       # joined id/name/module/fields/handlers table
parsed_configs/lua_packet_index/protocol_catalog_canonical.tsv
parsed_configs/lua_packet_index/protocol_login.tsv
parsed_configs/lua_packet_index/protocol_bluestarsea.tsv
parsed_configs/lua_packet_index/protocol_blld.tsv
parsed_configs/lua_packet_index/protocol_gongfa.tsv
parsed_configs/lua_packet_index/protocol_fight.tsv
parsed_configs/lua_packet_index/protocol_faze.tsv
```

Current true run: `11786` packet/VO files, `6176` message ids, `54959` read/write wire fields, `1166` `MessagePool` registrations, `1053` registered packets, `630` packets with named handlers, and `6210` de-duplicated canonical protocol rows. Feature subset counts: `login=38`, `bluestarsea=30`, `blld=20`, `gongfa=109`, `fight=133`, `faze=42`. Examples now line up cleanly: `CM_Login(20001)` has 14 write fields, `SM_Login(20002)` dispatches to `SM_LoginData`, `SM_ProtoHash(20014)` dispatches to `SM_ProtoHashFun`, and BlueStarSea packets such as `CM_BlueStarSeaPurify(98004)` / `SM_BlueStarSeaPurify(98005)` show both wire fields and `BlueStarSeaNetLogic` handlers.

BlueStarSea now also has a semantic call-chain overlay built from `protocol_bluestarsea.tsv` plus the runtime/model/authority probes:

```text
apk_static_index/hot_update_bluestarsea_protocol_semantics.tsv
apk_static_index/hot_update_bluestarsea_protocol_semantic_edges.tsv
apk_static_index/hot_update_bluestarsea_protocol_semantics_report.md
```

Current true run: `30` protocol/VO rows, `69` semantic edges, and `11` operations. Roles: `client_intent=11`, `server_state_or_result=11`, `value_object=5`, `server_packet_not_registered_in_current_netlogic=2`, `client_packet_not_called_in_current_lua=1`. This makes it possible to read `CM_BlueStarSeaPurify -> SM_BlueStarSeaPurifyFun -> OnPurify -> energy/reward state writes` without manually jumping across TSVs and Lua files.

BLLD has the same semantic overlay:

```text
apk_static_index/hot_update_blld_protocol_semantics.tsv
apk_static_index/hot_update_blld_protocol_semantic_edges.tsv
apk_static_index/hot_update_blld_protocol_semantics_report.md
```

Current true run: `20` protocol/VO rows, `41` semantic edges, and `7` operations. Roles: `server_state_or_result=8`, `client_intent=7`, `value_object=3`, `client_packet_not_called_in_current_lua=1`, `server_packet_not_registered_in_current_netlogic=1`. The key edge expansion is `CM_BlldFinishAndReward -> submits_summary_field(levelId/success/passRate/findReward) -> SM_BlldFinishAndReward -> SM_BlldFinishAndRewardFun -> SetFinishAndReward/RaiseEvent/ExitCurScene`; this is the clearest current static statement of the “client battle summary, server final reward” boundary.

Faze also has a semantic overlay:

```text
apk_static_index/hot_update_faze_protocol_semantics.tsv
apk_static_index/hot_update_faze_protocol_semantic_edges.tsv
apk_static_index/hot_update_faze_protocol_semantics_report.md
```

Current true run: `42` protocol/VO rows, `80` semantic edges, and `16` operations. Roles: `value_object=15`, `client_intent=11`, `server_state_or_result=11`, `server_push_or_notification=5`. The most important edge chain is `FazeEffect -> SM_FazeEffect -> SM_FazeEffectFun -> FazeEffectTip(msg)`, with explicit `carries_rule_field` edges for `fazeId/effectType/num/reason`.

These feature overlays are now also exposed through a shared CodeYun query layer:

```text
GET /api/fanxiu/resources/protocol-semantics?feature=bluestarsea|blld|faze|gongfa
frontend route: /fanxiu/protocol-semantics
```

The page is a compact packet/edge inspector rather than a raw file viewer: feature tabs switch between BlueStarSea/BLLD/Faze/Gongfa, filters can narrow by packet/handler/field, role chips show authority categories, the left table lists packets/VO rows, and the right panel shows selected packet semantics plus related operation/handler/state edges. Headless Edge smoke screenshots are kept at `ui_checks/protocol_semantics_page.png` and `ui_checks/protocol_semantics_gongfa_page.png` under the export root.

Gongfa now has the same semantic overlay:

```text
apk_static_index/hot_update_gongfa_protocol_semantics.tsv
apk_static_index/hot_update_gongfa_protocol_semantic_edges.tsv
apk_static_index/hot_update_gongfa_protocol_semantics_report.md
```

Current true run: `109` protocol/VO rows, `178` semantic edges, and `46` operations. Roles: `client_intent=40`, `server_state_or_result=38`, `value_object=21`, `server_push_or_notification=6`, `support_object=4`. Module split shows the surface is dominated by `player.gongfahomemake=53` and `player.gongfa=29`, with smaller career/role/partner/practice/explore packets. Key chains include `CM_GongFaView -> SM_GongFaViewFun -> GongFaNewModel:SetGongFaInfo`, `CM_GongFaUpgrade -> SM_GongFaUpgradeFun -> GongFaNewData:UpdateGongFaVo / ChangedAttrsVo`, and `CM_GongFaUpgradeTimes -> SM_GongFaUpgradeTimesFun -> batch GongFaItemVO + ChangedAttrsVo aggregation`.

`GongFaUpgradeTimes` has a narrow follow-up flow report:

```text
apk_static_index/hot_update_gongfa_upgrade_times_flow.tsv
apk_static_index/hot_update_gongfa_upgrade_times_edges.tsv
apk_static_index/hot_update_gongfa_upgrade_times_flow_report.md
```

Current true run: `17` evidence rows and `11` semantic edges. The static chain is now explicit: `CM_GongFaUpgradeTimes.upgradeList` is only a client request list; `SM_GongFaUpgradeTimes.upgradeList[]` is the source used by the client to update `GongFaItemVO`, aggregate `rewardResults` into local `exp`, merge `attrs.addAttrs`, take the last `attrs.finalAttrs`, and call `GameUtil.DealAttrChangeByModule(allAttrs, exp)`.

`GongFaHomeMake` now has a dedicated self-created Gongfa lifecycle report:

```text
apk_static_index/hot_update_gongfa_homemake_lifecycle_packets.tsv
apk_static_index/hot_update_gongfa_homemake_lifecycle_edges.tsv
apk_static_index/hot_update_gongfa_homemake_lifecycle_report.md
```

Current true run: `54` packet/VO rows and `144` semantic edges. The report joins `protocol_gongfa.tsv` with `lingjie_runtime_packets/vo_usage/vo_fields/net_functions/net_call_sites/state_updates/battle_refs.tsv`. Main conclusion: `GongFaHomeMakeVO` is the client-side full self-created Gongfa object; server responses populate `homeMakeDic`, `newMakeSkillDic`, and `V_CreatingSkillIdList`, while battle/equip UI often carries only `makeId`, traced to `GongFaHomeMakeVO.skillCommonVO.id`, then resolves the full object through `GongfahomemakeModel:GetGongFaHomeMakeVoById`.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-lifecycle-probe
```

`GongFaHomeMakeLearn/Teach` now has a dedicated consult/teach chain report:

```text
apk_static_index/hot_update_gongfa_homemake_learn_teach_packets.tsv
apk_static_index/hot_update_gongfa_homemake_learn_teach_flow.tsv
apk_static_index/hot_update_gongfa_homemake_learn_teach_costs.tsv
apk_static_index/hot_update_gongfa_homemake_learn_teach_edges.tsv
apk_static_index/hot_update_gongfa_homemake_learn_teach_report.md
```

Current true run: `18` protocol/VO rows, `159` runtime evidence rows, `16` cost/config rows, and `12` semantic edges. Main conclusion: `请教` is a filtered market/list flow plus a paid apply/cancel flow. The client sends `CM_GongFaHomeMakeLearn(id,cost,scopeType)`, where `id=data.skillCommonVO.id` and `cost=(selected+1)*GongFaLearnItemVO.pay`; list/status changes are applied from `SM_GongFaHomeMakeLearn*` responses and pushes. `赐教` starts with publishing a self-created Gongfa, then `SM_GongFaHomeMakeTeachList.itemVOS` fills `teachInfo[scopeType][skillType]`; when choosing a player, `CM_GongFaHomeMakeTeachFun(playerId, skillType, scopeType)` writes packet `id=Model:GetTeachId(...)` and `rewardPlayerId=playerId`, so the UI parameter named `id` is not the packet's skill id. Cost constants now link `CREATION_ITEM=悟道石`, `XINFA_ITEM=心悟石`, and `XIANFA_ITEM=悟仙石`.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-learn-teach-probe
```

`GongFaHomeMakeRecord/Grid/LightUp` now has a dedicated dynamic-state report:

```text
apk_static_index/hot_update_gongfa_homemake_record_grid_light_packets.tsv
apk_static_index/hot_update_gongfa_homemake_record_grid_light_flow.tsv
apk_static_index/hot_update_gongfa_homemake_record_grid_light_grid_config.tsv
apk_static_index/hot_update_gongfa_homemake_record_grid_light_edges.tsv
apk_static_index/hot_update_gongfa_homemake_record_grid_light_report.md
```

Current true run: `7` protocol/VO rows, `82` runtime evidence rows, `6` grid config rows, and `10` semantic edges. Main conclusion: `LightUp` sends only a self-created Gongfa id, then the success path mutates the request-side `msg.ClientData.gongFaHomeMakeVO.isLight=true` and raises `GongfahomemakeType.GongFaHomeMakeLightUp`; the packet itself does not carry a full updated VO. `GridList` returns `gridMap` into `GongfahomemakeData.gridInfo`, while the local `LingJieGrid/XianjieGrid` config provides three display/unlock slots for each scope. `RecordList(type,skillType,scopeType)` returns `recordVOS`, which are normalized into `tacRecordDic[scopeType][type][skillType]`; record rows carry both current player identity and creator identity fields.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-record-grid-light-probe
```

`GongFaHomeMake` remaining mutation operations now have a dedicated report:

```text
apk_static_index/hot_update_gongfa_homemake_mutation_ops_packets.tsv
apk_static_index/hot_update_gongfa_homemake_mutation_ops_flow.tsv
apk_static_index/hot_update_gongfa_homemake_mutation_ops_edges.tsv
apk_static_index/hot_update_gongfa_homemake_mutation_ops_report.md
```

Current true run: `21` protocol/VO rows, `228` runtime evidence rows, and `12` semantic edges. Main conclusion: `CM_GongFaHomeMakeCombine*` submits material/base ids and optional LingJie/XianGongfa context, but the created object is only applied from `SM_*homeMakeVO` via `GongFaHomeMakeCombineUpdate -> AddNewMakeSkill/V_CreatingSkillIdList`; `CheckName` only returns availability through `ClientData.callBackFun`, while `ChangeName` is the real rename/icon/mark mutation. `CM_GongFaCheck(id,isCheck)` toggles local attention after server confirmation by writing `homeMakeDic[msg.id].isCheck`; `CM_GongFaTenCreateCheck(type,isCheck)` controls batch-create check flags. `CM_GongFaExchange(fromId,toId)` is in this NetLogic surface but updates base `GongFaItemVO` through `GongFaNewData:UpdateGongFaVoEx`, not `GongFaHomeMakeVO`. `CM_GongFaSelectCareer(id,career)` closes the XianFa effect selector and raises a selection event after server confirmation.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-mutation-ops-probe
```

`GongFaHomeMakePageList/HMFilterVO` now has a dedicated list/filter report:

```text
apk_static_index/hot_update_gongfa_homemake_page_list_packets.tsv
apk_static_index/hot_update_gongfa_homemake_page_list_flow.tsv
apk_static_index/hot_update_gongfa_homemake_page_list_filter_fields.tsv
apk_static_index/hot_update_gongfa_homemake_page_list_edges.tsv
apk_static_index/hot_update_gongfa_homemake_page_list_report.md
```

Current true run: `7` protocol/VO rows, `273` runtime evidence rows, `15` filter/list VO field rows, and `9` semantic edges. Main conclusion: `CM/SM_GongFaHomeMakeList` is the broader initialization snapshot and `SM_GongFaHomeMakeList.homeMakeVOS` rewrites `GongfahomemakeData.homeMakeDic`; `CM/SM_GongFaHomeMakePageList` is the paged/filter view surface and does not write the global cache. `HomeMakeHandler` computes `startIdx/endIdx` from `pageIndex * V_PageShow` with default `V_PageShow=100`, sends `HMFilterVO(type,skillType,isNotLightUp,careers,mainSkills,assistSkills,threeSet,mainXianSkills,xianThreeSet)`, and maintains local `V_DataList/V_DataDic/V_PageDic/totalNum` after `SM_GongFaHomeMakePageList(filterVO,homeMakeVOS,totalNum)`. `ClientData.viewType` is response routing metadata used by views such as `GongFaChooseTeachView`, not packet body.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-page-list-probe
```

`GongFaHomeMakeShareVO/chat share` now has a dedicated packaging/display report:

```text
apk_static_index/hot_update_gongfa_homemake_share_packets.tsv
apk_static_index/hot_update_gongfa_homemake_share_fields.tsv
apk_static_index/hot_update_gongfa_homemake_share_config.tsv
apk_static_index/hot_update_gongfa_homemake_share_flow.tsv
apk_static_index/hot_update_gongfa_homemake_share_edges.tsv
apk_static_index/hot_update_gongfa_homemake_share_report.md
```

Current true run: `4` protocol/VO rows, `23` VO field rows, `1` share-channel config row, `198` runtime evidence rows, and `10` semantic edges. Main conclusion: self-created Gongfa sharing is not a normal `CM/SM_GongFaHomeMake*` mutation. The detail views call `GongfahomemakeMgr:GetShareList()` and `ChatMgr:OpenShareToChatView`, then send a `LING_JIE_GONGFA` chat param whose `id/value` are `CreateSkillCommonVO.id:ToString()`. The i18n/chat value wrapper is `I18nParam2LingJieGongFa.value -> GongFaHomeMakeShareVO`; `GongFaHomeMakeShareVO.homeMakeVO` carries the full self-created object, while `itemVOMap` is read by the client but not written by `GongFaHomeMakeShareVO.writing()`, so it should be treated as server/receiving-side display data for resolving base Gongfa item, pin, and quality metadata. Share channels come from `LingjieGongfa_ConfigValue.SHARE_CHANNEL_LIST=4,34,42,102,104`.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-share-probe
```

The chat-share UI consumer side now has a separate report:

```text
apk_static_index/hot_update_gongfa_homemake_share_ui_flow.tsv
apk_static_index/hot_update_gongfa_homemake_share_ui_edges.tsv
apk_static_index/hot_update_gongfa_homemake_share_ui_report.md
```

Current true run: `187` runtime evidence rows and `9` semantic edges. Main conclusion: the visible detail-page share buttons are direct-send paths (`OpenShareToChatView -> data.content="{0;9:0;}" -> LING_JIE_GONGFA id/value`). The generic chat share picker has a `ChatShareType.LingJieSkill` UI config using `LingJieSkillItem`, but current `ChatData._ChatShareItemDic` does not explicitly register a LingJieSkill item-list provider, so that generic picker should not be confused with the detail-page share path. On the receiving/rendering side, `ChatMgr.DecryptionHyper` routes the share regex to `GetShareLingJieSkillInfo`, which saves `GongFaHomeMakeShareVO` by `createTime` in `_ChatLingJieSkillShareMap` and formats a clickable label with payload `66|createTime`. The generic href callback and `66|createTime` consumer are now covered by the follow-up href report below.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-share-ui-probe
```

The chat-share href click path now has a separate TextEx/Lua callback report:

```text
apk_static_index/hot_update_gongfa_homemake_share_href_flow.tsv
apk_static_index/hot_update_gongfa_homemake_share_href_edges.tsv
apk_static_index/hot_update_gongfa_homemake_share_href_report.md
```

Current true run: `320` runtime/Cpp2IL evidence rows and `10` semantic edges. Main conclusion: C# `TextEx.OnPointerClick` only hit-tests `HrefInfo` boxes and dispatches raw `HrefInfo.m_Content` through `CallBackManager.CallStringDelegate`; it does not understand `66`. Lua `LuaTextEx:AddHyperClickEvent` registers the string callback through `LuaCallBackMgr` and stores its id via `TextExBridge.AddHyperClickListener`. The business consumer is `LuaGlobal.OnHyperLink -> HyperLinkMgr.DealWithHyperLinkStr -> HyperLinkType.LingJieSkill -> DealWithLingJieSkill`, where `66|createTime` becomes `data[1]`, then `ChatModel:GetChatShareLingJieSkillInfo(createTime)` opens `GongfahomemakeMgr:OpenCreateSkillDetailView(vo,true)`. Remaining gap: visible `ChatLabelChatCell.lua` sets `labelContent` text and long-press behavior, but does not show `labelContent:AddHyperClickEvent(LuaGlobal.OnHyperLink)`; likely prefab/generated/runtime binding or another non-exported initialization path.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-share-href-probe
```

The chat message prefab binding side now has a separate report:

```text
apk_static_index/hot_update_gongfa_homemake_share_href_prefab_variables.tsv
apk_static_index/hot_update_gongfa_homemake_share_href_prefab_components.tsv
apk_static_index/hot_update_gongfa_homemake_share_href_prefab_edges.tsv
apk_static_index/hot_update_gongfa_homemake_share_href_prefab_report.md
```

Current true run: `12` PrefabBinder variables, `26` MonoBehaviour components, `6` TextEx components, `0` errors, and `4` semantic edges from `selflabelchatcontent.bytes` / `otherlabelchatcontent.bytes`. Main conclusion: both self/other chat message prefabs bind variable id `1` as `labelContent:TextEx`, id `2` as `CalcTxt:TextEx`, id `5` as `AllianceCheck:TextEx`, and the Lua code's `SetComponent(LuaTextEx, 1/2/5)` lines match that prefab order. `AllianceCheck` has an explicit Lua `AddHyperClickEvent`; `labelContent` does not. PrefabBinder's serialized data only carries variable id, alias, GameObject reference, and type name, so the missing `labelContent` href callback is not hidden in these two prefabs.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-share-href-prefab-probe
```

The chat-label href registration-gap follow-up now has a separate static-exclusion report:

```text
apk_static_index/hot_update_gongfa_homemake_share_href_registration_gap_evidence.tsv
apk_static_index/hot_update_gongfa_homemake_share_href_registration_gap_edges.tsv
apk_static_index/hot_update_gongfa_homemake_share_href_registration_gap_report.md
```

Current true run: `81` evidence rows and `8` semantic edges. Main conclusion: `TextEx.OnPointerClick` requires `clickHyperCallId >= 1` before dispatching `CallBackManager.CallStringDelegate`, while `LuaTextEx:AddHyperClickEvent` is the only visible Lua API that writes that id through `TextExBridge.AddHyperClickListener`. `LuaUIText:SetText/SetTextSimple` only forwards text, `ChatContentBase.DoUpdateContent` only instantiates/binds/refreshes content, `ChatLabelChatCell.RefreshData` only sets `labelContent` text plus long-press copy, and direct exported Lua bridge bypasses were not found. Therefore the current static evidence does not prove chat message body clicks are wired; if the live game can click these labels, the missing registration is likely runtime/generated/non-exported behavior. Otherwise treat `66|createTime` in chat labels as rich display/copy markup with a reusable backend consumer, not necessarily a body-click entry.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-share-href-registration-gap-probe
```

`GongFaHomeMake` detail-page rendering now has a dedicated VO-to-UI report:

```text
apk_static_index/hot_update_gongfa_homemake_detail_view_flow.tsv
apk_static_index/hot_update_gongfa_homemake_detail_view_edges.tsv
apk_static_index/hot_update_gongfa_homemake_detail_view_report.md
```

Current true run: `362` evidence rows and `9` semantic edges. Main conclusion: the detail view data path is closed in readable Lua. `OpenCreateSkillDetailView(data,isOther,isBagClick)` accepts either a direct `GongFaHomeMakeVO` or an external/chat `GongFaHomeMakeShareVO` wrapper, then routes by `GetScopeType(skillCommonVO)`: non-empty `xianEffectMap` opens `XianShuCreateSkillDetailView`, otherwise `CreateSkillDetailView`. `LingJieSkillItem` renders top card fields from `skillCommonVO.skillName/mark/icon/iconBg` and quality config; `GetMainDes` renders the base main description; normal detail pages expand `effectMap`, while XianShu pages first expand `xianEffectMap` and then the original LingJie `effectMap`. For shared/other-player details, `itemVOMap` supplies snapshot `star/jie/pin/tongxuan` so the viewer does not accidentally render with local player state.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-detail-view-probe
```

`GongFaHomeMake` detail-page localization/config rendering now has a dedicated renderer report:

```text
apk_static_index/hot_update_gongfa_homemake_detail_renderer_templates.tsv
apk_static_index/hot_update_gongfa_homemake_detail_renderer_config_schema.tsv
apk_static_index/hot_update_gongfa_homemake_detail_renderer_flow.tsv
apk_static_index/hot_update_gongfa_homemake_detail_renderer_edges.tsv
apk_static_index/hot_update_gongfa_homemake_detail_renderer_report.md
```

Current true run: `12` localization templates, `9` config schemas, `1209` evidence rows, and `10` semantic edges. Main conclusion: the game-style detail text is generated by combining localization templates, LingjieGongfa config rows, quality colors, and live/snapshot state. `FeatureBase(id)` maps effect ids to `group/featureGroup`; `MainFeaturePin(gongfaId,pin)` supplies main row name/describe/quality; `SideFeatureJie(featureGroup,jie)` supplies side row name/describe/param; `SideFeaturePin(featureGroup,pin)` supplies pin appendix text; `XianjieGongfaStar(featureGroup,star)` supplies xian-effect rows; `Quality` supplies color/frame metadata. The next useful build step is a concrete sample renderer over real or synthetic `GongFaHomeMakeVO` examples, not another broad scan.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-detail-renderer-probe
```

`GongFaHomeMake` detail-page renderer now has a concrete VO-shaped sample report:

```text
apk_static_index/hot_update_gongfa_homemake_detail_renderer_samples.tsv
apk_static_index/hot_update_gongfa_homemake_detail_renderer_sample_report.md
```

Current true run: `5` sample rows, using `千锋聚灵剑` (`gongfa_id=306101`, `skill_id=306106000_1`) as a static example. It renders: base main description from `LingjieGongfaStar.describe + LingjieGongfaJie.param`, active and inactive main effect from `GongFa_LingJie_100/101 + MainFeaturePin + Quality`, and active/inactive side effect from `FeatureBase + SideFeatureJie + Quality`. This is not an account runtime capture; it is a controlled sample proving how CodeYun can render game-style text once a real `GongFaHomeMakeVO` or selected catalog source is available.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-detail-renderer-sample-probe
```

`GongFaHomeMake` renderer source selection now has a dedicated report:

```text
apk_static_index/hot_update_gongfa_homemake_renderer_source_selection.tsv
apk_static_index/hot_update_gongfa_homemake_renderer_source_selection_report.md
```

Current true run: `4` candidate rows. The selected first source is `static_gongfa_catalog`: `Gongfa=453`, `GongfaSkill=6902`, `FeatureBase=1199`, `MainFeaturePin=1692`, `SideFeatureJie=7450`, `SideFeaturePin=848`, `LingjieGongfaStar=1248`, `LingjieGongfaJie=2600`, `Quality=9`, with `12` renderer templates. `vo_shaped_renderer_sample` is ready as UI smoke-test input. `protocol_lifecycle_schema` is ready as a schema guardrail. `captured_or_shared_gongfa_homemake_vo` is `not_available_yet` because the current export contains `0` VO-like payload files. Verdict: CodeYun wiki can start static renderer integration now; no user intervention is needed for this step.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-renderer-source-selection-probe
```

Static `GongFaHomeMake` renderer API:

```text
GET /api/fanxiu/resources/gongfa/homemake-static-detail?gongfa_id=306101&star=1&jie=1&pin=1&include_inactive=true
```

Current true run for `gongfa_id=306101` returns `card.name=千锋聚灵剑`, `rows=5`, `side_effect_sources=1`, `warnings=[]` when `include_inactive=true`. The first three rows are: base main description from `LingjieGongfaStar + LingjieGongfaJie`, active main effect via `GongFa_LingJie_100`, and inactive main effect via `GongFa_LingJie_101`. With `include_inactive=false`, the UI-facing true run returns 3 rows: base description, active main effect, active side effect. The XianShu fallback true run for `gongfa_id=400101` now returns `rows=13`, with the first row from `GongfaSkill.describe` and 12 side-effect name rows; warnings remain for missing `MainFeaturePin/LingjieGongfaStar/LingjieGongfaJie` because the side-effect semantic descriptions are not in those tables. Frontend API helper is `getFanxiuGongfaHomeMakeStaticDetail(...)` in `frontend/src/api/fanxiu.ts`, and `/fanxiu/wiki` renders these rows in the right-side `功法效果` panel.

Static renderer coverage audit:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-static-renderer-coverage-probe
apk_static_index/hot_update_gongfa_homemake_static_renderer_coverage.tsv
apk_static_index/hot_update_gongfa_homemake_static_renderer_coverage_summary.tsv
apk_static_index/hot_update_gongfa_homemake_static_renderer_coverage_report.md
```

Current true run: `453` Gongfa rows, `ready=52`, `partial=6`, `zero_rows=395`. The common missing fields are `MainFeaturePin / LingjieGongfaStar / LingjieGongfaJie` on 401 rows. The six partial rows are `400101..400106`; after the XianShu fallback, they have usable main skill text plus static side-effect names, but still remain partial because `SideFeatureJie` does not provide full natural-language descriptions for these feature ids.

XianShu static gap report:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-xianshu-static-gap-probe
apk_static_index/hot_update_gongfa_homemake_xianshu_static_gap.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_static_gap_report.md
```

Current true run: `6` XianShu Gongfa rows and `120` side-feature rows. All 120 rows are `side_feature_description_missing`: `SideFeatureJie` has `name/feature/param` but no `describe`. The next static decoding target is `SideFeatureJie.feature -> skill/buff/feature` semantics, not another pass over `MainFeaturePin/LingjieGongfaStar/LingjieGongfaJie`.

Side-feature semantics trace:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-side-feature-semantics-probe
apk_static_index/hot_update_gongfa_homemake_side_feature_semantics.tsv
apk_static_index/hot_update_gongfa_homemake_side_feature_semantics_report.md
```

Current true run: `120` rows across `6` XianShu Gongfa. Source split is `buff_resource_feature_prefix=114` and `faze_level_name_match=6`; confidence split is `medium_feature_prefix_buff=114` and `medium_name_match_fazelevel=6`. Example: `38600101` (`【洞微剑天】`) links by buff prefix to `洞微剑气` with descriptions such as `每秒造成伤害并降低神识招架率`; `38500201` (`【须弥芥子】`) also has a `FazeLevel` full-text candidate. Treat these as candidate mechanism semantics, not final client tooltip text.

Buff field semantics trace:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-buff-field-semantics-probe
apk_static_index/hot_update_gongfa_homemake_buff_field_semantics.tsv
apk_static_index/hot_update_gongfa_homemake_buff_field_semantics_code.tsv
apk_static_index/hot_update_gongfa_homemake_buff_field_semantics_report.md
```

Current true run: `135` candidate buff rows and `7` code-evidence rows. Timing split is `duration+periodic=133`, `duration_only=2`; type split is `empty=134`, `FUNNEL=1`; layer split is led by `layer=1` with 113 rows. Example: `386001010 洞微剑气` gives `持续 16s，每 1s 周期触发/刷新` and `type=FUNNEL`; `385001010 无量劫火` gives `持续 4s，每 2s 周期触发/刷新`. This explains client-visible lifecycle and candidate mechanism timing, but damage/attribute formula ownership is still not fully static.

Buff combat-result ownership trace:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-buff-combat-result-probe
apk_static_index/hot_update_gongfa_homemake_buff_combat_result_flow.tsv
apk_static_index/hot_update_gongfa_homemake_buff_combat_result_report.md
```

Current true run: `135` candidate buff rows, `55` unique candidate buff ids, and `8` flow rows. `BuffVO` exposes server runtime state (`configId/layer/remainTime/duration`), while `BuffResultVO` exposes already-computed result values (`damage/damageView/recoverHp/recoverMp/fightEffect`). `BuffNetLogic` dispatches `SM_BuffChangeHpAndMp.resultVOs` into `BuffMgr.UpdateBuffResult`, and `EntityFightView.AddBuffResult` projects those numbers into `HurtData`. This strongly suggests the readable Lua layer is not the formula authority for these candidate buff damages.

Buff result correlation trace:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-buff-result-correlation-probe
apk_static_index/hot_update_gongfa_homemake_buff_result_correlation.tsv
apk_static_index/hot_update_gongfa_homemake_buff_result_field_usage.tsv
apk_static_index/hot_update_gongfa_homemake_fight_effect_enum.tsv
apk_static_index/hot_update_gongfa_homemake_buff_result_correlation_report.md
```

Current true run: `135` candidate buff rows, `55` unique candidate buff ids, `6` field-usage rows, and `23` `FightCastEffect` enum rows. `BuffVO.configId` is the visible field that links active buff state to `BuffResource.id`; `BuffResultVO` has no `configId`, and visible Lua does not consume `buffResultVO.id` or `buffResultVO.modelId` after packet reading. `fightEffect` is a result-display bitmask (`NORMAL/CRIT/BLOCK/IMMUNITY/...`) rather than a buff config id. Static Lua therefore cannot precisely correlate a result row back to one candidate buff config.

Cpp2IL buff-result symbol trace:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-cpp2il-buff-result-symbol-probe
apk_static_index/hot_update_gongfa_homemake_cpp2il_buff_result_symbol_terms.tsv
apk_static_index/hot_update_gongfa_homemake_cpp2il_buff_result_symbol_hits.tsv
apk_static_index/hot_update_gongfa_homemake_cpp2il_buff_result_symbol_report.md
```

Current true run: `8` search roots, `9` term rows, `72` kept hit rows, `0` business hits, and `69` `modelId` hits. The exact buff-result terms `BuffResultVO`, `SM_BuffChangeHpAndMp`, `BuffResult`, `FightCastEffect`, `fightEffect`, and `BuffVO` are absent from Cpp2IL/metadata output. The `modelId` hits are visual/model APIs such as `ShowPlotModelView`, `PreLoadPlotModelView`, `EntityRenderBridge`, and `PlotBridge`; `configId` hits are generic config names such as `s_SoundConfigID`. No Cpp2IL evidence currently links `BuffResultVO.modelId` to a combat formula producer.

BuffResource parameter semantics trace:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-buff-parameter-semantics-probe
apk_static_index/hot_update_gongfa_homemake_buff_parameter_semantics.tsv
apk_static_index/hot_update_gongfa_homemake_buff_parameter_semantics_groups.tsv
apk_static_index/hot_update_gongfa_homemake_buff_parameter_semantics_links.tsv
apk_static_index/hot_update_gongfa_homemake_buff_parameter_semantics_report.md
```

Current true run: `135` candidate rows, `55` exact semantic groups, and `21` cross-table links. Parameter-field population is `value=0`, `getBuff=0`, `buffContinued=1`, `buffmodified=0`, `removeBuff=0`, `buffMutex=0`, `stateEffect=0`, `viewSkillEffect=0`. The only populated field is `386001010 洞微剑气.buffContinued=316104001`, which links to `Renjie-GongfaJie:316104001`, multiple `GongfaSkill.jieId` rows for `破妄剑意`, and the matching `316104001.lua` timeline/config source file. This confirms the candidate `BuffResource` layer is useful for grouping, timing, display lifecycle, and selected external context, but it is not a complete client-side formula table.

Single-mechanism ownership drill-down:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-mechanism-ownership-probe
apk_static_index/hot_update_gongfa_homemake_mechanism_ownership_drilldown.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_ownership_funnel_flow.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_ownership_cpp2il_hits.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_ownership_report.md
```

Current true run for default `buff_id=386001010` (`洞微剑气`) outputs `7` drill-down evidence rows, `9` FUNNEL flow rows, `21` `buffContinued` cross-table links, `62` Cpp2IL Funnel presentation hits, and `0` business-like Cpp2IL packet/formula hits. Verdict: `origin_to_buffresource_closed=true`, `funnel_type_detected=true`, `funnel_packets_carry_buff_id=true`, `funnel_result_ownership_closed=true`, `client_formula_found=false`, `damage_values_from_server_result_packet=true`. Treat this as a static ownership proof, not a damage formula proof.

XianShu formula display-surface recovery:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-mechanism-formula-surface-probe
apk_static_index/hot_update_gongfa_homemake_mechanism_formula_surface.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_formula_family.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_formula_slots.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_formula_surface_report.md
```

Current true run for default `buff_id=386001010`, `star=1`, `jie=1` parses `2043` raw `XianjieGongfaStar` rows and finds `51` rows in featureGroup `3000101`. The selected star row contributes `[4000,0,0]`; `SideFeatureJie:300001 【洞微剑天】(专属)` contributes `[0,5,500]`; the client-visible display parameters become `[4000,5,500]`. Rendered plain text includes: skill extra spirit damage `4000%`, `洞微剑气` lowers spirit parry by `5%`, and deals `500%` attack spirit damage per second for `16` seconds. Keep this separate from runtime authority: server packets still provide the actual `SM_FightResultFunnel.results` numbers.

XianShu formula display catalog:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-xianshu-formula-catalog-probe
GET /api/fanxiu/resources/gongfa/homemake-xianshu-formula-catalog?gongfa_id=400101
apk_static_index/hot_update_gongfa_homemake_xianshu_formula_catalog.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_formula_groups.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_formula_catalog_report.md
```

Current true run with `star=1` outputs `2000` display formula rows across `40` featureGroups; `440` rows have BuffResource prefix candidates. This is the wiki-scale version of the single-mechanism probe: each row combines `XianjieGongfaStar.describe`/star params with one `SideFeatureJie.param` row and emits rendered plain text. The first group `3000101` covers `【洞微剑天】(专属)` over its jie rows; sample params progress from `[4000,5,500]` upward as `SideFeatureJie.param` changes. The GET query layer enriches featureGroups back to `gongfa_ids/gongfa_names`; true check for `gongfa_id=400101` returns `1000` formula rows across `20` groups, and the UI consumes the grouped rows rather than dumping all jie rows.

XianShu formula usage / authority-boundary probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-xianshu-formula-usage-probe
apk_static_index/hot_update_gongfa_homemake_xianshu_formula_usage.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_formula_usage_checks.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_formula_usage_cpp2il_hits.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_formula_usage_report.md
```

Current true run outputs `112` usage rows: `1` logic-index config ref, `6` relevant function-index rows, and `105` Lua usage rows. Checks show formula-detail renderer refs are present, `xianEffectMap` packet reads are present, direct battle/fight/message formula-config refs are `0`, and Cpp2IL formula-term hits are `0`. `xianEffectMap` does have battle-side consumers, but those consume existing runtime state for selection/display comparison; they are not direct `XianjieGongfaStar.describe/param` runtime formula producers. Verdict: `supports_display_surface_not_runtime_authority=true`.

XianShu battle-side state-consumer probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-xianshu-battle-state-usage-probe
apk_static_index/hot_update_gongfa_homemake_xianshu_battle_state_usage_flow.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_battle_state_usage_surface.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_battle_state_formula_hits.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_battle_state_usage_report.md
```

Current true run outputs `7` curated flow rows, `140` surface rows, and `0` battle/fight/message formula-config hits. Context split: `battle_setup_ui=74`, `manager_identity_compare=50`, `battle_runtime_guard=11`, `state_context=5`. The battle runtime entry is `SkillMgr.IsSkillConflict`: it takes `skillCommonVO.xianEffectMap`, builds a `GongFaIdArr` through `GongFaNewMgr.GetGongFaIdArrCompare`, and checks duplicate/equipped effects via `GongfahomemakeMgr.GetHaveSameEffect/CompareGongFaIdArr`. Verdict: `supports_state_consumer_not_formula_authority=true`.

XianShu cast-request boundary probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-xianshu-cast-request-boundary-probe
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_request_boundary_flow.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_request_packet_fields.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_request_checks.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_request_boundary_report.md
```

Current true run outputs `5` curated flow rows, `64` packet-field rows, and `6` checks. The concrete `CM_FightBy*` request write-field set is `casterId/currPos/movePos/selectDir/selectPos/selectTargetIds/skillId/targetId`. `UserSkillActor.ReleaseSkill4User` passes `skillVo.jie/star/makeId` into `FightNetLogic.CM_FightBySkill`, and `FightMgr.ReleaseSkillExecute` consumes `makeId` for local self-made Gongfa skill-tip/name presentation. But the actual client-to-server fight request packet family does not write `makeId`, `jie`, `star`, `xianEffectMap`, `featureGroup`, or formula `param` fields. `SM_FightCast` reads `jie/star` from the server-to-client cast ack. Verdict: `supports_cast_intent_not_formula_authority=true`.

XianShu cast-ack consumer probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-xianshu-cast-ack-consumer-probe
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_ack_consumer_flow.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_ack_consumer_packet_fields.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_ack_consumer_checks.tsv
apk_static_index/hot_update_gongfa_homemake_xianshu_cast_ack_consumer_report.md
```

Current true run outputs `7` curated flow rows, `22` packet-field rows, and `6` checks. `SM_FightCast` is registered in `FightNetLogic` and reads `skillId/jie/star/cdTime/attackPerSecond/fightCastVO/currPos/castingSpeed`; `SM_FightCastFun` sends the message to `FightMgr.EntityFightCast`. `FightMgr.EntityFightCast` forwards `msg.jie/msg.star` into `OnEntityCast` for other entities or `OnUserCast` for the local user. Other-entity paths preserve stage/star through delayed `DoSkillAction.InitData(curSkillStage/curSkillStar)` and direct `ReleaseSkillExecute`; user paths mostly start cooldown/speed/movement correction for the already-started runtime skill. The chain has `makeId` count `0`. Verdict: `supports_server_ack_stage_star_presentation_chain=true`.

SkillCastBridge geometry/target-selection boundary probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-skillcastbridge-boundary-probe
apk_static_index/hot_update_gongfa_homemake_skillcastbridge_boundary_flow.tsv
apk_static_index/hot_update_gongfa_homemake_skillcastbridge_lua_surface.tsv
apk_static_index/hot_update_gongfa_homemake_skillcastbridge_cpp2il_surface.tsv
apk_static_index/hot_update_gongfa_homemake_skillcastbridge_packet_fields.tsv
apk_static_index/hot_update_gongfa_homemake_skillcastbridge_formula_hits.tsv
apk_static_index/hot_update_gongfa_homemake_skillcastbridge_boundary_report.md
```

Current true run outputs `5` flow rows, `100` Lua surface rows, `35` Cpp2IL surface rows, `14` `CM_FightByTargets` packet-field rows, and `0` formula/state term hits. The route is `FightNetLogic.SendFightMessage -> GameSystem...SkillCastBridge.lua -> LuaBridge.Skill.SkillCastBridge -> CM_CheckFightByTargets -> CM_FightByTargets`. Lua contexts split across `fight_request_target_selection=36`, `lua_wrapper=20`, `activity_effect_range_preview=16`, `debug_or_range_preview=14`, `debug_toggle=8`, and `other=6`. Cpp2IL shows `Line/Rectangle/Sector/CircleCastAll` returning target-id arrays and temp `Collider/Raycast/Vector3` fields. Verdict: `supports_geometry_target_selection_not_formula_authority=true`.

Stage/Star timeline boundary probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-stage-star-timeline-boundary-probe
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_flow.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_surface.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_authority_terms.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_checks.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_report.md
```

Current true run outputs `6` flow rows, `58` focused surface rows, `4` timeline hit-timing rows, and `0` nearby formula/authority terms. The route is `FightMgr.ReleaseSkillExecute(jie,star) -> _TempSkillParam.Stage/Star -> StateSkill.Enter -> tParam.stage/star -> SkillActor.ReleaseSkill -> SkillBase.Start -> UpdateTimelineData -> SkillConfig.GetTimelineIdBySkillId`. `PreLoadMgr.AddEffectPathList4User` uses `skillVo.jie/star` for timeline asset preload. `SkillBase.UpdateTimelineData` loads `q_hurt_events/Cfg_Hurts`, so stage/star can affect selected timeline and hit-frame timing; current focused scan does not find nearby `FightResult/BuffResource/xianEffectMap/featureGroup/damage` authority terms. Verdict: `supports_stage_star_timeline_selection_not_formula_authority=true`.

Stage/Star timeline config resolution probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-stage-star-timeline-config-probe
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_config_rules.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_config_resolution.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_config_files.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_config_checks.tsv
apk_static_index/hot_update_gongfa_homemake_stage_star_timeline_config_report.md
```

Current true run outputs `6` runtime rule rows, `10685` skill timeline resolution rows, `1195` unique timeline ids, `1189` existing timeline Lua files, `6` missing timeline files, and `1174` decoded timeline files with clip/effect/hit-frame summaries. Context counts: `default=7094`, `max_rank=703`, `star_sword=722`, `star_celestial=722`, `star_demon=722`, `star_evil=722`. `SkillConfig.GetTimelineIdBySkillId` maps positive `star` to `jian/xian/mo/sha_timelineId`; stage-only switching uses `maxRankTimelineId` only when `stage >= CHANGE_TIMELINE_MIXRANK` (`6`). Verdict: `supports_config_resolution_to_timeline_playback_not_formula_authority=true`.

Timeline hurt/display projection probe:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-timeline-hurt-projection-probe
apk_static_index/hot_update_gongfa_homemake_timeline_hurt_projection_flow.tsv
apk_static_index/hot_update_gongfa_homemake_timeline_hurt_projection_surface.tsv
apk_static_index/hot_update_gongfa_homemake_timeline_hurt_projection_samples.tsv
apk_static_index/hot_update_gongfa_homemake_timeline_hurt_projection_formula_terms.tsv
apk_static_index/hot_update_gongfa_homemake_timeline_hurt_projection_checks.tsv
apk_static_index/hot_update_gongfa_homemake_timeline_hurt_projection_report.md
```

Current true run outputs `6` flow rows, `33` focused surface rows, `80` timeline hit-frame samples, and `0` formula-authority terms in focused excerpts. Chain: `q_hurt_events/Cfg_Hurts -> SkillBase.SetSM_FightResult -> HurtData:SetData -> HurtFrameVo.Add4HurtDataListDic` or `bullet:AddHurtData -> HurtFrameVo.CheckHurt/ExecuteHurtDataList`. `FightResultVO.damage/damageView/recoverHp/damageReflect/mpAddDamage/mpAddDamageView` are server result fields split by `percent*0.01`; `damageTimes` can drive visual separation. Verdict: `supports_timeline_hurt_projection_not_formula_authority=true`.

Fight Result packet-family decoder probe:

```text
POST /api/fanxiu/resources/hot-update/fight-result-family-decoder-probe
apk_static_index/hot_update_fight_result_family_decoder_family.tsv
apk_static_index/hot_update_fight_result_family_decoder_schema.tsv
apk_static_index/hot_update_fight_result_family_decoder_handlers.tsv
apk_static_index/hot_update_fight_result_family_decoder_report.md
```

Current true run outputs `4` family rows, `21` schema rows, and `9` handler rows. Packet ids: `SM_FightResult=60005`, `SM_FightResultTalisman=60041`, `SM_FightResultFunnel=60054`, `SM_FightResultPet=60055`. Talisman/Pet/Funnel inherit common `SM_FightResult`; Funnel additionally reads `buffId` first. Decode order for runtime samples is packet id -> optional variant-owned fields -> common `casterId/lockId/skillId/results/delayTime` -> nested `FightResultVO` rows. Verdict: `supports_result_family_decoder_map=true`.

Socket primitive decoder probe:

```text
POST /api/fanxiu/resources/hot-update/socket-primitive-decoder-probe
apk_static_index/hot_update_socket_primitive_decoder_rules.tsv
apk_static_index/hot_update_socket_primitive_decoder_flow.tsv
apk_static_index/hot_update_socket_primitive_decoder_evidence.tsv
apk_static_index/hot_update_socket_primitive_decoder_report.md
```

Current true run outputs `9` primitive rules, `5` flow rows, and `14` evidence rows, all found. Lua `BaseMessage` reads values from `SocketPoolData` typed pools; `SocketManager.ReceiveSocketMessage` receives typed lists and per-message pool lengths from C#; C#/IL2CPP `PoolMessageManage` fills `CCSocketPoolData` after parsing raw bytes through `LusuoStreamQuick`. Verdict: `supports_typed_pool_decoder_strategy=true`. Practical runtime direction: observe typed pool arguments first; raw TCP decoding remains below the `LusuoStreamQuick/PoolMessageManage` layer.

Socket raw decoder outline probe:

```text
POST /api/fanxiu/resources/hot-update/socket-raw-decoder-probe
apk_static_index/hot_update_socket_raw_decoder_frames.tsv
apk_static_index/hot_update_socket_raw_decoder_primitives.tsv
apk_static_index/hot_update_socket_raw_decoder_evidence.tsv
apk_static_index/hot_update_socket_raw_decoder_report.md
```

Current true run outputs `6` frame rows, `7` primitive rows, `12` evidence rows, all found, with `5` `ReadInt` calls in evidence. Verdict: `supports_raw_socket_decoder_outline=true`. Static outline: outer receive uses 4-byte `ByteUtil.ReadInt` length; send symmetry is `WriteNoCompress(totalLength) -> headerStream(sn, proId) -> bodyStream -> BeginSend`; receive constructs `LusuoStreamQuick(..., isMsgCompress)`, reads a forced non-compressed first int, then normal ints for the inner header before `PoolMessageManage.IsLuaMessage/read`. `ReadIntCompress` is varint/zigzag-like but still needs a packet fixture for final byte-perfect reimplementation.

Socket compressed-int candidate codec probe:

```text
POST /api/fanxiu/resources/hot-update/socket-compressed-int-codec-probe
apk_static_index/hot_update_socket_compressed_int_codec_rules.tsv
apk_static_index/hot_update_socket_compressed_int_codec_samples.tsv
apk_static_index/hot_update_socket_compressed_int_codec_evidence.tsv
apk_static_index/hot_update_socket_compressed_int_codec_report.md
```

Current true run outputs `5` rule rows, `16` sample rows, and `5` evidence rows, all found; all `16` signed-int roundtrip samples pass. Verdict: `supports_candidate_compressed_int_codec=true`. Candidate codec: `u=((value<<1)^(value>>31))&0xffffffff`, then 7-bit varint continuation bytes; decode with `(u>>1) ^ -(u&1)`. This is executable and self-consistent with static `WriteIntCompress/revertSign32/ReadInt` evidence, but remains a candidate until a real packet fixture confirms the exact byte stream.

Socket capture fixture codec calibration probe:

```text
POST /api/fanxiu/resources/hot-update/socket-capture-fixture-codec-calibration-probe
apk_static_index/hot_update_socket_capture_fixture_codec_calibration_frames.tsv
apk_static_index/hot_update_socket_capture_fixture_codec_calibration_protocol_counts.tsv
apk_static_index/hot_update_socket_capture_fixture_codec_calibration_sensitive_keys.tsv
apk_static_index/hot_update_socket_capture_fixture_codec_calibration_report.md
```

Current true run uses `tcp_captures/frxx_tcp_20260524_224855.codeyun_decoded.json` and outputs `86` frame rows, `60` protocol-count rows, `6` sensitive-key rows, and `86/86` frame body-length matches. Verdict: `supports_capture_fixture_codec_calibration=true`. This confirms the compressed-int candidate against existing decoded capture metadata using only lengths, protocol ids, names, directions, and redacted key counts; parsed payload values are not exported. The selected fixture has `0` FightResult-family frames, so combat-result numeric attribution still needs a focused sample later.

Combat formula authority contrast probe:

```text
POST /api/fanxiu/resources/hot-update/combat-formula-authority-contrast-probe
apk_static_index/hot_update_combat_formula_authority_contrast.tsv
apk_static_index/hot_update_combat_formula_authority_evidence.tsv
apk_static_index/hot_update_combat_formula_authority_contrast_report.md
```

Current true run outputs `4` contrast rows and `5` evidence rows. Verdict: `supports_combat_formula_authority_contrast=true`. Read it as a guardrail: `BLLD` has local formula evidence (`329` formula rows, `BLLDFightComponent:AddDamageResult`) but still returns final rewards/state via server packets; the `GongFaHomeMake`/main-fight line has no visible client `SM_FightResult` producer and uses server `FightResultVO` values for display projection. Do not infer main combat formula ownership from BLLD's local mini-game formula surface.

Cpp2IL main-combat formula-surface probe:

```text
POST /api/fanxiu/resources/hot-update/cpp2il-main-combat-formula-surface-probe
apk_static_index/hot_update_cpp2il_main_combat_formula_surface_roles.tsv
apk_static_index/hot_update_cpp2il_main_combat_formula_surface_hits.tsv
apk_static_index/hot_update_cpp2il_main_combat_formula_surface_report.md
```

Current true run outputs `6` role rows and `5` formula-term hit rows; all 5 hits are `ShowSkillDamageRangeDebug` in `SkillCastBridge`/wrapper ISIL, so `strong_hits_outside_known_geometry=0`. Verdict: `supports_cpp2il_main_combat_formula_surface_boundary=true`. Cpp2IL exposes battle state, SkillCastBridge geometry, and timeline hurt metadata, but not a strong main-combat local formula surface.

Typed-pool runtime observation plan:

```text
POST /api/fanxiu/resources/hot-update/typed-pool-runtime-observation-probe
apk_static_index/hot_update_typed_pool_runtime_observation_capture_points.tsv
apk_static_index/hot_update_typed_pool_runtime_observation_reconstruction.tsv
apk_static_index/hot_update_typed_pool_runtime_observation_sample_shape.tsv
apk_static_index/hot_update_typed_pool_runtime_observation_privacy.tsv
apk_static_index/hot_update_typed_pool_runtime_observation_report.md
```

Current true run outputs `4` capture-point rows, `7` reconstruction rows, `8` sample-shape rows, and `5` privacy rows, using `21` FightResult schema rows and `9` primitive-pool rules. Verdict: `supports_typed_pool_runtime_observation_plan=true`. This is a static/non-invasive plan only; it does not hook, inject, patch, or modify the game/APK. If a later runtime tool is used, capture only allowlisted `SM_FightResult` family typed-pool segments, reconstruct fields by replaying `BaseMessage` reads, and hash/truncate live ids while dropping credentials, login payloads, sessions, and unrelated packet streams.

FUNNEL result packet field drill-down:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-mechanism-result-packet-probe
apk_static_index/hot_update_gongfa_homemake_mechanism_result_packet_fields.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_result_packet_flow.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_result_hurtdata_mapping.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_result_packet_report.md
```

Current true run for default `buff_id=386001010` outputs `33` packet fields, `6` flow rows, `16` HurtData mapping rows, and `10` `FightResultVO` value fields. Verdict: `SM_FightResultFunnel` extends common `SM_FightResult`, `FightResultVO` has no buff/config id, and `SkillBase` projects server result values into `HurtData`. The useful fields for runtime observation are `buffId`, top-level `skillId/results`, and per-target `FightResultVO.targetId/fightEffect/damage/damageView/mpAddDamage/mpAddDamageView/damageTimes/recoverHp/damageReflect/mpDamageAbsorb`.

FUNNEL result producer/write-surface drill-down:

```text
POST /api/fanxiu/resources/hot-update/gongfa-homemake-mechanism-result-producer-probe
apk_static_index/hot_update_gongfa_homemake_mechanism_result_producer_surface.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_result_producer_checks.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_result_producer_cpp2il_hits.tsv
apk_static_index/hot_update_gongfa_homemake_mechanism_result_producer_report.md
```

Current true run for default `buff_id=386001010` outputs `22` focused Lua surface rows, `9` check rows, `0` potential client producer hits, `4` server-to-client registration/handler hits, `16` client consumer hits, `2` generated serializer hits, and `0` Cpp2IL named producer hits. Verdict: visible Lua registers and consumes `SM_FightResult/SM_FightResultFunnel`, but does not visibly produce, send, or construct the `FightResultVO` list. `writeList(self.results)` exists only as generated packet serializer surface and is not enough to prove client send ownership. Treat `SM_FightResultFunnel.results` as server-produced/client-consumed until runtime packet samples or deeper native/server-adjacent evidence say otherwise.

Wiki-facing API and UI:

```text
GET /api/fanxiu/resources/gongfa/homemake-buff-parameter-semantics?gongfa_id=400101
GET /api/fanxiu/resources/gongfa/homemake-buff-parameter-semantics?limit=200
GET /api/fanxiu/resources/gongfa/homemake-xianshu-formula-catalog?gongfa_id=400101&limit=200&star=1
```

Current true run for `gongfa_id=400101` returns `candidate_rows=21`, `groups=21`, `links=21`, `unique_buff_ids=21`, and only `buffContinued=1` among parameter fields. The global no-`gongfa_id` query returns all `55` groups, `135` candidate rows, and `55` unique BuffResource ids. Frontend helper is `getFanxiuGongfaHomeMakeBuffParameterSemantics(...)` in `frontend/src/api/fanxiu.ts`; `gongfaId` is optional so the same helper backs selected-card detail and the global overview.

`/fanxiu/wiki` renders grouped mechanism rows in two places: selected Gongfa detail as the `仙书机制` section below `仙书公式`, and the Gongfa tab's `仙书机制总览` strip above the left/right workspace. Link chips are intentionally human-facing (`重数`, `技能`, `表现文件`) instead of raw table names. The selected-card section has a local client-side filter over mechanism name, side-feature name, description, tags, buff ids, and linked skill/link text; screenshot verification with query `洞微` shows `2/21` visible groups and `洞微剑气` as the first result. The global overview can search across all 6 XianShu Gongfa and all 55 semantic groups; browser smoke with query `破妄` narrows to `1/55` group (`洞微剑气`). Cross-table links carry `target_gongfa_id` when a `GongfaSkill`/GongfaJie row can be safely resolved to a catalog card; clickable chips refresh the wiki search to that id, so clicking the `破妄剑意` relation lands on `316104 心法·破妄剑意` with the left list and right detail in sync.

Selected XianShu Gongfa detail now also renders `仙书公式` between `功法效果` and `仙书机制`. Frontend helper is `getFanxiuGongfaHomeMakeXianShuFormulaCatalog(...)`; it calls the GET query layer with `limit=200, star=1` and displays group samples plus tags such as `50 阶`, `51 星级行`, `FG 3000101`, and linked Gongfa name. Browser smoke against `http://127.0.0.1:5173/fanxiu/wiki?tab=gongfa&id=400101&q=400101` confirms the panel is visible and readable; screenshot is `ui_checks/wiki_homemake_xianshu_formula_panel.png`.

`GongFaView` now has a dedicated page snapshot report:

```text
apk_static_index/hot_update_gongfa_view_snapshot_flow.tsv
apk_static_index/hot_update_gongfa_view_snapshot_edges.tsv
apk_static_index/hot_update_gongfa_view_snapshot_report.md
```

Current true run: `115` evidence rows and `14` semantic edges. Main conclusion: the client first builds the local Gongfa catalog from `Gongfa_Gongfa/Gongfa_GongfaPin` into `GongFaNewData.gongFaDic` and wraps each static row as `GongFaVo(cfg)`. `SM_GongFaView` is a page-state snapshot carrying `actives / xinFaPutUpList / fazePutUpList / skillList / programVOList`; it does not directly carry a full `GongFaItemVO` list. Learned/upgraded Gongfa instance state overlays the static catalog through `GongFaItemVO.baseId -> GongFaNewData.gongFaDic -> GongFaVo.vo`, with confirmed incremental sources `SM_GongFaLearn`, `SM_GongFaUpgrade`, and `SM_GongFaUpgradeTimes`.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-view-snapshot-probe
```

`GongFaSaveProgram/XinFaPutUp` now has a dedicated saved-scheme and equip-slot report:

```text
apk_static_index/hot_update_gongfa_program_equip_packets.tsv
apk_static_index/hot_update_gongfa_program_equip_flow.tsv
apk_static_index/hot_update_gongfa_program_equip_edges.tsv
apk_static_index/hot_update_gongfa_program_equip_report.md
```

Current true run: `12` protocol/VO rows, `206` evidence rows, and `11` semantic edges. Main conclusion: saving a Gongfa scheme submits `CM_GongFaSaveProgram.programVO -> GongFaProgramVO.skillList`, whose `SkillProgramVO` preserves both `homeMakeVO` for full self-created display data and `skillInfoVO` for lightweight slot references. 心法上阵 submits `CM_XinFaPutUp.putUpList -> XinFaVO(idx, xinFaId=SkillInfoVO)`, and direct 神通/绝招 replacement uses `CM_ReplaceSkill(skillId,type,makeId,groupId,index)`. The same `SkillInfoVO.makeId` identity points back to `GongFaHomeMakeVO.skillCommonVO.id`; `HomeMakeXinFaVO.effectMap/xianEffectMap` are client-readable maps, but current write-side evidence does not show the client generating complete effect maps.

API for rerun:

```text
POST /api/fanxiu/resources/hot-update/gongfa-program-equip-probe
```

## Hot-Update/Gameplay Findings

BlueStarSea/淬灵域:

- Main config and runtime reports live in `apk_static_index/hot_update_bluestarsea_*.md`.
- The semantic packet graph lives in `hot_update_bluestarsea_protocol_semantics_report.md`; it joins packet id/name, operation, role, NetLogic function, handler, model event, and state writes.
- Client operations are mostly intent-only requests: `faqiId`, `treeId`, `planId`, `starTreeId`, `items`, `times`, `name`, `itemPriority`.
- Server回包 writes final energy/reward/faqi/tree/star/wake/plan state.
- `Star` groups and attr curves have been decoded for `鸿蒙洗髓 / 万源铸本 / 灵海涤魂 / 九霄荡魔 / 星海淬灵`.
- BlueStarSea faze effects connect to `FazeResource/FazeEffectResource`, but many actual algorithms are not visible in Lua and appear server-side or lower-level.

BLLD/百炼轮回:

- Runtime, finish flow, reward, combat, level, and authority-boundary reports exist under `apk_static_index/hot_update_blld_*.md`.
- The semantic packet graph lives in `hot_update_blld_protocol_semantics_report.md`; it joins packet id/name, operation, role, handler, client summary fields, and server state sinks.
- `CM_BlldFinishAndReward` submits local combat summary (`levelId/findReward/success/passRate`), but final display/sync depends on `SM_BlldFinishAndReward` and subsequent sync.

Faze/法则:

- `SM_FazeEffect(fazeId/effectType/num/reason)` is treated as a server rule notification.
- The semantic packet graph lives in `hot_update_faze_protocol_semantics_report.md`; it marks service push packets separately from client-intent request/response pairs and expands `SM_FazeEffect` rule fields.
- Lua mostly renders tips/effects from `fazeId + reason/num`; broad algorithmic semantics are not fully present in current Lua.
- Reports: `hot_update_faze_authority_boundary_report.md`, `hot_update_faze_effect_catalog_report.md`, `hot_update_faze_effect_lua_usage_report.md`, `hot_update_faze_source_semantics_report.md`.

## Wiki/Catalog State

The Fanxiu wiki goal is to make parsed game objects readable, not just dump raw table rows.

Current important catalog behavior:

- Gongfa cards support grouped duplicate display and right-side full detail expansion.
- Gongfa detail now loads the static `GongFaHomeMake` renderer rows into a game-style `功法效果` panel. It defaults to `include_inactive=false` so the normal view shows active/base text rather than duplicated locked templates.
- XianShu Gongfa detail now loads grouped display-formula samples into a `仙书公式` panel directly below `功法效果`. This uses `XianjieGongfaStar.describe + star params + SideFeatureJie.param` and should be treated as client-visible formula text, not combat authority.
- XianShu Gongfa detail now also loads grouped `BuffResource` parameter semantics into a `仙书机制` panel, with local filtering for mechanism/skill/tag lookup and safe relation jumps when `target_gongfa_id` is known. This is a readable mechanism catalog for timing/lifecycle/cross-table context; it is not proof of complete formula ownership.
- The Gongfa tab now also loads an all-XianShu `仙书机制总览` from the same optional-`gongfa_id` API, so agents/users can search all 55 semantic groups before selecting a specific card.
- Rich-text `<color>` tags are converted into readable inline styles in the UI.
- Exact duplicate/grouping work has been done for repeated text IDs and same-name items.
- Raw labels should generally stay hidden unless needed for debugging.
- For “游戏里一样直观”, prefer visual card/detail panels over plain TSV-like listings.

User preference from the UI iteration:

- Show full right-side effect text; avoid nested scroll inside the detail when the page itself can scroll.
- Put useful tag chips near the top only when they help navigation; remove raw/original technical labels from normal view.
- Merge by same display name when exact duplicate entries only differ by `lang.lua:<id>`; allow selecting IDs only when content differs.

## Things Already Learned Not To Overdo

- Cheat Engine against `MuMuNxMain.exe` is not the best route for high-level Unity/IL2CPP game logic. It sees the emulator host process, not a clean C#/Lua object model.
- Clash Verge connection view is useful for domain visibility, not full protocol decoding.
- Not all traffic is HTTP GET/POST. Core gameplay uses binary socket after login/server selection.
- Audio resources are likely Wwise/bank style; do not force generic image/audio parsing. Use external Wwise tools later if audio becomes important.
- Some exported texture previews look meaningless because they are atlases, compressed/packed data, masks, or non-human-facing textures. Do not assume every Unity Texture2D is a useful icon.
- `global-metadata.dat` is useful for names/signatures, but not enough for method bodies. Use Cpp2IL/IL2CPP tooling when control flow matters.

## Maintenance Rule

Update this document when any of these change:

- A new external reverse tool is installed or a tool path changes.
- A new canonical export/report directory is created.
- A major chain is proven, especially login, server-list, socket, resource-download, Lua bridge, or authority boundary.
- CodeYun adds or renames Fanxiu backend modules, API routes, or frontend wiki behavior.

For detailed chronological discoveries, append to:

```text
D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1\frxx_analysis_exports\parsed_configs\gongfa_catalog\gongfa_runtime_notes.md
```

For compact agent handoff, update this file.

## Next Best Step

Use the Gongfa semantic map to pick the next narrow chain and deepen it beyond packet-level semantics:

- `386001010 洞微剑气` ownership, XianShu display formula parameters/catalog, wiki-facing `仙书公式` panel, formula usage/authority-boundary tracing, battle-side state-consumer tracing, XianShu cast-request boundary tracing, XianShu cast-ack consumer tracing, SkillCastBridge geometry/target-selection boundary tracing, Stage/Star timeline boundary/config tracing, timeline hurt/display projection tracing, Fight Result packet-family decoder mapping, socket primitive typed-pool decoder mapping, typed-pool runtime observation planning, raw socket decoder outline mapping, compressed-int candidate codec derivation, existing capture fixture codec calibration, combat formula authority contrast, Cpp2IL main-combat formula-surface scan, inherited FUNNEL result packet fields, and `SM_FightResultFunnel.results` producer/write surface are now covered by `hot_update_gongfa_homemake_mechanism_ownership_report.md`, `hot_update_gongfa_homemake_mechanism_formula_surface_report.md`, `hot_update_gongfa_homemake_xianshu_formula_catalog_report.md`, `hot_update_gongfa_homemake_xianshu_formula_usage_report.md`, `hot_update_gongfa_homemake_xianshu_battle_state_usage_report.md`, `hot_update_gongfa_homemake_xianshu_cast_request_boundary_report.md`, `hot_update_gongfa_homemake_xianshu_cast_ack_consumer_report.md`, `hot_update_gongfa_homemake_skillcastbridge_boundary_report.md`, `hot_update_gongfa_homemake_stage_star_timeline_report.md`, `hot_update_gongfa_homemake_stage_star_timeline_config_report.md`, `hot_update_gongfa_homemake_timeline_hurt_projection_report.md`, `hot_update_fight_result_family_decoder_report.md`, `hot_update_socket_primitive_decoder_report.md`, `hot_update_typed_pool_runtime_observation_report.md`, `hot_update_socket_raw_decoder_report.md`, `hot_update_socket_compressed_int_codec_report.md`, `hot_update_socket_capture_fixture_codec_calibration_report.md`, `hot_update_combat_formula_authority_contrast_report.md`, `hot_update_cpp2il_main_combat_formula_surface_report.md`, `hot_update_gongfa_homemake_mechanism_result_packet_report.md`, and `hot_update_gongfa_homemake_mechanism_result_producer_report.md`. The next narrow step should look for deeper native/server-adjacent runtime formula evidence, collect/use a focused FightResult-family sample if one exists, or pick a different small Gongfa chain; do not repeat broad `BuffResource` scans, formula display catalog scans, XianjieGongfaStar usage scans, xianEffectMap battle-state scans, cast-request/cast-ack packet boundary scans, SkillCastBridge geometry scans, Stage/Star timeline/config scans, timeline hurt projection scans, Fight Result decoder mapping, socket primitive typed-pool mapping, typed-pool runtime observation planning, raw socket decoder outline mapping, compressed-int candidate codec derivation, existing fixture codec calibration, combat authority contrast, Cpp2IL main-combat formula-surface scan, or the Lua packet writer scan.
- `Runtime packet sample plan for SM_FightResultFunnel/BuffResultVO` becomes the next evidence source only if exact numeric formula or per-hit result attribution is required. Do runtime sampling as observation only, with account/privacy data filtered; the relevant fields to log are now known (`buffId`, `skillId`, `FightResultVO.*`).
- `ChatShare href runtime behavior` only if the goal is to prove whether chat message bodies are actually clickable in the live client. Static Lua/PrefabBinder/TextEx bridge surfaces are now covered by `hot_update_gongfa_homemake_share_href_registration_gap_report.md` and do not close the entry; the next useful check would be runtime observation with a real received share message, not another pass over the same static files.
- `GongFaHomeMake share href registration gap` is now covered by `hot_update_gongfa_homemake_share_href_registration_gap_report.md`; revisit only if runtime observation proves chat body clicks work and we need to locate the hidden registration path.
- `GongFaHomeMake share href prefab` is now covered by `hot_update_gongfa_homemake_share_href_prefab_report.md`; revisit only if a later resource update changes the chat message prefab layout.
- `GongFaHomeMake share href` is now covered by `hot_update_gongfa_homemake_share_href_report.md`; revisit only for the final chat-label callback registration gap above.
- `ChatSharedItemContent/LingJieSkillItem` UI consumer is now covered by `hot_update_gongfa_homemake_share_ui_report.md`; revisit only if the frontend needs a dedicated chat-share UI viewer.
- `GongFaHomeMakeShareVO` packaging is now covered by `hot_update_gongfa_homemake_share_report.md`; revisit only if the frontend needs a dedicated chat-share viewer.
- `GongFaHomeMakePageList/HMFilterVO` is now covered by `hot_update_gongfa_homemake_page_list_report.md`; revisit only if the frontend needs a dedicated list/filter viewer.
- `GongFaHomeMakeCombine/ChangeName/Check/Exchange` is now covered by `hot_update_gongfa_homemake_mutation_ops_report.md`; revisit only if the frontend needs a dedicated mutation-ops viewer.
- Saved scheme and 心法上阵 are now covered by `hot_update_gongfa_program_equip_report.md`; revisit them only if the frontend needs deeper UI consumer mapping.
- Learn/Teach is now covered by `hot_update_gongfa_homemake_learn_teach_report.md`; revisit only if the frontend needs a dedicated consult/teach viewer.
- Record/Grid/LightUp is now covered by `hot_update_gongfa_homemake_record_grid_light_report.md`; revisit only if the frontend needs a dedicated dynamic-state viewer.

Keep the next pass narrow: source packet -> NetLogic handler -> Model/Data functions -> UI/render consumers -> report TSV/edges. Avoid expanding all 46 Gongfa operations at once.
