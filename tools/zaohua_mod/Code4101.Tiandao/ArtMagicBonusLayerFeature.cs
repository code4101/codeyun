using System;
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

                // “To” 查询的是其他图形施加到当前功法的效果；相邻类规则的倍率
                // 属于当前功法作为来源产生的效果，因此必须按 fromUpdate 查询。
                var bonuses = Singleton<DantianController>.Instance.GetDantianUpListByArtMagicId(
                    artMagicId, npcSto.id);
                if (bonuses == null || bonuses.Count == 0) return;

                var summaries = bonuses
                    .Where(item => item != null && item.UpMultiplier > 0)
                    .GroupBy(item => item.drawStateId)
                    .Select(group =>
                    {
                        var rule = Singleton<TbDantianImpl>.Instance.GetDrawStateCfgById(group.Key);
                        if (rule == null || string.IsNullOrEmpty(rule.equipEff)) return null;
                        // 同一规则可能把相同倍率施加给多个目标，不能把目标记录再次累加。
                        var multiplier = group.Max(item => item.UpMultiplier);
                        var effect = Singleton<BsEquipEffectImpl>.Instance.GetEquipEffectStr(
                            rule.equipEff, multiplier);
                        if (string.IsNullOrWhiteSpace(effect)) return null;
                        return $"<color=#FFD36A>当前增幅：×{multiplier}</color>（{effect}）";
                    })
                    .Where(text => !string.IsNullOrEmpty(text))
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
    }
}
