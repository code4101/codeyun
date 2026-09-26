"""Runtime 可见功能顺序映射到固定槽位；入口定位不使用 OCR/图像。"""
from ..instrumentation.role_menu import read_role_menu_button

ROLE_SCENE = 770


def role_slot_point(first, second, slot):
    """Asset anchors define fixed slots, independent of function names."""
    if slot < 0 or int(slot) != slot:
        raise ValueError('角色菜单槽位必须为非负整数')
    return first[0]+slot*(second[0]-first[0]), first[1]+slot*(second[1]-first[1])


def plan_role_feature_click(context, name, *, source_view=ROLE_SCENE,
                            menu_shape='左侧菜单', first_slot_shape='左侧第一槽',
                            second_slot_shape='左侧第二槽', expected_scene_ids=None):
    """Resolve one visible RoleInfoPanel feature from Runtime into asset slots.

    The caller owns source-scene recognition and landing verification. A
    navigator can additionally require its Shape's declared landing, while a
    business task may use the Runtime target directly.
    """
    button = read_role_menu_button(name)
    if button.get('complete') is not True or button.get('name') != name:
        raise RuntimeError('角色菜单 Runtime 按钮身份不完整')
    target = button.get('expected_scene')
    if target is None:
        raise ValueError(f'{name} 尚未验证目标场景')
    if expected_scene_ids is not None and target not in expected_scene_ids:
        raise RuntimeError('角色菜单 Runtime 目标与 Shape 跳转数据不一致')

    def center(title):
        b = context.shape(source_view, title).box()
        return b['x']+b['w']/2, b['y']+b['h']/2

    point = role_slot_point(
        center(first_slot_shape), center(second_slot_shape), button['slot'],
    )
    area = context.shape(source_view, menu_shape).box()
    if not (area['x'] <= point[0] <= area['x']+area['w'] and
            area['y'] <= point[1] <= area['y']+area['h']):
        raise RuntimeError('角色按钮超出已验证槽位区域')
    return dict(name=name, point=point, slot=button['slot'], scene=target)


def enter_role_feature(context, name):
    """Runtime order -> fixed slot -> destination verification.

    Geometry lives in assets, order in the client prefab, enabled state in
    the mounted RoleInfoPanel. No OCR label or floating image is used.
    """
    yield from context.go_scene(ROLE_SCENE)
    scene = int((yield from context.wait_scene([ROLE_SCENE], wait=20)))
    if scene != ROLE_SCENE:
        raise RuntimeError(f'角色菜单未就绪：#{scene}')
    plan = plan_role_feature_click(context, name)
    context.click_frame_point(ROLE_SCENE, *plan['point'])
    target = plan['scene']
    scene = int((yield from context.wait_scene([target], wait=20)))
    if scene != target:
        raise RuntimeError(f'{name} 点击后场景 #{scene}，预期 #{target}')
    return plan
