using BepInEx;
using HarmonyLib;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.UI;

namespace CodeYun.Zaohua.HelloWorld
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class HelloWorldPlugin : BaseUnityPlugin
    {
        public const string PluginGuid = "codeyun.zaohua.helloworld";
        public const string PluginName = "CodeYun Zaohua Hello World";
        public const string PluginVersion = "0.5.0";

        internal static HelloWorldPlugin Instance { get; private set; }

        private Harmony _harmony;
        private GameObject _overlay;

        private void Awake()
        {
            Instance = this;
            Logger.LogInfo("Hello World from CodeYun Zaohua Mod!");
            Logger.LogInfo($"Game version: {Application.version}");

            _harmony = new Harmony(PluginGuid);
            _harmony.PatchAll();
            Logger.LogInfo("Harmony lifecycle probes registered.");

            var existingCanvas = Resources.FindObjectsOfTypeAll<UICanvas>().FirstOrDefault();
            if (existingCanvas == null)
            {
                Logger.LogWarning("Existing official UICanvas was not found.");
            }
            else
            {
                Logger.LogInfo($"Existing official UICanvas found: {existingCanvas.name}");
                CreateGameOverlay(existingCanvas.transform.Find("canvas/System"), "plugin startup scan");
            }
        }

        private void OnDestroy()
        {
            _harmony?.UnpatchSelf();
        }

        internal void Log(string message) => Logger.LogInfo(message);

        internal void CreateGameOverlay(Transform systemLayer, string source)
        {
            if (systemLayer == null)
            {
                Logger.LogWarning($"Official System layer is not ready from {source}.");
                return;
            }

            if (_overlay != null)
            {
                Destroy(_overlay);
            }

            _overlay = new GameObject("CodeYun.Zaohua.HelloWorld.Panel");
            _overlay.layer = systemLayer.gameObject.layer;
            _overlay.transform.SetParent(systemLayer, false);

            var panelRect = _overlay.AddComponent<RectTransform>();
            panelRect.anchorMin = new Vector2(0f, 1f);
            panelRect.anchorMax = new Vector2(0f, 1f);
            panelRect.pivot = new Vector2(0f, 1f);
            panelRect.anchoredPosition = new Vector2(24f, -24f);
            panelRect.sizeDelta = new Vector2(560f, 120f);

            var background = _overlay.AddComponent<Image>();
            background.color = new Color(0.03f, 0.06f, 0.08f, 0.96f);
            background.raycastTarget = false;

            var textObject = new GameObject("Message");
            textObject.layer = _overlay.layer;
            textObject.transform.SetParent(_overlay.transform, false);
            var textRect = textObject.AddComponent<RectTransform>();
            textRect.anchorMin = Vector2.zero;
            textRect.anchorMax = Vector2.one;
            textRect.offsetMin = new Vector2(22f, 14f);
            textRect.offsetMax = new Vector2(-22f, -14f);

            var text = textObject.AddComponent<Text>();
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.fontSize = 24;
            text.alignment = TextAnchor.MiddleLeft;
            text.color = new Color(0.18f, 1f, 0.72f, 1f);
            text.raycastTarget = false;
            text.text = "CodeYun Zaohua Mod\nHello World · Game UI 0.5.0";

            _overlay.transform.SetAsLastSibling();
            Logger.LogInfo($"Game overlay attached from {source} to official UI layer: {systemLayer.name}");
        }
    }

    [HarmonyPatch(typeof(Main), "Awake")]
    internal static class MainAwakePatch
    {
        private static void Prefix() => HelloWorldPlugin.Instance.Log("Probe: Main.Awake");
    }

    [HarmonyPatch(typeof(GameManager), nameof(GameManager.GameStart))]
    internal static class GameStartPatch
    {
        private static void Prefix() => HelloWorldPlugin.Instance.Log("Probe: GameManager.GameStart");
    }

    [HarmonyPatch(typeof(UIMgr), "Awake")]
    internal static class UIMgrAwakePatch
    {
        private static void Prefix() => HelloWorldPlugin.Instance.Log("Probe: UIMgr.Awake");
    }

    [HarmonyPatch(typeof(UICanvas), "Awake")]
    internal static class UICanvasAwakePatch
    {
        private static void Postfix(UICanvas __instance)
        {
            HelloWorldPlugin.Instance.Log("Probe: UICanvas.Awake");
            HelloWorldPlugin.Instance.CreateGameOverlay(__instance.transform.Find("canvas/System"), "UICanvas.Awake");
        }
    }

    [HarmonyPatch(typeof(StartPanel), nameof(StartPanel.ShowMe))]
    internal static class StartPanelShowPatch
    {
        private static void Postfix()
        {
            HelloWorldPlugin.Instance.Log("Probe: StartPanel.ShowMe");
            HelloWorldPlugin.Instance.CreateGameOverlay(
                MonoSingleton<UIMgr>.Instance.GetLayerFather(UI_Layer.System),
                "StartPanel.ShowMe");
        }
    }
}
