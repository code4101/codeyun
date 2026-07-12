using BepInEx.Configuration;
using HarmonyLib;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEngine;
using UnityEngine.UI;

namespace Code4101.Zaohua.Tiandao
{
    internal static class CompactNumberDisplay
    {
        private static readonly Regex IntegerPattern = new Regex(@"(?<![\d.])-?\d+(?![\d.])", RegexOptions.Compiled);

        internal static string FormatText(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return text;
            return IntegerPattern.Replace(text, match =>
            {
                if (!long.TryParse(match.Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
                    return match.Value;
                return Format(value);
            });
        }

        internal static string Format(long value)
        {
            var magnitude = Math.Abs((double)value);
            if (magnitude < 10000d) return value.ToString(CultureInfo.InvariantCulture);
            if (magnitude >= 100000000d) return FormatScaled(value / 100000000d) + "亿";
            return FormatScaled(value / 10000d) + "万";
        }

        private static string FormatScaled(double value)
        {
            var magnitude = Math.Abs(value);
            if (magnitude >= 1000d) return value.ToString("0", CultureInfo.InvariantCulture);
            if (magnitude >= 100d) return value.ToString("0.0", CultureInfo.InvariantCulture);
            if (magnitude >= 10d) return value.ToString("0.00", CultureInfo.InvariantCulture);
            return value.ToString("0.000", CultureInfo.InvariantCulture);
        }
    }

    internal enum DerivedBagFilter
    {
        All,
        Pill,
        Recipe,
        Helmet,
        Clothes,
        Shoes,
        Ornament,
    }

    internal static class BagEnhancementState
    {
        internal static readonly int[] EquipmentSlots =
        {
            (int)ItemSlot.helmet, (int)ItemSlot.clothes, (int)ItemSlot.shoe,
            (int)ItemSlot.ornaments1, (int)ItemSlot.ornaments2, (int)ItemSlot.ornaments3,
        };
        private static ConfigFile _config;
        private static ConfigEntry<bool> _rememberDerivedFilter;
        private static readonly Dictionary<int, DerivedBagFilter> RememberedFilters = new Dictionary<int, DerivedBagFilter>();

        internal static void Initialize(ConfigFile config)
        {
            _config = config;
            _rememberDerivedFilter = config.Bind("储物空间", "记住衍生筛选", true, "分别记住丹药和装备的二级筛选");
        }

        internal static DerivedBagFilter GetFilter(int parentTypeId)
        {
            return RememberedFilters.TryGetValue(parentTypeId, out var filter) ? filter : DerivedBagFilter.All;
        }

        internal static void SetFilter(int parentTypeId, DerivedBagFilter filter)
        {
            if (_rememberDerivedFilter?.Value == false) RememberedFilters.Clear();
            RememberedFilters[parentTypeId] = filter;
        }

        internal static void SetRememberDerivedFilter(bool remember)
        {
            if (_rememberDerivedFilter == null) return;
            _rememberDerivedFilter.Value = remember;
            if (!remember) RememberedFilters.Clear();
            _config?.Save();
        }

        internal static void ForgetFilter(int parentTypeId)
        {
            if (_rememberDerivedFilter?.Value == false) RememberedFilters.Remove(parentTypeId);
        }

    }

    internal static class DerivedBagFiltering
    {
        internal static string GetContext(int parentTypeId)
        {
            if (parentTypeId == 0) return null;
            var name = Singleton<TbItemImpl>.Instance.GetTypeName(parentTypeId) ?? string.Empty;
            // 游戏顶部按钮的真实父类映射：防具=1，丹药（含丹方）=6。
            if (parentTypeId == 6 || name.Contains("丹") || name.Contains("药")) return "drug";
            if (parentTypeId == 1 || name.Contains("防具") || name.Contains("装备") || name.Contains("服饰")) return "equipment";
            return null;
        }

        internal static bool Matches(TbPackSto item, DerivedBagFilter filter)
        {
            if (filter == DerivedBagFilter.All || item == null) return true;
            var typeName = Singleton<TbItemImpl>.Instance.GetTypeName(item.typeId) ?? string.Empty;
            switch (filter)
            {
                case DerivedBagFilter.Recipe:
                    return typeName.Contains("丹方") || item.name.EndsWith("丹方", StringComparison.Ordinal);
                case DerivedBagFilter.Pill:
                    return !(typeName.Contains("丹方") || item.name.EndsWith("丹方", StringComparison.Ordinal));
                case DerivedBagFilter.Helmet:
                    return ContainsAny(typeName, "头饰", "头部", "帽", "冠", "盔");
                case DerivedBagFilter.Clothes:
                    return ContainsAny(typeName, "服饰", "服装", "衣服", "衣", "袍", "甲");
                case DerivedBagFilter.Shoes:
                    return ContainsAny(typeName, "鞋子", "鞋", "靴", "履");
                case DerivedBagFilter.Ornament:
                    return ContainsAny(typeName, "饰品", "饰", "佩", "环", "戒");
                default:
                    return true;
            }
        }

        private static bool ContainsAny(string text, params string[] values)
        {
            return values.Any(text.Contains);
        }
    }

    internal sealed class BagEnhancementUi : MonoBehaviour
    {
        private BagPanel _panel;
        private TextPro _textTemplate;
        private GameObject _filterPopup;
        private Button _filterTrigger;
        private TextPro _filterTriggerLabel;
        private GameObject _loadoutPopup;
        private Button _loadoutTrigger;
        private TextPro _loadoutTriggerLabel;
        private string _loadoutMessage;
        private readonly List<Button> _filterButtons = new List<Button>();
        private readonly List<Button> _loadoutButtons = new List<Button>();
        private int _lastParentType = -1;

        internal void Initialize(BagPanel panel)
        {
            if (_panel != null) return;
            _panel = panel;
            var view = Traverse.Create(panel).Field<BagPanelView>("view").Value;
            var viewFields = Traverse.Create(view);
            _textTemplate = viewFields.Field<TextPro>("txtName").Value ??
                            viewFields.Field<TextPro>("txtIntroduction").Value;
            CreateFilterMenu(viewFields);
            CreateLoadoutMenu(viewFields, viewFields.Field<GameObject>("goEquip").Value.transform);
            var rememberToggle = viewFields.Field<Toggle>("togRememberSelect").Value;
            if (rememberToggle != null)
            {
                BagEnhancementState.SetRememberDerivedFilter(rememberToggle.isOn);
                rememberToggle.onValueChanged.AddListener(BagEnhancementState.SetRememberDerivedFilter);
            }
            RefreshAll();
        }

        private void Update()
        {
            if (_panel == null) return;
            var parentType = Traverse.Create(_panel).Field<int>("nowTypeParentId").Value;
            if (parentType == _lastParentType) return;
            BagEnhancementState.ForgetFilter(_lastParentType);
            _lastParentType = parentType;
            RebuildFilterButtons(parentType);
        }

        internal void RefreshAll()
        {
            var parentType = Traverse.Create(_panel).Field<int>("nowTypeParentId").Value;
            _lastParentType = parentType;
            RebuildFilterButtons(parentType);
            RefreshLoadoutTrigger();
        }

        private void CreateFilterMenu(Traverse viewFields)
        {
            var gradeButton = viewFields.Field<Button>("btnGrade").Value;
            var attributeButton = viewFields.Field<Button>("btnAttribute").Value;
            _filterTrigger = Instantiate(gradeButton, gradeButton.transform.parent);
            _filterTrigger.gameObject.name = "Code4101DerivedFilterTrigger";
            _filterTrigger.onClick.RemoveAllListeners();
            foreach (var localization in _filterTrigger.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var triggerTexts = _filterTrigger.GetComponentsInChildren<TextPro>(true);
            _filterTriggerLabel = triggerTexts.FirstOrDefault();
            if (_filterTriggerLabel != null) _filterTriggerLabel.text = "全部";
            for (var i = 1; i < triggerTexts.Length; i++) triggerTexts[i].text = string.Empty;

            var gradeRect = (RectTransform)gradeButton.transform;
            var attributeRect = (RectTransform)attributeButton.transform;
            var triggerRect = (RectTransform)_filterTrigger.transform;
            triggerRect.anchoredPosition = gradeRect.anchoredPosition -
                                            (attributeRect.anchoredPosition - gradeRect.anchoredPosition);
            _filterTrigger.onClick.AddListener(() =>
            {
                _filterPopup.SetActive(!_filterPopup.activeSelf);
                if (_filterPopup.activeSelf) _filterPopup.transform.SetAsLastSibling();
            });

            _filterPopup = new GameObject("Code4101DerivedFilterPopup",
                typeof(RectTransform), typeof(Image), typeof(VerticalLayoutGroup));
            _filterPopup.layer = gameObject.layer;
            _filterPopup.transform.SetParent(gradeButton.transform.parent, false);
            var popupRect = (RectTransform)_filterPopup.transform;
            popupRect.anchorMin = triggerRect.anchorMin;
            popupRect.anchorMax = triggerRect.anchorMax;
            popupRect.pivot = new Vector2(0.5f, 1f);
            popupRect.anchoredPosition = triggerRect.anchoredPosition + new Vector2(0f, -45f);
            popupRect.sizeDelta = new Vector2(170f, 260f);
            _filterPopup.GetComponent<Image>().color = new Color(0.055f, 0.045f, 0.035f, 0.96f);
            var layout = _filterPopup.GetComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(8, 8, 8, 8);
            layout.spacing = 6f;
            layout.childAlignment = TextAnchor.UpperCenter;
            layout.childControlWidth = false;
            layout.childControlHeight = false;
            _filterPopup.SetActive(false);
        }

        private void CreateLoadoutMenu(Traverse viewFields, Transform equipRoot)
        {
            var gradeButton = viewFields.Field<Button>("btnGrade").Value;
            _loadoutTrigger = Instantiate(gradeButton, equipRoot);
            _loadoutTrigger.gameObject.name = "Code4101EquipmentLoadoutTrigger";
            _loadoutTrigger.onClick.RemoveAllListeners();
            foreach (var localization in _loadoutTrigger.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var texts = _loadoutTrigger.GetComponentsInChildren<TextPro>(true);
            _loadoutTriggerLabel = texts.FirstOrDefault();
            for (var i = 1; i < texts.Length; i++) texts[i].text = string.Empty;
            var triggerRect = (RectTransform)_loadoutTrigger.transform;
            triggerRect.anchorMin = new Vector2(0.5f, 0.5f);
            triggerRect.anchorMax = new Vector2(0.5f, 0.5f);
            triggerRect.pivot = new Vector2(0.5f, 0.5f);
            triggerRect.sizeDelta = new Vector2(160f, 48f);
            var helmetCell = equipRoot.GetComponentsInChildren<BagEquipCellController>(true)
                .FirstOrDefault(cell => cell.typeId == (int)ItemSlot.helmet || cell.flag == (int)ItemSlot.helmet);
            if (helmetCell != null)
            {
                var helmetBounds = RectTransformUtility.CalculateRelativeRectTransformBounds(
                    equipRoot, helmetCell.transform);
                triggerRect.anchoredPosition = new Vector2(
                    helmetBounds.center.x,
                    helmetBounds.max.y + 55f);
            }
            else
            {
                triggerRect.anchoredPosition = new Vector2(-360f, 150f);
            }

            _loadoutTrigger.onClick.AddListener(() =>
            {
                if (_loadoutPopup.activeSelf)
                {
                    _loadoutPopup.SetActive(false);
                    return;
                }
                RebuildLoadoutPopup();
                _loadoutPopup.SetActive(true);
                _loadoutPopup.transform.SetAsLastSibling();
            });

            _loadoutPopup = new GameObject("Code4101EquipmentLoadoutPopup",
                typeof(RectTransform), typeof(Image), typeof(VerticalLayoutGroup));
            _loadoutPopup.layer = gameObject.layer;
            _loadoutPopup.transform.SetParent(equipRoot, false);
            var popupRect = (RectTransform)_loadoutPopup.transform;
            popupRect.anchorMin = triggerRect.anchorMin;
            popupRect.anchorMax = triggerRect.anchorMax;
            popupRect.pivot = new Vector2(0.5f, 1f);
            popupRect.anchoredPosition = triggerRect.anchoredPosition + new Vector2(0f, -45f);
            popupRect.sizeDelta = new Vector2(220f, 120f);
            _loadoutPopup.GetComponent<Image>().color = new Color(0.055f, 0.045f, 0.035f, 0.96f);
            var layout = _loadoutPopup.GetComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(8, 8, 8, 8);
            layout.spacing = 6f;
            layout.childAlignment = TextAnchor.UpperCenter;
            layout.childControlWidth = false;
            layout.childControlHeight = false;
            _loadoutPopup.SetActive(false);
            RefreshLoadoutTrigger();
        }

        private void RebuildFilterButtons(int parentType)
        {
            foreach (var button in _filterButtons) Destroy(button.gameObject);
            _filterButtons.Clear();
            var context = DerivedBagFiltering.GetContext(parentType);
            _filterTrigger.gameObject.SetActive(context != null);
            _filterPopup.SetActive(false);
            _loadoutTrigger.gameObject.SetActive(context == "equipment");
            _loadoutPopup.SetActive(false);
            if (context == null) return;
            AddFilterButton(parentType, DerivedBagFilter.All, "全部");
            if (context == "drug")
            {
                AddFilterButton(parentType, DerivedBagFilter.Pill, "丹药");
                AddFilterButton(parentType, DerivedBagFilter.Recipe, "丹方");
            }
            else
            {
                AddFilterButton(parentType, DerivedBagFilter.Helmet, "头饰");
                AddFilterButton(parentType, DerivedBagFilter.Clothes, "服饰");
                AddFilterButton(parentType, DerivedBagFilter.Shoes, "鞋履");
                AddFilterButton(parentType, DerivedBagFilter.Ornament, "饰品");
            }
            ((RectTransform)_filterPopup.transform).sizeDelta =
                new Vector2(170f, 16f + _filterButtons.Count * 44f + Math.Max(0, _filterButtons.Count - 1) * 6f);
            RefreshFilterButtonColors(parentType);
        }

        private void AddFilterButton(int parentType, DerivedBagFilter filter, string label)
        {
            var button = CreateButton(_filterPopup.transform, filter.ToString(), label, new Vector2(150f, 44f));
            button.onClick.AddListener(() =>
            {
                BagEnhancementState.SetFilter(parentType, filter);
                _panel.ChangeType(parentType, false);
                RefreshFilterButtonColors(parentType);
                _filterPopup.SetActive(false);
            });
            _filterButtons.Add(button);
        }

        private void RefreshFilterButtonColors(int parentType)
        {
            var selected = BagEnhancementState.GetFilter(parentType).ToString();
            var selectedLabel = "全部";
            foreach (var button in _filterButtons)
            {
                var isSelected = button.name == selected;
                button.GetComponent<Image>().color = isSelected
                    ? new Color(0.46f, 0.33f, 0.16f, 0.92f)
                    : new Color(0.08f, 0.08f, 0.08f, 0.78f);
                if (isSelected) selectedLabel = button.GetComponentInChildren<TextPro>().text;
            }
            if (_filterTriggerLabel != null) _filterTriggerLabel.text = selectedLabel;
        }

        private void RebuildLoadoutPopup()
        {
            foreach (var button in _loadoutButtons) Destroy(button.gameObject);
            _loadoutButtons.Clear();
            var state = EquipmentLoadoutRepository.GetCurrentSaveState();
            if (state == null) return;
            foreach (var loadout in state.loadouts)
            {
                var entity = loadout;
                var active = entity.id == state.activeLoadoutId;
                var button = CreateButton(_loadoutPopup.transform, entity.id,
                    (active ? "✓ " : "  ") + entity.name, new Vector2(204f, 44f));
                button.GetComponent<Image>().color = active
                    ? new Color(0.46f, 0.33f, 0.16f, 0.92f)
                    : new Color(0.08f, 0.08f, 0.08f, 0.78f);
                button.onClick.AddListener(() =>
                {
                    _loadoutMessage = EquipmentLoadoutRuntime.Apply(entity);
                    if (string.IsNullOrEmpty(_loadoutMessage))
                    {
                        _panel.RefreshBag(false);
                        _loadoutPopup.SetActive(false);
                    }
                    else
                    {
                        RebuildLoadoutPopup();
                    }
                    RefreshLoadoutTrigger();
                });
                _loadoutButtons.Add(button);
            }
            var create = CreateButton(_loadoutPopup.transform, "CreateLoadout", "＋ 新建方案（复制当前）",
                new Vector2(204f, 44f));
            create.onClick.AddListener(() =>
            {
                EquipmentLoadoutRepository.CreateFromCurrent();
                _loadoutMessage = null;
                RebuildLoadoutPopup();
                RefreshLoadoutTrigger();
            });
            _loadoutButtons.Add(create);
            if (!string.IsNullOrEmpty(_loadoutMessage))
            {
                var message = CreateButton(_loadoutPopup.transform, "LoadoutMessage", _loadoutMessage,
                    new Vector2(204f, 44f));
                message.interactable = false;
                message.GetComponent<Image>().color = new Color(0.38f, 0.12f, 0.08f, 0.88f);
                _loadoutButtons.Add(message);
            }
            ((RectTransform)_loadoutPopup.transform).sizeDelta = new Vector2(220f,
                16f + _loadoutButtons.Count * 44f + Math.Max(0, _loadoutButtons.Count - 1) * 6f);
        }

        private void RefreshLoadoutTrigger()
        {
            var state = EquipmentLoadoutRepository.GetCurrentSaveState();
            var active = EquipmentLoadoutRepository.GetActiveLoadout(state);
            if (_loadoutTriggerLabel != null) _loadoutTriggerLabel.text = active?.name ?? "方案1";
        }

        private Button CreateButton(Transform parent, string name, string label, Vector2 size)
        {
            var obj = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button), typeof(LayoutElement));
            obj.layer = gameObject.layer;
            obj.transform.SetParent(parent, false);
            obj.GetComponent<Image>().color = new Color(0.08f, 0.08f, 0.08f, 0.78f);
            var rect = (RectTransform)obj.transform;
            rect.sizeDelta = size;
            var element = obj.GetComponent<LayoutElement>();
            element.preferredWidth = size.x;
            element.preferredHeight = size.y;
            var text = CreateText(obj.transform, "Text", label, size);
            text.alignment = TMPro.TextAlignmentOptions.Center;
            return obj.GetComponent<Button>();
        }

        private TextPro CreateText(Transform parent, string name, string value, Vector2 size)
        {
            var text = Instantiate(_textTemplate, parent);
            text.gameObject.name = name;
            foreach (var localization in text.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            text.text = value;
            text.fontSize = 22f;
            text.color = Color.white;
            var rect = (RectTransform)text.transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
            var element = text.gameObject.GetComponent<LayoutElement>() ?? text.gameObject.AddComponent<LayoutElement>();
            element.preferredWidth = size.x;
            element.preferredHeight = size.y;
            return text;
        }

    }

    [HarmonyPatch(typeof(BagPanel), nameof(BagPanel.ShowMe))]
    internal static class BagPanelEnhancementShowPatch
    {
        private static void Postfix(BagPanel __instance)
        {
            var ui = __instance.gameObject.GetComponent<BagEnhancementUi>() ??
                     __instance.gameObject.AddComponent<BagEnhancementUi>();
            ui.Initialize(__instance);
            ui.RefreshAll();
        }
    }

    [HarmonyPatch(typeof(BagPanel), nameof(BagPanel.ChangeType))]
    internal static class BagPanelDerivedFilterPatch
    {
        private static void Postfix(BagPanel __instance, int parentId, bool isbuild)
        {
            var filter = BagEnhancementState.GetFilter(parentId);
            if (filter == DerivedBagFilter.All) return;
            var traverse = Traverse.Create(__instance);
            var items = traverse.Field<List<TbPackSto>>("packList").Value;
            if (items == null) return;
            var filtered = items.Where(item => DerivedBagFiltering.Matches(item, filter)).ToList();
            traverse.Field<List<TbPackSto>>("packList").Value = filtered;
            if (isbuild) __instance.StopAllCoroutines();
            traverse.Method("GenerateItem", filtered).GetValue();
        }
    }

    [HarmonyPatch(typeof(BagPanel), "RefreshPlayerData")]
    internal static class BagPanelCompactNumberPatch
    {
        private static readonly string[] NumericTextFields =
        {
            "Txt_CurHp", "txtHP", "txtMagAtk", "txtMagDef", "txtPhyAtk", "txtPhyDef",
            "txtArmor", "txtSpeed", "txtLuck", "txtMind", "txtShield",
        };

        private static void Postfix(BagPanel __instance)
        {
            var view = Traverse.Create(__instance).Field<BagPanelView>("view").Value;
            if (view == null) return;
            var fields = Traverse.Create(view);
            foreach (var fieldName in NumericTextFields)
            {
                var text = fields.Field<TextPro>(fieldName).Value;
                if (text != null) text.text = CompactNumberDisplay.FormatText(text.text);
            }
        }
    }

    [HarmonyPatch(typeof(BattleObject), "SetUiData")]
    internal static class BattleObjectCompactHealthPatch
    {
        private static void Postfix(BattleObject __instance)
        {
            var view = Traverse.Create(__instance).Field<BattleObjectView>("UIView").Value;
            if (view == null) return;
            var healthText = Traverse.Create(view).Field<TextPro>("txt_Hp").Value;
            if (healthText != null) healthText.text = CompactNumberDisplay.FormatText(healthText.text);
        }
    }
}
