"""Magic uses the shared first-100 / next-commodity milestone policy."""
from .exchange_challenge_planning import (
    exchange_challenge_milestones as magic_invasion_milestones,
    plan_exchange_challenge_batch as plan_magic_invasion_reward_batch,
)


def magic_invasion_seconds_per_exorcism(samples):
    """Latest continuously observed duration, independent of reward yields.

    Recovery can prove an original batch's count and rewards while its wall
    interval includes a failed attempt or a human wait. Such a row remains a
    valid yield sample, but cannot determine a per-run speed estimate. The
    planner separately bounds one naturally needed recovery batch by its
    whole elapsed interval without treating it as continuous timing.
    Legacy rows without the flag retain their original timing contract.
    """
    from math import isfinite

    for row in reversed(samples):
        if row.get('duration_reliable', True) is not True:
            continue
        count, seconds = row.get('completed_exorcisms'), row.get('duration_seconds')
        if (type(count) is not int or count <= 0
                or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not isfinite(seconds) or seconds <= 0):
            continue
        return seconds / count
    return None


def plan_magic_invasion_timed_reward_batch(*, milestones, current_currency,
        cumulative_currency, samples, resource_capacity, now, activity_end_at):
    """Combine proven yields with reliable runtime for future consumption.

    Already funded/deferred targets do not require a new speed estimate.
    A recovery interval is not a speed estimate. When no continuous sample
    exists, it can conservatively admit one naturally needed batch no larger
    than that completed batch, only if the whole upper-bound interval fits.
    Never create a measurement-only batch or scale this bound to more runs.
    """
    duration = magic_invasion_seconds_per_exorcism(samples)
    available_seconds = max(0, activity_end_at.timestamp() - 3600 - now.timestamp() - 90)
    time_capacity = int(available_seconds / duration) if duration is not None else 0
    duration_source = 'continuous' if duration is not None else 'missing'
    recovery_limit = 0
    recovery_seconds = None
    if duration is None:
        from math import isfinite
        for row in reversed(samples):
            count, seconds = row.get('completed_exorcisms'), row.get('duration_seconds')
            if (row.get('duration_reliable') is not False
                    or type(count) is not int or count < 100
                    or row.get('requested_exorcisms', count) != count
                    or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not isfinite(seconds) or seconds <= 0):
                continue
            recovery_limit, recovery_seconds = count, seconds
            time_capacity = count if available_seconds >= seconds else 0
            duration_source = 'recovered_upper_bound'
            break
    plan = plan_magic_invasion_reward_batch(
        milestones=milestones, current_currency=current_currency,
        cumulative_currency=cumulative_currency, samples=samples,
        capacity=min(resource_capacity, time_capacity), now=now,
        activity_end_at=activity_end_at)
    plan.update(resource_capacity=resource_capacity, time_capacity=time_capacity)
    plan.update(duration_source=duration_source,
                recovery_batch_limit=recovery_limit, recovery_upper_bound_seconds=recovery_seconds)
    # Missing timing cannot reopen an expired activity or fast-mode window.
    # Keep those terminal admission decisions independent of yield recovery.
    if (available_seconds <= 0
            and plan.get('reason') in ('next_batch', 'resource_insufficient')):
        plan.update(status='pass', reason='fast_mode_window_closed', count=0)
    elif (duration is None and recovery_seconds is None
            and plan.get('reason') in ('next_batch', 'resource_insufficient')):
        plan.update(status='pending', reason='reliable_duration_missing', count=0)
    return plan
