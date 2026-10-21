import pytest

from backend.core.messaging.wechat_wake_policy import admit_messages


@pytest.mark.parametrize('processor', ['codex', 'claude', 'lingnet', 'device_agent'])
@pytest.mark.parametrize('sender', ['owner', 'member'])
def test_mentions_mode_requires_at_even_for_owner_and_awake_session(processor, sender):
    hook = {'account_id': 'bot', 'processor': processor, 'wake_mode': 'mentions',
            'mention_aliases': ['Code4102'], 'priority_sender_ids': ['owner']}
    state = {'thread_id': 'existing', 'awake_until': 9999999999}
    message = {'sender_id': sender, 'text': '你好', 'mentions': []}
    assert not admit_messages([message], hook, state)
    assert admit_messages([{**message, 'mentions': ['bot']}], hook, state)
    assert admit_messages([{**message, 'text': '@Code4102 你好'}], hook, state)
    assert not admit_messages([{**message, 'text': '@Code41020 你好'}], hook, state)


def test_default_keeps_owner_priority_and_open_group_ignores_self():
    hook = {'account_id': 'bot', 'priority_sender_ids': ['owner']}
    assert admit_messages([{'sender_id': 'owner'}], hook, {})
    assert not admit_messages([{'sender_id': 'member'}], hook, {})
    assert admit_messages([{'sender_id': 'member'}], {**hook, 'wake_mode': 'all_members'}, {})
    assert not admit_messages([{'sender_id': 'bot', 'mentions': ['bot']}],
                              {**hook, 'wake_mode': 'all_members'}, {})


@pytest.mark.parametrize('sender', ['owner', 'member'])
def test_finite_window_applies_to_owner_and_member(sender):
    import time
    hook = {'account_id': 'bot', 'followup_seconds': 300, 'priority_sender_ids': ['owner']}
    message = {'sender_id': sender, 'text': '机器人已经处理好了'}
    state = {'thread_id': 'existing', 'awake_until': time.time() - 7200}
    assert not admit_messages([message], hook, state)
    assert admit_messages([{**message, 'mentions': ['bot']}], hook, state)
    assert admit_messages([message], hook, {**state, 'awake_until': time.time() + 300})
    assert admit_messages([message], {**hook, 'wake_mode': 'all_members'}, state)
