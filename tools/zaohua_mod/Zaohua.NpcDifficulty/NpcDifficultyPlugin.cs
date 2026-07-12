using BepInEx;
using BepInEx.Unity.Mono;
using HarmonyLib;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEngine;
using UnityEngine.UI;

namespace CodeYun.Zaohua.NpcDifficulty
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class NpcDifficultyPlugin : BaseUnityPlugin
    {
        public const string PluginGuid = "codeyun.zaohua.npcdifficulty";
        public const string PluginName = "CodeYun Zaohua NPC Difficulty";
        public const string PluginVersion = "0.1.0";

        private Harmony _harmony;

        private void Awake()
        {
            _harmony = new Harmony(PluginGuid);
            _harmony.PatchAll();
            Logger.LogInfo("NPC difficulty patches registered.");
        }

        private void OnDestroy()
        {
            _harmony?.UnpatchSelf();
        }
    }

    internal static class NpcDifficultyState
    {
        private const string SaveKey = "CodeYun.NpcAttributeMultiplier";
        private const string SpeedSaveKey = "CodeYun.NpcSpeedMultiplierEnabled";
        private const float DefaultMultiplier = 1f;
        private const float MinimumMultiplier = 1f;
        private const float Step = 1f;

        internal static float GetMultiplier()
        {
            var actor = BsSaveDataImpl.nowActor;
            var values = actor?.fileSto?.Global_valDic;
            if (values == null || !values.TryGetValue(SaveKey, out var text))
            {
                return DefaultMultiplier;
            }

            return float.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
                ? Mathf.Max(Mathf.Round(value), MinimumMultiplier)
                : DefaultMultiplier;
        }

        internal static float Change(int direction)
        {
            return SetMultiplier(GetMultiplier() + direction * Step);
        }

        internal static float SetMultiplier(float value)
        {
            value = Mathf.Max(Mathf.Round(value), MinimumMultiplier);
            var actor = BsSaveDataImpl.nowActor;
            if (actor?.fileSto != null)
            {
                var values = actor.fileSto.Global_valDic;
                if (values == null)
                {
                    values = new Dictionary<string, string>();
                    actor.fileSto.Global_valDic = values;
                }
                values[SaveKey] = value.ToString("0", CultureInfo.InvariantCulture);
            }
            return value;
        }

        internal static bool ShouldScale(DicAttrEnum attribute)
        {
            switch (attribute)
            {
                case DicAttrEnum.Hp:
                case DicAttrEnum.SorAtt:
                case DicAttrEnum.SorDef:
                case DicAttrEnum.PhyAtt:
                case DicAttrEnum.PhyDef:
                case DicAttrEnum.Armor:
                case DicAttrEnum.Shield:
                    return true;
                case DicAttrEnum.Advance:
                    return GetSpeedMultiplierEnabled();
                default:
                    return false;
            }
        }

        internal static float ScaleFinalValue(float value)
        {
            return value * GetMultiplier();
        }

        internal static bool GetSpeedMultiplierEnabled()
        {
            var values = BsSaveDataImpl.nowActor?.fileSto?.Global_valDic;
            return values != null && values.TryGetValue(SpeedSaveKey, out var value) && value == "1";
        }

        internal static bool ToggleSpeedMultiplier()
        {
            var enabled = !GetSpeedMultiplierEnabled();
            var actor = BsSaveDataImpl.nowActor;
            if (actor?.fileSto != null)
            {
                var values = actor.fileSto.Global_valDic;
                if (values == null)
                {
                    values = new Dictionary<string, string>();
                    actor.fileSto.Global_valDic = values;
                }
                values[SpeedSaveKey] = enabled ? "1" : "0";
            }
            return enabled;
        }
    }

    internal sealed class NpcDifficultyUi : MonoBehaviour
    {
        private TextPro _valueText;
        private TextPro _speedText;

        internal static NpcDifficultyUi Create(TechTreePanel panel, TextPro sourceText)
        {
            var root = new GameObject("CodeYunHeavenlyTrialPanel", typeof(RectTransform));
            root.layer = panel.gameObject.layer;
            root.transform.SetParent(HeavenlyTrialNativeTab.GetView(panel).cellList, false);
            var rect = (RectTransform)root.transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
            var ui = root.AddComponent<NpcDifficultyUi>();
            ui.Build(sourceText);
            return ui;
        }

        private void Build(TextPro sourceText)
        {
            var label = CreateText(sourceText, transform, "Label", "NPC属性倍率", new Vector2(90f, -110f), new Vector2(240f, 56f));
            label.alignment = TMPro.TextAlignmentOptions.MidlineLeft;
            var subtract = CreateButton(sourceText, transform, "Subtract", "－", new Vector2(360f, -110f));
            _valueText = CreateText(sourceText, transform, "Value", "×1", new Vector2(420f, -110f), new Vector2(90f, 56f));
            _valueText.alignment = TMPro.TextAlignmentOptions.Center;
            var add = CreateButton(sourceText, transform, "Add", "＋", new Vector2(520f, -110f));
            var speedLabel = CreateText(sourceText, transform, "SpeedLabel", "速度倍率", new Vector2(90f, -190f), new Vector2(240f, 56f));
            speedLabel.alignment = TMPro.TextAlignmentOptions.MidlineLeft;
            var speedButton = CreateButton(sourceText, transform, "SpeedToggle", "关闭", new Vector2(360f, -190f), new Vector2(150f, 50f));
            _speedText = speedButton.GetComponentInChildren<TextPro>();
            subtract.onClick.AddListener(() => { NpcDifficultyState.Change(-1); Refresh(); });
            add.onClick.AddListener(() => { NpcDifficultyState.Change(1); Refresh(); });
            speedButton.onClick.AddListener(() => { NpcDifficultyState.ToggleSpeedMultiplier(); Refresh(); });
            Refresh();
        }

        internal void Refresh()
        {
            _valueText.text = "×" + NpcDifficultyState.GetMultiplier().ToString("0", CultureInfo.InvariantCulture);
            _speedText.text = NpcDifficultyState.GetSpeedMultiplierEnabled() ? "开启" : "关闭";
        }

        private static TextPro CreateText(TextPro source, Transform parent, string name, string text, Vector2 position, Vector2 size)
        {
            var result = Instantiate(source, parent);
            result.name = name;
            foreach (var localization in result.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            result.text = text;
            result.fontSize = 28f;
            result.color = new Color(0.12f, 0.12f, 0.12f, 1f);
            var rect = (RectTransform)result.transform;
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(0f, 1f);
            rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
            return result;
        }

        private static Button CreateButton(TextPro source, Transform parent, string name, string text, Vector2 position, Vector2? size = null)
        {
            var obj = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            obj.layer = parent.gameObject.layer;
            obj.transform.SetParent(parent, false);
            var rect = (RectTransform)obj.transform;
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(0f, 1f);
            rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = position;
            rect.sizeDelta = size ?? new Vector2(50f, 50f);
            obj.GetComponent<Image>().color = new Color(0.15f, 0.12f, 0.09f, 0.18f);
            var label = CreateText(source, obj.transform, "Text", text, Vector2.zero, rect.sizeDelta);
            label.alignment = TMPro.TextAlignmentOptions.Center;
            return obj.GetComponent<Button>();
        }
    }

    internal static class HeavenlyTrialNativeTab
    {
        internal const PlayerTalentEnum Talent = (PlayerTalentEnum)1000;

        internal static TechTreePanelView GetView(TechTreePanel panel)
        {
            return Traverse.Create(panel).Field<TechTreePanelView>("view").Value;
        }

        internal static List<TechTreeMenuCell> GetMenuCells(TechTreePanel panel)
        {
            return Traverse.Create(panel).Field<List<TechTreeMenuCell>>("techTreeMenuList").Value;
        }

        internal static void SetSubPanel(TechTreePanel panel, GameObject subPanel)
        {
            Traverse.Create(panel).Field<GameObject>("subPanel").Value = subPanel;
        }
    }

    [HarmonyPatch(typeof(TechTreePanel), "ShowTechTreeMenu")]
    internal static class TechTreePanelMenuPatch
    {
        private static void Prefix(TechTreePanel __instance)
        {
            var menuCells = HeavenlyTrialNativeTab.GetMenuCells(__instance);
            var old = menuCells.FirstOrDefault(cell => cell != null && cell.talentEnum == HeavenlyTrialNativeTab.Talent);
            if (old == null) return;
            menuCells.Remove(old);
            UnityEngine.Object.Destroy(old.gameObject);
        }

        private static void Postfix(TechTreePanel __instance)
        {
            var view = HeavenlyTrialNativeTab.GetView(__instance);
            var menuCells = HeavenlyTrialNativeTab.GetMenuCells(__instance);
            var source = view.menuCellPrefab.LastOrDefault();
            if (source == null) return;
            var cell = UnityEngine.Object.Instantiate(source, view.treeList);
            cell.gameObject.name = "CodeYunHeavenlyTrialTab";
            cell.gameObject.SetActive(true);
            cell.SetInfo("天道试炼", HeavenlyTrialNativeTab.Talent, false);
            cell.transform.SetSiblingIndex(0);
            menuCells.Insert(0, cell);
        }
    }

    [HarmonyPatch(typeof(TechTreePanel), "ShowSubpanel")]
    internal static class TechTreePanelSubpanelPatch
    {
        private static bool Prefix(TechTreePanel __instance, PlayerTalentEnum __0)
        {
            if (__0 != HeavenlyTrialNativeTab.Talent) return true;
            var view = HeavenlyTrialNativeTab.GetView(__instance);
            var menuCells = HeavenlyTrialNativeTab.GetMenuCells(__instance);
            var ui = view.cellList.GetComponentInChildren<NpcDifficultyUi>(true);
            if (ui == null)
            {
                var cell = menuCells.First(menu => menu.talentEnum == HeavenlyTrialNativeTab.Talent);
                ui = NpcDifficultyUi.Create(__instance, cell.text_Pro);
            }
            ui.gameObject.SetActive(true);
            ui.Refresh();
            HeavenlyTrialNativeTab.SetSubPanel(__instance, ui.gameObject);
            return false;
        }
    }

    [HarmonyPatch(typeof(TbNpcSto), nameof(TbNpcSto.GetDicAttrib))]
    internal static class NpcStoredAttributeOwnerPatch
    {
        private static void Postfix(TbNpcSto __instance, DicAttrEnum __0, ref float __result)
        {
            if (__instance.id != 10000 && NpcDifficultyState.ShouldScale(__0))
            {
                __result = NpcDifficultyState.ScaleFinalValue(__result);
            }
        }
    }

    [HarmonyPatch(typeof(TbBattleNpcTmp), nameof(TbBattleNpcTmp.GetDicAttrib))]
    internal static class NpcBattleAttributeOwnerPatch
    {
        private static void Postfix(TbBattleNpcTmp __instance, DicAttrEnum __0, ref float __result)
        {
            if (__instance.npcStoId != 10000 && NpcDifficultyState.ShouldScale(__0))
            {
                __result = NpcDifficultyState.ScaleFinalValue(__result);
            }
        }
    }

}
