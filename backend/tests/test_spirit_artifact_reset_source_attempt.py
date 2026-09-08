import json

import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_reset_workflow import (
    begin_reset_source_attempt, record_reset_source_failure,
)


def test_explicit_not_sent_allows_next_claim_and_preserves_history(tmp_path):
    path = tmp_path / 'intent.json'
    first = begin_reset_source_attempt(path, {'entry_key': '4-3-old'})
    with pytest.raises(RuntimeError):
        begin_reset_source_attempt(path, {})
    error = RuntimeError('定位失败，确认尚未调用')
    error.purchase_confirmation_may_have_been_sent = False
    record_reset_source_failure(path, first, error)
    second = begin_reset_source_attempt(path, {'entry_key': '4-3-old'})
    value = json.loads(path.read_text(encoding='utf-8'))
    assert first != second and value['status'] == 'pending'
    assert value['history'][0]['status'] == 'not_sent'
    assert value['history'][0]['reason'] == str(error)
    with pytest.raises(RuntimeError):
        record_reset_source_failure(path, first, error)


@pytest.mark.parametrize('sent', [True, None, 0])
def test_unknown_or_sent_never_authorizes_retry(tmp_path, sent):
    path = tmp_path / 'intent.json'
    attempt = begin_reset_source_attempt(path, {})
    error = RuntimeError('失败')
    error.purchase_confirmation_may_have_been_sent = sent
    record_reset_source_failure(path, attempt, error)
    with pytest.raises(RuntimeError):
        begin_reset_source_attempt(path, {})


def test_legacy_intent_without_status_remains_blocked(tmp_path):
    path = tmp_path / 'intent.json'
    path.write_text('{"entry_key":"old"}')
    with pytest.raises(RuntimeError):
        begin_reset_source_attempt(path, {})
