"""Independent PRG gallery v1: gallery/index.msgpack + gallery/items/<id>.msgpack.

No native metadata version bump. Standard PG ignores this namespace. Gallery
items contain canonical UUID references in their own subgraph scope. Assets live
once in the document attachments directory. Collaboration leases each stored
item separately and commits moves together with canvas objects and @order.
"""
from typing import Any

from fastapi import HTTPException

INDEX = '@gallery:index'
ITEM = '@gallery:item:'
INDEX_MEMBER = 'gallery/index.msgpack'


def member_key(member: str) -> str | None:
    if member == INDEX_MEMBER:
        return INDEX
    if member.startswith('gallery/items/') and member.endswith('.msgpack'):
        identity = member[len('gallery/items/'):-len('.msgpack')]
        if not identity or '/' in identity or '\\' in identity or len(identity) > 100:
            raise ValueError('无效图库子图路径')
        return ITEM + identity
    return None


def key_member(key: str) -> str | None:
    if key == INDEX:
        return INDEX_MEMBER
    if key.startswith(ITEM):
        identity = key[len(ITEM):]
        if not identity or '/' in identity or '\\' in identity or len(identity) > 100:
            raise ValueError('无效图库子图身份')
        return f'gallery/items/{identity}.msgpack'
    return None


def refs(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(refs(item) for item in value))
    if isinstance(value, dict):
        if set(value) == {'$graphRef'}:
            return {value['$graphRef']}
        return set().union(*(refs(item) for item in value.values()))
    return set()


def validate_gallery(objects: dict[str, dict]) -> None:
    if any(key.startswith('@gallery:') and key != INDEX and not key.startswith(ITEM) for key in objects):
        raise HTTPException(422, '不支持的图库对象类型')
    index = objects.get(INDEX)
    items = {key: value for key, value in objects.items() if key.startswith(ITEM)}
    if index is None:
        if items:
            raise HTTPException(422, '图库缺少目录')
        return
    try:
        if index.get('version') != 1:
            raise ValueError('不支持的图库版本，请使用匹配的 CodeYun 版本')
        groups = index['groups']
        if not isinstance(groups, list) or len(groups) > 1000:
            raise ValueError('无效图库分组')
        group_ids = set()
        for group in groups:
            identity, title = group['id'], group['title']
            if not isinstance(identity, str) or not identity or identity in group_ids or len(identity) > 100:
                raise ValueError('重复或无效分组身份')
            if not isinstance(title, str) or not title.strip() or len(title) > 120:
                raise ValueError('无效分组名称')
            group_ids.add(identity)
        identities = {key for key in objects if not key.startswith('@')}
        for key, item in items.items():
            key_member(key)
            if item['id'] != key[len(ITEM):] or item['groupId'] not in group_ids:
                raise ValueError('图库子图身份或分组无效')
            if not isinstance(item['title'], str) or not item['title'].strip() or len(item['title']) > 120:
                raise ValueError('无效子图名称')
            if not isinstance(item['createdAt'], (float, int)):
                raise ValueError('无效子图时间')
            values = item['objects']
            order = values['@order']['value']
            ids = set(values) - {'@order'}
            if not ids or not isinstance(order, list) or len(order) != len(set(order)) or set(order) != ids:
                raise ValueError('图库对象顺序不一致')
            if ids & identities:
                raise ValueError('同一对象不能同时存在于画布与图库或多个子图中')
            for identity in ids:
                value = values[identity]
                if identity.startswith('@') or not isinstance(value, dict) or value.get('uuid') != identity:
                    raise ValueError('无效图库对象身份')
                if refs(value) - ids:
                    raise ValueError('图库子图包含外部引用')
            identities.update(ids)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, f'无效图库：{exc}') from exc
