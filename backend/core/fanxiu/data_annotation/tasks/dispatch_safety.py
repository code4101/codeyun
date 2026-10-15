"""Formal safe-boundary confirmation for releasing dispatch holds.

A dispatch hold (kernel_scheduler_control.record_dispatch_hold) is written when
a Job holding a native long action stopped without a proven safe exit. Kernel
idle is not game idle; only fresh world evidence observed after the hold may
release it. This confirmation observes, it never clicks or navigates.
"""

import time


def confirm_world_safe_boundary(context, *, timeout=15.0):
    """Observe exact world #34 now and release holds this fresh evidence proves safe.

    Returns the provider release outcome; a non-#34 scene or unreadable game
    process releases nothing. Call resume_engineering_control afterwards.
    """
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import release_dispatch_holds
    from backend.core.fanxiu.instrumentation.role_progression import read_role_progression_snapshot

    match = yield from context.wait_scene([34], wait=float(timeout), required=False,
                                          label='派发阻断解除：核验世界 #34')
    scene = getattr(match, 'scene_id', None)
    score = getattr(match, 'score', None)
    role = read_role_progression_snapshot()
    observed_at = time.time()
    if scene != 34 or role.get('complete') is not True:
        return {'released': [], 'remaining': None, 'scene_id': scene, 'score': score,
                'reason': 'world_34_or_role_not_confirmed'}
    evidence = {'kind': 'world_scene', 'scene_id': 34, 'score': score, 'observed_at': observed_at,
                'role_id': role.get('role_id'), 'pid': role['evidence']['pid'],
                'process_start_ticks': role['evidence']['process_start_ticks']}
    return {**release_dispatch_holds(evidence), 'evidence': evidence}
