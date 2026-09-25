"""Prefab sibling order supplements Runtime enabled state; no GUI recognition.

Lua wrappers do not expose sibling order. Use the local client resource mirror;
missing/ambiguous versions fail closed. Refresh the mirror after client updates.
"""
from functools import lru_cache
from pyxllib.file.game_assets import load_unity_environment
from ..catalog.resources import resolve_fanxiu_resource_root


@lru_cache(maxsize=2)
def _load(path, size, modified):
    env = load_unity_environment(path)
    objects = {o.path_id: o for o in env.objects}
    roots = []
    for obj in env.objects:
        if obj.type.name == 'RectTransform':
            rect = obj.read_typetree()
            if objects[rect['m_GameObject']['m_PathID']].read_typetree()['m_Name'] == 'buttonComp':
                roots.append(rect)
    if len(roots) != 1:
        raise RuntimeError('RoleInfoPanel.buttonComp 不唯一')
    names = []
    for child in roots[0]['m_Children']:
        rect = objects[child['m_PathID']].read_typetree()
        names.append(objects[rect['m_GameObject']['m_PathID']].read_typetree()['m_Name'])
    if not names or len(set(names)) != len(names):
        raise RuntimeError('角色菜单 prefab 顺序不完整')
    return tuple(names)


def read_role_menu_layout():
    paths = list((resolve_fanxiu_resource_root()/'ui'/'role').glob('roleinfopanel_*.bytes'))
    if len(paths) != 1:
        raise RuntimeError('角色菜单资源版本不唯一；先同步当前客户端资源')
    stat = paths[0].stat()
    return _load(str(paths[0]), stat.st_size, stat.st_mtime_ns)
