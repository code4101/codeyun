"""读取已打开道具详情中客户端按当前条件过滤的完整奖励预览。"""
from __future__ import annotations

import hashlib
import json
import time

from .resource_auto_use import read_runtime_config_rows
from .activity_menu import active_ui_component_objects
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    read_ui_object_field, read_ui_runtime_snapshot,
)


def read_item_detail_identity(*, expected_item_id: int) -> dict:
    """Bind a naturally opened item tooltip, including synthetic reward items.

    Reward tooltips need not have a backpack instance ID. Their current
    itemlo/DetailScroll/itemvo membership and baseId must still agree. This
    observes identity only; it neither proves a reward graph nor authorizes use.
    """
    if type(expected_item_id) is not int or expected_item_id <= 0:
        raise ValueError('详情身份需要正整数道具ID')

    def read(ctx):
        matches = []
        for panel in active_ui_component_objects(ctx, include_descendants=False):
            itemlo = table_ref(read_ui_object_field(ctx, panel.address, 'itemlo'))
            detail = table_ref(read_ui_object_field(ctx, panel.address, 'DetailScroll'))
            if itemlo is None or detail is None:
                continue
            config = read_runtime_config_rows(ctx.reader, [itemlo],
                environment_address=ctx.binding.environment_address,
                group_name='Item', table_name='Item', fields=('id', 'effectValue'))[0]
            if as_int(config['id']) != expected_item_id:
                continue
            itemvo = table_ref(read_ui_object_field(ctx, panel.address, 'itemvo'))
            base_id = as_int(read_ui_object_field(ctx, itemvo.address, 'baseId')) if itemvo else None
            quantity = as_int(read_ui_object_field(ctx, itemvo.address, 'num')) if itemvo else None
            if base_id != expected_item_id or quantity is None or quantity < 0:
                raise FanxiuRuntimeMemoryError('当前详情物品身份或数量未完整绑定', code='runtime_incomplete')
            instance = read_ui_object_field(ctx, itemvo.address, 'id')
            matches.append(dict(item_id=base_id, quantity=quantity,
                instance_id=str(ctx.reader.long(instance)) if instance is not None else None,
                effect_value=config['effectValue']))
        if len(matches) != 1:
            raise FanxiuRuntimeMemoryError('指定道具的当前详情不唯一或未打开', code='runtime_incomplete')
        return dict(matches[0], complete=True, read_only=True,
            source='active_item_detail_identity', consumption_authorized=False,
            observed_at=time.time(), evidence=dict(pid=ctx.binding.pid,
                process_start_ticks=ctx.binding.process_start_ticks))

    return read_ui_runtime_snapshot(('itemlo', 'DetailScroll', 'itemvo', 'baseId', 'num'), read, fast=True)


def read_item_reward_preview(*, expected_item_id: int) -> dict:
    """只读当前 TipsNewView 的条件过滤结果，不加载配置或执行游戏 Lua。

    TipsChooseItemCell.V_Rewards 来自 BagitemMgr.GetOptionalGiftList，已由
    ShowConditionFormat 按 GameUtil.CheckCondition 过滤。读取完整 CList，
    同时核对所属物品、配置组和 giftPacksSc.ItemInfoList，避免把历史缓存、
    可见几行或静态目录的跨条件并集当作当前预览。这里证明客户端展示分支，
    不宣称服务器概率或收益；实际开箱样本仍需独立完整取证。
    已在19070082详情实机核验完整两项当前奖励及连续稳定读取。
    缺失、多义或列表不一致时失败关闭。
    """
    if type(expected_item_id) is not int or expected_item_id <= 0:
        raise ValueError('奖励预览需要实际道具正整数 ID')

    def read(ctx):
        def fail(message):
            raise FanxiuRuntimeMemoryError('道具奖励预览：' + message,
                                         code='item_reward_preview_incomplete')

        def field(obj, name):
            ref = table_ref(obj)
            if ref is None:
                fail(f'{name} 父对象未加载')
            return read_ui_object_field(ctx, ref.address, name)

        def config(rows, table, names):
            return read_runtime_config_rows(ctx.reader, rows,
                environment_address=ctx.binding.environment_address,
                group_name='Item', table_name=table, fields=names)

        def complete_list(value, label):
            if table_ref(value) is None:
                fail(label + '未加载')
            rows, count = ctx.reader.list_items(value)
            if count != len(rows) or not 1 <= len(rows) <= 128:
                fail(label + '缺失、为空或超过边界')
            return rows

        panels = {}
        # TipsNewView 属于当前 registry 的直接面板；其奖励列表在下方按
        # 已验证详情关系读取，无需遍历储物袋全部物品子组件。
        for component in active_ui_component_objects(ctx, include_descendants=False):
            itemlo = table_ref(field(component, 'itemlo'))
            detail = table_ref(field(component, 'DetailScroll'))
            if itemlo is None or detail is None:
                continue
            card = config([itemlo], 'Item', ('id', 'effectValue'))[0]
            if as_int(card['id']) == expected_item_id:
                panels[component.address] = (component, itemlo, detail, card)
        if len(panels) != 1:
            fail('当前目标道具详情不唯一或未打开')
        panel, itemlo, detail, card = next(iter(panels.values()))
        itemvo = field(panel, 'itemvo')
        if as_int(field(itemvo, 'baseId')) != expected_item_id:
            fail('详情库存物品与配置身份不一致')
        instance = field(itemvo, 'id')
        instance_id = as_int(instance)
        if instance_id is None:
            instance_id = ctx.reader.long(instance)
        if instance_id is None or instance_id <= 0:
            fail('详情库存实例身份缺失')
        effect = card['effectValue']
        if not isinstance(effect, str) or len(effect.split('_')) != 2:
            fail('道具奖励组规则不是已支持的两段配置')
        try:
            group_id = int(effect.split('_')[1])
        except ValueError:
            fail('道具奖励组规则无效')
        if group_id <= 0:
            fail('道具奖励组规则非正整数')

        contents = {}
        instances = ctx.reader.dictionary_fields(field(detail, 'ItemClassDic'))
        if not 1 <= len(instances) <= 64:
            fail('详情渲染组件缺失或超过边界')
        for cell in instances.values():
            if table_ref(cell) is None:
                fail('详情渲染组件不是对象')
            data = field(cell, '_Data')
            if field(data, 'luaClass') != 'GameSystem.Game.Tips.View.TipsViewContent.TipsChooseItemCell':
                continue
            if field(data, 'prefabPath') != 'UI/Tips/Content/BoxChooseComp':
                fail('奖励预览组件配置不符')
            task = ctx.reader.dictionary_fields(field(cell, '_InstTaskDic')).get('UI/Tips/Content/BoxChooseComp')
            if table_ref(task) is None or field(task, 'isLoading') is not False:
                fail('奖励预览组件未完成加载')
            content = table_ref(field(task, 'content'))
            if content is None or table_ref(field(content, 'V_Itemlo')) != itemlo:
                fail('奖励预览组件物品身份不符')
            contents[content.address] = content
        if len(contents) != 1:
            fail('当前奖励预览组件不唯一')
        content = next(iter(contents.values()))
        rewards = complete_list(field(content, 'V_Rewards'), '条件过滤奖励列表')
        rendered = complete_list(field(field(content, 'giftPacksSc'), 'ItemInfoList'), '奖励展示完整列表')
        if rewards != rendered:
            fail('条件过滤列表与实际展示列表不一致')
        configs = config([field(row, 'param') for row in rewards], 'OptionalGift',
                         ('id', 'groupID', 'giftID', 'number', 'showCondition'))
        result = []
        for index, (row, cfg) in enumerate(zip(rewards, configs, strict=True)):
            base_id, quantity = as_int(field(row, 'baseId')), as_int(field(row, 'num'))
            if (base_id is None or base_id <= 0 or quantity is None or quantity <= 0
                    or as_int(cfg['giftID']) != base_id or as_int(cfg['number']) != quantity
                    or (as_int(cfg['groupID']) != group_id and cfg['groupID'] != str(group_id))
                    or not isinstance(cfg['showCondition'], str)):
                fail('奖励配置与当前展示数量、身份或组不符')
            config_id = as_int(cfg['id'])
            if config_id is None or config_id <= 0:
                fail('奖励配置身份缺失')
            result.append(dict(ui_index=index + 1, config_id=config_id,
                item_id=base_id, quantity=quantity, show_condition=cfg['showCondition']))
        if len({row['config_id'] for row in result}) != len(result):
            fail('奖励配置身份重复')
        fingerprint = hashlib.sha256(json.dumps(dict(item_id=expected_item_id,
            effect_value=effect, rewards=result), ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return dict(complete=True, read_only=True, source='active_item_condition_filtered_reward_preview',
            item_id=expected_item_id, instance_id=str(instance_id), effect_value=effect,
            reward_group_id=group_id, rewards=result, preview_fingerprint=fingerprint,
            observed_at=time.time(), evidence=dict(pid=ctx.binding.pid,
                process_start_ticks=ctx.binding.process_start_ticks))

    return read_ui_runtime_snapshot([], read, fast=True)
