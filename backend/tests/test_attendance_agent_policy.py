"""Attendance AI routing cannot restore historical model providers from saved inputs."""
import pytest

from backend.core.agents.contracts import AgentTaskRequest
from backend.core.agents.policy import resolve
from backend.core.agents.dispatch import dispatch_agent_task
from backend.core.agents.background import build_incident_prompt
from backend.core.ai.app_config import resolve_ai_app_runtime_config
from backend.core.messaging.wechat_agent import normalize_hook_processor


def test_attendance_defaults_to_single_local_cc():
    plan = resolve(business='attendance')
    assert plan['main'] == 'cc' and plan['aux'] is None
    for pair in ('codex', 'ds', 'cc+ds'):
        with pytest.raises(ValueError, match='cc'):
            dispatch_agent_task(AgentTaskRequest('test', 'test', 'test', business='attendance', pair=pair))


@pytest.mark.parametrize('app', ['attendance-precheck', 'note-sheet-excel-import', 'note-sheet-clockin-link-detection'])
def test_attendance_app_ignores_historical_provider_and_credentials(app):
    result = resolve_ai_app_runtime_config(session=None, current_user=None, app_id=app,
        provider='deepseek', model='historical-model', base_url='https://example.invalid', api_key='synthetic')
    assert result['provider'] == 'cc'
    assert result['model'] is None and result['api_key'] is None and result['base_url'] is None
    assert result['extra_providers'] == ()


def test_attendance_prompts_and_groups_use_the_attendance_boundary():
    prompt = build_incident_prompt(AgentTaskRequest('repair', 'failure', 'restore', business='attendance',
                                                  completion_criteria=('formal readback verified',)))
    assert '考勤技能' in prompt and '资金动作' in prompt
    assert 'Kernel/Cell' not in prompt
    hook = normalize_hook_processor({'domain': 'attendance', 'processor': 'codex'})
    assert hook['processor'] == 'claude' and hook['router_provider'] == 'ollama'
    private = {'domain': 'general', 'kind': 'owner_private', 'processor': 'codex'}
    assert normalize_hook_processor(private) == private


@pytest.mark.parametrize('processor', ['claude', 'codex', 'lingnet', 'device_agent', 'dots'])
def test_all_group_processors_normalize_historical_cloud_intake(processor):
    hook = normalize_hook_processor({'domain':'general', 'processor':processor,
        'message_routing':True, 'router_provider':'opencode_go', 'router_resource':'ds',
        'router_transport':'agent', 'router_model':'old-cloud', 'task_routing':True,
        'intake_model':'old-cloud'})
    assert hook['processor'] == processor
    assert hook['router_provider'] == 'ollama' and hook['router_resource'] == 'local'
    assert hook['router_transport'] == 'chat'
    assert hook['router_model'] == hook['intake_model'] == 'qwen3.5:4b-instruct'
