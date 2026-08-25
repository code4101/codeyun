using BepInEx;
using BepInEx.Unity.Mono;
using HarmonyLib;
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEngine;

namespace Code4101.Zaohua.RandomStoryDemo
{
    [BepInPlugin(Guid, Name, Version)]
    public sealed class RandomStoryDemoPlugin : BaseUnityPlugin
    {
        public const string Guid = "code4101.zaohua.random-story-demo";
        public const string Name = "Code4101随机世界Demo";
        public const string Version = "0.3.0";
        private Harmony _harmony;
        internal static RandomStoryDemoPlugin Instance { get; private set; }

        private void Awake()
        {
            Instance = this;
            DontDestroyOnLoad(gameObject);
            _harmony = new Harmony(Guid);
            _harmony.PatchAll();
            Logger.LogInfo("[随机世界] Demo 0.3.0 已加载；启用显式模式确认和原生随机地图注入。");
        }

        private void OnDestroy()
        {
            _harmony?.UnpatchSelf();
            if (Instance == this) Instance = null;
        }

        private void Update() => DemoRuntime.Tick();
        private void OnGUI() => DemoRuntime.Draw();

        internal static void Log(string message) => Instance?.Logger.LogInfo(message);
        internal static void Error(string message) => Instance?.Logger.LogError(message);
    }

    internal enum StoryRoute
    {
        Official,
        RandomTrial,
    }

    [Serializable]
    internal sealed class SaveEnvelope
    {
        public int schemaVersion = 2;
        public List<RandomStoryState> characters = new List<RandomStoryState>();
    }

    [Serializable]
    internal sealed class RandomStoryState
    {
        public int schemaVersion = 1;
        public string characterId;
        public string actorId;
        public string route = "random-trial";
        public int mapSeed;
        public int generatorVersion = 1;
        public string createdAtUtc;
        public string mapPlanFile;
    }

    [Serializable]
    internal sealed class RandomMapPlan
    {
        public int schemaVersion = 1;
        public int generatorVersion = 1;
        public int seed;
        public int width = 28;
        public int height = 28;
        public string tier = "炼气";
        public string theme = "仙缘城郊";
        public int startX = 14;
        public int startY = 15;
        public int[] terrain;
        public List<MapPlace> places = new List<MapPlace>();
        public List<MapRoad> roads = new List<MapRoad>();
    }

    [Serializable]
    internal sealed class MapPlace
    {
        public string kind;
        public string name;
        public int x;
        public int y;
        public int danger;
    }

    [Serializable]
    internal sealed class MapRoad
    {
        public int from;
        public int to;
    }

    internal static class RandomMapGenerator
    {
        internal static RandomMapPlan Generate(int seed)
        {
            var random = new System.Random(seed);
            var map = new RandomMapPlan { seed = seed, terrain = new int[28 * 28] };
            for (var y = 0; y < map.height; y++)
            for (var x = 0; x < map.width; x++)
            {
                var border = x < 2 || y < 2 || x >= map.width - 2 || y >= map.height - 2;
                map.terrain[y * map.width + x] = border
                    ? (random.NextDouble() < 0.65 ? 2 : 1)
                    : (random.NextDouble() < 0.12 ? 1 : 0);
            }

            Add(map, "city", "仙缘城", 14, 14, 0);
            Add(map, "town", "青溪镇", 6 + random.Next(3), 7 + random.Next(3), 1);
            Add(map, "town", "栖霞镇", 20 + random.Next(3), 18 + random.Next(3), 1);
            Add(map, "village", "药农村", 4 + random.Next(3), 18 + random.Next(3), 1);
            Add(map, "village", "望山村", 18 + random.Next(3), 4 + random.Next(3), 1);
            Add(map, "village", "渡口村", 21 + random.Next(3), 11 + random.Next(3), 1);
            Add(map, "sect", "青岚观", 9 + random.Next(3), 3 + random.Next(2), 2);
            Add(map, "cave", "黑风洞", 3 + random.Next(3), 10 + random.Next(3), 2);
            Add(map, "cave", "赤岩窟", 22 + random.Next(3), 5 + random.Next(3), 3);
            Add(map, "tomb", "无名古冢", 12 + random.Next(4), 22 + random.Next(3), 3);

            for (var i = 1; i < map.places.Count; i++)
            {
                var nearest = 0;
                var distance = int.MaxValue;
                for (var j = 0; j < i; j++)
                {
                    var candidate = Math.Abs(map.places[i].x - map.places[j].x)
                        + Math.Abs(map.places[i].y - map.places[j].y);
                    if (candidate >= distance) continue;
                    distance = candidate;
                    nearest = j;
                }
                map.roads.Add(new MapRoad { from = i, to = nearest });
            }
            return map;
        }

        private static void Add(RandomMapPlan map, string kind, string name, int x, int y, int danger)
        {
            map.places.Add(new MapPlace { kind = kind, name = name, x = x, y = y, danger = danger });
        }
    }

    internal static class StoryStore
    {
        private static readonly JsonSerializerSettings JsonSettings = new JsonSerializerSettings
        {
            Formatting = Formatting.Indented,
        };

        internal static string Root => Path.Combine(Application.persistentDataPath, "Code4101.Tiandao", "RandomStoryDemo");
        internal static string IndexPath => Path.Combine(Root, "random-story-saves.json");

        internal static RandomStoryState BindCurrentActor(StoryRoute route, int? expectedCharacterId = null)
        {
            var actor = GameReflection.CurrentActor();
            var characterId = GameReflection.NestedText(actor, "fileSto", "characterId");
            if (string.IsNullOrWhiteSpace(characterId)) return null;
            if (expectedCharacterId.HasValue && characterId != expectedCharacterId.Value.ToString())
            {
                RandomStoryDemoPlugin.Log(
                    $"[随机世界] 等待新角色切换：expected={expectedCharacterId.Value}, current={characterId}");
                return null;
            }

            var envelope = Load();
            envelope.characters.RemoveAll(item => item.characterId == characterId);
            if (route == StoryRoute.Official)
            {
                AtomicWrite(IndexPath, JsonConvert.SerializeObject(envelope, JsonSettings));
                return null;
            }

            var seed = CreateSeed(characterId);
            var state = new RandomStoryState
            {
                characterId = characterId,
                actorId = GameReflection.MemberText(actor, "id"),
                mapSeed = seed,
                createdAtUtc = DateTime.UtcNow.ToString("O"),
            };
            Directory.CreateDirectory(Path.Combine(Root, "maps"));
            state.mapPlanFile = Path.Combine(Root, "maps", $"lianqi-{Safe(characterId)}-{seed}.json");
            AtomicWrite(state.mapPlanFile, JsonConvert.SerializeObject(RandomMapGenerator.Generate(seed), JsonSettings));
            envelope.characters.Add(state);
            AtomicWrite(IndexPath, JsonConvert.SerializeObject(envelope, JsonSettings));
            return state;
        }

        internal static RandomStoryState FindCurrentActor()
        {
            var actor = GameReflection.CurrentActor();
            var characterId = GameReflection.NestedText(actor, "fileSto", "characterId");
            if (string.IsNullOrWhiteSpace(characterId)) return null;
            var envelope = Load();
            var state = envelope.characters.Find(item => item.characterId == characterId && item.route == "random-trial");
            return state;
        }

        private static SaveEnvelope Load()
        {
            Directory.CreateDirectory(Root);
            if (!File.Exists(IndexPath)) return new SaveEnvelope();
            var json = File.ReadAllText(IndexPath);
            var value = JsonConvert.DeserializeObject<SaveEnvelope>(json);
            if (value == null || value.characters == null)
                throw new InvalidDataException("随机世界索引无法解析；为保护数据，已停止覆盖。");
            return value;
        }

        private static void AtomicWrite(string path, string content)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            var temporary = path + ".tmp";
            var backup = path + ".bak";
            File.WriteAllText(temporary, content);
            if (File.Exists(path)) File.Copy(path, backup, true);
            if (File.Exists(path)) File.Delete(path);
            File.Move(temporary, path);
        }

        private static int CreateSeed(string value)
        {
            unchecked
            {
                var hash = 17;
                foreach (var ch in value) hash = hash * 31 + ch;
                hash ^= Environment.TickCount;
                return hash == int.MinValue ? 4101001 : Math.Abs(hash);
            }
        }

        private static string Safe(string value)
        {
            foreach (var ch in Path.GetInvalidFileNameChars()) value = value.Replace(ch, '_');
            return value;
        }
    }

    internal static class RuntimeMapInjector
    {
        private const string FirstChapterMapId = "1";
        private const int LianqiRandomType = 1;
        private const int TargetValidCells = 560;

        internal static void Inject(BsMapImpl mapImpl, int chapterId)
        {
            if (mapImpl?.newMapCfg == null || mapImpl.newMapSto == null) return;
            if (mapImpl.newMapCfg.uuid != FirstChapterMapId) return;
            var state = StoryStore.FindCurrentActor();
            if (state == null) return;

            var originalDetail = Singleton<MapEditorManager>.Instance.LoadMapDetail(FirstChapterMapId);
            if (originalDetail?.mapCfg == null) throw new InvalidDataException("无法读取原版第一章地图模板。");

            var template = Singleton<MapEditorManager>.Instance.LoadMapDetail(FirstChapterMapId);
            template.mapCfg.mapType = 1;
            template.mapCfg.randomType = LianqiRandomType;
            template.mapCfg.validCnt = TargetValidCells;
            template.mapCfg.sceneId = 0;
            template.mapCfg.riverSceneId = 0;

            var previousRandomState = UnityEngine.Random.state;
            TbMapDetail generated;
            try
            {
                UnityEngine.Random.InitState(state.mapSeed);
                generated = Singleton<MapEditorManager>.Instance.RandomCreateMinMap(template, false);
            }
            finally
            {
                UnityEngine.Random.state = previousRandomState;
            }

            EmbedXianyuanCity(originalDetail, generated);
            var replacement = ToMapSto(generated, FirstChapterMapId);
            var actor = BsSaveDataImpl.NowActor;
            actor.mapStoList.RemoveAll(item => item.uuid == FirstChapterMapId);
            actor.mapStoList.Add(replacement);
            mapImpl.newMapCfg = generated.mapCfg;
            mapImpl.newMapSto = replacement;
            Singleton<TbPlayerImpl>.Instance.UpdateMapStoId(FirstChapterMapId);
            Singleton<TbPlayerImpl>.Instance.UpdateMapInfoStoId(replacement.startPosition);
            DemoRuntime.ShowInjected(state, generated.mapObjectStoList.Count, generated.placeStoList.Count);
            RandomStoryDemoPlugin.Log(
                $"[随机世界] 已注入真实地图：chapter={chapterId}, seed={state.mapSeed}, " +
                $"cells={replacement.mapInfoStoList.Count}, objects={replacement.mapObjectStoList.Count}, places={replacement.placeStoList.Count}");
        }

        private static TbMapSto ToMapSto(TbMapDetail detail, string uuid)
        {
            return new TbMapSto
            {
                uuid = uuid,
                size = detail.size,
                sceneId = detail.mapCfg.sceneId,
                riverSceneId = detail.mapCfg.riverSceneId,
                borderSize = detail.borderSize,
                isNeedRefresh = true,
                startPosition = detail.startPosition,
                mapInfoStoList = detail.mapInfoStoList,
                mapObjectStoList = detail.mapObjectStoList,
                placeStoList = detail.placeStoList,
                placeLogList = detail.placeLogList,
            };
        }

        private static void EmbedXianyuanCity(TbMapDetail original, TbMapDetail generated)
        {
            var sourceCity = original.placeStoList.FirstOrDefault(place => place.placeId == 213)
                ?? original.placeStoList.FirstOrDefault(place => place.GetName().Contains("仙缘城"));
            if (sourceCity == null) throw new InvalidDataException("原版地图中未找到仙缘城地点。");

            var sourceCells = original.mapInfoStoList
                .Where(cell => cell.placeStoId == sourceCity.id)
                .ToList();
            if (sourceCells.Count == 0) throw new InvalidDataException("仙缘城没有对应地图格。");

            var targetCenter = new MyVector2Int(generated.size.x / 2, generated.size.y / 2);
            var delta = targetCenter - sourceCity.centerPos;
            var targetPositions = new HashSet<MyVector2Int>(sourceCells.Select(cell => cell.pos + delta));
            var conflictingPlaceIds = new HashSet<int>(generated.mapInfoStoList
                .Where(cell => targetPositions.Contains(cell.pos) && cell.placeStoId != 0)
                .Select(cell => cell.placeStoId));
            generated.placeStoList.RemoveAll(place => conflictingPlaceIds.Contains(place.id));

            foreach (var cell in generated.mapInfoStoList.Where(cell => conflictingPlaceIds.Contains(cell.placeStoId)))
                cell.placeStoId = 0;

            var minX = targetPositions.Min(pos => pos.x) - 1;
            var maxX = targetPositions.Max(pos => pos.x) + 1;
            var minY = targetPositions.Min(pos => pos.y) - 1;
            var maxY = targetPositions.Max(pos => pos.y) + 1;
            generated.mapObjectStoList.RemoveAll(obj =>
                obj.pos.x >= minX && obj.pos.x <= maxX && obj.pos.y >= minY && obj.pos.y <= maxY);

            var cityId = generated.placeStoList.Count == 0 ? 1 : generated.placeStoList.Max(place => place.id) + 1;
            foreach (var source in sourceCells)
            {
                var target = generated.mapInfoStoList.FirstOrDefault(cell => cell.pos == source.pos + delta);
                if (target == null) continue;
                target.terrainId = source.terrainId;
                target.placeStoId = cityId;
                target.isFind = true;
                target.trigger = source.trigger;
                target.pass = source.pass;
            }

            var city = sourceCity.Clone();
            city.id = cityId;
            city.centerPos += delta;
            city.namePos += delta;
            city.placeLogId = 0;
            generated.placeStoList.Add(city);
            generated.startPosition = city.centerPos;

            var sourceMinX = sourceCells.Min(cell => cell.pos.x) - 1;
            var sourceMaxX = sourceCells.Max(cell => cell.pos.x) + 1;
            var sourceMinY = sourceCells.Min(cell => cell.pos.y) - 1;
            var sourceMaxY = sourceCells.Max(cell => cell.pos.y) + 1;
            var nextObjectId = generated.mapObjectStoList.Count == 0 ? 1 : generated.mapObjectStoList.Max(obj => obj.id) + 1;
            foreach (var sourceObject in original.mapObjectStoList.Where(obj =>
                         obj.pos.x >= sourceMinX && obj.pos.x <= sourceMaxX &&
                         obj.pos.y >= sourceMinY && obj.pos.y <= sourceMaxY))
            {
                var copy = sourceObject.Clone();
                copy.id = nextObjectId++;
                copy.pos += (Vector2)delta;
                copy.placeLogId = 0;
                generated.mapObjectStoList.Add(copy);
            }
        }
    }

    internal static class GameReflection
    {
        internal static object CurrentActor()
        {
            var type = AccessTools.TypeByName("BsSaveDataImpl");
            if (type == null) return null;
            var field = AccessTools.Field(type, "nowActor");
            if (field != null) return field.GetValue(null);
            var property = AccessTools.Property(type, "nowActor");
            return property?.GetValue(null, null);
        }

        internal static string NestedText(object root, string first, string second)
        {
            return MemberText(Member(root, first), second);
        }

        internal static string MemberText(object root, string name)
        {
            return Convert.ToString(Member(root, name));
        }

        private static object Member(object root, string name)
        {
            if (root == null) return null;
            var type = root.GetType();
            var field = AccessTools.Field(type, name);
            if (field != null) return field.GetValue(root);
            var property = AccessTools.Property(type, name);
            return property?.GetValue(root, null);
        }
    }

    internal static class DemoRuntime
    {
        internal static StoryRoute PendingRoute = StoryRoute.Official;
        internal static bool SelectorVisible;
        internal static bool RouteConfirmed;
        private static float _activateUntil;
        private static float _bannerUntil;
        private static string _banner;

        internal static bool CommitCreationRoute()
        {
            if (!RouteConfirmed)
            {
                _banner = "请先选择并确认开局模式，再完成角色创建。";
                _bannerUntil = Time.realtimeSinceStartup + 8f;
                return false;
            }

            try
            {
                var state = StoryStore.BindCurrentActor(PendingRoute);
                if (PendingRoute == StoryRoute.RandomTrial && state == null)
                    throw new InvalidOperationException("当前新角色尚未初始化，无法绑定天道试炼。请稍后重试。");

                if (state != null)
                    RandomStoryDemoPlugin.Log(
                        $"[随机世界] 最终建档已绑定天道试炼：character={state.characterId}, seed={state.mapSeed}");
                else
                    RandomStoryDemoPlugin.Log("[随机世界] 最终建档选择原版仙途。");
                return true;
            }
            catch (Exception ex)
            {
                RandomStoryDemoPlugin.Error($"[随机世界] 最终建档绑定失败：{ex}");
                _banner = "天道试炼绑定失败，已阻止进入原版。请重试或更改模式。";
                _bannerUntil = Time.realtimeSinceStartup + 12f;
                return false;
            }
        }

        internal static void BeginActivationProbe() => _activateUntil = Time.realtimeSinceStartup + 15f;

        internal static void ShowInjected(RandomStoryState state, int objectCount, int placeCount)
        {
            _activateUntil = 0f;
            _banner = $"天道试炼·随机世界  |  Seed {state.mapSeed}\n原生随机地图已加载：{placeCount}处地点，{objectCount}个地图物件";
            _bannerUntil = Time.realtimeSinceStartup + 15f;
        }

        internal static void Tick()
        {
            if (_activateUntil > 0f && Time.realtimeSinceStartup <= _activateUntil)
            {
                TryActivate();
            }
        }

        private static void TryActivate()
        {
            try
            {
                var state = StoryStore.FindCurrentActor();
                if (GameReflection.CurrentActor() == null) return;
                _activateUntil = 0f;
                if (state == null) return;
                _banner = $"天道试炼·随机世界  |  炼气地图种子 {state.mapSeed}\n地图蓝图已生成；功法、属性、装备和战斗仍使用原版机制";
                _bannerUntil = Time.realtimeSinceStartup + 15f;
                RandomStoryDemoPlugin.Log($"[随机世界] 试炼分支已激活，seed={state.mapSeed}");
            }
            catch (Exception ex)
            {
                _activateUntil = 0f;
                RandomStoryDemoPlugin.Error($"[随机世界] 激活失败：{ex}");
            }
        }

        internal static void Draw()
        {
            if (SelectorVisible)
            {
                if (!RouteConfirmed)
                {
                    var modalWidth = Mathf.Clamp(Screen.width * 0.48f, 720f, 980f);
                    var modalHeight = Mathf.Clamp(Screen.height * 0.34f, 300f, 390f);
                    var modal = new Rect(
                        (Screen.width - modalWidth) / 2f,
                        (Screen.height - modalHeight) / 2f,
                        modalWidth,
                        modalHeight);
                    GUI.ModalWindow(410102, modal, DrawRouteModal, "选择开局模式");
                }
                else
                {
                    DrawConfirmedRoute();
                }
            }

            if (_bannerUntil > Time.realtimeSinceStartup && !string.IsNullOrEmpty(_banner))
            {
                var style = new GUIStyle(GUI.skin.box)
                {
                    alignment = TextAnchor.MiddleCenter,
                    fontSize = 20,
                    wordWrap = true,
                };
                GUI.Box(new Rect(Screen.width / 2f - 310f, 28f, 620f, 76f), _banner, style);
            }
        }

        private static void DrawRouteModal(int windowId)
        {
            var title = new GUIStyle(GUI.skin.label)
            {
                alignment = TextAnchor.MiddleCenter,
                fontSize = Mathf.RoundToInt(Mathf.Clamp(Screen.height * 0.026f, 22f, 32f)),
                fontStyle = FontStyle.Bold,
                wordWrap = true,
            };
            var detail = new GUIStyle(GUI.skin.label)
            {
                alignment = TextAnchor.MiddleCenter,
                fontSize = Mathf.RoundToInt(Mathf.Clamp(Screen.height * 0.018f, 17f, 23f)),
                wordWrap = true,
            };
            var button = new GUIStyle(GUI.skin.button)
            {
                fontSize = Mathf.RoundToInt(Mathf.Clamp(Screen.height * 0.021f, 19f, 27f)),
                fontStyle = FontStyle.Bold,
                wordWrap = true,
            };

            GUILayout.Space(18f);
            GUILayout.Label("你要进入哪一条开局路线？", title, GUILayout.Height(48f));
            GUILayout.Label(
                "天道试炼会完整继承原版角色、功法、属性、装备与战斗。\n完成角色创建后，第一章世界地图才会替换为随机地图。",
                detail,
                GUILayout.Height(82f));
            GUILayout.Space(10f);
            GUILayout.BeginHorizontal();
            GUILayout.Space(24f);
            if (GUILayout.Button("原版仙途\n原版地图与剧情", button, GUILayout.Height(92f)))
            {
                PendingRoute = StoryRoute.Official;
                RouteConfirmed = true;
            }
            GUILayout.Space(18f);
            if (GUILayout.Button("天道试炼·随机世界\n随机地图 Demo", button, GUILayout.Height(92f)))
            {
                PendingRoute = StoryRoute.RandomTrial;
                RouteConfirmed = true;
            }
            GUILayout.Space(24f);
            GUILayout.EndHorizontal();
        }

        private static void DrawConfirmedRoute()
        {
            var width = Mathf.Clamp(Screen.width * 0.46f, 720f, 940f);
            var height = Mathf.Clamp(Screen.height * 0.105f, 104f, 132f);
            var left = (Screen.width - width) / 2f;
            var top = Screen.height - height - Mathf.Clamp(Screen.height * 0.11f, 112f, 150f);
            var label = PendingRoute == StoryRoute.RandomTrial
                ? "✓ 已选择：天道试炼·随机世界\n角色创建沿用原版；完成创建后进入随机地图"
                : "✓ 已选择：原版仙途\n地图与剧情保持原版";
            var style = new GUIStyle(GUI.skin.box)
            {
                alignment = TextAnchor.MiddleCenter,
                fontSize = Mathf.RoundToInt(Mathf.Clamp(Screen.height * 0.019f, 18f, 25f)),
                fontStyle = FontStyle.Bold,
                wordWrap = true,
            };
            GUI.Box(new Rect(left, top, width - 170f, height), label, style);
            if (GUI.Button(new Rect(left + width - 158f, top, 158f, height), "更改模式"))
                RouteConfirmed = false;
        }
    }

    [HarmonyPatch]
    internal static class CreateRoleEnablePatch
    {
        private static MethodBase TargetMethod() => AccessTools.Method(AccessTools.TypeByName("CreateRolePanel"), "OnEnable");
        private static void Postfix()
        {
            DemoRuntime.PendingRoute = StoryRoute.Official;
            DemoRuntime.RouteConfirmed = false;
            DemoRuntime.SelectorVisible = true;
        }
    }

    [HarmonyPatch]
    internal static class CreateRoleDisablePatch
    {
        private static MethodBase TargetMethod() => AccessTools.Method(AccessTools.TypeByName("CreateRolePanel"), "OnDisable");
        private static void Postfix() => DemoRuntime.SelectorVisible = false;
    }

    [HarmonyPatch]
    internal static class CreateRoleSavePatch
    {
        private static MethodBase TargetMethod() => AccessTools.Method(AccessTools.TypeByName("CreateRolePanel"), "AddNewSave");
        private static bool Prefix() => DemoRuntime.CommitCreationRoute();
    }

    [HarmonyPatch]
    internal static class GameStartPatch
    {
        private static MethodBase TargetMethod() => AccessTools.Method(AccessTools.TypeByName("GameManager"), "startNewGame");
        private static void Postfix()
        {
            DemoRuntime.SelectorVisible = false;
            DemoRuntime.BeginActivationProbe();
        }
    }

    [HarmonyPatch(typeof(BsMapImpl), nameof(BsMapImpl.InitChapters))]
    internal static class RandomMapInitChapterPatch
    {
        private static void Postfix(BsMapImpl __instance, int chapterId)
        {
            try
            {
                RuntimeMapInjector.Inject(__instance, chapterId);
            }
            catch (Exception ex)
            {
                RandomStoryDemoPlugin.Error($"[随机世界] 地图注入失败，保留原版地图：{ex}");
            }
        }
    }

    [HarmonyPatch(typeof(BsMapImpl), nameof(BsMapImpl.IsConsistentMapInfoSto))]
    internal static class RandomMapNpcPositionGuardPatch
    {
        private static bool Prefix(
            BsMapImpl __instance,
            MyVector2Int npcPos,
            int nameId,
            int buildId,
            ref bool __result)
        {
            if (StoryStore.FindCurrentActor() == null) return true;
            var mapInfo = __instance.GetMapInfoSto(npcPos);
            if (mapInfo == null)
            {
                __result = false;
                return false;
            }
            if ((nameId != 0 || buildId != 0) && __instance.GetPlaceSto(mapInfo.placeStoId) == null)
            {
                __result = false;
                return false;
            }

            // The generated map can invalidate fixed-story NPC coordinates. Returning
            // false asks the native placement code to choose a fresh compatible cell.
            return true;
        }
    }
}
