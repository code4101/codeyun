using System;
using System.Collections.Generic;
using System.Linq;

namespace CodeYun.Zaohua.SmartAlchemy
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

        internal TbCraftingTemplateSto ToTemplate(TbDrugRecipeCfg recipe, int index)
        {
            return new TbCraftingTemplateSto
            {
                id = -100000 - index,
                type = 0,
                isFollow = false,
                name = $"{recipe.GetName} · 智能方案 {index + 1}",
                itemId = recipe.blendId,
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

        private sealed class PackedPiece
        {
            internal HerbCandidate Candidate;
            internal RotationShape Shape;
            internal int X;
            internal int Y;
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

            var candidates = BuildCandidates(stocks, recipe).ToList();
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
            var searchNodes = 0;
            var maxPieces = Math.Min(
                (furnaceCfg.yangGridSize.x * furnaceCfg.yangGridSize.y) +
                (furnaceCfg.yinGridSize.x * furnaceCfg.yinGridSize.y),
                24);

            void Search(int startIndex)
            {
                if (searchNodes++ >= SearchNodeLimit || solutions.Count >= Math.Max(limit * 5, 40)) return;

                if (MatchesTarget(vector, target))
                {
                    var key = string.Join(";", chosen
                        .GroupBy(candidate => $"{candidate.Stock.ItemId.sedId}:{candidate.Side}")
                        .OrderBy(group => group.Key)
                        .Select(group => $"{group.Key}:{group.Count()}"));
                    if (solutionKeys.Add(key) && TryPack(
                            chosen,
                            furnaceCfg.yangGridSize.x,
                            furnaceCfg.yangGridSize.y,
                            furnaceCfg.yinGridSize.x,
                            furnaceCfg.yinGridSize.y,
                            out var placements))
                    {
                        solutions.Add(new AlchemySolution
                        {
                            Placements = placements,
                            ItemCounts = chosen.GroupBy(c => c.Stock.ItemId.sedId)
                                .ToDictionary(group => group.Key, group => group.Count()),
                            GradeScore = chosen.Sum(c => c.GradeWeight),
                        });
                    }
                    return;
                }

                if (chosen.Count >= maxPieces) return;

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
            return solutions
                // 与 CodeYun 页面求解器一致：优先使用更低品阶、占位更少的药材组合。
                .OrderBy(solution => solution.GradeScore)
                .ThenBy(solution => solution.Placements.Count)
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
                if (!crafting.attrDic.Keys.Any(targetKeys.Contains)) continue;
                var grade = data.GetGradeCfg(stock.ItemCfg.gradeId);
                var rotations = BuildRotations(draw.Coordinates);
                for (var side = 1; side <= 2; side++)
                {
                    yield return new HerbCandidate
                    {
                        Stock = stock,
                        Crafting = crafting,
                        Draw = draw,
                        Side = side,
                        GradeWeight = grade?.weight ?? 0,
                        Rotations = rotations,
                    };
                }
            }
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
            int yangWidth,
            int yangHeight,
            int yinWidth,
            int yinHeight,
            out List<AlchemyPlacement> placements)
        {
            placements = new List<AlchemyPlacement>();
            foreach (var side in new[] { 1, 2 })
            {
                var width = side == 1 ? yangWidth : yinWidth;
                var height = side == 1 ? yangHeight : yinHeight;
                var pieces = chosen.Where(candidate => candidate.Side == side)
                    .OrderByDescending(candidate => candidate.Draw.Coordinates.Count).ToList();
                var occupied = new bool[width, height];
                var packed = new List<PackedPiece>();
                var nodes = 0;

                bool PackAt(int pieceIndex)
                {
                    if (pieceIndex >= pieces.Count) return true;
                    if (nodes++ >= PackingNodeLimit) return false;
                    var piece = pieces[pieceIndex];
                    foreach (var shape in piece.Rotations)
                    {
                        for (var y = 0; y <= height - shape.Height; y++)
                        for (var x = 0; x <= width - shape.Width; x++)
                        {
                            if (shape.Normalized.Any(cell => occupied[x + cell.x, y + cell.y])) continue;
                            foreach (var cell in shape.Normalized) occupied[x + cell.x, y + cell.y] = true;
                            packed.Add(new PackedPiece { Candidate = piece, Shape = shape, X = x, Y = y });
                            if (PackAt(pieceIndex + 1)) return true;
                            packed.RemoveAt(packed.Count - 1);
                            foreach (var cell in shape.Normalized) occupied[x + cell.x, y + cell.y] = false;
                        }
                    }
                    return false;
                }

                if (!PackAt(0)) return false;
                var boardMinX = (1 - width) / 2;
                var boardMinY = (1 - height) / 2;
                foreach (var packedPiece in packed)
                {
                    placements.Add(new AlchemyPlacement
                    {
                        ItemId = packedPiece.Candidate.Stock.ItemId,
                        PoolType = side,
                        Rotation = packedPiece.Shape.Rotation,
                        Position = new MyVector2Int(
                            boardMinX + packedPiece.X - packedPiece.Shape.MinX,
                            boardMinY + packedPiece.Y - packedPiece.Shape.MinY),
                    });
                }
            }
            return true;
        }
    }
}
