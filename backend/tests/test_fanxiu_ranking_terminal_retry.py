from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, create_engine

from backend.core.fanxiu.activity.ranking_lifecycle import RankingCheckpoint
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    clear_terminal_ranking_checkpoint_retries,
    ensure_ranking_lifecycle_checkpoint_table,
    ranking_checkpoint_retry_times,
    record_ranking_checkpoint_result,
)


@pytest.fixture
def session():
    engine = create_engine('sqlite://')
    ensure_ranking_lifecycle_checkpoint_table(engine)
    with Session(engine) as current:
        yield current
    engine.dispose()


def checkpoint(instance='today'):
    return RankingCheckpoint(instance, 'lingchong-jingwu', 'resource_rank', 'runtime-1',
        16042901, 'lingchong_round_rewards_once', '2026-10-08',
        datetime(2026, 10, 8, 5, tzinfo=timezone(timedelta(hours=8))))


@pytest.mark.parametrize('status', ['completed', 'retained', 'unavailable'])
def test_terminal_result_clears_prior_and_supplied_retry_time(session, status):
    cp = checkpoint()
    retry = cp.due_at + timedelta(hours=6)
    record_ranking_checkpoint_result(session, cp, status='pending', retry_at=retry)
    row = record_ranking_checkpoint_result(session, cp, status=status, retry_at=retry)
    assert row.retry_at == ''
    assert ranking_checkpoint_retry_times(session, family='resource_rank') == ()


@pytest.mark.parametrize('status', ['pending', 'blocked', 'error'])
def test_unfinished_checkpoint_retains_its_retry(session, status):
    cp = checkpoint()
    retry = cp.due_at + timedelta(hours=6)
    record_ranking_checkpoint_result(session, cp, status=status, retry_at=retry)
    assert ranking_checkpoint_retry_times(session, family='resource_rank') == (retry,)


def test_legacy_terminal_retry_cannot_mask_real_pending_retry(session):
    cp = checkpoint('legacy')
    retry = cp.due_at + timedelta(hours=6)
    legacy = record_ranking_checkpoint_result(session, cp, status='completed')
    # Reproduce a historical row from before the provider cleared terminal retry.
    legacy.retry_at = retry.isoformat()
    session.add(legacy)
    session.commit()
    pending_retry = retry + timedelta(hours=1)
    record_ranking_checkpoint_result(session, checkpoint('pending'), status='blocked', retry_at=pending_retry)
    assert ranking_checkpoint_retry_times(session, family='resource_rank') == (pending_retry,)
    original_attempts = legacy.attempt_count
    assert clear_terminal_ranking_checkpoint_retries(session, family='resource_rank') == 1
    session.refresh(legacy)
    assert legacy.retry_at == ''
    assert legacy.status == 'completed'
    assert legacy.attempt_count == original_attempts
    assert ranking_checkpoint_retry_times(session, family='resource_rank') == (pending_retry,)
    assert clear_terminal_ranking_checkpoint_retries(session, family='resource_rank') == 0
