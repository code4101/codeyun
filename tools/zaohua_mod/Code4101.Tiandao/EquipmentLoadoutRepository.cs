using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

namespace Code4101.Zaohua.Tiandao
{
    [Serializable]
    internal sealed class EquipmentLoadoutStore
    {
        public int version = 1;
        public List<EquipmentLoadoutSaveState> saves = new List<EquipmentLoadoutSaveState>();
    }

    [Serializable]
    internal sealed class EquipmentLoadoutSaveState
    {
        public string saveKey;
        public string activeLoadoutId;
        public int nextLoadoutNumber = 2;
        public List<EquipmentLoadoutEntity> loadouts = new List<EquipmentLoadoutEntity>();
    }

    [Serializable]
    internal sealed class EquipmentLoadoutEntity
    {
        public string id;
        public string name;
        public List<EquipmentLoadoutSlot> slots = new List<EquipmentLoadoutSlot>();
    }

    [Serializable]
    internal sealed class EquipmentLoadoutSlot
    {
        public int slot;
        public int packId;
        public int blendType;
        public int itemId;
    }

    internal static class EquipmentLoadoutRepository
    {
        private const string DirectoryName = "Code4101.Tiandao";
        private const string FileName = "equipment-loadouts.json";
        private static EquipmentLoadoutStore _store;
        private static string _filePath;

        internal static EquipmentLoadoutSaveState GetCurrentSaveState(bool create = true)
        {
            var actor = BsSaveDataImpl.nowActor;
            if (actor?.fileSto == null) return null;
            EnsureLoaded();
            var key = $"{actor.id}:{actor.fileSto.buildTime}";
            var state = _store.saves.FirstOrDefault(item => item.saveKey == key);
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

        internal static EquipmentLoadoutEntity CreateEmptyLoadout()
        {
            CaptureActive();
            var state = GetCurrentSaveState();
            if (state == null) return null;
            var number = Math.Max(2, state.nextLoadoutNumber);
            state.nextLoadoutNumber = number + 1;
            var entity = new EquipmentLoadoutEntity
            {
                id = Guid.NewGuid().ToString("N"),
                name = $"方案{number}",
                slots = BagEnhancementState.EquipmentSlots
                    .Select(slot => new EquipmentLoadoutSlot { slot = slot })
                    .ToList(),
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
            if (_store == null) return;
            try
            {
                EnsurePath();
                var json = JsonUtility.ToJson(_store, true);
                var temporaryPath = _filePath + ".tmp";
                var backupPath = _filePath + ".bak";
                File.WriteAllText(temporaryPath, json);
                if (File.Exists(_filePath))
                {
                    if (File.Exists(backupPath)) File.Delete(backupPath);
                    File.Replace(temporaryPath, _filePath, backupPath);
                    if (File.Exists(backupPath)) File.Delete(backupPath);
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
            if (_store != null) return;
            EnsurePath();
            if (!File.Exists(_filePath))
            {
                _store = new EquipmentLoadoutStore();
                return;
            }
            try
            {
                _store = JsonUtility.FromJson<EquipmentLoadoutStore>(File.ReadAllText(_filePath)) ??
                         new EquipmentLoadoutStore();
                if (_store.saves == null) _store.saves = new List<EquipmentLoadoutSaveState>();
                foreach (var state in _store.saves)
                {
                    if (state.loadouts == null) state.loadouts = new List<EquipmentLoadoutEntity>();
                    foreach (var loadout in state.loadouts)
                    {
                        if (loadout.slots == null) loadout.slots = new List<EquipmentLoadoutSlot>();
                    }
                }
            }
            catch (Exception error)
            {
                Debug.LogError($"[Code4101 Tiandao] load equipment loadouts failed: {error}");
                _store = new EquipmentLoadoutStore();
            }
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
            var resolved = new Dictionary<int, TbPackSto>();
            foreach (var desired in target.slots.Where(item => item.packId != 0))
            {
                // 配装引用的是具体物品实例。实例已经出售、丢弃或消耗后，该槽位必须留空，
                // 不能按配置 ID 找另一件同名装备代替，也不能因此阻止其他槽位切换。
                var item = actor.packStoList.FirstOrDefault(candidate => candidate.id == desired.packId);
                if (item != null) resolved[desired.slot] = item;
            }

            IsApplying = true;
            try
            {
                var bag = Singleton<BsBagImpl>.Instance;
                foreach (var slot in BagEnhancementState.EquipmentSlots)
                {
                    var current = actor.packStoList.FirstOrDefault(item => item.npcStoId == 10000 && item.flag == slot);
                    if (resolved.TryGetValue(slot, out var desired))
                    {
                        if (current?.id != desired.id) bag.EquipItem(current, desired, slot, 10000);
                    }
                    else if (current != null)
                    {
                        bag.EquipItem(current, null, slot, 10000);
                    }
                }
                EquipmentLoadoutRepository.SetActive(state, target);
                EquipmentLoadoutRepository.CaptureActive();
                return null;
            }
            finally
            {
                IsApplying = false;
                _capturePending = false;
            }
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
