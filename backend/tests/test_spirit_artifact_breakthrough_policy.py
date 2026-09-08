import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_breakthrough_action import (
    validate_spirit_artifact_breakthrough_attributes,
)


def check(*, values=(100, 100, 100, 100, 1, 1), qualities=(6,) * 6,
          codes=('ATTACK', 'SPECIAL1', 'SPECIAL2', 'MAXMP', 'MAXHP', 'DEFENSE'),
          a_codes=None, maximum=100, part=4):
    effects = [dict(cleanse_id=i + 1, value=value, quality=quality, locked=False)
               for i, (value, quality) in enumerate(zip(values, qualities))]
    rules = {i + 1: dict(code=code, max=maximum) for i, code in enumerate(codes)}
    validate_spirit_artifact_breakthrough_attributes(
        effects, rules=rules, part=part,
        a_codes=set(codes[:4]) if a_codes is None else a_codes,
    )


def test_dynamic_four_a_accepts_full_and_peak_without_full_b():
    check(values=(120, 100, 100, 100, 1, 1))


@pytest.mark.parametrize('part', [5, 6])
def test_configured_maximum_is_not_multiplied_again(part):
    check(values=(150, 150, 150, 150, 1, 1), maximum=150, part=part)


def test_nearly_full_a_rejected():
    with pytest.raises(RuntimeError):
        check(values=(100, 100, 100, 99, 100, 100))


def test_non_red_full_a_rejected():
    with pytest.raises(RuntimeError):
        check(qualities=(6, 6, 6, 5, 6, 6))


def test_duplicate_a_cannot_replace_missing_dynamic_a():
    with pytest.raises(RuntimeError):
        check(codes=('ATTACK', 'SPECIAL1', 'SPECIAL2', 'SPECIAL2', 'MAXHP', 'DEFENSE'),
              a_codes={'ATTACK', 'SPECIAL1', 'SPECIAL2', 'MAXMP'})
