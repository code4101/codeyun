"""Lossless PG archive boundary: native serializer paths ↔ stable object IDs.

Only top-level stage objects become lease units. Geometry, rich text and other
nested values stay within their parent object. ZIP members unknown to CodeYun
are preserved verbatim, including attachments and future upstream metadata.
"""
from __future__ import annotations

import base64
import copy
import io
import zipfile

import msgpack
from fastapi import HTTPException
from backend.core.project_graph.gallery import member_key, key_member, validate_gallery, ITEM

MEMBERS = {'@tags': 'tags.msgpack', '@references': 'reference.msgpack', '@metadata': 'metadata.msgpack'}


def has_prg_content(content: bytes) -> bool:
    """Whether a new canvas contains authored content; camera/metadata do not count."""
    objects = import_prg(content)
    references = objects.get('@references', {}).get('value') or {}
    if isinstance(references, dict) and any(references.values()):
        return True
    return any(not key.startswith('@') or key == '@gallery:index' or key.startswith(ITEM) or key.startswith('@attachment:')
               or (key in {'@tags', '@readme'} and bool(value.get('value')))
               for key, value in objects.items())


def stage_to_objects(stage: list) -> dict[str, dict]:
    def resolve(value):
        seen = set()
        while isinstance(value, dict) and set(value) == {'$'}:
            path = value['$']
            if not isinstance(path, str) or path in seen:
                raise ValueError('循环或无效对象引用')
            seen.add(path)
            value = stage
            for segment in path.split('/'):
                if segment:
                    value = value[int(segment)] if isinstance(value, list) else value[segment]
        return value

    top = [resolve(item) for item in stage]
    if any(not isinstance(item, dict) or not isinstance(item.get('uuid'), str) for item in top):
        raise ValueError('不支持的图对象结构')
    identities = {item['uuid'] for item in top}
    if len(identities) != len(top):
        raise ValueError('重复图对象身份')

    def rewrite(value, root=False):
        value = resolve(value)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            if not root and value.get('uuid') in identities:
                return {'$graphRef': value['uuid']}
            return {key: rewrite(item) for key, item in value.items()}
        return value

    return {**{item['uuid']: rewrite(item, True) for item in top}, '@order': {'value': [item['uuid'] for item in top]}}


def objects_to_stage(objects: dict[str, dict]) -> list:
    order = objects.get('@order', {}).get('value', [])
    identities = {key for key in objects if not key.startswith('@')}
    if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != identities:
        raise ValueError('对象顺序与对象集合不一致')
    paths = {}

    def expand(value, path):
        if isinstance(value, list):
            return [expand(item, f'{path}/{index}') for index, item in enumerate(value)]
        if isinstance(value, dict):
            if set(value) == {'$graphRef'}:
                return object_at(value['$graphRef'], path)
            return {key: expand(item, f'{path}/{key}') for key, item in value.items()}
        return value

    def object_at(identity, path):
        if identity in paths:
            return {'$': paths[identity]}
        if identity not in identities:
            raise ValueError('悬空对象引用')
        paths[identity] = path
        return expand(objects[identity], path)

    return [object_at(identity, f'/{index}') for index, identity in enumerate(order)]


def import_prg(content: bytes) -> dict[str, dict]:
    if not content:
        raise HTTPException(409, '请先打开并保存新文件，再启用协作')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            stage = msgpack.unpackb(archive.read('stage.msgpack'), raw=False)
            if not isinstance(stage, list):
                raise ValueError('无效舞台')
            objects = stage_to_objects(stage)
            for key, member in MEMBERS.items():
                if member in archive.namelist():
                    objects[key] = {'value': msgpack.unpackb(archive.read(member), raw=False)}
            if 'README.md' in archive.namelist():
                objects['@readme'] = {'value': archive.read('README.md').decode('utf-8')}
            # Attachments participate in the same durable state as object edits;
            # new image content cannot be acknowledged before it is persisted.
            for member in archive.namelist():
                gallery_key = member_key(member)
                if gallery_key:
                    if gallery_key in objects:
                        raise ValueError('重复图库成员')
                    objects[gallery_key] = msgpack.unpackb(archive.read(member), raw=False)
                if member.startswith('attachments/') and not member.endswith('/'):
                    objects['@attachment:' + member[12:]] = {'value': base64.b64encode(archive.read(member)).decode('ascii')}
            validate_gallery(objects)
            return objects
    except (ValueError, KeyError, IndexError, TypeError, RecursionError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, f'此 PRG 无法启用协作：{exc}') from exc


def export_prg(template: bytes, objects: dict[str, dict]) -> bytes:
    """Materialize a standard PRG for export or a rollback to the sharing baseline."""
    try:
        validate_gallery(objects)
        replacements = {'stage.msgpack': msgpack.packb(objects_to_stage(objects), use_bin_type=True)}
        for key, member in MEMBERS.items():
            if key in objects:
                replacements[member] = msgpack.packb(objects[key]['value'], use_bin_type=True)
        if '@readme' in objects:
            replacements['README.md'] = objects['@readme']['value'].encode('utf-8')
        for key, value in objects.items():
            gallery_member = key_member(key)
            if gallery_member:
                replacements[gallery_member] = msgpack.packb(value, use_bin_type=True)
            if key.startswith('@attachment:'):
                name = key[12:]
                if not name or '/' in name or '\\' in name or name in {'.', '..'}:
                    raise ValueError('无效附件名称')
                replacements['attachments/' + name] = base64.b64decode(value['value'], validate=True)
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(template)) as original, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            managed = set(MEMBERS.values()) | {'stage.msgpack', 'README.md'}
            for info in original.infolist():
                if info.filename not in managed and not info.filename.startswith('attachments/') and not member_key(info.filename):
                    archive.writestr(info, original.read(info.filename))
            for name, value in replacements.items():
                archive.writestr(name, value)
        return output.getvalue()
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise HTTPException(422, f'无法导出 PRG：{exc}') from exc
