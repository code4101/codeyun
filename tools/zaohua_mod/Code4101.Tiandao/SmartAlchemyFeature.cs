using HarmonyLib;
using System.Globalization;
using System.Collections.Generic;
using System.Collections;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.UI;

namespace Code4101.Zaohua.Tiandao
{
    internal static class SmartAlchemyFeature
    {
        internal static bool Enabled { get; private set; } = true;
        internal static bool IsEnabled => TiandaoState.GetAlchemyAssistantEnabled();

        internal static void ApplyConfiguredState()
        {
            SetEnabled(TiandaoState.GetAlchemyAssistantEnabled());
        }

        internal static void SetEnabled(bool enabled)
        {
            Enabled = enabled;
            foreach (var ui in Resources.FindObjectsOfTypeAll<SmartAlchemyUi>())
            {
                ui.SetFeatureEnabled(enabled);
            }
            if (!enabled) return;
            foreach (var cell in Resources.FindObjectsOfTypeAll<CraftingDrugCell>())
            {
                if (!cell.gameObject.scene.IsValid()) continue;
                var ui = cell.gameObject.GetComponent<SmartAlchemyUi>() ?? cell.gameObject.AddComponent<SmartAlchemyUi>();
                ui.Initialize(cell);
                ui.SetFeatureEnabled(true);
            }
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
        private readonly Dictionary<string, List<AlchemySolution>> _solutionCache =
            new Dictionary<string, List<AlchemySolution>>();
        private readonly Dictionary<string, int> _solutionCacheStages = new Dictionary<string, int>();
        private TbDrugRecipeCfg _solvedRecipe;
        private int _visibleSolutionCount = 5;
        private CancellationTokenSource _solveCancellation;
        private Task<AlchemySolveResponse> _solveTask;
        private string _activeSolveKey;
        private int _solveGeneration;
        private bool _onlyAvailable;
        private IReadOnlyDictionary<int, long> _inventory = new Dictionary<int, long>();

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
            var officialButtons = buttons.ToList();
            _spectrumButton = CreateTabButton(sourceButton, buttons, "Code4101AlchemySpectrumButton", "丹", "谱");
            _spectrumButton.onClick.AddListener(ShowSpectrum);

            sourceButton = buttons.Last();
            _smartButton = Instantiate(sourceButton, sourceButton.transform.parent);
            _smartButton.name = "Code4101SmartAlchemyButton";
            _smartButton.onClick.RemoveAllListeners();
            _smartButton.transform.SetAsLastSibling();

            PositionAfterLastButton(_smartButton.transform as RectTransform, buttons);
            SetButtonText(_smartButton);

            buttons.Add(_smartButton);
            _smartButton.onClick.AddListener(ShowSmart);

            CreateSpectrumPanel();
            CreateSmartPanel();
            foreach (var officialButton in officialButtons)
            {
                officialButton.onClick.AddListener(HideSmart);
            }
            RefreshGameState();
        }

        internal void SetFeatureEnabled(bool enabled)
        {
            if (_spectrumButton != null) _spectrumButton.gameObject.SetActive(enabled);
            if (_smartButton != null) _smartButton.gameObject.SetActive(enabled);
            if (!enabled)
            {
                CancelActiveSolve();
                HideSmart();
            }
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
            _spectrumPanel.name = "Code4101AlchemySpectrumPanel";
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
            card.imgArrow.gameObject.SetActive(false);
            card._tog.onValueChanged.RemoveAllListeners();
            card._tog.onValueChanged.AddListener(isOn =>
            {
                if (_changingSpectrumRecipeSelection) return;
                if (isOn)
                {
                    _changingSpectrumRecipeSelection = true;
                    foreach (var otherCard in _spectrumRecipeCards)
                    {
                        if (otherCard == null || otherCard == card || !otherCard._tog.isOn) continue;
                        otherCard._tog.SetIsOnWithoutNotify(false);
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
            _smartPanel.name = "Code4101SmartAlchemyPanel";
            var emptyTipName = _cell.view.txtNoCraftingLogTip.gameObject.name;
            foreach (var tip in _smartPanel.GetComponentsInChildren<TextPro>(true)
                         .Where(text => text.gameObject.name == emptyTipName))
            {
                tip.gameObject.SetActive(false);
            }
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
            layout.childControlWidth = true;
            layout.childControlHeight = true;
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
            StartSolveOrRenderCache();
        }

        private void StartSolveOrRenderCache()
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
            var globalOutcome = ReadAlchemyGlobalOutcome(Furnace);
            var cacheKey = BuildSolutionCacheKey(globalOutcome.count, globalOutcome.quality);
            if (!_solutionCache.ContainsKey(cacheKey) &&
                AlchemySolutionCacheRepository.TryLoad(
                    cacheKey, out var persistedSolutions, out var persistedCompletedStage))
            {
                _solutionCache[cacheKey] = persistedSolutions;
                _solutionCacheStages[cacheKey] = persistedCompletedStage;
            }
            if (_solutionCache.TryGetValue(cacheKey, out _solutions) &&
                _solutionCacheStages.TryGetValue(cacheKey, out var completedStage) &&
                (_solutions.Any(solution => solution.SearchStage <= completedStage && solution.IsAvailable(_inventory)) ||
                 completedStage >= 3))
            {
                Debug.Log($"[Code4101 Tiandao] cache hit recipe={_solvedRecipe.id}, solutions={_solutions.Count}");
                RenderSmartResults();
                return;
            }

            if (_solveTask != null && !_solveTask.IsCompleted && _activeSolveKey == cacheKey)
            {
                SetMessage("智能炼丹\n\n正在后台求解……\n可以继续查看丹方、切换页面或进行游戏");
                return;
            }

            _solveCancellation?.Cancel();
            _solveCancellation?.Dispose();
            _solveCancellation = new CancellationTokenSource();
            var token = _solveCancellation.Token;
            var generation = ++_solveGeneration;
            _activeSolveKey = cacheKey;
            var request = new AlchemySolveRequest
            {
                CacheKey = cacheKey,
                Generation = generation,
                Recipe = _solvedRecipe,
                Furnace = Furnace,
                GlobalCountBonus = globalOutcome.count,
                GlobalQualityBonus = globalOutcome.quality,
                Herbs = Herbs.ToList(),
                Inventory = new Dictionary<int, long>(_inventory),
                Limit = 50,
            };
            SetMessage("智能炼丹\n\n正在后台求解……\n可以继续查看丹方、切换页面或进行游戏");
            Debug.Log($"[Code4101 Tiandao] background solve started recipe={request.Recipe.id}, generation={generation}");
            var progress = new AlchemySolveProgress();
            _solveTask = AlchemySolveWorker.RunAsync(request, progress, token);
            StartCoroutine(CompleteSolveOnMainThread(_solveTask, progress, request));
        }

        private IEnumerator CompleteSolveOnMainThread(
            Task<AlchemySolveResponse> task,
            AlchemySolveProgress progress,
            AlchemySolveRequest activeRequest)
        {
            var nextProgressRefresh = Time.realtimeSinceStartup + 5f;
            var publishedRevision = 0;
            while (!task.IsCompleted)
            {
                if (Time.realtimeSinceStartup >= nextProgressRefresh)
                {
                    nextProgressRefresh = Time.realtimeSinceStartup + 5f;
                    var snapshot = progress.Snapshot(50, out var revision);
                    if (revision > publishedRevision && snapshot.Count > 0 &&
                        activeRequest.Generation == _solveGeneration &&
                        _smartPanel.activeSelf && _activeSolveKey == activeRequest.CacheKey)
                    {
                        publishedRevision = revision;
                        _solutions = snapshot;
                        RenderSmartResults();
                    }
                }
                yield return null;
            }
            if (task.IsCanceled) yield break;
            var result = task.Result;
            var request = result.Request;
            if (request.Generation != _solveGeneration) yield break;
            if (result.Error != null)
            {
                Debug.LogError($"[Code4101 Tiandao] background solve failed: {result.Error}");
                if (_smartPanel.activeSelf) SetMessage("智能炼丹\n\n后台求解失败，请重新选择丹方后再试");
                yield break;
            }
            if (_solutionCache.Count >= 32)
            {
                _solutionCache.Clear();
                _solutionCacheStages.Clear();
            }
            _solutionCache[request.CacheKey] = result.Solutions;
            _solutionCacheStages[request.CacheKey] = result.CompletedStage;
            AlchemySolutionCacheRepository.Save(request.CacheKey, result.Solutions, result.CompletedStage);
            Debug.Log($"[Code4101 Tiandao] background solved recipe={request.Recipe.id}, " +
                      $"solutions={result.Solutions.Count}, elapsed={result.ElapsedMilliseconds}ms");
            if (!_smartPanel.activeSelf || _activeSolveKey != request.CacheKey) yield break;
            _solutions = result.Solutions;
            _solvedRecipe = request.Recipe;
            RenderSmartResults();
        }

        private static CraftingDrugTmp ReadAlchemyGlobalOutcome(TbPackSto furnace)
        {
            var outcome = new CraftingDrugTmp();

            // 与 CraftingDrugCell.RefreshDrugInfo 保持同序：先执行全部已激活的炼制天赋，
            // 再执行当前丹炉效果。这里只采集摆放图形规则之前的全局结果快照。
            foreach (var sideId in Singleton<TbTreeImpl>.Instance.GetSideTalentSto(SideTypeEnum.LianDan))
            {
                var sideCfg = Singleton<TbDataImpl>.Instance.sideCfgList.Find(item => item.id == sideId);
                if (sideCfg == null) continue;
                var talentEffects = GenericMethods.GetEffectListByStr(sideCfg.craftingDrugEff);
                MonoSingleton<BsPlayEffectImpl>.Instance.AllDOEvent(talentEffects, outcome);
            }

            if (furnace == null) return outcome;
            var furnaceCfg = Singleton<TbDataImpl>.Instance.GetCraftingItemCfg(furnace.itemId.sedId);
            if (furnaceCfg == null || furnaceCfg.creaftingEffect == null) return outcome;
            var effects = Singleton<PlayEditor.PlayEditorManager>.Instance.GetAllActiveDoBaseEffect(
                furnaceCfg.creaftingEffect, true, outcome);
            MonoSingleton<BsPlayEffectImpl>.Instance.AllDOEvent(effects, outcome);
            return outcome;
        }

        private string BuildSolutionCacheKey(int globalCountBonus, int globalQualityBonus)
        {
            var data = Singleton<TbDataImpl>.Instance;
            var furnaceCfg = data.GetCraftingItemCfg(Furnace.itemId.sedId);
            var furnaceShape = furnaceCfg == null ? "" :
                $"{furnaceCfg.yangGridSize.x}x{furnaceCfg.yangGridSize.y}:{furnaceCfg.yinGridSize.x}x{furnaceCfg.yinGridSize.y}";
            return $"solver-v5|{_solvedRecipe.id}|{_solvedRecipe.attrLimiteStr}|{_solvedRecipe.stateIdStr}|" +
                   $"{Furnace.itemId.blendEnum}:{Furnace.itemId.sedId}:{furnaceShape}:" +
                   $"count+{globalCountBonus}:quality+{globalQualityBonus}|" +
                   string.Join(";", Herbs.OrderBy(stock => stock.ItemId.sedId)
                       .Select(stock =>
                       {
                           var crafting = data.GetCraftingItemCfg(stock.ItemId.sedId);
                           var attributes = crafting?.attrDic == null ? "" : string.Join(",",
                               crafting.attrDic.OrderBy(pair => pair.Key).Select(pair => $"{pair.Key}={pair.Value}"));
                           return $"{stock.ItemId.sedId}:{stock.ItemCfg.gradeId}:{crafting?.drawId}:{attributes}";
                       }));
        }

        private void RenderSmartResults()
        {
            ClearSmartResults();
            if (_solutions.Count == 0)
            {
                SetMessage($"智能炼丹\n\n{_solvedRecipe.GetName}\n当前丹炉无法组成该丹方");
                return;
            }

            _message.gameObject.SetActive(false);
            CreateOnlyAvailableToggle();
            var firstAvailableStage = _solutions
                .Where(solution => solution.IsAvailable(_inventory))
                .Select(solution => solution.SearchStage)
                .DefaultIfEmpty(int.MaxValue)
                .Min();
            var stagedSolutions = firstAvailableStage == int.MaxValue
                ? _solutions
                : _solutions.Where(solution => solution.SearchStage <= firstAvailableStage).ToList();
            var visibleSolutions = _onlyAvailable
                ? stagedSolutions.Where(solution => solution.IsAvailable(_inventory)).ToList()
                : stagedSolutions;
            if (visibleSolutions.Count == 0)
            {
                SetMessage($"智能炼丹\n\n{_solvedRecipe.GetName}\n当前没有背包可用方案");
                LayoutRebuilder.ForceRebuildLayoutImmediate(_smartContent);
                return;
            }
            var count = Mathf.Min(_visibleSolutionCount, visibleSolutions.Count);
            for (var index = 0; index < count; index++)
            {
                var solution = visibleSolutions[index];
                var card = ABMgr.InstantiateObj(_cell.view.craftingLogInfoCellPrefab, _smartContent);
                card.gameObject.name = $"Code4101SmartSolution_{index + 1}";
                card.SetInfo(solution.ToTemplate(_solvedRecipe, index), false, false);
                AppendPlantingCost(card, solution, _solvedRecipe);
                NormalizeSmartSolutionCard(card);
                _smartResultObjects.Add(card.gameObject);
            }
            if (count < visibleSolutions.Count)
            {
                CreateLoadMoreButton(count, visibleSolutions.Count);
            }
            LayoutRebuilder.ForceRebuildLayoutImmediate(_smartContent);
        }

        private static void AppendPlantingCost(
            CraftingLogInfoCell card,
            AlchemySolution solution,
            TbDrugRecipeCfg recipe)
        {
            if (card?.txtAttrLimit == null || solution == null || recipe == null) return;
            var totalPillCount = recipe.count + solution.TotalCountBonus;
            if (totalPillCount <= 0) return;

            var daysPerPill = (double)solution.PlantingDays / totalPillCount;
            var cost = daysPerPill > 300d
                ? (daysPerPill / 360d).ToString("F2", CultureInfo.InvariantCulture) + "年"
                : daysPerPill.ToString("F2", CultureInfo.InvariantCulture) + "天";
            var prefix = string.IsNullOrWhiteSpace(card.txtAttrLimit.text) ? "" : "\n";
            card.txtAttrLimit.text += prefix + $"每丹种植成本： {cost}";
        }

        private void CreateOnlyAvailableToggle()
        {
            var toggleObject = new GameObject("Code4101OnlyAvailable", typeof(RectTransform), typeof(Image), typeof(Button), typeof(LayoutElement));
            toggleObject.layer = _smartPanel.layer;
            toggleObject.transform.SetParent(_smartContent, false);
            toggleObject.transform.SetAsFirstSibling();
            var image = toggleObject.GetComponent<Image>();
            image.color = new Color(0.18f, 0.16f, 0.13f, 0.72f);
            var layout = toggleObject.GetComponent<LayoutElement>();
            layout.preferredHeight = 64f;
            layout.minHeight = 64f;
            var labelObject = Instantiate(_cell.view.txtNoRecipeTip.gameObject, toggleObject.transform);
            labelObject.SetActive(true);
            foreach (var localization in labelObject.GetComponentsInChildren<TextProLocalization>(true)) localization.enabled = false;
            var rect = labelObject.transform as RectTransform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = new Vector2(18f, 0f);
            rect.offsetMax = new Vector2(-18f, 0f);
            var label = labelObject.GetComponent<TextPro>();
            label.alignment = TMPro.TextAlignmentOptions.MidlineLeft;
            label.fontSize = 25f;
            label.text = (_onlyAvailable ? "✓ " : "□ ") + "仅看可用";
            toggleObject.GetComponent<Button>().onClick.AddListener(() =>
            {
                _onlyAvailable = !_onlyAvailable;
                _visibleSolutionCount = 5;
                RenderSmartResults();
            });
            _smartResultObjects.Add(toggleObject);
        }

        private void CreateLoadMoreButton(int shownCount, int totalCount)
        {
            var buttonObject = new GameObject("Code4101LoadMore", typeof(RectTransform), typeof(Image), typeof(Button), typeof(LayoutElement));
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
            label.text = $"加载更多\n（已显示 {shownCount}/{totalCount}）";
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
                if (resultObject == null) continue;
                resultObject.SetActive(false);
                Destroy(resultObject);
            }
            _smartResultObjects.Clear();
        }

        internal void OnRecipeChanged()
        {
            CancelActiveSolve();
            ClearSmartResults();
            _solutions.Clear();
            _solvedRecipe = null;
            _visibleSolutionCount = 5;
            if (_message != null)
            {
                SetMessage("智能炼丹\n\n丹方已更换，进入“智能”后将重新计算");
            }
            if (_smartContent != null)
            {
                _smartContent.anchoredPosition = Vector2.zero;
            }
        }

        private void CancelActiveSolve()
        {
            if (_solveTask != null && !_solveTask.IsCompleted)
            {
                Debug.Log($"[Code4101 Tiandao] background solve cancellation requested generation={_solveGeneration}");
            }
            _solveCancellation?.Cancel();
            _solveCancellation?.Dispose();
            _solveCancellation = null;
            _activeSolveKey = null;
            _solveGeneration++;
        }

        private void OnDestroy()
        {
            CancelActiveSolve();
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

        private void NormalizeSmartSolutionCard(CraftingLogInfoCell card)
        {
            const float minimumCardHeight = 176f;
            var cardRect = card.transform as RectTransform;
            if (cardRect == null) return;
            cardRect.sizeDelta = new Vector2(cardRect.sizeDelta.x, minimumCardHeight);
            Canvas.ForceUpdateCanvases();
            var attrRect = card.txtAttrLimit.transform as RectTransform;
            if (attrRect != null)
            {
                var requiredHeight = card.txtAttrLimit.preferredHeight;
                if (requiredHeight > attrRect.rect.height)
                {
                    attrRect.sizeDelta = new Vector2(attrRect.sizeDelta.x, requiredHeight + 8f);
                }
            }

            var textHeight = card.txtEffect.preferredHeight +
                             card.txtName.preferredHeight +
                             card.txtAttrLimit.preferredHeight + 44f;
            var height = Mathf.Max(minimumCardHeight, textHeight);
            cardRect.sizeDelta = new Vector2(cardRect.sizeDelta.x, height);
            var cardLayout = card.GetComponent<LayoutElement>() ?? card.gameObject.AddComponent<LayoutElement>();
            cardLayout.preferredWidth = -1f;
            cardLayout.minWidth = -1f;
            cardLayout.flexibleWidth = 1f;
            cardLayout.preferredHeight = height;
            cardLayout.minHeight = height;
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
                _inventory = new Dictionary<int, long>();
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
                _inventory = new Dictionary<int, long>();
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

            _inventory = Herbs.ToDictionary(stock => stock.ItemId.sedId, stock => stock.Count);
            Herbs = Singleton<TbDataImpl>.Instance.itemCfgList
                .Where(item => Singleton<TbItemImpl>.Instance.GetParentByTypeId(item.typeId) == 10)
                .Where(item => Singleton<TbDataImpl>.Instance.GetCraftingItemCfg(item.id) != null)
                .GroupBy(item => item.id)
                .Select(group =>
                {
                    var item = group.First();
                    var count = _inventory.TryGetValue(item.id, out var owned) ? owned : 0;
                    return new HerbStock(item.blendId, item, count);
                })
                .OrderBy(stock => stock.ItemCfg.gradeId)
                .ThenBy(stock => stock.ItemCfg.id)
                .ToList();

            var totalCount = _inventory.Values.Sum();
            if (_message != null)
            {
                SetMessage(
                    $"智能炼丹\n\n丹炉：{Furnace.name}\n" +
                    $"背包药材：{_inventory.Count} 种，共 {totalCount} 份\n" +
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
            if (!SmartAlchemyFeature.IsEnabled) return;
            var ui = __instance.gameObject.GetComponent<SmartAlchemyUi>() ??
                     __instance.gameObject.AddComponent<SmartAlchemyUi>();
            ui.Initialize(__instance);
        }
    }

    [HarmonyPatch(typeof(CraftingPanel), nameof(CraftingPanel.ShowMe))]
    internal static class CraftingPanelShowPatch
    {
        private static void Prefix()
        {
            if (!SmartAlchemyFeature.IsEnabled) return;
            // 此插件的主要工作区在“炼制丹药”，每次打开炼丹菜单都直接进入该页。
            CommonStatic.craftingPanelSubpanelIndex = 1;
        }
    }

    [HarmonyPatch(typeof(CraftingDrugCell), nameof(CraftingDrugCell.UpdateTog))]
    internal static class CraftingDrugCellUpdateTogPatch
    {
        private static void Prefix(CraftingDrugCell __instance)
        {
            if (!SmartAlchemyFeature.IsEnabled) return;
            __instance.gameObject.GetComponent<SmartAlchemyUi>()?.HideSmart();
        }
    }

    [HarmonyPatch(typeof(CraftingDrugCell), nameof(CraftingDrugCell.RefreshDrugFurnace))]
    internal static class CraftingDrugCellRefreshFurnacePatch
    {
        private static void Postfix(CraftingDrugCell __instance)
        {
            if (!SmartAlchemyFeature.IsEnabled) return;
            __instance.gameObject.GetComponent<SmartAlchemyUi>()?.RefreshGameState();
        }
    }

    [HarmonyPatch(typeof(CraftingDrugCell), nameof(CraftingDrugCell.UpdateLockRecipe))]
    internal static class CraftingDrugCellUpdateLockRecipePatch
    {
        private static void Postfix(CraftingDrugCell __instance)
        {
            if (!SmartAlchemyFeature.IsEnabled) return;
            __instance.gameObject.GetComponent<SmartAlchemyUi>()?.OnRecipeChanged();
        }
    }
}
