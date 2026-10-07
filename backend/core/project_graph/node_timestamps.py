"""Portable node times in Unix milliseconds, with durable legacy completion.

Completion is an estimate, not recovered history: existing creation, node edit,
file creation, then current time. Merely opening never advances an existing edit.
"""
import time
from fastapi import HTTPException


def complete_node_timestamps(objects: dict[str, dict], file_created_at: int | None = None,
                             now: int | None = None) -> dict[str, dict]:
    """Return a copy with missing stage/gallery dates filled; stable on repetition."""
    now = now or int(time.time() * 1000)
    result = dict(objects)
    for key, value in objects.items():
        if not key.startswith('@'):
            validate_node_times(value)
            created = value.get('createdAt') or value.get('updatedAt') or file_created_at or now
            result[key] = {**value, 'createdAt': created, 'updatedAt': value.get('updatedAt') or created}
        elif key.startswith('@gallery:item:'):
            result[key] = {**value, 'objects': complete_node_timestamps(value.get('objects', {}), file_created_at, now)}
    return result


def validate_node_times(value: dict) -> None:
    for key in ('createdAt', 'updatedAt'):
        timestamp = value.get(key)
        if timestamp is not None and (type(timestamp) is not int or not 0 < timestamp <= 2**53 - 1):
            raise HTTPException(422, '无效节点时间')
    if value.get('createdAt') and value.get('updatedAt') and value['updatedAt'] < value['createdAt']:
        raise HTTPException(422, '编辑时间早于创建时间')


def validate_node_timestamps(objects: dict[str, dict]) -> None:
    for key, value in objects.items():
        if not key.startswith('@'):
            validate_node_times(value)
        elif key.startswith('@gallery:item:'):
            for node_id, node in value.get('objects', {}).items():
                if not node_id.startswith('@'):
                    validate_node_times(node)
