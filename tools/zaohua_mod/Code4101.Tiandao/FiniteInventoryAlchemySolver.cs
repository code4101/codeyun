using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace Code4101.Zaohua.Tiandao
{
    internal sealed class AlchemyPlacement
    {
        internal BlendId ItemId;
        internal int PoolType;
        internal MyVector2Int Position;
        internal int Rotation;
    }

    internal sealed class AlchemySolution
    {
        internal List<AlchemyPlacement> Placements = new List<AlchemyPlacement>();
        internal Dictionary<int, int> ItemCounts = new Dictionary<int, int>();
        internal int PlantingDays;
        internal int BasePillCount;
        internal int SearchStage;
        internal int GlobalCountBonus;
        internal int GlobalQualityBonus;
        internal AlchemyRuleOutcome RuleOutcome = new AlchemyRuleOutcome();

        internal int TotalCountBonus => GlobalCountBonus + RuleOutcome.CountBonus;
        internal int TotalQualityBonus => GlobalQualityBonus + RuleOutcome.QualityBonus;
        internal int QualityRank => Math.Max(1, Math.Min(3, 1 + TotalQualityBonus));
        internal double PlantingDaysPerPill =>
            (double)PlantingDays / Math.Max(1, BasePillCount + TotalCountBonus);

        internal bool IsAvailable(IReadOnlyDictionary<int, long> inventory)
        {
            return ItemCounts.All(pair => inventory != null &&
                inventory.TryGetValue(pair.Key, out var count) && count >= pair.Value);
        }

        internal TbCraftingTemplateSto ToTemplate(TbDrugRecipeCfg recipe, int index)
        {
            var quality = QualityRank;
            var data = Singleton<TbDataImpl>.Instance;
            var output = data.itemCfgList
                .Where(item => item.id == recipe.itemId || item.groupId == recipe.itemId)
                .Where(item => item.drugQuality <= quality)
                .OrderByDescending(item => item.drugQuality)
                .FirstOrDefault() ?? data.GetItemCfg(recipe.itemId);
            var bonuses = new List<string>();
            if (TotalQualityBonus != 0) bonuses.Add($"品质{TotalQualityBonus:+#;-#;0}");
            if (TotalCountBonus != 0) bonuses.Add($"成丹{TotalCountBonus:+#;-#;0}");
            return new TbCraftingTemplateSto
            {
                id = -100000 - index,
                type = 0,
                isFollow = false,
                name = recipe.GetName +
                       (bonuses.Count == 0 ? "" : $"（{string.Join("，", bonuses)}）"),
                itemId = output.blendId,
                itemLogStoList = Placements.Select(p => new TbCraftingItemLogSto(
                    p.ItemId,
                    p.PoolType,
                    p.Position,
                    p.Rotation)).ToList(),
            };
        }
    }

    internal static class FiniteInventoryAlchemySolver
    {
        private const int FormNodeLimit = 160000;
        private const int QuantityNodeLimit = 240000;
        private const int QuantityNodesPerForm = 5000;
        private const int PackingNodeLimit = 30000;
        private const int QuantityCandidatesPerForm = 8;
        private const int RepresentativesPerRuleTier = 3;
        private static readonly int[] PlantingDaysByGrade =
            { 10, 20, 30, 360, 720, 1080, 3600, 7200, 10800, 36000, 72000, 108000 };

        private sealed class HerbCandidate
        {
            internal SmartAlchemyUi.HerbStock Stock;
            internal TbCraftingItemCfg Crafting;
            internal TbDrawCfg Draw;
            internal int Side;
            internal int GradeWeight;
            internal int PlantingDays;
            internal HashSet<string> ExtraKeys;
            internal List<RotationShape> Rotations;
            internal int CellCount;
            internal double HeuristicScore;
        }

        private sealed class RotationShape
        {
            internal int Rotation;
            internal List<MyVector2Int> Cells;
            internal List<MyVector2Int> Normalized;
            internal int MinX;
            internal int MinY;
            internal int Width;
            internal int Height;
        }

        private sealed class PlacementPose
        {
            internal HerbCandidate Candidate;
            internal RotationShape Shape;
            internal int X;
            internal int Y;
            internal ulong LowMask;
            internal ulong HighMask;
            internal int RuleHint;
        }

        private sealed class QuantityProfile
        {
            internal int[] Counts;
            internal string ExactKey;
            internal string RuleTierKey;
            internal int RuleTierScore;
            internal int PlantingDays;
            internal int YangCells;
            internal int YinCells;
            internal int PieceCount;
        }

        internal static List<AlchemySolution> SolvePhased(
            TbDrugRecipeCfg recipe,
            TbPackSto furnace,
            int globalCountBonus,
            int globalQualityBonus,
            IReadOnlyList<SmartAlchemyUi.HerbStock> catalog,
            IReadOnlyDictionary<int, long> inventory,
            int limit,
            CancellationToken cancellationToken = default,
            Action<AlchemySolution> onSolution = null,
            Action<int> onStageCompleted = null)
        {
            var combined = new List<AlchemySolution>();
            for (var stage = 1; stage <= 3; stage++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var availableInStage = new List<AlchemySolution>();
                var stageSolutions = Solve(recipe, furnace, globalCountBonus, globalQualityBonus,
                    catalog, limit, stage,
                    cancellationToken, solution =>
                    {
                        solution.SearchStage = stage;
                        if (solution.IsAvailable(inventory)) availableInStage.Add(solution);
                        onSolution?.Invoke(solution);
                    });
                foreach (var solution in stageSolutions) solution.SearchStage = stage;
                combined.AddRange(stageSolutions);
                combined.AddRange(availableInStage);
                combined = RankAndSelectSolutions(combined, limit);
                onStageCompleted?.Invoke(stage);
                // 阶段内必须完整求解；阶段结束后，只要已有背包可用解就短路。
                if (availableInStage.Count > 0)
                {
                    if (!combined.Any(solution => solution.IsAvailable(inventory)))
                    {
                        var bestAvailable = RankAndSelectSolutions(availableInStage, 1).First();
                        if (combined.Count >= limit) combined.RemoveAt(combined.Count - 1);
                        combined.Add(bestAvailable);
                    }
                    break;
                }
            }
            return RankAndSelectSolutions(combined, limit);
        }

        private static List<AlchemySolution> Solve(
            TbDrugRecipeCfg recipe,
            TbPackSto furnace,
            int globalCountBonus,
            int globalQualityBonus,
            IReadOnlyList<SmartAlchemyUi.HerbStock> stocks,
            int limit,
            int searchStage,
            CancellationToken cancellationToken = default,
            Action<AlchemySolution> onSolution = null)
        {
            var stageStopwatch = System.Diagnostics.Stopwatch.StartNew();
            if (recipe == null || furnace == null || stocks == null || limit <= 0)
            {
                return new List<AlchemySolution>();
            }

            var data = Singleton<TbDataImpl>.Instance;
            var furnaceCfg = data.GetCraftingItemCfg(furnace.itemId.sedId);
            if (furnaceCfg == null)
            {
                return new List<AlchemySolution>();
            }

            var candidateStopwatch = System.Diagnostics.Stopwatch.StartNew();
            var candidates = BuildCandidates(stocks, recipe)
                .Where(candidate => searchStage == 3 || candidate.ExtraKeys.Count <= searchStage - 1)
                // 先找到能覆盖目标属性的可行形式，再在成品解上比较种植成本。
                // 若先按种植天数深搜，大量低阶组合会在节点上限前饿死后面的高阶精确药材。
                .OrderByDescending(candidate => candidate.HeuristicScore)
                .ThenBy(candidate => candidate.PlantingDays)
                .ThenBy(candidate => candidate.CellCount)
                .ThenBy(candidate => candidate.GradeWeight)
                .ThenBy(candidate => candidate.Stock.ItemId.sedId)
                .ThenBy(candidate => candidate.Side)
                .ToList();
            candidateStopwatch.Stop();
            if (candidates.Count == 0)
            {
                return new List<AlchemySolution>();
            }

            var target = recipe.AttrLimitDic
                .Where(pair => pair.Value != 0)
                .ToDictionary(pair => pair.Key, pair => pair.Value);
            var allKeys = new HashSet<string>(target.Keys);
            foreach (var candidate in candidates)
            {
                if (candidate.Crafting.attrDic == null) continue;
                foreach (var key in candidate.Crafting.attrDic.Keys) allKeys.Add(key);
            }

            var form = new List<HerbCandidate>();
            var solutions = new List<AlchemySolution>();
            var solutionKeys = new HashSet<string>();
            var poseModelCache = new Dictionary<HerbCandidate, List<PlacementPose>>();
            var formNodes = 0;
            var quantityNodes = 0;
            var solvedForms = 0;
            var packingAttempts = 0;
            var balancedQuantityVectors = 0;
            var quantityRuleTiers = 0;
            var quantityRepresentatives = 0;
            var largestFormSizeSearched = 0;
            long packingMilliseconds = 0;
            var maxPieces = Math.Min(
                (furnaceCfg.yangGridSize.x * furnaceCfg.yangGridSize.y) +
                (furnaceCfg.yinGridSize.x * furnaceCfg.yinGridSize.y),
                int.MaxValue);
            var maxFormSize = Math.Min(maxPieces, Math.Max(3, Math.Min(7, allKeys.Count + searchStage + 1)));

            void EvaluateForm()
            {
                cancellationToken.ThrowIfCancellationRequested();
                var extraKeyCount = form.SelectMany(candidate => candidate.ExtraKeys).Distinct().Count();
                var requiredExtraCount = searchStage == 1 ? 0 : searchStage == 2 ? 1 : 2;
                if ((searchStage < 3 && extraKeyCount != requiredExtraCount) ||
                    (searchStage == 3 && extraKeyCount < requiredExtraCount) ||
                    !CanFormReachTarget(form, target, allKeys)) return;

                solvedForms++;
                var quantityCandidates = SolveQuantitiesForForm(
                    form, target, allKeys, furnaceCfg, recipe, cancellationToken,
                    ref quantityNodes, QuantityNodeLimit, QuantityCandidatesPerForm,
                    out var balancedCount, out var ruleTierCount);
                balancedQuantityVectors += balancedCount;
                quantityRuleTiers += ruleTierCount;
                quantityRepresentatives += quantityCandidates.Count;
                foreach (var counts in quantityCandidates)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    var chosen = ExpandForm(form, counts);
                    var quantityKey = string.Join(";", form.Select((candidate, index) =>
                        $"{candidate.Stock.ItemId.sedId}:{candidate.Side}:{counts[index]}"));
                    if (!solutionKeys.Add(quantityKey)) continue;
                    packingAttempts++;
                    var packingStopwatch = System.Diagnostics.Stopwatch.StartNew();
                    var packed = TryPack(chosen, furnaceCfg, recipe, poseModelCache,
                        out var placements, out var ruleOutcome, cancellationToken);
                    packingStopwatch.Stop();
                    packingMilliseconds += packingStopwatch.ElapsedMilliseconds;
                    if (packed)
                    {
                        var solution = new AlchemySolution
                        {
                            Placements = placements,
                            ItemCounts = chosen.GroupBy(c => c.Stock.ItemId.sedId)
                                .ToDictionary(group => group.Key, group => group.Count()),
                            PlantingDays = chosen.Sum(c => c.PlantingDays),
                            BasePillCount = recipe.count,
                            SearchStage = searchStage,
                            GlobalCountBonus = globalCountBonus,
                            GlobalQualityBonus = globalQualityBonus,
                            RuleOutcome = ruleOutcome,
                        };
                        solutions.Add(solution);
                        onSolution?.Invoke(solution);
                    }
                }
            }

            void SearchForms(int startIndex, int targetFormSize)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (formNodes++ >= FormNodeLimit || quantityNodes >= QuantityNodeLimit) return;

                if (form.Count == targetFormSize)
                {
                    EvaluateForm();
                    return;
                }

                var extraKeys = new HashSet<string>(form.SelectMany(candidate => candidate.ExtraKeys));
                if (searchStage < 3 && extraKeys.Count > searchStage - 1) return;

                var remaining = targetFormSize - form.Count;
                for (var index = startIndex; index <= candidates.Count - remaining; index++)
                {
                    var candidate = candidates[index];
                    if (form.Any(existing => existing.Stock.ItemId.sedId == candidate.Stock.ItemId.sedId &&
                                             existing.Side != candidate.Side)) continue;
                    if (searchStage < 3 && extraKeys.Union(candidate.ExtraKeys).Distinct().Count() > searchStage - 1)
                        continue;
                    form.Add(candidate);
                    SearchForms(index + 1, targetFormSize);
                    form.RemoveAt(form.Count - 1);
                    if (formNodes >= FormNodeLimit || quantityNodes >= QuantityNodeLimit) return;
                }
            }

            // 迭代加深保证先完整覆盖单药、双药等简单形式，避免传统深搜把节点
            // 全耗在“第一个候选 + 大量复杂组合”上，导致明显的两味药解也被漏掉。
            for (var formSize = 1;
                 formSize <= maxFormSize && formNodes < FormNodeLimit && quantityNodes < QuantityNodeLimit;
                 formSize++)
            {
                largestFormSizeSearched = formSize;
                SearchForms(0, formSize);
            }
            stageStopwatch.Stop();
            UnityEngine.Debug.Log($"[Code4101 Tiandao] alchemy stage={searchStage}, " +
                                  $"candidates={candidates.Count}, forms={formNodes}, solvedForms={solvedForms}, " +
                                  $"largestForm={largestFormSizeSearched}/{maxFormSize}, " +
                                  $"formLimitReached={formNodes >= FormNodeLimit}, " +
                                  $"quantityNodes={quantityNodes}, " +
                                  $"balancedQuantities={balancedQuantityVectors}, " +
                                  $"quantityTiers={quantityRuleTiers}, " +
                                  $"quantityRepresentatives={quantityRepresentatives}, " +
                                  $"packingAttempts={packingAttempts}, solutions={solutions.Count}, " +
                                  $"candidateMs={candidateStopwatch.ElapsedMilliseconds}, " +
                                  $"algebraMs={Math.Max(0, stageStopwatch.ElapsedMilliseconds - candidateStopwatch.ElapsedMilliseconds - packingMilliseconds)}, " +
                                  $"packingMs={packingMilliseconds}, elapsed={stageStopwatch.ElapsedMilliseconds}ms");
            return RankAndSelectSolutions(solutions, limit);
        }

        private static bool CanFormReachTarget(
            IReadOnlyList<HerbCandidate> form,
            IReadOnlyDictionary<string, int> target,
            IReadOnlyCollection<string> allKeys)
        {
            foreach (var key in allKeys)
            {
                var expected = target.TryGetValue(key, out var value) ? value : 0;
                var contributions = form.Select(candidate => GetContribution(candidate, key)).Where(value => value != 0).ToList();
                if (expected != 0 && !contributions.Any(value => Math.Sign(value) == Math.Sign(expected))) return false;
                if (expected == 0 && contributions.Count > 0 &&
                    !(contributions.Any(value => value > 0) && contributions.Any(value => value < 0))) return false;
                var gcd = contributions.Aggregate(0, (current, value) => GreatestCommonDivisor(current, Math.Abs(value)));
                if (gcd != 0 && Math.Abs(expected) % gcd != 0) return false;
            }
            return true;
        }

        private static List<int[]> SolveQuantitiesForForm(
            IReadOnlyList<HerbCandidate> form,
            IReadOnlyDictionary<string, int> target,
            IReadOnlyCollection<string> allKeys,
            TbCraftingItemCfg furnace,
            TbDrugRecipeCfg recipe,
            CancellationToken cancellationToken,
            ref int totalNodes,
            int nodeLimit,
            int resultLimit,
            out int balancedCount,
            out int ruleTierCount)
        {
            var keys = allKeys.OrderBy(key => key).ToList();
            var pivotIndex = Enumerable.Range(0, form.Count)
                .OrderByDescending(index => keys.Count(key => GetContribution(form[index], key) != 0))
                .ThenByDescending(index => keys.Sum(key => Math.Abs(GetContribution(form[index], key))))
                .First();
            var variableIndexes = Enumerable.Range(0, form.Count).Where(index => index != pivotIndex)
                .OrderBy(index => MaximumCount(form[index], furnace)).ToList();
            var counts = Enumerable.Repeat(1, form.Count).ToArray();
            var residual = keys.ToDictionary(key => key, key => target.TryGetValue(key, out var value) ? value : 0);
            var yangCells = furnace.yangGridSize.x * furnace.yangGridSize.y;
            var yinCells = furnace.yinGridSize.x * furnace.yinGridSize.y;
            var results = new List<int[]>();
            var startNodes = totalNodes;
            var localNodes = totalNodes;
            balancedCount = 0;
            ruleTierCount = 0;

            void SearchCount(int depth, int usedYang, int usedYin)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (localNodes++ >= nodeLimit || localNodes - startNodes >= QuantityNodesPerForm) return;
                if (depth >= variableIndexes.Count)
                {
                    var pivot = form[pivotIndex];
                    int? pivotCount = null;
                    foreach (var key in keys)
                    {
                        var coefficient = GetContribution(pivot, key);
                        if (coefficient == 0)
                        {
                            if (residual[key] != 0) return;
                            continue;
                        }
                        if (residual[key] % coefficient != 0) return;
                        var derived = residual[key] / coefficient;
                        if (derived <= 0 || (pivotCount.HasValue && pivotCount.Value != derived)) return;
                        pivotCount = derived;
                    }
                    if (!pivotCount.HasValue) return;
                    counts[pivotIndex] = pivotCount.Value;
                    var finalYang = usedYang + (pivot.Side == 1 ? pivot.CellCount * pivotCount.Value : 0);
                    var finalYin = usedYin + (pivot.Side == 2 ? pivot.CellCount * pivotCount.Value : 0);
                    if (finalYang > yangCells || finalYin > yinCells) return;
                    results.Add((int[])counts.Clone());
                    return;
                }

                var variableIndex = variableIndexes[depth];
                var candidate = form[variableIndex];
                var availableCells = candidate.Side == 1 ? yangCells - usedYang : yinCells - usedYin;
                var maximum = availableCells / candidate.CellCount;
                for (var count = 1; count <= maximum; count++)
                {
                    counts[variableIndex] = count;
                    foreach (var key in keys) residual[key] -= GetContribution(candidate, key) * count;
                    SearchCount(depth + 1,
                        usedYang + (candidate.Side == 1 ? candidate.CellCount * count : 0),
                        usedYin + (candidate.Side == 2 ? candidate.CellCount * count : 0));
                    foreach (var key in keys) residual[key] += GetContribution(candidate, key) * count;
                    if (localNodes >= nodeLimit || localNodes - startNodes >= QuantityNodesPerForm) break;
                }
            }

            SearchCount(0, 0, 0);
            totalNodes = localNodes;
            balancedCount = results.Count;
            var representatives = SelectQuantityRepresentatives(form, results, recipe, resultLimit);
            ruleTierCount = representatives.Select(profile => profile.RuleTierKey).Distinct().Count();
            return representatives.Select(profile => profile.Counts).ToList();
        }

        private static List<QuantityProfile> SelectQuantityRepresentatives(
            IReadOnlyList<HerbCandidate> form,
            IEnumerable<int[]> quantityVectors,
            TbDrugRecipeCfg recipe,
            int limit)
        {
            var profiles = quantityVectors
                .Select(counts => BuildQuantityProfile(form, counts, recipe))
                .GroupBy(profile => profile.ExactKey)
                .Select(group => group.First())
                .ToList();
            if (profiles.Count == 0) return profiles;

            var frontiers = new List<QuantityProfile>();
            foreach (var tier in profiles.GroupBy(profile => profile.RuleTierKey))
            {
                var candidates = tier
                    .OrderBy(profile => profile.PlantingDays)
                    .ThenBy(profile => profile.YangCells + profile.YinCells)
                    .ThenBy(profile => profile.PieceCount)
                    .ToList();
                var frontier = candidates
                    .Where(candidate => !candidates.Any(other =>
                        !ReferenceEquals(other, candidate) && Dominates(other, candidate)))
                    .Take(RepresentativesPerRuleTier)
                    .ToList();
                frontiers.AddRange(frontier);
            }

            // 最省种植时间的代表无条件保留；其余优先保留能跨越更多丹方规则阈值、
            // 且在阴阳占格上不被支配的代表。数量仅 +/-1 但仍处于同一阈值区间时，
            // 不再各自触发一次几何装箱。
            var selected = new List<QuantityProfile>();
            var cheapest = profiles.OrderBy(profile => profile.PlantingDays)
                .ThenBy(profile => profile.YangCells + profile.YinCells)
                .ThenBy(profile => profile.PieceCount)
                .First();
            selected.Add(cheapest);
            selected.AddRange(frontiers
                .Where(profile => profile.ExactKey != cheapest.ExactKey)
                .OrderByDescending(profile => profile.RuleTierScore)
                .ThenBy(profile => profile.PlantingDays)
                .ThenBy(profile => profile.YangCells + profile.YinCells)
                .ThenBy(profile => profile.PieceCount));
            return selected.GroupBy(profile => profile.ExactKey)
                .Select(group => group.First())
                .Take(limit)
                .ToList();
        }

        private static QuantityProfile BuildQuantityProfile(
            IReadOnlyList<HerbCandidate> form,
            int[] counts,
            TbDrugRecipeCfg recipe)
        {
            var tierParts = new List<string>();
            var tierScore = 0;
            foreach (var stateId in recipe?.StateIds ?? new List<int>())
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                if (state == null) continue;
                var first = 0;
                var second = 0;
                var usesSecondTarget = state.relation >= 10 && state.relation <= 16;
                for (var index = 0; index < form.Count; index++)
                {
                    var candidate = form[index];
                    if (state.poolType != 0 && state.poolType != candidate.Side) continue;
                    var units = state.stateType == 0
                        ? candidate.CellCount * counts[index]
                        : counts[index];
                    if (TargetMayMatch(candidate.Stock.ItemCfg, state.target1))
                    {
                        if (state.relation == 0 && state.stateType >= 11 && state.stateType <= 18)
                        {
                            var keys = new[]
                                { "gold", "water", "wood", "fire", "soil", "ice", "wind", "thunder" };
                            var key = keys[state.stateType - 11];
                            if (candidate.Crafting.attrDic.TryGetValue(key, out var contribution))
                                first += contribution * (candidate.Side == 1 ? 1 : -1) * counts[index];
                        }
                        else
                        {
                            first += units;
                        }
                    }
                    if (usesSecondTarget && TargetMayMatch(candidate.Stock.ItemCfg, state.target2))
                        second += units;
                }
                var firstTier = QuantityRuleTier(first, state.calculateType);
                var secondTier = usesSecondTarget
                    ? QuantityRuleTier(second, state.calculateType)
                    : 0;
                tierParts.Add(usesSecondTarget
                    ? $"{stateId}:{firstTier}:{secondTier}"
                    : $"{stateId}:{firstTier}");
                tierScore += Math.Max(0, firstTier) + Math.Max(0, secondTier);
            }

            return new QuantityProfile
            {
                Counts = (int[])counts.Clone(),
                ExactKey = string.Join(",", counts),
                RuleTierKey = tierParts.Count == 0 ? "base" : string.Join(";", tierParts),
                RuleTierScore = tierScore,
                PlantingDays = form.Select((candidate, index) =>
                    candidate.PlantingDays * counts[index]).Sum(),
                YangCells = form.Select((candidate, index) => candidate.Side == 1
                    ? candidate.CellCount * counts[index]
                    : 0).Sum(),
                YinCells = form.Select((candidate, index) => candidate.Side == 2
                    ? candidate.CellCount * counts[index]
                    : 0).Sum(),
                PieceCount = counts.Sum(),
            };
        }

        private static int QuantityRuleTier(int value, string expression)
        {
            if (string.IsNullOrEmpty(expression)) return 0;
            var parts = expression.Split('#');
            if (!int.TryParse(parts[0], out var operation)) return 0;
            var threshold = parts.Length > 1 && int.TryParse(parts[1], out var parsed)
                ? parsed
                : 0;
            return operation switch
            {
                0 => value == threshold ? 1 : 0,
                1 => value > threshold ? 1 : 0,
                2 => value >= threshold ? 1 : 0,
                3 => value < threshold ? 1 : 0,
                4 => value <= threshold ? 1 : 0,
                5 => threshold == 0 ? 0 : value / threshold,
                _ => 0,
            };
        }

        private static bool Dominates(QuantityProfile left, QuantityProfile right)
        {
            var noWorse = left.PlantingDays <= right.PlantingDays &&
                          left.YangCells <= right.YangCells &&
                          left.YinCells <= right.YinCells &&
                          left.PieceCount <= right.PieceCount;
            var strictlyBetter = left.PlantingDays < right.PlantingDays ||
                                 left.YangCells < right.YangCells ||
                                 left.YinCells < right.YinCells ||
                                 left.PieceCount < right.PieceCount;
            return noWorse && strictlyBetter;
        }

        private static int MaximumCount(HerbCandidate candidate, TbCraftingItemCfg furnace)
        {
            var size = candidate.Side == 1 ? furnace.yangGridSize : furnace.yinGridSize;
            return Math.Max(1, size.x * size.y / Math.Max(1, candidate.CellCount));
        }

        private static int GetContribution(HerbCandidate candidate, string key)
        {
            return candidate.Crafting.attrDic.TryGetValue(key, out var value)
                ? value * (candidate.Side == 1 ? 1 : -1)
                : 0;
        }

        private static List<HerbCandidate> ExpandForm(IReadOnlyList<HerbCandidate> form, IReadOnlyList<int> counts)
        {
            var chosen = new List<HerbCandidate>();
            for (var index = 0; index < form.Count; index++)
            for (var count = 0; count < counts[index]; count++)
                chosen.Add(form[index]);
            return chosen;
        }

        internal static List<AlchemySolution> RankAndSelectSolutions(
            IEnumerable<AlchemySolution> solutions,
            int limit)
        {
            var orderedSolutions = solutions
                .OrderBy(solution => solution.SearchStage)
                .ThenByDescending(solution => solution.QualityRank)
                .ThenBy(solution => solution.PlantingDaysPerPill)
                .ThenBy(solution => solution.PlantingDays)
                .ThenBy(solution => solution.Placements.Count)
                .ThenBy(solution => string.Join(",", solution.ItemCounts.Keys.OrderBy(id => id)))
                .ToList();
            // 同一组药材种类只保留价值最高的数量/阴阳/布局变体。
            return orderedSolutions
                .GroupBy(solution => string.Join(",", solution.ItemCounts.Keys.OrderBy(id => id)))
                .Select(group => group.First())
                .Take(limit)
                .ToList();
        }

        private static IEnumerable<HerbCandidate> BuildCandidates(
            IReadOnlyList<SmartAlchemyUi.HerbStock> stocks,
            TbDrugRecipeCfg recipe)
        {
            var data = Singleton<TbDataImpl>.Instance;
            var targetKeys = new HashSet<string>(recipe.AttrLimitDic.Where(p => p.Value != 0).Select(p => p.Key));
            foreach (var stock in stocks)
            {
                var crafting = data.GetCraftingItemCfg(stock.ItemId.sedId);
                if (crafting?.attrDic == null || crafting.attrDic.Count == 0) continue;
                var draw = data.GetDrawCfg(crafting.drawId);
                if (draw?.Coordinates == null || draw.Coordinates.Count == 0) continue;
                var grade = data.GetGradeCfg(stock.ItemCfg.gradeId);
                var gradeWeight = grade?.weight ?? 0;
                var plantingDays = gradeWeight >= 1 && gradeWeight <= PlantingDaysByGrade.Length
                    ? PlantingDaysByGrade[gradeWeight - 1]
                    : int.MaxValue / 1000;
                var extraKeys = new HashSet<string>(crafting.attrDic
                    .Where(pair => pair.Value != 0 && !targetKeys.Contains(pair.Key))
                    .Select(pair => pair.Key));
                var rotations = BuildRotations(draw.Coordinates);
                for (var side = 1; side <= 2; side++)
                {
                    var sign = side == 1 ? 1 : -1;
                    var alignedContribution = crafting.attrDic.Sum(pair =>
                    {
                        var expected = recipe.AttrLimitDic.TryGetValue(pair.Key, out var value) ? value : 0;
                        var contribution = pair.Value * sign;
                        return Math.Sign(expected) == Math.Sign(contribution)
                            ? Math.Min(Math.Abs(expected), Math.Abs(contribution))
                            : -Math.Abs(contribution) * 0.35;
                    });
                    yield return new HerbCandidate
                    {
                        Stock = stock,
                        Crafting = crafting,
                        Draw = draw,
                        Side = side,
                        GradeWeight = gradeWeight,
                        PlantingDays = plantingDays,
                        ExtraKeys = extraKeys,
                        Rotations = rotations,
                        CellCount = draw.Coordinates.Count,
                        HeuristicScore = alignedContribution / Math.Max(1.0, draw.Coordinates.Count),
                    };
                }
            }
        }

        private static int GreatestCommonDivisor(int left, int right)
        {
            while (right != 0)
            {
                var remainder = left % right;
                left = right;
                right = remainder;
            }
            return Math.Abs(left);
        }

        private static List<RotationShape> BuildRotations(List<MyVector2Int> source)
        {
            var result = new List<RotationShape>();
            var seen = new HashSet<string>();
            for (var rotation = 0; rotation < 4; rotation++)
            {
                var cells = source.Select(cell => Rotate(cell, rotation)).ToList();
                var minX = cells.Min(cell => cell.x);
                var minY = cells.Min(cell => cell.y);
                var normalized = cells.Select(cell => new MyVector2Int(cell.x - minX, cell.y - minY))
                    .OrderBy(cell => cell.x).ThenBy(cell => cell.y).ToList();
                var key = string.Join(";", normalized.Select(cell => $"{cell.x},{cell.y}"));
                if (!seen.Add(key)) continue;
                result.Add(new RotationShape
                {
                    Rotation = rotation,
                    Cells = cells,
                    Normalized = normalized,
                    MinX = minX,
                    MinY = minY,
                    Width = normalized.Max(cell => cell.x) + 1,
                    Height = normalized.Max(cell => cell.y) + 1,
                });
            }
            return result;
        }

        private static MyVector2Int Rotate(MyVector2Int cell, int rotation)
        {
            return rotation switch
            {
                1 => new MyVector2Int(cell.y, -cell.x),
                2 => new MyVector2Int(-cell.x, -cell.y),
                3 => new MyVector2Int(-cell.y, cell.x),
                _ => cell,
            };
        }

        private static bool TryPack(
            List<HerbCandidate> chosen,
            TbCraftingItemCfg furnace,
            TbDrugRecipeCfg recipe,
            Dictionary<HerbCandidate, List<PlacementPose>> sharedPoseCache,
            out List<AlchemyPlacement> placements,
            out AlchemyRuleOutcome ruleOutcome,
            CancellationToken cancellationToken)
        {
            placements = new List<AlchemyPlacement>();
            ruleOutcome = new AlchemyRuleOutcome();
            foreach (var candidate in chosen.Distinct())
            {
                if (!sharedPoseCache.ContainsKey(candidate))
                    sharedPoseCache[candidate] = BuildPoses(candidate, furnace, recipe);
            }
            var poseCache = chosen.Distinct().ToDictionary(candidate => candidate, candidate => sharedPoseCache[candidate]);
            if (poseCache.Any(pair => pair.Value.Count == 0)) return false;
            var pieces = chosen
                .OrderBy(candidate => poseCache[candidate].Count)
                .ThenByDescending(candidate => candidate.CellCount)
                .ThenBy(candidate => candidate.Stock.ItemId.sedId)
                .ThenBy(candidate => candidate.Side)
                .ToList();
            var occupiedLow = new ulong[3];
            var occupiedHigh = new ulong[3];
            var packed = new List<PlacementPose>();
            var lastPoseByCandidate = new Dictionary<HerbCandidate, int>();
            var seenCompleteLayouts = new HashSet<string>();
            var bestPlacements = new List<AlchemyPlacement>();
            AlchemyRuleOutcome bestOutcome = null;
            var nodes = 0;
            var hasRules = recipe.StateIds != null && recipe.StateIds.Count > 0;

            List<AlchemyPlacement> MakePlacements()
            {
                var result = new List<AlchemyPlacement>();
                foreach (var item in packed)
                {
                    var size = item.Candidate.Side == 1 ? furnace.yangGridSize : furnace.yinGridSize;
                    result.Add(new AlchemyPlacement
                    {
                        ItemId = item.Candidate.Stock.ItemId,
                        PoolType = item.Candidate.Side,
                        Rotation = item.Shape.Rotation,
                        Position = new MyVector2Int(
                            (1 - size.x) / 2 + item.X - item.Shape.MinX,
                            (1 - size.y) / 2 + item.Y - item.Shape.MinY),
                    });
                }
                return result;
            }

            bool PackAt(int pieceIndex)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (nodes++ >= PackingNodeLimit) return true;
                if (pieceIndex >= pieces.Count)
                {
                    var candidatePlacements = MakePlacements();
                    var layoutKey = string.Join(";", candidatePlacements
                        .OrderBy(item => item.ItemId.sedId).ThenBy(item => item.PoolType)
                        .ThenBy(item => item.Position.x).ThenBy(item => item.Position.y)
                        .Select(item => $"{item.ItemId.sedId}:{item.PoolType}:{item.Position.x},{item.Position.y}:{item.Rotation}"));
                    if (!seenCompleteLayouts.Add(layoutKey)) return false;
                    var candidateOutcome = AlchemyRuleEvaluator.Evaluate(recipe, candidatePlacements, furnace);
                    if (bestOutcome == null || candidateOutcome.Score > bestOutcome.Score)
                    {
                        bestOutcome = candidateOutcome;
                        bestPlacements = candidatePlacements;
                    }
                    return !hasRules;
                }
                var piece = pieces[pieceIndex];
                var poses = poseCache[piece];
                var startPose = lastPoseByCandidate.TryGetValue(piece, out var previousPose) ? previousPose + 1 : 0;
                for (var poseIndex = startPose; poseIndex < poses.Count; poseIndex++)
                {
                    var pose = poses[poseIndex];
                    if ((occupiedLow[piece.Side] & pose.LowMask) != 0 ||
                        (occupiedHigh[piece.Side] & pose.HighMask) != 0) continue;
                    occupiedLow[piece.Side] |= pose.LowMask;
                    occupiedHigh[piece.Side] |= pose.HighMask;
                    packed.Add(pose);
                    var hadPrevious = lastPoseByCandidate.TryGetValue(piece, out previousPose);
                    lastPoseByCandidate[piece] = poseIndex;
                    if (PackAt(pieceIndex + 1) && (!hasRules || nodes >= PackingNodeLimit)) return true;
                    if (hadPrevious) lastPoseByCandidate[piece] = previousPose;
                    else lastPoseByCandidate.Remove(piece);
                    packed.RemoveAt(packed.Count - 1);
                    occupiedLow[piece.Side] ^= pose.LowMask;
                    occupiedHigh[piece.Side] ^= pose.HighMask;
                }
                return false;
            }

            PackAt(0);
            if (bestOutcome == null) return false;
            placements = bestPlacements;
            ruleOutcome = bestOutcome;
            return true;
        }

        private static List<PlacementPose> BuildPoses(
            HerbCandidate candidate,
            TbCraftingItemCfg furnace,
            TbDrugRecipeCfg recipe)
        {
            var size = candidate.Side == 1 ? furnace.yangGridSize : furnace.yinGridSize;
            var poses = new List<PlacementPose>();
            var seenMasks = new HashSet<string>();
            foreach (var shape in candidate.Rotations)
            for (var y = 0; y <= size.y - shape.Height; y++)
            for (var x = 0; x <= size.x - shape.Width; x++)
            {
                ulong low = 0;
                ulong high = 0;
                foreach (var cell in shape.Normalized)
                {
                    var bit = (y + cell.y) * size.x + x + cell.x;
                    if (bit < 64) low |= 1UL << bit;
                    else high |= 1UL << (bit - 64);
                }
                var key = low + ":" + high;
                if (!seenMasks.Add(key)) continue;
                poses.Add(new PlacementPose
                {
                    Candidate = candidate,
                    Shape = shape,
                    X = x,
                    Y = y,
                    LowMask = low,
                    HighMask = high,
                    RuleHint = CalculatePoseRuleHint(candidate, shape, x, y, size, recipe),
                });
            }
            return poses.OrderByDescending(pose => pose.RuleHint).ThenBy(pose => pose.Y).ThenBy(pose => pose.X).ToList();
        }

        private static int CalculatePoseRuleHint(
            HerbCandidate candidate,
            RotationShape shape,
            int x,
            int y,
            MyVector2Int size,
            TbDrugRecipeCfg recipe)
        {
            var score = 0;
            foreach (var stateId in recipe.StateIds)
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                if (state == null || (state.poolType != 0 && state.poolType != candidate.Side)) continue;
                if (!TargetMayMatch(candidate.Stock.ItemCfg, state.target1)) continue;
                var areaCodes = state.area.Split('&').SelectMany(stage => stage.Split('|'))
                    .Select(raw => int.TryParse(raw, out var code) ? code : -1).ToList();
                var minX = x;
                var maxX = x + shape.Width - 1;
                var minY = y;
                var maxY = y + shape.Height - 1;
                if (areaCodes.Contains(1)) score += maxY * 20;
                if (areaCodes.Contains(2)) score += (size.y - 1 - minY) * 20;
                if (areaCodes.Contains(3)) score += (size.x - 1 - minX) * 20;
                if (areaCodes.Contains(4)) score += maxX * 20;
                if (areaCodes.Contains(5)) score -= Math.Abs((minX + maxX) - (size.x - 1)) * 10 +
                                                   Math.Abs((minY + maxY) - (size.y - 1)) * 10;
                if (state.relation >= 10 && state.relation <= 16) score += 8;
            }
            return score;
        }

        private static bool TargetMayMatch(TbItemCfg item, string expression)
        {
            if (string.IsNullOrEmpty(expression)) return true;
            var grade = Singleton<TbDataImpl>.Instance.GetGradeCfg(item.gradeId);
            foreach (var option in expression.Split('|'))
            {
                var all = true;
                foreach (var raw in option.Split('&'))
                {
                    if (!int.TryParse(raw, out var condition)) continue;
                    if (condition == 0) { all = false; break; }
                    if (condition >= 1 && condition <= 8 && item.attribute != condition) { all = false; break; }
                    if (condition >= 11 && condition <= 14 && (grade.weight + 2) / 3 < condition - 10) { all = false; break; }
                    if (condition >= 21 && condition <= 32 && grade.weight < condition - 20) { all = false; break; }
                    if (condition >= 41 && condition <= 44 && (grade.weight + 2) / 3 > condition - 40) { all = false; break; }
                    if (condition >= 51 && condition <= 62 && grade.weight > condition - 50) { all = false; break; }
                }
                if (all) return true;
            }
            return false;
        }
    }
}
