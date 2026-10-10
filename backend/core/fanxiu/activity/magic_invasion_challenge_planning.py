"""Magic uses the shared first-100 / next-commodity milestone policy."""
from .exchange_challenge_planning import (
    exchange_challenge_milestones as magic_invasion_milestones,
    plan_exchange_challenge_batch as plan_magic_invasion_reward_batch,
)


def magic_invasion_seconds_per_exorcism(samples):
    """Latest continuously observed duration, independent of reward yields.

    Recovery can prove an original batch's count and rewards while its wall
    interval includes a failed attempt or a human wait. Such a row remains a
    valid yield sample, but cannot determine the next run's time capacity.
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
    A recovered yield remains usable in milestone arithmetic even when its
    wall interval cannot authorize a future batch's time capacity.
    """
    duration = magic_invasion_seconds_per_exorcism(samples)
    available_seconds = max(0, activity_end_at.timestamp() - 3600 - now.timestamp() - 90)
    time_capacity = int(available_seconds / duration) if duration is not None else 0
    plan = plan_magic_invasion_reward_batch(
        milestones=milestones, current_currency=current_currency,
        cumulative_currency=cumulative_currency, samples=samples,
        capacity=min(resource_capacity, time_capacity), now=now,
        activity_end_at=activity_end_at)
    plan.update(resource_capacity=resource_capacity, time_capacity=time_capacity)
    # Missing timing cannot reopen an expired activity or fast-mode window.
    # Keep those terminal admission decisions independent of yield recovery.
    if (available_seconds <= 0
            and plan.get('reason') in ('next_batch', 'resource_insufficient')):
        plan.update(status='pass', reason='fast_mode_window_closed', count=0)
    elif (duration is None
            and plan.get('reason') in ('next_batch', 'resource_insufficient')):
        plan.update(status='pending', reason='reliable_duration_missing', count=0)
    return plan
