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
        private const int CurrentStoreVersion = 7;
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
            var previousName = entity.name;
            entity.name = normalized.Length > 12 ? normalized.Substring(0, 12) : normalized;
            Save();
            Debug.Log($"[Code4101 Tiandao][Loadout] renamed id={entity.id} " +
                      $"from={previousName} to={entity.name}");
            return true;
        }

        internal static bool Delete(EquipmentLoadoutEntity entity)
        {
            var state = GetCurrentSaveState(false);
            if (state?.loadouts == null || entity == null || state.loadouts.Count <= 1 ||
                state.activeLoadoutId == entity.id)
                return false;
            var removed = state.loadouts.RemoveAll(item => item != null && item.id == entity.id) > 0;
            if (!removed) return false;
            Save();
            Debug.Log($"[Code4101 Tiandao][Loadout] deleted id={entity.id} name={entity.name} " +
                      $"active={state.activeLoadoutId} remaining={state.loadouts.Count}");
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
                    // packId 是会随拆分、合并、装备操作变化的背包实例 ID。
                    // v3 起已有稳定的物品定义 ID，迁移后彻底丢弃实例身份。
                    foreach (var slot in loadout.slots.Where(slot => slot != null))
                        slot.packId = 0;
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
                .Select(slot => $"{slot.slot}:{slot.blendType}:{slot.itemId}"));
        }

        internal static List<EquipmentLoadoutSlot> CloneSlots(IEnumerable<EquipmentLoadoutSlot> slots)
        {
            return (slots ?? Enumerable.Empty<EquipmentLoadoutSlot>())
                .Where(slot => slot != null)
                .Select(slot => new EquipmentLoadoutSlot
                {
                    slot = slot.slot,
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
        private static long _switchSequence;
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

            var traceId = (++_switchSequence).ToString("D4");
            EquipmentLoadoutRepository.CaptureActive();
            var source = EquipmentLoadoutRepository.GetActiveLoadout(state);
            var sourceSlots = EquipmentLoadoutRepository.CloneSlots(source?.slots);
            Trace(traceId, $"begin actor={actor.id} source={DescribeLoadout(source)} " +
                           $"target={DescribeLoadout(target)} targets=[{DescribeSlots(target.slots)}]");
            Trace(traceId, $"inventory-before=[{DescribeInventory(actor, target.slots)}]");
            TryResolve(actor, target.slots, out var targetSlots, out var missingSlots);
            if (missingSlots.Count > 0)
            {
                var details = string.Join(", ", missingSlots.Select(item =>
                    $"槽位{item.slot}=BlendId({item.blendType},{item.itemId})"));
                TraceWarning(traceId, $"missing-items clear-target-slots=[{details}]");
                foreach (var missing in missingSlots)
                {
                    missing.packId = 0;
                    missing.blendType = 0;
                    missing.itemId = 0;
                }
                // 缺失代表玩家确实已不再持有该数量。目标方案继续切换，
                // 对应槽位留空并同步修正方案，避免以后每次切换重复失败。
                EquipmentLoadoutRepository.Save();
            }

            IsApplying = true;
            try
            {
                ApplyResolved(actor, targetSlots, traceId);
                if (!Matches(actor, targetSlots))
                {
                    TraceError(traceId, $"verify-failed actual=[{DescribeEquipped(actor)}]; rollback-start");
                    var rolledBack = false;
                    if (TryResolve(actor, sourceSlots, out var rollbackSlots, out _))
                    {
                        ApplyResolved(actor, rollbackSlots, traceId + "R");
                        rolledBack = Matches(actor, rollbackSlots);
                    }
                    TraceError(traceId, $"rollback-finished success={rolledBack} actual=[{DescribeEquipped(actor)}]");
                    return rolledBack
                        ? $"切换失败，已恢复原方案（日志编号 {traceId}）"
                        : $"切换失败，请重新打开装备界面（日志编号 {traceId}）";
                }
                EquipmentLoadoutRepository.SetActive(state, target);
                Trace(traceId, $"success active={DescribeLoadout(target)} actual=[{DescribeEquipped(actor)}]");
                return null;
            }
            catch (Exception error)
            {
                TraceError(traceId, $"exception type={error.GetType().Name} message={error.Message} " +
                                    $"actual=[{DescribeEquipped(actor)}]\n{error}");
                var rolledBack = false;
                try
                {
                    if (TryResolve(actor, sourceSlots, out var rollbackSlots, out _))
                    {
                        ApplyResolved(actor, rollbackSlots, traceId + "E");
                        rolledBack = Matches(actor, rollbackSlots);
                    }
                }
                catch (Exception rollbackError)
                {
                    TraceError(traceId, $"exception-rollback-failed {rollbackError}");
                }
                TraceError(traceId, $"exception-rollback-finished success={rolledBack} " +
                                    $"actual=[{DescribeEquipped(actor)}]");
                return rolledBack
                    ? $"切换异常，已恢复原方案（日志编号 {traceId}）"
                    : $"切换异常，请重新打开装备界面（日志编号 {traceId}）";
            }
            finally
            {
                IsApplying = false;
                _capturePending = false;
            }
        }

        private static bool TryResolve(TbActor actor, IEnumerable<EquipmentLoadoutSlot> slots,
            out Dictionary<int, EquipmentLoadoutSlot> bySlot,
            out List<EquipmentLoadoutSlot> missingSlots)
        {
            bySlot = (slots ?? Enumerable.Empty<EquipmentLoadoutSlot>())
                .Where(item => item != null)
                .GroupBy(item => item.slot)
                .ToDictionary(group => group.Key, group => group.First());
            missingSlots = new List<EquipmentLoadoutSlot>();

            // 同一个背包条目可以是数量大于 1 的堆叠。饰品三个槽位也允许使用
            // 同一种物品，因此这里按玩家实际持有数量分配，不能按 TbPackSto 引用去重。
            // npcStoId 必须限定为玩家，避免同类装备误命中其他 NPC 的背包。
            var availableCounts = actor.packStoList
                .Where(item => item != null && item.npcStoId == 10000 && item.haveCount > 0)
                .GroupBy(item => $"{(int)item.itemId.blendEnum}:{item.itemId.sedId}")
                .ToDictionary(group => group.Key, group => group.Sum(item => item.haveCount));
            foreach (var desired in bySlot.Values
                         .Where(item => item.itemId != 0)
                         .OrderBy(item => item.slot))
            {
                var key = $"{desired.blendType}:{desired.itemId}";
                if (!availableCounts.TryGetValue(key, out var count) || count <= 0)
                {
                    missingSlots.Add(desired);
                    continue;
                }
                availableCounts[key] = count - 1;
            }
            return missingSlots.Count == 0;
        }

        private static void ApplyResolved(TbActor actor,
            IReadOnlyDictionary<int, EquipmentLoadoutSlot> slots, string traceId)
        {
            var bag = Singleton<BsBagImpl>.Instance;

            // 第一阶段统一卸下所有不符合目标槽位的装备。这样交换槽位、以及同类
            // 饰品从多个实例重新合并成堆叠时，第二阶段看到的是稳定的最新背包。
            foreach (var slot in BagEnhancementState.EquipmentSlots)
            {
                if (!slots.TryGetValue(slot, out var targetSlot)) continue;
                var current = actor.packStoList.FirstOrDefault(item =>
                    item != null && item.npcStoId == 10000 && item.flag == slot);
                if (current != null &&
                    (targetSlot.itemId == 0 || !Matches(current, targetSlot)))
                {
                    Trace(traceId, $"unequip slot={slot} item={DescribeItem(current)}");
                    bag.EquipItem(current, null, slot, 10000);
                    var after = actor.packStoList.FirstOrDefault(item =>
                        item != null && item.npcStoId == 10000 && item.flag == slot);
                    Trace(traceId, $"unequip-result slot={slot} empty={after == null} actual={DescribeItem(after)}");
                }
            }

            // 第二阶段逐槽穿戴。每次穿戴都会拆分堆叠并可能重建 TbPackSto，
            // 所以相同饰品的第二、第三份也必须从实时背包重新查询。
            foreach (var slot in BagEnhancementState.EquipmentSlots)
            {
                if (!slots.TryGetValue(slot, out var targetSlot) || targetSlot.itemId == 0)
                    continue;
                var current = actor.packStoList.FirstOrDefault(item =>
                    item != null && item.npcStoId == 10000 && item.flag == slot);
                if (Matches(current, targetSlot)) continue;

                var desired = FindCurrentCandidate(actor, targetSlot);
                if (desired == null)
                {
                    TraceWarning(traceId, $"candidate-missing slot={slot} " +
                                          $"target={DescribeSlot(targetSlot)} inventory=[{DescribeInventory(actor, new[] { targetSlot })}]");
                    continue;
                }
                Trace(traceId, $"equip slot={slot} target={DescribeSlot(targetSlot)} " +
                               $"candidate={DescribeItem(desired)}");
                bag.EquipItem(current, desired, slot, 10000);

                var actual = actor.packStoList.FirstOrDefault(item =>
                    item != null && item.npcStoId == 10000 && item.flag == slot);
                Trace(traceId, $"equip-result slot={slot} matched={Matches(actual, targetSlot)} " +
                               $"actual={DescribeItem(actual)}");
                if (!Matches(actual, targetSlot))
                {
                    // 宿主调用可能替换实例身份；刷新引用后只重试当前槽位一次。
                    current = actual;
                    desired = FindCurrentCandidate(actor, targetSlot);
                    if (desired != null && desired.id != current?.id)
                    {
                        TraceWarning(traceId, $"equip-retry slot={slot} candidate={DescribeItem(desired)}");
                        bag.EquipItem(current, desired, slot, 10000);
                        actual = actor.packStoList.FirstOrDefault(item =>
                            item != null && item.npcStoId == 10000 && item.flag == slot);
                        Trace(traceId, $"equip-retry-result slot={slot} matched={Matches(actual, targetSlot)} " +
                                       $"actual={DescribeItem(actual)}");
                    }
                }
            }
        }

        private static TbPackSto FindCurrentCandidate(TbActor actor,
            EquipmentLoadoutSlot desired)
        {
            return actor.packStoList
                .Where(candidate => candidate != null && candidate.npcStoId == 10000 &&
                                    candidate.flag == 0 && candidate.haveCount > 0 &&
                                    Matches(candidate, desired))
                .OrderByDescending(candidate => candidate.haveCount)
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

        private static string DescribeLoadout(EquipmentLoadoutEntity loadout)
        {
            return loadout == null ? "null" : $"{loadout.name}({loadout.id})";
        }

        private static string DescribeSlot(EquipmentLoadoutSlot slot)
        {
            return slot == null || slot.itemId == 0
                ? $"slot={slot?.slot ?? 0}:empty"
                : $"slot={slot.slot}:BlendId({slot.blendType},{slot.itemId})";
        }

        private static string DescribeSlots(IEnumerable<EquipmentLoadoutSlot> slots)
        {
            return string.Join(", ", (slots ?? Enumerable.Empty<EquipmentLoadoutSlot>())
                .Where(slot => slot != null)
                .OrderBy(slot => slot.slot)
                .Select(DescribeSlot));
        }

        private static string DescribeItem(TbPackSto item)
        {
            return item == null
                ? "null"
                : $"pack={item.id}/BlendId({(int)item.itemId.blendEnum},{item.itemId.sedId})" +
                  $"/owner={item.npcStoId}/flag={item.flag}/count={item.haveCount}";
        }

        private static string DescribeEquipped(TbActor actor)
        {
            return string.Join(", ", BagEnhancementState.EquipmentSlots.Select(slot =>
            {
                var item = actor?.packStoList?.FirstOrDefault(candidate =>
                    candidate != null && candidate.npcStoId == 10000 && candidate.flag == slot);
                return $"slot={slot}:{DescribeItem(item)}";
            }));
        }

        private static string DescribeInventory(TbActor actor,
            IEnumerable<EquipmentLoadoutSlot> targets)
        {
            var keys = new HashSet<string>((targets ?? Enumerable.Empty<EquipmentLoadoutSlot>())
                .Where(slot => slot != null && slot.itemId != 0)
                .Select(slot => $"{slot.blendType}:{slot.itemId}"));
            return string.Join(", ", (actor?.packStoList ?? new List<TbPackSto>())
                .Where(item => item != null && item.npcStoId == 10000 && item.haveCount > 0)
                .Where(item => keys.Contains($"{(int)item.itemId.blendEnum}:{item.itemId.sedId}"))
                .OrderBy(item => (int)item.itemId.blendEnum)
                .ThenBy(item => item.itemId.sedId)
                .ThenBy(item => item.flag)
                .ThenBy(item => item.id)
                .Select(DescribeItem));
        }

        private static void Trace(string traceId, string message)
        {
            Debug.Log($"[Code4101 Tiandao][Loadout:{traceId}] {message}");
        }

        private static void TraceWarning(string traceId, string message)
        {
            Debug.LogWarning($"[Code4101 Tiandao][Loadout:{traceId}] {message}");
        }

        private static void TraceError(string traceId, string message)
        {
            Debug.LogError($"[Code4101 Tiandao][Loadout:{traceId}] {message}");
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
