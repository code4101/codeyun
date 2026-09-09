"""Kernel 内共享的高级道具库存模型，无落盘、无运行游标。

首次目录观察加载库存；已确认回包后按固定规则扣一。跨 Cell 保留，
外部操作、进程切换或结果不明必须 invalidate。动作结果由新属性对象
版本及完整候选共同证明，不要求随机结果内容不同。库存跨 Cell 连续消耗
已在 7-5 验收；目标快照跨 Cell 复用尚待验收。未知结果调查回包观察层。
"""
from copy import deepcopy


class SpiritArtifactMemory:
    def __init__(self):
        self.invalidate()

    def invalidate(self):
        self.identity = None
        self.counts = {}
        self.catalogs = {}
        self.confirmed = {}
        self.current_snapshot = None
        self.selected_item = None

    def navigation_started(self):
        """切换页面只失效当前选择，不丢弃独立库存事实。"""
        self.current_snapshot = None
        self.selected_item = None

    @property
    def selected_ware(self):
        value = self.current_snapshot
        return value['ware_id'] if value and self.selected_item == value['item_id'] else None

    def remember_snapshot(self, snapshot):
        identity = (snapshot['pid'], snapshot['process_start_ticks'])
        if self.identity != identity:
            self.invalidate()
            self.identity = identity
        if snapshot.get('is_wash') is True:
            self.selected_item = snapshot['item_id']
        if self.selected_item == snapshot['item_id']:
            self.current_snapshot = deepcopy(snapshot)

    def snapshot(self, target):
        """仅返回同一独占操作期间已知的目标事实，不冒充新 Runtime 观察。"""
        value = self.current_snapshot
        if (value is None or self.identity != target.process_identity
                or value['item_id'] != target.item_id or self.selected_item != target.item_id
                or value['ware_id'] != target.ware_id or value['part'] != target.part
                or (target.base_id and value.get('base_id') != target.base_id)):
            return None
        return {**deepcopy(value), 'is_wash': True, 'ui_verified': False,
                'observation_source': 'kernel_model'}

    def remember_catalog(self, catalog):
        identity = (catalog['pid'], catalog['process_start_ticks'])
        if (self.identity != identity or any(
                row['item'] in self.counts and self.counts[row['item']] != row['count']
                for row in catalog['items'])):
            self.invalidate()
            self.identity = identity
        self.catalogs[catalog['item_id']] = deepcopy(catalog)
        for row in catalog['items']:
            self.counts.setdefault(row['item'], row['count'])

    def catalog(self, snapshot):
        if self.identity != (snapshot['pid'], snapshot['process_start_ticks']):
            self.invalidate()
            return None
        value = self.catalogs.get(snapshot['item_id'])
        if value is None:
            return None
        result = deepcopy(value)
        for row in result['items']:
            row['count'] = self.counts[row['item']]
        result['inventory_source'] = 'kernel_model'
        result['timings'] = {}
        result['inventory_diagnostics'] = {'source': 'kernel_model'}
        return result

    def confirm_use(self, material, before, after):
        """一次新对象版本的完整候选证明回包；相同数值仍是有效新结果。"""
        receipt = (material, before.get('pending_revision'), after.get('pending_revision'))
        if (self.identity == (after['pid'], after['process_start_ticks'])
                and self.confirmed.get(after['item_id']) == receipt):
            return self.counts[material]
        def values(rows):
            return {e['cleanse_id']: (e['value'], e['quality'], e['locked']) for e in rows}
        locked = {k: v for k, v in values(before['effects']).items() if v[2]}
        valid = (
            self.identity == (after['pid'], after['process_start_ticks'])
            and before['item_id'] == after['item_id']
            and before.get('pending_revision') is not None
            and after.get('pending_revision') is not None
            and before['pending_revision'] != after['pending_revision']
            and values(before['effects']) == values(after['effects'])
            and len(after['pending_effects']) == len(before['effects'])
            and all(values(after['pending_effects']).get(k) == v for k, v in locked.items())
            and self.counts.get(material, 0) > 0)
        if not valid:
            self.invalidate()
            raise RuntimeError('高级洗炼新回包未确认；库存模型失效，禁止重发')
        self.counts[material] -= 1
        self.confirmed[after['item_id']] = deepcopy(receipt)
        return self.counts[material]


if 'spirit_artifact_memory' in globals():
    # 研发重载只更新实现，不使仍有效的共享事实因代码分批执行而丢失。
    spirit_artifact_memory.__class__ = SpiritArtifactMemory
    if not hasattr(spirit_artifact_memory, 'current_snapshot'):
        spirit_artifact_memory.navigation_started()
else:
    spirit_artifact_memory = SpiritArtifactMemory()
