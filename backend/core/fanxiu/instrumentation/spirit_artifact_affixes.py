"""灵器洗炼词缀：复用客户端 GetMaxValueTypeShow 的阈值和图标语义。

SpiritWareWashAttrItem 仅在 cleanseCfg.full == 0 时显示图标。
0002=满，1002=粉色巅，1003=蓝色巅；品质颜色与这个判定互相独立。
"""

from __future__ import annotations

import math
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError


def classify_spirit_artifact_affix(value: int, maximum: int, ratio_percent: float, full: int = 0) -> dict[str, Any]:
    """按客户端分支顺序计算档位；full 非零只隐藏词缀，不改变数值档位。"""
    peak = math.floor(maximum * (ratio_percent * 0.01))
    tier = 3 if value >= peak else 2 if value > maximum else 1 if value == maximum else 0
    return {"affix": ("", "满", "巅", "巅")[tier] if full == 0 else "",
            "max_type": tier, "normal_max": maximum, "peak_max": peak,
            "affix_icon": ("", "lingqi_zw_word_0002", "lingqi_zw_word_1002", "lingqi_zw_word_1003")[tier] if full == 0 else "",
            "affix_visible": full == 0 and tier > 0}


def read_spirit_artifact_affix_rules(cleanse_ids: list[int]) -> dict[str, Any]:
    """读取指定已加载词条；共享全量目录 decoder，但不为小请求枚举全部行。

    缺省字段由当前生成配置 closure 的默认表证明，不再将未知 full 猜为0。
    不宣称全目录覆盖或末尾代际核验；返回字段兼容原 rules/ratio/pid/start。
    """
    from .spirit_artifact_affix_catalog import read_affix_configuration
    return read_affix_configuration(cleanse_ids=cleanse_ids)


def read_spirit_artifact_affix_catalog(*, expected_cleanse_ids: list[int] | None = None) -> dict[str, Any]:
    """一次性读取客户端全部已加载词条，返回覆盖、内容指纹与新观察代际复核。

    expected_cleanse_ids 仅供比对导出范围，不能限定实际枚举范围；出现新增
    ID也必须读取。缺配置直接失败，既有底层有界映射恢复之外不加重试。
    不执行Lua、请求、写文件或消费。仅affix已验证，不能外推本体/A集合。
    根和成员稳定不等于逐字节原子快照；尚待实际全目录与热更新场景验收。
    """
    from .spirit_artifact_affix_catalog import read_affix_configuration
    return read_affix_configuration(cleanse_ids=None,
        expected_cleanse_ids=expected_cleanse_ids, verify_generation=True)


def enrich_spirit_artifact_effects(effects: list[dict[str, Any]], *, pid: int, process_start_ticks: int) -> None:
    """在原始属性投影前补齐词缀及官方名称，防止 ID 取模猜名导致属性错列。"""
    config = read_spirit_artifact_affix_rules([effect["cleanse_id"] for effect in effects])
    if (pid, process_start_ticks) != (config["pid"], config["process_start_ticks"]):
        raise FanxiuRuntimeMemoryError("属性和词缀配置来自不同游戏进程")
    for effect in effects:
        rule = config["rules"][effect["cleanse_id"]]
        effect.update(classify_spirit_artifact_affix(effect["value"], rule["max"], config["ratio_percent"], rule["full"]))
        effect.update(name=rule["name"], code=rule["code"] or "", type=rule["type"])


def read_spirit_artifact_affix_runtime() -> dict[str, Any]:
    """读取每个部位当前/待保存属性的词缀和阈值，不执行 Lua 或游戏操作。"""
    from .spirit_artifact import read_spirit_artifact_cleanse_runtime
    return read_spirit_artifact_cleanse_runtime()
