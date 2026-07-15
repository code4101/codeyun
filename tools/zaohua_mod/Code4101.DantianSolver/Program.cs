using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using Google.OrTools.Sat;
using Newtonsoft.Json;

namespace Code4101.DantianSolver
{
    internal sealed class Request
    {
        public int version = 0;
        public int timeLimitMs = 0;
        public int seed = 0;
        public int cellCount = 0;
        public int[] cellX = Array.Empty<int>();
        public int[] cellY = Array.Empty<int>();
        public int[] currentPlacements = Array.Empty<int>();
        public int[] expectedCurrentMultipliers = Array.Empty<int>();
        public Piece[] pieces = Array.Empty<Piece>();
        public Rule[] rules = Array.Empty<Rule>();
    }

    internal sealed class Piece
    {
        public string name = null;
        public Placement[] placements = Array.Empty<Placement>();
    }

    internal sealed class Placement
    {
        public int[] cells = Array.Empty<int>();
    }

    internal sealed class Rule
    {
        public string name = null;
        public int sourcePiece = 0;
        public int maxMultiplier = 0;
        public int[] multiplierByCount = Array.Empty<int>();
        public SourceOption[] sourceOptions = Array.Empty<SourceOption>();
        public bool countSelf = false;
        public int[] countGeometry = Array.Empty<int>();
        public int[] countTargetPieces = Array.Empty<int>();
        public bool gateSelf = false;
        public int[] gateGeometry = Array.Empty<int>();
        public int[] gateTargetPieces = Array.Empty<int>();
    }

    internal sealed class SourceOption
    {
        public Feature[] countFeatures = Array.Empty<Feature>();
        public Feature gateFeature = null;
    }

    internal sealed class Feature
    {
        public Term[] terms = Array.Empty<Term>();
    }

    internal sealed class Term
    {
        public int piece = 0;
        public string placementFlags = null;
    }

    internal sealed class Response
    {
        public int version = 1;
        public string status;
        public string error;
        public int[] placements;
        public int[] multipliers;
        public long product;
        public int total;
        public double objective;
        public double bestBound;
        public double elapsedSeconds;
        public double modelBuildSeconds;
        public double totalSeconds;
    }

    internal static class Program
    {
        private static int Main()
        {
            Console.InputEncoding = Encoding.UTF8;
            Console.OutputEncoding = Encoding.UTF8;
            try
            {
                var json = Console.In.ReadToEnd();
                Request request;
                try
                {
                    request = JsonConvert.DeserializeObject<Request>(json);
                }
                catch (JsonReaderException exception)
                {
                    var position = Math.Max(0, Math.Min(json.Length, exception.LinePosition));
                    var start = Math.Max(0, position - 80);
                    var length = Math.Min(160, json.Length - start);
                    var excerpt = length > 0 ? json.Substring(start, length) : string.Empty;
                    return WriteError($"JSON解析失败 len={json.Length} pos={position} " +
                                      $"excerpt={JsonConvert.SerializeObject(excerpt)}；" +
                                      exception.Message);
                }
                if (request == null || request.version != 2)
                    return WriteError("不支持的求解协议");
                var response = Solve(request);
                Console.Out.Write(JsonConvert.SerializeObject(response));
                return response.error == null ? 0 : 2;
            }
            catch (Exception exception)
            {
                return WriteError(exception.GetType().Name + ": " + exception.Message);
            }
        }

        private static int WriteError(string error)
        {
            Console.Out.Write(JsonConvert.SerializeObject(new Response
            {
                status = "ERROR",
                error = error,
            }));
            return 2;
        }

        private static Response Solve(Request request)
        {
            Validate(request);
            return SolveShapeModel(request);
        }

        private sealed class ShapeVars
        {
            internal IntVar Placement;
            internal IntVar[] Cells;
            internal IntVar[] X;
            internal IntVar[] Y;
        }

        private sealed class HeuristicSolution
        {
            internal int[] Placements;
            internal int[] Multipliers;
            internal int Active;
            internal double Balance;
            internal int Total;
        }

        private static Response SolveShapeModel(Request request)
        {
            var watch = Stopwatch.StartNew();
            var heuristic = ImprovePlacementHint(request,
                Math.Min(1500, Math.Max(300, request.timeLimitMs / 10)));
            request.currentPlacements = heuristic.Placements.ToArray();
            var model = new CpModel();
            var shapes = new ShapeVars[request.pieces.Length];
            var allCells = new List<IntVar>();
            for (var pieceIndex = 0; pieceIndex < request.pieces.Length; pieceIndex++)
            {
                var piece = request.pieces[pieceIndex];
                var cellCount = piece.placements[0].cells.Length;
                if (piece.placements.Any(placement => placement.cells.Length != cellCount))
                    throw new InvalidDataException($"功法{pieceIndex}的形状格数不一致");
                var shape = new ShapeVars
                {
                    Placement = model.NewIntVar(0, piece.placements.Length - 1,
                        $"placement_{pieceIndex}"),
                    Cells = new IntVar[cellCount],
                    X = new IntVar[cellCount],
                    Y = new IntVar[cellCount],
                };
                model.AddHint(shape.Placement, request.currentPlacements[pieceIndex]);
                for (var slot = 0; slot < cellCount; slot++)
                {
                    shape.Cells[slot] = model.NewIntVar(0, request.cellCount - 1,
                        $"cell_{pieceIndex}_{slot}");
                    model.AddElement(shape.Placement,
                        piece.placements.Select(placement => (long)placement.cells[slot]).ToArray(),
                        shape.Cells[slot]);
                    shape.X[slot] = model.NewIntVar(request.cellX.Min(), request.cellX.Max(),
                        $"x_{pieceIndex}_{slot}");
                    shape.Y[slot] = model.NewIntVar(request.cellY.Min(), request.cellY.Max(),
                        $"y_{pieceIndex}_{slot}");
                    model.AddElement(shape.Cells[slot], request.cellX.Select(x => (long)x).ToArray(),
                        shape.X[slot]);
                    model.AddElement(shape.Cells[slot], request.cellY.Select(y => (long)y).ToArray(),
                        shape.Y[slot]);
                    var currentCell = piece.placements[request.currentPlacements[pieceIndex]].cells[slot];
                    model.AddHint(shape.Cells[slot], currentCell);
                    model.AddHint(shape.X[slot], request.cellX[currentCell]);
                    model.AddHint(shape.Y[slot], request.cellY[currentCell]);
                    allCells.Add(shape.Cells[slot]);
                }
                shapes[pieceIndex] = shape;
            }
            model.AddAllDifferent(allCells);

            var hitCache = new Dictionary<string, BoolVar>();
            var multipliers = new List<IntVar>();
            var balancedScores = new List<IntVar>();
            var activeRules = new List<BoolVar>();
            var currentMultipliers = new List<int>();
            var totalUpper = 0;
            for (var ruleIndex = 0; ruleIndex < request.rules.Length; ruleIndex++)
            {
                var rule = request.rules[ruleIndex];
                var countHits = new List<BoolVar>();
                foreach (var target in rule.countSelf
                             ? Array.Empty<int>()
                             : rule.countTargetPieces ?? Array.Empty<int>())
                {
                    var hit = GetShapeHit(model, shapes, hitCache, rule.sourcePiece, target,
                        rule.countGeometry, $"r{ruleIndex}_count_p{target}");
                    model.AddHint(hit, CurrentShapeHit(request, rule.sourcePiece, target,
                        rule.countGeometry) ? 1 : 0);
                    countHits.Add(hit);
                }
                var countUpper = Math.Max(0, rule.multiplierByCount.Length - 1);
                var count = model.NewIntVar(0, countUpper, $"r{ruleIndex}_count");
                if (rule.countSelf) model.Add(count == 1);
                else model.Add(count == LinearExpr.Sum(countHits));
                var currentCount = rule.countSelf
                    ? 1
                    : (rule.countTargetPieces ?? Array.Empty<int>()).Count(target =>
                        CurrentShapeHit(request, rule.sourcePiece, target, rule.countGeometry));
                model.AddHint(count, currentCount);
                var raw = model.NewIntVar(0, rule.maxMultiplier, $"r{ruleIndex}_raw");
                model.AddElement(count, rule.multiplierByCount.Select(value => (long)value).ToArray(), raw);
                var currentRaw = rule.multiplierByCount[currentCount];
                model.AddHint(raw, currentRaw);
                BoolVar gate;
                var currentGate = rule.gateSelf;
                if (rule.gateSelf)
                {
                    gate = model.NewBoolVar($"r{ruleIndex}_gate_self");
                    model.Add(gate == 1);
                }
                else
                {
                    var gateHits = new List<ILiteral>();
                    foreach (var target in rule.gateTargetPieces ?? Array.Empty<int>())
                    {
                        var hit = GetShapeHit(model, shapes, hitCache, rule.sourcePiece, target,
                            rule.gateGeometry, $"r{ruleIndex}_gate_p{target}");
                        var currentHit = CurrentShapeHit(request, rule.sourcePiece, target,
                            rule.gateGeometry);
                        model.AddHint(hit, currentHit ? 1 : 0);
                        currentGate |= currentHit;
                        gateHits.Add(hit);
                    }
                    gate = Or(model, gateHits, $"r{ruleIndex}_gate");
                }
                model.AddHint(gate, currentGate ? 1 : 0);
                var multiplier = model.NewIntVar(0, rule.maxMultiplier, $"r{ruleIndex}_mul");
                model.Add(multiplier <= raw);
                model.Add(multiplier <= rule.maxMultiplier * gate);
                model.Add(multiplier >= raw - rule.maxMultiplier * (1 - gate));
                var currentMultiplier = currentGate ? currentRaw : 0;
                model.AddHint(multiplier, currentMultiplier);
                currentMultipliers.Add(currentMultiplier);
                var active = ReifyLessOrEqual(model, 1 - multiplier, 0,
                    $"r{ruleIndex}_active");
                model.AddHint(active, currentMultiplier > 0 ? 1 : 0);
                activeRules.Add(active);
                multipliers.Add(multiplier);
                var scoreTable = Enumerable.Range(0, rule.maxMultiplier + 1)
                    .Select(value => (long)Math.Round(Math.Log(1d + value) * 1000000d))
                    .ToArray();
                var balancedScore = model.NewIntVar(0, scoreTable.Last(),
                    $"r{ruleIndex}_balanced_score");
                model.AddElement(multiplier, scoreTable, balancedScore);
                model.AddHint(balancedScore, scoreTable[currentMultiplier]);
                balancedScores.Add(balancedScore);
                totalUpper += rule.maxMultiplier;
            }

            var total = model.NewIntVar(0, totalUpper, "total");
            model.Add(total == LinearExpr.Sum(multipliers));
            model.AddHint(total, currentMultipliers.Sum());
            // 精确目标与游戏侧的比较口径保持一致：先最大化各规则 (倍率+1) 的乘积
            // （用对数和表示），再比较总倍率。激活规则数只用于打破完全同分，不能反过来
            // 强迫布局追求“亮灯数量”。
            var activeTieWeight = request.rules.Length + 1L;
            var balanceWeight = (totalUpper + 1L) * activeTieWeight;
            var exactObjective = LinearExpr.Sum(balancedScores) * balanceWeight +
                                 total * activeTieWeight + LinearExpr.Sum(activeRules);

            // 第一阶段只负责开路：激活更多规则能显著减少 CP-SAT 在全零平台上的盲搜。
            // 第二阶段会换回 exactObjective，因此这不是最终目标约束。
            var phaseOneTieWeight = totalUpper + 1L;
            var maximumBalancedScore = request.rules.Sum(rule =>
                (long)Math.Round(Math.Log(1d + rule.maxMultiplier) * 1000000d));
            var phaseOneActiveWeight = (maximumBalancedScore + 1L) * phaseOneTieWeight;
            model.Maximize(LinearExpr.Sum(activeRules) * phaseOneActiveWeight +
                           LinearExpr.Sum(balancedScores) * phaseOneTieWeight + total);

            var modelBuildSeconds = watch.Elapsed.TotalSeconds;
            var remainingSeconds = Math.Max(0.1,
                request.timeLimitMs / 1000d - modelBuildSeconds);
            var phaseOneSeconds = remainingSeconds <= 14d
                ? remainingSeconds
                : Math.Min(12d, remainingSeconds * 0.7d);
            var phaseOneSolver = new CpSolver
            {
                StringParameters =
                    $"max_time_in_seconds:{phaseOneSeconds:0.###} " +
                    $"random_seed:{request.seed} num_search_workers:8 log_search_progress:false",
            };
            var phaseOneStatus = phaseOneSolver.Solve(model);
            var phaseOneFeasible = phaseOneStatus == CpSolverStatus.Feasible ||
                                   phaseOneStatus == CpSolverStatus.Optimal;
            var phaseOnePlacements = phaseOneFeasible
                ? shapes.Select(shape => (int)phaseOneSolver.Value(shape.Placement)).ToArray()
                : heuristic.Placements;
            var phaseOneMultipliers = phaseOneFeasible
                ? multipliers.Select(variable => (int)phaseOneSolver.Value(variable)).ToArray()
                : heuristic.Multipliers;

            CpSolver exactSolver = null;
            var exactStatus = CpSolverStatus.Unknown;
            var exactSeconds = Math.Max(0d, remainingSeconds - phaseOneSolver.WallTime());
            if (exactSeconds >= 0.5d)
            {
                model.Maximize(exactObjective);
                model.ClearHints();
                for (var piece = 0; piece < shapes.Length; piece++)
                    model.AddHint(shapes[piece].Placement, phaseOnePlacements[piece]);
                exactSolver = new CpSolver
                {
                    StringParameters =
                        $"max_time_in_seconds:{exactSeconds:0.###} " +
                        $"random_seed:{request.seed + 1} num_search_workers:8 log_search_progress:false",
                };
                exactStatus = exactSolver.Solve(model);
            }
            var exactFeasible = exactStatus == CpSolverStatus.Feasible ||
                                exactStatus == CpSolverStatus.Optimal;
            var chosenPlacements = phaseOnePlacements;
            var chosenMultipliers = phaseOneMultipliers;
            if (exactFeasible)
            {
                var candidateMultipliers = multipliers
                    .Select(variable => (int)exactSolver.Value(variable)).ToArray();
                if (BetterExact(candidateMultipliers, chosenMultipliers))
                {
                    chosenPlacements = shapes
                        .Select(shape => (int)exactSolver.Value(shape.Placement)).ToArray();
                    chosenMultipliers = candidateMultipliers;
                }
            }
            var response = new Response
            {
                status = exactStatus == CpSolverStatus.Optimal ? "OPTIMAL" : "FEASIBLE",
                objective = exactFeasible ? exactSolver.ObjectiveValue : 0d,
                bestBound = exactSolver?.BestObjectiveBound ?? 0d,
                elapsedSeconds = phaseOneSolver.WallTime() + (exactSolver?.WallTime() ?? 0d),
                modelBuildSeconds = modelBuildSeconds,
                totalSeconds = watch.Elapsed.TotalSeconds,
                placements = chosenPlacements,
                multipliers = chosenMultipliers,
                product = Product(chosenMultipliers),
                total = chosenMultipliers.Sum(),
            };
            return response;
        }

        private static bool BetterExact(int[] left, int[] right)
        {
            var leftProduct = Product(left);
            var rightProduct = Product(right);
            if (leftProduct != rightProduct) return leftProduct > rightProduct;
            return left.Sum() > right.Sum();
        }

        private static HeuristicSolution ImprovePlacementHint(Request request, int milliseconds)
        {
            var random = new Random(request.seed);
            var watch = Stopwatch.StartNew();
            var working = request.currentPlacements.ToArray();
            var initialPlacements = working.ToArray();
            var occupancy = new int[request.cellCount];
            for (var piece = 0; piece < request.pieces.Length; piece++)
                foreach (var cell in request.pieces[piece].placements[working[piece]].cells)
                    occupancy[cell]++;
            var current = EvaluateHeuristic(request, working);
            var best = current;
            var bestPlacements = working.ToArray();
            var iteration = 0;
            const int restartMilliseconds = 250;
            var nextRestart = restartMilliseconds;
            var restart = 0;
            while (watch.ElapsedMilliseconds < milliseconds)
            {
                if (watch.ElapsedMilliseconds >= nextRestart && nextRestart < milliseconds)
                {
                    restart++;
                    working = (restart % 2 == 0 ? bestPlacements : initialPlacements).ToArray();
                    Array.Clear(occupancy, 0, occupancy.Length);
                    for (var piece = 0; piece < request.pieces.Length; piece++)
                    foreach (var cell in request.pieces[piece].placements[working[piece]].cells)
                        occupancy[cell]++;
                    current = EvaluateHeuristic(request, working);
                    nextRestart += restartMilliseconds;
                }
                iteration++;
                // 棋盘较满时，单件移动经常没有空间。混合 1/2/3 件的 ruin-recreate
                // 邻域，既保留便宜的小步，也允许互换和局部腾挪。
                var roll = random.NextDouble();
                var moveCount = roll < 0.08d ? 3 : roll < 0.42d ? 2 : 1;
                moveCount = Math.Min(moveCount, request.pieces.Length);
                var movedPieces = new List<int>(moveCount);
                Rule focusRule = null;
                var focusTarget = -1;
                int[] focusGeometry = null;
                var focusRules = request.rules.Where(rule => rule.maxMultiplier > 0 &&
                    (rule.countTargetPieces ?? Array.Empty<int>())
                        .Concat(rule.gateTargetPieces ?? Array.Empty<int>())
                        .Any(target => target != rule.sourcePiece)).ToArray();
                if (focusRules.Length != 0 && random.NextDouble() < 0.65d)
                {
                    focusRule = focusRules[random.Next(focusRules.Length)];
                    var countTargets = (focusRule.countTargetPieces ?? Array.Empty<int>())
                        .Where(target => target != focusRule.sourcePiece).ToArray();
                    var targets = countTargets.Length != 0
                        ? countTargets
                        : (focusRule.gateTargetPieces ?? Array.Empty<int>())
                            .Where(target => target != focusRule.sourcePiece).ToArray();
                    focusGeometry = countTargets.Length != 0
                        ? focusRule.countGeometry
                        : focusRule.gateGeometry;
                    focusTarget = targets[random.Next(targets.Length)];
                    moveCount = Math.Max(2, moveCount);
                    // 先重放目标、最后重放来源，来源候选就能按通用几何关系筛选。
                    movedPieces.Add(focusTarget);
                }
                while (movedPieces.Count < moveCount - (focusRule == null ? 0 : 1))
                {
                    var piece = request.rules.Length != 0 && random.NextDouble() < 0.7d
                        ? request.rules[random.Next(request.rules.Length)].sourcePiece
                        : random.Next(request.pieces.Length);
                    if (focusRule != null && piece == focusRule.sourcePiece) continue;
                    if (!movedPieces.Contains(piece)) movedPieces.Add(piece);
                }
                if (focusRule != null) movedPieces.Add(focusRule.sourcePiece);
                var oldPlacements = movedPieces.Select(piece => working[piece]).ToArray();
                foreach (var piece in movedPieces)
                foreach (var cell in request.pieces[piece].placements[working[piece]].cells)
                    occupancy[cell]--;

                var rebuilt = true;
                var changed = false;
                for (var movedIndex = 0; movedIndex < movedPieces.Count; movedIndex++)
                {
                    var piece = movedPieces[movedIndex];
                    var candidates = request.pieces[piece].placements;
                    var start = random.Next(candidates.Length);
                    var candidate = -1;
                    for (var offset = 0; offset < candidates.Length; offset++)
                    {
                        var index = (start + offset) % candidates.Length;
                        if (candidates[index].cells.Any(cell => occupancy[cell] != 0)) continue;
                        if (focusRule != null && piece == focusRule.sourcePiece)
                        {
                            working[piece] = index;
                            if (!ShapeHit(request, working, piece, focusTarget, focusGeometry))
                                continue;
                        }
                        // 前几个合法候选随机跳过，避免每次退化成固定顺序贪心。
                        if (random.NextDouble() < 0.35d && offset + 1 < candidates.Length) continue;
                        candidate = index;
                        break;
                    }
                    if (candidate < 0)
                    {
                        rebuilt = false;
                        break;
                    }
                    working[piece] = candidate;
                    changed |= candidate != oldPlacements[movedIndex];
                    foreach (var cell in candidates[candidate].cells) occupancy[cell]++;
                }
                if (!rebuilt || !changed)
                {
                    for (var movedIndex = 0; movedIndex < movedPieces.Count; movedIndex++)
                        working[movedPieces[movedIndex]] = oldPlacements[movedIndex];
                    Array.Clear(occupancy, 0, occupancy.Length);
                    for (var piece = 0; piece < request.pieces.Length; piece++)
                    foreach (var cell in request.pieces[piece].placements[working[piece]].cells)
                        occupancy[cell]++;
                    continue;
                }
                var next = EvaluateHeuristic(request, working);
                var progress = Math.Min(1d,
                    (watch.ElapsedMilliseconds % restartMilliseconds) /
                    (double)restartMilliseconds);
                var temperature = Math.Max(0.02d, 1.5d * (1d - progress));
                var delta = HeuristicValue(next) - HeuristicValue(current);
                var accept = delta >= 0d || random.NextDouble() < Math.Exp(delta / temperature);
                if (accept)
                {
                    current = next;
                    if (BetterHeuristic(next, best))
                    {
                        best = next;
                        bestPlacements = working.ToArray();
                    }
                }
                else
                {
                    foreach (var piece in movedPieces)
                    foreach (var cell in request.pieces[piece].placements[working[piece]].cells)
                        occupancy[cell]--;
                    for (var movedIndex = 0; movedIndex < movedPieces.Count; movedIndex++)
                        working[movedPieces[movedIndex]] = oldPlacements[movedIndex];
                    foreach (var piece in movedPieces)
                    foreach (var cell in request.pieces[piece].placements[working[piece]].cells)
                        occupancy[cell]++;
                }
            }

            best.Placements = bestPlacements;
            return best;
        }

        private static HeuristicSolution EvaluateHeuristic(Request request, int[] placements)
        {
            var multipliers = new int[request.rules.Length];
            for (var ruleIndex = 0; ruleIndex < request.rules.Length; ruleIndex++)
            {
                var rule = request.rules[ruleIndex];
                var count = rule.countSelf ? 1 : (rule.countTargetPieces ?? Array.Empty<int>())
                    .Count(target => ShapeHit(request, placements, rule.sourcePiece,
                        target, rule.countGeometry));
                var gate = rule.gateSelf || (rule.gateTargetPieces ?? Array.Empty<int>())
                    .Any(target => ShapeHit(request, placements, rule.sourcePiece,
                        target, rule.gateGeometry));
                multipliers[ruleIndex] = gate
                    ? rule.multiplierByCount[Math.Min(count, rule.multiplierByCount.Length - 1)]
                    : 0;
            }
            return new HeuristicSolution
            {
                Placements = placements.ToArray(),
                Multipliers = multipliers,
                Active = multipliers.Count(value => value > 0),
                Balance = multipliers.Sum(value => Math.Log(1d + value)),
                Total = multipliers.Sum(),
            };
        }

        private static bool ShapeHit(Request request, int[] placements, int sourcePiece,
            int targetPiece, int[] geometry)
        {
            var sourceCells = request.pieces[sourcePiece].placements[placements[sourcePiece]].cells;
            var targetCells = request.pieces[targetPiece].placements[placements[targetPiece]].cells;
            return sourceCells.Any(source => targetCells.Any(target =>
                (geometry ?? Array.Empty<int>()).All(code =>
                    MatchesRelation(request, source, target, code))));
        }

        private static double HeuristicValue(HeuristicSolution solution)
        {
            // 激活数只塑造搜索地形，帮助越过大量零倍率平台；权重小于一次真正的
            // 0→1 倍率收益，因此不会取代实际的均衡度目标。
            return solution.Balance + solution.Active * 5d + solution.Total * 0.0001d;
        }

        private static bool BetterHeuristic(HeuristicSolution left, HeuristicSolution right)
        {
            if (Math.Abs(left.Balance - right.Balance) > 0.0000001d)
                return left.Balance > right.Balance;
            if (left.Total != right.Total) return left.Total > right.Total;
            return left.Active > right.Active;
        }

        private static long Product(IEnumerable<int> multipliers)
        {
            long product = 1;
            foreach (var multiplier in multipliers)
            {
                if (product > long.MaxValue / (multiplier + 1L))
                    return long.MaxValue;
                product *= multiplier + 1L;
            }
            return product;
        }

        private static bool CurrentShapeHit(Request request, int sourcePiece,
            int targetPiece, int[] geometry)
        {
            var sourceCells = request.pieces[sourcePiece]
                .placements[request.currentPlacements[sourcePiece]].cells;
            var targetCells = request.pieces[targetPiece]
                .placements[request.currentPlacements[targetPiece]].cells;
            return sourceCells.Any(source => targetCells.Any(target =>
                (geometry ?? Array.Empty<int>()).All(code =>
                    MatchesRelation(request, source, target, code))));
        }

        private static BoolVar GetShapeHit(CpModel model, ShapeVars[] shapes,
            Dictionary<string, BoolVar> cache, int sourcePiece, int targetPiece,
            int[] geometry, string name)
        {
            var codes = geometry ?? Array.Empty<int>();
            var key = $"{sourcePiece}:{targetPiece}:{string.Join(",", codes)}";
            if (cache.TryGetValue(key, out var cached)) return cached;
            var pairs = new List<ILiteral>();
            for (var sourceSlot = 0; sourceSlot < shapes[sourcePiece].Cells.Length; sourceSlot++)
            for (var targetSlot = 0; targetSlot < shapes[targetPiece].Cells.Length; targetSlot++)
            {
                var conditions = new List<ILiteral>();
                foreach (var code in codes)
                    conditions.Add(BuildGeometryCondition(model, shapes[sourcePiece], sourceSlot,
                        shapes[targetPiece], targetSlot, code,
                        $"{name}_s{sourceSlot}_t{targetSlot}_g{code}"));
                pairs.Add(AndMany(model, conditions,
                    $"{name}_s{sourceSlot}_t{targetSlot}"));
            }
            var hit = Or(model, pairs, name + "_hit");
            cache[key] = hit;
            return hit;
        }

        private static BoolVar BuildGeometryCondition(CpModel model, ShapeVars source,
            int sourceSlot, ShapeVars target, int targetSlot, int code, string name)
        {
            if (code == 7)
            {
                var always = model.NewBoolVar(name);
                model.Add(always == 1);
                return always;
            }
            if (code == 10)
            {
                var dx = model.NewIntVar(0, 1000, name + "_dx");
                var dy = model.NewIntVar(0, 1000, name + "_dy");
                model.AddAbsEquality(dx, source.X[sourceSlot] - target.X[targetSlot]);
                model.AddAbsEquality(dy, source.Y[sourceSlot] - target.Y[targetSlot]);
                return ReifyLessOrEqual(model, dx + dy, 1, name);
            }
            if (code == 1)
                return AndMany(model, new ILiteral[]
                {
                    ReifyEqual(model, source.X[sourceSlot] - target.X[targetSlot], 0, name + "_x"),
                    ReifyLessOrEqual(model, source.Y[sourceSlot] - target.Y[targetSlot], -1, name + "_y"),
                }, name);
            if (code == 2)
                return AndMany(model, new ILiteral[]
                {
                    ReifyEqual(model, source.X[sourceSlot] - target.X[targetSlot], 0, name + "_x"),
                    ReifyLessOrEqual(model, target.Y[targetSlot] - source.Y[sourceSlot], -1, name + "_y"),
                }, name);
            if (code == 3)
                return AndMany(model, new ILiteral[]
                {
                    ReifyEqual(model, source.Y[sourceSlot] - target.Y[targetSlot], 0, name + "_y"),
                    ReifyLessOrEqual(model, target.X[targetSlot] - source.X[sourceSlot], -1, name + "_x"),
                }, name);
            if (code == 4)
                return AndMany(model, new ILiteral[]
                {
                    ReifyEqual(model, source.Y[sourceSlot] - target.Y[targetSlot], 0, name + "_y"),
                    ReifyLessOrEqual(model, source.X[sourceSlot] - target.X[targetSlot], -1, name + "_x"),
                }, name);
            if (code == 5)
                return ReifyEqual(model,
                    source.X[sourceSlot] - target.X[targetSlot] - source.Y[sourceSlot] + target.Y[targetSlot],
                    0, name);
            if (code == 6)
                return ReifyEqual(model,
                    source.X[sourceSlot] - target.X[targetSlot] + source.Y[sourceSlot] - target.Y[targetSlot],
                    0, name);
            throw new InvalidDataException($"不支持的几何规则代码：{code}");
        }

        private static BoolVar ReifyEqual(CpModel model, LinearExpr expression, long value,
            string name)
        {
            var result = model.NewBoolVar(name);
            model.Add(expression == value).OnlyEnforceIf(result);
            model.Add(expression != value).OnlyEnforceIf(result.Not());
            return result;
        }

        private static BoolVar ReifyLessOrEqual(CpModel model, LinearExpr expression, long value,
            string name)
        {
            var result = model.NewBoolVar(name);
            model.Add(expression <= value).OnlyEnforceIf(result);
            model.Add(expression >= value + 1).OnlyEnforceIf(result.Not());
            return result;
        }

        private static Response SolveLegacy(Request request)
        {
            var model = new CpModel();
            var choices = new BoolVar[request.pieces.Length][];
            for (var pieceIndex = 0; pieceIndex < request.pieces.Length; pieceIndex++)
            {
                var piece = request.pieces[pieceIndex];
                choices[pieceIndex] = new BoolVar[piece.placements.Length];
                for (var placementIndex = 0; placementIndex < piece.placements.Length; placementIndex++)
                    choices[pieceIndex][placementIndex] = model.NewBoolVar($"p{pieceIndex}_{placementIndex}");
                model.Add(LinearExpr.Sum(choices[pieceIndex]) == 1);
                model.AddHint(choices[pieceIndex][request.currentPlacements[pieceIndex]], 1);
            }

            for (var cell = 0; cell < request.cellCount; cell++)
            {
                var covering = new List<ILiteral>();
                for (var pieceIndex = 0; pieceIndex < request.pieces.Length; pieceIndex++)
                for (var placementIndex = 0;
                     placementIndex < request.pieces[pieceIndex].placements.Length;
                     placementIndex++)
                    if (request.pieces[pieceIndex].placements[placementIndex].cells.Contains(cell))
                        covering.Add(choices[pieceIndex][placementIndex]);
                if (covering.Count > 1) model.Add(LinearExpr.Sum(covering) <= 1);
            }

            var multipliers = new List<IntVar>();
            var totalUpper = 0;
            var occupancy = new Dictionary<string, BoolVar>();
            foreach (var rule in request.rules ?? Array.Empty<Rule>())
            {
                if (request.version == 2)
                {
                    var compactMultiplier = BuildCompactRule(model, request, choices, occupancy,
                        rule, multipliers.Count);
                    multipliers.Add(compactMultiplier);
                    totalUpper += rule.maxMultiplier;
                    continue;
                }
                var sourceChoices = choices[rule.sourcePiece];
                var activeCountFeatures = new List<IntVar>();
                var activeGates = new List<IntVar>();
                for (var sourcePlacement = 0; sourcePlacement < rule.sourceOptions.Length; sourcePlacement++)
                {
                    var sourceSelected = sourceChoices[sourcePlacement];
                    var option = rule.sourceOptions[sourcePlacement];
                    foreach (var feature in option.countFeatures ?? Array.Empty<Feature>())
                    {
                        var active = BuildFeature(model, choices, feature,
                            $"r{multipliers.Count}_s{sourcePlacement}_f{activeCountFeatures.Count}");
                        activeCountFeatures.Add(And(model, sourceSelected, active,
                            $"r{multipliers.Count}_count_{activeCountFeatures.Count}"));
                    }
                    var gate = BuildFeature(model, choices, option.gateFeature,
                        $"r{multipliers.Count}_s{sourcePlacement}_gate");
                    activeGates.Add(And(model, sourceSelected, gate,
                        $"r{multipliers.Count}_active_gate_{sourcePlacement}"));
                }

                var countUpper = Math.Max(0, rule.multiplierByCount.Length - 1);
                var count = model.NewIntVar(0, countUpper, $"r{multipliers.Count}_count");
                model.Add(count == LinearExpr.Sum(activeCountFeatures));
                var raw = model.NewIntVar(0, rule.maxMultiplier, $"r{multipliers.Count}_raw");
                model.AddElement(count, rule.multiplierByCount.Select(value => (long)value).ToArray(), raw);
                var hasGate = model.NewBoolVar($"r{multipliers.Count}_gate");
                if (activeGates.Count == 0)
                    model.Add(hasGate == 0);
                else
                    model.Add(hasGate == LinearExpr.Sum(activeGates));
                var multiplier = model.NewIntVar(0, rule.maxMultiplier, $"r{multipliers.Count}_mul");
                model.Add(multiplier <= raw);
                model.Add(multiplier <= rule.maxMultiplier * hasGate);
                model.Add(multiplier >= raw - rule.maxMultiplier * (1 - hasGate));
                multipliers.Add(multiplier);
                totalUpper += rule.maxMultiplier;
            }

            var factors = new List<IntVar>();
            foreach (var multiplier in multipliers)
            {
                var factor = model.NewIntVar(1, multiplier.Proto.Domain.Last() + 1, multiplier.Name() + "_factor");
                model.Add(factor == multiplier + 1);
                factors.Add(factor);
            }

            long productUpper = 1;
            foreach (var rule in request.rules ?? Array.Empty<Rule>())
            {
                if (productUpper > long.MaxValue / Math.Max(1, rule.maxMultiplier + 1L))
                    throw new InvalidDataException("目标乘积超过64位整数范围");
                productUpper *= Math.Max(1, rule.maxMultiplier + 1L);
            }
            var product = model.NewIntVar(1, Math.Max(1, productUpper), "product");
            if (factors.Count == 0)
                model.Add(product == 1);
            else
                model.AddMultiplicationEquality(product, factors);
            var total = model.NewIntVar(0, totalUpper, "total");
            model.Add(total == LinearExpr.Sum(multipliers));
            var tieWeight = totalUpper + 1L;
            if (productUpper > (long.MaxValue - totalUpper) / tieWeight)
                throw new InvalidDataException("组合目标超过64位整数范围");
            model.Maximize(product * tieWeight + total);

            var solver = new CpSolver
            {
                StringParameters =
                    $"max_time_in_seconds:{Math.Max(0.1, request.timeLimitMs / 1000d):0.###} " +
                    $"random_seed:{request.seed} num_search_workers:8 log_search_progress:false",
            };
            var status = solver.Solve(model);
            var response = new Response
            {
                status = status.ToString().ToUpperInvariant(),
                objective = solver.ObjectiveValue,
                bestBound = solver.BestObjectiveBound,
                elapsedSeconds = solver.WallTime(),
            };
            if (status != CpSolverStatus.Feasible && status != CpSolverStatus.Optimal)
                return response;

            response.placements = choices.Select(options =>
                Array.FindIndex(options, option => solver.BooleanValue(option))).ToArray();
            response.multipliers = multipliers.Select(variable => (int)solver.Value(variable)).ToArray();
            response.product = solver.Value(product);
            response.total = (int)solver.Value(total);
            return response;
        }

        private static IntVar BuildCompactRule(CpModel model, Request request,
            BoolVar[][] choices, Dictionary<string, BoolVar> occupancy, Rule rule, int ruleIndex)
        {
            var countHits = rule.countSelf
                ? new List<BoolVar>()
                : BuildCompactHits(model, request, choices, occupancy, rule.sourcePiece,
                    rule.countGeometry, rule.countTargetPieces, $"r{ruleIndex}_count");
            var countUpper = Math.Max(0, rule.multiplierByCount.Length - 1);
            var count = model.NewIntVar(0, countUpper, $"r{ruleIndex}_count_value");
            if (rule.countSelf)
                model.Add(count == 1);
            else
                model.Add(count == LinearExpr.Sum(countHits));
            var raw = model.NewIntVar(0, rule.maxMultiplier, $"r{ruleIndex}_raw");
            model.AddElement(count, rule.multiplierByCount.Select(value => (long)value).ToArray(), raw);

            BoolVar gate;
            if (rule.gateSelf)
            {
                gate = model.NewBoolVar($"r{ruleIndex}_gate_self");
                model.Add(gate == 1);
            }
            else
            {
                var gateHits = BuildCompactHits(model, request, choices, occupancy,
                    rule.sourcePiece, rule.gateGeometry, rule.gateTargetPieces,
                    $"r{ruleIndex}_gate");
                gate = Or(model, gateHits.Cast<ILiteral>(), $"r{ruleIndex}_gate_any");
            }
            var multiplier = model.NewIntVar(0, rule.maxMultiplier, $"r{ruleIndex}_mul");
            model.Add(multiplier <= raw);
            model.Add(multiplier <= rule.maxMultiplier * gate);
            model.Add(multiplier >= raw - rule.maxMultiplier * (1 - gate));
            return multiplier;
        }

        private static List<BoolVar> BuildCompactHits(CpModel model, Request request,
            BoolVar[][] choices, Dictionary<string, BoolVar> occupancy, int sourcePiece,
            int[] geometry, int[] targetPieces, string name)
        {
            var regions = new BoolVar[request.cellCount];
            for (var candidate = 0; candidate < request.cellCount; candidate++)
            {
                var conditions = new List<ILiteral>();
                foreach (var code in geometry ?? Array.Empty<int>())
                {
                    var sourceCells = Enumerable.Range(0, request.cellCount)
                        .Where(source => MatchesRelation(request, source, candidate, code))
                        .Select(source => (ILiteral)GetOccupancy(model, request, choices,
                            occupancy, sourcePiece, source));
                    conditions.Add(Or(model, sourceCells,
                        $"{name}_c{candidate}_g{code}"));
                }
                regions[candidate] = AndMany(model, conditions,
                    $"{name}_region_{candidate}");
            }
            var hits = new List<BoolVar>();
            foreach (var targetPiece in targetPieces ?? Array.Empty<int>())
            {
                var cells = new List<ILiteral>();
                for (var cell = 0; cell < request.cellCount; cell++)
                {
                    var targetHere = GetOccupancy(model, request, choices, occupancy,
                        targetPiece, cell);
                    cells.Add(AndMany(model, new ILiteral[] { regions[cell], targetHere },
                        $"{name}_p{targetPiece}_c{cell}"));
                }
                hits.Add(Or(model, cells, $"{name}_p{targetPiece}_hit"));
            }
            return hits;
        }

        private static BoolVar GetOccupancy(CpModel model, Request request,
            BoolVar[][] choices, Dictionary<string, BoolVar> cache, int piece, int cell)
        {
            var key = $"{piece}:{cell}";
            if (cache.TryGetValue(key, out var result)) return result;
            var literals = request.pieces[piece].placements
                .Select((placement, index) => new { placement, index })
                .Where(item => item.placement.cells.Contains(cell))
                .Select(item => (ILiteral)choices[piece][item.index]);
            result = Or(model, literals, $"occ_p{piece}_c{cell}");
            cache[key] = result;
            return result;
        }

        private static bool MatchesRelation(Request request, int source, int target, int code)
        {
            var dx = request.cellX[source] - request.cellX[target];
            var dy = request.cellY[source] - request.cellY[target];
            if (code == 1) return dx == 0 && dy < 0;
            if (code == 2) return dx == 0 && dy > 0;
            if (code == 3) return dy == 0 && dx > 0;
            if (code == 4) return dy == 0 && dx < 0;
            if (code == 5) return dx == dy;
            if (code == 6) return dx == -dy;
            if (code == 7) return source != target;
            if (code == 10) return Math.Abs(dx) + Math.Abs(dy) <= 1;
            if (code == 11) return Math.Abs(dx) + Math.Abs(dy) > 1;
            return false;
        }

        private static BoolVar Or(CpModel model, IEnumerable<ILiteral> source, string name)
        {
            var literals = source.Distinct().ToList();
            var result = model.NewBoolVar(name);
            if (literals.Count == 0)
            {
                model.Add(result == 0);
                return result;
            }
            model.AddBoolOr(literals).OnlyEnforceIf(result);
            foreach (var literal in literals) model.AddImplication(literal, result);
            return result;
        }

        private static BoolVar AndMany(CpModel model, IEnumerable<ILiteral> source, string name)
        {
            var literals = source.Distinct().ToList();
            var result = model.NewBoolVar(name);
            if (literals.Count == 0)
            {
                model.Add(result == 1);
                return result;
            }
            model.AddBoolAnd(literals).OnlyEnforceIf(result);
            var reverse = literals.Select(literal => literal.Not()).ToList();
            reverse.Add(result);
            model.AddBoolOr(reverse);
            return result;
        }

        private static BoolVar BuildFeature(CpModel model, BoolVar[][] choices, Feature feature,
            string name)
        {
            var candidates = new List<ILiteral>();
            foreach (var term in feature?.terms ?? Array.Empty<Term>())
            {
                var flags = Convert.FromBase64String(term.placementFlags ?? string.Empty);
                for (var placement = 0; placement < choices[term.piece].Length; placement++)
                    if ((flags[placement >> 3] & (1 << (placement & 7))) != 0)
                        candidates.Add(choices[term.piece][placement]);
            }
            var active = model.NewBoolVar(name);
            if (candidates.Count == 0)
            {
                model.Add(active == 0);
                return active;
            }
            model.AddBoolOr(candidates).OnlyEnforceIf(active);
            foreach (var candidate in candidates) model.AddImplication(candidate, active);
            return active;
        }

        private static BoolVar And(CpModel model, BoolVar left, BoolVar right, string name)
        {
            var result = model.NewBoolVar(name);
            model.Add(result <= left);
            model.Add(result <= right);
            model.Add(result >= left + right - 1);
            return result;
        }

        private static void Validate(Request request)
        {
            if (request.pieces == null || request.pieces.Length == 0)
                throw new InvalidDataException("没有待排布功法");
            if (request.currentPlacements == null || request.currentPlacements.Length != request.pieces.Length)
                throw new InvalidDataException("当前布局长度错误");
            for (var i = 0; i < request.pieces.Length; i++)
            {
                if (request.pieces[i].placements == null || request.pieces[i].placements.Length == 0)
                    throw new InvalidDataException($"功法{i}没有合法姿态");
                if (request.currentPlacements[i] < 0 ||
                    request.currentPlacements[i] >= request.pieces[i].placements.Length)
                    throw new InvalidDataException($"功法{i}当前姿态越界");
            }
            foreach (var rule in request.rules ?? Array.Empty<Rule>())
            {
                if (rule.sourcePiece < 0 || rule.sourcePiece >= request.pieces.Length)
                    throw new InvalidDataException("规则来源越界");
                if (request.version == 2)
                {
                    if (request.cellX == null || request.cellY == null ||
                        request.cellX.Length != request.cellCount ||
                        request.cellY.Length != request.cellCount)
                        throw new InvalidDataException("棋盘坐标长度错误");
                    if ((rule.countTargetPieces ?? Array.Empty<int>()).Any(piece =>
                            piece < 0 || piece >= request.pieces.Length) ||
                        (rule.gateTargetPieces ?? Array.Empty<int>()).Any(piece =>
                            piece < 0 || piece >= request.pieces.Length))
                        throw new InvalidDataException("规则目标越界");
                    if (rule.multiplierByCount == null || rule.multiplierByCount.Length == 0)
                        throw new InvalidDataException("规则缺少倍率表");
                    continue;
                }
                if (rule.sourceOptions == null ||
                    rule.sourceOptions.Length != request.pieces[rule.sourcePiece].placements.Length)
                    throw new InvalidDataException("规则来源姿态数量错误");
                if (rule.multiplierByCount == null || rule.multiplierByCount.Length == 0)
                    throw new InvalidDataException("规则缺少倍率表");
            }
        }
    }
}
