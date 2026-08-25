using System;
using System.Collections.Generic;
using System.Linq;

namespace Code4101.Zaohua.Tiandao
{
    internal sealed class AlchemyRuleOutcome
    {
        internal int TriggerCount;
        internal int CountBonus;
        internal int QualityBonus;
        internal int DayBonus;
        internal int DayMultiplierBonus;
        internal int FreeRateBonus;

    }

    internal static class AlchemyRuleEvaluator
    {
        private sealed class Piece
        {
            internal int Id;
            internal TbItemCfg Item;
            internal TbCraftingItemCfg Crafting;
        }

        internal static AlchemyRuleOutcome Evaluate(
            TbDrugRecipeCfg recipe,
            IReadOnlyList<AlchemyPlacement> placements,
            TbCraftingItemCfg furnace)
        {
            var outcome = new AlchemyRuleOutcome();
            if (recipe?.StateIds == null || recipe.StateIds.Count == 0) return outcome;
            var boards = BuildBoards(placements, furnace);
            foreach (var stateId in recipe.StateIds)
            {
                var state = Singleton<TbDataImpl>.Instance.GetDrugRecipeStateCfg(stateId);
                if (state == null || string.IsNullOrEmpty(state.area)) continue;
                var measured = 0;
                for (var poolType = 1; poolType <= 2; poolType++)
                {
                    if (state.poolType != 0 && state.poolType != poolType) continue;
                    var area = SelectArea(boards[poolType], state.area);
                    var first = Eligible(area, state.target1);
                    var second = Eligible(area, state.target2);
                    measured += Measure(area, first, second, state, poolType);
                }

                var multiplier = Calculate(measured, state.calculateType);
                if (multiplier == 0 || string.IsNullOrEmpty(state.baseEff)) continue;
                outcome.TriggerCount += multiplier;
                ApplyEffect(outcome, state.baseEff, multiplier);
            }
            return outcome;
        }

        private static Dictionary<int, Dictionary<MyVector2Int, Piece>> BuildBoards(
            IReadOnlyList<AlchemyPlacement> placements,
            TbCraftingItemCfg furnace)
        {
            var result = new Dictionary<int, Dictionary<MyVector2Int, Piece>>();
            for (var side = 1; side <= 2; side++)
            {
                var size = side == 1 ? furnace.yangGridSize : furnace.yinGridSize;
                var board = new Dictionary<MyVector2Int, Piece>();
                for (var x = (1 - size.x) / 2; x <= size.x / 2; x++)
                for (var y = (1 - size.y) / 2; y <= size.y / 2; y++)
                    board[new MyVector2Int(x, y)] = null;
                result[side] = board;
            }

            for (var index = 0; index < placements.Count; index++)
            {
                var placement = placements[index];
                var data = Singleton<TbDataImpl>.Instance;
                var crafting = data.GetCraftingItemCfg(placement.ItemId.sedId);
                var draw = data.GetDrawCfg(crafting.drawId);
                var piece = new Piece
                {
                    Id = index,
                    Item = data.GetItemCfg(placement.ItemId.sedId),
                    Crafting = crafting,
                };
                foreach (var cell in draw.Coordinates)
                {
                    var rotated = Rotate(cell, placement.Rotation);
                    result[placement.PoolType][new MyVector2Int(
                        placement.Position.x + rotated.x,
                        placement.Position.y + rotated.y)] = piece;
                }
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

        private static Dictionary<MyVector2Int, Piece> SelectArea(
            Dictionary<MyVector2Int, Piece> board,
            string areaExpression)
        {
            var current = new Dictionary<MyVector2Int, Piece>(board);
            foreach (var stage in areaExpression.Split('&'))
            {
                var next = new Dictionary<MyVector2Int, Piece>();
                foreach (var rawCode in stage.Split('|'))
                {
                    if (!int.TryParse(rawCode, out var code)) continue;
                    if (code == 0)
                    {
                        next = new Dictionary<MyVector2Int, Piece>(board);
                        continue;
                    }
                    if (code >= 1 && code <= 5)
                    {
                        foreach (var pair in current)
                        {
                            var selected = code switch
                            {
                                1 => pair.Key.y == current.Keys.Max(key => key.y),
                                2 => pair.Key.y == current.Keys.Min(key => key.y),
                                3 => pair.Key.x == current.Keys.Min(key => key.x),
                                4 => pair.Key.x == current.Keys.Max(key => key.x),
                                5 => pair.Key == MyVector2Int.zero,
                                _ => false,
                            };
                            if (selected) next[pair.Key] = pair.Value;
                        }
                        continue;
                    }
                    if (code < 10 || code >= 20 || current.Count == 0) continue;
                    var distance = code - 10;
                    foreach (var pair in board)
                    {
                        if (current.Keys.Min(origin => Manhattan(pair.Key, origin)) == distance)
                            next[pair.Key] = pair.Value;
                    }
                }
                current = next;
            }
            return current;
        }

        internal static int CountAreaCells(MyVector2Int size, string areaExpression)
        {
            var board = new Dictionary<MyVector2Int, Piece>();
            for (var x = (1 - size.x) / 2; x <= size.x / 2; x++)
            for (var y = (1 - size.y) / 2; y <= size.y / 2; y++)
                board[new MyVector2Int(x, y)] = null;
            return SelectArea(board, areaExpression).Count;
        }

        private static int Manhattan(MyVector2Int left, MyVector2Int right) =>
            Math.Abs(left.x - right.x) + Math.Abs(left.y - right.y);

        private static Dictionary<MyVector2Int, Piece> Eligible(
            Dictionary<MyVector2Int, Piece> area,
            string targetExpression)
        {
            if (string.IsNullOrEmpty(targetExpression))
                return new Dictionary<MyVector2Int, Piece>(area);
            var result = new Dictionary<MyVector2Int, Piece>();
            foreach (var option in targetExpression.Split('|'))
            {
                var conditions = option.Split('&').Select(int.Parse).ToArray();
                foreach (var pair in area)
                {
                    if (conditions.All(condition => Matches(pair.Value, condition))) result[pair.Key] = pair.Value;
                }
            }
            return result;
        }

        private static bool Matches(Piece piece, int condition)
        {
            if (condition == 0) return piece == null;
            if (piece?.Item == null) return false;
            if (condition >= 1 && condition <= 8) return piece.Item.attribute == condition;
            var grade = Singleton<TbDataImpl>.Instance.GetGradeCfg(piece.Item.gradeId);
            if (grade == null) return false;
            if (condition >= 11 && condition <= 14) return (grade.weight + 2) / 3 >= condition - 10;
            if (condition >= 21 && condition <= 32) return grade.weight >= condition - 20;
            if (condition >= 41 && condition <= 44) return (grade.weight + 2) / 3 <= condition - 40;
            if (condition >= 51 && condition <= 62) return grade.weight <= condition - 50;
            return false;
        }

        private static int Measure(
            Dictionary<MyVector2Int, Piece> area,
            Dictionary<MyVector2Int, Piece> first,
            Dictionary<MyVector2Int, Piece> second,
            TbDrugRecipeStateCfg state,
            int poolType)
        {
            if (state.relation == 0)
            {
                if (state.stateType >= 11 && state.stateType <= 18)
                {
                    var keys = new[] { "gold", "water", "wood", "fire", "soil", "ice", "wind", "thunder" };
                    var key = keys[state.stateType - 11];
                    return first.Values.Where(piece => piece != null).Distinct()
                        .Sum(piece => piece.Crafting.attrDic.TryGetValue(key, out var value)
                            ? (poolType == 1 ? value : -value) : 0);
                }
                return state.stateType == 0 ? first.Count : first.Values.Distinct().Count();
            }
            if (state.relation == 100) return MaximumDistinctByAxis(first, true);
            if (state.relation == 102) return MaximumDistinctByAxis(first, false);
            if (state.relation == 101)
            {
                var total = state.stateType == 0 ? area.Values.Count(piece => piece != null) :
                    area.Values.Where(piece => piece != null).Distinct().Count();
                var selected = state.stateType == 0 ? first.Count :
                    first.Values.Where(piece => piece != null).Distinct().Count();
                return total == selected && total > 0 ? 1 : 0;
            }

            var uniquePairs = new HashSet<(int, int)>();
            var count = 0;
            foreach (var left in first)
            foreach (var right in second)
            {
                var matched = state.relation switch
                {
                    10 => Manhattan(left.Key, right.Key) == 1,
                    11 => right.Key.x == left.Key.x,
                    12 => right.Key.y == left.Key.y,
                    13 => right.Key.y == left.Key.y && right.Key.x == left.Key.x - 1,
                    14 => right.Key.y == left.Key.y && right.Key.x == left.Key.x + 1,
                    15 => right.Key.x == left.Key.x && right.Key.y == left.Key.y + 1,
                    16 => right.Key.x == left.Key.x && right.Key.y == left.Key.y - 1,
                    _ => false,
                };
                if (!matched) continue;
                if (state.stateType == 0 || uniquePairs.Add((left.Value?.Id ?? -1, right.Value?.Id ?? -1))) count++;
            }
            return count;
        }

        private static int MaximumDistinctByAxis(Dictionary<MyVector2Int, Piece> cells, bool byX) =>
            cells.GroupBy(pair => byX ? pair.Key.x : pair.Key.y)
                .Select(group => group.Select(pair => pair.Value).Distinct().Count()).DefaultIfEmpty(0).Max();

        private static int Calculate(int value, string expression)
        {
            if (string.IsNullOrEmpty(expression)) return 0;
            var parts = expression.Split('#');
            var operation = int.Parse(parts[0]);
            var threshold = parts.Length > 1 ? int.Parse(parts[1]) : 0;
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

        internal static void ApplyMeasuredState(
            AlchemyRuleOutcome outcome,
            TbDrugRecipeStateCfg state,
            int measured)
        {
            if (outcome == null || state == null) return;
            var multiplier = Calculate(measured, state.calculateType);
            if (multiplier == 0 || string.IsNullOrEmpty(state.baseEff)) return;
            outcome.TriggerCount += multiplier;
            ApplyEffect(outcome, state.baseEff, multiplier);
        }

        private static void ApplyEffect(AlchemyRuleOutcome outcome, string effectText, int multiplier)
        {
            var effect = GenericMethods.GetEffect(effectText);
            if (effect == null || effect.functionType != EventEnum.UpdateCraftingDrugAttr.ToString() || effect.parameter.Count < 2)
                return;
            if (!int.TryParse(effect.parameter[0], out var type) || !int.TryParse(effect.parameter[1], out var value)) return;
            value *= multiplier;
            switch (type)
            {
                case 1: outcome.CountBonus += value; break;
                case 2: outcome.DayBonus += value; break;
                case 3: outcome.DayMultiplierBonus += value; break;
                case 4: outcome.QualityBonus += value; break;
                case 5: outcome.FreeRateBonus += value; break;
            }
        }
    }
}
