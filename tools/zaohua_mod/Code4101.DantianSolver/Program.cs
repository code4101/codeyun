using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using Google.OrTools.Sat;
using Newtonsoft.Json;
using Code4101.Dantian.Common;

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
        public int[] priorityOrder = Array.Empty<int>();
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
        public string key = null;
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
        public int[] targetCounts;
        public long product;
        public int total;
        public double objective;
        public double bestBound;
        public double elapsedSeconds;
        public double modelBuildSeconds;
        public double totalSeconds;
        public string phaseOneStatus;
        public double phaseOneSeconds;
        public string exactStatus;
        public double exactSeconds;
        public string resultSource;
        public int encodedPriorityCount;
    }

    internal static class Program
    {
        private static int Main(string[] args)
        {
            Console.InputEncoding = Encoding.UTF8;
            Console.OutputEncoding = Encoding.UTF8;
            if (args.Length == 1 && args[0] == "--self-test-priority-order")
                return SelfTestPriorityOrder();
            if (args.Length == 1 && args[0] == "--self-test-satisfaction-floor")
                return SelfTestSatisfactionFloor();
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

        private static int SelfTestPriorityOrder()
        {
            var saved = new[] { "a", "b", "c" };
            var available = DantianPriorityOrder.ForAvailable(saved, new[] { "c", "a", "d" });
            if (!available.SequenceEqual(new[] { "a", "c", "d" }))
                return WriteError("priority available merge failed");
            var persisted = DantianPriorityOrder.ForSave(saved, new[] { "c", "a", "d" });
            if (!persisted.SequenceEqual(new[] { "c", "a", "d", "b" }))
                return WriteError("priority save merge failed");
            var reloaded = DantianPriorityOrder.ForAvailable(persisted,
                new[] { "a", "c", "d", "e" });
            if (!reloaded.SequenceEqual(new[] { "c", "a", "d", "e" }))
                return WriteError("priority reload/new append failed");
            Console.Out.Write("priority order self-test passed");
            return 0;
        }

        private static int SelfTestSatisfactionFloor()
        {
            var request = new Request
            {
                priorityOrder = new[] { 0, 1, 2 },
                rules = Enumerable.Range(0, 3).Select(index => new Rule
                {
                    name = $"r{index}",
                    maxMultiplier = 1,
                    gateSelf = true,
                }).ToArray(),
            };
            var targets = new[] { 1, 1, 1 };
            var floor = new[] { 0, 1, 1 };
            var priorityGainButTotalLoss = new[] { 1, 0, 0 };
            if (BetterExact(request, priorityGainButTotalLoss, targets, floor, targets))
                return WriteError("total floor allowed an exact regression");
            var equalTotalPriorityGain = new[] { 1, 1, 0 };
            if (!BetterExact(request, equalTotalPriorityGain, targets, floor, targets))
                return WriteError("total floor rejected a non-regressing priority gain");
            var floorHeuristic = new HeuristicSolution
            {
                Multipliers = floor,
                TargetCounts = targets,
                Total = 2,
            };
            var regressingHeuristic = new HeuristicSolution
            {
                Multipliers = priorityGainButTotalLoss,
                TargetCounts = targets,
                Total = 1,
            };
            if (BetterHeuristic(request, regressingHeuristic, floorHeuristic))
                return WriteError("total floor allowed a heuristic regression");
            Console.Out.Write("satisfaction floor self-test passed");
            return 0;
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
            internal int[] TargetCounts;
            internal int Active;
            internal double Balance;
            internal int Total;
        }

        private static Response SolveShapeModel(Request request)
        {
            var watch = Stopwatch.StartNew();
            var placementCount = request.pieces.Sum(piece => piece.placements.Length);
            var isLargeLayout = request.pieces.Length >= 20 || placementCount >= 5000;
            // 实盘大模型中 CP-SAT 在数十秒内通常仍为 UNKNOWN；可行解和质量提升
            // 实际来自结构化邻域搜索。因此把主要预算交给能持续产出改进的启发式，
            // 只保留一个短 CP 增益窗口。小模型仍让 CP-SAT 获取主要预算，以便
            // 快速求得并证明最优解。
            var heuristicMilliseconds = isLargeLayout
                ? Math.Max(300, request.timeLimitMs - 900)
                : Math.Min(1500, Math.Max(300, request.timeLimitMs / 10));
            var heuristic = ImprovePlacementHint(request, heuristicMilliseconds);
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
            var targetCounts = new List<IntVar>();
            var benefitScores = new List<IntVar>();
            var benefitTotals = new List<IntVar>();
            var balancedScores = new List<IntVar>();
            var activeRules = new List<BoolVar>();
            var currentMultipliers = new List<int>();
            var currentTargetCounts = new List<int>();
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
                var gateHitVariables = new List<BoolVar>();
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
                        gateHitVariables.Add(hit);
                        gateHits.Add(hit);
                    }
                    gate = Or(model, gateHits, $"r{ruleIndex}_gate");
                }
                model.AddHint(gate, currentGate ? 1 : 0);
                var targetUpper = rule.gateSelf
                    ? 1
                    : (rule.gateTargetPieces ?? Array.Empty<int>()).Length;
                var targetCount = model.NewIntVar(0, targetUpper,
                    $"r{ruleIndex}_target_count");
                if (rule.gateSelf) model.Add(targetCount == 1);
                else model.Add(targetCount == LinearExpr.Sum(gateHitVariables));
                var currentTargetCount = rule.gateSelf
                    ? 1
                    : (rule.gateTargetPieces ?? Array.Empty<int>()).Count(target =>
                        CurrentShapeHit(request, rule.sourcePiece, target, rule.gateGeometry));
                model.AddHint(targetCount, currentTargetCount);
                targetCounts.Add(targetCount);
                currentTargetCounts.Add(currentTargetCount);
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
                var benefitScore = model.NewIntVar(0, scoreTable.Last() * targetUpper,
                    $"r{ruleIndex}_benefit_score");
                model.AddMultiplicationEquality(benefitScore,
                    new IntVar[] { balancedScore, targetCount });
                model.AddHint(benefitScore, scoreTable[currentMultiplier] * currentTargetCount);
                benefitScores.Add(benefitScore);
                var benefitTotal = model.NewIntVar(0, rule.maxMultiplier * targetUpper,
                    $"r{ruleIndex}_benefit_total");
                model.AddMultiplicationEquality(benefitTotal,
                    new IntVar[] { multiplier, targetCount });
                model.AddHint(benefitTotal, currentMultiplier * currentTargetCount);
                benefitTotals.Add(benefitTotal);
                totalUpper += rule.maxMultiplier * targetUpper;
            }

            var total = model.NewIntVar(0, totalUpper, "total");
            model.Add(total == LinearExpr.Sum(benefitTotals));
            model.AddHint(total, currentMultipliers.Zip(currentTargetCounts,
                (multiplier, targets) => multiplier * targets).Sum());
            // 用户只提供规则顺序；模型根据每条规则的严格上界生成混合进制系数。
            // 因而前一条提高 1，必然胜过后续所有规则与最终总收益的总和。
            var priorityOrder = NormalizePriorityOrder(request);
            var encodedPriorityCount = 0;
            var encodedRange = 1L;
            for (var position = 0; position < priorityOrder.Length; position++)
            {
                var ruleIndex = priorityOrder[position];
                var rule = request.rules[ruleIndex];
                var targetUpper = rule.gateSelf
                    ? 1L
                    : (rule.gateTargetPieces ?? Array.Empty<int>()).LongLength;
                var upper = checked((long)rule.maxMultiplier * targetUpper);
                if (encodedRange > long.MaxValue / (upper + 1L)) break;
                encodedRange *= upper + 1L;
                encodedPriorityCount++;
            }
            // CP-SAT 使用可安全编码的最高优先级前缀。完整顺序仍由启发式与最终
            // BetterExact 比较，因此尾部不会反向覆盖任何更高优先级结果。
            LinearExpr exactObjective = total * 0L;
            var scale = 1L;
            for (var position = encodedPriorityCount - 1; position >= 0; position--)
            {
                var ruleIndex = priorityOrder[position];
                exactObjective += benefitTotals[ruleIndex] * scale;
                var rule = request.rules[ruleIndex];
                var targetUpper = rule.gateSelf
                    ? 1L
                    : (rule.gateTargetPieces ?? Array.Empty<int>()).LongLength;
                var upper = checked((long)rule.maxMultiplier * targetUpper);
                scale *= upper + 1L;
            }
            model.Maximize(exactObjective);

            var modelBuildSeconds = watch.Elapsed.TotalSeconds;
            var remainingSeconds = Math.Max(0.1,
                request.timeLimitMs / 1000d - modelBuildSeconds);
            var phaseOneSeconds = remainingSeconds;
            var phaseOneSolver = new CpSolver
            {
                StringParameters =
                    $"max_time_in_seconds:{phaseOneSeconds:0.###} " +
                    $"random_seed:{request.seed} num_search_workers:8 " +
                    "search_branching:HINT_SEARCH log_search_progress:false",
            };
            var phaseOneStatus = phaseOneSolver.Solve(model);
            var phaseOneFeasible = phaseOneStatus == CpSolverStatus.Feasible ||
                                   phaseOneStatus == CpSolverStatus.Optimal;
            var phaseOnePlacements = heuristic.Placements;
            var phaseOneMultipliers = heuristic.Multipliers;
            var phaseOneTargetCounts = heuristic.TargetCounts;
            var phaseOneImproved = false;
            if (phaseOneFeasible)
            {
                var candidatePlacements = shapes
                    .Select(shape => (int)phaseOneSolver.Value(shape.Placement)).ToArray();
                var candidateMultipliers = multipliers
                    .Select(variable => (int)phaseOneSolver.Value(variable)).ToArray();
                var candidateTargets = targetCounts
                    .Select(variable => (int)phaseOneSolver.Value(variable)).ToArray();
                if (BetterExact(request, candidateMultipliers, candidateTargets,
                        phaseOneMultipliers, phaseOneTargetCounts))
                {
                    phaseOnePlacements = candidatePlacements;
                    phaseOneMultipliers = candidateMultipliers;
                    phaseOneTargetCounts = candidateTargets;
                    phaseOneImproved = true;
                }
            }

            var chosenPlacements = phaseOnePlacements;
            var chosenMultipliers = phaseOneMultipliers;
            var chosenTargetCounts = phaseOneTargetCounts;
            var resultSource = phaseOneImproved ? "phase-one" : "heuristic";
            var response = new Response
            {
                status = phaseOneStatus == CpSolverStatus.Optimal ? "OPTIMAL" : "FEASIBLE",
                objective = phaseOneFeasible ? phaseOneSolver.ObjectiveValue : 0d,
                bestBound = phaseOneFeasible ? phaseOneSolver.BestObjectiveBound : 0d,
                elapsedSeconds = phaseOneSolver.WallTime(),
                modelBuildSeconds = modelBuildSeconds,
                totalSeconds = watch.Elapsed.TotalSeconds,
                phaseOneStatus = phaseOneStatus.ToString().ToUpperInvariant(),
                phaseOneSeconds = phaseOneSolver.WallTime(),
                exactStatus = "NOT_RUN",
                exactSeconds = 0d,
                resultSource = resultSource,
                encodedPriorityCount = encodedPriorityCount,
                placements = chosenPlacements,
                multipliers = chosenMultipliers,
                targetCounts = chosenTargetCounts,
                product = BenefitProduct(chosenMultipliers, chosenTargetCounts),
                total = chosenMultipliers.Zip(chosenTargetCounts,
                    (multiplier, targets) => multiplier * targets).Sum(),
            };
            return response;
        }

        private static bool BetterExact(Request request, int[] left, int[] leftTargets,
            int[] right, int[] rightTargets)
        {
            var leftTotal = left.Zip(leftTargets, (value, targets) => value * targets).Sum();
            var rightTotal = right.Zip(rightTargets, (value, targets) => value * targets).Sum();
            if (leftTotal < rightTotal) return false;
            foreach (var ruleIndex in NormalizePriorityOrder(request))
            {
                var leftBenefit = left[ruleIndex] * leftTargets[ruleIndex];
                var rightBenefit = right[ruleIndex] * rightTargets[ruleIndex];
                if (leftBenefit != rightBenefit) return leftBenefit > rightBenefit;
            }
            return leftTotal > rightTotal;
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
                var delta = HeuristicValue(request, next) - HeuristicValue(request, current);
                var accept = delta >= 0d || random.NextDouble() < Math.Exp(delta / temperature);
                if (accept)
                {
                    current = next;
                    if (BetterHeuristic(request, next, best))
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
            var targetCounts = new int[request.rules.Length];
            for (var ruleIndex = 0; ruleIndex < request.rules.Length; ruleIndex++)
            {
                var rule = request.rules[ruleIndex];
                var count = rule.countSelf ? 1 : (rule.countTargetPieces ?? Array.Empty<int>())
                    .Count(target => ShapeHit(request, placements, rule.sourcePiece,
                        target, rule.countGeometry));
                var targetCount = rule.gateSelf ? 1 :
                    (rule.gateTargetPieces ?? Array.Empty<int>()).Count(target =>
                        ShapeHit(request, placements, rule.sourcePiece,
                            target, rule.gateGeometry));
                var gate = targetCount > 0;
                multipliers[ruleIndex] = gate
                    ? rule.multiplierByCount[Math.Min(count, rule.multiplierByCount.Length - 1)]
                    : 0;
                targetCounts[ruleIndex] = targetCount;
            }
            return new HeuristicSolution
            {
                Placements = placements.ToArray(),
                Multipliers = multipliers,
                TargetCounts = targetCounts,
                Active = multipliers.Zip(targetCounts,
                    (value, targets) => value > 0 ? targets : 0).Sum(),
                Balance = multipliers.Zip(targetCounts,
                    (value, targets) => Math.Log(1d + value) * targets).Sum(),
                Total = multipliers.Zip(targetCounts,
                    (value, targets) => value * targets).Sum(),
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

        private static double HeuristicValue(Request request, HeuristicSolution solution)
        {
            // 激活数只塑造搜索地形，帮助越过大量零倍率平台；权重小于一次真正的
            // 0→1 倍率收益，因此不会取代实际的均衡度目标。
            // double 只用于退火接受概率；最终优劣仍由 BetterHeuristic 的逐项比较决定。
            var value = 0d;
            foreach (var ruleIndex in NormalizePriorityOrder(request))
            {
                var rule = request.rules[ruleIndex];
                var upper = rule.maxMultiplier * (rule.gateSelf
                    ? 1
                    : (rule.gateTargetPieces ?? Array.Empty<int>()).Length);
                value = value * (upper + 1d) +
                        solution.Multipliers[ruleIndex] * solution.TargetCounts[ruleIndex];
            }
            return value;
        }

        private static bool BetterHeuristic(Request request, HeuristicSolution left,
            HeuristicSolution right)
        {
            // 历史/当前满意解既是词典序起点，也是总收益硬下限。允许搜索过程
            // 暂时走低，但只有总收益不下降且优先级严格改善的候选才能替换 best。
            if (left.Total < right.Total) return false;
            foreach (var ruleIndex in NormalizePriorityOrder(request))
            {
                var leftBenefit = left.Multipliers[ruleIndex] * left.TargetCounts[ruleIndex];
                var rightBenefit = right.Multipliers[ruleIndex] * right.TargetCounts[ruleIndex];
                if (leftBenefit != rightBenefit) return leftBenefit > rightBenefit;
            }
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

        private static long BenefitProduct(int[] multipliers, int[] targetCounts)
        {
            long product = 1;
            for (var index = 0; index < multipliers.Length; index++)
            for (var target = 0; target < targetCounts[index]; target++)
            {
                if (product > long.MaxValue / (multipliers[index] + 1L))
                    return long.MaxValue;
                product *= multipliers[index] + 1L;
            }
            return product;
        }

        private static int[] NormalizePriorityOrder(Request request)
        {
            var result = new List<int>();
            foreach (var index in request.priorityOrder ?? Array.Empty<int>())
                if (index >= 0 && index < request.rules.Length && !result.Contains(index))
                    result.Add(index);
            for (var index = 0; index < request.rules.Length; index++)
                if (!result.Contains(index)) result.Add(index);
            return result.ToArray();
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
