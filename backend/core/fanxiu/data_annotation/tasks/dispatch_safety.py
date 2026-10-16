"""Formal safe-boundary confirmation for releasing dispatch holds.

A dispatch hold (kernel_scheduler_control.record_dispatch_hold) is written when
a Job holding a native long action stopped without a proven safe exit. Kernel
idle is not game idle; only fresh world evidence observed after the hold may
release it. This confirmation observes, it never clicks or navigates.
"""


def confirm_world_safe_boundary(context, *, timeout=15.0):
    """Observe world #34 now; the provider builds the evidence and releases holds it proves safe.

    A non-#34 scene or unreadable game process releases nothing. Call
    resume_engineering_control afterwards.
    """
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import release_dispatch_holds_with_world_observation

    match = yield from context.wait_scene([34], wait=float(timeout), required=False,
                                          label='派发阻断解除：核验世界 #34')
    return release_dispatch_holds_with_world_observation(getattr(match, 'scene_id', None),
                                                         getattr(match, 'score', None))
