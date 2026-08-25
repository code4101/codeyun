from backend.api import fanxiu_instrumentation as api


def test_lingmai_snapshot_route_uses_standard_fanxiu_authorization(monkeypatch):
    authorized = []
    monkeypatch.setattr(api, "_authorize", lambda user, session: authorized.append((user, session)))
    monkeypatch.setattr(
        api.fanxiu_instrumentation_service,
        "lingmai_snapshot",
        lambda: {"kind": "lingmai"},
    )
    user = object()
    session = object()

    assert api.get_fanxiu_lingmai_snapshot(current_user=user, session=session) == {
        "kind": "lingmai"
    }
    assert authorized == [(user, session)]


def test_arena_snapshot_routes_use_standard_fanxiu_authorization(monkeypatch):
    authorized = []
    monkeypatch.setattr(api, "_authorize", lambda user, session: authorized.append((user, session)))
    monkeypatch.setattr(api.fanxiu_instrumentation_service, "daofa_snapshot", lambda: {"kind": "daofa"})
    monkeypatch.setattr(
        api.fanxiu_instrumentation_service,
        "xianyuan_duel_snapshot",
        lambda: {"kind": "xianyuan-duel"},
    )
    user = object()
    session = object()

    assert api.get_fanxiu_daofa_snapshot(current_user=user, session=session) == {"kind": "daofa"}
    assert api.get_fanxiu_xianyuan_duel_snapshot(current_user=user, session=session) == {
        "kind": "xianyuan-duel"
    }
    assert authorized == [(user, session), (user, session)]


def test_lingzhuang_huadao_route_uses_standard_fanxiu_authorization(monkeypatch):
    authorized = []
    monkeypatch.setattr(api, "_authorize", lambda user, session: authorized.append((user, session)))
    monkeypatch.setattr(
        api.fanxiu_instrumentation_service,
        "lingzhuang_huadao_ranking_snapshot",
        lambda: {"kind": "lingzhuang-huadao"},
    )
    user = object()
    session = object()

    assert api.get_fanxiu_lingzhuang_huadao_ranking_snapshot(
        current_user=user,
        session=session,
    ) == {"kind": "lingzhuang-huadao"}
    assert authorized == [(user, session)]


def test_generic_activity_rank_route_uses_activity_id_and_authorization(monkeypatch):
    authorized = []
    calls = []
    monkeypatch.setattr(
        api, "_authorize", lambda user, session: authorized.append((user, session))
    )
    monkeypatch.setattr(
        api.fanxiu_instrumentation_service,
        "activity_rank_snapshot",
        lambda activity_id: calls.append(activity_id) or {"activity_id": activity_id},
    )
    user = object()
    session = object()

    assert api.get_fanxiu_activity_rank_snapshot(
        98765,
        current_user=user,
        session=session,
    ) == {"activity_id": 98765}
    assert calls == [98765]
    assert authorized == [(user, session)]


def test_beast_spirit_route_uses_standard_authorization_and_optimize_flag(monkeypatch):
    authorized = []
    calls = []
    monkeypatch.setattr(api, "_authorize", lambda user, session: authorized.append((user, session)))
    monkeypatch.setattr(
        api.fanxiu_instrumentation_service,
        "beast_spirit_snapshot",
        lambda *, optimize: calls.append(optimize) or {"kind": "beast-spirit"},
    )
    user = object()
    session = object()

    assert api.get_fanxiu_beast_spirit_snapshot(
        optimize=False,
        current_user=user,
        session=session,
    ) == {"kind": "beast-spirit"}
    assert calls == [False]
    assert authorized == [(user, session)]
