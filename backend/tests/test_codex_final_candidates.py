import json
import pytest
from backend.core.codex.final_candidates import merge_structured_final_text


def test_same_turn_unrelated_ignore_preserves_unsent_owner_answer():
    reply = {'action': 'reply', 'text': '新Goal已创建', 'summary': 'old', 'question_seqs': [2450]}
    ignored = {'action': 'ignore', 'text': '', 'summary': '指导已接收', 'question_seqs': []}
    actual = json.loads(merge_structured_final_text(json.dumps(reply), json.dumps(ignored)))
    assert actual == {**reply, 'summary': '指导已接收'}


@pytest.mark.parametrize('replacement', [
    {'action': 'ignore', 'question_seqs': [2450], 'text': ''},
    {'action': 'defer', 'question_seqs': [], 'text': ''},
    {'action': 'reply', 'question_seqs': [2450], 'text': 'updated'},
    {'action': 'ignore', 'text': ''},
])
def test_explicit_replacement_or_invalid_schema_is_not_hidden(replacement):
    current = json.dumps(replacement)
    assert merge_structured_final_text(json.dumps({'action': 'reply'}), current) == current


def test_invalid_latest_json_remains_visible_to_terminal_validation():
    assert merge_structured_final_text('{"action":"reply"}', 'bad') == 'bad'
    assert merge_structured_final_text('', '{"action":"ignore"}') == '{"action":"ignore"}'
    assert merge_structured_final_text('old', '') == 'old'
