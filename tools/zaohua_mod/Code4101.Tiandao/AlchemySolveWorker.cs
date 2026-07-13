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
        internal int Limit { get; set; }
    }

    internal sealed class AlchemySolveResponse
    {
        internal AlchemySolveRequest Request { get; set; }
        internal List<AlchemySolution> Solutions { get; set; }
        internal long ElapsedMilliseconds { get; set; }
        internal int CompletedStage { get; set; }
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
                     solution.PlantingDays < existing.PlantingDays))
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
                    response.Solutions = FiniteInventoryAlchemySolver.SolvePhased(
                        request.Recipe, request.Furnace,
                        request.GlobalCountBonus, request.GlobalQualityBonus,
                        request.Herbs, request.Inventory,
                        request.Limit, cancellationToken, progress.Publish,
                        stage => response.CompletedStage = stage);
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
