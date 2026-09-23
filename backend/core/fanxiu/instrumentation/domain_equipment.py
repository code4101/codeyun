"""镇物总览：每次返回 #814 重读游戏已经计算的红点叶子结果。

不复刻材料/品质条件。isQuickActive、父组 finalShowCount 和旧显示实例
都不代表当前可操作状态；使用 RedDotMgr.Model 的各业务叶子结果。
槽位映射已通过 V_Cfg 中名称/原生坐标及真实点击核对。
"""
from datetime import datetime, timezone
from .runtime_memory import FanxiuRuntimeMemoryError, table_ref
from .ui_runtime_context import read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot

# Top to bottom, then left to right; existing fixed-coordinate Shapes.
DOMAIN_EQUIPMENT_SLOTS = (
    (11, '核心', '鼎'), (7, '内圈左上', '旗'), (8, '内圈右上', '图'),
    (9, '内圈左下', '钟'), (10, '内圈右下', '珠'),
    (1, '外圈左上', '符'), (6, '外圈右上', '仪'),
    (2, '外圈左中', '鉴'), (5, '外圈右中', '筹'),
    (3, '外圈左下', '铃'), (4, '外圈右下', '尺'),
)
_REASONS = {'Empty': '空位', 'LevelUp': '装配', 'Shengji': '祭炼',
            'Xilian': '重铸', 'Shenzhu': '铸魂'}


def read_domain_equipment_activation() -> dict:
    """Read a fresh complete overview; missing or still-calculating data raises."""
    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError(message, code='runtime_incomplete')
        def ref(value):
            result = table_ref(value)
            if result is None:
                fail('镇物红点数据未加载')
            return result
        def field(obj, key):
            return read_ui_object_field(c, ref(obj).address, key)
        panels = read_active_ui_window_panels(c, 'DomainEquipPanel')
        if len(panels) != 1:
            fail('镇物总览未唯一加载')
        mgr = read_ui_object_field(c, c.binding.environment_address, 'RedDotMgr')
        nodes = c.reader.fields(ref(field(field(field(mgr, 'inst'), 'Model'), 'redDotNodeDic')))
        slots = []
        for index, shape, name in DOMAIN_EQUIPMENT_SLOTS:
            active_reasons = []
            for suffix, label in _REASONS.items():
                key = f'DomainEquip_{index}_{suffix}'
                node = c.reader.fields(ref(nodes.get(key)))
                count = node.get('finalShowCount')
                if node.get('redDotId') != key or node.get('needCalculate') is not False:
                    fail(f'镇物红点尚未稳定：{key}')
                if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0:
                    fail(f'镇物红点结果无效：{key}')
                if count > 0:
                    active_reasons.append(label)
            slots.append({'native_index': index, 'shape': shape, 'name': name,
                          'active': bool(active_reasons), 'reasons': active_reasons})
        return {'complete': True, 'observed_at': datetime.now(timezone.utc).isoformat(),
                'source': 'RedDotMgr.inst.Model.redDotNodeDic.finalShowCount', 'slots': slots}
    return read_ui_runtime_snapshot(
        ['Window', 'DomainEquipPanel', 'uId', 'V_uId', 'RedDotMgr', 'inst', 'Model', 'redDotNodeDic'], read)
