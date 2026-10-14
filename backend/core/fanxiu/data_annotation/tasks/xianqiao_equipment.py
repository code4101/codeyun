"""Equip owned higher-quality 玉京仙纹; preserve old equipment and experience."""
from datetime import datetime

from backend.core.fanxiu.instrumentation.xianqiao import (
    read_xianqiao_snapshot, read_xianqiao_equipment_inventory_snapshot,
    read_xianqiao_equipment_config_snapshot,
)
from backend.core.fanxiu.instrumentation.xianqiao_equipment import read_xianqiao_equipment_panel_snapshot
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, record_world_discovery

STAGE_ID = 'xianqiao-equipment-update'
STAGE_VERSION = '1'
_PARTS = {1:'天璇', 2:'天玑', 3:'天权', 4:'玉衡', 5:'开阳', 6:'瑶光'}


def _owned_part(inventory, config, part):
    if inventory['evidence'] != config['evidence']:
        raise RuntimeError('仙纹库存与配置来自不同原生进程')
    rows = []
    for item in inventory['items']:
        cfg = config['by_base_id'].get(item['base_id'])
        if cfg is None:
            raise RuntimeError('已拥有仙纹缺少当前原生配置，拒绝猜测品质或部位')
        if cfg['system_type'] == 2 and cfg['part_id'] == part:
            rows.append({**item, **cfg})
    return rows


def accept_xianqiao_replacement_transaction(*, key):
    """Verify an existing wear intent without clicking 更换 again."""
    intent = read_world_facts().get('discoveries', {}).get(key)
    if not isinstance(intent, dict):
        raise RuntimeError('仙纹换装事务不存在')
    if intent.get('outcome') == 'verified':
        return intent
    bag = read_xianqiao_equipment_inventory_snapshot()
    index = {v['equip_id']:v for v in bag['items']}
    old, new = intent['old_before'], intent['new_before']
    a, b = index.get(old['equip_id']), index.get(new['equip_id'])
    immutable = ('equip_id', 'base_id', 'level', 'quantity', 'sum_exp')
    if (a is None or b is None or a['worn'] is not False or b['worn'] is not True
            or any(a[k] != old[k] or b[k] != new[k] for k in immutable)):
        raise RuntimeError('仙纹换装或旧经验保留尚未验证，保留意图禁止重复点击')
    state = read_xianqiao_snapshot()
    installed = [v for v in state['equipped'] if v['part_id'] == intent['part_id']]
    if (state.get('complete') is not True or state['active_system_type'] != 2
            or len(installed) != 1 or installed[0]['equip_id'] != new['equip_id']):
        raise RuntimeError('仙窍当前部位未绑定目标仙纹实例')
    result = {**intent, 'outcome':'verified', 'old_after':a, 'new_after':b,
              'installed':installed[0], 'verified_at':datetime.now().astimezone().isoformat()}
    record_world_discovery(key, result)
    return result


def replace_xianqiao_equipment_part(context, *, part_id):
    """One fresh whole-part replacement, with a durable pre-tap intent.

    Strictly higher quality is the owner's version-upgrade criterion. Same
    quality alternatives are retained. Only currently worn 玉京 parts enter
    this path; no unopened hole, old-system gear or equipment feeding occurs.
    """
    if type(part_id) is not int or part_id not in _PARTS:
        raise ValueError('未知仙纹部位')
    state = read_xianqiao_snapshot()
    if state.get('complete') is not True or state['active_system_type'] != 2:
        raise RuntimeError('自动仙纹换装当前仅支持已实机核实的玉京体系')
    config = read_xianqiao_equipment_config_snapshot()
    bag = read_xianqiao_equipment_inventory_snapshot()
    owned = _owned_part(bag, config, part_id)
    worn = [v for v in owned if v['worn']]
    if len(worn) != 1:
        raise RuntimeError('指定仙纹部位未唯一装配')
    old = worn[0]
    best_quality = max(v['quality'] for v in owned)
    if best_quality <= old['quality']:
        return {'outcome':'already_highest_quality', 'part_id':part_id, 'worn':old}
    frame = context.cur_frame(update=True)
    reuse = context.match_view(488, frame_data_url=frame)[0]
    if reuse:
        opened = read_xianqiao_equipment_panel_snapshot()
        reuse = opened['system_type'] == 2 and opened['part_id'] == part_id
    if not reuse:
        yield from context.go_scene(484)
        yield from context.wait_click_then_scene(484,'玉京',[485],timeout=30)
        yield from context.wait_click_then_scene(485,_PARTS[part_id],[486],timeout=30)
        yield from context.wait_click_then_scene(486,'更换',[488],timeout=30)
    panel = read_xianqiao_equipment_panel_snapshot()
    if panel['system_type'] != 2 or panel['part_id'] != part_id:
        raise RuntimeError('仙纹更换页部位与业务意图不一致')
    if any(panel[k] != bag['evidence'][k] for k in ('pid', 'process_start_ticks')):
        raise RuntimeError('仙纹更换页与库存来自不同原生进程')
    if any(config['by_base_id'][v['base_id']]['system_type'] != 2
           or config['by_base_id'][v['base_id']]['part_id'] != part_id for v in panel['candidates']):
        raise RuntimeError('仙纹候选包含其他体系或部位')
    candidates = [v for v in panel['candidates']
                  if config['by_base_id'][v['base_id']]['quality'] == best_quality]
    if not candidates:
        raise RuntimeError('更高品质仙纹未出现在真实候选列表')
    # Native order already sorts quality/level/instance ID. Choose its first
    # highest-quality row, then guard the actual selected instance.
    chosen = candidates[0]
    if chosen['index'] >= 4:
        raise RuntimeError('目标仙纹尚无已验收的可见候选定位')
    if (not panel['selection_verified'] or panel['selected']['equip_id'] != chosen['equip_id']):
        context.click_shape_center(488, f"候选{chosen['index']+1}")
        yield from context.wait_action_settle(.5)
        panel = read_xianqiao_equipment_panel_snapshot()
    if (not panel['selection_verified'] or panel['selected']['equip_id'] != chosen['equip_id']
            or panel['selected']['worn'] is not False):
        raise RuntimeError('仙纹选择尚未与当前原生实例一致')
    fresh = read_xianqiao_equipment_inventory_snapshot()
    current = _owned_part(fresh, config, part_id)
    old_now = next(v for v in current if v['equip_id'] == old['equip_id'])
    new = next(v for v in current if v['equip_id'] == chosen['equip_id'])
    if old_now != old or new['worn'] or new['quality'] <= old['quality']:
        raise RuntimeError('提交前仙纹装配或候选事实发生变化')
    key = f"xianqiao-equip:2:{part_id}:{old['equip_id']}:{new['equip_id']}"
    previous = read_world_facts().get('discoveries', {}).get(key)
    if previous:
        return accept_xianqiao_replacement_transaction(key=key)
    record_world_discovery(key, {'outcome':'commit_pending','part_id':part_id,
        'old_before':old,'new_before':new,'created_at':datetime.now().astimezone().isoformat()})
    context.click_shape_center(488,'更换')
    yield from context.wait_action_settle(1)
    result = accept_xianqiao_replacement_transaction(key=key)
    yield from context.go_scene(34)
    return result


def replace_higher_quality_xianqiao(context):
    """Daily component; rebuild plans from current worn/owned facts each call."""
    transactions = []
    for key, value in read_world_facts().get('discoveries', {}).items():
        if key.startswith('xianqiao-equip:2:') and value.get('outcome') == 'commit_pending':
            transactions.append(accept_xianqiao_replacement_transaction(key=key))
    for part in sorted(v['part_id'] for v in read_xianqiao_snapshot()['equipped']):
        result = yield from replace_xianqiao_equipment_part(context, part_id=part)
        transactions.append(result)
    yield from context.go_scene(34)
    return {'result':'success','outcome':'higher_quality_equipment_verified',
            'transactions':transactions,'scene':34}
