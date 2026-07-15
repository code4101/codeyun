using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
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
        public int[] currentPlacements = Array.Empty<int>();
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
    }

    internal static class Program
    {
        private static int Main()
        {
            try
            {
                var json = Console.In.ReadToEnd();
                var request = JsonConvert.DeserializeObject<Request>(json);
                if (request == null || request.version != 1)
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
            foreach (var rule in request.rules ?? Array.Empty<Rule>())
            {
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
                if (rule.sourceOptions == null ||
                    rule.sourceOptions.Length != request.pieces[rule.sourcePiece].placements.Length)
                    throw new InvalidDataException("规则来源姿态数量错误");
                if (rule.multiplierByCount == null || rule.multiplierByCount.Length == 0)
                    throw new InvalidDataException("规则缺少倍率表");
            }
        }
    }
}
