using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Text;
using Newtonsoft.Json;
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
        public int[] cellX;
        public int[] cellY;
        public int[] currentPlacements;
        public int[] expectedCurrentMultipliers;
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
        public bool countSelf;
        public int[] countGeometry;
        public int[] countTargetPieces;
        public bool gateSelf;
        public int[] gateGeometry;
        public int[] gateTargetPieces;
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
        public double modelBuildSeconds = 0;
        public double totalSeconds = 0;
    }

    internal sealed class DantianSolverRunResult
    {
        internal DantianSolverResponse Response;
        internal string Error;
        internal string StandardError;
        internal string SnapshotPath;
    }

    internal sealed class DantianSolverBuildResult
    {
        internal DantianSolverRequest Request;
        internal string Error;
        internal long ElapsedMilliseconds;
    }

    internal sealed class DantianSolverBuildProgress
    {
        internal int CompletedOptions;
        internal int TotalOptions;
        internal string CurrentRule;
    }

    internal static class DantianCpSatBridge
    {
        private const int ProtocolVersion = 2;

        internal static Task<DantianSolverBuildResult> BuildAsync(DantianLayoutProblem problem,
            DantianLayoutScore currentScore, int timeLimitMs, int seed,
            DantianSolverBuildProgress progress, CancellationToken cancellationToken)
        {
            return Task.Run(() =>
            {
                var watch = Stopwatch.StartNew();
                var succeeded = TryBuildCompactRequest(problem, currentScore, timeLimitMs, seed,
                    progress, cancellationToken, out var request, out var error);
                return new DantianSolverBuildResult
                {
                    Request = succeeded ? request : null,
                    Error = error,
                    ElapsedMilliseconds = watch.ElapsedMilliseconds,
                };
            }, cancellationToken);
        }

        private sealed class CompactEffect
        {
            internal bool Self;
            internal int[] Geometry = Array.Empty<int>();
            internal int[] TargetPieces = Array.Empty<int>();
        }

        private static bool TryBuildCompactRequest(DantianLayoutProblem problem,
            DantianLayoutScore currentScore, int timeLimitMs, int seed,
            DantianSolverBuildProgress progress, CancellationToken cancellationToken,
            out DantianSolverRequest request, out string error)
        {
            request = null;
            error = null;
            try
            {
                var rules = new List<DantianSolverRule>();
                var nativeIndex = 0;
                for (var sourcePiece = 0; sourcePiece < problem.Pieces.Count; sourcePiece++)
                {
                    var piece = problem.Pieces[sourcePiece];
                    for (var ruleIndex = 0; ruleIndex < piece.Rules.Count; ruleIndex++)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        var cfg = piece.Rules[ruleIndex];
                        var parsed = ParseMultiplier(cfg.upMulType, cfg.maxUpMul);
                        progress.CurrentRule = $"{piece.Name}#{ruleIndex + 1}";
                        if (parsed.CountKind != 0 ||
                            !TryCompileCompactEffect(problem, sourcePiece, cfg.upMulEff,
                                out var countEffect) ||
                            !TryCompileCompactEffect(problem, sourcePiece, cfg.targetEff,
                                out var gateEffect))
                        {
                            error = $"暂不支持的原生规则：{progress.CurrentRule}，" +
                                    $"target='{cfg.targetEff}'，up='{cfg.upMulEff}'，" +
                                    $"type='{cfg.upMulType}'。为避免错误排布，已拒绝建模。";
                            return false;
                        }
                        var count = countEffect.Self
                            ? 1
                            : CountCurrentHits(problem, sourcePiece, countEffect);
                        var gate = gateEffect.Self ||
                                   CountCurrentHits(problem, sourcePiece, gateEffect) > 0;
                        var modeled = gate ? parsed.GetMultiplier(count) : 0;
                        var native = nativeIndex < currentScore.Multipliers.Count
                            ? currentScore.Multipliers[nativeIndex]
                            : -1;
                        if (modeled != native)
                        {
                            var evidence = nativeIndex < currentScore.RuleEvidence.Count
                                ? currentScore.RuleEvidence[nativeIndex]
                                : "native evidence unavailable";
                            error = $"紧凑模型校验失败：{progress.CurrentRule}，模型x{modeled}，" +
                                    $"原生x{native}，count={count}，gate={gate}；{evidence}";
                            return false;
                        }
                        var maximumCount = countEffect.Self
                            ? 1
                            : countEffect.TargetPieces.Length;
                        var table = Enumerable.Range(0, maximumCount + 1)
                            .Select(parsed.GetMultiplier).ToArray();
                        rules.Add(new DantianSolverRule
                        {
                            name = progress.CurrentRule,
                            sourcePiece = sourcePiece,
                            maxMultiplier = table.Max(),
                            multiplierByCount = table,
                            countSelf = countEffect.Self,
                            countGeometry = countEffect.Geometry,
                            countTargetPieces = countEffect.TargetPieces,
                            gateSelf = gateEffect.Self,
                            gateGeometry = gateEffect.Geometry,
                            gateTargetPieces = gateEffect.TargetPieces,
                        });
                        nativeIndex++;
                        Interlocked.Increment(ref progress.CompletedOptions);
                    }
                }
                request = new DantianSolverRequest
                {
                    version = ProtocolVersion,
                    timeLimitMs = timeLimitMs,
                    seed = seed,
                    cellCount = problem.Board.Count,
                    cellX = problem.Board.Select(cell => cell.x).ToArray(),
                    cellY = problem.Board.Select(cell => cell.y).ToArray(),
                    currentPlacements = problem.CurrentPlacements.ToArray(),
                    expectedCurrentMultipliers = currentScore.Multipliers.ToArray(),
                    pieces = problem.Pieces.Select(piece => new DantianSolverPiece
                    {
                        name = piece.Name,
                        placements = piece.Placements.Select(placement =>
                            new DantianSolverPlacement { cells = placement.CellIndices }).ToArray(),
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

        private static bool TryCompileCompactEffect(DantianLayoutProblem problem,
            int sourcePiece, string effect, out CompactEffect compiled)
        {
            compiled = new CompactEffect();
            if (IsSourceSelfEffect(effect))
            {
                compiled.Self = true;
                return true;
            }
            if (effect.Contains("|")) return false;
            var geometry = new List<int>();
            var staticCodes = new List<Tuple<int, int>>();
            foreach (var raw in effect.Split('&'))
            {
                var parts = raw.Split('#');
                if (!int.TryParse(parts[0], out var code) || code < 0) return false;
                var index = 0;
                if (parts.Length > 1 && !int.TryParse(parts[1], out index)) return false;
                if ((code >= 1 && code <= 7) || code == 10 || code == 13)
                    geometry.Add(code == 13 ? 10 : code);
                if (code == 13 || (code >= 20 && code <= 28) ||
                    (code >= 40 && code <= 51) || code == 100 || code == 101 ||
                    code == 200 || code == 201)
                    staticCodes.Add(Tuple.Create(code, index));
                else if (!((code >= 1 && code <= 7) || code == 10))
                    return false;
            }
            compiled.Geometry = geometry.Distinct().ToArray();
            compiled.TargetPieces = Enumerable.Range(0, problem.Pieces.Count)
                .Where(target => target != sourcePiece && staticCodes.All(item =>
                    MatchesStaticCode(problem.Pieces[target], item.Item1, item.Item2)))
                .ToArray();
            return true;
        }

        private static bool MatchesStaticCode(DantianLayoutPiece piece, int code, int index)
        {
            if (code == 13) return piece.IsArt || piece.IsMagic;
            if (code >= 20 && code <= 28) return piece.Attribute + 20 == code;
            if (code >= 40 && code <= 45)
                return (piece.IsMagic && piece.Type - 221 == code - 40) ||
                       (piece.IsArt && piece.Type - 201 == code - 40);
            if (code == 50) return piece.IsMagic && piece.IsAttackMagic;
            if (code == 51) return piece.IsMagic && piece.IsDefenceMagic;
            if (code == 100) return piece.IsArt;
            if (code == 101) return piece.IsMagic;
            if (code == 200) return piece.IsArt && piece.Id.sedId == index;
            if (code == 201) return piece.IsMagic && piece.Id.sedId == index;
            return true;
        }

        private static int CountCurrentHits(DantianLayoutProblem problem, int sourcePiece,
            CompactEffect effect)
        {
            var sourceCells = problem.Pieces[sourcePiece]
                .Placements[problem.CurrentPlacements[sourcePiece]].CellIndices;
            return effect.TargetPieces.Count(target => problem.Pieces[target]
                .Placements[problem.CurrentPlacements[target]].CellIndices.Any(candidate =>
                    MatchesGeometry(problem, sourceCells, candidate, effect.Geometry)));
        }

        private static bool MatchesGeometry(DantianLayoutProblem problem, int[] sourceCells,
            int candidate, int[] geometry)
        {
            var target = problem.Board[candidate];
            return geometry.All(code =>
            {
                if (code == 7) return true;
                if (code == 10) return sourceCells.Any(source =>
                    Math.Abs(problem.Board[source].x - target.x) +
                    Math.Abs(problem.Board[source].y - target.y) <= 1);
                if (code == 11) return sourceCells.All(source =>
                    Math.Abs(problem.Board[source].x - target.x) +
                    Math.Abs(problem.Board[source].y - target.y) > 1);
                if (code == 1) return sourceCells.Any(source =>
                    problem.Board[source].x == target.x && problem.Board[source].y < target.y);
                if (code == 2) return sourceCells.Any(source =>
                    problem.Board[source].x == target.x && problem.Board[source].y > target.y);
                if (code == 3) return sourceCells.Any(source =>
                    problem.Board[source].y == target.y && problem.Board[source].x > target.x);
                if (code == 4) return sourceCells.Any(source =>
                    problem.Board[source].y == target.y && problem.Board[source].x < target.x);
                if (code == 5) return sourceCells.Any(source =>
                    problem.Board[source].x - target.x == problem.Board[source].y - target.y);
                if (code == 6) return sourceCells.Any(source =>
                    problem.Board[source].x - target.x == target.y - problem.Board[source].y);
                return false;
            });
        }

        private static bool TryBuildRequest(DantianLayoutProblem problem,
            DantianLayoutScore currentScore, int timeLimitMs, int seed,
            DantianSolverBuildProgress progress, CancellationToken cancellationToken,
            out DantianSolverRequest request, out string error)
        {
            request = null;
            error = null;
            try
            {
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
                        if (parsed.CountKind != 0 ||
                            !IsSupportedObjectCountEffect(cfg.upMulEff) ||
                            !IsSupportedObjectCountEffect(cfg.targetEff))
                        {
                            error = $"暂不支持的原生规则：{sourcePiece.Name}#{ruleIndex + 1}，" +
                                    $"target='{cfg.targetEff}'，up='{cfg.upMulEff}'，" +
                                    $"type='{cfg.upMulType}'。为避免错误排布，已拒绝建模。";
                            return false;
                        }
                        var unconditionalGate = IsUnconditionalTargetEffect(cfg.targetEff);
                        var options = new DantianSolverSourceOption[sourcePiece.Placements.Count];
                        var featureCounts = new int[sourcePiece.Placements.Count];
                        var currentPlacement = problem.CurrentPlacements[sourcePieceIndex];
                        progress.CurrentRule = $"{sourcePiece.Name}#{ruleIndex + 1}";

                        // Validate the rule semantics against the native game before paying the
                        // cost of expanding every possible source placement.
                        options[currentPlacement] = BuildSourceOption(problem, cfg, parsed,
                            sourcePieceIndex, currentPlacement, unconditionalGate,
                            cancellationToken, out featureCounts[currentPlacement]);
                        Interlocked.Increment(ref progress.CompletedOptions);
                        var currentOption = options[currentPlacement];
                        var currentCount = currentOption.countFeatures.Count(feature =>
                            IsFeatureActive(problem, feature));
                        var currentGate = IsFeatureActive(problem, currentOption.gateFeature);
                        var modeledCurrent = currentGate ? parsed.GetMultiplier(currentCount) : 0;
                        var nativeCurrent = currentMultiplierIndex < currentScore.Multipliers.Count
                            ? currentScore.Multipliers[currentMultiplierIndex]
                            : -1;
                        if (modeledCurrent != nativeCurrent)
                        {
                            var nativeEvidence = currentMultiplierIndex < currentScore.RuleEvidence.Count
                                ? currentScore.RuleEvidence[currentMultiplierIndex]
                                : "native evidence unavailable";
                            error = $"规则模型校验失败：{progress.CurrentRule}，" +
                                    $"模型x{modeledCurrent}，原生x{nativeCurrent}，" +
                                    $"count={currentCount}，gate={currentGate}；{nativeEvidence}";
                            return false;
                        }

                        var parallelOptions = new ParallelOptions
                        {
                            CancellationToken = cancellationToken,
                            MaxDegreeOfParallelism = Math.Min(6,
                                Math.Max(1, Environment.ProcessorCount - 2)),
                        };
                        Parallel.For(0, sourcePiece.Placements.Count, parallelOptions,
                            sourcePlacementIndex =>
                            {
                                if (sourcePlacementIndex == currentPlacement) return;
                                options[sourcePlacementIndex] = BuildSourceOption(problem, cfg,
                                    parsed, sourcePieceIndex, sourcePlacementIndex,
                                    unconditionalGate, cancellationToken,
                                    out featureCounts[sourcePlacementIndex]);
                                Interlocked.Increment(ref progress.CompletedOptions);
                            });
                        var maximumCount = featureCounts.Max();
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

        private static DantianSolverSourceOption BuildSourceOption(
            DantianLayoutProblem problem, TbDrawStateCfg cfg, ParsedMultiplier parsed,
            int sourcePieceIndex, int sourcePlacementIndex, bool unconditionalGate,
            CancellationToken cancellationToken, out int featureCount)
        {
            var maskCache = new Dictionary<string, HashSet<int>>();
            var distanceCache = new Dictionary<string, int[]>();
            var upMasks = new HashSet<int>[problem.Pieces.Count];
            var gateMasks = new HashSet<int>[problem.Pieces.Count];
            var sourceCountsItself = IsSourceSelfEffect(cfg.upMulEff);
            for (var targetPieceIndex = 0;
                 targetPieceIndex < problem.Pieces.Count;
                 targetPieceIndex++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (targetPieceIndex == sourcePieceIndex)
                {
                    upMasks[targetPieceIndex] = new HashSet<int>();
                    gateMasks[targetPieceIndex] = new HashSet<int>();
                    continue;
                }
                upMasks[targetPieceIndex] = sourceCountsItself
                    ? new HashSet<int>()
                    : GetCachedMask(maskCache, distanceCache, cfg.upMulEff, problem,
                        sourcePieceIndex, sourcePlacementIndex, targetPieceIndex,
                        cancellationToken);
                gateMasks[targetPieceIndex] = unconditionalGate
                    ? new HashSet<int>()
                    : GetCachedMask(maskCache, distanceCache, cfg.targetEff, problem,
                        sourcePieceIndex, sourcePlacementIndex, targetPieceIndex,
                        cancellationToken);
            }
            var features = sourceCountsItself
                ? new List<DantianSolverFeature>
                {
                    BuildAlwaysFeature(sourcePieceIndex,
                        problem.Pieces[sourcePieceIndex].Placements.Count),
                }
                : BuildCountFeatures(problem, parsed, upMasks, sourcePieceIndex);
            featureCount = features.Count;
            return new DantianSolverSourceOption
            {
                countFeatures = features.ToArray(),
                gateFeature = unconditionalGate
                    ? BuildAlwaysFeature(sourcePieceIndex,
                        problem.Pieces[sourcePieceIndex].Placements.Count)
                    : BuildGateFeature(problem, gateMasks, sourcePieceIndex),
            };
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
                    StandardInputEncoding = Encoding.UTF8,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
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
                    var payload = JsonConvert.SerializeObject(request);
                    result.SnapshotPath = SaveSnapshot("request", payload);
                    var localCheck = JsonConvert.DeserializeObject<DantianSolverRequest>(payload);
                    if (localCheck?.pieces == null || localCheck.pieces.Length != request.pieces.Length)
                    {
                        result.Error = "求解请求在发送前自检失败";
                        try { process.Kill(); } catch { }
                        return result;
                    }
                    process.StandardInput.Write(payload);
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
                    if (!string.IsNullOrWhiteSpace(output)) SaveSnapshot("response", output);
                    if (string.IsNullOrWhiteSpace(output))
                    {
                        result.Error = $"求解器没有返回结果（退出码{process.ExitCode}）";
                        return result;
                    }
                    result.Response = JsonConvert.DeserializeObject<DantianSolverResponse>(output);
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

        private static string SaveSnapshot(string kind, string content)
        {
            try
            {
                var directory = Path.Combine(Path.GetTempPath(), "codeyun", "zaohua_mod",
                    "dantian_solver");
                Directory.CreateDirectory(directory);
                var path = Path.Combine(directory, $"latest.{kind}.json");
                File.WriteAllText(path, content, Encoding.UTF8);
                return path;
            }
            catch
            {
                return null;
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
            int targetPieceIndex, int[] distances, CancellationToken cancellationToken)
        {
            var sourcePiece = problem.Pieces[sourcePieceIndex];
            var targetPiece = problem.Pieces[targetPieceIndex];
            var sourceCells = new HashSet<int>(
                sourcePiece.Placements[sourcePlacementIndex].CellIndices);
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
            Dictionary<string, int[]> distanceCache, string effect, DantianLayoutProblem problem,
            int sourcePiece, int sourcePlacement, int targetPiece,
            CancellationToken cancellationToken)
        {
            var distanceKey = $"{sourcePiece}:{sourcePlacement}";
            if (!distanceCache.TryGetValue(distanceKey, out var distances))
            {
                distances = GetDistances(problem, new HashSet<int>(
                    problem.Pieces[sourcePiece].Placements[sourcePlacement].CellIndices));
                distanceCache[distanceKey] = distances;
            }
            var targetSignature = GetTargetSignature(effect, problem.Pieces[targetPiece]);
            var key = $"{distanceKey}:{effect ?? string.Empty}:{targetSignature}";
            if (cache.TryGetValue(key, out var mask)) return mask;
            mask = GetMask(effect, problem, sourcePiece, sourcePlacement,
                targetPiece, distances, cancellationToken);
            cache[key] = mask;
            return mask;
        }

        private static string GetTargetSignature(string effect, DantianLayoutPiece piece)
        {
            var needsCategory = false;
            var needsAttribute = false;
            var needsType = false;
            var needsAttack = false;
            var needsDefence = false;
            var needsExactId = false;
            foreach (var raw in (effect ?? string.Empty).Split('&', '|'))
            {
                var parts = raw.Split('#');
                if (!int.TryParse(parts[0], out var signedCode)) continue;
                var code = Math.Abs(signedCode);
                if (code == 13 || code == 100 || code == 101 || code == 200 || code == 201)
                    needsCategory = true;
                if (code >= 20 && code <= 28) needsAttribute = true;
                if (code >= 40 && code <= 45)
                {
                    needsCategory = true;
                    needsType = true;
                }
                if (code == 50)
                {
                    needsCategory = true;
                    needsAttack = true;
                }
                if (code == 51)
                {
                    needsCategory = true;
                    needsDefence = true;
                }
                if (code == 200 || code == 201) needsExactId = true;
            }
            return string.Join(":", new[]
            {
                needsCategory ? $"{piece.IsArt},{piece.IsMagic}" : "*",
                needsAttribute ? piece.Attribute.ToString() : "*",
                needsType ? piece.Type.ToString() : "*",
                needsAttack ? piece.IsAttackMagic.ToString() : "*",
                needsDefence ? piece.IsDefenceMagic.ToString() : "*",
                needsExactId ? $"{(int)piece.Id.blendEnum},{piece.Id.sedId}" : "*",
            });
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

        private static bool IsSourceSelfEffect(string effect)
        {
            return string.IsNullOrWhiteSpace(effect) ||
                   string.Equals(effect.Trim(), "0", StringComparison.Ordinal);
        }

        private static bool IsSupportedObjectCountEffect(string effect)
        {
            if (IsSourceSelfEffect(effect)) return true;
            if (effect.Contains("|")) return false;
            foreach (var raw in effect.Split('&'))
            {
                var parts = raw.Split('#');
                if (!int.TryParse(parts[0], out var code) || code < 0) return false;
                var supported = (code >= 1 && code <= 7) || code == 10 || code == 11 ||
                                code == 13 || (code >= 20 && code <= 28) ||
                                (code >= 40 && code <= 51) || code == 100 || code == 101 ||
                                code == 200 || code == 201;
                if (!supported) return false;
            }
            return true;
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
