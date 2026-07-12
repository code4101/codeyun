using System;
using System.Collections.Generic;
using System.Linq;

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
        internal int GradeScore;
        internal AlchemyRuleOutcome RuleOutcome = new AlchemyRuleOutcome();

        internal TbCraftingTemplateSto ToTemplate(TbDrugRecipeCfg recipe, int index)
        {
            var quality = Math.Max(0, Math.Min(3, 1 + RuleOutcome.QualityBonus));
            var data = Singleton<TbDataImpl>.Instance;
            var output = data.itemCfgList
                .Where(item => item.id == recipe.itemId || item.groupId == recipe.itemId)
                .Where(item => item.drugQuality <= quality)
                .OrderByDescending(item => item.drugQuality)
                .FirstOrDefault() ?? data.GetItemCfg(recipe.itemId);
            var bonuses = new List<string>();
            if (RuleOutcome.QualityBonus != 0) bonuses.Add($"品质{RuleOutcome.QualityBonus:+#;-#;0}");
            if (RuleOutcome.CountBonus != 0) bonuses.Add($"成丹{RuleOutcome.CountBonus:+#;-#;0}");
            return new TbCraftingTemplateSto
            {
                id = -100000 - index,
                type = 0,
                isFollow = false,
                name = $"{recipe.GetName} · 智能方案 {index + 1}" +
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
        private const int SearchNodeLimit = 160000;
        private const int PackingNodeLimit = 30000;

        private sealed class HerbCandidate
        {
            internal SmartAlchemyUi.HerbStock Stock;
            internal TbCraftingItemCfg Crafting;
            internal TbDrawCfg Draw;
            internal int Side;
            internal int GradeWeight;
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

        internal static List<AlchemySolution> Solve(
            TbDrugRecipeCfg recipe,
            TbPackSto furnace,
            IReadOnlyList<SmartAlchemyUi.HerbStock> stocks,
            int limit)
        {
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

            var candidates = BuildCandidates(stocks, recipe)
                .OrderByDescending(candidate => candidate.HeuristicScore)
                .ThenBy(candidate => candidate.CellCount)
                .ThenBy(candidate => candidate.GradeWeight)
                .ThenBy(candidate => candidate.Stock.ItemId.sedId)
                .ThenBy(candidate => candidate.Side)
                .ToList();
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

            var inventory = stocks.ToDictionary(stock => stock.ItemId.sedId, stock => stock.Count);
            var chosen = new List<HerbCandidate>();
            var vector = allKeys.ToDictionary(key => key, _ => 0);
            var solutions = new List<AlchemySolution>();
            var solutionKeys = new HashSet<string>();
            var poseModelCache = new Dictionary<HerbCandidate, List<PlacementPose>>();
            var searchNodes = 0;
            var visitedStates = new HashSet<string>();
            var initialInventory = new Dictionary<int, long>(inventory);
            var maxPieces = Math.Min(
                (furnaceCfg.yangGridSize.x * furnaceCfg.yangGridSize.y) +
                (furnaceCfg.yinGridSize.x * furnaceCfg.yinGridSize.y),
                stocks.Sum(stock => stock.Count) > int.MaxValue
                    ? int.MaxValue
                    : (int)stocks.Sum(stock => stock.Count));

            void Search(int startIndex)
            {
                if (searchNodes++ >= SearchNodeLimit || solutions.Count >= Math.Max(limit * 5, 40)) return;

                var stateKey = BuildSearchStateKey(startIndex, vector, inventory, initialInventory, candidates);
                if (!visitedStates.Add(stateKey)) return;

                if (MatchesTarget(vector, target))
                {
                    var key = string.Join(";", chosen
                        .GroupBy(candidate => $"{candidate.Stock.ItemId.sedId}:{candidate.Side}")
                        .OrderBy(group => group.Key)
                        .Select(group => $"{group.Key}:{group.Count()}"));
                    if (solutionKeys.Add(key) && TryPack(
                            chosen, furnaceCfg, recipe, poseModelCache,
                            out var placements, out var ruleOutcome))
                    {
                        solutions.Add(new AlchemySolution
                        {
                            Placements = placements,
                            ItemCounts = chosen.GroupBy(c => c.Stock.ItemId.sedId)
                                .ToDictionary(group => group.Key, group => group.Count()),
                            GradeScore = chosen.Sum(c => c.GradeWeight),
                            RuleOutcome = ruleOutcome,
                        });
                    }
                    return;
                }

                if (chosen.Count >= maxPieces) return;
                var usedYangCells = chosen.Where(candidate => candidate.Side == 1).Sum(candidate => candidate.CellCount);
                var usedYinCells = chosen.Where(candidate => candidate.Side == 2).Sum(candidate => candidate.CellCount);
                if (!CanStillReachTarget(vector, target, candidates, startIndex, inventory,
                        furnaceCfg.yangGridSize.x * furnaceCfg.yangGridSize.y - usedYangCells,
                        furnaceCfg.yinGridSize.x * furnaceCfg.yinGridSize.y - usedYinCells)) return;

                for (var index = startIndex; index < candidates.Count; index++)
                {
                    var candidate = candidates[index];
                    var itemId = candidate.Stock.ItemId.sedId;
                    if (!inventory.TryGetValue(itemId, out var remaining) || remaining <= 0) continue;
                    if (!CanMoveTowardTarget(vector, target, candidate)) continue;

                    inventory[itemId] = remaining - 1;
                    chosen.Add(candidate);
                    AddVector(vector, candidate, 1);
                    Search(index);
                    AddVector(vector, candidate, -1);
                    chosen.RemoveAt(chosen.Count - 1);
                    inventory[itemId] = remaining;
                }
            }

            Search(0);
            var orderedSolutions = solutions
                .OrderByDescending(solution => solution.RuleOutcome.Score)
                // 与 CodeYun 页面求解器一致：优先使用更低品阶、占位更少的药材组合。
                .ThenBy(solution => solution.GradeScore)
                .ThenBy(solution => solution.Placements.Count)
                .ToList();
            return SelectDiverseSolutions(orderedSolutions, limit);
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
                if (!crafting.attrDic.Keys.Any(targetKeys.Contains)) continue;
                var grade = data.GetGradeCfg(stock.ItemCfg.gradeId);
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
                        GradeWeight = grade?.weight ?? 0,
                        Rotations = rotations,
                        CellCount = draw.Coordinates.Count,
                        HeuristicScore = alignedContribution / Math.Max(1.0, draw.Coordinates.Count),
                    };
                }
            }
        }

        private static string BuildSearchStateKey(
            int startIndex,
            Dictionary<string, int> vector,
            Dictionary<int, long> inventory,
            Dictionary<int, long> initialInventory,
            List<HerbCandidate> candidates)
        {
            var relevantItems = candidates.Skip(startIndex).Select(candidate => candidate.Stock.ItemId.sedId).Distinct();
            return startIndex + "|" +
                   string.Join(",", vector.OrderBy(pair => pair.Key).Select(pair => pair.Key + ":" + pair.Value)) + "|" +
                   string.Join(",", relevantItems.Where(id => inventory[id] != initialInventory[id])
                       .OrderBy(id => id).Select(id => id + ":" + inventory[id]));
        }

        private static bool CanStillReachTarget(
            Dictionary<string, int> vector,
            Dictionary<string, int> target,
            List<HerbCandidate> candidates,
            int startIndex,
            Dictionary<int, long> inventory,
            int remainingYangCells,
            int remainingYinCells)
        {
            if (remainingYangCells < 0 || remainingYinCells < 0) return false;
            var remainingTotalCells = remainingYangCells + remainingYinCells;
            foreach (var key in vector.Keys)
            {
                var expected = target.TryGetValue(key, out var targetValue) ? targetValue : 0;
                var deficit = expected - vector[key];
                if (deficit == 0) continue;
                long available = 0;
                var bestPerCell = 0.0;
                for (var index = startIndex; index < candidates.Count; index++)
                {
                    var candidate = candidates[index];
                    var sideCells = candidate.Side == 1 ? remainingYangCells : remainingYinCells;
                    if (candidate.CellCount > sideCells) continue;
                    var contribution = candidate.Crafting.attrDic.TryGetValue(key, out var raw)
                        ? raw * (candidate.Side == 1 ? 1 : -1)
                        : 0;
                    if (Math.Sign(contribution) != Math.Sign(deficit)) continue;
                    var count = inventory.TryGetValue(candidate.Stock.ItemId.sedId, out var stock) ? stock : 0;
                    available += Math.Abs((long)contribution) * count;
                    bestPerCell = Math.Max(bestPerCell, Math.Abs(contribution) / (double)candidate.CellCount);
                }
                if (available < Math.Abs((long)deficit) || bestPerCell <= 0) return false;
                var minimumCells = (int)Math.Ceiling(Math.Abs(deficit) / bestPerCell);
                if (minimumCells > remainingTotalCells) return false;
            }
            return true;
        }

        private static List<AlchemySolution> SelectDiverseSolutions(List<AlchemySolution> ordered, int limit)
        {
            var selected = new List<AlchemySolution>();
            var seenLayouts = new HashSet<string>();
            var seenCompositions = new HashSet<string>();
            var seenFamilies = new HashSet<string>();

            bool Add(AlchemySolution solution, bool requireNewFamily)
            {
                var composition = string.Join(";", solution.Placements
                    .GroupBy(item => $"{item.ItemId.sedId}:{item.PoolType}")
                    .OrderBy(group => group.Key).Select(group => $"{group.Key}:{group.Count()}"));
                var layout = string.Join(";", solution.Placements
                    .OrderBy(item => item.ItemId.sedId).ThenBy(item => item.PoolType)
                    .ThenBy(item => item.Position.x).ThenBy(item => item.Position.y)
                    .Select(item => $"{item.ItemId.sedId}:{item.PoolType}:{item.Position.x},{item.Position.y}:{item.Rotation}"));
                var family = $"q{solution.RuleOutcome.QualityBonus}:c{solution.RuleOutcome.CountBonus}:" +
                             string.Join(",", solution.ItemCounts.Keys.OrderBy(id => id));
                if (seenLayouts.Contains(layout) || seenCompositions.Contains(composition)) return false;
                if (requireNewFamily && seenFamilies.Contains(family)) return false;
                seenLayouts.Add(layout);
                seenCompositions.Add(composition);
                seenFamilies.Add(family);
                selected.Add(solution);
                return true;
            }

            foreach (var solution in ordered)
            {
                Add(solution, true);
                if (selected.Count >= limit) return selected;
            }
            foreach (var solution in ordered)
            {
                Add(solution, false);
                if (selected.Count >= limit) break;
            }
            return selected;
        }

        private static bool MatchesTarget(Dictionary<string, int> vector, Dictionary<string, int> target)
        {
            foreach (var pair in vector)
            {
                var expected = target.TryGetValue(pair.Key, out var value) ? value : 0;
                if (pair.Value != expected) return false;
            }
            return true;
        }

        private static bool CanMoveTowardTarget(
            Dictionary<string, int> vector,
            Dictionary<string, int> target,
            HerbCandidate candidate)
        {
            var sign = candidate.Side == 1 ? 1 : -1;
            foreach (var pair in candidate.Crafting.attrDic)
            {
                var current = vector.TryGetValue(pair.Key, out var value) ? value : 0;
                var expected = target.TryGetValue(pair.Key, out var targetValue) ? targetValue : 0;
                var next = current + pair.Value * sign;
                if (Math.Abs(next - expected) < Math.Abs(current - expected)) return true;
            }
            return false;
        }

        private static void AddVector(Dictionary<string, int> vector, HerbCandidate candidate, int multiplier)
        {
            var sign = candidate.Side == 1 ? 1 : -1;
            foreach (var pair in candidate.Crafting.attrDic)
            {
                vector[pair.Key] = vector.TryGetValue(pair.Key, out var value)
                    ? value + pair.Value * sign * multiplier
                    : pair.Value * sign * multiplier;
            }
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
            out AlchemyRuleOutcome ruleOutcome)
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
