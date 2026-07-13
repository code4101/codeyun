using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;

namespace Code4101.Zaohua.Tiandao
{
    [HarmonyPatch(typeof(ArtMagicTipContrast), "SetDrawEffDes")]
    internal static class ArtMagicBonusLayerTipPatch
    {
        private static void Postfix(ArtMagicTipContrast __instance)
        {
            try
            {
                if (__instance == null) return;
                var fields = Traverse.Create(__instance);
                var type = fields.Field<int>("type").Value;
                var npcSto = fields.Field<TbNpcSto>("npcSto").Value;
                var artMagicId = fields.Field<BlendId>("magicCfgId").Value;
                // type=1 是功法；术法、技能不走丹田相邻加成投影。
                if (type != 1 || npcSto == null) return;
                var label = __instance.View?.drawStateEff?.txtEffDes;
                if (label == null) return;

                var summaries = GetCurrentMultipliers(artMagicId, npcSto.id)
                    .Select(multiplier => $"<color=#FFD36A>当前增幅 ×{multiplier}</color>")
                    .ToList();
                if (summaries.Count == 0) return;

                var addition = string.Join("\n", summaries);
                label.text = string.IsNullOrWhiteSpace(label.text)
                    ? addition
                    : label.text.TrimEnd() + "\n" + addition;
            }
            catch (Exception error)
            {
                UnityEngine.Debug.LogWarning($"[Code4101 Tiandao] art bonus layer tip skipped: {error.Message}");
            }
        }

        private static List<int> GetCurrentMultipliers(BlendId artMagicId, int npcStoId)
        {
            var artImpl = Singleton<TbArtImpl>.Instance;
            var artCfg = artImpl.GetArtCfg(artMagicId);
            if (artCfg == null || string.IsNullOrEmpty(artCfg.drawStateId)) return new List<int>();
            var calculated = BsSaveDataImpl.nowActor?.dantianUpStoList ??
                             new List<TbDantianUpSto>();
            var multipliers = new List<int>();
            foreach (var rawId in artCfg.drawStateId.Split('&'))
            {
                if (!int.TryParse(rawId, out var ruleId)) continue;
                // 原生 UpdateDanTianUp/AddDantianUpByArtSto 已把最终计算结果写入这里。
                // 同一来源与规则可能对多个目标生成记录，但增幅倍数相同；取最大值
                // 可避免把“受影响目标数量”误当成增幅层数。
                var values = calculated
                    .Where(item => item != null && item.npcStoId == npcStoId &&
                                   item.drawStateId == ruleId &&
                                   // 原生写入 fromUpdate 时使用 ArtCfg 的规范身份，
                                   // 不一定等于悬浮框传入的展示/实例 BlendId。
                                   item.fromUpdate.blendEnum == artCfg.blendEnum &&
                                   item.fromUpdate.sedId == artCfg.id)
                    .Select(item => item.UpMultiplier)
                    .ToList();
                multipliers.Add(values.Count == 0 ? 0 : values.Max());
            }
            return multipliers;
        }
    }
}
