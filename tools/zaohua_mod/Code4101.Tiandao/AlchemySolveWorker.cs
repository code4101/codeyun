using System;
using System.Collections.Generic;
using System.Diagnostics;
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
        internal IReadOnlyList<SmartAlchemyUi.HerbStock> Herbs { get; set; }
        internal int Limit { get; set; }
    }

    internal sealed class AlchemySolveResponse
    {
        internal AlchemySolveRequest Request { get; set; }
        internal List<AlchemySolution> Solutions { get; set; }
        internal long ElapsedMilliseconds { get; set; }
        internal Exception Error { get; set; }
    }

    internal static class AlchemySolveWorker
    {
        internal static Task<AlchemySolveResponse> RunAsync(
            AlchemySolveRequest request,
            CancellationToken cancellationToken)
        {
            return Task.Run(() =>
            {
                var stopwatch = Stopwatch.StartNew();
                var response = new AlchemySolveResponse { Request = request };
                try
                {
                    response.Solutions = FiniteInventoryAlchemySolver.Solve(
                        request.Recipe,
                        request.Furnace,
                        request.Herbs,
                        request.Limit,
                        cancellationToken);
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
