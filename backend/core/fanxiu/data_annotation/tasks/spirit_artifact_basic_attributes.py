"""洗灵第一步：凑齐气血、灵力、攻击、守御；纯决策，不读取或操作游戏。

调用方将 Runtime 词条 ID 通过正式配置映射为名称，再传入本模块。
本步骤只收集属性种类，不按颜色、数值或总评分取舍。每次仅返回下一动作，
动作成功后用最新事实再次决策；不将本决策结果当作执行成功证据。
"""

from dataclasses import dataclass
from typing import Literal, Sequence


BASIC_ATTRIBUTES = frozenset({'气血', '灵力', '攻击', '守御'})


@dataclass(frozen=True)
class WashAttribute:
    cleanse_id: int
    name: str
    value: int
    quality: int
    locked: bool

    @property
    def canonical_name(self) -> str:
        return '守御' if self.name == '防御' else self.name


@dataclass(frozen=True)
class BasicAttributeDecision:
    action: Literal['lock', 'wash', 'retain', 'complete', 'blocked']
    lock_ids: tuple[int, ...] = ()
    reason: str = ''


def plan_basic_attributes(
    current: Sequence[WashAttribute], candidate: Sequence[WashAttribute] = (),
) -> BasicAttributeDecision:
    """当前和候选均为完整六条；候选为空表示没有待保存结果。

Runtime map 的遍历顺序不是 UI 行序，本模块只返回词条 ID，不推测点击行。
保存后必须重新绑定 UI 行序。非目标已锁项的解锁策略尚未讨论，显式阻塞。
"""
    for rows in (current, candidate):
        if rows and (len(rows) != 6 or len({e.cleanse_id for e in rows}) != 6
                     or any(e.cleanse_id <= 0 or not e.name or type(e.locked) is not bool for e in rows)):
            raise ValueError('需要完整、唯一且有名称与锁状态的六条属性')
    if not current:
        raise ValueError('当前属性不能为空')
    present = {e.canonical_name for e in current} & BASIC_ATTRIBUTES
    if candidate:
        pending_by_id = {e.cleanse_id: e for e in candidate}
        for effect in current:
            if effect.locked and pending_by_id.get(effect.cleanse_id) != effect:
                return BasicAttributeDecision('blocked', reason='候选未保留锁定属性，需重新校准')
        offered = {e.canonical_name for e in candidate} & BASIC_ATTRIBUTES
        if present < offered:
            return BasicAttributeDecision('retain', reason='保留已有目标种类，并新增缺失种类')
        # 不接受仅数值提升、重复已有种类，或新增一种却丢失另一种的结果。
    missing_locks = tuple(e.cleanse_id for e in current
                          if e.canonical_name in BASIC_ATTRIBUTES and not e.locked)
    if candidate and missing_locks:
        return BasicAttributeDecision('blocked', reason='已有目标尚未锁定且存在候选，需先处理当前现场')
    if missing_locks:
        return BasicAttributeDecision('lock', missing_locks)
    if present == BASIC_ATTRIBUTES:
        if candidate:
            return BasicAttributeDecision('blocked', reason='四项已齐但仍有待处理候选，不能宣布收尾')
        return BasicAttributeDecision('complete')
    if any(e.locked and e.canonical_name not in BASIC_ATTRIBUTES for e in current):
        return BasicAttributeDecision('blocked', reason='存在非目标锁定项，解锁策略尚未定义')
    return BasicAttributeDecision('wash', reason='继续寻找缺失的基础属性')
