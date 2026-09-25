"""Runtime 可见功能顺序映射到固定槽位；入口定位不使用 OCR/图像。"""
from ..instrumentation.role_menu import read_role_menu_button

ROLE_SCENE = 770


def role_slot_point(first, second, slot):
    """Asset anchors define fixed slots, independent of function names."""
    if slot < 0 or int(slot) != slot:
        raise ValueError('角色菜单槽位必须为非负整数')
    return first[0]+slot*(second[0]-first[0]), first[1]+slot*(second[1]-first[1])


def enter_role_feature(context, name):
    """Runtime order -> fixed slot -> destination verification.

    Geometry lives in assets, order in the client prefab, enabled state in
    the mounted RoleInfoPanel. No OCR label or floating image is used.
    """
    yield from context.go_scene(ROLE_SCENE)
    scene = int((yield from context.wait_scene([ROLE_SCENE], wait=20)))
    if scene != ROLE_SCENE:
        raise RuntimeError(f'角色菜单未就绪：#{scene}')
    button = read_role_menu_button(name)
    if button['expected_scene'] is None:
        raise ValueError(f'{name} 尚未验证目标场景')
    def center(title):
        b = context.shape(ROLE_SCENE, title).box()
        return b['x']+b['w']/2, b['y']+b['h']/2
    point = role_slot_point(center('左侧第一槽'), center('左侧第二槽'), button['slot'])
    area = context.shape(ROLE_SCENE, '左侧菜单').box()
    if not (area['x'] <= point[0] <= area['x']+area['w'] and area['y'] <= point[1] <= area['y']+area['h']):
        raise RuntimeError('角色按钮超出已验证槽位区域')
    context.click_frame_point(ROLE_SCENE, *point)
    target = button['expected_scene']
    scene = int((yield from context.wait_scene([target], wait=20)))
    if scene != target:
        raise RuntimeError(f'{name} 点击后场景 #{scene}，预期 #{target}')
    return dict(name=name, point=point, slot=button['slot'], scene=scene)
