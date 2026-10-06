"""Account-scoped access to this deployment's game, without host credentials.

Viewing never takes control. Each manual input acquires the existing engineering
handoff, runs in the unique Kernel, and returns ownership. Busy/AI-owned games
refuse input rather than interrupting another task.
"""
from __future__ import annotations

import json
import time
from uuid import UUID
from pathlib import Path

from fastapi import HTTPException
from filelock import FileLock
from sqlmodel import Session, select

from backend.core.settings import get_settings
from backend.models import User


def sharing_path() -> Path:
    path = get_settings().data_dir / 'fanxiu/game-sharing.json'
    return path


def read_game_sharing() -> dict:
    path = sharing_path()
    if not path.parent.exists():
        return {'users':{}}
    with FileLock(str(path.with_suffix('.lock')),timeout=10):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'users':{}}


def grant_game_sharing(session: Session, username: str, *, view: bool = True, control: bool = True) -> dict:
    """Grant/revoke one existing active login, never elevate its system role."""
    user = session.exec(select(User).where(User.username==username.strip())).first()
    if user is None or not user.is_active or user.id is None:
        raise ValueError('CodeYun账号不存在或未启用')
    path = sharing_path()
    path.parent.mkdir(parents=True,exist_ok=True)
    with FileLock(str(path.with_suffix('.lock')),timeout=10):
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'users':{}}
        grant = {'username':user.username,'view':bool(view or control),'control':bool(control),'updated_at':time.time()}
        state['users'][str(user.id)] = grant
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(path)
    return {'user_id':user.id,**grant}


def game_sharing_permission(user: User) -> dict:
    if not user.is_active:
        return {'view':False,'control':False}
    if user.is_superuser:
        return {'view':True,'control':True}
    grant = read_game_sharing()['users'].get(str(user.id),{})
    # Deleted/recreated accounts must not inherit a recycled numeric ID.
    return grant if grant.get('username') == user.username else {'view':False,'control':False}


def require_game_sharing(user: User, *, control: bool = False) -> dict:
    grant = game_sharing_permission(user)
    if not grant.get('control' if control else 'view'):
        raise HTTPException(403,'当前账号尚未加入游戏共享白名单')
    return grant


def shared_game_status(user: User) -> dict:
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler
    grant = require_game_sharing(user)
    status = scheduler.kernel_scheduler_status()
    busy = bool(status.get('running') or status.get('kernel',{}).get('execution_state') != 'idle'
                or not status.get('job_group_enabled'))
    return {'view':True,'control':bool(grant.get('control')),'busy':busy,
            'message':'自动任务正在操作，可观看；空闲后可手动操作' if busy else '游戏空闲，可操作'}


def execute_shared_game_input(user: User, action: dict, *, request_id: str) -> dict:
    """Run exactly one bounded click/drag; never retry an ambiguous side effect."""
    require_game_sharing(user,control=True)
    request_id = str(UUID(request_id))
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler
    from backend.core.fanxiu.data_annotation.ai_assistance import assistance_control_lock
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import execute_fanxiu_jupyter_cell
    with assistance_control_lock():
        receipts = sharing_path().parent / 'shared-game-inputs'
        receipts.mkdir(parents=True,exist_ok=True)
        receipt = receipts / f'{user.id}-{request_id}.json'
        if receipt.exists():
            previous = json.loads(receipt.read_text(encoding='utf-8'))
            if previous.get('action') != action:
                raise HTTPException(409,'同一操作编号不能用于不同操作')
            if previous.get('status') == 'sent':
                return {'ok':True,'replayed':True}
            raise HTTPException(409,'该次操作已提交，结果需核验，禁止重复执行')
        if shared_game_status(user)['busy']:
            raise HTTPException(409,'游戏正在由自动任务使用，请稍后操作')
        # Use the existing ownership fact. A racing engineering claim is
        # checked again after admission closes, without interrupting its Cell.
        scheduler.set_scheduler_job_group_enabled(False)
        try:
            status = scheduler.kernel_scheduler_status()
            if status.get('running') or status.get('kernel',{}).get('execution_state') != 'idle':
                raise HTTPException(409,'自动任务刚开始执行，请稍后操作')
            call = 'click_mumu_window_processed_point' if action['kind']=='click' else 'drag_mumu_window_processed_points'
            args = action['args']
            code = ('import json\nfrom backend.core.fanxiu.client.mumu_control import '+call+'\n'
                    'print(json.dumps('+call+'(**json.loads('+repr(json.dumps(args))+')),ensure_ascii=False))')
            receipt.write_text(json.dumps({'status':'submitted','action':action,'at':time.time()}),encoding='utf-8')
            result = execute_fanxiu_jupyter_cell(code,timeout_seconds=None,max_output_chars=2500)
            if result.get('error') or result.get('error_type'):
                raise HTTPException(502,'操作结果需核验，请刷新画面，勿重复点击')
            try:
                actual = json.loads(result.get('output','').splitlines()[-1])
            except (ValueError,IndexError):
                actual = {}
            if actual.get('ok') is not True:
                raise HTTPException(502,'操作未取得可靠回执，请刷新画面，勿重复点击')
            receipt.write_text(json.dumps({'status':'sent','action':action,'at':time.time()}),encoding='utf-8')
            return {'ok':True}
        finally:
            scheduler.resume_engineering_control()
