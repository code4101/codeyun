using System;
using System.Collections.Generic;
using System.Diagnostics;
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
        // 1=基础解，2=丹方规则驱动的迭代解，3=当前背包可行解。
        internal int SearchStage;
        internal int GlobalCountBonus;
        internal int GlobalQualityBonus;
        internal AlchemyRuleOutcome RuleOutcome = new AlchemyRuleOutcome();

        internal int TotalCountBonus => GlobalCountBonus + RuleOutcome.CountBonus;
        internal int TotalQualityBonus => GlobalQualityBonus + RuleOutcome.QualityBonus;
        internal int QualityRank => Math.Max(1, Math.Min(3, 1 + TotalQualityBonus));
        internal double PlantingDaysPerPill =>
            (double)PlantingDays / Math.Max(1, BasePillCount + TotalCountBonus);

        internal TbItemCfg ResolveOutputItem(TbDrugRecipeCfg recipe)
        {
            if (recipe == null) return null;
            var quality = QualityRank;
            var data = Singleton<TbDataImpl>.Instance;
            return data.itemCfgList
                       .Where(item => item.id == recipe.itemId || item.groupId == recipe.itemId)
                       .Where(item => item.drugQuality <= quality)
                       .OrderByDescending(item => item.drugQuality)
                       .FirstOrDefault() ?? data.GetItemCfg(recipe.itemId);
        }

        internal long MaterialValue()
        {
            var data = Singleton<TbDataImpl>.Instance;
            return ItemCounts.Sum(pair =>
                (long)(data.GetItemCfg(pair.Key)?.price ?? 0) * pair.Value);
        }

        internal int CraftingDays(
            TbDrugRecipeCfg recipe,
            int globalDayBonus,
            float globalDayMultiplier)
        {
            if (recipe == null) return 0;
            var day = Math.Max(0, recipe.GetCostDay + globalDayBonus + RuleOutcome.DayBonus);
            var multiplier = globalDayMultiplier + RuleOutcome.DayMultiplierBonus / 100f;
            return Math.Max(0, (int)Math.Round(day * multiplier, MidpointRounding.AwayFromZero));
        }

        internal bool IsAvailable(IReadOnlyDictionary<int, long> inventory)
        {
            return ItemCounts.All(pair => inventory != null &&
                inventory.TryGetValue(pair.Key, out var count) && count >= pair.Value);
        }

        internal TbCraftingTemplateSto ToTemplate(TbDrugRecipeCfg recipe, int index)
        {
            var output = ResolveOutputItem(recipe);
            var bonuses = new List<string>();
            if (TotalQualityBonus != 0) bonuses.Add($"品质{TotalQualityBonus:+#;-#;0}");
            if (TotalCountBonus != 0) bonuses.Add($"成丹{TotalCountBonus:+#;-#;0}");
            var kind = SearchStage == 1 ? "基础解" : SearchStage == 2 ? "迭代解" : "背包解";
            return new TbCraftingTemplateSto
            {
                id = -100000 - index,
                type = 0,
                isFollow = false,
                name = $"{kind} · {recipe.GetName}" +
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
        private const int ElementOptionLimit = 32;
        private const int CompositionLimit = 160;
        private const int ElementSearchNodeLimit = 80000;
        private const int CompositionNodeLimit = 50000;
        private const int PackingNodeLimit = 30000;
        private const int FastElementSearchNodeLimit = 2048;
        private const int ParetoAllocationStateLimit = 50000;
        private const int ElementParetoOptionLimit = 24;
        private const int JointCompositionBeamLimit = 96;
        private const int JointCompositionPackingLimit = 32;
        private static readonly int[] PlantingDaysByGrade =
            { 10, 20, 30, 360, 720, 1080, 3600, 7200, 10800, 36000, 72000, 108000 };

        private sealed class HerbCandidate
        {
            internal SmartAlchemyUi.HerbStock Stock;
            internal TbCraftingItemCfg Crafting;
            internal TbDrawCfg Draw;
            internal string ElementKey;
            internal int Contribution;
            internal int Side;
            internal int GradeWeight;
            internal int PlantingDays;
            internal List<RotationShape> Rotations;
            internal int CellCount;
        }

        private sealed class RotationShape
        {
            internal int Rotation;
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

        private sealed class Composition
        {
            internal List<HerbCandidate> Pieces = new List<HerbCandidate>();
            internal int YangCells;
            internal int YinCells;

            internal string Key => string.Join(";", Pieces
                .GroupBy(item => $"{item.Stock.ItemId.sedId}:{item.Side}")
                .OrderBy(group => group.Key)
                .Select(group => $"{group.Key}:{group.Count()}"));

            internal int MaxGrade => Pieces.Select(item => item.GradeWeight).DefaultIfEmpty(0).Max();
            internal int GradeSum => Pieces.Sum(item => item.GradeWeight);
            internal int PlantingDays => Pieces.Sum(item => item.PlantingDays);
        }

        private sealed class RuleSearchResult
        {
            internal AlchemySolution Solution;
            internal Composition Composition;
        }

        private sealed class ElementAllocationState
        {
            internal List<HerbCandidate> Pieces = new List<HerbCandidate>();
            internal int Contribution;
            internal int YangCells;
            internal int YinCells;
            internal int NextCandidateIndex;
        }

        internal static List<AlchemySolution> SolveStatic(
            TbDrugRecipeCfg recipe,
            TbPackSto furnace,
            int globalCountBonus,
            int globalQualityBonus,
            IReadOnlyList<SmartAlchemyUi.HerbStock> catalog,
            CancellationToken cancellationToken = default,
            Action<AlchemySolution> onSolution = null)
        {
            var result = new List<AlchemySolution>();
            if (recipe == null || furnace == null || catalog == null) return result;
            var data = Singleton<TbDataImpl>.Instance;
            var furnaceCfg = data.GetCraftingItemCfg(furnace.itemId.sedId);
            if (furnaceCfg == null) return result;

            var targets = recipe.AttrLimitDic
                .Where(pair => pair.Value != 0)
                .ToDictionary(pair => pair.Key, pair => pair.Value);
            if (targets.Count == 0) return result;
            var monotoneCandidates = BuildMonotoneCandidates(catalog, targets, furnaceCfg).ToList();
            if (monotoneCandidates.Count == 0) return result;

            var baseStopwatch = Stopwatch.StartNew();
            var baseResult = FindCanonicalSolution(recipe, furnaceCfg, monotoneCandidates,
                targets, null, globalCountBonus, globalQualityBonus, cancellationToken);
            baseStopwatch.Stop();
            var baseSolution = baseResult?.Solution;
            if (baseSolution != null)
            {
                baseSolution.SearchStage = 1;
                onSolution?.Invoke(baseSolution);
            }
            if (baseSolution == null) return result;
            result.Add(baseSolution);

            AlchemySolution optimizedSolution = null;
            var iterativeStopwatch = Stopwatch.StartNew();
            if (recipe.StateIds != null && recipe.StateIds.Count > 0)
            {
                var baseComposition = baseResult.Composition;
                if (baseComposition != null)
                {
                    optimizedSolution = FindRuleOptimizedSolution(recipe, furnaceCfg,
                        monotoneCandidates, targets, baseSolution, baseComposition,
                        globalCountBonus, globalQualityBonus, cancellationToken)?.Solution;
                    if (optimizedSolution != null)
                    {
                        optimizedSolution.SearchStage = 2;
                        onSolution?.Invoke(optimizedSolution);
                    }
                }
            }
            iterativeStopwatch.Stop();
            TiandaoPlugin.LogAlchemy($"alchemy static recipe={recipe.id}, base={baseStopwatch.ElapsedMilliseconds}ms, " +
                                     $"iterative={iterativeStopwatch.ElapsedMilliseconds}ms, " +
                                     $"baseFound={baseSolution != null}, iterativeFound={optimizedSolution != null}");
            if (optimizedSolution != null) result.Add(optimizedSolution);
            return result.OrderBy(solution => solution.SearchStage).Take(2).ToList();
        }

        internal static AlchemySolution SolveBackpack(
            TbDrugRecipeCfg recipe,
            TbPackSto furnace,
            int globalCountBonus,
            int globalQualityBonus,
            IReadOnlyList<SmartAlchemyUi.HerbStock> catalog,
            IReadOnlyDictionary<int, long> inventory,
            AlchemySolution idealSolution,
            CancellationToken cancellationToken = default,
            Action<AlchemySolution> onSolution = null)
        {
            if (recipe == null || furnace == null || catalog == null || idealSolution == null) return null;
            var data = Singleton<TbDataImpl>.Instance;
            var furnaceCfg = data.GetCraftingItemCfg(furnace.itemId.sedId);
            if (furnaceCfg == null) return null;
            var targets = recipe.AttrLimitDic
                .Where(pair => pair.Value != 0)
                .ToDictionary(pair => pair.Key, pair => pair.Value);
            if (targets.Count == 0) return null;
            var monotoneCandidates = BuildMonotoneCandidates(catalog, targets, furnaceCfg).ToList();
            if (monotoneCandidates.Count == 0) return null;

            var repaired = FindInventoryRepairSolution(recipe, furnaceCfg, monotoneCandidates,
                targets, inventory, idealSolution, globalCountBonus, globalQualityBonus,
                cancellationToken, candidate =>
                {
                    candidate.SearchStage = 3;
                    onSolution?.Invoke(candidate);
                });
            if (repaired?.Solution == null) return null;
            repaired.Solution.SearchStage = 3;
            return repaired.Solution;
        }

        private static RuleSearchResult FindCanonicalSolution(
            TbDrugRecipeCfg recipe,
            TbCraftingItemCfg furnace,
            IReadOnlyList<HerbCandidate> candidates,
            IReadOnlyDictionary<string, int> targets,
            IReadOnlyDictionary<int, long> inventory,
            int globalCountBonus,
            int globalQualityBonus,
            CancellationToken cancellationToken)
        {
            var distinctGrades = candidates.Select(item => item.GradeWeight).Distinct().OrderBy(value => value);
            foreach (var maximumGrade in distinctGrades)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var gradeCandidates = candidates
                    .Where(item => item.GradeWeight <= maximumGrade).ToList();
                var coveredElements = new HashSet<string>(
                    gradeCandidates.Select(item => item.ElementKey));
                // 例如普通五行一阶即可、只有异灵根冰需要更高档时，在冰候选尚未
                // 出现的品阶直接跳过，不能先为金木等元素展开数万条无意义组合。
                if (targets.Keys.Any(element => !coveredElements.Contains(element))) continue;

                // 常见低阶丹方先走首个严格配平组合。它与完整搜索使用相同的
                // 五行、炉位、容量和真实形状校验，只省略“先枚举所有候选再排序”。
                var fastComposition = BuildFastCanonicalComposition(
                    gradeCandidates, targets, furnace, cancellationToken);
                if (fastComposition != null &&
                    TryPack(fastComposition.Pieces, furnace, recipe, true, globalQualityBonus,
                        out var fastPlacements, out var fastOutcome, cancellationToken))
                {
                    return new RuleSearchResult
                    {
                        Composition = fastComposition,
                        Solution = CreateSolution(recipe, fastComposition, fastPlacements, fastOutcome,
                            globalCountBonus, globalQualityBonus),
                    };
                }

                var compositions = GenerateExactCompositions(gradeCandidates,
                    targets, furnace, inventory, cancellationToken);
                foreach (var composition in compositions)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    if (!TryPack(composition.Pieces, furnace, recipe, true, globalQualityBonus,
                            out var placements, out var outcome, cancellationToken)) continue;
                    return new RuleSearchResult
                    {
                        Composition = composition,
                        Solution = CreateSolution(recipe, composition, placements, outcome,
                            globalCountBonus, globalQualityBonus),
                    };
                }
            }
            return null;
        }

        private static Composition BuildFastCanonicalComposition(
            IReadOnlyList<HerbCandidate> candidates,
            IReadOnlyDictionary<string, int> targets,
            TbCraftingItemCfg furnace,
            CancellationToken cancellationToken)
        {
            var composition = new Composition();
            foreach (var target in targets.OrderBy(pair => pair.Key))
            {
                var elementCandidates = candidates
                    .Where(item => item.ElementKey == target.Key)
                    .OrderBy(item => item.GradeWeight)
                    .ThenBy(item => item.CellCount)
                    .ThenByDescending(item => item.Contribution)
                    .ThenBy(item => item.Stock.ItemId.sedId)
                    .ThenBy(item => item.Side)
                    .ToList();
                var option = FindFirstExactElementOption(
                    elementCandidates, Math.Abs(target.Value), furnace, cancellationToken);
                if (option == null) return null;
                composition.Pieces.AddRange(option.Pieces);
                composition.YangCells += option.YangCells;
                composition.YinCells += option.YinCells;
                if (composition.YangCells > furnace.yangGridSize.x * furnace.yangGridSize.y ||
                    composition.YinCells > furnace.yinGridSize.x * furnace.yinGridSize.y)
                    return null;
            }
            return composition;
        }

        private static Composition FindFirstExactElementOption(
            IReadOnlyList<HerbCandidate> candidates,
            int target,
            TbCraftingItemCfg furnace,
            CancellationToken cancellationToken)
        {
            var pieces = new List<HerbCandidate>();
            var yangCapacity = furnace.yangGridSize.x * furnace.yangGridSize.y;
            var yinCapacity = furnace.yinGridSize.x * furnace.yinGridSize.y;
            var nodes = 0;
            Composition found = null;

            bool Search(int start, int remaining, int usedYang, int usedYin)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (nodes++ >= FastElementSearchNodeLimit) return false;
                if (remaining == 0)
                {
                    found = new Composition
                    {
                        Pieces = pieces.ToList(),
                        YangCells = usedYang,
                        YinCells = usedYin,
                    };
                    return true;
                }
                for (var index = start; index < candidates.Count; index++)
                {
                    var candidate = candidates[index];
                    if (candidate.Contribution > remaining) continue;
                    var cellCapacity = candidate.Side == 1
                        ? (yangCapacity - usedYang) / candidate.CellCount
                        : (yinCapacity - usedYin) / candidate.CellCount;
                    var maximum = Math.Min(remaining / candidate.Contribution, cellCapacity);
                    for (var count = maximum; count >= 1; count--)
                    {
                        for (var copy = 0; copy < count; copy++) pieces.Add(candidate);
                        var solved = Search(index + 1,
                            remaining - candidate.Contribution * count,
                            usedYang + (candidate.Side == 1 ? candidate.CellCount * count : 0),
                            usedYin + (candidate.Side == 2 ? candidate.CellCount * count : 0));
                        pieces.RemoveRange(pieces.Count - count, count);
                        if (solved) return true;
                    }
                }
                return false;
            }

            Search(0, target, 0, 0);
            return found;
        }

        private static RuleSearchResult FindRuleOptimizedSolution(
            TbDrugRecipeCfg recipe,
            TbCraftingItemCfg furnace,
            IReadOnlyList<HerbCandidate> monotoneCandidates,
            IReadOnlyDictionary<string, int> targets,
            AlchemySolution baseline,
            Composition baselineComposition,
            int globalCountBonus,
            int globalQualityBonus,
            CancellationToken cancellationToken)
        {
            var qualityOnlyRules = HasOnlyQualityRuleEffects(recipe);
            if (qualityOnlyRules && baseline.QualityRank >= 3) return null;
            var searchWatch = Stopwatch.StartNew();
            var proposals = BuildJointRuleCompositions(monotoneCandidates, targets, furnace,
                    recipe, baselineComposition, cancellationToken)
                .Where(composition => composition.Key != baselineComposition.Key)
                .OrderByDescending(composition => EstimateCompositionRuleBenefit(
                    composition, furnace, recipe, globalQualityBonus))
                .ThenBy(composition => composition,
                    Comparer<Composition>.Create(CompareGradeProfiles))
                .ThenBy(composition => composition.Pieces.Count)
                .ThenBy(composition => composition.PlantingDays)
                .Take(JointCompositionPackingLimit)
                .ToList();

            RuleSearchResult best = null;
            var incumbent = baseline;
            var packedCount = 0;
            foreach (var composition in proposals)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (!TryPack(composition.Pieces, furnace, recipe, true, globalQualityBonus,
                        out var placements, out var outcome, cancellationToken)) continue;
                packedCount++;
                var solution = CreateSolution(recipe, composition, placements, outcome,
                    globalCountBonus, globalQualityBonus);
                if (!IsBetterRuleSolution(solution, incumbent)) continue;
                best = new RuleSearchResult { Solution = solution, Composition = composition };
                incumbent = solution;
                if (qualityOnlyRules && incumbent.QualityRank >= 3) break;
            }
            searchWatch.Stop();
            TiandaoPlugin.LogAlchemy($"alchemy joint iterative recipe={recipe.id}, " +
                                     $"proposals={proposals.Count}, packed={packedCount}, " +
                                     $"elapsed={searchWatch.ElapsedMilliseconds}ms, improved={best != null}");
            return best;
        }

        private static List<Composition> BuildJointRuleCompositions(
            IReadOnlyList<HerbCandidate> candidates,
            IReadOnlyDictionary<string, int> targets,
            TbCraftingItemCfg furnace,
            TbDrugRecipeCfg recipe,
            Composition baseline,
            CancellationToken cancellationToken)
        {
            var optionSets = new List<List<Composition>>();
            var yangCapacity = furnace.yangGridSize.x * furnace.yangGridSize.y;
            var yinCapacity = furnace.yinGridSize.x * furnace.yinGridSize.y;
            foreach (var target in targets.OrderBy(pair => pair.Key))
            {
                cancellationToken.ThrowIfCancellationRequested();
                var baselineOption = CreateComposition(baseline.Pieces
                    .Where(piece => piece.ElementKey == target.Key).ToList());
                var allocations = SolveParetoElementAllocations(
                    candidates.Where(candidate => candidate.ElementKey == target.Key).ToList(),
                    Math.Abs(target.Value), yangCapacity, yinCapacity, recipe, cancellationToken);
                var options = allocations
                    .Select(allocation => CreateComposition(allocation.Pieces))
                    .Concat(new[] { baselineOption })
                    .GroupBy(composition => composition.Key)
                    .Select(group => group.First())
                    .OrderByDescending(composition => EstimateCompositionRuleBenefit(
                        composition, furnace, recipe, 0))
                    .ThenBy(composition => composition,
                        Comparer<Composition>.Create(CompareGradeProfiles))
                    .ThenBy(composition => composition.Pieces.Count)
                    .ThenBy(composition => composition.PlantingDays)
                    .Take(ElementParetoOptionLimit)
                    .ToList();
                if (options.All(option => option.Key != baselineOption.Key))
                {
                    if (options.Count >= ElementParetoOptionLimit) options.RemoveAt(options.Count - 1);
                    options.Add(baselineOption);
                }
                if (options.Count == 0) return new List<Composition>();
                optionSets.Add(options);
            }

            // 元素内只保留整数配平的 Pareto 候选；随后一次性联合所有元素。
            // 因此同行、相邻、双目标等规则可以由多个元素同时改变，不再依赖逐元素爬山。
            var beam = new List<Composition> { new Composition() };
            var baselinePrefix = new Composition();
            for (var optionSetIndex = 0; optionSetIndex < optionSets.Count; optionSetIndex++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var options = optionSets[optionSetIndex];
                var elementKey = targets.OrderBy(pair => pair.Key).ElementAt(optionSetIndex).Key;
                baselinePrefix = CreateComposition(baselinePrefix.Pieces.Concat(
                    baseline.Pieces.Where(piece => piece.ElementKey == elementKey)).ToList());
                var next = new Dictionary<string, Composition>();
                foreach (var current in beam)
                foreach (var option in options)
                {
                    var nextYang = current.YangCells + option.YangCells;
                    var nextYin = current.YinCells + option.YinCells;
                    if (nextYang > yangCapacity || nextYin > yinCapacity) continue;
                    var composition = CreateComposition(current.Pieces.Concat(option.Pieces).ToList());
                    next[composition.Key] = composition;
                }
                beam = next.Values
                    .OrderByDescending(composition => EstimateCompositionRuleBenefit(
                        composition, furnace, recipe, 0))
                    .ThenBy(composition => composition,
                        Comparer<Composition>.Create(CompareGradeProfiles))
                    .ThenBy(composition => composition.Pieces.Count)
                    .ThenBy(composition => composition.PlantingDays)
                    .Take(JointCompositionBeamLimit)
                    .ToList();
                if (beam.All(composition => composition.Key != baselinePrefix.Key))
                {
                    if (beam.Count >= JointCompositionBeamLimit) beam.RemoveAt(beam.Count - 1);
                    beam.Add(CloneComposition(baselinePrefix));
                }
                if (beam.Count == 0) break;
            }
            return beam;
        }

        private static List<ElementAllocationState> SolveParetoElementAllocations(
            IReadOnlyList<HerbCandidate> candidates,
            int targetContribution,
            int yangCapacity,
            int yinCapacity,
            TbDrugRecipeCfg recipe,
            CancellationToken cancellationToken)
        {
            var orderedCandidates = candidates
                .Where(candidate => candidate.Contribution <= targetContribution)
                .OrderByDescending(candidate => CalculateCandidateRulePriority(candidate, recipe))
                .ThenBy(candidate => candidate.GradeWeight)
                .ThenBy(candidate => candidate.CellCount)
                .ThenBy(candidate => candidate.Stock.ItemId.sedId)
                .ThenBy(candidate => candidate.Side)
                .ToList();
            var ruleFeatureKeys = orderedCandidates.ToDictionary(candidate => candidate,
                candidate => BuildCandidateRuleFeatureKey(candidate, recipe));
            var initial = new ElementAllocationState();
            var states = new Dictionary<string, ElementAllocationState> { ["0:0:0:"] = initial };
            var pending = new Queue<ElementAllocationState>();
            pending.Enqueue(initial);
            while (pending.Count > 0 && states.Count < ParetoAllocationStateLimit)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var state = pending.Dequeue();
                if (state.Contribution >= targetContribution) continue;
                for (var candidateIndex = state.NextCandidateIndex;
                     candidateIndex < orderedCandidates.Count; candidateIndex++)
                {
                    var candidate = orderedCandidates[candidateIndex];
                    var nextContribution = state.Contribution + candidate.Contribution;
                    if (nextContribution > targetContribution) continue;
                    var nextYang = state.YangCells + (candidate.Side == 1 ? candidate.CellCount : 0);
                    var nextYin = state.YinCells + (candidate.Side == 2 ? candidate.CellCount : 0);
                    if (nextYang > yangCapacity || nextYin > yinCapacity) continue;
                    var next = new ElementAllocationState
                    {
                        Pieces = state.Pieces.Concat(new[] { candidate }).ToList(),
                        Contribution = nextContribution,
                        YangCells = nextYang,
                        YinCells = nextYin,
                        NextCandidateIndex = candidateIndex,
                    };
                    // 只按规则可观察特征区分状态：属性、炉位、形状以及每条规则的
                    // target1/target2 命中情况。相同特征的具体药材只保留品阶更低的代表，
                    // 避免按 itemId 展开成数万种语义完全相同的组合。
                    var featureSignature = string.Join(";", next.Pieces
                        .GroupBy(piece => ruleFeatureKeys[piece])
                        .OrderBy(group => group.Key)
                        .Select(group => $"{group.Key}*{group.Count()}"));
                    var key = $"{nextContribution}:{nextYang}:{nextYin}:" +
                              featureSignature;
                    if (states.TryGetValue(key, out var existing) &&
                        !IsBetterParetoRepresentative(next, existing)) continue;
                    states[key] = next;
                    pending.Enqueue(next);
                }
            }
            return states.Values.Where(state => state.Contribution == targetContribution)
                .OrderByDescending(state => EstimateAllocationRulePotential(state, recipe))
                .ThenBy(state => CreateComposition(state.Pieces),
                    Comparer<Composition>.Create(CompareGradeProfiles))
                .ThenBy(state => state.Pieces.Count)
                .ThenBy(state => state.Pieces.Sum(piece => piece.PlantingDays))
                .Take(ElementParetoOptionLimit * 2)
                .ToList();
        }

        private static long EstimateAllocationRulePotential(
            ElementAllocationState state,
            TbDrugRecipeCfg recipe) => state.Pieces.Sum(candidate =>
                (long)CalculateCandidateRulePriority(candidate, recipe) * 1000L +
                Math.Max(0, 20 - candidate.CellCount));

        private static string BuildCandidateRuleFeatureKey(
            HerbCandidate candidate,
            TbDrugRecipeCfg recipe)
        {
            var shapeKey = string.Join("/", candidate.Rotations
                .Select(rotation => rotation.Width + "x" + rotation.Height + ":" +
                    string.Join(",", rotation.Normalized.Select(cell => cell.x + "." + cell.y)))
                .OrderBy(value => value));
            var ruleBits = new List<string>();
            foreach (var stateId in recipe?.StateIds ?? new List<int>())
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                if (state == null)
                {
                    ruleBits.Add("0");
                    continue;
                }
                var activePool = state.poolType == 0 || state.poolType == candidate.Side;
                var first = activePool && TargetMayMatch(candidate.Stock.ItemCfg, state.target1);
                var second = activePool && !string.IsNullOrEmpty(state.target2) &&
                             TargetMayMatch(candidate.Stock.ItemCfg, state.target2);
                ruleBits.Add((first ? "1" : "0") + (second ? "1" : "0"));
            }
            return candidate.Side + ":" + candidate.Contribution + ":" + candidate.CellCount +
                   ":" + shapeKey + ":" + string.Join("", ruleBits);
        }

        private static bool IsBetterParetoRepresentative(
            ElementAllocationState left,
            ElementAllocationState right)
        {
            var gradeComparison = CompareGradeProfiles(
                CreateComposition(left.Pieces), CreateComposition(right.Pieces));
            if (gradeComparison != 0) return gradeComparison < 0;
            var leftDays = left.Pieces.Sum(piece => piece.PlantingDays);
            var rightDays = right.Pieces.Sum(piece => piece.PlantingDays);
            if (leftDays != rightDays) return leftDays < rightDays;
            return string.CompareOrdinal(CreateComposition(left.Pieces).Key,
                       CreateComposition(right.Pieces).Key) < 0;
        }

        private static long EstimateCompositionRuleBenefit(
            Composition composition,
            TbCraftingItemCfg furnace,
            TbDrugRecipeCfg recipe,
            int globalQualityBonus)
        {
            if (TryCalculateIndependentRuleUpperBound(
                    composition.Pieces, furnace, recipe, out var upperBound))
                return CalculateEffectiveRuleBenefit(upperBound, globalQualityBonus);
            return composition.Pieces.Sum(candidate =>
                (long)CalculateCandidateRulePriority(candidate, recipe));
        }

        private static Composition CreateComposition(IReadOnlyList<HerbCandidate> pieces)
        {
            var composition = new Composition { Pieces = pieces.ToList() };
            foreach (var piece in pieces)
            {
                if (piece.Side == 1) composition.YangCells += piece.CellCount;
                else composition.YinCells += piece.CellCount;
            }
            return composition;
        }

        private static bool HasOnlyQualityRuleEffects(TbDrugRecipeCfg recipe)
        {
            var found = false;
            foreach (var stateId in recipe?.StateIds ?? new List<int>())
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                if (state == null || string.IsNullOrEmpty(state.baseEff)) return false;
                var effect = GenericMethods.GetEffect(state.baseEff);
                if (effect == null || effect.functionType != EventEnum.UpdateCraftingDrugAttr.ToString() ||
                    effect.parameter.Count < 1 || !int.TryParse(effect.parameter[0], out var type)) return false;
                if (type != 4) return false;
                found = true;
            }
            return found;
        }

        private static RuleSearchResult FindInventoryRepairSolution(
            TbDrugRecipeCfg recipe,
            TbCraftingItemCfg furnace,
            IReadOnlyList<HerbCandidate> candidates,
            IReadOnlyDictionary<string, int> targets,
            IReadOnlyDictionary<int, long> inventory,
            AlchemySolution ideal,
            int globalCountBonus,
            int globalQualityBonus,
            CancellationToken cancellationToken,
            Action<AlchemySolution> onImproved)
        {
            RuleSearchResult best = null;
            var preferred = ideal.Placements
                .GroupBy(item => $"{item.ItemId.sedId}:{item.PoolType}")
                .ToDictionary(group => group.Key, group => group.Count());
            var compositions = GenerateExactCompositions(candidates, targets, furnace, inventory,
                cancellationToken, preferred);
            foreach (var composition in compositions)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (!TryPack(composition.Pieces, furnace, recipe, true, globalQualityBonus,
                        out var placements, out var outcome, cancellationToken)) continue;
                var solution = CreateSolution(recipe, composition, placements, outcome,
                    globalCountBonus, globalQualityBonus);
                if (best == null || IsBetterRepairSolution(solution, best.Solution, ideal))
                {
                    best = new RuleSearchResult { Solution = solution, Composition = composition };
                    onImproved?.Invoke(solution);
                }
            }
            return best;
        }

        private static List<Composition> GenerateExactCompositions(
            IReadOnlyList<HerbCandidate> candidates,
            IReadOnlyDictionary<string, int> targets,
            TbCraftingItemCfg furnace,
            IReadOnlyDictionary<int, long> inventory,
            CancellationToken cancellationToken,
            IReadOnlyDictionary<string, int> preferred = null)
        {
            var optionSets = new List<List<Composition>>();
            foreach (var target in targets.OrderBy(pair => pair.Key))
            {
                var elementCandidates = candidates
                    .Where(item => item.ElementKey == target.Key)
                    .OrderBy(item => item.GradeWeight)
                    .ThenBy(item => item.CellCount)
                    .ThenByDescending(item => item.Contribution)
                    .ThenBy(item => item.Stock.ItemId.sedId)
                    .ThenBy(item => item.Side)
                    .ToList();
                var options = EnumerateElementOptions(elementCandidates, Math.Abs(target.Value),
                    furnace, inventory, cancellationToken, preferred);
                if (options.Count == 0) return new List<Composition>();
                optionSets.Add(options);
            }

            var combined = new List<Composition>();
            var nodes = 0;
            var yangCapacity = furnace.yangGridSize.x * furnace.yangGridSize.y;
            var yinCapacity = furnace.yinGridSize.x * furnace.yinGridSize.y;

            void Combine(int depth, Composition current)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (nodes++ >= CompositionNodeLimit) return;
                if (depth >= optionSets.Count)
                {
                    combined.Add(CloneComposition(current));
                    return;
                }
                foreach (var option in optionSets[depth])
                {
                    var nextYang = current.YangCells + option.YangCells;
                    var nextYin = current.YinCells + option.YinCells;
                    if (nextYang > yangCapacity || nextYin > yinCapacity) continue;
                    var oldCount = current.Pieces.Count;
                    current.Pieces.AddRange(option.Pieces);
                    current.YangCells = nextYang;
                    current.YinCells = nextYin;
                    if (FitsInventory(current, inventory)) Combine(depth + 1, current);
                    current.Pieces.RemoveRange(oldCount, current.Pieces.Count - oldCount);
                    current.YangCells -= option.YangCells;
                    current.YinCells -= option.YinCells;
                    if (combined.Count >= CompositionLimit || nodes >= CompositionNodeLimit) break;
                }
            }

            Combine(0, new Composition());
            return OrderCompositions(combined, preferred).Take(CompositionLimit).ToList();
        }

        private static List<Composition> EnumerateElementOptions(
            IReadOnlyList<HerbCandidate> candidates,
            int target,
            TbCraftingItemCfg furnace,
            IReadOnlyDictionary<int, long> inventory,
            CancellationToken cancellationToken,
            IReadOnlyDictionary<string, int> preferred = null)
        {
            var results = new Dictionary<string, Composition>();
            var pieces = new List<HerbCandidate>();
            var yangCapacity = furnace.yangGridSize.x * furnace.yangGridSize.y;
            var yinCapacity = furnace.yinGridSize.x * furnace.yinGridSize.y;
            var nodes = 0;

            void Search(int start, int remaining, int usedYang, int usedYin)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (nodes++ >= ElementSearchNodeLimit) return;
                if (remaining == 0)
                {
                    var composition = new Composition
                    {
                        Pieces = pieces.ToList(),
                        YangCells = usedYang,
                        YinCells = usedYin,
                    };
                    if (!results.ContainsKey(composition.Key)) results[composition.Key] = composition;
                    if (results.Count > ElementOptionLimit * 8)
                    {
                        var retained = OrderCompositions(results.Values, preferred)
                            .Take(ElementOptionLimit * 4).ToList();
                        results.Clear();
                        foreach (var item in retained) results[item.Key] = item;
                    }
                    return;
                }
                if (start >= candidates.Count) return;

                var remainingYang = yangCapacity - usedYang;
                var remainingYin = yinCapacity - usedYin;
                var bestYangRatio = candidates.Skip(start).Where(item => item.Side == 1)
                    .Select(item => (double)item.Contribution / item.CellCount).DefaultIfEmpty(0).Max();
                var bestYinRatio = candidates.Skip(start).Where(item => item.Side == 2)
                    .Select(item => (double)item.Contribution / item.CellCount).DefaultIfEmpty(0).Max();
                if (remaining > remainingYang * bestYangRatio + remainingYin * bestYinRatio + 0.0001) return;

                for (var index = start; index < candidates.Count; index++)
                {
                    var candidate = candidates[index];
                    if (candidate.Contribution > remaining) continue;
                    var cellCapacity = candidate.Side == 1
                        ? (yangCapacity - usedYang) / candidate.CellCount
                        : (yinCapacity - usedYin) / candidate.CellCount;
                    var stockCapacity = inventory == null
                        ? int.MaxValue
                        : inventory.TryGetValue(candidate.Stock.ItemId.sedId, out var owned)
                            ? (int)Math.Min(int.MaxValue, owned)
                            : 0;
                    var maximum = Math.Min(remaining / candidate.Contribution,
                        Math.Min(cellCapacity, stockCapacity));
                    // candidates 已按品阶从低到高排列；先尽量多取当前低阶药材，
                    // 避免“少量低阶 + 高贡献高阶”的分支先把有限候选池占满。
                    for (var count = maximum; count >= 1; count--)
                    {
                        for (var copy = 0; copy < count; copy++) pieces.Add(candidate);
                        Search(index + 1,
                            remaining - candidate.Contribution * count,
                            usedYang + (candidate.Side == 1 ? candidate.CellCount * count : 0),
                            usedYin + (candidate.Side == 2 ? candidate.CellCount * count : 0));
                        pieces.RemoveRange(pieces.Count - count, count);
                        if (nodes >= ElementSearchNodeLimit) return;
                    }
                }
            }

            Search(0, target, 0, 0);
            return OrderCompositions(results.Values, preferred).Take(ElementOptionLimit).ToList();
        }

        private static IEnumerable<Composition> OrderCompositions(
            IEnumerable<Composition> compositions,
            IReadOnlyDictionary<string, int> preferred = null)
        {
            var gradeProfileComparer = Comparer<Composition>.Create(CompareGradeProfiles);
            if (preferred != null)
            {
                return compositions
                    .OrderBy(item => CompositionDistance(item, preferred))
                    .ThenBy(item => item, gradeProfileComparer)
                    .ThenBy(item => item.Pieces.Count)
                    .ThenBy(item => item.YangCells + item.YinCells)
                    .ThenBy(item => item.PlantingDays)
                    .ThenBy(item => item.Key);
            }
            return compositions
                // 基础解按品阶分布逐档比较：先减少最高档药材，再减少次高档。
                // 因此“两株下品”严格优于“一株中品”，不会再因 1+1=2 而打平。
                .OrderBy(item => item, gradeProfileComparer)
                .ThenBy(item => item.Pieces.Count)
                .ThenBy(item => item.YangCells + item.YinCells)
                .ThenBy(item => item.PlantingDays)
                .ThenBy(item => item.Key);
        }

        private static int CompareGradeProfiles(Composition left, Composition right)
        {
            if (ReferenceEquals(left, right)) return 0;
            if (left == null) return 1;
            if (right == null) return -1;
            var maximumGrade = Math.Max(left.MaxGrade, right.MaxGrade);
            for (var grade = maximumGrade; grade >= 1; grade--)
            {
                var leftCount = left.Pieces.Count(item => item.GradeWeight == grade);
                var rightCount = right.Pieces.Count(item => item.GradeWeight == grade);
                if (leftCount != rightCount) return leftCount.CompareTo(rightCount);
            }
            return 0;
        }

        private static int CompositionDistance(
            Composition composition,
            IReadOnlyDictionary<string, int> preferred)
        {
            var current = composition.Pieces
                .GroupBy(item => $"{item.Stock.ItemId.sedId}:{item.Side}")
                .ToDictionary(group => group.Key, group => group.Count());
            return current.Keys.Union(preferred.Keys)
                .Sum(key => Math.Abs((current.TryGetValue(key, out var left) ? left : 0) -
                                    (preferred.TryGetValue(key, out var right) ? right : 0)));
        }

        private static bool IsBetterRuleSolution(AlchemySolution left, AlchemySolution right)
        {
            var leftBenefit = CalculateEffectiveRuleBenefit(left);
            var rightBenefit = CalculateEffectiveRuleBenefit(right);
            if (leftBenefit != rightBenefit) return leftBenefit > rightBenefit;
            if (left.QualityRank != right.QualityRank) return left.QualityRank > right.QualityRank;
            var leftCount = left.BasePillCount + left.TotalCountBonus;
            var rightCount = right.BasePillCount + right.TotalCountBonus;
            if (leftCount != rightCount) return leftCount > rightCount;
            if (Math.Abs(left.PlantingDaysPerPill - right.PlantingDaysPerPill) > 0.0001)
                return left.PlantingDaysPerPill < right.PlantingDaysPerPill;
            if (left.Placements.Count != right.Placements.Count)
                return left.Placements.Count < right.Placements.Count;
            return left.PlantingDays < right.PlantingDays;
        }

        private static long CalculateEffectiveRuleBenefit(AlchemySolution solution) =>
            CalculateEffectiveRuleBenefit(solution.RuleOutcome, solution.GlobalQualityBonus);

        private static long CalculateEffectiveRuleBenefit(
            AlchemyRuleOutcome outcome,
            int globalQualityBonus)
        {
            // 品质只按最终可达到的 1~3 档计分；超过极品后的原始品质加成没有收益。
            var effectiveQuality = Math.Max(1, Math.Min(3,
                1 + globalQualityBonus + outcome.QualityBonus));
            return (long)effectiveQuality * 1_000_000L +
                   (long)outcome.CountBonus * 10_000L +
                   (long)outcome.FreeRateBonus * 100L -
                   (long)outcome.DayBonus * 10L -
                   outcome.DayMultiplierBonus;
        }

        private static bool IsBetterRepairSolution(
            AlchemySolution left,
            AlchemySolution right,
            AlchemySolution ideal)
        {
            if (left.QualityRank != right.QualityRank) return left.QualityRank > right.QualityRank;
            var leftCount = left.BasePillCount + left.TotalCountBonus;
            var rightCount = right.BasePillCount + right.TotalCountBonus;
            if (leftCount != rightCount) return leftCount > rightCount;
            var idealCounts = ideal.Placements
                .GroupBy(item => $"{item.ItemId.sedId}:{item.PoolType}")
                .ToDictionary(group => group.Key, group => group.Count());
            var leftDistance = PlacementDistance(left, idealCounts);
            var rightDistance = PlacementDistance(right, idealCounts);
            if (leftDistance != rightDistance) return leftDistance < rightDistance;
            if (Math.Abs(left.PlantingDaysPerPill - right.PlantingDaysPerPill) > 0.0001)
                return left.PlantingDaysPerPill < right.PlantingDaysPerPill;
            return left.Placements.Count < right.Placements.Count;
        }

        private static int PlacementDistance(
            AlchemySolution solution,
            IReadOnlyDictionary<string, int> preferred)
        {
            var current = solution.Placements
                .GroupBy(item => $"{item.ItemId.sedId}:{item.PoolType}")
                .ToDictionary(group => group.Key, group => group.Count());
            return current.Keys.Union(preferred.Keys)
                .Sum(key => Math.Abs((current.TryGetValue(key, out var left) ? left : 0) -
                                    (preferred.TryGetValue(key, out var right) ? right : 0)));
        }

        private static AlchemySolution CreateSolution(
            TbDrugRecipeCfg recipe,
            Composition composition,
            List<AlchemyPlacement> placements,
            AlchemyRuleOutcome outcome,
            int globalCountBonus,
            int globalQualityBonus)
        {
            return new AlchemySolution
            {
                Placements = placements,
                ItemCounts = composition.Pieces.GroupBy(item => item.Stock.ItemId.sedId)
                    .ToDictionary(group => group.Key, group => group.Count()),
                PlantingDays = composition.PlantingDays,
                BasePillCount = recipe.count,
                GlobalCountBonus = globalCountBonus,
                GlobalQualityBonus = globalQualityBonus,
                RuleOutcome = outcome ?? new AlchemyRuleOutcome(),
            };
        }

        private static Composition RebuildComposition(
            AlchemySolution solution,
            IReadOnlyList<HerbCandidate> candidates)
        {
            var byKey = candidates.GroupBy(item => $"{item.Stock.ItemId.sedId}:{item.Side}")
                .ToDictionary(group => group.Key, group => group.First());
            var composition = new Composition();
            foreach (var placement in solution.Placements)
            {
                if (!byKey.TryGetValue($"{placement.ItemId.sedId}:{placement.PoolType}", out var candidate))
                    return null;
                composition.Pieces.Add(candidate);
                if (candidate.Side == 1) composition.YangCells += candidate.CellCount;
                else composition.YinCells += candidate.CellCount;
            }
            return composition;
        }

        private static Composition CloneComposition(Composition source)
        {
            return new Composition
            {
                Pieces = source.Pieces.ToList(),
                YangCells = source.YangCells,
                YinCells = source.YinCells,
            };
        }

        private static bool FitsInventory(Composition composition, IReadOnlyDictionary<int, long> inventory)
        {
            if (inventory == null) return true;
            return composition.Pieces.GroupBy(item => item.Stock.ItemId.sedId)
                .All(group => inventory.TryGetValue(group.Key, out var owned) && owned >= group.Count());
        }

        private static IEnumerable<HerbCandidate> BuildMonotoneCandidates(
            IReadOnlyList<SmartAlchemyUi.HerbStock> stocks,
            IReadOnlyDictionary<string, int> targets,
            TbCraftingItemCfg furnace)
        {
            foreach (var stock in stocks)
            {
                if (!TryReadSingleAttribute(stock, out var crafting, out var draw,
                        out var elementKey, out var rawValue, out var gradeWeight, out var plantingDays,
                        out var rotations)) continue;
                if (!targets.TryGetValue(elementKey, out var target) || target == 0) continue;
                for (var side = 1; side <= 2; side++)
                {
                    var effective = rawValue * (side == 1 ? 1 : -1);
                    if (Math.Sign(effective) != Math.Sign(target) || Math.Abs(effective) > Math.Abs(target))
                        continue;
                    var size = side == 1 ? furnace.yangGridSize : furnace.yinGridSize;
                    if (!rotations.Any(shape => shape.Width <= size.x && shape.Height <= size.y)) continue;
                    yield return new HerbCandidate
                    {
                        Stock = stock,
                        Crafting = crafting,
                        Draw = draw,
                        ElementKey = elementKey,
                        Contribution = Math.Abs(effective),
                        Side = side,
                        GradeWeight = gradeWeight,
                        PlantingDays = plantingDays,
                        Rotations = rotations,
                        CellCount = draw.Coordinates.Count,
                    };
                }
            }
        }

        private static bool TryReadSingleAttribute(
            SmartAlchemyUi.HerbStock stock,
            out TbCraftingItemCfg crafting,
            out TbDrawCfg draw,
            out string elementKey,
            out int rawValue,
            out int gradeWeight,
            out int plantingDays,
            out List<RotationShape> rotations)
        {
            var data = Singleton<TbDataImpl>.Instance;
            crafting = data.GetCraftingItemCfg(stock.ItemId.sedId);
            draw = null;
            elementKey = null;
            rawValue = 0;
            gradeWeight = 0;
            plantingDays = 0;
            rotations = null;
            if (crafting?.attrDic == null) return false;
            var attributes = crafting.attrDic.Where(pair => pair.Value != 0).ToList();
            // A族相生与C族转换均在当前简化模型中排除；求解器只接收B族单属性药材。
            if (attributes.Count != 1) return false;
            draw = data.GetDrawCfg(crafting.drawId);
            if (draw?.Coordinates == null || draw.Coordinates.Count == 0) return false;
            elementKey = attributes[0].Key;
            rawValue = attributes[0].Value;
            var grade = data.GetGradeCfg(stock.ItemCfg.gradeId);
            gradeWeight = grade?.weight ?? 0;
            plantingDays = gradeWeight >= 1 && gradeWeight <= PlantingDaysByGrade.Length
                ? PlantingDaysByGrade[gradeWeight - 1]
                : int.MaxValue / 1000;
            rotations = BuildRotations(draw.Coordinates);
            return rotations.Count > 0;
        }

        internal static List<AlchemySolution> RankAndSelectSolutions(
            IEnumerable<AlchemySolution> solutions,
            int limit)
        {
            return solutions.Where(solution => solution != null)
                .OrderBy(solution => solution.SearchStage)
                .ThenByDescending(solution => solution.QualityRank)
                .ThenBy(solution => solution.PlantingDaysPerPill)
                .GroupBy(solution => solution.SearchStage)
                .Select(group => group.First())
                .Take(Math.Min(3, limit))
                .ToList();
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
            bool optimizeRules,
            int globalQualityBonus,
            out List<AlchemyPlacement> placements,
            out AlchemyRuleOutcome ruleOutcome,
            CancellationToken cancellationToken)
        {
            placements = new List<AlchemyPlacement>();
            ruleOutcome = new AlchemyRuleOutcome();
            var poseCache = chosen.Distinct().ToDictionary(candidate => candidate,
                candidate => BuildPoses(candidate, furnace, optimizeRules ? recipe : null));
            if (poseCache.Any(pair => pair.Value.Count == 0)) return false;
            var pieces = chosen
                // 先放置真正参与丹方位置规则的药材，避免普通配平药材抢占底部、边缘或中心。
                // 这只是通用搜索顺序，不改变规则得分，也不绑定具体丹方或五行。
                .OrderByDescending(candidate => optimizeRules
                    ? CalculateCandidateRulePriority(candidate, recipe)
                    : 0)
                .ThenByDescending(candidate => poseCache[candidate].Max(pose => pose.RuleHint))
                .ThenBy(candidate => poseCache[candidate].Count)
                .ThenByDescending(candidate => candidate.CellCount)
                .ThenBy(candidate => candidate.Stock.ItemId.sedId)
                .ThenBy(candidate => candidate.Side)
                .ToList();
            var occupiedLow = new ulong[3];
            var occupiedHigh = new ulong[3];
            var packed = new List<PlacementPose>();
            var lastPoseByCandidate = new Dictionary<HerbCandidate, int>();
            var bestPlacements = new List<AlchemyPlacement>();
            AlchemyRuleOutcome bestOutcome = null;
            var nodes = 0;
            AlchemyRuleOutcome simpleUpperBound = null;
            var hasSimpleUpperBound = optimizeRules &&
                                      TryCalculateIndependentRuleUpperBound(
                                          chosen, furnace, recipe, out simpleUpperBound);
            var simpleUpperBenefit = hasSimpleUpperBound
                ? CalculateEffectiveRuleBenefit(simpleUpperBound, globalQualityBonus)
                : long.MaxValue;

            List<AlchemyPlacement> MakePlacements()
            {
                return packed.Select(item =>
                {
                    var size = item.Candidate.Side == 1 ? furnace.yangGridSize : furnace.yinGridSize;
                    return new AlchemyPlacement
                    {
                        ItemId = item.Candidate.Stock.ItemId,
                        PoolType = item.Candidate.Side,
                        Rotation = item.Shape.Rotation,
                        Position = new MyVector2Int(
                            (1 - size.x) / 2 + item.X - item.Shape.MinX,
                            (1 - size.y) / 2 + item.Y - item.Shape.MinY),
                    };
                }).ToList();
            }

            bool PackAt(int pieceIndex)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (nodes++ >= PackingNodeLimit) return true;
                if (pieceIndex >= pieces.Count)
                {
                    var candidatePlacements = MakePlacements();
                    var candidateOutcome = AlchemyRuleEvaluator.Evaluate(recipe, candidatePlacements, furnace);
                    if (bestOutcome == null ||
                        CalculateEffectiveRuleBenefit(candidateOutcome, globalQualityBonus) >
                        CalculateEffectiveRuleBenefit(bestOutcome, globalQualityBonus))
                    {
                        bestOutcome = candidateOutcome;
                        bestPlacements = candidatePlacements;
                    }
                    if (!optimizeRules) return true;
                    return hasSimpleUpperBound &&
                           CalculateEffectiveRuleBenefit(bestOutcome, globalQualityBonus) >= simpleUpperBenefit;
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
                    if (PackAt(pieceIndex + 1)) return true;
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

        private static bool TryCalculateIndependentRuleUpperBound(
            IReadOnlyList<HerbCandidate> chosen,
            TbCraftingItemCfg furnace,
            TbDrugRecipeCfg recipe,
            out AlchemyRuleOutcome outcome)
        {
            outcome = new AlchemyRuleOutcome();
            if (recipe?.StateIds == null || recipe.StateIds.Count == 0) return false;
            foreach (var stateId in recipe.StateIds)
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                // 仅对“指定区域内每株目标药材独立计数”的规则使用上界。
                // 属性求和、空格计数、相邻、同行等复杂规则继续完整搜索。
                if (state == null || state.relation != 0 || state.stateType == 0 ||
                    (state.stateType >= 11 && state.stateType <= 18)) return false;
                var measured = 0;
                for (var side = 1; side <= 2; side++)
                {
                    if (state.poolType != 0 && state.poolType != side) continue;
                    var size = side == 1 ? furnace.yangGridSize : furnace.yinGridSize;
                    var areaCapacity = AlchemyRuleEvaluator.CountAreaCells(size, state.area);
                    var matchingCount = chosen.Count(candidate => candidate.Side == side &&
                        TargetMayMatch(candidate.Stock.ItemCfg, state.target1));
                    measured += Math.Min(matchingCount, areaCapacity);
                }
                AlchemyRuleEvaluator.ApplyMeasuredState(outcome, state, measured);
            }
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

        private static int CalculateCandidateRulePriority(
            HerbCandidate candidate,
            TbDrugRecipeCfg recipe)
        {
            var priority = 0;
            foreach (var stateId in recipe?.StateIds ?? new List<int>())
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                if (state == null || (state.poolType != 0 && state.poolType != candidate.Side)) continue;
                var matchesFirst = string.IsNullOrEmpty(state.target1) ||
                                   TargetMayMatch(candidate.Stock.ItemCfg, state.target1);
                var matchesSecond = !string.IsNullOrEmpty(state.target2) &&
                                    TargetMayMatch(candidate.Stock.ItemCfg, state.target2);
                if (matchesFirst || matchesSecond) priority++;
            }
            return priority;
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
            foreach (var stateId in recipe?.StateIds ?? new List<int>())
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                if (state == null || (state.poolType != 0 && state.poolType != candidate.Side)) continue;
                if (!TargetMayMatch(candidate.Stock.ItemCfg, state.target1)) continue;
                var areaCodes = (state.area ?? "").Split('&').SelectMany(stage => stage.Split('|'))
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
