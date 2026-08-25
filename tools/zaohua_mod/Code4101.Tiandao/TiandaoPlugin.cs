using BepInEx;
using BepInEx.Configuration;
using BepInEx.Unity.Mono;
using HarmonyLib;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEngine;
using UnityEngine.UI;

namespace Code4101.Zaohua.Tiandao
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class TiandaoPlugin : BaseUnityPlugin
    {
        public const string PluginGuid = "code4101.zaohua.tiandao";
        public const string PluginName = "Code4101的天道系统";
        public const string PluginVersion = "0.2.0";

        private Harmony _harmony;
        private static TiandaoPlugin _instance;

        internal static void LogAlchemy(string message)
        {
            _instance?.Logger.LogInfo(message);
        }

        private void Awake()
        {
            _instance = this;
            TiandaoState.Initialize(Config);
            BagEnhancementState.Initialize(Config);
            _harmony = new Harmony(PluginGuid);
            _harmony.PatchAll();
            SmartAlchemyFeature.ApplyConfiguredState();
            Logger.LogInfo("Code4101 Tiandao patches registered.");
        }

        private void OnDestroy()
        {
            _harmony?.UnpatchSelf();
            if (_instance == this) _instance = null;
        }

        private void Update()
        {
            EquipmentLoadoutRuntime.Tick();
        }

        private void OnApplicationQuit()
        {
            EquipmentLoadoutRuntime.Flush();
        }
    }

    internal static class TiandaoState
    {
        private const string PreviousSaveKey = "Code4101.Tiandao.NpcAttributeMultiplier";
        private const string PreviousSpeedSaveKey = "Code4101.Tiandao.NpcSpeedMultiplierEnabled";
        private const string PreviousAlchemySaveKey = "Code4101.Tiandao.AlchemyAssistantEnabled";
        private const string LegacySaveKey = "CodeYun.NpcAttributeMultiplier";
        private const string LegacySpeedSaveKey = "CodeYun.NpcSpeedMultiplierEnabled";
        private const string LegacyAlchemySaveKey = "CodeYun.AlchemyAssistantEnabled";
        private const float MinimumMultiplier = 1f;
        private const float Step = 1f;
        private static ConfigFile _config;
        private static ConfigEntry<int> _multiplier;
        private static ConfigEntry<bool> _speedMultiplier;
        private static ConfigEntry<bool> _alchemyAssistant;
        private static ConfigEntry<bool> _saveMigrationCompleted;
        private static ConfigEntry<int> _configSchemaVersion;

        internal static void Initialize(ConfigFile config)
        {
            _config = config;
            _configSchemaVersion = config.Bind("系统", "配置版本", 1, "用于后续新增参数或迁移配置结构");
            _multiplier = config.Bind("天道试炼", "属性倍率", 1, "NPC最终属性倍率，最低为1且不设上限");
            _speedMultiplier = config.Bind("天道试炼", "速度倍率", false, "NPC属性倍率是否对速度生效");
            _alchemyAssistant = config.Bind("天道助缘", "炼丹助手", true, "开启丹谱与智能炼丹功能");
            _saveMigrationCompleted = config.Bind("兼容", "已迁移旧存档配置", false, "内部兼容标记");
        }

        internal static float GetMultiplier()
        {
            TryMigrateSaveSettings();
            return Mathf.Max(_multiplier?.Value ?? 1, MinimumMultiplier);
        }

        internal static float Change(int direction)
        {
            return SetMultiplier(GetMultiplier() + direction * Step);
        }

        internal static float SetMultiplier(float value)
        {
            value = Mathf.Max(Mathf.Round(value), MinimumMultiplier);
            _multiplier.Value = Mathf.RoundToInt(value);
            MarkConfigured();
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
            TryMigrateSaveSettings();
            return _speedMultiplier?.Value ?? false;
        }

        internal static bool ToggleSpeedMultiplier()
        {
            var enabled = !GetSpeedMultiplierEnabled();
            SetSpeedMultiplierEnabled(enabled);
            return enabled;
        }

        internal static void SetSpeedMultiplierEnabled(bool enabled)
        {
            _speedMultiplier.Value = enabled;
            MarkConfigured();
        }

        internal static void ResetDefaults()
        {
            SetMultiplier(1f);
            SetSpeedMultiplierEnabled(false);
            SetAlchemyAssistantEnabled(true);
        }

        internal static bool GetAlchemyAssistantEnabled()
        {
            TryMigrateSaveSettings();
            return _alchemyAssistant?.Value ?? true;
        }

        internal static bool ToggleAlchemyAssistant()
        {
            var enabled = !GetAlchemyAssistantEnabled();
            SetAlchemyAssistantEnabled(enabled);
            return enabled;
        }

        internal static void SetAlchemyAssistantEnabled(bool enabled)
        {
            _alchemyAssistant.Value = enabled;
            MarkConfigured();
            SmartAlchemyFeature.SetEnabled(enabled);
        }

        private static void MarkConfigured()
        {
            if (_saveMigrationCompleted != null) _saveMigrationCompleted.Value = true;
            _config?.Save();
        }

        private static void TryMigrateSaveSettings()
        {
            if (_saveMigrationCompleted == null || _saveMigrationCompleted.Value) return;
            var values = BsSaveDataImpl.nowActor?.fileSto?.Global_valDic;
            if (values == null) return;

            if ((values.TryGetValue(PreviousSaveKey, out var multiplierText) || values.TryGetValue(LegacySaveKey, out multiplierText)) &&
                float.TryParse(multiplierText, NumberStyles.Float, CultureInfo.InvariantCulture, out var multiplier))
            {
                _multiplier.Value = Mathf.Max(1, Mathf.RoundToInt(multiplier));
            }
            if (values.TryGetValue(PreviousSpeedSaveKey, out var speedText) || values.TryGetValue(LegacySpeedSaveKey, out speedText))
            {
                _speedMultiplier.Value = speedText == "1";
            }
            if (values.TryGetValue(PreviousAlchemySaveKey, out var alchemyText) || values.TryGetValue(LegacyAlchemySaveKey, out alchemyText))
            {
                _alchemyAssistant.Value = alchemyText != "0";
            }
            _saveMigrationCompleted.Value = true;
            _config.Save();
        }
    }

    internal sealed class NpcDifficultyUi : MonoBehaviour
    {
        private TextPro _valueText;
        private TextPro _speedText;
        private Image _speedButtonImage;

        internal static NpcDifficultyUi Create(TechTreePanel panel, TextPro sourceText)
        {
            var root = new GameObject("Code4101TiandaoTrialPanel", typeof(RectTransform), typeof(Animator));
            root.layer = panel.gameObject.layer;
            root.transform.SetParent(HeavenlyTrialNativeTab.GetView(panel).cellList, false);
            var rect = (RectTransform)root.transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
            var sourceAnimator = HeavenlyTrialNativeTab.GetSubPanel(panel)?.GetComponent<Animator>();
            if (sourceAnimator != null)
            {
                root.GetComponent<Animator>().runtimeAnimatorController = sourceAnimator.runtimeAnimatorController;
            }
            var ui = root.AddComponent<NpcDifficultyUi>();
            ui.Build(sourceText);
            return ui;
        }

        private void Build(TextPro sourceText)
        {
            var multiplierRow = CreateRow(transform, "MultiplierRow", new Vector2(190f, -100f), new Vector2(720f, 72f));
            var label = CreateText(sourceText, multiplierRow, "Label", "NPC属性倍率", new Vector2(22f, -8f), new Vector2(270f, 56f));
            label.alignment = TMPro.TextAlignmentOptions.MidlineLeft;
            var subtract = CreateButton(sourceText, multiplierRow, "Subtract", "－", new Vector2(410f, -10f));
            var valueSlot = CreateRow(multiplierRow, "ValueSlot", new Vector2(470f, -10f), new Vector2(96f, 50f), 0.72f);
            _valueText = CreateText(sourceText, valueSlot, "Value", "×1", Vector2.zero, new Vector2(96f, 50f));
            _valueText.alignment = TMPro.TextAlignmentOptions.Center;
            var add = CreateButton(sourceText, multiplierRow, "Add", "＋", new Vector2(578f, -10f));

            var speedRow = CreateRow(transform, "SpeedRow", new Vector2(190f, -188f), new Vector2(720f, 72f));
            var speedLabel = CreateText(sourceText, speedRow, "SpeedLabel", "速度倍率", new Vector2(22f, -8f), new Vector2(270f, 56f));
            speedLabel.alignment = TMPro.TextAlignmentOptions.MidlineLeft;
            var speedButton = CreateButton(sourceText, speedRow, "SpeedToggle", "关闭", new Vector2(410f, -10f), new Vector2(156f, 50f));
            _speedButtonImage = speedButton.GetComponent<Image>();
            _speedText = speedButton.GetComponentInChildren<TextPro>();
            subtract.onClick.AddListener(() => { TiandaoState.Change(-1); Refresh(); });
            add.onClick.AddListener(() => { TiandaoState.Change(1); Refresh(); });
            speedButton.onClick.AddListener(() => { TiandaoState.ToggleSpeedMultiplier(); Refresh(); });
            Refresh();
        }

        internal void Refresh()
        {
            _valueText.text = "×" + TiandaoState.GetMultiplier().ToString("0", CultureInfo.InvariantCulture);
            var speedEnabled = TiandaoState.GetSpeedMultiplierEnabled();
            _speedText.text = speedEnabled ? "开启" : "关闭";
            _speedButtonImage.color = speedEnabled
                ? new Color(0.16f, 0.42f, 0.20f, 0.82f)
                : new Color(0.10f, 0.10f, 0.10f, 0.68f);
        }

        private static Transform CreateRow(Transform parent, string name, Vector2 position, Vector2 size, float alpha = 0.52f)
        {
            var row = new GameObject(name, typeof(RectTransform), typeof(Image));
            row.layer = parent.gameObject.layer;
            row.transform.SetParent(parent, false);
            var rect = (RectTransform)row.transform;
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(0f, 1f);
            rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
            row.GetComponent<Image>().color = new Color(0.10f, 0.10f, 0.10f, alpha);
            return row.transform;
        }

        private static TextPro CreateText(TextPro source, Transform parent, string name, string text, Vector2 position, Vector2 size)
        {
            var result = Instantiate(source, parent);
            result.name = name;
            foreach (var localization in result.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            result.text = text;
            result.fontSize = 26f;
            result.color = Color.white;
            result.outlineColor = Color.black;
            result.outlineWidth = 0.16f;
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
            obj.GetComponent<Image>().color = new Color(0.10f, 0.10f, 0.10f, 0.68f);
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

        internal static GameObject GetSubPanel(TechTreePanel panel)
        {
            return Traverse.Create(panel).Field<GameObject>("subPanel").Value;
        }
    }

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
            cell.gameObject.name = "Code4101TiandaoTrialTab";
            cell.gameObject.SetActive(true);
            cell.SetInfo("天道试炼", HeavenlyTrialNativeTab.Talent, false);
            cell.transform.SetSiblingIndex(0);
            menuCells.Insert(0, cell);
        }
    }

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
                var oldPanel = HeavenlyTrialNativeTab.GetSubPanel(__instance);
                var contentText = oldPanel?.GetComponentsInChildren<TextPro>(true)
                    .Where(text => !string.IsNullOrWhiteSpace(text.text))
                    .OrderBy(text => Mathf.Abs(text.fontSize - 26f))
                    .FirstOrDefault();
                ui = NpcDifficultyUi.Create(__instance, contentText ?? cell.text_Pro);
            }
            ui.gameObject.SetActive(true);
            ui.Refresh();
            HeavenlyTrialNativeTab.SetSubPanel(__instance, ui.gameObject);
            return false;
        }
    }

    internal sealed class TiandaoSettingUi : MonoBehaviour
    {
        private SettingView _view;
        private Button _tabButton;
        private GameObject _panel;
        private Button _resetButton;
        private TextPro _multiplierValue;
        private TextPro _speedValue;
        private TextPro _alchemyValue;
        private Button _multiplierLower;

        internal void Initialize(SettingView view)
        {
            if (_panel != null) return;
            _view = view;
            CreateTab();
            CreatePanel();
            BindNativeTabs();
            Hide();
        }

        private void CreateTab()
        {
            // “游戏”在 SettingPanel.Awake 时已经处于选中态；从它克隆会把蓝色描边材质一并复制，
            // 导致新按钮即使未选中也偶尔常亮。使用未选中的同款“视频”按钮作为干净模板。
            _tabButton = Instantiate(_view.BtnVideoSetting, _view.BtnGameSetting.transform.parent);
            _tabButton.gameObject.name = "Code4101TiandaoSettingTab";
            _tabButton.onClick.RemoveAllListeners();
            foreach (var localization in _tabButton.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var tabTexts = _tabButton.GetComponentsInChildren<TextPro>(true);
            if (tabTexts.Length > 0) tabTexts[0].text = "天";
            if (tabTexts.Length > 1) tabTexts[1].text = "道";
            for (var i = 2; i < tabTexts.Length; i++) tabTexts[i].text = string.Empty;
            var gameRect = (RectTransform)_view.BtnGameSetting.transform;
            var videoRect = (RectTransform)_view.BtnVideoSetting.transform;
            var audioRect = (RectTransform)_view.BtnAudioSetting.transform;
            var keyRect = (RectTransform)_view.BtnKeySetting.transform;
            var tabRect = (RectTransform)_tabButton.transform;
            var firstPosition = gameRect.anchoredPosition;
            var menuStep = (keyRect.anchoredPosition - firstPosition) / 4f;
            tabRect.anchoredPosition = firstPosition;
            gameRect.anchoredPosition = firstPosition + menuStep;
            videoRect.anchoredPosition = firstPosition + menuStep * 2f;
            audioRect.anchoredPosition = firstPosition + menuStep * 3f;
            keyRect.anchoredPosition = firstPosition + menuStep * 4f;
            tabRect.SetSiblingIndex(gameRect.GetSiblingIndex());
            _view.Btns.toggleButtons.Insert(0, _tabButton);
            _tabButton.onClick.AddListener(Show);
            _view.Btns.SetActiveButton(_view.BtnGameSetting);
        }

        private void CreatePanel()
        {
            _panel = Instantiate(_view.GameSetting, _view.GameSetting.transform.parent);
            _panel.name = "Code4101TiandaoSettingPanel";

            var originalAutoValue = GetField<TextPro>("txtAutoSave");
            var originalSpeedValue = GetField<TextPro>("txtShowTime");
            var originalAlchemyValue = GetField<TextPro>("txtShowGrid");
            _multiplierValue = FindCloneComponent(_view.GameSetting, _panel, originalAutoValue);
            _speedValue = FindCloneComponent(_view.GameSetting, _panel, originalSpeedValue);
            _alchemyValue = FindCloneComponent(_view.GameSetting, _panel, originalAlchemyValue);

            var multiplierRow = GetDirectChild(_panel.transform, _multiplierValue.transform);
            var speedRow = GetDirectChild(_panel.transform, _speedValue.transform);
            var alchemyRow = GetDirectChild(_panel.transform, _alchemyValue.transform);
            ConfigureRow(multiplierRow, _multiplierValue, "属性倍率", "强化NPC属性倍率");
            ConfigureRow(speedRow, _speedValue, "速度倍率", "NPC属性倍率是否对速度生效");
            ConfigureRow(alchemyRow, _alchemyValue, "炼丹助手", "开启丹谱与智能炼丹功能");

            var trialHeaderRow = GetClonedRow("txtBattleImpulse");
            var assistanceHeaderRow = GetClonedRow("txtLanguage");
            ArrangeGroupedRows(trialHeaderRow, multiplierRow, speedRow, assistanceHeaderRow, alchemyRow);
            CreateGroupHeader(trialHeaderRow, "天道试炼");
            CreateGroupHeader(assistanceHeaderRow, "天道助缘");

            _multiplierLower = FindCloneComponent(_view.GameSetting, _panel, GetField<Button>("btnAutoSaveLower"));
            var upper = FindCloneComponent(_view.GameSetting, _panel, GetField<Button>("btnAutoSaveUpper"));
            var speedLower = FindCloneComponent(_view.GameSetting, _panel, GetField<Button>("btnShowTimeLower"));
            var speedUpper = FindCloneComponent(_view.GameSetting, _panel, GetField<Button>("btnShowTimeUpper"));
            var alchemyLower = FindCloneComponent(_view.GameSetting, _panel, GetField<Button>("btnShowGridLower"));
            var alchemyUpper = FindCloneComponent(_view.GameSetting, _panel, GetField<Button>("btnShowGridUpper"));
            BindButton(_multiplierLower, () => { TiandaoState.Change(-1); Refresh(); });
            BindButton(upper, () => { TiandaoState.Change(1); Refresh(); });
            BindButton(speedLower, () => { TiandaoState.SetSpeedMultiplierEnabled(false); Refresh(); });
            BindButton(speedUpper, () => { TiandaoState.SetSpeedMultiplierEnabled(true); Refresh(); });
            BindButton(alchemyLower, () => { TiandaoState.SetAlchemyAssistantEnabled(false); Refresh(); });
            BindButton(alchemyUpper, () => { TiandaoState.SetAlchemyAssistantEnabled(true); Refresh(); });

            _resetButton = Instantiate(_view.BtnReset, _view.BtnReset.transform.parent);
            _resetButton.gameObject.name = "Code4101TiandaoReset";
            _resetButton.onClick.RemoveAllListeners();
            _resetButton.onClick.AddListener(() => { TiandaoState.ResetDefaults(); Refresh(); });
        }

        private void BindNativeTabs()
        {
            foreach (var button in new[] { _view.BtnGameSetting, _view.BtnVideoSetting, _view.BtnAudioSetting, _view.BtnKeySetting })
            {
                button.onClick.AddListener(Hide);
            }
        }

        private void Show()
        {
            _view.GameSetting.SetActive(false);
            _view.VideoSetting.SetActive(false);
            _view.AudioSetting.SetActive(false);
            _view.KeySetting.SetActive(false);
            _panel.SetActive(true);
            _view.Title.text = "天道系统";
            _view.Btns.SetActiveButton(_tabButton);
            _view.BtnReset.gameObject.SetActive(false);
            _resetButton.gameObject.SetActive(true);
            Refresh();
        }

        private void Hide()
        {
            _panel.SetActive(false);
            _resetButton.gameObject.SetActive(false);
            _view.BtnReset.gameObject.SetActive(true);
        }

        private void Refresh()
        {
            SmartAlchemyFeature.SetEnabled(TiandaoState.GetAlchemyAssistantEnabled());
            var multiplier = TiandaoState.GetMultiplier();
            _multiplierValue.text = "×" + multiplier.ToString("0", CultureInfo.InvariantCulture);
            if (_multiplierLower != null) _multiplierLower.interactable = multiplier > 1f;
            _speedValue.text = TiandaoState.GetSpeedMultiplierEnabled() ? "开启" : "关闭";
            _alchemyValue.text = TiandaoState.GetAlchemyAssistantEnabled() ? "开启" : "关闭";
        }

        private T GetField<T>(string name) where T : UnityEngine.Object
        {
            return Traverse.Create(_view).Field<T>(name).Value;
        }

        private Transform GetClonedRow(string textFieldName)
        {
            var original = GetField<TextPro>(textFieldName);
            var clone = FindCloneComponent(_view.GameSetting, _panel, original);
            return GetDirectChild(_panel.transform, clone.transform);
        }

        private static void ArrangeGroupedRows(params Transform[] rows)
        {
            var positions = rows
                .Select(row => ((RectTransform)row).anchoredPosition)
                .OrderByDescending(position => position.y)
                .ToArray();
            for (var i = 0; i < rows.Length; i++)
            {
                ((RectTransform)rows[i]).anchoredPosition = positions[i];
                rows[i].SetSiblingIndex(i);
                rows[i].gameObject.SetActive(true);
            }
        }

        private void CreateGroupHeader(Transform row, string title)
        {
            foreach (Transform child in row.Cast<Transform>().ToList()) child.gameObject.SetActive(false);
            foreach (var button in row.GetComponents<Button>()) button.enabled = false;
            foreach (var image in row.GetComponents<Image>()) image.enabled = false;

            var header = Instantiate(_view.Title, row);
            header.gameObject.name = "Code4101" + title;
            foreach (var localization in header.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            header.text = "◆ " + title + " ◆";
            header.fontSize = _view.Title.fontSize * 0.72f;
            header.alignment = TMPro.TextAlignmentOptions.Center;
            var rect = (RectTransform)header.transform;
            rect.anchorMin = new Vector2(0.5f, 0.5f);
            rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = Vector2.zero;
            rect.sizeDelta = new Vector2(520f, ((RectTransform)row).rect.height);
            rect.localScale = Vector3.one;
        }

        private static T FindCloneComponent<T>(GameObject originalRoot, GameObject cloneRoot, T original) where T : Component
        {
            if (original == null) return null;
            var path = GetRelativePath(originalRoot.transform, original.transform);
            return cloneRoot.transform.Find(path)?.GetComponent<T>();
        }

        private static string GetRelativePath(Transform root, Transform target)
        {
            var names = new Stack<string>();
            for (var current = target; current != null && current != root; current = current.parent) names.Push(current.name);
            return string.Join("/", names);
        }

        private static Transform GetDirectChild(Transform root, Transform descendant)
        {
            var current = descendant;
            while (current.parent != null && current.parent != root) current = current.parent;
            return current;
        }

        private static void ConfigureRow(Transform row, TextPro valueText, string label, string description)
        {
            var texts = row.GetComponentsInChildren<TextPro>(true).OrderBy(text => text.transform.position.x).ToList();
            foreach (var text in texts)
            {
                foreach (var localization in text.GetComponents<TextProLocalization>()) localization.enabled = false;
                if (text == valueText) continue;
                text.text = string.Empty;
            }
            var labelText = texts.FirstOrDefault(text => text != valueText);
            var descriptionText = texts.LastOrDefault(text => text != valueText && text != labelText);
            if (labelText != null)
            {
                labelText.enableWordWrapping = false;
                labelText.overflowMode = TMPro.TextOverflowModes.Overflow;
                labelText.text = label;
            }
            if (descriptionText != null)
            {
                descriptionText.enableWordWrapping = false;
                descriptionText.overflowMode = TMPro.TextOverflowModes.Overflow;
                descriptionText.text = description;
            }
        }

        private static void BindButton(Button button, UnityEngine.Events.UnityAction action)
        {
            button.onClick.RemoveAllListeners();
            button.onClick.AddListener(action);
        }
    }

    [HarmonyPatch(typeof(SettingPanel), "Awake")]
    internal static class SettingPanelAwakeDifficultyPatch
    {
        private static void Postfix(SettingPanel __instance)
        {
            var view = Traverse.Create(__instance).Field<SettingView>("view").Value;
            var ui = __instance.gameObject.GetComponent<TiandaoSettingUi>() ?? __instance.gameObject.AddComponent<TiandaoSettingUi>();
            ui.Initialize(view);
        }
    }

    [HarmonyPatch(typeof(TbNpcSto), nameof(TbNpcSto.GetDicAttrib))]
    internal static class NpcStoredAttributeOwnerPatch
    {
        private static void Postfix(TbNpcSto __instance, DicAttrEnum __0, ref float __result)
        {
            if (__instance.id != 10000 && TiandaoState.ShouldScale(__0))
            {
                __result = TiandaoState.ScaleFinalValue(__result);
            }
        }
    }

    [HarmonyPatch(typeof(TbBattleNpcTmp), nameof(TbBattleNpcTmp.GetDicAttrib))]
    internal static class NpcBattleAttributeOwnerPatch
    {
        private static void Postfix(TbBattleNpcTmp __instance, DicAttrEnum __0, ref float __result)
        {
            if (__instance.npcStoId != 10000 && TiandaoState.ShouldScale(__0))
            {
                __result = TiandaoState.ScaleFinalValue(__result);
            }
        }
    }

}
