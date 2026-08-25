using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Code4101.Zaohua.Tiandao
{
    internal sealed class AlchemySolveRequest
    {
        internal string CacheKey { get; set; }
        internal int Generation { get; set; }
        internal TbDrugRecipeCfg Recipe { get; set; }
        internal TbPackSto Furnace { get; set; }
        internal int GlobalCountBonus { get; set; }
        internal int GlobalQualityBonus { get; set; }
        internal IReadOnlyList<SmartAlchemyUi.HerbStock> Herbs { get; set; }
        internal IReadOnlyDictionary<int, long> Inventory { get; set; }
        internal IReadOnlyList<AlchemySolution> CachedStaticSolutions { get; set; }
        internal bool StaticComplete { get; set; }
        internal string InventorySignature { get; set; }
    }

    internal sealed class AlchemySolveResponse
    {
        internal AlchemySolveRequest Request { get; set; }
        internal List<AlchemySolution> Solutions { get; set; }
        internal long ElapsedMilliseconds { get; set; }
        internal long StaticElapsedMilliseconds { get; set; }
        internal long BackpackElapsedMilliseconds { get; set; }
        internal Exception Error { get; set; }
    }

    internal sealed class AlchemySolveProgress
    {
        private readonly object _gate = new object();
        private readonly Dictionary<string, AlchemySolution> _solutions =
            new Dictionary<string, AlchemySolution>();
        private int _revision;

        internal void Publish(AlchemySolution solution)
        {
            if (solution == null) return;
            var key = string.Join(";", solution.Placements
                .GroupBy(item => $"{item.ItemId.sedId}:{item.PoolType}")
                .OrderBy(group => group.Key)
                .Select(group => $"{group.Key}:{group.Count()}"));
            lock (_gate)
            {
                if (!_solutions.TryGetValue(key, out var existing) ||
                    solution.SearchStage < existing.SearchStage ||
                    (solution.SearchStage == existing.SearchStage && solution.QualityRank > existing.QualityRank) ||
                    (solution.SearchStage == existing.SearchStage && solution.QualityRank == existing.QualityRank &&
                     solution.PlantingDaysPerPill < existing.PlantingDaysPerPill))
                {
                    _solutions[key] = solution;
                    _revision++;
                }
            }
        }

        internal List<AlchemySolution> Snapshot(int limit, out int revision)
        {
            List<AlchemySolution> copy;
            lock (_gate)
            {
                copy = _solutions.Values.ToList();
                revision = _revision;
            }
            return FiniteInventoryAlchemySolver.RankAndSelectSolutions(copy, limit);
        }
    }

    internal static class AlchemySolveWorker
    {
        internal static Task<AlchemySolveResponse> RunAsync(
            AlchemySolveRequest request,
            AlchemySolveProgress progress,
            CancellationToken cancellationToken)
        {
            return Task.Run(() =>
            {
                var stopwatch = Stopwatch.StartNew();
                var response = new AlchemySolveResponse { Request = request };
                try
                {
                    var staticStopwatch = Stopwatch.StartNew();
                    var staticSolutions = (request.CachedStaticSolutions ?? new List<AlchemySolution>())
                        .Where(solution => solution != null &&
                                           (solution.SearchStage == 1 || solution.SearchStage == 2))
                        .OrderBy(solution => solution.SearchStage)
                        .ToList();
                    // “没有静态解”和“静态解尚未求完”是两种状态。即使完成后的
                    // 基础解、迭代解均为空，也必须复用这个已证明无解的进程缓存。
                    if (!request.StaticComplete)
                    {
                        staticSolutions = FiniteInventoryAlchemySolver.SolveStatic(
                            request.Recipe, request.Furnace,
                            request.GlobalCountBonus, request.GlobalQualityBonus,
                            request.Herbs, cancellationToken, progress.Publish);
                    }
                    staticStopwatch.Stop();
                    response.StaticElapsedMilliseconds = staticStopwatch.ElapsedMilliseconds;
                    response.Solutions = staticSolutions.ToList();

                    var hasAvailableStatic = staticSolutions.Any(solution => solution.IsAvailable(request.Inventory));
                    if (!hasAvailableStatic)
                    {
                        var ideal = staticSolutions.FirstOrDefault(solution => solution.SearchStage == 2) ??
                                    staticSolutions.FirstOrDefault(solution => solution.SearchStage == 1);
                        var backpackStopwatch = Stopwatch.StartNew();
                        var backpack = FiniteInventoryAlchemySolver.SolveBackpack(
                            request.Recipe, request.Furnace,
                            request.GlobalCountBonus, request.GlobalQualityBonus,
                            request.Herbs, request.Inventory, ideal,
                            cancellationToken, progress.Publish);
                        backpackStopwatch.Stop();
                        response.BackpackElapsedMilliseconds = backpackStopwatch.ElapsedMilliseconds;
                        if (backpack != null) response.Solutions.Add(backpack);
                    }
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (Exception error)
                {
                    response.Solutions = new List<AlchemySolution>();
                    response.Error = error;
                }
                finally
                {
                    stopwatch.Stop();
                    response.ElapsedMilliseconds = stopwatch.ElapsedMilliseconds;
                }
                return response;
            }, cancellationToken);
        }
    }
}
