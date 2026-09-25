"""RoleInfoPanel 按钮身份、启用状态及固定槽位投影。

仅缓存公共 UI 根，按钮地址每次从当前选中面板重新解析。
不调用 Unity rect/active 方法；读取 Lua 保存的 ActiveState/isQuickActive，
结合客户端 prefab sibling 顺序，按 VerticalLayoutGroup 压紧可见槽位。
尚未真实验证未解锁账号的隐藏按钮布局；客户端更新后须同步资源镜像，
若新增组件没有可读启用状态则失败关闭，由接口层继续适配。
"""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .role_menu_layout import read_role_menu_layout
from .ui_runtime_context import (
    read_ui_runtime_snapshot, read_active_ui_window_panels,
    read_ui_selected_tab_panel, read_ui_object_field,
)

ROLE_BUTTONS = {
    '天赋': ('btnTalent', 849),
    '神焰': ('btnGodFlame', 852),
    '炼神': ('btnLianShen', 771),
    '三皇灵威': ('FeiShengBtn1', 856),
    '仙谕': ('themeBtn', None),
}


def read_role_menu_button(name):
    """Project enabled Runtime components into the prefab's fixed slot order."""
    field, scene = ROLE_BUTTONS[name]
    order = read_role_menu_layout()

    def read(c):
        panels = []
        for host in read_active_ui_window_panels(c, 'PlayerMainPanel'):
            selected = read_ui_selected_tab_panel(c, host.address)
            if selected and table_ref(read_ui_object_field(c, selected[0], 'btnTalent')):
                panels.append(selected[0])
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('角色属性面板未唯一加载', code='data_not_loaded')
        visible, parents = [], set()
        for child_name in order:
            ref = table_ref(read_ui_object_field(c, panels[0], child_name))
            if ref is None:
                raise FanxiuRuntimeMemoryError(f'prefab/Runtime 组件不一致：{child_name}', code='runtime_incomplete')
            parents.add(as_int(read_ui_object_field(c, ref.address, 'FatherId')))
            enabled = read_ui_object_field(c, ref.address, 'isQuickActive')
            if not isinstance(enabled, bool):
                enabled = read_ui_object_field(c, ref.address, 'ActiveState')
            if not isinstance(enabled, bool):
                raise FanxiuRuntimeMemoryError(f'{child_name} 启用状态未知', code='runtime_incomplete')
            if enabled:
                visible.append(child_name)
        if len(parents) != 1 or None in parents or field not in visible:
            raise FanxiuRuntimeMemoryError('角色目标未显示或父组件不一致', code='runtime_incomplete')
        button = table_ref(read_ui_object_field(c, panels[0], field))
        if button is None:
            raise FanxiuRuntimeMemoryError(f'角色按钮 {field} 未加载', code='data_not_loaded')
        identity = {}
        # FatherId + prefab component slot uniquely binds this wrapper. The
        # optional ComponentId Long proxy is not uniformly materialized.
        for key in ('FatherId', 'OrginComponentId'):
            value = read_ui_object_field(c, button.address, key)
            number = as_int(value)
            if number is None or number <= 0:
                raise FanxiuRuntimeMemoryError(f'角色按钮 {field} 身份不完整', code='runtime_incomplete')
            identity[key] = number
        return dict(name=name, key=field, expected_scene=scene, complete=True,
                    visible_order=tuple(visible), slot=visible.index(field),
                    identity=identity, pid=c.binding.pid,
                    process_start_ticks=c.binding.process_start_ticks)

    return read_ui_runtime_snapshot((), read)
