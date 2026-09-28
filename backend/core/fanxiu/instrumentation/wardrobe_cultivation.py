"""衣装阁已加载 UI 的只读观察；不调用 Lua 或发送游戏协议。

每次重新取得活动窗口和当前列表，避免换页后复用池化条目。主馆目录的
can_* 是客户端刷新后的提示事实；消费材料前仍以详情的原生门槛为准。
"""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
)
from .resource_auto_use import read_runtime_config_rows


def read_wardrobe_cultivation(*, include_items=True):
    """返回主馆分类、所选衣装和详情门槛；include_items 控制完整目录投影。"""
    def read(c):
        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                raise FanxiuRuntimeMemoryError('衣装阁对象未加载')
            return read_ui_object_field(c, ref.address, key)

        def vo(obj):
            ident, level = as_int(field(obj, 'id')), as_int(field(obj, 'level'))
            if ident is None or level is None:
                raise FanxiuRuntimeMemoryError('衣装身份或等级缺失')
            return dict(id=ident, level=level, owned=field(obj, 'isGet') is True,
                        can_activate=field(obj, 'isCanGet') is True,
                        can_upgrade=field(obj, 'isCanLevelUp') is True,
                        maximum=field(obj, 'isMaxLevel') is True)

        mains = read_active_ui_window_panels(c, 'FashionMainView')
        if len(mains) != 1:
            raise FanxiuRuntimeMemoryError('衣装阁主馆未唯一加载')
        main = mains[0]
        manager = read_ui_object_field(c, c.binding.environment_address, 'FashionMgr')
        model = field(field(manager, 'inst'), 'Model')
        new_suits, suit_count = c.reader.list_items(field(model, 'newFashionSuitRedList'))
        new_fashions, fashion_count = c.reader.list_items(field(model, 'newFashionRedList'))
        if suit_count != len(new_suits) or fashion_count != len(new_fashions):
            raise FanxiuRuntimeMemoryError('衣装新提示目录不完整')
        new_ids = [as_int(v) for v in new_fashions]
        suit_notices = [c.reader.fields(v) for v in new_suits]
        values, count = c.reader.list_items(field(main, 'FashionDataList')) if include_items else ([], 0)
        if count is None or count != len(values):
            raise FanxiuRuntimeMemoryError('衣装阁展示目录不完整')
        selected = field(main, 'curSelectedItem')
        vector = c.reader.fields(field(main, 'V_CurTabAndItem'))
        category = as_int(vector.get('x'))
        if category == 7 and values:
            rows = read_runtime_config_rows(c.reader, values,
                environment_address=c.binding.environment_address, group_name='Fashion',
                table_name='FashionSuit', fields=('id', 'suitId', 'level', 'name'))
        else:
            rows = [vo(v) for v in values]
            for row in rows:
                row['new'] = row['id'] in new_ids
        result = dict(bottom=as_int(field(main, 'selectedBottomType')),
                      category=category, items=rows, new_suits=suit_notices, new_ids=new_ids,
                      selected=vo(selected) if table_ref(selected) and category != 7 else None, detail=None)
        tips = read_active_ui_window_panels(c, 'TipsView')
        if tips:
            if len(tips) != 1:
                raise FanxiuRuntimeMemoryError('衣装详情不唯一')
            tip = tips[0]
            current = vo(field(tip, 'fashionInfoVo'))
            if current['id'] != as_int(field(tip, 'fashionId')):
                raise FanxiuRuntimeMemoryError('衣装详情身份不一致')
            current.update(action_type=as_int(field(tip, 'extraUseType')),
                           actionable=field(tip, 'isCanLevelUp') is True,
                           batch=field(tip, 'isCanOneKeyLevelUp') is True,
                           batch_count=as_int(field(tip, 'oneKeyTimes')))
            result['detail'] = current
        return result
    return read_ui_runtime_snapshot(['FashionMgr'], read)


def read_wardrobe_resonance():
    """只读仙语阁当前共鸣档位、原生 canUp 和下一档条件。"""
    def read(c):
        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                raise FanxiuRuntimeMemoryError('仙语阁对象未加载')
            return read_ui_object_field(c, ref.address, key)

        panels = read_active_ui_window_panels(c, 'FashionStarCollectView')
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('仙语阁窗口未唯一加载')
        panel = panels[0]
        manager = read_ui_object_field(c, c.binding.environment_address, 'FashionMgr')
        data = field(field(field(manager, 'inst'), 'Model'), 'FashionData')
        stage = as_int(field(data, '_StarJie'))
        current, selected = field(panel, 'curCount'), field(panel, 'selectCountCfg')
        configs = read_runtime_config_rows(c.reader, [current, selected],
            environment_address=c.binding.environment_address, group_name='Fashion',
            table_name='FashionStarMasterLevel', fields=('id', 'jie', 'condition'))
        can_up = field(panel, 'canUp')
        if stage is None or not isinstance(can_up, bool):
            raise FanxiuRuntimeMemoryError('仙语阁阶数/门槛缺失')
        return dict(stage=stage, current=configs[0], selected=configs[1],
                    actionable=can_up, already_active=field(panel, 'hasActived') is True)
    return read_ui_runtime_snapshot(['FashionMgr'], read)
