"""Portable node audit times: Unix milliseconds; absent/NULL means unknown.

The editor owns authored changes. Import/export and remote delivery preserve
times verbatim; opening an old file cannot reconstruct its creation history.
"""
from fastapi import HTTPException


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
