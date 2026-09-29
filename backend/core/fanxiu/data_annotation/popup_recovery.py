"""Bounded recovery for a visually verified exit that repeatedly does nothing.

Unknown scenes and unverified Shapes still belong to scene repair. An AI may
certify an existing exit after comparing its geometry with a device frame.
That certificate is invalidated by any Shape edit. Only three consecutive
successful clicks followed by the same recognized popup request VM recovery.
The Scheduler executes recovery after the failed Cell has ended, then logs in
and submits a whole new business attempt. Never restart inside a generator.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from filelock import FileLock


class VerifiedPopupExitStalled(RuntimeError):
    """The verified exit exhausted its click budget; the old Cell must end."""


def exit_shape_signature(shape: dict) -> str:
    payload = {k: v for k, v in shape.items()
               if not k.startswith('_') and k != 'verifiedExitRecovery'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verified_exit_recovery(shape: dict | None, *, scene_id: int | None = None,
                           frame_size: tuple[int, int] | None = None) -> bool:
    if not isinstance(shape, dict) or shape.get('title') not in {'关闭', '返回', '取消', '离开'}:
        return False
    policy = shape.get('verifiedExitRecovery') or {}
    return (isinstance(policy, dict) and policy.get('action') == 'restart_emulator'
            and policy.get('shape_signature') == exit_shape_signature(shape)
            and (scene_id is None or policy.get('scene_id') == scene_id)
            and (frame_size is None or policy.get('frame_size') == list(frame_size)))


def certify_popup_exit(*, scene_id: int, shape_path: str, evidence_frame_path: str,
                       entry_id: str, geometry_verified: bool) -> None:
    """Record the AI's visual verification, never infer it from recognition score.

    Use only after inspecting the actual device frame and click coordinates.
    This authorizes one VM restart per 30 minutes after bounded failed exit clicks;
    it does not execute a click or restart. Changing the Shape revokes the approval.
    """
    from .storage import (data_annotation_asset_tree_path, update_data_annotation_asset_tree,
                          resolve_data_annotation_scene_node_id)
    if not geometry_verified or not Path(evidence_frame_path).is_file():
        raise ValueError('需真实帧和已完成的退出 Shape 几何核验')
    if shape_path not in {'关闭', '返回', '取消', '离开'}:
        raise ValueError('仅允许独立的安全退出 Shape')

    def update(tree):
        node_id = resolve_data_annotation_scene_node_id(tree, scene_id)
        def visit(nodes):
            for node in nodes:
                if node.get('id') == node_id:
                    matches = [s for s in node.get('shapes', []) if s.get('title') == shape_path]
                    if len(matches) != 1:
                        raise ValueError('退出 Shape 缺失或不唯一')
                    shape = matches[0]
                    shape['verifiedExitRecovery'] = dict(
                        action='restart_emulator', shape_signature=exit_shape_signature(shape),
                        scene_id=scene_id, frame_size=[node.get('width'), node.get('height')],
                        evidence_frame_path=str(evidence_frame_path), verified_at=time.time())
                    return True
                if visit(node.get('children') or []):
                    return True
            return False
        return visit(tree)
    update_data_annotation_asset_tree(data_annotation_asset_tree_path(entry_id), update)


def reserve_popup_restart(path: Path, *, now: float) -> None:
    """Persist before restart; failure and successful login both consume budget.

    One restart per 30 minutes across all popups/Jobs prevents restart-login loops.
    The budget survives Kernel/process replacement. Corrupt state fails closed.
    """
    from .state import write_data_annotation_json
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix('.lock')), timeout=5):
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError('退出卡滞恢复预算记录无效')
        last = float(state['last_restart_at']) if state else None
        if not math.isfinite(now) or (last is not None and (not math.isfinite(last) or last < 0)):
            raise ValueError('退出卡滞恢复预算时间无效')
        if last is not None and now - last < 1800:
            raise RuntimeError('退出卡滞恢复：30 分钟内已重启过模拟器，转交 AI 诊断')
        write_data_annotation_json(path, {'last_restart_at': now})


def restart_after_verified_popup_stall(*, reason: str, state_path: Path, dispatch_lease) -> dict:
    """Scheduler-only recovery while holding the submission lease, Kernel idle."""
    from .kernel_scheduler_control import kernel_scheduler_status
    from backend.core.fanxiu.client.mumu_control import recover_mumu_device
    expected = state_path.with_name('scheduler_cell_dispatch.lock').resolve()
    if not dispatch_lease.is_locked or Path(dispatch_lease.lock_file).resolve() != expected:
        raise RuntimeError('退出卡滞恢复需要 Scheduler 派发独占权')
    status = kernel_scheduler_status()
    if status.get('running') or (status.get('kernel') or {}).get('execution_state') != 'idle':
        raise RuntimeError('旧 Cell 未结束，禁止重启模拟器')
    reserve_popup_restart(state_path.with_name('popup_restart.json'), now=time.time())
    result = recover_mumu_device(force_restart=True, reason=reason)
    if not result.get('recovered'):
        raise RuntimeError(f'模拟器恢复未完成：{result}')
    return result
