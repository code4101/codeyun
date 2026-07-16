using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using BepInEx;
using Newtonsoft.Json;

namespace Code4101.Zaohua.Tiandao
{
    [Serializable]
    internal sealed class DantianBestSolutionDocument
    {
        public int version = 1;
        public string modelSignature;
        public string savedAtUtc;
        public int[] placements;
        public int[] multipliers;
        public int[] targetCounts;
        public int total;
    }

    internal static class DantianBestSolutionRepository
    {
        internal static bool TryLoad(DantianSolverRequest request,
            out DantianBestSolutionDocument document, out string path, out string error)
        {
            document = null;
            error = null;
            var signature = ComputeSignature(request);
            path = PathFor(signature);
            if (!File.Exists(path) && !File.Exists(path + ".bak")) return false;
            foreach (var candidatePath in new[] { path, path + ".bak" })
            {
                if (!File.Exists(candidatePath)) continue;
                try
                {
                    var candidate = JsonConvert.DeserializeObject<DantianBestSolutionDocument>(
                        File.ReadAllText(candidatePath, Encoding.UTF8));
                    if (IsValid(request, signature, candidate))
                    {
                        document = candidate;
                        return true;
                    }
                }
                catch (Exception exception)
                {
                    error = exception.GetType().Name + ": " + exception.Message;
                }
            }
            error = error ?? "最佳解快照格式无效";
            return false;
        }

        internal static bool SaveIfBetter(DantianSolverRequest request, int[] placements,
            DantianLayoutScore score, out string path, out string error)
        {
            error = null;
            var signature = ComputeSignature(request);
            path = PathFor(signature);
            if (TryLoad(request, out var existing, out _, out var loadError))
            {
                if (!Better(request, score.Multipliers.ToArray(), score.TargetCounts.ToArray(),
                        existing.multipliers, existing.targetCounts))
                    return false;
            }
            else if (!string.IsNullOrEmpty(loadError) && File.Exists(path))
            {
                error = "现有最佳解快照损坏，已进入只读保护：" + loadError;
                return false;
            }
            var document = new DantianBestSolutionDocument
            {
                modelSignature = signature,
                savedAtUtc = DateTime.UtcNow.ToString("O"),
                placements = placements.ToArray(),
                multipliers = score.Multipliers.ToArray(),
                targetCounts = score.TargetCounts.ToArray(),
                total = score.Total,
            };
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var temporaryPath = path + ".tmp";
                File.WriteAllText(temporaryPath, JsonConvert.SerializeObject(document),
                    Encoding.UTF8);
                if (File.Exists(path)) File.Replace(temporaryPath, path, path + ".bak", true);
                else File.Move(temporaryPath, path);
                return true;
            }
            catch (Exception exception)
            {
                error = exception.GetType().Name + ": " + exception.Message;
                return false;
            }
        }

        private static bool Better(DantianSolverRequest request, int[] left,
            int[] leftTargets, int[] right, int[] rightTargets)
        {
            var leftTotal = left.Zip(leftTargets, (value, targets) => value * targets).Sum();
            var rightTotal = right.Zip(rightTargets, (value, targets) => value * targets).Sum();
            if (leftTotal < rightTotal) return false;
            foreach (var index in request.priorityOrder ?? Enumerable.Range(0, left.Length))
            {
                var leftBenefit = left[index] * leftTargets[index];
                var rightBenefit = right[index] * rightTargets[index];
                if (leftBenefit != rightBenefit) return leftBenefit > rightBenefit;
            }
            return leftTotal > rightTotal;
        }

        private static bool IsValid(DantianSolverRequest request, string signature,
            DantianBestSolutionDocument document)
        {
            if (document == null || document.version != 1 ||
                document.modelSignature != signature ||
                document.placements?.Length != request.pieces.Length ||
                document.multipliers?.Length != request.rules.Length ||
                document.targetCounts?.Length != request.rules.Length)
                return false;
            for (var index = 0; index < document.placements.Length; index++)
            {
                if (document.placements[index] < 0 ||
                    document.placements[index] >= request.pieces[index].placements.Length)
                    return false;
            }
            return true;
        }

        internal static string ComputeSignature(DantianSolverRequest request)
        {
            var structural = new DantianSolverRequest
            {
                version = request.version,
                timeLimitMs = 0,
                seed = 0,
                cellCount = request.cellCount,
                cellX = request.cellX,
                cellY = request.cellY,
                currentPlacements = null,
                expectedCurrentMultipliers = null,
                priorityOrder = request.priorityOrder,
                pieces = request.pieces,
                rules = request.rules,
            };
            var bytes = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(structural));
            using (var hash = SHA256.Create())
                return string.Concat(hash.ComputeHash(bytes).Select(value => value.ToString("x2")));
        }

        private static string PathFor(string signature)
        {
            return Path.Combine(Paths.ConfigPath, "Code4101.Zaohua.Tiandao",
                "dantian-best", signature + ".json");
        }
    }
}
