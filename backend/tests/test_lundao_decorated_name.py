from backend.core.fanxiu.data_annotation.tasks.lundao import lundao_unique_cjk_name


def test_real_decorated_name_requires_complete_unique_seat_role_binding():
    target = {'name': 'ℳ亮ぅ晨曦ꦿ࿐', 'seat_id': 12843, 'role_id': 123}
    seat = {'seat_id': 12843, 'owner': {'name': target['name'], 'role_id': 123}}
    roster = {'complete': True, 'available': True, 'seats': [seat]}
    assert lundao_unique_cjk_name(target, roster) == '亮晨曦'
    assert not lundao_unique_cjk_name(target, {**roster, 'complete': False})
    assert not lundao_unique_cjk_name({**target, 'role_id': 124}, roster)
    assert not lundao_unique_cjk_name({**target, 'seat_id': 12844}, roster)
    collision = {'seat_id': 2, 'owner': {'name': '另一亮晨曦', 'role_id': 2}}
    assert not lundao_unique_cjk_name(target, {**roster, 'seats': [seat, collision]})
    collision['owner']['name'] = '亮晨晞'
    assert not lundao_unique_cjk_name(target, {**roster, 'seats': [seat, collision]})


def test_short_and_ordinary_names_keep_original_matching():
    for name in ('ℳ晨曦', '亮晨曦'):
        target = {'name': name, 'seat_id': 1, 'role_id': 1}
        roster = {'complete': True, 'available': True,
                  'seats': [{'seat_id': 1, 'owner': {'name': name, 'role_id': 1}}]}
        assert not lundao_unique_cjk_name(target, roster)
