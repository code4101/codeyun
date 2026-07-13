using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using UnityEngine;

namespace Code4101.Zaohua.Tiandao
{
    [Serializable]
    [DataContract]
    internal sealed class EquipmentLoadoutStore
    {
        [DataMember(Order = 1)]
        public int version = 1;
        [DataMember(Order = 2)]
        public List<EquipmentLoadoutSaveState> saves = new List<EquipmentLoadoutSaveState>();
    }

    [Serializable]
    [DataContract]
    internal sealed class EquipmentLoadoutSaveState
    {
        [DataMember(Order = 1)]
        public string saveKey;
        [DataMember(Order = 2)]
        public string activeLoadoutId;
        [DataMember(Order = 3)]
        public int nextLoadoutNumber = 2;
        [DataMember(Order = 4)]
        public List<EquipmentLoadoutEntity> loadouts = new List<EquipmentLoadoutEntity>();
    }

    [Serializable]
    [DataContract]
    internal sealed class EquipmentLoadoutEntity
    {
        [DataMember(Order = 1)]
        public string id;
        [DataMember(Order = 2)]
        public string name;
        [DataMember(Order = 3)]
        public List<EquipmentLoadoutSlot> slots = new List<EquipmentLoadoutSlot>();
    }

    [Serializable]
    [DataContract]
    internal sealed class EquipmentLoadoutSlot
    {
        [DataMember(Order = 1)]
        public int slot;
        [DataMember(Order = 2)]
        public int packId;
        [DataMember(Order = 3)]
        public int blendType;
        [DataMember(Order = 4)]
        public int itemId;
    }

    internal static class EquipmentLoadoutRepository
    {
        private const int CurrentStoreVersion = 6;
        private const string DirectoryName = "Code4101.Tiandao";
        private const string FileName = "equipment-loadouts.json";
        private static EquipmentLoadoutStore _store;
        private static string _filePath;
        private static bool _loadBlocked;

        internal static EquipmentLoadoutSaveState GetCurrentSaveState(bool create = true)
        {
            var actor = BsSaveDataImpl.nowActor;
            if (actor?.fileSto == null) return null;
            EnsureLoaded();
            if (_store == null || _loadBlocked) return null;
            var key = $"{actor.id}:{actor.fileSto.buildTime}";
            var state = _store.saves.FirstOrDefault(item => item.saveKey == key);
            if (state == null)
            {
                // buildTime 在部分游戏版本或存档流程中可能变化。旧键存在时迁移到新键，
                // 绝不能因为精确键未命中就静默创建一套空方案。
                var legacyStates = _store.saves
                    .Where(item => item != null && item.saveKey != null &&
                                   item.saveKey.StartsWith(actor.id + ":", StringComparison.Ordinal))
                    .OrderByDescending(item => item.loadouts?.Count ?? 0)
                    .ToList();
                if (legacyStates.Count > 0)
                {
                    state = legacyStates[0];
                    foreach (var legacy in legacyStates.Skip(1)) MergeState(state, legacy);
                    foreach (var legacy in legacyStates.Skip(1)) _store.saves.Remove(legacy);
                    state.saveKey = key;
                    Save();
                }
            }
            if (state != null || !create) return state;
            state = new EquipmentLoadoutSaveState
            {
                saveKey = key,
                activeLoadoutId = "loadout-1",
            };
            state.loadouts.Add(new EquipmentLoadoutEntity
            {
                id = "loadout-1",
                name = "方案1",
                slots = CaptureSlots(actor.packStoList),
            });
            _store.saves.Add(state);
            Save();
            return state;
        }

        internal static EquipmentLoadoutEntity GetActiveLoadout(EquipmentLoadoutSaveState state)
        {
            return state?.loadouts.FirstOrDefault(item => item.id == state.activeLoadoutId);
        }

        internal static void CaptureActive()
        {
            var actor = BsSaveDataImpl.nowActor;
            var state = GetCurrentSaveState();
            var active = GetActiveLoadout(state);
            if (actor == null || active == null) return;
            active.slots = CaptureSlots(actor.packStoList);
            Save();
        }

        internal static EquipmentLoadoutEntity CreateLoadoutFromCurrent()
        {
            CaptureActive();
            var state = GetCurrentSaveState();
            var actor = BsSaveDataImpl.nowActor;
            if (state == null || actor == null) return null;
            var number = Math.Max(2, state.nextLoadoutNumber);
            state.nextLoadoutNumber = number + 1;
            var entity = new EquipmentLoadoutEntity
            {
                id = Guid.NewGuid().ToString("N"),
                name = $"方案{number}",
                // 新方案是当前装备的一个分支，而不是“卸下全部装备”的快捷键。
                slots = CaptureSlots(actor.packStoList),
            };
            state.loadouts.Add(entity);
            Save();
            return entity;
        }

        internal static void SetActive(EquipmentLoadoutSaveState state, EquipmentLoadoutEntity entity)
        {
            if (state == null || entity == null) return;
            state.activeLoadoutId = entity.id;
            Save();
        }

        internal static bool Rename(EquipmentLoadoutEntity entity, string name)
        {
            var normalized = name?.Trim();
            if (entity == null || string.IsNullOrEmpty(normalized)) return false;
            entity.name = normalized.Length > 12 ? normalized.Substring(0, 12) : normalized;
            Save();
            return true;
        }

        internal static void Save()
        {
            if (_store == null || _loadBlocked || _store.saves == null || _store.saves.Count == 0) return;
            try
            {
                EnsurePath();
                _store.version = CurrentStoreVersion;
                var json = SerializeStore(_store);
                var roundTrip = DeserializeStore(json);
                if (roundTrip?.saves == null || roundTrip.saves.Count != _store.saves.Count)
                    throw new InvalidDataException("方案序列化校验失败");
                var temporaryPath = _filePath + ".tmp";
                var backupPath = _filePath + ".bak";
                File.WriteAllText(temporaryPath, json);
                if (File.Exists(_filePath))
                {
                    if (File.Exists(backupPath)) File.Delete(backupPath);
                    File.Replace(temporaryPath, _filePath, backupPath);
                }
                else
                {
                    File.Move(temporaryPath, _filePath);
                }
            }
            catch (Exception error)
            {
                Debug.LogError($"[Code4101 Tiandao] save equipment loadouts failed: {error}");
            }
        }

        private static List<EquipmentLoadoutSlot> CaptureSlots(IEnumerable<TbPackSto> items)
        {
            var bySlot = items.Where(item => item != null && item.npcStoId == 10000)
                .GroupBy(item => item.flag)
                .ToDictionary(group => group.Key, group => group.First());
            return BagEnhancementState.EquipmentSlots.Select(slot =>
            {
                if (!bySlot.TryGetValue(slot, out var item)) return new EquipmentLoadoutSlot { slot = slot };
                return new EquipmentLoadoutSlot
                {
                    slot = slot,
                    packId = item.id,
                    blendType = (int)item.itemId.blendEnum,
                    itemId = item.itemId.sedId,
                };
            }).ToList();
        }

        private static void EnsureLoaded()
        {
            if (_store != null || _loadBlocked) return;
            EnsurePath();
            if (!File.Exists(_filePath))
            {
                _store = new EquipmentLoadoutStore { version = CurrentStoreVersion };
                return;
            }
            try
            {
                _store = LoadAndValidate(_filePath);
            }
            catch (Exception primaryError)
            {
                var backupPath = _filePath + ".bak";
                try
                {
                    if (!File.Exists(backupPath)) throw;
                    _store = LoadAndValidate(backupPath);
                    Debug.LogWarning($"[Code4101 Tiandao] recovered equipment loadouts from backup: {primaryError.Message}");
                }
                catch (Exception backupError)
                {
                    _store = null;
                    _loadBlocked = true;
                    Debug.LogError($"[Code4101 Tiandao] equipment loadouts are read-only blocked; " +
                                   $"main={primaryError}; backup={backupError}");
                    return;
                }
            }
            MigrateStore();
        }

        private static EquipmentLoadoutStore LoadAndValidate(string path)
        {
            var json = File.ReadAllText(path);
            var store = DeserializeStore(json);
            if (store == null) throw new InvalidDataException("方案文件无法解析");
            if (store.saves == null) store.saves = new List<EquipmentLoadoutSaveState>();
            if (store.version >= 4 && store.saves.Count == 0)
                throw new InvalidDataException("新版方案文件异常为空，拒绝覆盖");
            return store;
        }

        private static string SerializeStore(EquipmentLoadoutStore store)
        {
            var serializer = new DataContractJsonSerializer(typeof(EquipmentLoadoutStore));
            using var stream = new MemoryStream();
            serializer.WriteObject(stream, store);
            return Encoding.UTF8.GetString(stream.ToArray());
        }

        private static EquipmentLoadoutStore DeserializeStore(string json)
        {
            if (string.IsNullOrWhiteSpace(json)) throw new InvalidDataException("方案文件为空");
            var serializer = new DataContractJsonSerializer(typeof(EquipmentLoadoutStore));
            using var stream = new MemoryStream(Encoding.UTF8.GetBytes(json));
            return serializer.ReadObject(stream) as EquipmentLoadoutStore;
        }

        private static void MigrateStore()
        {
            if (_store == null) return;
            var sourceVersion = _store.version <= 0 ? 1 : _store.version;
            if (sourceVersion > CurrentStoreVersion)
            {
                _store = null;
                _loadBlocked = true;
                Debug.LogError($"[Code4101 Tiandao] equipment loadout schema is newer than supported: {sourceVersion}");
                return;
            }
            if (sourceVersion < CurrentStoreVersion && File.Exists(_filePath))
            {
                var snapshotPath = Path.Combine(Path.GetDirectoryName(_filePath) ?? string.Empty,
                    $"equipment-loadouts.v{sourceVersion}.json");
                if (!File.Exists(snapshotPath)) File.Copy(_filePath, snapshotPath);
            }
            foreach (var state in _store.saves.Where(state => state != null))
            {
                if (state.loadouts == null) state.loadouts = new List<EquipmentLoadoutEntity>();
                foreach (var loadout in state.loadouts.Where(loadout => loadout != null))
                {
                    if (string.IsNullOrEmpty(loadout.id)) loadout.id = Guid.NewGuid().ToString("N");
                    if (string.IsNullOrWhiteSpace(loadout.name)) loadout.name = "方案";
                    if (loadout.slots == null) loadout.slots = new List<EquipmentLoadoutSlot>();
                    foreach (var slot in BagEnhancementState.EquipmentSlots)
                    {
                        if (loadout.slots.All(item => item.slot != slot))
                            loadout.slots.Add(new EquipmentLoadoutSlot { slot = slot });
                    }
                    if (sourceVersion < 3)
                    {
                        var actorItems = BsSaveDataImpl.nowActor?.packStoList;
                        foreach (var slot in loadout.slots.Where(slot => slot != null &&
                                     slot.itemId == 0 && slot.packId != 0))
                        {
                            var legacyItem = actorItems?.FirstOrDefault(item => item != null && item.id == slot.packId);
                            if (legacyItem == null) continue;
                            slot.blendType = (int)legacyItem.itemId.blendEnum;
                            slot.itemId = legacyItem.itemId.sedId;
                        }
                    }
                }
                if (string.IsNullOrEmpty(state.activeLoadoutId) ||
                    state.loadouts.All(loadout => loadout.id != state.activeLoadoutId))
                    state.activeLoadoutId = state.loadouts.FirstOrDefault()?.id;
                state.nextLoadoutNumber = Math.Max(2, state.nextLoadoutNumber);

            }
            _store.version = CurrentStoreVersion;
            if (sourceVersion < CurrentStoreVersion && _store.saves.Count > 0) Save();
        }

        private static void MergeState(EquipmentLoadoutSaveState target, EquipmentLoadoutSaveState source)
        {
            if (target == null || source?.loadouts == null) return;
            if (target.loadouts == null) target.loadouts = new List<EquipmentLoadoutEntity>();
            foreach (var loadout in source.loadouts)
            {
                if (loadout == null) continue;
                var duplicate = target.loadouts.Any(existing => existing.id == loadout.id) ||
                                target.loadouts.Any(existing => existing.name == loadout.name &&
                                    SlotSignature(existing) == SlotSignature(loadout));
                if (!duplicate) target.loadouts.Add(loadout);
            }
            target.nextLoadoutNumber = Math.Max(target.nextLoadoutNumber, source.nextLoadoutNumber);
        }

        private static string SlotSignature(EquipmentLoadoutEntity loadout)
        {
            return string.Join(";", (loadout?.slots ?? new List<EquipmentLoadoutSlot>())
                .OrderBy(slot => slot.slot)
                .Select(slot => $"{slot.slot}:{slot.packId}:{slot.blendType}:{slot.itemId}"));
        }

        internal static List<EquipmentLoadoutSlot> CloneSlots(IEnumerable<EquipmentLoadoutSlot> slots)
        {
            return (slots ?? Enumerable.Empty<EquipmentLoadoutSlot>())
                .Where(slot => slot != null)
                .Select(slot => new EquipmentLoadoutSlot
                {
                    slot = slot.slot,
                    packId = slot.packId,
                    blendType = slot.blendType,
                    itemId = slot.itemId,
                })
                .ToList();
        }

        private static void EnsurePath()
        {
            if (!string.IsNullOrEmpty(_filePath)) return;
            var directory = Path.Combine(Application.persistentDataPath, DirectoryName);
            Directory.CreateDirectory(directory);
            _filePath = Path.Combine(directory, FileName);
        }
    }

    internal static class EquipmentLoadoutRuntime
    {
        private static bool _capturePending;
        internal static bool IsApplying { get; private set; }

        internal static void NotifyEquipmentChanged()
        {
            if (!IsApplying) _capturePending = true;
        }

        internal static void Tick()
        {
            if (!_capturePending || IsApplying || BsSaveDataImpl.nowActor == null) return;
            _capturePending = false;
            EquipmentLoadoutRepository.CaptureActive();
        }

        internal static void Flush()
        {
            if (IsApplying || BsSaveDataImpl.nowActor == null) return;
            _capturePending = false;
            EquipmentLoadoutRepository.CaptureActive();
        }

        internal static string Apply(EquipmentLoadoutEntity target)
        {
            var actor = BsSaveDataImpl.nowActor;
            var state = EquipmentLoadoutRepository.GetCurrentSaveState();
            if (actor == null || state == null || target == null) return "存档数据尚未就绪";
            if (state.activeLoadoutId == target.id) return null;

            EquipmentLoadoutRepository.CaptureActive();
            var source = EquipmentLoadoutRepository.GetActiveLoadout(state);
            var sourceSlots = EquipmentLoadoutRepository.CloneSlots(source?.slots);
            if (!TryResolve(actor, target.slots, out var targetSlots, out var resolved,
                    out var missingSlots))
            {
                var details = string.Join(", ", missingSlots.Select(item =>
                    $"槽位{item.slot}=BlendId({item.blendType},{item.itemId})/旧实例{item.packId}"));
                Debug.LogWarning($"[Code4101 Tiandao] loadout switch aborted; unresolved: {details}");
                return "方案装备未全部找到，已取消切换，当前装备保持不变";
            }

            IsApplying = true;
            try
            {
                ApplyResolved(actor, targetSlots);
                if (!Matches(actor, targetSlots))
                {
                    Debug.LogError("[Code4101 Tiandao] loadout switch verification failed; rolling back source loadout");
                    var rolledBack = false;
                    if (TryResolve(actor, sourceSlots, out var rollbackSlots, out var rollbackResolved,
                            out _))
                    {
                        ApplyResolved(actor, rollbackSlots);
                        rolledBack = Matches(actor, rollbackSlots);
                    }
                    return rolledBack
                        ? "切换失败，已恢复原方案"
                        : "切换失败，请重新打开装备界面";
                }
                EquipmentLoadoutRepository.SetActive(state, target);
                return null;
            }
            finally
            {
                IsApplying = false;
                _capturePending = false;
            }
        }

        private static bool TryResolve(TbActor actor, IEnumerable<EquipmentLoadoutSlot> slots,
            out Dictionary<int, EquipmentLoadoutSlot> bySlot,
            out Dictionary<int, TbPackSto> resolved,
            out List<EquipmentLoadoutSlot> missingSlots)
        {
            bySlot = (slots ?? Enumerable.Empty<EquipmentLoadoutSlot>())
                .Where(item => item != null)
                .GroupBy(item => item.slot)
                .ToDictionary(group => group.Key, group => group.First());
            resolved = new Dictionary<int, TbPackSto>();
            missingSlots = new List<EquipmentLoadoutSlot>();
            var usedItems = new HashSet<TbPackSto>();
            foreach (var desired in bySlot.Values.Where(item => item.itemId != 0))
            {
                // packId 是易变的背包实例身份；物品定义 (blendType, itemId) 才是稳定身份。
                var item = actor.packStoList
                    .Where(candidate => candidate != null && !usedItems.Contains(candidate))
                    .Where(candidate => (int)candidate.itemId.blendEnum == desired.blendType &&
                                        candidate.itemId.sedId == desired.itemId)
                    .OrderByDescending(candidate => candidate.id == desired.packId)
                    .ThenByDescending(candidate => candidate.npcStoId == 10000 &&
                                                   candidate.flag == desired.slot)
                    .ThenBy(candidate => candidate.id)
                    .FirstOrDefault();
                if (item == null) missingSlots.Add(desired);
                else
                {
                    resolved[desired.slot] = item;
                    usedItems.Add(item);
                }
            }
            return missingSlots.Count == 0;
        }

        private static void ApplyResolved(TbActor actor,
            IReadOnlyDictionary<int, EquipmentLoadoutSlot> slots)
        {
            var bag = Singleton<BsBagImpl>.Instance;
            foreach (var slot in BagEnhancementState.EquipmentSlots)
            {
                if (!slots.TryGetValue(slot, out var targetSlot)) continue;
                var current = actor.packStoList.FirstOrDefault(item =>
                    item != null && item.npcStoId == 10000 && item.flag == slot);
                if (targetSlot.itemId != 0)
                {
                    if (Matches(current, targetSlot)) continue;

                    // EquipItem 每执行一次都会增删/重建背包项。不能复用切换开始时
                    // 缓存的 TbPackSto 引用；每个槽位都必须从最新集合重新定位。
                    var desired = FindCurrentCandidate(actor, targetSlot, slot);
                    if (desired == null)
                    {
                        Debug.LogWarning($"[Code4101 Tiandao] slot {slot} candidate disappeared while switching");
                        continue;
                    }
                    bag.EquipItem(current, desired, slot, 10000);

                    var actual = actor.packStoList.FirstOrDefault(item =>
                        item != null && item.npcStoId == 10000 && item.flag == slot);
                    if (!Matches(actual, targetSlot))
                    {
                        // 宿主调用可能替换实例身份；刷新引用后只重试当前槽位一次。
                        current = actual;
                        desired = FindCurrentCandidate(actor, targetSlot, slot);
                        if (desired != null && desired.id != current?.id)
                            bag.EquipItem(current, desired, slot, 10000);
                    }
                }
                else if (current != null)
                {
                    bag.EquipItem(current, null, slot, 10000);
                }
            }
        }

        private static TbPackSto FindCurrentCandidate(TbActor actor,
            EquipmentLoadoutSlot desired, int targetSlot)
        {
            return actor.packStoList
                .Where(candidate => Matches(candidate, desired))
                .OrderByDescending(candidate => candidate.id == desired.packId)
                .ThenByDescending(candidate => candidate.npcStoId != 10000)
                .ThenByDescending(candidate => candidate.npcStoId == 10000 &&
                                               candidate.flag == targetSlot)
                .ThenBy(candidate => candidate.id)
                .FirstOrDefault();
        }

        private static bool Matches(TbPackSto actual, EquipmentLoadoutSlot expected)
        {
            return actual != null && expected != null && expected.itemId != 0 &&
                   (int)actual.itemId.blendEnum == expected.blendType &&
                   actual.itemId.sedId == expected.itemId;
        }

        private static bool Matches(TbActor actor,
            IReadOnlyDictionary<int, EquipmentLoadoutSlot> slots)
        {
            foreach (var slot in BagEnhancementState.EquipmentSlots)
            {
                if (!slots.TryGetValue(slot, out var expected)) continue;
                var actual = actor.packStoList.FirstOrDefault(item =>
                    item != null && item.npcStoId == 10000 && item.flag == slot);
                if (expected.itemId == 0)
                {
                    if (actual != null) return false;
                }
                else if (!Matches(actual, expected))
                {
                    return false;
                }
            }
            return true;
        }

    }

    [HarmonyLib.HarmonyPatch(typeof(BsBagImpl), nameof(BsBagImpl.EquipItem))]
    internal static class EquipmentLoadoutEquipPatch
    {
        private static void Postfix(int __2, int __3)
        {
            if (__3 == 10000 && BagEnhancementState.EquipmentSlots.Contains(__2))
                EquipmentLoadoutRuntime.NotifyEquipmentChanged();
        }
    }

    [HarmonyLib.HarmonyPatch(typeof(BsBagImpl), nameof(BsBagImpl.UnsnatchEquip))]
    internal static class EquipmentLoadoutUnequipPatch
    {
        private static void Postfix(int __1, int __2)
        {
            if (__2 == 10000 && BagEnhancementState.EquipmentSlots.Contains(__1))
                EquipmentLoadoutRuntime.NotifyEquipmentChanged();
        }
    }
}
