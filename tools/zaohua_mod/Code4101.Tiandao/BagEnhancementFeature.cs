using BepInEx.Configuration;
using HarmonyLib;
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
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
            _rememberDerivedFilter = config.Bind("储物空间", "记住衍生筛选", true, "分别记住丹药、装备、法宝、功法、术法和符箓的细分筛选");
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
            // BagPanel 与 TbTypeCfg 根类型一致：丹药（含丹方）=5，符箓=6。
            if (parentTypeId == 5) return "drug";
            if (parentTypeId == 6) return "talisman";
            if (name.Contains("丹") || name.Contains("药")) return "drug";
            if (name.Contains("符箓")) return "talisman";
            if (parentTypeId == 1 || name.Contains("防具") || name.Contains("装备") || name.Contains("服饰")) return "equipment";
            if (parentTypeId == 2 || name.Contains("法宝")) return "treasure";
            if (parentTypeId == 3 || name.Contains("功法")) return "art";
            if (parentTypeId == 4 || name.Contains("术法")) return "magic";
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
                    var nativeTypeId = (int)filter;
                    if ((nativeTypeId >= 151 && nativeTypeId <= 200) ||
                        (nativeTypeId >= 201 && nativeTypeId <= 207) ||
                        (nativeTypeId >= 221 && nativeTypeId <= 227) ||
                        (nativeTypeId >= 261 && nativeTypeId <= 280))
                        return item.itemCfg?.typeId == nativeTypeId;
                    if (nativeTypeId >= 301 && nativeTypeId <= 308)
                        return item.itemCfg != null && (int)item.itemCfg.attribute == nativeTypeId - 300;
                    return true;
            }
        }

        internal static List<(DerivedBagFilter Filter, string Label)> GetAvailableTreasureFilters()
        {
            var actor = BsSaveDataImpl.nowActor;
            if (actor?.packStoList == null) return new List<(DerivedBagFilter, string)>();
            return actor.packStoList
                .Where(item => item?.itemCfg != null)
                .Select(item => item.itemCfg.typeId)
                .Where(typeId => (typeId >= 151 && typeId <= 174) || typeId == 200)
                .Distinct()
                .OrderBy(typeId => typeId)
                .Select(typeId =>
                {
                    var label = Singleton<TbItemImpl>.Instance.GetTypeName(typeId) ?? typeId.ToString();
                    var separator = label.LastIndexOf('-');
                    if (separator >= 0 && separator + 1 < label.Length) label = label.Substring(separator + 1);
                    return ((DerivedBagFilter)typeId, label.Trim());
                })
                .Where(item => !string.IsNullOrEmpty(item.Item2))
                .ToList();
        }

        internal static List<(DerivedBagFilter Filter, string Label)> GetAvailableArtFilters()
        {
            return GetAvailableCultivationFilters(201, 207);
        }

        internal static List<(DerivedBagFilter Filter, string Label)> GetAvailableMagicFilters()
        {
            return GetAvailableCultivationFilters(221, 227);
        }

        internal static List<(DerivedBagFilter Filter, string Label)> GetAvailableTalismanFilters()
        {
            var actor = BsSaveDataImpl.nowActor;
            if (actor?.packStoList == null) return new List<(DerivedBagFilter, string)>();
            var validTypes = new HashSet<int> { 261, 262, 263, 264, 265, 280 };
            return actor.packStoList
                .Where(item => item?.itemCfg != null && validTypes.Contains(item.itemCfg.typeId))
                .Select(item => item.itemCfg.typeId)
                .Distinct()
                .OrderBy(typeId => typeId)
                .Select(typeId =>
                {
                    var label = Singleton<TbItemImpl>.Instance.GetTypeName(typeId) ?? typeId.ToString();
                    var separator = label.LastIndexOf('-');
                    if (separator >= 0 && separator + 1 < label.Length) label = label.Substring(separator + 1);
                    return ((DerivedBagFilter)typeId, label.Trim());
                })
                .Where(item => !string.IsNullOrEmpty(item.Item2))
                .ToList();
        }

        private static List<(DerivedBagFilter Filter, string Label)> GetAvailableCultivationFilters(
            int minimumTypeId,
            int maximumTypeId)
        {
            var actor = BsSaveDataImpl.nowActor;
            if (actor?.packStoList == null) return new List<(DerivedBagFilter, string)>();
            var arts = actor.packStoList
                .Where(item => item?.itemCfg != null &&
                               item.itemCfg.typeId >= minimumTypeId && item.itemCfg.typeId <= maximumTypeId)
                .ToList();
            var systems = arts.Select(item => item.itemCfg.typeId)
                .Distinct()
                .OrderBy(typeId => typeId)
                .Select(typeId =>
                {
                    var label = Singleton<TbItemImpl>.Instance.GetTypeName(typeId) ?? typeId.ToString();
                    var separator = label.LastIndexOf('-');
                    if (separator >= 0 && separator + 1 < label.Length) label = label.Substring(separator + 1);
                    return ((DerivedBagFilter)typeId, label.Trim());
                });
            var attributes = arts.Select(item => (int)item.itemCfg.attribute)
                .Where(attribute => attribute >= 1 && attribute <= 8)
                .Distinct()
                .OrderBy(attribute => attribute)
                .Select(attribute => ((DerivedBagFilter)(300 + attribute), AttributeLabel(attribute)));
            return systems.Concat(attributes)
                .Where(item => !string.IsNullOrEmpty(item.Item2))
                .ToList();
        }

        private static string AttributeLabel(int attribute)
        {
            switch (attribute)
            {
                case 1: return "金";
                case 2: return "水";
                case 3: return "木";
                case 4: return "火";
                case 5: return "土";
                case 6: return "冰";
                case 7: return "风";
                case 8: return "雷";
                default: return string.Empty;
            }
        }

        private static bool ContainsAny(string text, params string[] values)
        {
            return values.Any(text.Contains);
        }
    }

    internal sealed class InlineRenameCaret : MonoBehaviour
    {
        internal TMPro.TMP_InputField Input;
        internal TextPro Text;
        internal RectTransform Viewport;
        internal Image Caret;

        private void Update()
        {
            if (Input == null || Text == null || Viewport == null || Caret == null) return;
            Caret.enabled = Input.isFocused && Mathf.FloorToInt(Time.unscaledTime * 2f) % 2 == 0;
            if (!Input.isFocused) return;
            var position = Mathf.Clamp(Input.stringPosition, 0, Input.text?.Length ?? 0);
            var prefix = position == 0 ? string.Empty : Input.text.Substring(0, position);
            var x = Mathf.Min(Text.GetPreferredValues(prefix).x, Mathf.Max(0f, Viewport.rect.width - 3f));
            Caret.rectTransform.anchoredPosition = new Vector2(x, 0f);
        }
    }

    internal sealed class BagEnhancementUi : MonoBehaviour
    {
        private BagPanel _panel;
        private TextPro _textTemplate;
        private GameObject _filterPopup;
        private GridLayoutGroup _filterGridLayout;
        private Button _filterTrigger;
        private TextPro _filterTriggerLabel;
        private GameObject _loadoutPopup;
        private RectTransform _loadoutContent;
        private ScrollRect _loadoutScroll;
        private Button _loadoutTrigger;
        private TextPro _loadoutTriggerLabel;
        private Button _loadoutRenameButton;
        private Button _loadoutDeleteButton;
        private TextPro _loadoutDeleteLabel;
        private float _deleteConfirmationUntil;
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
            if (_deleteConfirmationUntil > 0f && Time.unscaledTime > _deleteConfirmationUntil)
            {
                ResetDeleteConfirmation();
            }
            if (_loadoutPopup != null && _loadoutPopup.activeSelf && Input.GetMouseButtonDown(0) &&
                !ContainsScreenPoint((RectTransform)_loadoutPopup.transform) &&
                !ContainsScreenPoint((RectTransform)_loadoutTrigger.transform))
            {
                CloseLoadoutPopup();
            }
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
            var rememberToggle = viewFields.Field<Toggle>("togRememberSelect").Value;
            _filterTrigger = Instantiate(gradeButton, gradeButton.transform.parent);
            _filterTrigger.gameObject.name = "Code4101DerivedFilterTrigger";
            _filterTrigger.onClick.RemoveAllListeners();
            foreach (var localization in _filterTrigger.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var triggerTexts = _filterTrigger.GetComponentsInChildren<TextPro>(true);
            _filterTriggerLabel = triggerTexts.FirstOrDefault();
            if (_filterTriggerLabel != null) _filterTriggerLabel.text = "全部";
            for (var i = 1; i < triggerTexts.Length; i++) triggerTexts[i].text = string.Empty;

            var gradeRect = (RectTransform)gradeButton.transform;
            var triggerRect = (RectTransform)_filterTrigger.transform;
            var controlParent = gradeButton.transform.parent;
            var rememberBounds = RectTransformUtility.CalculateRelativeRectTransformBounds(
                controlParent, rememberToggle.transform);
            triggerRect.localPosition = new Vector3(
                rememberBounds.max.x + 30f,
                gradeRect.localPosition.y,
                gradeRect.localPosition.z);
            _filterTrigger.onClick.AddListener(() =>
            {
                _filterPopup.SetActive(!_filterPopup.activeSelf);
                if (_filterPopup.activeSelf) _filterPopup.transform.SetAsLastSibling();
            });

            _filterPopup = new GameObject("Code4101DerivedFilterPopup",
                typeof(RectTransform), typeof(Image), typeof(GridLayoutGroup));
            _filterPopup.layer = gameObject.layer;
            _filterPopup.transform.SetParent(controlParent, false);
            var popupRect = (RectTransform)_filterPopup.transform;
            popupRect.anchorMin = triggerRect.anchorMin;
            popupRect.anchorMax = triggerRect.anchorMax;
            popupRect.pivot = new Vector2(0.5f, 1f);
            popupRect.localPosition = triggerRect.localPosition + new Vector3(0f, -45f, 0f);
            popupRect.sizeDelta = new Vector2(170f, 260f);
            _filterPopup.GetComponent<Image>().color = new Color(0.055f, 0.045f, 0.035f, 0.96f);
            _filterGridLayout = _filterPopup.GetComponent<GridLayoutGroup>();
            _filterGridLayout.padding = new RectOffset(8, 8, 8, 8);
            _filterGridLayout.spacing = new Vector2(6f, 6f);
            _filterGridLayout.cellSize = new Vector2(150f, 44f);
            _filterGridLayout.constraint = GridLayoutGroup.Constraint.FixedColumnCount;
            _filterGridLayout.constraintCount = 1;
            _filterPopup.SetActive(false);
        }

        private void CreateLoadoutMenu(Traverse viewFields, Transform equipRoot)
        {
            var gradeButton = viewFields.Field<Button>("btnGrade").Value;
            var controlParent = gradeButton.transform.parent;
            _loadoutTrigger = Instantiate(gradeButton, controlParent);
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
                var targetInEquip = new Vector3(helmetBounds.center.x, helmetBounds.max.y + 55f, 0f);
                var targetWorld = equipRoot.TransformPoint(targetInEquip);
                triggerRect.localPosition = controlParent.InverseTransformPoint(targetWorld);
            }
            else
            {
                triggerRect.localPosition = new Vector3(-360f, 150f, 0f);
            }
            _loadoutTrigger.transform.SetAsLastSibling();

            // 当前方案的低频管理动作集中放在标题右侧；下拉列表只负责选择方案。
            _loadoutRenameButton = CreateLoadoutActionButton(controlParent, triggerRect,
                "Code4101LoadoutRename", "改名", 114f, false);
            _loadoutRenameButton.onClick.AddListener(BeginActiveLoadoutRename);
            _loadoutDeleteButton = CreateLoadoutActionButton(controlParent, triggerRect,
                "Code4101LoadoutDelete", "删除", 178f, true);
            _loadoutDeleteLabel = _loadoutDeleteButton.GetComponentInChildren<TextPro>();
            _loadoutDeleteButton.onClick.AddListener(DeleteActiveLoadout);
            Debug.Log($"[Code4101 Tiandao][LoadoutUI] management-actions-created " +
                      $"parent={controlParent.name} trigger={triggerRect.localPosition} " +
                      $"rename={((RectTransform)_loadoutRenameButton.transform).localPosition} " +
                      $"delete={((RectTransform)_loadoutDeleteButton.transform).localPosition}");

            _loadoutTrigger.onClick.AddListener(() =>
            {
                if (_loadoutPopup.activeSelf)
                {
                    CloseLoadoutPopup();
                    return;
                }
                RebuildLoadoutPopup();
                _loadoutPopup.SetActive(true);
                _loadoutPopup.transform.SetAsLastSibling();
            });

            _loadoutPopup = new GameObject("Code4101EquipmentLoadoutPopup",
                typeof(RectTransform), typeof(Image), typeof(ScrollRect));
            _loadoutPopup.layer = gameObject.layer;
            _loadoutPopup.transform.SetParent(controlParent, false);
            var popupRect = (RectTransform)_loadoutPopup.transform;
            popupRect.anchorMin = triggerRect.anchorMin;
            popupRect.anchorMax = triggerRect.anchorMax;
            popupRect.pivot = new Vector2(0.5f, 0f);
            popupRect.localPosition = triggerRect.localPosition + new Vector3(0f, 45f, 0f);
            popupRect.sizeDelta = new Vector2(220f, 120f);
            _loadoutPopup.GetComponent<Image>().color = new Color(0.055f, 0.045f, 0.035f, 0.96f);

            var viewportObject = new GameObject("Viewport", typeof(RectTransform), typeof(RectMask2D));
            viewportObject.layer = gameObject.layer;
            viewportObject.transform.SetParent(_loadoutPopup.transform, false);
            var viewportRect = (RectTransform)viewportObject.transform;
            viewportRect.anchorMin = Vector2.zero;
            viewportRect.anchorMax = Vector2.one;
            viewportRect.offsetMin = Vector2.zero;
            viewportRect.offsetMax = new Vector2(-18f, 0f);

            var contentObject = new GameObject("Content", typeof(RectTransform), typeof(VerticalLayoutGroup));
            contentObject.layer = gameObject.layer;
            contentObject.transform.SetParent(viewportObject.transform, false);
            _loadoutContent = (RectTransform)contentObject.transform;
            _loadoutContent.anchorMin = new Vector2(0f, 1f);
            _loadoutContent.anchorMax = new Vector2(1f, 1f);
            _loadoutContent.pivot = new Vector2(0.5f, 1f);
            _loadoutContent.anchoredPosition = Vector2.zero;
            var layout = contentObject.GetComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(8, 8, 8, 8);
            layout.spacing = 6f;
            layout.childAlignment = TextAnchor.UpperCenter;
            layout.childControlWidth = false;
            layout.childControlHeight = false;

            var nativeScrollbar = viewFields.Field<Scrollbar>("Scrollbar").Value;
            var scrollbar = Instantiate(nativeScrollbar, _loadoutPopup.transform);
            scrollbar.gameObject.name = "Code4101LoadoutScrollbar";
            scrollbar.onValueChanged.RemoveAllListeners();
            scrollbar.direction = Scrollbar.Direction.BottomToTop;
            var scrollbarRect = (RectTransform)scrollbar.transform;
            scrollbarRect.anchorMin = new Vector2(1f, 0f);
            scrollbarRect.anchorMax = new Vector2(1f, 1f);
            scrollbarRect.pivot = new Vector2(1f, 0.5f);
            scrollbarRect.anchoredPosition = Vector2.zero;
            scrollbarRect.sizeDelta = new Vector2(16f, 0f);

            _loadoutScroll = _loadoutPopup.GetComponent<ScrollRect>();
            _loadoutScroll.viewport = viewportRect;
            _loadoutScroll.content = _loadoutContent;
            _loadoutScroll.horizontal = false;
            _loadoutScroll.vertical = true;
            _loadoutScroll.movementType = ScrollRect.MovementType.Clamped;
            _loadoutScroll.scrollSensitivity = 38f;
            _loadoutScroll.verticalScrollbar = scrollbar;
            _loadoutScroll.verticalScrollbarVisibility = ScrollRect.ScrollbarVisibility.Permanent;
            _loadoutPopup.SetActive(false);
            RefreshLoadoutTrigger();
        }

        private Button CreateLoadoutActionButton(Transform parent, RectTransform triggerRect,
            string name, string label, float xOffset, bool destructive)
        {
            var button = CreateButton(parent, name, label, new Vector2(56f, 36f));
            var text = button.GetComponentInChildren<TextPro>();
            if (text != null)
            {
                text.fontSize = 17f;
                text.color = destructive
                    ? new Color(0.92f, 0.72f, 0.65f, 1f)
                    : new Color(0.96f, 0.82f, 0.54f, 1f);
            }
            SetLoadoutActionStyle(button, false);
            var rect = (RectTransform)button.transform;
            rect.anchorMin = triggerRect.anchorMin;
            rect.anchorMax = triggerRect.anchorMax;
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.localPosition = triggerRect.localPosition + new Vector3(xOffset, 0f, 0f);
            rect.sizeDelta = new Vector2(56f, 36f);
            button.transform.SetAsLastSibling();
            return button;
        }

        private static void SetLoadoutActionStyle(Button button, bool confirmingDelete)
        {
            if (button == null) return;
            var normal = confirmingDelete
                ? new Color(0.46f, 0.09f, 0.045f, 0.90f)
                : new Color(0.055f, 0.045f, 0.035f, 0.34f);
            var highlighted = confirmingDelete
                ? new Color(0.58f, 0.11f, 0.055f, 0.96f)
                : new Color(0.12f, 0.095f, 0.065f, 0.62f);
            var pressed = confirmingDelete
                ? new Color(0.36f, 0.055f, 0.03f, 0.98f)
                : new Color(0.035f, 0.028f, 0.022f, 0.72f);
            var colors = button.colors;
            colors.normalColor = normal;
            colors.highlightedColor = highlighted;
            colors.selectedColor = highlighted;
            colors.pressedColor = pressed;
            colors.disabledColor = new Color(normal.r, normal.g, normal.b, 0.16f);
            colors.colorMultiplier = 1f;
            colors.fadeDuration = 0.08f;
            button.colors = colors;
            var image = button.GetComponent<Image>();
            if (image != null) image.color = normal;
        }

        private void CloseLoadoutPopup()
        {
            if (_loadoutPopup == null) return;
            var input = _loadoutPopup.GetComponentInChildren<TMPro.TMP_InputField>(true);
            if (input != null && input.isFocused) input.DeactivateInputField();
            _loadoutPopup.SetActive(false);
        }

        private static bool ContainsScreenPoint(RectTransform rect)
        {
            if (rect == null) return false;
            var canvas = rect.GetComponentInParent<Canvas>();
            var camera = canvas != null && canvas.renderMode != RenderMode.ScreenSpaceOverlay
                ? canvas.worldCamera
                : null;
            return RectTransformUtility.RectangleContainsScreenPoint(rect, Input.mousePosition, camera);
        }

        private void RebuildFilterButtons(int parentType)
        {
            foreach (var button in _filterButtons) Destroy(button.gameObject);
            _filterButtons.Clear();
            var context = DerivedBagFiltering.GetContext(parentType);
            _filterTrigger.gameObject.SetActive(context != null);
            _filterPopup.SetActive(false);
            _loadoutTrigger.gameObject.SetActive(true);
            _loadoutPopup.SetActive(false);
            if (context == null) return;
            AddFilterButton(parentType, DerivedBagFilter.All, "全部");
            if (context == "drug")
            {
                var selected = BagEnhancementState.GetFilter(parentType);
                if (selected != DerivedBagFilter.All && selected != DerivedBagFilter.Pill &&
                    selected != DerivedBagFilter.Recipe)
                    BagEnhancementState.SetFilter(parentType, DerivedBagFilter.All);
                AddFilterButton(parentType, DerivedBagFilter.Pill, "丹药");
                AddFilterButton(parentType, DerivedBagFilter.Recipe, "丹方");
            }
            else if (context == "equipment")
            {
                var selected = BagEnhancementState.GetFilter(parentType);
                if (selected != DerivedBagFilter.All && selected != DerivedBagFilter.Helmet &&
                    selected != DerivedBagFilter.Clothes && selected != DerivedBagFilter.Shoes &&
                    selected != DerivedBagFilter.Ornament)
                    BagEnhancementState.SetFilter(parentType, DerivedBagFilter.All);
                AddFilterButton(parentType, DerivedBagFilter.Helmet, "头饰");
                AddFilterButton(parentType, DerivedBagFilter.Clothes, "服饰");
                AddFilterButton(parentType, DerivedBagFilter.Shoes, "鞋履");
                AddFilterButton(parentType, DerivedBagFilter.Ornament, "饰品");
            }
            else if (context == "treasure")
            {
                var available = DerivedBagFiltering.GetAvailableTreasureFilters();
                foreach (var option in available) AddFilterButton(parentType, option.Filter, option.Label);
                var selected = BagEnhancementState.GetFilter(parentType);
                if (selected != DerivedBagFilter.All && available.All(option => option.Filter != selected))
                    BagEnhancementState.SetFilter(parentType, DerivedBagFilter.All);
            }
            else if (context == "art" || context == "magic")
            {
                var available = context == "magic"
                    ? DerivedBagFiltering.GetAvailableMagicFilters()
                    : DerivedBagFiltering.GetAvailableArtFilters();
                foreach (var option in available) AddFilterButton(parentType, option.Filter, option.Label);
                var selected = BagEnhancementState.GetFilter(parentType);
                if (selected != DerivedBagFilter.All && available.All(option => option.Filter != selected))
                    BagEnhancementState.SetFilter(parentType, DerivedBagFilter.All);
            }
            else
            {
                var available = DerivedBagFiltering.GetAvailableTalismanFilters();
                foreach (var option in available) AddFilterButton(parentType, option.Filter, option.Label);
                var selected = BagEnhancementState.GetFilter(parentType);
                if (selected != DerivedBagFilter.All && available.All(option => option.Filter != selected))
                    BagEnhancementState.SetFilter(parentType, DerivedBagFilter.All);
            }
            var popupRect = (RectTransform)_filterPopup.transform;
            const int comfortableRowsPerColumn = 7;
            const int maximumColumns = 4;
            const float compactCellWidth = 100f;
            const float singleColumnWidth = 150f;
            const float cellHeight = 44f;
            const float spacing = 6f;
            const float padding = 16f;
            var itemCount = Math.Max(1, _filterButtons.Count);
            var columns = Mathf.Clamp(
                Mathf.CeilToInt(itemCount / (float)comfortableRowsPerColumn), 1, maximumColumns);
            var rows = Mathf.CeilToInt(itemCount / (float)columns);
            var cellWidth = columns == 1 ? singleColumnWidth : compactCellWidth;
            _filterGridLayout.constraintCount = columns;
            _filterGridLayout.cellSize = new Vector2(cellWidth, cellHeight);
            popupRect.sizeDelta = new Vector2(
                padding + columns * cellWidth + Math.Max(0, columns - 1) * spacing,
                padding + rows * cellHeight + Math.Max(0, rows - 1) * spacing);
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
            ClearLoadoutPopup();
            var state = EquipmentLoadoutRepository.GetCurrentSaveState();
            if (state == null) return;
            foreach (var loadout in state.loadouts)
            {
                var entity = loadout;
                var active = entity.id == state.activeLoadoutId;
                var button = CreateButton(_loadoutContent, entity.id,
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
            var create = CreateButton(_loadoutContent, "CreateLoadout", "＋ 新建方案",
                new Vector2(204f, 44f));
            create.onClick.AddListener(() =>
            {
                var entity = EquipmentLoadoutRepository.CreateLoadoutFromCurrent();
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
            _loadoutButtons.Add(create);
            if (!string.IsNullOrEmpty(_loadoutMessage))
            {
                var message = CreateButton(_loadoutContent, "LoadoutMessage", _loadoutMessage,
                    new Vector2(204f, 44f));
                message.interactable = false;
                message.GetComponent<Image>().color = new Color(0.38f, 0.12f, 0.08f, 0.88f);
                _loadoutButtons.Add(message);
            }
            var contentHeight = 16f + _loadoutButtons.Count * 44f +
                                Math.Max(0, _loadoutButtons.Count - 1) * 6f;
            _loadoutContent.sizeDelta = new Vector2(-18f, contentHeight);
            ((RectTransform)_loadoutPopup.transform).sizeDelta =
                new Vector2(220f, Mathf.Min(contentHeight, 360f));
            _loadoutScroll.verticalNormalizedPosition = 1f;
        }

        private void BeginActiveLoadoutRename()
        {
            var state = EquipmentLoadoutRepository.GetCurrentSaveState();
            var entity = EquipmentLoadoutRepository.GetActiveLoadout(state);
            if (entity == null || _loadoutTrigger == null) return;
            CloseLoadoutPopup();
            ResetDeleteConfirmation();
            _loadoutTrigger.interactable = false;
            _loadoutRenameButton.gameObject.SetActive(false);
            _loadoutDeleteButton.gameObject.SetActive(false);
            if (_loadoutTriggerLabel != null) _loadoutTriggerLabel.gameObject.SetActive(false);

            var inputObject = new GameObject("LoadoutNameInput", typeof(RectTransform), typeof(Image),
                typeof(TMPro.TMP_InputField));
            inputObject.layer = gameObject.layer;
            inputObject.transform.SetParent(_loadoutTrigger.transform, false);
            inputObject.GetComponent<Image>().color = new Color(0.12f, 0.11f, 0.09f, 0.96f);
            var inputRect = (RectTransform)inputObject.transform;
            inputRect.anchorMin = Vector2.zero;
            inputRect.anchorMax = Vector2.one;
            inputRect.offsetMin = new Vector2(6f, 4f);
            inputRect.offsetMax = new Vector2(-6f, -4f);
            var viewportObject = new GameObject("TextViewport", typeof(RectTransform), typeof(RectMask2D));
            viewportObject.layer = gameObject.layer;
            viewportObject.transform.SetParent(inputObject.transform, false);
            var viewportRect = (RectTransform)viewportObject.transform;
            viewportRect.anchorMin = Vector2.zero;
            viewportRect.anchorMax = Vector2.one;
            viewportRect.offsetMin = new Vector2(8f, 0f);
            viewportRect.offsetMax = new Vector2(-8f, 0f);
            var inputText = CreateText(viewportObject.transform, "InputText", entity.name, new Vector2(176f, 36f));
            inputText.alignment = TMPro.TextAlignmentOptions.MidlineLeft;
            var textRect = (RectTransform)inputText.transform;
            textRect.offsetMin = Vector2.zero;
            textRect.offsetMax = Vector2.zero;
            var input = inputObject.GetComponent<TMPro.TMP_InputField>();
            input.textViewport = viewportRect;
            input.textComponent = inputText;
            input.lineType = TMPro.TMP_InputField.LineType.SingleLine;
            input.characterLimit = 12;
            input.interactable = true;
            input.readOnly = false;
            input.customCaretColor = true;
            input.caretColor = Color.white;
            input.caretWidth = 2;
            input.caretBlinkRate = 0.65f;
            input.text = entity.name;

            var caretObject = new GameObject("Code4101VisibleCaret", typeof(RectTransform), typeof(Image));
            caretObject.layer = gameObject.layer;
            caretObject.transform.SetParent(viewportObject.transform, false);
            var caret = caretObject.GetComponent<Image>();
            caret.color = Color.white;
            caret.raycastTarget = false;
            var caretRect = (RectTransform)caret.transform;
            caretRect.anchorMin = new Vector2(0f, 0.5f);
            caretRect.anchorMax = new Vector2(0f, 0.5f);
            caretRect.pivot = new Vector2(0.5f, 0.5f);
            caretRect.sizeDelta = new Vector2(2f, 28f);
            caretRect.anchoredPosition = Vector2.zero;
            var visibleCaret = inputObject.AddComponent<InlineRenameCaret>();
            visibleCaret.Input = input;
            visibleCaret.Text = inputText;
            visibleCaret.Viewport = viewportRect;
            visibleCaret.Caret = caret;

            var finished = false;
            void FinishRename(string value)
            {
                if (finished) return;
                finished = true;
                EquipmentLoadoutRepository.Rename(entity, value);
                Destroy(inputObject);
                _loadoutTrigger.interactable = true;
                if (_loadoutTriggerLabel != null) _loadoutTriggerLabel.gameObject.SetActive(true);
                _loadoutRenameButton.gameObject.SetActive(true);
                RefreshLoadoutTrigger();
            }

            input.onSubmit.AddListener(FinishRename);
            input.onEndEdit.AddListener(FinishRename);
            StartCoroutine(FocusRenameInput(input));
        }

        private void DeleteActiveLoadout()
        {
            var state = EquipmentLoadoutRepository.GetCurrentSaveState();
            var active = EquipmentLoadoutRepository.GetActiveLoadout(state);
            if (state?.loadouts == null || active == null || state.loadouts.Count <= 1) return;
            if (_deleteConfirmationUntil <= 0f || Time.unscaledTime > _deleteConfirmationUntil)
            {
                _deleteConfirmationUntil = Time.unscaledTime + 3f;
                if (_loadoutDeleteLabel != null) _loadoutDeleteLabel.text = "确认";
                SetLoadoutActionStyle(_loadoutDeleteButton, true);
                return;
            }

            ResetDeleteConfirmation();
            var activeIndex = state.loadouts.IndexOf(active);
            var fallback = activeIndex > 0
                ? state.loadouts[activeIndex - 1]
                : state.loadouts.Skip(1).FirstOrDefault(loadout => loadout != null);
            if (fallback == null) return;
            _loadoutMessage = EquipmentLoadoutRuntime.Apply(fallback);
            if (!string.IsNullOrEmpty(_loadoutMessage))
            {
                RebuildLoadoutPopup();
                _loadoutPopup.SetActive(true);
                return;
            }
            EquipmentLoadoutRepository.Delete(active);
            _panel.RefreshBag(false);
            RefreshLoadoutTrigger();
        }

        private void ResetDeleteConfirmation()
        {
            _deleteConfirmationUntil = 0f;
            if (_loadoutDeleteLabel != null) _loadoutDeleteLabel.text = "删除";
            SetLoadoutActionStyle(_loadoutDeleteButton, false);
        }

        private static IEnumerator FocusRenameInput(TMPro.TMP_InputField input)
        {
            // 等待触发编辑的笔按钮完成本帧点击，避免 EventSystem 把焦点重新留给旧按钮。
            yield return null;
            if (input == null) yield break;
            input.Select();
            input.ActivateInputField();
            // TMP 会在激活后的 LateUpdate 初始化编辑状态；同帧移动光标会被重置到开头。
            yield return null;
            if (input == null) yield break;
            var end = input.text?.Length ?? 0;
            input.stringPosition = end;
            input.selectionStringAnchorPosition = end;
            input.selectionStringFocusPosition = end;
            input.MoveTextEnd(false);
            input.ForceLabelUpdate();
        }

        private void ClearLoadoutPopup()
        {
            foreach (Transform child in _loadoutContent) Destroy(child.gameObject);
            _loadoutButtons.Clear();
        }

        private void RefreshLoadoutTrigger()
        {
            var state = EquipmentLoadoutRepository.GetCurrentSaveState();
            var active = EquipmentLoadoutRepository.GetActiveLoadout(state);
            if (_loadoutTriggerLabel != null) _loadoutTriggerLabel.text = active?.name ?? "方案1";
            var controlsEnabled = active != null && _loadoutTrigger != null && _loadoutTrigger.interactable;
            if (_loadoutRenameButton != null)
            {
                _loadoutRenameButton.gameObject.SetActive(controlsEnabled);
                if (controlsEnabled) _loadoutRenameButton.transform.SetAsLastSibling();
            }
            if (_loadoutDeleteButton != null)
            {
                var canDelete = controlsEnabled && state?.loadouts != null && state.loadouts.Count > 1;
                _loadoutDeleteButton.gameObject.SetActive(canDelete);
                if (canDelete) _loadoutDeleteButton.transform.SetAsLastSibling();
            }
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

    internal sealed class BagNameSortUi : MonoBehaviour
    {
        private enum ExtraSortKind
        {
            None,
            Name,
            Hp,
            SorAtt,
            SorDef,
            PhyAtt,
            PhyDef,
            Armor,
            Shield,
            Speed,
            Fate,
        }

        private static readonly (ExtraSortKind Kind, ArtAttrEnum Attribute, string Label)[] EquipmentSorts =
        {
            (ExtraSortKind.Hp, ArtAttrEnum.Hp, "气血"),
            (ExtraSortKind.SorAtt, ArtAttrEnum.SorAtt, "术攻"),
            (ExtraSortKind.SorDef, ArtAttrEnum.SorDef, "术防"),
            (ExtraSortKind.PhyAtt, ArtAttrEnum.PhyAtt, "物攻"),
            (ExtraSortKind.PhyDef, ArtAttrEnum.PhyDef, "物防"),
            (ExtraSortKind.Armor, ArtAttrEnum.Armor, "护甲"),
            (ExtraSortKind.Shield, ArtAttrEnum.Shield, "护盾"),
            (ExtraSortKind.Speed, ArtAttrEnum.Advance, "速度"),
            (ExtraSortKind.Fate, ArtAttrEnum.Fate, "气运"),
        };

        private BagPanel _panel;
        private Toggle _nameToggle;
        private TextPro _nameLabel;
        private readonly Dictionary<ExtraSortKind, Toggle> _attributeToggles =
            new Dictionary<ExtraSortKind, Toggle>();
        private GameObject _sortPopup;
        private Toggle[] _nativeToggles;
        private Vector2 _rowStep;
        private Vector2 _nativePopupPosition;
        private float _nativePopupHeight;
        private Quaternion _nativeDescendingArrowRotation;
        private Vector3 _nativeArrowScale = Vector3.one;
        private string _nativeArrowText = "▼";
        private readonly Dictionary<Toggle, TextPro> _sortArrowTexts =
            new Dictionary<Toggle, TextPro>();
        private ExtraSortKind _kind;
        private bool _equipmentContext;
        private bool _initialized;

        internal bool Active => _kind != ExtraSortKind.None;
        internal bool Ascending { get; private set; }

        internal void Initialize(BagPanel panel)
        {
            if (_initialized) return;
            _initialized = true;
            _panel = panel;
            var view = Traverse.Create(panel).Field<BagPanelView>("view").Value;
            var fields = Traverse.Create(view);
            var typeToggle = fields.Field<Toggle>("togSortType").Value;
            var levelToggle = fields.Field<Toggle>("togSortLevel").Value;
            var timeToggle = fields.Field<Toggle>("togSortTime").Value;
            var timeTexts = timeToggle.GetComponentsInChildren<TextPro>(true);
            var timeArrow = timeTexts.Skip(1).FirstOrDefault(text => !string.IsNullOrWhiteSpace(text.text));
            if (timeArrow != null)
            {
                _nativeDescendingArrowRotation = timeArrow.rectTransform.localRotation;
                _nativeArrowScale = timeArrow.rectTransform.localScale;
                _nativeArrowText = timeArrow.text;
            }
            _sortPopup = typeToggle.transform.parent.gameObject;
            var sortGroup = _sortPopup.GetComponent<ToggleGroup>() ?? _sortPopup.AddComponent<ToggleGroup>();
            sortGroup.allowSwitchOff = true;
            _nameToggle = Instantiate(typeToggle, typeToggle.transform.parent);
            _nameToggle.gameObject.name = "Code4101SortByName";
            ConfigureToggle(_nameToggle, out _nameLabel);
            _nameToggle.group = sortGroup;

            var typeRect = (RectTransform)typeToggle.transform;
            var levelRect = (RectTransform)levelToggle.transform;
            var nameRect = (RectTransform)_nameToggle.transform;
            _rowStep = typeRect.anchoredPosition - levelRect.anchoredPosition;
            // 名称排序直接接替被移除的类别排序位置。
            nameRect.anchoredPosition = typeRect.anchoredPosition;
            nameRect.SetAsLastSibling();
            typeToggle.gameObject.SetActive(false);
            var popupRect = (RectTransform)_sortPopup.transform;
            _nativePopupPosition = popupRect.anchoredPosition;
            _nativePopupHeight = popupRect.sizeDelta.y;

            for (var index = 0; index < EquipmentSorts.Length; index++)
            {
                var definition = EquipmentSorts[index];
                var toggle = Instantiate(typeToggle, typeToggle.transform.parent);
                toggle.gameObject.name = "Code4101SortBy" + definition.Kind;
                ConfigureToggle(toggle, out var label);
                toggle.group = sortGroup;
                label.text = definition.Label + "排序";
                var rowRect = (RectTransform)toggle.transform;
                rowRect.anchoredPosition = nameRect.anchoredPosition + _rowStep * (index + 1);
                rowRect.SetAsLastSibling();
                toggle.gameObject.SetActive(false);
                toggle.onValueChanged.AddListener(_ =>
                {
                    Select(definition.Kind);
                    RefreshLabel();
                    _sortPopup.SetActive(false);
                    _panel.RefreshBag(false);
                });
                _attributeToggles[definition.Kind] = toggle;
            }

            _nativeToggles = new[]
                     {
                         fields.Field<Toggle>("togSortTime").Value,
                         fields.Field<Toggle>("togSortValue").Value,
                         fields.Field<Toggle>("togSortLevel").Value,
                     };
            foreach (var nativeToggle in _nativeToggles) nativeToggle.group = sortGroup;
            foreach (var nativeToggle in _nativeToggles)
            {
                nativeToggle.onValueChanged.AddListener(isOn =>
                {
                    if (!isOn) return;
                    _kind = ExtraSortKind.None;
                    foreach (var candidate in _nativeToggles)
                    {
                        foreach (var arrow in candidate.GetComponentsInChildren<TextPro>(true).Skip(1))
                            arrow.gameObject.SetActive(candidate == nativeToggle);
                    }
                    RefreshNativeArrows();
                });
            }

            _nameToggle.onValueChanged.AddListener(isOn =>
            {
                Select(ExtraSortKind.Name);
                RefreshLabel();
                _sortPopup.SetActive(false);
                _panel.RefreshBag(false);
            });
            RefreshLabel();
        }

        internal void UpdateContext(int parentId)
        {
            if (!_initialized) return;
            _equipmentContext = parentId == 1;
            foreach (var toggle in _attributeToggles.Values) toggle.gameObject.SetActive(_equipmentContext);
            if (!_equipmentContext)
            {
                if (_kind != ExtraSortKind.None && _kind != ExtraSortKind.Name) _kind = ExtraSortKind.None;
            }
            ResizePopup(_equipmentContext ? EquipmentSorts.Length : 0);
            RefreshLabel();
        }

        private void ResizePopup(int addedRows)
        {
            var popupRect = (RectTransform)_sortPopup.transform;
            var addedHeight = addedRows * Mathf.Abs(_rowStep.y);
            popupRect.sizeDelta = new Vector2(popupRect.sizeDelta.x, _nativePopupHeight + addedHeight);
            // 原生面板的 pivot 位于中部。只增加高度会同时向上、向下扩张，导致顶部溢出屏幕。
            // 抵消向上的增量，保持原菜单顶部不变，让扩展行只沿清单方向向下增长。
            popupRect.anchoredPosition = _nativePopupPosition
                                         + Vector2.down * (addedHeight * (1f - popupRect.pivot.y));
        }

        internal List<TbPackSto> Sort(IEnumerable<TbPackSto> items)
        {
            var direction = Ascending ? 1 : -1;
            if (_kind != ExtraSortKind.Name)
            {
                var definition = EquipmentSorts.First(entry => entry.Kind == _kind);
                var values = new Dictionary<int, int>();
                foreach (var item in items)
                    values[item.id] = GetEquipmentAttribute(item, definition.Attribute);
                return items.OrderBy(item => item, Comparer<TbPackSto>.Create((left, right) =>
                {
                    var compared = values[left.id].CompareTo(values[right.id]);
                    if (compared == 0) compared = string.Compare(left?.name, right?.name, StringComparison.CurrentCulture);
                    if (compared == 0) compared = (left?.id ?? 0).CompareTo(right?.id ?? 0);
                    return compared * direction;
                })).ToList();
            }
            var nameItems = items.ToList();
            var gradeWeights = nameItems.ToDictionary(item => item.id, item =>
            {
                var grade = item?.itemCfg == null
                    ? null
                    : Singleton<TbDataImpl>.Instance.GetGradeCfg(item.itemCfg.gradeId);
                return grade?.weight ?? item?.itemCfg?.gradeId ?? 0;
            });
            return nameItems.OrderBy(item => item, Comparer<TbPackSto>.Create((left, right) =>
            {
                // 主键只反转品阶；名称始终升序，使同品阶套装稳定聚在一起。
                var compared = gradeWeights[left.id].CompareTo(gradeWeights[right.id]) * direction;
                if (compared == 0)
                    compared = string.Compare(left?.name, right?.name, StringComparison.CurrentCulture);
                if (compared == 0) compared = (left?.id ?? 0).CompareTo(right?.id ?? 0);
                return compared;
            })).ToList();
        }

        private void RefreshLabel()
        {
            if (_nameLabel != null) _nameLabel.text = "名称排序";
            RefreshNativeArrows();
        }

        private void Select(ExtraSortKind kind)
        {
            // 名称排序的方向表示品阶方向：首次降序，再次升序；名称次键始终升序。
            Ascending = _kind == kind && !Ascending;
            _kind = kind;
            foreach (var nativeToggle in _nativeToggles) nativeToggle.SetIsOnWithoutNotify(false);
            RefreshNativeArrows();
        }

        private void RefreshNativeArrows()
        {
            if (_kind != ExtraSortKind.None)
            {
                // 原生排序逻辑仍可能保留上一次的箭头文字；扩展排序生效时必须显式隐藏。
                foreach (var nativeToggle in _nativeToggles)
                {
                    var texts = nativeToggle.GetComponentsInChildren<TextPro>(true);
                    foreach (var arrow in texts.Skip(1)) arrow.gameObject.SetActive(false);
                }
            }
            SetNativeArrow(_nameToggle, _kind == ExtraSortKind.Name);
            foreach (var pair in _attributeToggles) SetNativeArrow(pair.Value, _kind == pair.Key);
        }

        private void SetNativeArrow(Toggle toggle, bool active)
        {
            if (toggle == null) return;
            toggle.SetIsOnWithoutNotify(active);
            if (!_sortArrowTexts.TryGetValue(toggle, out var arrow) || arrow == null) return;
            arrow.text = _nativeArrowText;
            arrow.gameObject.SetActive(active);
            var rect = arrow.rectTransform;
            rect.localScale = _nativeArrowScale;
            rect.localRotation = _nativeDescendingArrowRotation *
                                 Quaternion.Euler(0f, 0f, active && Ascending ? 180f : 0f);
        }

        private void ConfigureToggle(Toggle toggle, out TextPro label)
        {
            toggle.onValueChanged.RemoveAllListeners();
            toggle.group = null;
            toggle.SetIsOnWithoutNotify(false);
            foreach (var localization in toggle.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var labels = toggle.GetComponentsInChildren<TextPro>(true);
            label = labels.FirstOrDefault();
            var arrow = labels.Skip(1).FirstOrDefault();
            if (arrow != null)
            {
                arrow.text = _nativeArrowText;
                arrow.gameObject.SetActive(false);
                _sortArrowTexts[toggle] = arrow;
            }
            for (var i = 2; i < labels.Length; i++)
            {
                labels[i].text = string.Empty;
            }
        }

        private int GetEquipmentAttribute(TbPackSto item, ArtAttrEnum attribute)
        {
            try
            {
                var itemCfg = Singleton<TbItemImpl>.Instance.GetItemCfg(item.itemId);
                var method = AccessTools.Method(typeof(BagPanel), "GetItemAdValue");
                var values = method?.Invoke(_panel, new object[] { itemCfg, null }) as Dictionary<ArtAttrEnum, int>;
                return values != null && values.TryGetValue(attribute, out var value) ? value : 0;
            }
            catch (Exception)
            {
                return 0;
            }
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
            var nameSort = __instance.gameObject.GetComponent<BagNameSortUi>() ??
                           __instance.gameObject.AddComponent<BagNameSortUi>();
            nameSort.Initialize(__instance);
        }
    }

    [HarmonyPatch(typeof(BagPanel), nameof(BagPanel.ChangeType))]
    internal static class BagPanelDerivedFilterPatch
    {
        private static void Postfix(BagPanel __instance, int parentId, bool isbuild)
        {
            var filter = BagEnhancementState.GetFilter(parentId);
            var traverse = Traverse.Create(__instance);
            // GenerateItem 已把游戏原生的背包状态过滤、整理顺序和序号投影写入该列表。
            // 衍生筛选只能继续收窄这个展示结果，不能回到未排序的 packList 重建列表。
            var items = isbuild
                ? traverse.Field<List<TbPackSto>>("packList").Value?
                    .Where(item => item.flag == 0)
                    .OrderByDescending(item => item.seq)
                    .ToList()
                : traverse.Field<List<TbPackSto>>("showPackStoList").Value;
            if (items == null) return;
            var filtered = filter == DerivedBagFilter.All
                ? items.ToList()
                : items.Where(item => DerivedBagFiltering.Matches(item, filter)).ToList();
            var nameSort = __instance.gameObject.GetComponent<BagNameSortUi>();
            nameSort?.UpdateContext(parentId);
            if (nameSort?.Active == true) filtered = nameSort.Sort(filtered);
            if (filter == DerivedBagFilter.All && nameSort?.Active != true) return;
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
