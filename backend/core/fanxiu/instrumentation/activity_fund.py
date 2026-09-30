"""活动基金实时状态：UI 完整页签、服务器已领 ID 和游戏可领叶子。

0=未开放，1=已开放但未全领，2=所有适用奖励已领取。未购买的付费列
不属于应领集合；无红点只代表当前不可领，不能替代状态 2。无持久化缓存。
"""
from .runtime_memory import (FanxiuRuntimeMemoryError, LuaRef, as_int,
                             manager_index_fields, resolve_lua_global_manager_root, table_ref)
from .ui_runtime_context import active_ui_component_objects, read_ui_object_field, read_ui_runtime_snapshot
from .resource_auto_use import read_runtime_config_rows


def read_activity_fund_state():
    """Read the currently open ActivityFundView; never initialize or operate it."""
    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError('活动基金：' + message, code='runtime_incomplete')
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
            values = [as_int(v) for v in items(value)]
            if any(v is None or v <= 0 for v in values) or len(set(values)) != len(values):
                fail('领取 ID 列表异常')
            return set(values)
        panels = [o for o in active_ui_component_objects(c)
                  if as_int(f(o, '_CurActivityId')) and as_int(f(o, '_CurSelectFundId'))
                  and table_ref(f(o, '_TabItemList'))]
        if len(panels) != 1:
            fail('当前活动基金页不唯一')
        panel = panels[0]
        methods = frozenset({'Inst_get', 'LuaActivityfundMgr'})
        root, _, env = resolve_lua_global_manager_root(
            c.memory, manager_key='activity-fund', state_address=c.binding.state_address,
            global_name='ActivityfundMgr', required_methods=methods,
            validate=lambda reader, address: manager_index_fields(reader, address, methods),
        )
        data = f(f(manager_index_fields(c.reader, root, methods)['inst'], 'Model'), 'ActivityfundData')
        info = f(data, '_FoundationInfo')
        bought, free, paid = (ids(f(info, key)) for key in ('buyFundIds', 'gainFreeBonusIds', 'gainPaidBonusIds'))
        environment = LuaRef('table', env)
        configs = c.reader.dictionary_fields(ref(f(f(f(environment, 'DBMgr'), 'inst'), 'ConfigDic')))
        rewards = read_runtime_config_rows(
            c.reader, list(c.reader.fields(ref(configs.get('ActivityFund.ActivityFundReward'))).values()),
            environment_address=env, group_name='ActivityFund', table_name='ActivityFundReward',
            fields=('id', 'fundId', 'sort', 'condition', 'freeReward', 'paidReward'),
        )
        reds = c.reader.fields(ref(f(f(f(environment, 'RedDotMgr'), 'inst'), 'Model')))
        nodes = c.reader.fields(ref(reds.get('redDotNodeDic')))
        tabs = []
        for index, item in enumerate(items(f(panel, '_TabItemList'))):
            button = f(item, 'tabButton')
            key = f(button, 'redDotId')
            if not isinstance(key, str) or not key.startswith('ActivityFoundation_'):
                fail('页签身份不完整')
            fund_id = int(key.removeprefix('ActivityFoundation_'))
            opened = f(button, 'isCustomFunctionOpen')
            if not isinstance(opened, bool):
                fail('开放状态未知')
            rows = [r for r in rewards if r['fundId'] == fund_id]
            if not rows or len({r['id'] for r in rows}) != len(rows):
                fail('奖励目录缺失或重复')
            required_free = {r['id'] for r in rows if r.get('freeReward')}
            required_paid = {r['id'] for r in rows if r.get('paidReward')} if fund_id in bought else set()
            missing_free, missing_paid = required_free - free, required_paid - paid
            reward_key = f'ActivityFoundation_Reward_{fund_id}'
            node = c.reader.fields(ref(nodes.get(reward_key)))
            count = as_int(node.get('finalShowCount'))
            if node.get('needCalculate') is not False or count is None or count < 0:
                fail(f'奖励叶子未稳定：{reward_key}')
            tabs.append(dict(index=index, fund_id=fund_id, name=f(button, 'name'),
                             opened=opened, state=0 if not opened else 1 if missing_free or missing_paid else 2,
                             claimable=count > 0, bought=fund_id in bought,
                             reward_count=len(rows), missing_free=sorted(missing_free), missing_paid=sorted(missing_paid)))
        selected = as_int(f(panel, '_CurSelectFundId'))
        if not tabs or len({t['fund_id'] for t in tabs}) != len(tabs) or selected not in {t['fund_id'] for t in tabs}:
            fail('页签目录不完整')
        return dict(complete=True, activity_id=as_int(f(panel, '_CurActivityId')), selected_fund=selected,
                    tabs=tabs, claimed_free=sorted(free), claimed_paid=sorted(paid))
    return read_ui_runtime_snapshot([], read)
