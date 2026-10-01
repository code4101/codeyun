import pytest

from backend.core.fanxiu.data_annotation.tasks.sword_spirit_update import decomposition_prompt_kind


@pytest.mark.parametrize(('prompt', 'expected'), [
    ('批量分解仙品及以下剑纹，是否确认分解？', 'quality'),
    ('本次批量分解【仙品及以下】剑纹，是否确认分解？', 'quality'),
    ('批量分解的剑纹中存在比佩戴中剑纹评分更高或有未装配的剑纹，是否继续分解？', 'equipment_warning'),
    ('批量分解的剑纹中存在比佩戴中剑纹评分更高或有未装配的剑纹,是否继续分解?', 'equipment_warning'),
    ('批量分解仙品及以下剑纹，\n是否确认分解？', 'quality'),
    ('批量分解绝品及以下剑纹，是否确认分解？', None),
    ('仙品及以下', None),
    ('是否继续分解？', None),
    ('批量分解的灵器中存在评分更高的灵器，是否继续分解？', None),
    ('批量分解仙品及以下灵器，是否确认分解？', None),
    ('批量分解的剑纹中存在品质较高的剑纹，是否继续分解？', None),
    ('', None),
])
def test_decomposition_prompt_keeps_exact_business_scope(prompt, expected):
    assert decomposition_prompt_kind(prompt) == expected
