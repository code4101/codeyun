"""突破前的策略与客户端准入分别判定；不点击，不把 A 数量当仙品门槛。"""
from dataclasses import dataclass
from typing import Sequence

from .spirit_artifact_yinxian import YinxianAttribute


@dataclass(frozen=True)
class BreakthroughPreparation:
    action: str  # collect_a / supplement_b / ready / blocked
    target_code: str | None = None
    reason: str = ''


def plan_breakthrough_preparation(
    current: Sequence[YinxianAttribute], *, a_codes: set[str],
    client_can_breakthrough: bool, is_break: bool,
    required_red_count: int,
    b_preference: tuple[str, ...] | None = None,
) -> BreakthroughPreparation:
    """调用方传入当前客户端控件条件及该配置已核实的仙品数量要求。

    策略 A 集合为四种，必须全部满值；不再存在剑三 A 加红 B 的例外。
    本版只处理已存在的 B 槽位，不选择新的来源、不牺牲 A、不执行突破。
    配置与客户端就绪标志矛盾、已有仙品数量足够却不就绪时保留现场。
    """
    from ...catalog.spirit_artifact_wash_rules import BASE_ATTRIBUTE_PRIORITY
    if b_preference is None:
        b_preference = tuple(code for code in BASE_ATTRIBUTE_PRIORITY if code not in a_codes)
    if (len(current) != 6 or len({e.code for e in current}) != 6 or len(a_codes) != 4
            or type(client_can_breakthrough) is not bool or type(is_break) is not bool
            or type(required_red_count) is not int or not 1 <= required_red_count <= 6
            or len(set(b_preference)) != len(b_preference) or a_codes & set(b_preference)):
        raise ValueError('突破准备需要完整属性、明确配置和客户端真实布尔条件')
    if is_break:
        return BreakthroughPreparation('blocked', reason='已突破，不重复执行突破')
    full_a = {e.code for e in current if e.quality >= 6 and e.is_full and e.code in a_codes}
    if not a_codes <= full_a:
        return BreakthroughPreparation('collect_a')
    red_count = sum(e.quality >= 6 for e in current)
    if client_can_breakthrough:
        if red_count < required_red_count:
            return BreakthroughPreparation('blocked', reason='客户端就绪与已核实仙品数量配置矛盾')
        return BreakthroughPreparation('ready')
    if red_count >= required_red_count:
        return BreakthroughPreparation('blocked', reason='仙品数量已满足但客户端仍未就绪，需核对其他条件')
    by_code = {e.code: e for e in current}
    for code in b_preference:
        if code in by_code and by_code[code].quality < 6:
            return BreakthroughPreparation('supplement_b', target_code=code)
    return BreakthroughPreparation('blocked', reason='缺仙品且没有已存在的可补 B 槽位')
