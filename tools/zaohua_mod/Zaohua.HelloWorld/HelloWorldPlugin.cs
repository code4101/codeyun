using BepInEx;
using BepInEx.Unity.Mono;
using HarmonyLib;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.UI;

namespace CodeYun.Zaohua.SmartAlchemy
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class SmartAlchemyPlugin : BaseUnityPlugin
    {
        public const string PluginGuid = "codeyun.zaohua.smartalchemy";
        public const string PluginName = "CodeYun Zaohua Smart Alchemy";
        public const string PluginVersion = "0.1.0";

        private Harmony _harmony;

        private void Awake()
        {
            _harmony = new Harmony(PluginGuid);
            _harmony.PatchAll();
            Logger.LogInfo("Smart Alchemy patches registered.");
        }

        private void OnDestroy()
        {
            _harmony?.UnpatchSelf();
        }
    }

    internal sealed class SmartAlchemyUi : MonoBehaviour
    {
        private CraftingDrugCell _cell;
        private Button _spectrumButton;
        private Button _smartButton;
        private GameObject _spectrumPanel;
        private RectTransform _spectrumContent;
        private readonly List<GameObject> _spectrumItems = new List<GameObject>();
        private readonly List<CraftingDrugRecipeCell> _spectrumRecipeCards = new List<CraftingDrugRecipeCell>();
        private bool _changingSpectrumRecipeSelection;
        private GameObject _smartPanel;
        private TextPro _message;
        private RectTransform _smartContent;
        private readonly List<GameObject> _smartResultObjects = new List<GameObject>();
        private List<AlchemySolution> _solutions = new List<AlchemySolution>();
        private TbDrugRecipeCfg _solvedRecipe;
        private int _visibleSolutionCount = 5;

        internal TbPackSto Furnace { get; private set; }
        internal IReadOnlyList<HerbStock> Herbs { get; private set; } = new List<HerbStock>();

        internal void Initialize(CraftingDrugCell cell)
        {
            if (_smartButton != null)
            {
                return;
            }

            _cell = cell;
            var buttons = cell.view.togGroup.toggleButtons;
            if (buttons.Count == 0)
            {
                return;
            }

            var sourceButton = buttons.Last();
            _spectrumButton = CreateTabButton(sourceButton, buttons, "CodeYunAlchemySpectrumButton", "丹", "谱");
            _spectrumButton.onClick.AddListener(ShowSpectrum);

            sourceButton = buttons.Last();
            _smartButton = Instantiate(sourceButton, sourceButton.transform.parent);
            _smartButton.name = "CodeYunSmartAlchemyButton";
            _smartButton.onClick.RemoveAllListeners();
            _smartButton.transform.SetAsLastSibling();

            PositionAfterLastButton(_smartButton.transform as RectTransform, buttons);
            SetButtonText(_smartButton);

            buttons.Add(_smartButton);
            _smartButton.onClick.AddListener(ShowSmart);

            CreateSpectrumPanel();
            CreateSmartPanel();
            RefreshGameState();
        }

        private static Button CreateTabButton(
            Button sourceButton,
            List<Button> buttons,
            string name,
            string firstCharacter,
            string secondCharacter)
        {
            var button = Instantiate(sourceButton, sourceButton.transform.parent);
            button.name = name;
            button.onClick.RemoveAllListeners();
            button.transform.SetAsLastSibling();
            PositionAfterLastButton(button.transform as RectTransform, buttons);
            SetButtonText(button, firstCharacter, secondCharacter);
            buttons.Add(button);
            return button;
        }

        private static void PositionAfterLastButton(RectTransform smartRect, System.Collections.Generic.List<Button> buttons)
        {
            if (smartRect == null || buttons.Count == 0)
            {
                return;
            }

            var lastRect = buttons[buttons.Count - 1].transform as RectTransform;
            if (lastRect == null)
            {
                return;
            }

            if (buttons.Count >= 2)
            {
                var previousRect = buttons[buttons.Count - 2].transform as RectTransform;
                if (previousRect != null)
                {
                    smartRect.anchoredPosition = lastRect.anchoredPosition +
                                                 (lastRect.anchoredPosition - previousRect.anchoredPosition);
                    return;
                }
            }

            smartRect.anchoredPosition = lastRect.anchoredPosition + new Vector2(0f, -lastRect.rect.height);
        }

        private static void SetButtonText(Button button)
        {
            SetButtonText(button, "智", "能");
        }

        private static void SetButtonText(Button button, string firstCharacter, string secondCharacter)
        {
            foreach (var localization in button.GetComponentsInChildren<TextProLocalization>(true))
            {
                localization.enabled = false;
            }

            var texts = button.GetComponentsInChildren<TextPro>(true);
            if (texts.Length >= 2)
            {
                texts[0].text = firstCharacter;
                texts[1].text = secondCharacter;
            }
            else if (texts.Length == 1)
            {
                texts[0].text = firstCharacter + secondCharacter;
            }
        }

        private void CreateSpectrumPanel()
        {
            var sourceScroll = _cell.view.recipeScroll;
            _spectrumPanel = Instantiate(sourceScroll.gameObject, sourceScroll.transform.parent);
            _spectrumPanel.name = "CodeYunAlchemySpectrumPanel";
            var emptyTipName = _cell.view.txtNoRecipeTip.gameObject.name;
            foreach (var tip in _spectrumPanel.GetComponentsInChildren<TextPro>(true)
                         .Where(text => text.gameObject.name == emptyTipName))
            {
                tip.gameObject.SetActive(false);
            }
            var scroll = _spectrumPanel.GetComponent<ScrollRect>();
            var content = scroll.content;
            _spectrumContent = content;

            foreach (Transform child in content)
            {
                child.gameObject.SetActive(false);
                Destroy(child.gameObject);
            }

            foreach (var layout in content.GetComponents<LayoutGroup>()) layout.enabled = false;
            var fitter = content.GetComponent<ContentSizeFitter>();
            if (fitter != null) fitter.enabled = false;
            content.anchorMin = new Vector2(0f, 1f);
            content.anchorMax = new Vector2(1f, 1f);
            content.pivot = new Vector2(0.5f, 1f);

            var data = Singleton<TbDataImpl>.Instance;
            var groupedRecipes = data.drugRecipeList
                .Where(recipe => data.GetItemCfg(recipe.itemId) != null)
                .GroupBy(recipe => data.GetGradeCfg(data.GetItemCfg(recipe.itemId).gradeId))
                .Where(group => group.Key != null)
                .OrderBy(group => group.Key.weight)
                .ThenBy(group => group.Key.id);

            foreach (var gradeGroup in groupedRecipes)
            {
                CreateGradeGroup(content, gradeGroup.Key, gradeGroup.OrderBy(recipe => recipe.id).ToList());
            }

            RelayoutSpectrum();

            _spectrumPanel.SetActive(false);
        }

        private void CreateGradeGroup(Transform content, TbGradeCfg grade, List<TbDrugRecipeCfg> recipes)
        {
            var header = ABMgr.InstantiateObj(_cell.view.recipeCellPrefab, content);
            PrepareSpectrumItem(header.gameObject, 56f);
            _spectrumItems.Add(header.gameObject);
            header.gameObject.name = $"Grade_{grade.id}";
            header.btnDelete.gameObject.SetActive(false);
            header.togFollow.gameObject.SetActive(false);
            header.togReady.gameObject.SetActive(false);
            header.txtName.text = $"{grade.GetName}  ·  {recipes.Count} 张丹方";
            header.txtName.fontSize = 30f;
            header.txtName.color = new Color(0.92f, 0.88f, 0.75f, 1f);
            if (header._tog.targetGraphic != null)
            {
                header._tog.targetGraphic.color = new Color(0.26f, 0.23f, 0.18f, 0.86f);
            }
            header._tog.transition = Selectable.Transition.ColorTint;
            var headerColors = header._tog.colors;
            headerColors.normalColor = Color.white;
            headerColors.highlightedColor = new Color(1f, 0.90f, 0.68f, 1f);
            headerColors.selectedColor = new Color(0.82f, 0.65f, 0.36f, 1f);
            headerColors.pressedColor = new Color(0.72f, 0.54f, 0.28f, 1f);
            headerColors.colorMultiplier = 1f;
            header._tog.colors = headerColors;
            header.imgLightHigh.gameObject.SetActive(false);
            header._tog.onValueChanged.RemoveAllListeners();

            var cards = new List<GameObject>();
            foreach (var recipe in recipes)
            {
                var card = CreateSpectrumRecipeCard(content, recipe);
                card.gameObject.SetActive(false);
                cards.Add(card.gameObject);
            }

            header._tog.isOn = false;
            header.imgArrow.transform.rotation = Quaternion.Euler(0f, 0f, 90f);
            header._tog.onValueChanged.AddListener(isOn =>
            {
                header.txtName.color = isOn
                    ? new Color(0.16f, 0.12f, 0.08f, 1f)
                    : new Color(0.92f, 0.88f, 0.75f, 1f);
                header.imgArrow.transform.rotation = Quaternion.Euler(0f, 0f, isOn ? -90f : 90f);
                foreach (var card in cards)
                {
                    card.SetActive(isOn);
                }
                RelayoutSpectrum();
            });
        }

        private CraftingDrugRecipeCell CreateSpectrumRecipeCard(Transform content, TbDrugRecipeCfg recipe)
        {
            var card = ABMgr.InstantiateObj(_cell.view.recipeCellPrefab, content);
            PrepareSpectrumItem(card.gameObject, 62f);
            _spectrumItems.Add(card.gameObject);
            var recipeSto = new TbDrugRecipeSto
            {
                id = -recipe.id,
                recipeId = recipe.id,
                isFollow = false,
                isNew = false,
            };
            card.SetInfo(recipeSto, false, false, false);
            _spectrumRecipeCards.Add(card);
            card.btnDelete.gameObject.SetActive(false);
            card.togFollow.gameObject.SetActive(false);
            card._tog.onValueChanged.RemoveAllListeners();
            card._tog.onValueChanged.AddListener(isOn =>
            {
                card.imgArrow.transform.rotation = Quaternion.Euler(0f, 0f, isOn ? -90f : 90f);
                if (_changingSpectrumRecipeSelection) return;
                if (isOn)
                {
                    _changingSpectrumRecipeSelection = true;
                    foreach (var otherCard in _spectrumRecipeCards)
                    {
                        if (otherCard == null || otherCard == card || !otherCard._tog.isOn) continue;
                        otherCard._tog.SetIsOnWithoutNotify(false);
                        otherCard.imgArrow.transform.rotation = Quaternion.Euler(0f, 0f, 90f);
                    }
                    _changingSpectrumRecipeSelection = false;
                }
                _cell.UpdateLockRecipe(recipeSto, isOn);
            });
            return card;
        }

        private static void PrepareSpectrumItem(GameObject item, float height)
        {
            var rect = item.transform as RectTransform;
            if (rect == null) return;
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(1f, 1f);
            rect.pivot = new Vector2(0.5f, 1f);
            rect.sizeDelta = new Vector2(-12f, height);
        }

        private void RelayoutSpectrum()
        {
            if (_spectrumContent == null) return;
            var y = 4f;
            foreach (var item in _spectrumItems)
            {
                if (item == null || !item.activeSelf) continue;
                var rect = item.transform as RectTransform;
                if (rect == null) continue;
                rect.anchoredPosition = new Vector2(0f, -y);
                y += rect.sizeDelta.y + 4f;
            }
            _spectrumContent.sizeDelta = new Vector2(_spectrumContent.sizeDelta.x, y);
        }

        private void ShowSpectrum()
        {
            _cell.view.togGroup.SetActiveButton(_spectrumButton);
            _cell.view.recipeScroll.gameObject.SetActive(false);
            _cell.view.itemScroll.gameObject.SetActive(false);
            _cell.view.craftingLogScroll.gameObject.SetActive(false);
            _smartPanel.SetActive(false);
            _spectrumPanel.SetActive(true);
        }

        private void CreateSmartPanel()
        {
            var sourceScroll = _cell.view.craftingLogScroll;
            _smartPanel = Instantiate(sourceScroll.gameObject, sourceScroll.transform.parent);
            _smartPanel.name = "CodeYunSmartAlchemyPanel";
            var scroll = _smartPanel.GetComponent<ScrollRect>();
            _smartContent = scroll.content;
            foreach (Transform child in _smartContent)
            {
                child.gameObject.SetActive(false);
                Destroy(child.gameObject);
            }
            var layout = _smartContent.GetComponent<VerticalLayoutGroup>() ??
                         _smartContent.gameObject.AddComponent<VerticalLayoutGroup>();
            layout.spacing = 12f;
            layout.childAlignment = TextAnchor.UpperCenter;
            layout.childControlWidth = false;
            layout.childControlHeight = false;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = false;
            var fitter = _smartContent.GetComponent<ContentSizeFitter>() ??
                         _smartContent.gameObject.AddComponent<ContentSizeFitter>();
            fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

            var messageObject = Instantiate(_cell.view.txtNoRecipeTip.gameObject, _smartContent);
            messageObject.name = "Message";
            messageObject.SetActive(true);
            foreach (var localization in messageObject.GetComponentsInChildren<TextProLocalization>(true))
            {
                localization.enabled = false;
            }

            var messageRect = messageObject.transform as RectTransform;
            var messageLayout = messageObject.GetComponent<LayoutElement>() ?? messageObject.AddComponent<LayoutElement>();
            messageLayout.preferredHeight = 260f;

            _message = messageObject.GetComponent<TextPro>();
            _message.text = "智能炼丹\n\n正在读取丹炉与背包药材……";

            _smartPanel.SetActive(false);
        }

        private void ShowSmart()
        {
            RefreshGameState();
            _cell.view.togGroup.SetActiveButton(_smartButton);
            _cell.view.recipeScroll.gameObject.SetActive(false);
            _cell.view.itemScroll.gameObject.SetActive(false);
            _cell.view.craftingLogScroll.gameObject.SetActive(false);
            _spectrumPanel.SetActive(false);
            _smartPanel.SetActive(true);
            SolveAndRender();
        }

        private void SolveAndRender()
        {
            ClearSmartResults();
            var recipeSto = _cell.lockRecipeSto;
            if (recipeSto == null)
            {
                SetMessage("智能炼丹\n\n请先从“丹方”或“丹谱”选择一个丹方");
                return;
            }
            if (Furnace == null)
            {
                SetMessage("智能炼丹\n\n请先装备丹炉\n丹方仍可查看，但求解需要丹炉");
                return;
            }

            _solvedRecipe = Singleton<TbDataImpl>.Instance.GetDrugRecipeCfg(recipeSto.recipeId);
            if (_solvedRecipe == null)
            {
                SetMessage("智能炼丹\n\n当前丹方数据读取失败");
                return;
            }

            _visibleSolutionCount = 5;
            SetMessage("智能炼丹\n\n正在使用当前丹炉与有限药材库存计算……");
            _solutions = FiniteInventoryAlchemySolver.Solve(_solvedRecipe, Furnace, Herbs, 50);
            RenderSmartResults();
        }

        private void RenderSmartResults()
        {
            ClearSmartResults();
            if (_solutions.Count == 0)
            {
                SetMessage($"智能炼丹\n\n{_solvedRecipe.GetName}\n当前丹炉尺寸与背包药材无法组成该丹方");
                return;
            }

            _message.gameObject.SetActive(false);
            var count = Mathf.Min(_visibleSolutionCount, _solutions.Count);
            for (var index = 0; index < count; index++)
            {
                var card = ABMgr.InstantiateObj(_cell.view.craftingLogInfoCellPrefab, _smartContent);
                NormalizeListItem(card.gameObject, _cell.view.craftingLogInfoCellPrefab.gameObject);
                card.gameObject.name = $"CodeYunSmartSolution_{index + 1}";
                card.SetInfo(_solutions[index].ToTemplate(_solvedRecipe, index), false, false);
                _smartResultObjects.Add(card.gameObject);
            }
            if (count < _solutions.Count)
            {
                CreateLoadMoreButton(count);
            }
            LayoutRebuilder.ForceRebuildLayoutImmediate(_smartContent);
        }

        private void CreateLoadMoreButton(int shownCount)
        {
            var buttonObject = new GameObject("CodeYunLoadMore", typeof(RectTransform), typeof(Image), typeof(Button), typeof(LayoutElement));
            buttonObject.layer = _smartPanel.layer;
            buttonObject.transform.SetParent(_smartContent, false);
            var image = buttonObject.GetComponent<Image>();
            image.color = new Color(0.25f, 0.20f, 0.15f, 0.22f);
            var layout = buttonObject.GetComponent<LayoutElement>();
            layout.preferredWidth = 360f;
            layout.minWidth = 360f;
            layout.preferredHeight = 86f;
            var buttonRect = buttonObject.transform as RectTransform;
            buttonRect.sizeDelta = new Vector2(360f, 86f);
            var labelObject = Instantiate(_cell.view.txtNoRecipeTip.gameObject, buttonObject.transform);
            labelObject.SetActive(true);
            foreach (var localization in labelObject.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var rect = labelObject.transform as RectTransform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
            var label = labelObject.GetComponent<TextPro>();
            label.fontSize = 28f;
            label.text = $"加载更多\n（已显示 {shownCount}/{_solutions.Count}）";
            buttonObject.GetComponent<Button>().onClick.AddListener(() =>
            {
                _visibleSolutionCount += 5;
                RenderSmartResults();
            });
            _smartResultObjects.Add(buttonObject);
        }

        private void ClearSmartResults()
        {
            foreach (var resultObject in _smartResultObjects)
            {
                if (resultObject != null) Destroy(resultObject);
            }
            _smartResultObjects.Clear();
        }

        private static void NormalizeListItem(GameObject item, GameObject sourcePrefab)
        {
            var itemRect = item.transform as RectTransform;
            var sourceRect = sourcePrefab.transform as RectTransform;
            if (itemRect == null || sourceRect == null) return;
            itemRect.anchorMin = new Vector2(0.5f, 1f);
            itemRect.anchorMax = new Vector2(0.5f, 1f);
            itemRect.pivot = new Vector2(0.5f, 1f);
            itemRect.sizeDelta = new Vector2(sourceRect.rect.width, sourceRect.rect.height);
            var layout = item.GetComponent<LayoutElement>() ?? item.AddComponent<LayoutElement>();
            layout.preferredWidth = sourceRect.rect.width;
            layout.preferredHeight = sourceRect.rect.height;
            layout.minWidth = sourceRect.rect.width;
            layout.minHeight = sourceRect.rect.height;
        }

        private void SetMessage(string text)
        {
            _message.gameObject.SetActive(true);
            _message.text = text;
            LayoutRebuilder.ForceRebuildLayoutImmediate(_smartContent);
        }

        internal void RefreshGameState()
        {
            var actor = BsSaveDataImpl.nowActor;
            if (actor == null)
            {
                Furnace = null;
                Herbs = new List<HerbStock>();
                if (_message != null)
                {
                    SetMessage("智能炼丹\n\n当前角色数据尚未就绪");
                }
                return;
            }

            Furnace = actor.packStoList.Find(s => s.flag == 202 && s.npcStoId == 10000);

            if (Furnace == null)
            {
                Herbs = new List<HerbStock>();
                if (_message != null)
                {
                    SetMessage("智能炼丹\n\n请先装备丹炉\n依赖丹炉的求解功能暂不可用");
                }
                return;
            }

            Herbs = Singleton<BsBagImpl>.Instance
                .GetPackListByNpcStoId(10000, -1)
                .Where(s => s.haveCount > 0)
                .Where(s => Singleton<TbItemImpl>.Instance.GetParentByTypeId(s.itemCfg.typeId) == 10)
                .GroupBy(s => s.itemId)
                .Select(group => new HerbStock(
                    group.Key,
                    group.First().itemCfg,
                    group.Sum(item => item.haveCount)))
                .OrderBy(stock => stock.ItemCfg.gradeId)
                .ThenBy(stock => stock.ItemCfg.id)
                .ToList();

            var totalCount = Herbs.Sum(stock => stock.Count);
            if (_message != null)
            {
                SetMessage(
                    $"智能炼丹\n\n丹炉：{Furnace.name}\n" +
                    $"背包药材：{Herbs.Count} 种，共 {totalCount} 份\n" +
                    "丹炉与药材数据已就绪");
            }
        }

        internal void HideSmart()
        {
            if (_smartPanel != null)
            {
                _smartPanel.SetActive(false);
            }
            if (_spectrumPanel != null)
            {
                _spectrumPanel.SetActive(false);
            }
        }

        internal sealed class HerbStock
        {
            internal HerbStock(BlendId itemId, TbItemCfg itemCfg, long count)
            {
                ItemId = itemId;
                ItemCfg = itemCfg;
                Count = count;
            }

            internal BlendId ItemId { get; }
            internal TbItemCfg ItemCfg { get; }
            internal long Count { get; }
        }
    }

    [HarmonyPatch(typeof(CraftingDrugCell), "Awake")]
    internal static class CraftingDrugCellAwakePatch
    {
        private static void Postfix(CraftingDrugCell __instance)
        {
            var ui = __instance.gameObject.GetComponent<SmartAlchemyUi>() ??
                     __instance.gameObject.AddComponent<SmartAlchemyUi>();
            ui.Initialize(__instance);
        }
    }

    [HarmonyPatch(typeof(CraftingDrugCell), nameof(CraftingDrugCell.UpdateTog))]
    internal static class CraftingDrugCellUpdateTogPatch
    {
        private static void Prefix(CraftingDrugCell __instance)
        {
            __instance.gameObject.GetComponent<SmartAlchemyUi>()?.HideSmart();
        }
    }

    [HarmonyPatch(typeof(CraftingDrugCell), nameof(CraftingDrugCell.RefreshDrugFurnace))]
    internal static class CraftingDrugCellRefreshFurnacePatch
    {
        private static void Postfix(CraftingDrugCell __instance)
        {
            __instance.gameObject.GetComponent<SmartAlchemyUi>()?.RefreshGameState();
        }
    }
}
