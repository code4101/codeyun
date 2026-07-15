using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace Code4101.Zaohua.Tiandao
{
    [Serializable]
    internal sealed class DantianSolverRequest
    {
        public int version = 1;
        public int timeLimitMs;
        public int seed;
        public int cellCount;
        public int[] currentPlacements;
        public DantianSolverPiece[] pieces;
        public DantianSolverRule[] rules;
    }

    [Serializable]
    internal sealed class DantianSolverPiece
    {
        public string name;
        public DantianSolverPlacement[] placements;
    }

    [Serializable]
    internal sealed class DantianSolverPlacement
    {
        public int[] cells;
    }

    [Serializable]
    internal sealed class DantianSolverRule
    {
        public string name;
        public int sourcePiece;
        public int maxMultiplier;
        public int[] multiplierByCount;
        public DantianSolverSourceOption[] sourceOptions;
    }

    [Serializable]
    internal sealed class DantianSolverSourceOption
    {
        public DantianSolverFeature[] countFeatures;
        public DantianSolverFeature gateFeature;
    }

    [Serializable]
    internal sealed class DantianSolverFeature
    {
        public DantianSolverTerm[] terms;
    }

    [Serializable]
    internal sealed class DantianSolverTerm
    {
        public int piece;
        public string placementFlags;
    }

    [Serializable]
    internal sealed class DantianSolverResponse
    {
        public int version = 0;
        public string status = null;
        public string error = null;
        public int[] placements = null;
        public int[] multipliers = null;
        public long product = 0;
        public int total = 0;
        public double objective = 0;
        public double bestBound = 0;
        public double elapsedSeconds = 0;
    }

    internal sealed class DantianSolverRunResult
    {
        internal DantianSolverResponse Response;
        internal string Error;
        internal string StandardError;
    }

    internal sealed class DantianSolverBuildResult
    {
        internal DantianSolverRequest Request;
        internal string Error;
        internal long ElapsedMilliseconds;
    }

    internal static class DantianCpSatBridge
    {
        private const int ProtocolVersion = 1;

        internal static Task<DantianSolverBuildResult> BuildAsync(DantianLayoutProblem problem,
            DantianLayoutScore currentScore, int timeLimitMs, int seed,
            CancellationToken cancellationToken)
        {
            return Task.Run(() =>
            {
                var watch = Stopwatch.StartNew();
                var succeeded = TryBuildRequest(problem, currentScore, timeLimitMs, seed,
                    cancellationToken, out var request, out var error);
                return new DantianSolverBuildResult
                {
                    Request = succeeded ? request : null,
                    Error = error,
                    ElapsedMilliseconds = watch.ElapsedMilliseconds,
                };
            }, cancellationToken);
        }

        private static bool TryBuildRequest(DantianLayoutProblem problem,
            DantianLayoutScore currentScore, int timeLimitMs, int seed,
            CancellationToken cancellationToken,
            out DantianSolverRequest request, out string error)
        {
            request = null;
            error = null;
            try
            {
                var maskCache = new Dictionary<string, HashSet<int>>();
                var rules = new List<DantianSolverRule>();
                var currentMultiplierIndex = 0;
                for (var sourcePieceIndex = 0;
                     sourcePieceIndex < problem.Pieces.Count;
                    sourcePieceIndex++)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    var sourcePiece = problem.Pieces[sourcePieceIndex];
                    for (var ruleIndex = 0; ruleIndex < sourcePiece.Rules.Count; ruleIndex++)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        var cfg = sourcePiece.Rules[ruleIndex];
                        var parsed = ParseMultiplier(cfg.upMulType, cfg.maxUpMul);
                        var unconditionalGate = IsUnconditionalTargetEffect(cfg.targetEff);
                        var options = new DantianSolverSourceOption[sourcePiece.Placements.Count];
                        var maximumCount = 0;
                        for (var sourcePlacementIndex = 0;
                             sourcePlacementIndex < sourcePiece.Placements.Count;
                             sourcePlacementIndex++)
                        {
                            cancellationToken.ThrowIfCancellationRequested();
                            var upMasks = new HashSet<int>[problem.Pieces.Count];
                            var gateMasks = new HashSet<int>[problem.Pieces.Count];
                            for (var targetPieceIndex = 0;
                                 targetPieceIndex < problem.Pieces.Count;
                                 targetPieceIndex++)
                            {
                                if (targetPieceIndex == sourcePieceIndex)
                                {
                                    upMasks[targetPieceIndex] = new HashSet<int>();
                                    gateMasks[targetPieceIndex] = new HashSet<int>();
                                    continue;
                                }
                                upMasks[targetPieceIndex] = GetCachedMask(maskCache,
                                    cfg.upMulEff, problem, sourcePieceIndex,
                                    sourcePlacementIndex, targetPieceIndex, cancellationToken);
                                gateMasks[targetPieceIndex] = unconditionalGate
                                    ? new HashSet<int>()
                                    : GetCachedMask(maskCache, cfg.targetEff, problem,
                                        sourcePieceIndex, sourcePlacementIndex, targetPieceIndex,
                                        cancellationToken);
                            }

                            var features = BuildCountFeatures(problem, parsed, upMasks,
                                sourcePieceIndex);
                            maximumCount = Math.Max(maximumCount, features.Count);
                            options[sourcePlacementIndex] = new DantianSolverSourceOption
                            {
                                countFeatures = features.ToArray(),
                                // targetEff=0 means the rule has no separate target gate.  The
                                // native evaluator treats the source piece itself as a valid
                                // target, so requiring another piece here incorrectly forces the
                                // multiplier to zero (for example, 涌泉化春霖 x5 -> x0).
                                gateFeature = unconditionalGate
                                    ? BuildAlwaysFeature(sourcePieceIndex,
                                        sourcePiece.Placements.Count)
                                    : BuildGateFeature(problem, gateMasks, sourcePieceIndex),
                            };
                        }

                        var table = Enumerable.Range(0, maximumCount + 1)
                            .Select(parsed.GetMultiplier).ToArray();
                        var maxMultiplier = table.Length == 0 ? 0 : table.Max();
                        var solverRule = new DantianSolverRule
                        {
                            name = $"{sourcePiece.Name}#{ruleIndex + 1}",
                            sourcePiece = sourcePieceIndex,
                            maxMultiplier = maxMultiplier,
                            multiplierByCount = table,
                            sourceOptions = options,
                        };
                        var modeledCurrent = EvaluateCurrentRule(problem, solverRule);
                        var nativeCurrent = currentMultiplierIndex < currentScore.Multipliers.Count
                            ? currentScore.Multipliers[currentMultiplierIndex]
                            : -1;
                        if (modeledCurrent != nativeCurrent)
                        {
                            error = $"规则模型校验失败：{solverRule.name}，" +
                                    $"模型x{modeledCurrent}，原生x{nativeCurrent}";
                            return false;
                        }
                        currentMultiplierIndex++;
                        rules.Add(solverRule);
                    }
                }

                request = new DantianSolverRequest
                {
                    version = ProtocolVersion,
                    timeLimitMs = timeLimitMs,
                    seed = seed,
                    cellCount = problem.Board.Count,
                    currentPlacements = problem.CurrentPlacements.ToArray(),
                    pieces = problem.Pieces.Select(piece => new DantianSolverPiece
                    {
                        name = piece.Name,
                        placements = piece.Placements.Select(placement => new DantianSolverPlacement
                        {
                            cells = placement.CellIndices,
                        }).ToArray(),
                    }).ToArray(),
                    rules = rules.ToArray(),
                };
                return true;
            }
            catch (OperationCanceledException)
            {
                error = "求解已取消";
                return false;
            }
            catch (Exception exception)
            {
                error = exception.GetType().Name + ": " + exception.Message;
                return false;
            }
        }

        internal static Task<DantianSolverRunResult> RunAsync(DantianSolverRequest request,
            CancellationToken cancellationToken)
        {
            return Task.Run(() => Run(request, cancellationToken), cancellationToken);
        }

        internal static bool ValidateSolution(DantianLayoutProblem problem, int[] placements,
            out string error)
        {
            error = null;
            if (placements == null || placements.Length != problem.Pieces.Count)
            {
                error = "求解器返回的姿态数量错误";
                return false;
            }
            var occupied = new HashSet<int>();
            for (var pieceIndex = 0; pieceIndex < problem.Pieces.Count; pieceIndex++)
            {
                var placementIndex = placements[pieceIndex];
                if (placementIndex < 0 || placementIndex >= problem.Pieces[pieceIndex].Placements.Count)
                {
                    error = $"求解器返回的第{pieceIndex + 1}件姿态越界";
                    return false;
                }
                foreach (var cell in problem.Pieces[pieceIndex].Placements[placementIndex].CellIndices)
                    if (!occupied.Add(cell))
                    {
                        error = $"求解器返回的布局在格位{cell}重叠";
                        return false;
                    }
            }
            return true;
        }

        private static DantianSolverRunResult Run(DantianSolverRequest request,
            CancellationToken cancellationToken)
        {
            var result = new DantianSolverRunResult();
            var worker = FindWorker();
            if (worker == null)
            {
                result.Error = "未找到随附的丹田求解器";
                return result;
            }
            try
            {
                var startInfo = new ProcessStartInfo
                {
                    FileName = worker,
                    WorkingDirectory = Path.GetDirectoryName(worker),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardInput = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                using (var process = Process.Start(startInfo))
                {
                    if (process == null)
                    {
                        result.Error = "丹田求解器启动失败";
                        return result;
                    }
                    var stdout = process.StandardOutput.ReadToEndAsync();
                    var stderr = process.StandardError.ReadToEndAsync();
                    process.StandardInput.Write(JsonUtility.ToJson(request));
                    process.StandardInput.Close();
                    var timeout = Math.Max(3000, request.timeLimitMs + 5000);
                    var elapsed = 0;
                    while (!process.WaitForExit(100))
                    {
                        elapsed += 100;
                        if (!cancellationToken.IsCancellationRequested && elapsed < timeout) continue;
                        try { process.Kill(); } catch { }
                        result.Error = cancellationToken.IsCancellationRequested ? "求解已取消" : "求解器响应超时";
                        return result;
                    }
                    Task.WaitAll(new Task[] { stdout, stderr }, 2000);
                    result.StandardError = stderr.IsCompleted ? stderr.Result : string.Empty;
                    var output = stdout.IsCompleted ? stdout.Result : string.Empty;
                    if (string.IsNullOrWhiteSpace(output))
                    {
                        result.Error = $"求解器没有返回结果（退出码{process.ExitCode}）";
                        return result;
                    }
                    result.Response = JsonUtility.FromJson<DantianSolverResponse>(output);
                    if (result.Response == null)
                        result.Error = "无法解析求解器结果";
                    else if (!string.IsNullOrEmpty(result.Response.error))
                        result.Error = result.Response.error;
                    return result;
                }
            }
            catch (Exception exception)
            {
                result.Error = exception.GetType().Name + ": " + exception.Message;
                return result;
            }
        }

        private static string FindWorker()
        {
            var pluginDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            if (string.IsNullOrEmpty(pluginDir)) return null;
            var candidates = new[]
            {
                Path.Combine(pluginDir, "solver", "Code4101.DantianSolver.exe"),
                Path.Combine(pluginDir, "Code4101.DantianSolver.exe"),
            };
            return candidates.FirstOrDefault(File.Exists);
        }

        private static HashSet<int> GetMask(string effect,
            DantianLayoutProblem problem, int sourcePieceIndex, int sourcePlacementIndex,
            int targetPieceIndex, CancellationToken cancellationToken)
        {
            var sourcePiece = problem.Pieces[sourcePieceIndex];
            var targetPiece = problem.Pieces[targetPieceIndex];
            var sourceCells = new HashSet<int>(
                sourcePiece.Placements[sourcePlacementIndex].CellIndices);
            var distances = GetDistances(problem, sourceCells);
            var mask = new HashSet<int>();
            for (var candidateCell = 0; candidateCell < problem.Board.Count; candidateCell++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (sourceCells.Contains(candidateCell)) continue;
                if (MatchesEffect(effect, problem, sourceCells, distances,
                        candidateCell, targetPiece))
                    mask.Add(candidateCell);
            }
            return mask;
        }

        private static int[] GetDistances(DantianLayoutProblem problem, HashSet<int> sourceCells)
        {
            var distances = Enumerable.Repeat(-1, problem.Board.Count).ToArray();
            var queue = new Queue<int>();
            foreach (var sourceCell in sourceCells)
            {
                distances[sourceCell] = 0;
                queue.Enqueue(sourceCell);
            }
            while (queue.Count > 0)
            {
                var cell = queue.Dequeue();
                foreach (var neighbor in problem.NeighborIndices[cell])
                {
                    if (distances[neighbor] >= 0) continue;
                    distances[neighbor] = distances[cell] + 1;
                    queue.Enqueue(neighbor);
                }
            }
            return distances;
        }

        private static bool MatchesEffect(string effect, DantianLayoutProblem problem,
            HashSet<int> sourceCells, int[] distances, int candidateCell,
            DantianLayoutPiece targetPiece)
        {
            // Native GetVaildArtMagicIdList returns the source cells for an empty/0
            // expression. A mask describes other target pieces, so none can match.
            if (string.IsNullOrWhiteSpace(effect) || effect.Trim() == "0") return false;
            return effect.Split('&').All(group => group.Split('|').Any(raw =>
                MatchesEffectToken(raw, problem, sourceCells, distances,
                    candidateCell, targetPiece)));
        }

        private static bool MatchesEffectToken(string raw, DantianLayoutProblem problem,
            HashSet<int> sourceCells, int[] distances, int candidateCell,
            DantianLayoutPiece targetPiece)
        {
            var parts = (raw ?? string.Empty).Split('#');
            if (!int.TryParse(parts[0], out var signedCode)) return true;
            var negate = signedCode < 0;
            var code = Math.Abs(signedCode);
            var index = 0;
            if (parts.Length > 1) int.TryParse(parts[1], out index);
            var candidate = problem.Board[candidateCell];
            var matched = false;
            switch (code)
            {
                case 1:
                    matched = sourceCells.Any(cell => problem.Board[cell].x == candidate.x &&
                        problem.Board[cell].y < candidate.y);
                    break;
                case 2:
                    matched = sourceCells.Any(cell => problem.Board[cell].x == candidate.x &&
                        problem.Board[cell].y > candidate.y);
                    break;
                case 3:
                    matched = sourceCells.Any(cell => problem.Board[cell].y == candidate.y &&
                        problem.Board[cell].x > candidate.x);
                    break;
                case 4:
                    matched = sourceCells.Any(cell => problem.Board[cell].y == candidate.y &&
                        problem.Board[cell].x < candidate.x);
                    break;
                case 5:
                    matched = sourceCells.Any(cell => problem.Board[cell].x - candidate.x ==
                        problem.Board[cell].y - candidate.y);
                    break;
                case 6:
                    matched = sourceCells.Any(cell => problem.Board[cell].x - candidate.x ==
                        candidate.y - problem.Board[cell].y);
                    break;
                case 7:
                    matched = true;
                    break;
                case 10:
                    matched = distances[candidateCell] == 1;
                    break;
                case 11:
                    matched = distances[candidateCell] != 1;
                    break;
                case 12:
                    matched = distances[candidateCell] > 0;
                    break;
                case 13:
                    matched = distances[candidateCell] == 1 &&
                        (targetPiece.IsArt || targetPiece.IsMagic);
                    break;
                case 50:
                    matched = targetPiece.IsMagic && targetPiece.IsAttackMagic;
                    break;
                case 51:
                    matched = targetPiece.IsMagic && targetPiece.IsDefenceMagic;
                    break;
                case 102:
                    matched = false;
                    break;
                case 100:
                    matched = targetPiece.IsArt;
                    break;
                case 101:
                    matched = targetPiece.IsMagic;
                    break;
                case 200:
                    matched = targetPiece.IsArt && targetPiece.Id.sedId == index;
                    break;
                case 201:
                    matched = targetPiece.IsMagic && targetPiece.Id.sedId == index;
                    break;
                default:
                    if (code >= 20 && code <= 28)
                        matched = targetPiece.Attribute + 20 == code;
                    else if (code >= 40 && code <= 45)
                        matched = (targetPiece.IsMagic && targetPiece.Type - 221 == code - 40) ||
                                  (targetPiece.IsArt && targetPiece.Type - 201 == code - 40);
                    else if (code >= 61 && code <= 69)
                        matched = distances[candidateCell] == code - 60;
                    else
                        matched = true;
                    break;
            }
            return negate ? !matched : matched;
        }

        private static HashSet<int> GetCachedMask(Dictionary<string, HashSet<int>> cache,
            string effect, DantianLayoutProblem problem,
            int sourcePiece, int sourcePlacement, int targetPiece,
            CancellationToken cancellationToken)
        {
            var key = $"{sourcePiece}:{sourcePlacement}:{targetPiece}:{effect ?? string.Empty}";
            if (cache.TryGetValue(key, out var mask)) return mask;
            mask = GetMask(effect, problem, sourcePiece, sourcePlacement,
                targetPiece, cancellationToken);
            cache[key] = mask;
            return mask;
        }

        private static List<DantianSolverFeature> BuildCountFeatures(DantianLayoutProblem problem,
            ParsedMultiplier parsed, HashSet<int>[] masks, int sourcePieceIndex)
        {
            if (parsed.CountKind == 1)
                return BuildGroupedFeatures(problem, masks, sourcePieceIndex,
                    cell => cell.ToString());
            if (parsed.CountKind == 2)
                return BuildGroupedFeatures(problem, masks, sourcePieceIndex,
                    cell => problem.Board[cell].y.ToString());
            if (parsed.CountKind == 3)
                return BuildGroupedFeatures(problem, masks, sourcePieceIndex,
                    cell => problem.Board[cell].x.ToString());

            var features = new List<DantianSolverFeature>();
            for (var targetPieceIndex = 0;
                 targetPieceIndex < problem.Pieces.Count;
                 targetPieceIndex++)
            {
                if (targetPieceIndex == sourcePieceIndex ||
                    !MatchesCountKind(problem.Pieces[targetPieceIndex], parsed)) continue;
                var flags = PlacementFlags(problem.Pieces[targetPieceIndex], masks[targetPieceIndex], null);
                if (!Any(flags)) continue;
                features.Add(new DantianSolverFeature
                {
                    terms = new[]
                    {
                        new DantianSolverTerm
                        {
                            piece = targetPieceIndex,
                            placementFlags = Convert.ToBase64String(flags),
                        },
                    },
                });
            }
            return features;
        }

        private static List<DantianSolverFeature> BuildGroupedFeatures(DantianLayoutProblem problem,
            HashSet<int>[] masks, int sourcePieceIndex, Func<int, string> groupKey)
        {
            var groups = new Dictionary<string, List<DantianSolverTerm>>();
            for (var targetPieceIndex = 0;
                 targetPieceIndex < problem.Pieces.Count;
                 targetPieceIndex++)
            {
                if (targetPieceIndex == sourcePieceIndex) continue;
                foreach (var group in masks[targetPieceIndex].GroupBy(groupKey))
                {
                    var cells = new HashSet<int>(group);
                    var flags = PlacementFlags(problem.Pieces[targetPieceIndex], cells, null);
                    if (!Any(flags)) continue;
                    if (!groups.TryGetValue(group.Key, out var terms))
                        groups[group.Key] = terms = new List<DantianSolverTerm>();
                    terms.Add(new DantianSolverTerm
                    {
                        piece = targetPieceIndex,
                        placementFlags = Convert.ToBase64String(flags),
                    });
                }
            }
            return groups.Values.Select(terms => new DantianSolverFeature
            {
                terms = terms.ToArray(),
            }).ToList();
        }

        private static DantianSolverFeature BuildGateFeature(DantianLayoutProblem problem,
            HashSet<int>[] masks, int sourcePieceIndex)
        {
            var terms = new List<DantianSolverTerm>();
            for (var targetPieceIndex = 0;
                 targetPieceIndex < problem.Pieces.Count;
                 targetPieceIndex++)
            {
                if (targetPieceIndex == sourcePieceIndex) continue;
                var flags = PlacementFlags(problem.Pieces[targetPieceIndex], masks[targetPieceIndex], null);
                if (!Any(flags)) continue;
                terms.Add(new DantianSolverTerm
                {
                    piece = targetPieceIndex,
                    placementFlags = Convert.ToBase64String(flags),
                });
            }
            return new DantianSolverFeature { terms = terms.ToArray() };
        }

        private static bool IsUnconditionalTargetEffect(string effect)
        {
            return string.Equals((effect ?? string.Empty).Trim(), "0",
                StringComparison.Ordinal);
        }

        private static DantianSolverFeature BuildAlwaysFeature(int sourcePieceIndex,
            int placementCount)
        {
            var flags = new byte[(placementCount + 7) / 8];
            for (var placement = 0; placement < placementCount; placement++)
                flags[placement >> 3] |= (byte)(1 << (placement & 7));
            return new DantianSolverFeature
            {
                terms = new[]
                {
                    new DantianSolverTerm
                    {
                        piece = sourcePieceIndex,
                        placementFlags = Convert.ToBase64String(flags),
                    },
                },
            };
        }

        private static byte[] PlacementFlags(DantianLayoutPiece piece, HashSet<int> mask,
            Func<int, bool> predicate)
        {
            var bytes = new byte[(piece.Placements.Count + 7) / 8];
            for (var placement = 0; placement < piece.Placements.Count; placement++)
            {
                var hit = piece.Placements[placement].CellIndices.Any(cell =>
                    mask.Contains(cell) && (predicate == null || predicate(cell)));
                if (hit) bytes[placement >> 3] |= (byte)(1 << (placement & 7));
            }
            return bytes;
        }

        private static bool Any(byte[] bytes) => bytes.Any(value => value != 0);

        private static bool MatchesCountKind(DantianLayoutPiece piece, ParsedMultiplier parsed)
        {
            if (parsed.CountKind == 4) return piece.IsArt;
            if (parsed.CountKind == 5) return piece.IsMagic;
            if (parsed.CountKind == 6) return piece.IsArt && piece.Id.sedId == parsed.Index;
            if (parsed.CountKind == 7) return piece.IsMagic && piece.Id.sedId == parsed.Index;
            return true;
        }

        private static int EvaluateCurrentRule(DantianLayoutProblem problem, DantianSolverRule rule)
        {
            var sourcePlacement = problem.CurrentPlacements[rule.sourcePiece];
            var option = rule.sourceOptions[sourcePlacement];
            var count = option.countFeatures.Count(feature => IsFeatureActive(problem, feature));
            var gate = IsFeatureActive(problem, option.gateFeature);
            return gate ? rule.multiplierByCount[Math.Min(count, rule.multiplierByCount.Length - 1)] : 0;
        }

        private static bool IsFeatureActive(DantianLayoutProblem problem, DantianSolverFeature feature)
        {
            foreach (var term in feature?.terms ?? Array.Empty<DantianSolverTerm>())
            {
                var flags = Convert.FromBase64String(term.placementFlags ?? string.Empty);
                var placement = problem.CurrentPlacements[term.piece];
                if ((flags[placement >> 3] & (1 << (placement & 7))) != 0) return true;
            }
            return false;
        }

        private static ParsedMultiplier ParseMultiplier(string raw, int max)
        {
            if (string.IsNullOrEmpty(raw))
                return new ParsedMultiplier { CountKind = 0, Operation = 0, Index = 1, Max = 0 };
            var hash = raw.Split('#');
            var first = hash.Length == 0 ? Array.Empty<string>() : hash[0].Split(',');
            int.TryParse(first.Length > 0 ? first[0] : "0", out var countKind);
            int.TryParse(first.Length > 1 ? first[1] : "0", out var operation);
            var index = 1;
            if (hash.Length == 2) int.TryParse(hash[1], out index);
            if (index == 0) index = 1;
            return new ParsedMultiplier
            {
                CountKind = countKind,
                Operation = operation,
                Index = index,
                Max = max,
            };
        }

        private sealed class ParsedMultiplier
        {
            internal int CountKind;
            internal int Operation;
            internal int Index;
            internal int Max;

            internal int GetMultiplier(int count)
            {
                if (CountKind == 6 || CountKind == 7) return count;
                int value;
                switch (Operation)
                {
                    case 11: value = count > Index ? 1 : 0; break;
                    case 12: value = count >= Index ? 1 : 0; break;
                    case 13: value = count < Index ? 1 : 0; break;
                    case 14: value = count <= Index ? 1 : 0; break;
                    case 15: value = count == Index ? 1 : 0; break;
                    default: value = count / Math.Max(1, Index); break;
                }
                return Max == 0 ? value : Math.Min(Max, value);
            }
        }
    }
}
