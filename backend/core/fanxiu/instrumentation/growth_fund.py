"""成长基金只读事实：完整配置、服务器领取记录、游戏已计算的奖励叶子。

不执行 Lua/协议，不以“新”角标推断可领，也不缓存 UI 地址。
Foundation_Reward_<id> 才是可领事实；Foundation_<id> 还混有新档提示。
"""
from .runtime_memory import (FanxiuRuntimeMemoryError, LuaRef, as_int,
                             manager_index_fields, resolve_lua_global_manager_root, table_ref)
from .ui_runtime_context import (active_ui_component_objects, read_ui_object_field,
                                 read_ui_selected_tab_panel, read_ui_runtime_snapshot)
from .resource_auto_use import read_runtime_config_rows
from .item_config import read_loaded_item_metadata, read_loaded_item_text
import re


def parse_fund_reward(value):
    """完整解析一次领取的全部附件；未知奖励编码拒绝当普通资源放行。"""
    parts = str(value).split(',')
    result = []
    for part in parts:
        match = re.fullmatch(r'(?i:item)\|(\d+)_(\d+)', part)
        if not match or min(map(int, match.groups())) <= 0:
            raise ValueError(f'成长基金奖励编码未支持：{value!r}')
        result.append({'item_id': int(match[1]), 'amount': int(match[2])})
    return result


def read_growth_fund_state():
    """在基金页读取全目录及当前选择；任何未计算叶子/缺失配置均报错。

    奖励门槛由游戏的 CheckFoundationRewardByFundId 计算，不在 Python
    重写 CL/建筑等条件。bulk 可领性由叶子证明，到账由两列 ID 集合证明。
    _CurTabList 是宿主已经过滤解锁条件的有序分类，_TabList 是会被红点
    回调覆盖的临时表，不能拿后者代表当前页签或完整基金目录。
    """
    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError('成长基金：' + message, code='runtime_incomplete')
        def ref(value):
            result = table_ref(value)
            if result is None:
                fail('对象未加载')
            return result
        def f(obj, key):
            return read_ui_object_field(c, ref(obj).address, key)
        def items(value):
            values, count = c.reader.list_items(ref(value))
            if count is None or count != len(values) or not 0 <= count <= 10000:
                fail('列表不完整')
            return values
        def ids(value):
            result = [as_int(v) for v in items(value)]
            if any(v is None or v <= 0 for v in result) or len(set(result)) != len(result):
                fail('ID 列表不完整')
            return sorted(result)

        panels = {}
        for obj in active_ui_component_objects(c):
            host = table_ref(f(obj, 'm_panel')) or obj
            selected = read_ui_selected_tab_panel(c, host.address)
            if selected:
                panel = LuaRef('table', selected[0])
                if as_int(f(panel, '_CurSelectFundId')) is not None:
                    if f(panel, 'panelShow') is not True:
                        continue
                    panels[panel.address] = (host, panel)
        if len(panels) != 1:
            fail('当前基金页不唯一')
        host, panel = next(iter(panels.values()))
        methods = frozenset({'Inst_get', 'LuaFundMgr'})
        root, _, env = resolve_lua_global_manager_root(c.memory, manager_key='growth-fund',
            state_address=c.binding.state_address, global_name='FundMgr', required_methods=methods,
            validate=lambda reader, address: manager_index_fields(reader, address, methods))
        data = f(f(manager_index_fields(c.reader, root, methods)['inst'], 'Model'), 'FundData')
        info = f(data, '_FoundationInfo')
        environment = LuaRef('table', env)
        db = f(f(environment, 'DBMgr'), 'inst')
        configs = c.reader.dictionary_fields(ref(f(db, 'ConfigDic')))
        def decode(rows, group, table, fields):
            return read_runtime_config_rows(c.reader, rows, environment_address=env,
                group_name=group, table_name=table, fields=fields)
        funds = decode(list(c.reader.fields(ref(configs.get('Fund.Fund'))).values()),
                       'Fund', 'Fund', ('fundId', 'type', 'sort', 'name'))
        rewards = decode(list(c.reader.fields(ref(configs.get('Fund.FundReward'))).values()),
            'Fund', 'FundReward', ('id', 'fundId', 'sort', 'level', 'rewardCondition', 'freeReward', 'paidReward'))
        categories = decode(items(f(host, '_CurTabList')), 'Open_function', 'OpenFunction', ('id', 'name'))
        names = read_loaded_item_text({int(r['name']) for r in funds + categories},
            reader=c.reader, state_address=c.binding.state_address, environment_address=env)
        if not names['complete']:
            fail('基金名称未完整加载')
        for row in funds + categories:
            row['name'] = names['texts_by_id'][int(row['name'])]
        for row in rewards:
            row['free'] = parse_fund_reward(row.pop('freeReward'))
            row['paid'] = parse_fund_reward(row.pop('paidReward'))
        metadata, evidence = read_loaded_item_metadata(
            {item['item_id'] for row in rewards for track in ('free', 'paid') for item in row[track]},
            memory=c.memory, reader=c.reader, state_address=c.binding.state_address)
        if not evidence['complete'] or any(not r.get('item_name') for r in metadata.values()):
            fail('奖励道具名称不完整')
        # 宝匣也可能包含祈愿材料。把权威描述交给业务策略，不靠菜单名猜测。
        descriptions = read_loaded_item_text({int(r['runtime_description_id']) for r in metadata.values()
            if r.get('runtime_description_id')}, reader=c.reader,
            state_address=c.binding.state_address, environment_address=env)
        if not descriptions['complete']:
            fail('奖励描述不完整')
        for row in metadata.values():
            row['description'] = descriptions['texts_by_id'].get(row.get('runtime_description_id'), '')
        nodes = c.reader.fields(ref(f(f(f(environment, 'RedDotMgr'), 'inst'), 'Model')))
        reds = c.reader.fields(ref(nodes.get('redDotNodeDic')))
        for row in funds:
            key = f"Foundation_Reward_{row['fundId']}"
            node = c.reader.fields(ref(reds.get(key)))
            count = as_int(node.get('finalShowCount'))
            if node.get('redDotId') != key or node.get('needCalculate') is not False or count is None or count < 0:
                fail(f'奖励叶子未稳定：{key}')
            row['claimable'] = count > 0
        fund_ids = {r['fundId'] for r in funds}
        selected_fund = as_int(f(panel, '_CurSelectFundId'))
        selected_type = as_int(f(panel, '_CurFuncType'))
        if (not funds or not rewards or len({r['id'] for r in rewards}) != len(rewards)
                or len(fund_ids) != len(funds)
                or any(r['fundId'] not in fund_ids for r in rewards)
                or len({r['id'] for r in categories}) != len(categories)
                or not any(r['fundId'] == selected_fund and r['type'] == selected_type for r in funds)):
            fail('奖励目录不完整')
        return {'complete': True, 'selected_fund': selected_fund,
            'selected_type': selected_type, 'categories': categories,
            'funds': funds, 'rewards': rewards, 'items': metadata,
            'bought': ids(f(info, 'buyFundIds')), 'claimed_free': ids(f(info, 'gainFreeBonusIds')),
            'claimed_paid': ids(f(info, 'gainPaidBonusIds'))}
    return read_ui_runtime_snapshot([], read)
