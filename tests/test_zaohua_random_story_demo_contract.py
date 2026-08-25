from pathlib import Path


SOURCE = Path("tools/zaohua_mod/RandomStoryDemo/RandomStoryDemoPlugin.cs")


def source_text() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_demo_offers_two_creation_routes():
    text = source_text()
    assert "原版仙途" in text
    assert "天道试炼·随机世界" in text
    assert "CreateRolePanel" in text
    assert "AddNewSave" in text


def test_demo_uses_external_atomic_state_storage():
    text = source_text()
    assert '"random-story-saves.json"' in text
    assert "Application.persistentDataPath" in text
    assert 'path + ".tmp"' in text
    assert 'path + ".bak"' in text


def test_map_plan_is_lianqi_sized_and_connected():
    text = source_text()
    assert "public int width = 28" in text
    assert "public int height = 28" in text
    assert 'public string tier = "炼气"' in text
    assert 'Add(map, "city", "仙缘城"' in text
    assert "map.roads.Add" in text


def test_route_is_committed_at_final_creation_boundary():
    text = source_text()
    assert "CommitCreationRoute()" in text
    assert 'AccessTools.TypeByName("CreateRolePanel"), "AddNewSave"' in text
    assert "private static bool Prefix() => DemoRuntime.CommitCreationRoute()" in text
    assert "if (!RouteConfirmed)" in text
    assert "已阻止进入原版" in text
    assert "BindInitializedActor" not in text


def test_route_choice_is_explicit_and_explains_when_the_map_changes():
    text = source_text()
    assert "GUI.ModalWindow" in text
    assert "RouteConfirmed" in text
    assert "完成角色创建后，第一章世界地图才会替换为随机地图" in text
    assert "更改模式" in text


def test_first_chapter_uses_native_random_map_before_loading():
    text = source_text()
    assert "RandomCreateMinMap(template, false)" in text
    assert '[HarmonyPatch(typeof(BsMapImpl), nameof(BsMapImpl.InitChapters))]' in text
    assert "EmbedXianyuanCity(originalDetail, generated)" in text
    assert "mapImpl.newMapSto = replacement" in text
    assert "UpdateMapInfoStoId(replacement.startPosition)" in text


def test_random_map_rejects_stale_story_npc_coordinates_safely():
    text = source_text()
    assert "RandomMapNpcPositionGuardPatch" in text
    assert "GetMapInfoSto(npcPos)" in text
    assert "GetPlaceSto(mapInfo.placeStoId)" in text
    assert "TrialCreationSmokeTestPatch" not in text
