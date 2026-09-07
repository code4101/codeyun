"""洗炼窗口轻量身份投影；沿已验证成员链读取，不展开属性、配置或库存。"""

from __future__ import annotations

from typing import Any, Literal

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import read_ui_object_field, read_ui_runtime_snapshot


def read_spirit_artifact_ui_identity(
    *, window_kind: Literal['view', 'wash', 'advanced'] = 'wash', fast: bool = True,
    include_readiness: bool = False,
) -> dict[str, Any]:
    """读取灵器外层窗口、洗炼页或高级道具窗口的实时身份。

    view 仅返回外层 ware_id/tab_index，不读取子页 m_panel 或 item_id。
    用于已由 scene 证明到达装配页后的灵器身份校验：SpiritWareView
    在 OpenByParam 设置 v_wareId，子页通过独立加载回调接收它；
    确认进入哪件灵器不应依赖洗炼属性页是否加载。此新投影待真实验收。

    使用 spirit_artifact_ui / spirit_artifact_advanced 已验证的注册表和
    成员链，要求对应窗口唯一。高级窗口打开时底层洗炼页可能仍注册，
    因此调用方必须明确需要验证哪种窗口；本函数不推断最上层弹窗。

    每次调用由 read_ui_runtime_snapshot 取得新 context/reader。需要围绕
    item 快读校验身份时，前后分别调用本函数并比较 pid、start、ware、item；
    不把同一个 reader 的重读当二次验证。它不是原子快照，也不能排除 ABA。
    part/base_id 由 item 快读提供，不猜测未验证的 UI 字段。

    include_readiness 仅用于 wash，附带原生布尔字段 _IsCanUpgrade 和
    _CurItemBreak。SpiritWareWashPanel.UpdateCurSelect 先重置二者，随后
    根据客户端突破条件、当前 isBreak 更新；不从 A 类或满值数量推断。
    这些是当前面板控制状态，页面过渡时可能仍处于重置值。

    fast 只复用经过进程校验的根绑定，失效恢复由公共 UI Runtime 管理。
    此投影尚需真实窗口、切换目标及热路径耗时验收。
    """
    if window_kind not in ('view', 'wash', 'advanced'):
        raise ValueError('window_kind 必须是 view、wash 或 advanced')
    if include_readiness and window_kind != 'wash':
        raise ValueError('include_readiness 仅支持 wash 窗口')

    from ..catalog.spirit_artifact_wash_rules import spirit_artifact_ware_ids
    supported_ware_ids = spirit_artifact_ware_ids()

    def read(ctx):
        reader = ctx.reader
        field = lambda obj, key: read_ui_object_field(ctx, obj.address, key)
        storage = reader.table(ctx.binding.component_storage_address)
        candidates = {}
        # 与现有两个完整投影相同：仅访问 UIShowMgr 注册窗口的当前成员。
        for raw in [*storage['array'], *storage['fields'].values()]:
            window = table_ref(raw)
            if window is None:
                continue
            members, count = reader.list_items(window)
            if not count or len(members) != count:
                continue
            component = table_ref(members[-1])
            outer = table_ref(field(component, 'm_panel')) if component else None
            if outer is None:
                continue
            if window_kind == 'advanced':
                ware_id = as_int(field(outer, 'V_SpiritWare'))
                if (ware_id not in supported_ware_ids
                        or table_ref(field(outer, 'realList')) is None
                        or table_ref(field(outer, 'ScrollView')) is None):
                    continue
                panel = outer
            else:
                ware_id = as_int(field(outer, 'v_wareId'))
                group = table_ref(field(outer, 'tabPanelGroup'))
                if ware_id not in supported_ware_ids or group is None:
                    continue
                index = as_int(field(group, 'curTabIndex'))
                if window_kind == 'view':
                    if index is None or index < 0:
                        raise FanxiuRuntimeMemoryError('灵器当前页签身份不完整')
                    candidates[outer.address] = {'ware_id': ware_id, 'tab_index': index}
                    continue
                panels = table_ref(field(group, 'panelShowComps'))
                items, panel_count = reader.list_items(panels) if panels else ([], None)
                if (index is None or not panel_count or len(items) != panel_count
                        or not 0 <= index < len(items)):
                    raise FanxiuRuntimeMemoryError('灵器当前页签成员链不完整')
                comp = table_ref(items[index])
                panel = table_ref(field(comp, 'm_panel')) if comp else None
                if panel is None:
                    raise FanxiuRuntimeMemoryError('灵器当前页签面板未加载')
                if not all(table_ref(field(panel, key)) for key in (
                    'curAttrScroll', 'nextAttrScroll', 'SpiritWareScroll',
                )):
                    continue
            item_id = reader.long(field(panel, '_CurSelectSlot'))
            if not item_id:
                raise FanxiuRuntimeMemoryError('洗炼窗口没有明确选中实例')
            result = {'ware_id': ware_id, 'item_id': str(item_id)}
            if include_readiness:
                for key in ('_IsCanUpgrade', '_CurItemBreak'):
                    value = field(panel, key)
                    if type(value) is not bool:
                        raise FanxiuRuntimeMemoryError(f'洗炼突破控制状态 {key} 不完整')
                    result[key] = value
            candidates[outer.address] = result
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(
                f'当前 {window_kind} 洗炼窗口数量必须为 1，实际 {len(candidates)}',
            )
        return {
            **next(iter(candidates.values())), 'window_kind': window_kind,
            'pid': ctx.memory.pid, 'process_start_ticks': ctx.memory.process_start_ticks,
            'source': 'active_spiritware_window_identity',
        }

    return read_ui_runtime_snapshot([], read, fast=fast)
