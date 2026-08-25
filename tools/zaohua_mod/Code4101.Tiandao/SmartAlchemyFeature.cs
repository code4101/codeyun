using HarmonyLib;
using System;
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
        private sealed class AlchemyPlanState
        {
            internal string CacheKey { get; set; }
            internal int RecipeId { get; set; }
            internal string InventorySignature { get; private set; } = string.Empty;
            internal List<AlchemySolution> StaticSolutions { get; private set; } = new List<AlchemySolution>();
            internal AlchemySolution BackpackSolution { get; private set; }
            internal bool StaticComplete { get; private set; }
            internal bool InventoryComplete { get; private set; }
            private IReadOnlyDictionary<int, long> CurrentInventory { get; set; } =
                new Dictionary<int, long>();

            internal IReadOnlyList<AlchemySolution> Solutions
            {
                get
                {
                    var solutions = StaticSolutions.ToList();
                    // 状态层硬约束：任一静态解可用时，背包解既不保留也不展示。
                    if (BackpackSolution != null && !HasAvailableStatic())
                        solutions.Add(BackpackSolution);
                    return FiniteInventoryAlchemySolver.RankAndSelectSolutions(solutions, 3);
                }
            }

            private bool HasAvailableStatic() =>
                StaticSolutions.Any(solution => solution.IsAvailable(CurrentInventory));

            internal void PrepareInventory(
                string inventorySignature,
                IReadOnlyDictionary<int, long> inventory)
            {
                var inventoryChanged = InventorySignature != inventorySignature;
                InventorySignature = inventorySignature;
                CurrentInventory = inventory ?? new Dictionary<int, long>();
                if (inventoryChanged) BackpackSolution = null;
                if (HasAvailableStatic())
                {
                    BackpackSolution = null;
                    InventoryComplete = StaticComplete;
                }
                else if (inventoryChanged)
                {
                    InventoryComplete = false;
                }
            }

            internal void Publish(
                IReadOnlyList<AlchemySolution> solutions,
                string inventorySignature,
                IReadOnlyDictionary<int, long> inventory)
            {
                var staticSolutions = solutions
                    .Where(solution => solution.SearchStage == 1 || solution.SearchStage == 2)
                    .OrderBy(solution => solution.SearchStage)
                    .ToList();
                if (staticSolutions.Count > 0) StaticSolutions = staticSolutions;
                if (InventorySignature != inventorySignature) return;
                CurrentInventory = inventory ?? new Dictionary<int, long>();
                BackpackSolution = HasAvailableStatic()
                    ? null
                    : solutions.FirstOrDefault(solution => solution.SearchStage == 3);
            }

            internal void Complete(
                IReadOnlyList<AlchemySolution> solutions,
                string inventorySignature,
                IReadOnlyDictionary<int, long> inventory)
            {
                Publish(solutions, inventorySignature, inventory);
                StaticComplete = true;
                if (InventorySignature == inventorySignature)
                {
                    // 有解和已证明无解都属于这个背包版本的完整结果，避免反复重算。
                    InventoryComplete = true;
                }
            }
        }

        private CraftingDrugCell _cell;
        private Button _spectrumButton;
        private Button _smartButton;
        private GameObject _spectrumPanel;
        private RectTransform _spectrumContent;
        private readonly List<GameObject> _spectrumItems = new List<GameObject>();
        private readonly List<CraftingDrugRecipeCell> _spectrumRecipeCards = new List<CraftingDrugRecipeCell>();
        private readonly Dictionary<int, CraftingDrugRecipeCell> _spectrumCardsByRecipe =
            new Dictionary<int, CraftingDrugRecipeCell>();
        private bool _changingSpectrumRecipeSelection;
        private GameObject _smartPanel;
        private TextPro _message;
        private RectTransform _smartContent;
        private readonly List<GameObject> _smartResultObjects = new List<GameObject>();
        private static readonly Dictionary<string, AlchemyPlanState> SessionPlanStates =
            new Dictionary<string, AlchemyPlanState>();
        private AlchemyPlanState _selectedPlanState;
        private readonly LinkedList<int> _prefetchQueue = new LinkedList<int>();
        private readonly HashSet<int> _queuedRecipeIds = new HashSet<int>();
        private TbDrugRecipeCfg _solvedRecipe;
        private CancellationTokenSource _solveCancellation;
        private Task<AlchemySolveResponse> _solveTask;
        private string _activeSolveKey;
        private int _activeRecipeId;
        private int _solveGeneration;
        private Coroutine _solveQueueCoroutine;
        private string _inventorySignature = string.Empty;
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
            RefreshStaticAvailabilityAndQueueFallbacks();
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
                if (isOn)
                {
                    // 卡片可能在后台结果发布后才展开；展开时必须主动从统一状态重放标记。
                    RefreshStaticAvailabilityAndQueueFallbacks();
                    foreach (var recipe in recipes) EnqueueRecipe(recipe.id, false);
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
            _spectrumCardsByRecipe[recipe.id] = card;
            card.btnDelete.gameObject.SetActive(false);
            card.togFollow.gameObject.SetActive(false);
            card.togReady.onValueChanged.RemoveAllListeners();
            foreach (var graphic in card.togReady.GetComponentsInChildren<Graphic>(true))
                graphic.raycastTarget = false;
            card.togReady.SetIsOnWithoutNotify(false);
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
            RefreshGameState();
            _cell.view.togGroup.SetActiveButton(_spectrumButton);
            _cell.view.recipeScroll.gameObject.SetActive(false);
            _cell.view.itemScroll.gameObject.SetActive(false);
            _cell.view.craftingLogScroll.gameObject.SetActive(false);
            _smartPanel.SetActive(false);
            _spectrumPanel.SetActive(true);
            // 丹谱是统一状态的投影，不依赖后台任务恰好在当前页面完成。
            RefreshStaticAvailabilityAndQueueFallbacks();
            if (_cell.lockRecipeSto != null) EnqueueRecipe(_cell.lockRecipeSto.recipeId, true);
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
            RenderSelectedPlanOrEnqueue();
        }

        private void RenderSelectedPlanOrEnqueue()
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

            var globalOutcome = ReadAlchemyGlobalOutcome(Furnace);
            var cacheKey = BuildSolutionCacheKey(_solvedRecipe, globalOutcome);
            var state = GetOrCreatePlanState(cacheKey, _solvedRecipe.id);
            state.PrepareInventory(_inventorySignature, _inventory);
            _selectedPlanState = state;
            if (state.InventoryComplete || state.Solutions.Count > 0)
            {
                RenderSmartResults();
            }
            else
            {
                SetMessage("智能炼丹\n\n丹谱已在后台预热当前丹方……\n可以继续查看丹方、切换页面或进行游戏");
            }
            if (!state.InventoryComplete) EnqueueRecipe(_solvedRecipe.id, true);
        }

        private void EnqueueRecipe(int recipeId, bool prioritize)
        {
            if (recipeId <= 0 || Furnace == null) return;
            if (_activeRecipeId == recipeId && _solveTask != null && !_solveTask.IsCompleted) return;
            var recipe = Singleton<TbDataImpl>.Instance.GetDrugRecipeCfg(recipeId);
            if (recipe != null)
            {
                var globalOutcome = ReadAlchemyGlobalOutcome(Furnace);
                var cacheKey = BuildSolutionCacheKey(recipe, globalOutcome);
                if (SessionPlanStates.TryGetValue(cacheKey, out var cachedState))
                {
                    cachedState.PrepareInventory(_inventorySignature, _inventory);
                    if (cachedState.InventoryComplete)
                    {
                        RefreshRecipeViews(recipe, cachedState);
                        return;
                    }
                }
            }
            if (_queuedRecipeIds.Contains(recipeId))
            {
                if (!prioritize) return;
                var existing = _prefetchQueue.Find(recipeId);
                if (existing != null) _prefetchQueue.Remove(existing);
            }
            if (prioritize) _prefetchQueue.AddFirst(recipeId);
            else _prefetchQueue.AddLast(recipeId);
            _queuedRecipeIds.Add(recipeId);
            if (_solveQueueCoroutine == null) _solveQueueCoroutine = StartCoroutine(ProcessSolveQueue());
        }

        private IEnumerator ProcessSolveQueue()
        {
            if (_solveCancellation == null || _solveCancellation.IsCancellationRequested)
            {
                _solveCancellation?.Dispose();
                _solveCancellation = new CancellationTokenSource();
            }
            while (_prefetchQueue.Count > 0 && !_solveCancellation.IsCancellationRequested)
            {
                var node = _prefetchQueue.First;
                _prefetchQueue.RemoveFirst();
                var recipeId = node.Value;
                _queuedRecipeIds.Remove(recipeId);
                var recipe = Singleton<TbDataImpl>.Instance.GetDrugRecipeCfg(recipeId);
                if (recipe == null || Furnace == null) continue;

                var globalOutcome = ReadAlchemyGlobalOutcome(Furnace);
                var cacheKey = BuildSolutionCacheKey(recipe, globalOutcome);
                var state = GetOrCreatePlanState(cacheKey, recipe.id);
                state.PrepareInventory(_inventorySignature, _inventory);
                if (state.InventoryComplete)
                {
                    RefreshRecipeViews(recipe, state);
                    continue;
                }
                var request = new AlchemySolveRequest
                {
                    CacheKey = cacheKey,
                    Generation = _solveGeneration,
                    Recipe = recipe,
                    Furnace = Furnace,
                    GlobalCountBonus = globalOutcome.count,
                    GlobalQualityBonus = globalOutcome.quality,
                    Herbs = Herbs.ToList(),
                    Inventory = new Dictionary<int, long>(_inventory),
                    InventorySignature = _inventorySignature,
                    CachedStaticSolutions = state.StaticSolutions.ToList(),
                    StaticComplete = state.StaticComplete,
                };
                _activeRecipeId = recipeId;
                _activeSolveKey = cacheKey;
                var progress = new AlchemySolveProgress();
                Debug.Log($"[Code4101 Tiandao] prefetch started recipe={recipeId}, " +
                          $"staticComplete={request.StaticComplete}, staticCached={request.CachedStaticSolutions.Count}");
                _solveTask = AlchemySolveWorker.RunAsync(request, progress, _solveCancellation.Token);
                var publishedRevision = 0;
                var nextRefresh = Time.realtimeSinceStartup;
                while (!_solveTask.IsCompleted)
                {
                    if (Time.realtimeSinceStartup >= nextRefresh)
                    {
                        nextRefresh = Time.realtimeSinceStartup + 0.25f;
                        PublishSolveProgress(recipe, request, progress, ref publishedRevision);
                    }
                    yield return null;
                }
                if (_solveTask.IsCanceled) break;
                var result = _solveTask.Result;
                if (result.Error != null)
                {
                    Debug.LogError($"[Code4101 Tiandao] prefetch failed recipe={recipeId}: {result.Error}");
                    continue;
                }
                var staticSolutions = result.Solutions
                    .Where(solution => solution.SearchStage == 1 || solution.SearchStage == 2)
                    .OrderBy(solution => solution.SearchStage)
                    .ToList();
                state.Complete(result.Solutions, request.InventorySignature, request.Inventory);
                if (request.InventorySignature == _inventorySignature)
                {
                    RefreshRecipeViews(recipe, state);
                }
                else
                {
                    state.PrepareInventory(_inventorySignature, _inventory);
                    RefreshRecipeViews(recipe, state);
                    if (staticSolutions.Any(solution => solution.IsAvailable(_inventory)))
                    {
                        state.Complete(staticSolutions, _inventorySignature, _inventory);
                        RefreshRecipeViews(recipe, state);
                    }
                    else
                    {
                        EnqueueRecipe(recipeId, false);
                    }
                }
                Debug.Log($"[Code4101 Tiandao] prefetch solved recipe={recipeId}, solutions={result.Solutions.Count}, " +
                          $"static={result.StaticElapsedMilliseconds}ms, backpack={result.BackpackElapsedMilliseconds}ms, " +
                          $"total={result.ElapsedMilliseconds}ms");
                _activeRecipeId = 0;
                _activeSolveKey = null;
            }
            _solveQueueCoroutine = null;
            _activeRecipeId = 0;
            _activeSolveKey = null;
            if (_prefetchQueue.Count > 0 && SmartAlchemyFeature.IsEnabled)
                _solveQueueCoroutine = StartCoroutine(ProcessSolveQueue());
        }

        private void PublishSolveProgress(
            TbDrugRecipeCfg recipe,
            AlchemySolveRequest request,
            AlchemySolveProgress progress,
            ref int publishedRevision)
        {
            var snapshot = progress.Snapshot(3, out var revision);
            if (revision <= publishedRevision || snapshot.Count == 0) return;
            publishedRevision = revision;
            var state = GetOrCreatePlanState(request.CacheKey, recipe.id);
            state.Publish(snapshot, request.InventorySignature, request.Inventory);
            if (request.InventorySignature == _inventorySignature) RefreshRecipeViews(recipe, state);
        }

        private void RefreshRecipeViews(TbDrugRecipeCfg recipe, AlchemyPlanState state)
        {
            RefreshSpectrumAvailability(recipe.id, state.Solutions);
            if (!_smartPanel.activeSelf || _cell.lockRecipeSto?.recipeId != recipe.id) return;
            var selectedOutcome = ReadAlchemyGlobalOutcome(Furnace);
            var selectedKey = BuildSolutionCacheKey(recipe, selectedOutcome);
            if (selectedKey != state.CacheKey) return;
            _solvedRecipe = recipe;
            _selectedPlanState = state;
            RenderSmartResults();
        }

        private static AlchemyPlanState GetOrCreatePlanState(string cacheKey, int recipeId)
        {
            if (SessionPlanStates.TryGetValue(cacheKey, out var state)) return state;
            state = new AlchemyPlanState { CacheKey = cacheKey, RecipeId = recipeId };
            SessionPlanStates[cacheKey] = state;
            return state;
        }

        private void RefreshSpectrumAvailability(int recipeId, IReadOnlyList<AlchemySolution> solutions)
        {
            if (!_spectrumCardsByRecipe.TryGetValue(recipeId, out var card) || card?.togReady == null) return;
            var available = solutions != null && solutions.Any(solution => solution.IsAvailable(_inventory));
            if (card.togReady.isOn != available)
                Debug.Log($"[Code4101 Tiandao] spectrum availability recipe={recipeId}, available={available}");
            card.togReady.SetIsOnWithoutNotify(available);
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

        private string BuildSolutionCacheKey(
            TbDrugRecipeCfg recipe,
            CraftingDrugTmp globalOutcome)
        {
            var data = Singleton<TbDataImpl>.Instance;
            var furnaceCfg = data.GetCraftingItemCfg(Furnace.itemId.sedId);
            var furnaceShape = furnaceCfg == null ? "" :
                $"{furnaceCfg.yangGridSize.x}x{furnaceCfg.yangGridSize.y}:{furnaceCfg.yinGridSize.x}x{furnaceCfg.yinGridSize.y}";
            return $"solver-v21|{recipe.id}|{recipe.attrLimiteStr}|{recipe.stateIdStr}|" +
                   $"{Furnace.itemId.blendEnum}:{Furnace.itemId.sedId}:{furnaceShape}:" +
                   $"count+{globalOutcome.count}:quality+{globalOutcome.quality}:" +
                   $"day+{globalOutcome.day}:dayMul={globalOutcome.dayMul.ToString("R", CultureInfo.InvariantCulture)}|" +
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
            var solutions = _selectedPlanState?.Solutions ?? new List<AlchemySolution>();
            if (solutions.Count == 0)
            {
                SetMessage($"智能炼丹\n\n{_solvedRecipe.GetName}\n当前丹炉无法组成该丹方");
                return;
            }

            _message.gameObject.SetActive(false);
            var visibleSolutions = solutions.OrderBy(solution => solution.SearchStage).Take(3).ToList();
            var globalOutcome = ReadAlchemyGlobalOutcome(Furnace);
            var count = visibleSolutions.Count;
            for (var index = 0; index < count; index++)
            {
                var solution = visibleSolutions[index];
                var card = ABMgr.InstantiateObj(_cell.view.craftingLogInfoCellPrefab, _smartContent);
                card.gameObject.name = $"Code4101SmartSolution_{index + 1}";
                card.SetInfo(solution.ToTemplate(_solvedRecipe, index), false, false);
                var plantingCost = CreatePlantingCostField(card, solution, _solvedRecipe);
                var value = CreateValueField(card, solution, _solvedRecipe, globalOutcome);
                NormalizeSmartSolutionCard(card, plantingCost, value);
                card.gameObject.SetActive(true);
                _smartResultObjects.Add(card.gameObject);
            }
            LayoutRebuilder.ForceRebuildLayoutImmediate(_smartContent);
        }

        private static TextPro CreatePlantingCostField(
            CraftingLogInfoCell card,
            AlchemySolution solution,
            TbDrugRecipeCfg recipe)
        {
            if (card?.txtAttrLimit == null || solution == null || recipe == null) return null;
            var totalPillCount = solution.BasePillCount + solution.TotalCountBonus;
            if (totalPillCount <= 0) return null;

            var daysPerPill = solution.PlantingDaysPerPill;
            var cost = daysPerPill > 300d
                ? (daysPerPill / 360d).ToString("F2", CultureInfo.InvariantCulture) + " 年"
                : daysPerPill.ToString("F2", CultureInfo.InvariantCulture) + " 天";
            var field = Instantiate(card.txtAttrLimit, card.txtAttrLimit.transform.parent);
            field.gameObject.name = "Code4101PlantingCost";
            foreach (var localization in field.GetComponentsInChildren<TextProLocalization>(true))
                localization.enabled = false;
            // 克隆官方字段会同时克隆内部标题；标题和值必须分别修改，不能只覆盖值。
            foreach (var nested in field.GetComponentsInChildren<TextPro>(true))
            {
                if (nested == field) continue;
                if ((nested.text ?? string.Empty).Contains("灵草") ||
                    nested.gameObject.name.IndexOf("title", System.StringComparison.OrdinalIgnoreCase) >= 0 ||
                    nested.gameObject.name.IndexOf("label", System.StringComparison.OrdinalIgnoreCase) >= 0)
                    nested.text = "成本：";
            }
            field.text = $"每丹种植时间 {cost}";
            field.raycastTarget = false;
            field.transform.SetSiblingIndex(card.txtAttrLimit.transform.GetSiblingIndex() + 1);
            return field;
        }

        private TextPro CreateValueField(
            CraftingLogInfoCell card,
            AlchemySolution solution,
            TbDrugRecipeCfg recipe,
            CraftingDrugTmp globalOutcome)
        {
            if (card?.txtAttrLimit == null || solution == null || recipe == null) return null;
            var output = solution.ResolveOutputItem(recipe);
            if (output == null) return null;
            var pillCount = Math.Max(0, solution.BasePillCount + solution.TotalCountBonus);
            var outputValue = (long)output.price * pillCount;
            var materialValue = solution.MaterialValue();
            var craftingDays = solution.CraftingDays(recipe, globalOutcome.day, globalOutcome.dayMul);
            var netValue = outputValue - materialValue;
            var dailyProfit = netValue / (double)Math.Max(1, craftingDays);
            var roundedDailyProfit = (long)Math.Round(dailyProfit, MidpointRounding.AwayFromZero);

            var field = Instantiate(card.txtAttrLimit, card.txtAttrLimit.transform.parent);
            field.gameObject.name = "Code4101AlchemyValue";
            foreach (var localization in field.GetComponentsInChildren<TextProLocalization>(true))
                localization.enabled = false;
            foreach (var nested in field.GetComponentsInChildren<TextPro>(true))
            {
                if (nested == field) continue;
                if ((nested.text ?? string.Empty).Contains("灵草") ||
                    nested.gameObject.name.IndexOf("title", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    nested.gameObject.name.IndexOf("label", StringComparison.OrdinalIgnoreCase) >= 0)
                    nested.text = "价值：";
            }
            field.text = $"日收益 {CompactNumberDisplay.Format(roundedDailyProfit)}";
            field.raycastTarget = false;
            field.transform.SetSiblingIndex(card.txtAttrLimit.transform.GetSiblingIndex() + 2);
            return field;
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
            ClearSmartResults();
            _selectedPlanState = null;
            var recipeId = _cell.lockRecipeSto?.recipeId ?? 0;
            _solvedRecipe = recipeId > 0
                ? Singleton<TbDataImpl>.Instance.GetDrugRecipeCfg(recipeId)
                : null;
            if (_message != null)
            {
                SetMessage("智能炼丹\n\n丹方已更换，丹谱正在后台预热计算");
            }
            if (_smartContent != null)
            {
                _smartContent.anchoredPosition = Vector2.zero;
            }
            if (recipeId > 0)
            {
                EnqueueRecipe(recipeId, true);
                if (_smartPanel.activeSelf) RenderSelectedPlanOrEnqueue();
                if (_spectrumPanel.activeSelf) RefreshStaticAvailabilityAndQueueFallbacks();
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
            _activeRecipeId = 0;
            _solveGeneration++;
            _prefetchQueue.Clear();
            _queuedRecipeIds.Clear();
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

        private void NormalizeSmartSolutionCard(CraftingLogInfoCell card, params TextPro[] extraFields)
        {
            const float minimumCardHeight = 206f;
            const float extraFieldGap = 6f;
            const float bottomPadding = 12f;
            var cardRect = card.transform as RectTransform;
            if (cardRect == null) return;
            cardRect.anchorMin = new Vector2(0f, 1f);
            cardRect.anchorMax = new Vector2(1f, 1f);
            cardRect.pivot = new Vector2(0.5f, 1f);
            cardRect.sizeDelta = new Vector2(cardRect.sizeDelta.x, minimumCardHeight);
            foreach (var canvasGroup in card.GetComponentsInChildren<CanvasGroup>(true))
            {
                canvasGroup.alpha = 1f;
                canvasGroup.interactable = true;
                canvasGroup.blocksRaycasts = true;
            }
            Canvas.ForceUpdateCanvases();
            var attrRect = card.txtAttrLimit.transform as RectTransform;
            if (attrRect != null)
            {
                var requiredHeight = Mathf.Max(attrRect.rect.height, card.txtAttrLimit.preferredHeight + 8f);
                SetHeightPreservingTop(attrRect, requiredHeight);
            }

            var previousRect = attrRect;
            foreach (var extraField in extraFields ?? new TextPro[0])
            {
                var extraRect = extraField?.transform as RectTransform;
                if (previousRect == null || extraRect == null) continue;
                var extraHeight = Mathf.Max(28f, extraField.preferredHeight + 4f);
                var previousBottom = previousRect.TransformPoint(new Vector3(
                    previousRect.rect.xMin + previousRect.rect.width * previousRect.pivot.x,
                    previousRect.rect.yMin,
                    0f));
                extraRect.anchorMin = previousRect.anchorMin;
                extraRect.anchorMax = previousRect.anchorMax;
                extraRect.pivot = new Vector2(previousRect.pivot.x, 1f);
                extraRect.sizeDelta = new Vector2(previousRect.sizeDelta.x, extraHeight);
                // 两行可能处于拉伸锚点下，直接相减 anchoredPosition 会使用错误的旧高度。
                // 以真实世界坐标的上一行底边为基准，保证灵草换行后成本、价值顺次下移。
                extraRect.position = previousBottom +
                                     extraRect.parent.TransformVector(new Vector3(0f, -extraFieldGap, 0f));
                previousRect = extraRect;
            }

            Canvas.ForceUpdateCanvases();
            var contentBottom = previousRect == null
                ? cardRect.rect.yMin
                : cardRect.InverseTransformPoint(previousRect.TransformPoint(new Vector3(
                    previousRect.rect.center.x,
                    previousRect.rect.yMin,
                    0f))).y;
            var height = Mathf.Max(minimumCardHeight, cardRect.rect.yMax - contentBottom + bottomPadding);
            cardRect.sizeDelta = new Vector2(cardRect.sizeDelta.x, height);
            var cardLayout = card.GetComponent<LayoutElement>() ?? card.gameObject.AddComponent<LayoutElement>();
            cardLayout.ignoreLayout = false;
            cardLayout.preferredWidth = -1f;
            cardLayout.minWidth = -1f;
            cardLayout.flexibleWidth = 1f;
            cardLayout.preferredHeight = height;
            cardLayout.minHeight = height;
        }

        private static void SetHeightPreservingTop(RectTransform rect, float height)
        {
            if (rect == null) return;
            var top = rect.TransformPoint(new Vector3(
                rect.rect.xMin + rect.rect.width * rect.pivot.x,
                rect.rect.yMax,
                0f));
            rect.pivot = new Vector2(rect.pivot.x, 1f);
            rect.sizeDelta = new Vector2(rect.sizeDelta.x, height);
            rect.position = top;
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

            var previousFurnaceId = Furnace?.itemId.sedId ?? 0;
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
            var furnaceChanged = previousFurnaceId != 0 && previousFurnaceId != Furnace.itemId.sedId;
            if (furnaceChanged)
            {
                CancelActiveSolve();
                foreach (var card in _spectrumCardsByRecipe.Values)
                {
                    if (card?.togReady != null) card.togReady.SetIsOnWithoutNotify(false);
                }
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

            var nextInventorySignature = string.Join(";", _inventory.OrderBy(pair => pair.Key)
                .Select(pair => $"{pair.Key}:{pair.Value}"));
            var inventoryChanged = nextInventorySignature != _inventorySignature;
            if (inventoryChanged)
            {
                _inventorySignature = nextInventorySignature;
                foreach (var card in _spectrumCardsByRecipe.Values)
                {
                    if (card?.togReady != null) card.togReady.SetIsOnWithoutNotify(false);
                }
                RefreshStaticAvailabilityAndQueueFallbacks();
            }
            if (furnaceChanged)
            {
                foreach (var pair in _spectrumCardsByRecipe)
                {
                    if (_cell.lockRecipeSto?.recipeId == pair.Key || pair.Value.gameObject.activeSelf)
                        EnqueueRecipe(pair.Key, _cell.lockRecipeSto?.recipeId == pair.Key);
                }
            }

            var totalCount = _inventory.Values.Sum();
            if (_message != null)
            {
                SetMessage(
                    $"智能炼丹\n\n丹炉：{Furnace.name}\n" +
                    $"背包药材：{_inventory.Count} 种，共 {totalCount} 份\n" +
                    "丹炉与药材数据已就绪");
            }
        }

        private void RefreshStaticAvailabilityAndQueueFallbacks()
        {
            if (Furnace == null) return;
            foreach (var pair in _spectrumCardsByRecipe)
            {
                var recipe = Singleton<TbDataImpl>.Instance.GetDrugRecipeCfg(pair.Key);
                if (recipe == null) continue;
                var outcome = ReadAlchemyGlobalOutcome(Furnace);
                var cacheKey = BuildSolutionCacheKey(recipe, outcome);
                if (!SessionPlanStates.TryGetValue(cacheKey, out var state)) continue;
                state.PrepareInventory(_inventorySignature, _inventory);
                RefreshRecipeViews(recipe, state);
                if (!state.InventoryComplete &&
                    (_cell.lockRecipeSto?.recipeId == pair.Key ||
                     (_spectrumCardsByRecipe.TryGetValue(pair.Key, out var card) && card.gameObject.activeSelf)))
                    EnqueueRecipe(pair.Key, _cell.lockRecipeSto?.recipeId == pair.Key);
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
