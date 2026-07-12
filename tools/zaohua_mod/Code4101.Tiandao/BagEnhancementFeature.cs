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
        private readonly List<Image> _renameIcons = new List<Image>();
        private int _lastParentType = -1;
        private float _nextRenameIconLookup;

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
            if (_renameIcons.Any(icon => icon != null && icon.sprite == null) &&
                Time.unscaledTime >= _nextRenameIconLookup)
            {
                _nextRenameIconLookup = Time.unscaledTime + 1f;
                RefreshNativeRenameIcons();
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
                typeof(RectTransform), typeof(Image), typeof(VerticalLayoutGroup));
            _filterPopup.layer = gameObject.layer;
            _filterPopup.transform.SetParent(controlParent, false);
            var popupRect = (RectTransform)_filterPopup.transform;
            popupRect.anchorMin = triggerRect.anchorMin;
            popupRect.anchorMax = triggerRect.anchorMax;
            popupRect.pivot = new Vector2(0.5f, 1f);
            popupRect.localPosition = triggerRect.localPosition + new Vector3(0f, -45f, 0f);
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
            _loadoutPopup.transform.SetParent(controlParent, false);
            var popupRect = (RectTransform)_loadoutPopup.transform;
            popupRect.anchorMin = triggerRect.anchorMin;
            popupRect.anchorMax = triggerRect.anchorMax;
            popupRect.pivot = new Vector2(0.5f, 1f);
            popupRect.localPosition = triggerRect.localPosition + new Vector3(0f, -45f, 0f);
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
            _loadoutTrigger.gameObject.SetActive(true);
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
            ClearLoadoutPopup();
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
                AddInlineRenameButton(button, entity, active);
                _loadoutButtons.Add(button);
            }
            var create = CreateButton(_loadoutPopup.transform, "CreateLoadout", "＋ 新建方案",
                new Vector2(204f, 44f));
            create.onClick.AddListener(() =>
            {
                var entity = EquipmentLoadoutRepository.CreateEmptyLoadout();
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
                var message = CreateButton(_loadoutPopup.transform, "LoadoutMessage", _loadoutMessage,
                    new Vector2(204f, 44f));
                message.interactable = false;
                message.GetComponent<Image>().color = new Color(0.38f, 0.12f, 0.08f, 0.88f);
                _loadoutButtons.Add(message);
            }
            ((RectTransform)_loadoutPopup.transform).sizeDelta = new Vector2(220f,
                16f + _loadoutButtons.Count * 44f + Math.Max(0, _loadoutButtons.Count - 1) * 6f);
        }

        private void AddInlineRenameButton(Button row, EquipmentLoadoutEntity entity, bool active)
        {
            var label = row.transform.Find("Text")?.GetComponent<TextPro>();
            if (label != null) ((RectTransform)label.transform).offsetMax = new Vector2(-42f, 0f);
            var editObject = new GameObject("Rename", typeof(RectTransform), typeof(Image), typeof(Button));
            editObject.layer = gameObject.layer;
            editObject.transform.SetParent(row.transform, false);
            editObject.GetComponent<Image>().color = Color.clear;
            var edit = editObject.GetComponent<Button>();
            var rect = (RectTransform)edit.transform;
            rect.anchorMin = new Vector2(1f, 0f);
            rect.anchorMax = new Vector2(1f, 1f);
            rect.pivot = new Vector2(1f, 0.5f);
            rect.anchoredPosition = Vector2.zero;
            rect.sizeDelta = new Vector2(42f, 0f);
            var iconObject = new GameObject("NativeRenameIcon", typeof(RectTransform), typeof(Image));
            iconObject.layer = gameObject.layer;
            iconObject.transform.SetParent(edit.transform, false);
            var icon = iconObject.GetComponent<Image>();
            icon.raycastTarget = false;
            icon.preserveAspect = true;
            var iconRect = (RectTransform)icon.transform;
            iconRect.anchorMin = new Vector2(0.5f, 0.5f);
            iconRect.anchorMax = new Vector2(0.5f, 0.5f);
            iconRect.pivot = new Vector2(0.5f, 0.5f);
            iconRect.anchoredPosition = Vector2.zero;
            iconRect.sizeDelta = new Vector2(22f, 22f);
            _renameIcons.Add(icon);
            ApplyNativeRenameIcon(icon);
            edit.onClick.AddListener(() => BeginInlineRename(row, edit, entity));
        }

        private void RefreshNativeRenameIcons()
        {
            foreach (var icon in _renameIcons.Where(icon => icon != null && icon.sprite == null))
                ApplyNativeRenameIcon(icon);
        }

        private static void ApplyNativeRenameIcon(Image target)
        {
            var source = Resources.FindObjectsOfTypeAll<CombinationCellController>()
                .Select(cell => Traverse.Create(cell).Field<Button>("btnChangeName").Value)
                .Where(button => button != null)
                .Select(button => button.targetGraphic as Image ?? button.GetComponentInChildren<Image>(true))
                .FirstOrDefault(image => image != null && image.sprite != null);
            if (source == null) return;
            target.sprite = source.sprite;
            target.type = source.type;
            target.material = source.material;
            target.color = source.color;
            target.preserveAspect = true;
            var sourceSize = ((RectTransform)source.transform).rect.size * 0.5f;
            if (sourceSize.x > 0f && sourceSize.y > 0f)
                ((RectTransform)target.transform).sizeDelta = sourceSize;
        }

        private void BeginInlineRename(Button row, Button edit, EquipmentLoadoutEntity entity)
        {
            row.interactable = false;
            edit.gameObject.SetActive(false);
            var rowLabel = row.transform.Find("Text")?.GetComponent<TextPro>();
            if (rowLabel != null) rowLabel.gameObject.SetActive(false);

            var inputObject = new GameObject("LoadoutNameInput", typeof(RectTransform), typeof(Image),
                typeof(TMPro.TMP_InputField));
            inputObject.layer = gameObject.layer;
            inputObject.transform.SetParent(row.transform, false);
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

            var finished = false;
            void FinishRename(string value)
            {
                if (finished) return;
                finished = true;
                EquipmentLoadoutRepository.Rename(entity, value);
                RefreshLoadoutTrigger();
                RebuildLoadoutPopup();
            }

            input.onSubmit.AddListener(FinishRename);
            input.onEndEdit.AddListener(FinishRename);
            StartCoroutine(FocusRenameInput(input));
        }

        private static IEnumerator FocusRenameInput(TMPro.TMP_InputField input)
        {
            // 等待触发编辑的笔按钮完成本帧点击，避免 EventSystem 把焦点重新留给旧按钮。
            yield return null;
            if (input == null) yield break;
            input.Select();
            input.ActivateInputField();
            input.MoveTextEnd(false);
            input.ForceLabelUpdate();
        }

        private void ClearLoadoutPopup()
        {
            foreach (Transform child in _loadoutPopup.transform) Destroy(child.gameObject);
            _loadoutButtons.Clear();
            _renameIcons.Clear();
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
        };

        private BagPanel _panel;
        private Toggle _nameToggle;
        private TextPro _nameLabel;
        private Toggle _attributeToggle;
        private TextPro _attributeLabel;
        private GameObject _attributePopup;
        private GameObject _sortPopup;
        private Toggle[] _nativeToggles;
        private Vector2 _rowStep;
        private Vector2 _nativePopupPosition;
        private float _nativePopupHeight;
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
            _sortPopup = typeToggle.transform.parent.gameObject;
            _nameToggle = Instantiate(typeToggle, typeToggle.transform.parent);
            _nameToggle.gameObject.name = "Code4101SortByName";
            _nameToggle.onValueChanged.RemoveAllListeners();
            _nameToggle.group = null;
            _nameToggle.SetIsOnWithoutNotify(false);
            foreach (var localization in _nameToggle.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var labels = _nameToggle.GetComponentsInChildren<TextPro>(true);
            _nameLabel = labels.FirstOrDefault();
            for (var i = 1; i < labels.Length; i++) labels[i].text = string.Empty;

            var typeRect = (RectTransform)typeToggle.transform;
            var levelRect = (RectTransform)levelToggle.transform;
            var nameRect = (RectTransform)_nameToggle.transform;
            _rowStep = typeRect.anchoredPosition - levelRect.anchoredPosition;
            nameRect.anchoredPosition = typeRect.anchoredPosition + _rowStep;
            nameRect.SetAsLastSibling();
            var popupRect = (RectTransform)_sortPopup.transform;
            _nativePopupPosition = popupRect.anchoredPosition;
            _nativePopupHeight = popupRect.sizeDelta.y;

            _attributeToggle = Instantiate(typeToggle, typeToggle.transform.parent);
            _attributeToggle.gameObject.name = "Code4101SortByEquipmentAttribute";
            ConfigureToggle(_attributeToggle, out _attributeLabel);
            var attributeRect = (RectTransform)_attributeToggle.transform;
            attributeRect.anchoredPosition = nameRect.anchoredPosition + _rowStep;
            attributeRect.SetAsLastSibling();
            _attributeToggle.gameObject.SetActive(false);
            CreateAttributePopup(typeToggle, popupRect, attributeRect);

            _nativeToggles = new[]
                     {
                         fields.Field<Toggle>("togSortTime").Value,
                         fields.Field<Toggle>("togSortValue").Value,
                         fields.Field<Toggle>("togSortLevel").Value,
                         fields.Field<Toggle>("togSortType").Value,
                     };
            foreach (var nativeToggle in _nativeToggles)
            {
                nativeToggle.onValueChanged.AddListener(isOn =>
                {
                    if (isOn) _kind = ExtraSortKind.None;
                });
            }

            _nameToggle.onValueChanged.AddListener(isOn =>
            {
                if (!isOn) return;
                Select(ExtraSortKind.Name);
                RefreshLabel();
                _nameToggle.SetIsOnWithoutNotify(false);
                _sortPopup.SetActive(false);
                _panel.RefreshBag(false);
            });
            _attributeToggle.onValueChanged.AddListener(isOn =>
            {
                if (!isOn) return;
                _attributeToggle.SetIsOnWithoutNotify(false);
                ShowAttributeMenu();
            });
            RefreshLabel();
        }

        private void Update()
        {
            if (_attributePopup != null && _attributePopup.activeSelf && !_sortPopup.activeSelf)
                HideAttributeMenu();
        }

        internal void UpdateContext(int parentId)
        {
            if (!_initialized) return;
            _equipmentContext = parentId == 1;
            _attributeToggle.gameObject.SetActive(_equipmentContext);
            if (!_equipmentContext)
            {
                _attributePopup.SetActive(false);
                if (_kind != ExtraSortKind.None && _kind != ExtraSortKind.Name) _kind = ExtraSortKind.None;
            }
            ResizePopup(_equipmentContext ? 2 : 1);
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
            return items.OrderBy(item => item, Comparer<TbPackSto>.Create((left, right) =>
            {
                var compared = string.Compare(left?.name, right?.name, StringComparison.CurrentCulture);
                if (compared == 0) compared = (left?.id ?? 0).CompareTo(right?.id ?? 0);
                return compared * direction;
            })).ToList();
        }

        private void RefreshLabel()
        {
            if (_nameLabel != null) _nameLabel.text = "名称排序" + (_kind == ExtraSortKind.Name ? (Ascending ? "▲" : "▼") : string.Empty);
            if (_attributeLabel != null)
            {
                var current = EquipmentSorts.FirstOrDefault(entry => entry.Kind == _kind);
                _attributeLabel.text = _kind == ExtraSortKind.None || _kind == ExtraSortKind.Name
                    ? "属性排序"
                    : current.Label + "排序" + (Ascending ? "▲" : "▼");
            }
        }

        private void Select(ExtraSortKind kind)
        {
            Ascending = _kind == kind ? !Ascending : kind == ExtraSortKind.Name;
            _kind = kind;
            foreach (var nativeToggle in _nativeToggles) nativeToggle.SetIsOnWithoutNotify(false);
        }

        private void ConfigureToggle(Toggle toggle, out TextPro label)
        {
            toggle.onValueChanged.RemoveAllListeners();
            toggle.group = null;
            toggle.SetIsOnWithoutNotify(false);
            foreach (var localization in toggle.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var labels = toggle.GetComponentsInChildren<TextPro>(true);
            label = labels.FirstOrDefault();
            for (var i = 1; i < labels.Length; i++) labels[i].text = string.Empty;
        }

        private void CreateAttributePopup(Toggle template, RectTransform sortRect, RectTransform attributeRect)
        {
            _attributePopup = new GameObject("Code4101EquipmentAttributeSortPopup", typeof(RectTransform),
                typeof(Image), typeof(VerticalLayoutGroup));
            _attributePopup.layer = _sortPopup.layer;
            _attributePopup.transform.SetParent(_sortPopup.transform, false);
            var image = _attributePopup.GetComponent<Image>();
            var sourceImage = _sortPopup.GetComponent<Image>();
            if (sourceImage != null)
            {
                image.sprite = sourceImage.sprite;
                image.type = sourceImage.type;
                image.color = sourceImage.color;
            }
            else image.color = new Color(0.08f, 0.08f, 0.08f, 0.94f);
            var rect = (RectTransform)_attributePopup.transform;
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(1f, 1f);
            rect.pivot = new Vector2(0.5f, 1f);
            rect.anchoredPosition = Vector2.zero;
            rect.sizeDelta = new Vector2(0f, Mathf.Abs(_rowStep.y) * (EquipmentSorts.Length + 1));
            var layout = _attributePopup.GetComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(0, 0, 0, 0);
            layout.spacing = 0f;
            layout.childAlignment = TextAnchor.UpperCenter;
            layout.childControlWidth = true;
            layout.childControlHeight = true;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = false;

            var back = Instantiate(template, rect);
            back.gameObject.name = "Code4101BackToMainSorts";
            ConfigureToggle(back, out var backLabel);
            backLabel.text = "‹ 返回";
            var backLayout = back.GetComponent<LayoutElement>() ?? back.gameObject.AddComponent<LayoutElement>();
            backLayout.preferredHeight = Mathf.Abs(_rowStep.y);
            back.onValueChanged.AddListener(isOn =>
            {
                if (!isOn) return;
                back.SetIsOnWithoutNotify(false);
                HideAttributeMenu();
            });

            foreach (var definition in EquipmentSorts)
            {
                var toggle = Instantiate(template, rect);
                toggle.gameObject.name = "Code4101SortBy" + definition.Kind;
                ConfigureToggle(toggle, out var label);
                label.text = definition.Label + "排序";
                var rowLayout = toggle.GetComponent<LayoutElement>() ?? toggle.gameObject.AddComponent<LayoutElement>();
                rowLayout.preferredHeight = Mathf.Abs(_rowStep.y);
                toggle.onValueChanged.AddListener(isOn =>
                {
                    if (!isOn) return;
                    Select(definition.Kind);
                    toggle.SetIsOnWithoutNotify(false);
                    HideAttributeMenu();
                    _sortPopup.SetActive(false);
                    RefreshLabel();
                    _panel.RefreshBag(false);
                });
            }
            _attributePopup.SetActive(false);
        }

        private void ShowAttributeMenu()
        {
            _attributePopup.SetActive(true);
            _attributePopup.transform.SetAsLastSibling();
            // 二级清单替换一级清单，而不是接在其后面叠加显示。
            ResizePopup(EquipmentSorts.Length + 1 - 4);
        }

        private void HideAttributeMenu()
        {
            _attributePopup.SetActive(false);
            ResizePopup(_equipmentContext ? 2 : 1);
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
