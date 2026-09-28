"""Shared authenticated WebSocket lifecycle for every object provider.

CodeYun user sessions are rechecked before commands and outbound messages;
provider.role bridges to the existing resource/parent permission policy.
"""
import asyncio
import contextlib
import json
import math
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from sqlmodel import Session
from backend.core.access.auth import get_current_user_from_token
from .objects import ObjectHead, ObjectCommit, ObjectRoom, Peer, snapshot, MAX_BYTES, MAX_BATCH

async def serve_socket(socket: WebSocket, resource_id: int, session: Session, room: ObjectRoom):
    await socket.accept()
    peer = None
    writer = None
    engine = session.get_bind()
    session.close()
    try:
        auth = await asyncio.wait_for(socket.receive_json(), 10)
        if not isinstance(auth, dict) or auth.get('type') != 'auth' or not isinstance(auth.get('token'), str):
            raise HTTPException(401, '请先登录')
        token = auth['token']

        def authorized(db):
            user = get_current_user_from_token(token, db)
            return user, room.provider.role(db, resource_id, user)

        async with room.guard:
            with Session(engine) as db:
                user, role = authorized(db)
                if not db.get(ObjectHead, resource_id):
                    raise HTTPException(409, '文件尚未启用协作')
                if len(room.peers) >= 64:
                    raise HTTPException(429, '此文件在线人数已达上限')
                peer = Peer(socket, user.id, user.nickname or user.username, role)
                room.peers[peer.id] = peer
                pending_ids = auth.get('pendingMutationIds', [])
                if not isinstance(pending_ids, list) or len(pending_ids) > 16 or any(not isinstance(item, str) for item in pending_ids):
                    raise HTTPException(422, '无效待确认操作')
                accepted = [identity for identity in pending_ids if (receipt := db.get(ObjectCommit, (resource_id, identity)))
                            and receipt.user_id == user.id]
                room.send(peer, dict(type='joined', peerId=peer.id, role=role, accepted=accepted, **snapshot(db, resource_id)))
                room.broadcast(room.presence())

        async def write_messages():
            try:
                while True:
                    message = await peer.outbox.get()
                    # Recheck before delivery as well as before mutation: revoked
                    # sockets must not receive future edits or collaborator data.
                    with Session(engine) as db:
                        _, current_role = authorized(db)
                        if current_role != peer.role:
                            raise HTTPException(403, '文件权限已变化，请重新打开')
                    await asyncio.wait_for(socket.send_json(message), 5)
            except (HTTPException, asyncio.TimeoutError, RuntimeError, WebSocketDisconnect):
                with contextlib.suppress(Exception):
                    await socket.close(code=1008)

        writer = asyncio.create_task(write_messages())
        while True:
            try:
                message = await asyncio.wait_for(socket.receive_json(), 5)
            except asyncio.TimeoutError:
                message = {'type': 'heartbeat'}
            if not isinstance(message, dict) or len(json.dumps(message)) > MAX_BYTES:
                raise HTTPException(413, '协作消息过大')
            request_id = message.get('id')
            async with room.guard:
                try:
                    with Session(engine) as db:
                        _, role = authorized(db)
                        if role != peer.role:
                            raise HTTPException(403, '文件权限已变化，请重新打开')
                        kind = message.get('type')
                        if kind in {'acquire', 'renew', 'release', 'commit'} and role == 'viewer':
                            raise HTTPException(403, '只读用户不能编辑')
                        if kind == 'acquire':
                            room.send(peer, dict(type='locked', id=request_id, tokens=room.acquire(peer, message.get('objects'))))
                        elif kind == 'renew':
                            tokens = message.get('tokens')
                            if not isinstance(tokens, dict) or len(tokens) > MAX_BATCH:
                                raise HTTPException(422, '无效锁凭据')
                            valid = room.renew(peer, tokens)
                            room.send(peer, dict(type='renewed', id=request_id, tokens=valid))
                        elif kind == 'release':
                            keys = message.get('objects')
                            if keys is not None and (not isinstance(keys, list) or any(not isinstance(key, str) for key in keys)):
                                raise HTTPException(422, '无效锁对象')
                            room.release(peer, keys)
                        elif kind == 'commit':
                            result = room.commit(db, peer, message)
                            room.broadcast({**result, 'id': request_id})
                        elif kind == 'presence':
                            cursor = message.get('cursor')
                            if cursor is not None and (not isinstance(cursor, dict) or any(
                                not isinstance(cursor.get(k), (int, float)) or not math.isfinite(cursor[k]) for k in ('x', 'y'))):
                                raise HTTPException(422, '无效光标')
                            selection = message.get('selection', [])
                            if not isinstance(selection, list) or len(selection) > MAX_BATCH or any(not isinstance(key, str) for key in selection):
                                raise HTTPException(422, '无效选中对象')
                            peer.cursor, peer.selection = cursor, selection
                            room.broadcast(dict(type='cursor', peerId=peer.id, cursor=cursor, selection=selection))
                        elif kind not in {'heartbeat'}:
                            raise HTTPException(422, '未知协作消息')
                        if kind in {'acquire', 'renew', 'release', 'heartbeat'}:
                            room.broadcast(room.presence())
                except HTTPException as exc:
                    room.send(peer, dict(type='error', id=request_id, code=exc.status_code, detail=exc.detail))
                    if exc.status_code in {401, 403, 404} and message.get('type') not in {'acquire', 'renew', 'release', 'commit'}:
                        break
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
        pass
    except (ValueError, TypeError, RecursionError):
        with contextlib.suppress(Exception):
            await socket.send_json(dict(type='error', code=422, detail='无效协作消息'))
    except HTTPException as exc:
        with contextlib.suppress(Exception):
            await socket.send_json(dict(type='error', code=exc.status_code, detail=exc.detail))
    finally:
        if writer:
            writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer
        if peer:
            async with room.guard:
                room.peers.pop(peer.id, None)
                room.release(peer)
                room.broadcast(room.presence())
                if not room.peers:
                    room.cached_objects = None
                    room.cached_sizes = {}
        with contextlib.suppress(Exception):
            await socket.close()
