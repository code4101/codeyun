using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace Code4101.Zaohua.Tiandao
{
    internal static class AlchemySolutionCacheRepository
    {
        private const int SchemaVersion = 1;

        [Serializable]
        private sealed class CacheDocument
        {
            public int version;
            public string key;
            public List<SolutionDto> solutions = new List<SolutionDto>();
        }

        [Serializable]
        private sealed class SolutionDto
        {
            public int plantingDays;
            public int searchStage;
            public int triggerCount;
            public int countBonus;
            public int qualityBonus;
            public int dayBonus;
            public int dayMultiplierBonus;
            public int freeRateBonus;
            public List<int> itemIds = new List<int>();
            public List<int> itemCounts = new List<int>();
            public List<PlacementDto> placements = new List<PlacementDto>();
        }

        [Serializable]
        private sealed class PlacementDto
        {
            public BlendId itemId;
            public int poolType;
            public int x;
            public int y;
            public int rotation;
        }

        internal static bool TryLoad(string key, out List<AlchemySolution> solutions)
        {
            solutions = null;
            try
            {
                var path = GetPath(key);
                if (!File.Exists(path)) return false;
                var document = JsonUtility.FromJson<CacheDocument>(File.ReadAllText(path));
                if (document == null || document.version != SchemaVersion || document.key != key) return false;
                solutions = document.solutions.Select(FromDto).Where(solution => solution != null).ToList();
                return true;
            }
            catch (Exception error)
            {
                Debug.LogWarning($"[Code4101 Tiandao] alchemy cache ignored: {error.Message}");
                solutions = null;
                return false;
            }
        }

        internal static void Save(string key, IReadOnlyList<AlchemySolution> solutions)
        {
            try
            {
                var path = GetPath(key);
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var document = new CacheDocument
                {
                    version = SchemaVersion,
                    key = key,
                    solutions = solutions.Select(ToDto).ToList(),
                };
                var temporaryPath = path + ".tmp";
                File.WriteAllText(temporaryPath, JsonUtility.ToJson(document));
                if (File.Exists(path)) File.Replace(temporaryPath, path, null);
                else File.Move(temporaryPath, path);
            }
            catch (Exception error)
            {
                Debug.LogWarning($"[Code4101 Tiandao] alchemy cache save failed: {error.Message}");
            }
        }

        private static string GetPath(string key)
        {
            using var sha = SHA256.Create();
            var hash = string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(key)).Select(value => value.ToString("x2")));
            return Path.Combine(Application.persistentDataPath, "Code4101.Zaohua.Tiandao", "cache", "alchemy", hash + ".json");
        }

        private static SolutionDto ToDto(AlchemySolution solution)
        {
            var dto = new SolutionDto
            {
                plantingDays = solution.PlantingDays,
                searchStage = solution.SearchStage,
                triggerCount = solution.RuleOutcome.TriggerCount,
                countBonus = solution.RuleOutcome.CountBonus,
                qualityBonus = solution.RuleOutcome.QualityBonus,
                dayBonus = solution.RuleOutcome.DayBonus,
                dayMultiplierBonus = solution.RuleOutcome.DayMultiplierBonus,
                freeRateBonus = solution.RuleOutcome.FreeRateBonus,
                placements = solution.Placements.Select(item => new PlacementDto
                {
                    itemId = item.ItemId,
                    poolType = item.PoolType,
                    x = item.Position.x,
                    y = item.Position.y,
                    rotation = item.Rotation,
                }).ToList(),
            };
            foreach (var pair in solution.ItemCounts.OrderBy(pair => pair.Key))
            {
                dto.itemIds.Add(pair.Key);
                dto.itemCounts.Add(pair.Value);
            }
            return dto;
        }

        private static AlchemySolution FromDto(SolutionDto dto)
        {
            if (dto == null || dto.itemIds.Count != dto.itemCounts.Count) return null;
            return new AlchemySolution
            {
                PlantingDays = dto.plantingDays,
                SearchStage = dto.searchStage,
                ItemCounts = dto.itemIds.Select((id, index) => new { id, count = dto.itemCounts[index] })
                    .ToDictionary(pair => pair.id, pair => pair.count),
                Placements = dto.placements.Select(item => new AlchemyPlacement
                {
                    ItemId = item.itemId,
                    PoolType = item.poolType,
                    Position = new MyVector2Int(item.x, item.y),
                    Rotation = item.rotation,
                }).ToList(),
                RuleOutcome = new AlchemyRuleOutcome
                {
                    TriggerCount = dto.triggerCount,
                    CountBonus = dto.countBonus,
                    QualityBonus = dto.qualityBonus,
                    DayBonus = dto.dayBonus,
                    DayMultiplierBonus = dto.dayMultiplierBonus,
                    FreeRateBonus = dto.freeRateBonus,
                },
            };
        }
    }
}
