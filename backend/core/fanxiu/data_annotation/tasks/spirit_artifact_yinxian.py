"""引仙石候选筛选、完整样本与精炼锁计划；纯计算，不读写游戏。"""

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Sequence


# 所需 A 类达到基础满值的此比例后转入精炼；最终仍须满值再突破。
DEFAULT_PREPARED_TARGET_RATIO = .92


# 用户明确的 B 类保留顺序：灵力 > 气血 > 守御。未知身份不推断优先级。
B_RETENTION_PRIORITY = MappingProxyType({'MAXMP': 3, 'MAXHP': 2, 'DEFENSE': 1})


def meets_yinxian_target(
    *, is_needed_a: bool, basic_score: int | float | Decimal,
    basic_full_score: int, target_ratio: float | Decimal = DEFAULT_PREPARED_TARGET_RATIO,
) -> bool:
    """需要的 A 类达到该部位基础满分的目标比例。

    类别由上层按官方属性身份映射；不能把所有专属属性都当 A 类。
    basic_score 使用对应灵器/培养状态的100分基准归一化；满分为100或150。
    本规则只覆盖当前突破前的基础满分，不推测巅峰石生效上限是否作分母。
    """
    score, ratio = Decimal(str(basic_score)), Decimal(str(target_ratio))
    if (type(is_needed_a) is not bool
            or basic_full_score not in (100, 150) or not score.is_finite() or score < 0
            or not ratio.is_finite() or not 0 < ratio <= 1):
        raise ValueError('引仙石筛选需要有效类别、基础分、满分和目标比例')
    return is_needed_a and score >= Decimal(basic_full_score) * ratio


@dataclass(frozen=True)
class YinxianAttribute:
    cleanse_id: int
    code: str
    value: int
    quality: int
    locked: bool
    normal_max: int
    basic_full_score: int = 100

    def __post_init__(self):
        if (self.cleanse_id <= 0 or not self.code or type(self.value) is not int or self.value < 0
                or type(self.quality) is not int or self.quality < 0 or type(self.locked) is not bool
                or type(self.normal_max) is not int or self.normal_max <= 0
                or self.basic_full_score not in (100, 150)):
            raise ValueError('候选统计需要完整词条身份、值、品质、锁及对应基础上限')

    @property
    def ratio(self) -> Decimal:
        return Decimal(self.value) / self.normal_max

    @property
    def is_full(self) -> bool:
        return self.value >= self.normal_max


@dataclass(frozen=True)
class YinxianSample:
    roll_index: int
    unlocked_candidates: tuple[YinxianAttribute, ...]
    highest_red_ratio: Decimal | None
    hits: tuple[YinxianAttribute, ...]

    @property
    def stop_yinxian(self) -> bool:
        return bool(self.hits)


def analyze_yinxian_sample(
    candidates: Sequence[YinxianAttribute], *, roll_index: int,
    needed_a_codes: set[str], target_ratio: float | Decimal = DEFAULT_PREPARED_TARGET_RATIO,
) -> YinxianSample:
    """每次实际消耗产生独立样本；不按命中/颜色筛样本，不按内容去重。

    candidates 是完整五／六条候选。统计包含全部未锁项，红条最高比例不限定 A；
    任意需要的未锁 A 达阈值就停止继续引仙石，hits 保留本轮全部命中。
    本函数不自动保存；保存前仍需验证候选来自本轮且没有改动原锁定项。
    """
    if roll_index <= 0 or len(candidates) not in (5, 6) or len({e.cleanse_id for e in candidates}) != len(candidates):
        raise ValueError('引仙石样本需要独立轮次和完整五／六条候选')
    unlocked = tuple(e for e in candidates if not e.locked)
    # 即使没有目标或没有红条，也验证配置比例，避免错误配置被空集合掩盖。
    meets_yinxian_target(is_needed_a=False, basic_score=0,
                         basic_full_score=100, target_ratio=target_ratio)
    hits = tuple(e for e in unlocked if meets_yinxian_target(
        is_needed_a=e.code in needed_a_codes,
        basic_score=e.ratio * e.basic_full_score, basic_full_score=e.basic_full_score,
        target_ratio=target_ratio))
    return YinxianSample(roll_index, unlocked,
                         max((e.ratio for e in unlocked if e.quality >= 6), default=None), hits)


def refine_other_lock_ids(current: Sequence[YinxianAttribute], target_cleanse_id: int) -> tuple[int, ...]:
    """当前已保存六条里，只留选中目标可精炼，其余 N−1 条都应锁。

    返回期望锁集合，由既有锁账本仅切换差异；不选择目标、不点击或消耗。
    目标已满时调用方应跳过精炼消耗，并进入锁满 A、解除 C 锁的下一步。
    """
    if (len(current) not in (5, 6) or len({e.cleanse_id for e in current}) != len(current)
            or target_cleanse_id not in {e.cleanse_id for e in current}):
        raise ValueError('精炼锁计划需要已保存的完整五／六条及明确目标')
    return tuple(e.cleanse_id for e in current if e.cleanse_id != target_cleanse_id)


@dataclass(frozen=True)
class ACollectionPlan:
    action: str  # locks / yinxian / refine / complete / blocked
    desired_lock_ids: tuple[int, ...]
    target_cleanse_id: int | None = None
    reason: str = ''


def plan_b_supplement(current: Sequence[YinxianAttribute], *, a_codes: set[str],
                      target_code: str) -> ACollectionPlan:
    """A 全满后，将已存在的指定 B 洗至红色；不要求比例或精炼。"""
    if (len(current) not in (5, 6) or len({e.cleanse_id for e in current}) != len(current)
            or len({e.code for e in current}) != len(current) or not a_codes
            or target_code not in {'MAXMP', 'MAXHP', 'DEFENSE'} or target_code in a_codes):
        raise ValueError('补 B 需要完整唯一属性和明确 B 目标')
    if not a_codes <= {e.code for e in current if e.is_full and e.quality >= 6}:
        return ACollectionPlan('blocked', (), reason='补 B 前全部 A 必须已满')
    matches = [e for e in current if e.code == target_code]
    if len(matches) != 1:
        return ACollectionPlan('blocked', (), reason='补 B 仅支持已存在的目标槽位')
    target = matches[0]
    actual = {e.cleanse_id for e in current if e.locked}
    if target.quality >= 6:
        return ACollectionPlan('complete', tuple(sorted(actual)))
    desired = tuple(sorted(e.cleanse_id for e in current if e.cleanse_id != target.cleanse_id))
    return ACollectionPlan('locks' if actual != set(desired) else 'yinxian', desired)


def plan_a_collection(
    current: Sequence[YinxianAttribute], *, a_codes: set[str], c_codes: set[str],
    b_codes: set[str], target_ratio: float | Decimal = DEFAULT_PREPARED_TARGET_RATIO,
) -> ACollectionPlan:
    """根据已保存事实计算下一步，不依赖上一 Cell 的步骤游标。

    调用前必须处理待保存候选；每次采用候选后重新调用。多个达标 A 逐个
    精炼，其余 N−1 条全锁；完成后仅释放 C 和未达门槛的 A，保留 B。
    仍缺 A 且无 C/低 A 槽位时，释放保留优先级最低的 B；未知 B 则阻塞。
    执行方先验证锁计划，再消耗，
    每次真实消耗独立记录样本；本函数既不读写游戏，也不宣称动作已成功。
    """
    if (len(current) not in (5, 6) or len({e.cleanse_id for e in current}) != len(current)
            or len({e.code for e in current}) != len(current) or not a_codes
            or a_codes & b_codes or a_codes & c_codes or b_codes & c_codes):
        raise ValueError('A 类培养需要完整唯一词条及互斥类别')
    meets_yinxian_target(is_needed_a=False, basic_score=0,
                         basic_full_score=100, target_ratio=target_ratio)
    if any(e.code not in a_codes | b_codes | c_codes for e in current):
        return ACollectionPlan('blocked', (), reason='存在本阶段未定义的属性类别')
    full = {e.code for e in current if e.quality >= 6 and e.is_full}
    actual_locks = {e.cleanse_id for e in current if e.locked}
    if a_codes <= full:
        # 客户端至少留一条未锁。培养完成不强求六条全锁；下一阶段先
        # 解锁其目标，再锁住本轮刚精炼项，避免无效的第六把锁。
        return ACollectionPlan('complete', tuple(sorted(actual_locks)))
    # 稳定身份排序不依赖 Runtime map 遍历顺序，也不能用此排序作为 UI 行号。
    refinable = sorted((e for e in current if e.code in a_codes - full
                        and meets_yinxian_target(
                            is_needed_a=True,
                            basic_score=e.ratio * e.basic_full_score,
                            basic_full_score=e.basic_full_score, target_ratio=target_ratio)),
                       key=lambda e: e.cleanse_id)
    target = refinable[0] if refinable else None
    if target:
        desired = tuple(sorted(refine_other_lock_ids(current, target.cleanse_id)))
        action = 'refine'
    else:
        desired = tuple(sorted(e.cleanse_id for e in current
                               if e.code in b_codes or e.code in a_codes & full))
        action = 'complete' if a_codes <= full else 'yinxian'
        if action == 'yinxian' and len(desired) == len(current):
            retained_b = [e for e in current if e.code in b_codes]
            if not retained_b or any(e.code not in B_RETENTION_PRIORITY for e in retained_b):
                return ACollectionPlan('blocked', desired, reason='没有可用槽位，B 类保留优先级未知')
            released = min(retained_b, key=lambda e: B_RETENTION_PRIORITY[e.code])
            desired = tuple(i for i in desired if i != released.cleanse_id)
    return ACollectionPlan('locks' if actual_locks != set(desired) else action,
                           desired, target.cleanse_id if target else None,
                           reason=('精炼目标 A：临时锁住其余 N−1 条（含 C），精炼结束后重算'
                                   if target else '引仙寻找缺失 A：解除 C 与未达标 A 的旧锁，保护满 A 并按需释放 B'))
