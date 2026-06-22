from sqlmodel import select

from backend.app import app
from backend.api import fanxiu as fanxiu_api
from backend.core.access.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.notes.refs import note_public_id
from backend.core.notes.semantics import NOTE_WEIGHT_MODE_LINEAR
from backend.models import NoteNode


def _override_user(user):
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override():
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def test_fanxiu_wardrobe_note_put_creates_note_and_persists_note_id(client, session, monkeypatch):
    wardrobe_hall = {
        "robes": [
            {
                "id": "robe-1",
                "name": "青纹法袍",
                "rank": 7,
                "date": "2026-06-01",
            }
        ]
    }
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_wardrobe_hall", lambda: wardrobe_hall)
    monkeypatch.setattr(fanxiu_api, "save_wardrobe_hall", lambda payload: saved_payloads.append(payload) or payload)

    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    _override_user(fanxiu_user)

    try:
        response = client.put(
            "/api/fanxiu/inventory/wardrobe-notes/robe-1",
            json={
                "content": "<p>适合前期过渡</p>",
                "note_types": [{"key": fanxiu_api.FANXIU_WARDROBE_TYPE, "weight": 100}],
                "node_status": "active",
                "custom_fields": [["来源", "text", "测试"]],
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "青纹法袍"
    assert payload["content"] == "<p>适合前期过渡</p>"
    assert payload["weight"] == 7
    assert payload["note_kind"] == fanxiu_api.FANXIU_WARDROBE_KIND
    assert payload["weight_mode"] == NOTE_WEIGHT_MODE_LINEAR
    assert payload["custom_fields"] == []

    created_note = session.exec(select(NoteNode).where(NoteNode.title == "青纹法袍")).one()
    assert created_note.user_id == fanxiu_user.id
    assert created_note.content == "<p>适合前期过渡</p>"
    assert created_note.weight == 7
    assert created_note.note_kind == fanxiu_api.FANXIU_WARDROBE_KIND
    assert created_note.node_type == fanxiu_api.FANXIU_WARDROBE_TYPE
    assert created_note.note_types == [{"key": fanxiu_api.FANXIU_WARDROBE_TYPE, "weight": 100}]
    assert created_note.node_status == "active"
    assert created_note.custom_fields == []

    assert len(saved_payloads) == 1
    assert wardrobe_hall["robes"][0]["note_id"] == note_public_id(created_note)
    assert payload["id"] == created_note.numeric_id


def test_fanxiu_wardrobe_note_put_updates_existing_note_and_keeps_identity(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    existing_note = NoteNode(
        id="wardrobe-note-legacy",
        numeric_id=31001,
        legacy_id="wardrobe-note-legacy",
        user_id=fanxiu_user.id,
        title="旧法袍",
        content="<p>旧内容</p>",
        weight=1,
        node_type="memo",
        note_types=[],
        note_kind=fanxiu_api.FANXIU_WARDROBE_KIND,
        node_status="idea",
        weight_mode=None,
        custom_fields={},
        history=[],
        start_at=100.0,
        created_at=100.0,
        updated_at=100.0,
    )
    session.add(existing_note)
    session.commit()

    wardrobe_hall = {
        "robes": [
            {
                "id": "robe-1",
                "name": "青纹法袍",
                "rank": 9,
                "date": "2026-06-02",
                "note_id": note_public_id(existing_note),
            }
        ]
    }
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_wardrobe_hall", lambda: wardrobe_hall)
    monkeypatch.setattr(fanxiu_api, "save_wardrobe_hall", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/inventory/wardrobe-notes/robe-1",
            json={
                "content": "<p>更新内容</p>",
                "node_status": "done",
                "custom_fields": [["评分", "number", 5]],
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == existing_note.numeric_id
    assert payload["title"] == "青纹法袍"
    assert payload["content"] == "<p>更新内容</p>"
    assert payload["weight"] == 9
    assert payload["note_kind"] == fanxiu_api.FANXIU_WARDROBE_KIND
    assert payload["node_status"] == "done"
    assert payload["custom_fields"] == [["评分", "number", 5]]

    session.refresh(existing_note)
    assert existing_note.title == "青纹法袍"
    assert existing_note.content == "<p>更新内容</p>"
    assert existing_note.weight == 9
    assert existing_note.note_kind == fanxiu_api.FANXIU_WARDROBE_KIND
    assert existing_note.weight_mode == NOTE_WEIGHT_MODE_LINEAR
    assert existing_note.node_type == fanxiu_api.FANXIU_WARDROBE_TYPE
    assert existing_note.note_types == [{"key": fanxiu_api.FANXIU_WARDROBE_TYPE, "weight": 100}]
    assert existing_note.node_status == "done"
    assert existing_note.custom_fields == [["评分", "number", 5]]

    assert len(session.exec(select(NoteNode).where(NoteNode.title == "青纹法袍")).all()) == 1
    assert len(saved_payloads) == 1
    assert wardrobe_hall["robes"][0]["note_id"] == note_public_id(existing_note)


def test_fanxiu_wardrobe_hall_put_syncs_existing_note_reference(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    existing_note = NoteNode(
        id="wardrobe-hall-note",
        numeric_id=35001,
        legacy_id="wardrobe-hall-note",
        user_id=fanxiu_user.id,
        title="旧道具名",
        content="<p>保留内容</p>",
        weight=1,
        node_type=fanxiu_api.FANXIU_WARDROBE_TYPE,
        note_types=[{"key": fanxiu_api.FANXIU_WARDROBE_TYPE, "weight": 100}],
        note_kind=fanxiu_api.FANXIU_WARDROBE_KIND,
        node_status="active",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        custom_fields=[],
        history=[],
        start_at=100.0,
        created_at=100.0,
        updated_at=100.0,
    )
    session.add(existing_note)
    session.commit()

    saved_payloads = []
    monkeypatch.setattr(fanxiu_api, "save_wardrobe_hall", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/inventory/wardrobe-hall",
            json={
                "shizhuang": [
                    {
                        "id": "robe-1",
                        "name": "青纹法袍",
                        "rank": 12,
                        "date": "2026-06-11",
                        "note_id": note_public_id(existing_note),
                    }
                ]
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    assert len(saved_payloads) == 1
    assert saved_payloads[0]["shizhuang"][0]["note_id"] == note_public_id(existing_note)

    session.refresh(existing_note)
    assert existing_note.title == "青纹法袍"
    assert existing_note.content == "<p>保留内容</p>"
    assert existing_note.weight == 12
    assert existing_note.start_at == fanxiu_api.wardrobe_item_date_to_timestamp("2026-06-11")


def test_fanxiu_wardrobe_hall_put_removes_stale_note_reference(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    saved_payloads = []
    monkeypatch.setattr(fanxiu_api, "save_wardrobe_hall", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/inventory/wardrobe-hall",
            json={
                "shizhuang": [
                    {
                        "id": "robe-1",
                        "name": "青纹法袍",
                        "rank": 12,
                        "date": "2026-06-11",
                        "note_id": "missing-note-id",
                    }
                ]
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    assert len(saved_payloads) == 1
    assert "note_id" not in saved_payloads[0]["shizhuang"][0]
    assert response.json()["shizhuang"][0]["note_id"] is None


def test_fanxiu_spirit_beast_note_put_creates_note_and_persists_note_id(client, session, monkeypatch):
    spirit_beast_hall = {
        "beasts": [
            {
                "id": "beast-1",
                "name": "玄霜灵鹿",
                "rank": 5,
                "date": "2026-06-03",
            }
        ]
    }
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_spirit_beast_hall", lambda: spirit_beast_hall)
    monkeypatch.setattr(fanxiu_api, "save_spirit_beast_hall", lambda payload: saved_payloads.append(payload) or payload)

    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    _override_user(fanxiu_user)

    try:
        response = client.put(
            "/api/fanxiu/inventory/spirit-beast-notes/beast-1",
            json={
                "content": "<p>速度资质优秀</p>",
                "note_types": [{"key": fanxiu_api.FANXIU_SPIRIT_BEAST_TYPE, "weight": 100}],
                "node_status": "active",
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "玄霜灵鹿"
    assert payload["content"] == "<p>速度资质优秀</p>"
    assert payload["weight"] == 5
    assert payload["note_kind"] == fanxiu_api.FANXIU_SPIRIT_BEAST_KIND
    assert payload["weight_mode"] == NOTE_WEIGHT_MODE_LINEAR

    created_note = session.exec(select(NoteNode).where(NoteNode.title == "玄霜灵鹿")).one()
    assert created_note.user_id == fanxiu_user.id
    assert created_note.content == "<p>速度资质优秀</p>"
    assert created_note.weight == 5
    assert created_note.note_kind == fanxiu_api.FANXIU_SPIRIT_BEAST_KIND
    assert created_note.node_type == fanxiu_api.FANXIU_SPIRIT_BEAST_TYPE
    assert created_note.note_types == [{"key": fanxiu_api.FANXIU_SPIRIT_BEAST_TYPE, "weight": 100}]
    assert created_note.node_status == "active"
    assert created_note.custom_fields == []

    assert len(saved_payloads) == 1
    assert spirit_beast_hall["beasts"][0]["note_id"] == note_public_id(created_note)
    assert payload["id"] == created_note.numeric_id


def test_fanxiu_spirit_beast_note_put_updates_existing_note_and_keeps_identity(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    existing_note = NoteNode(
        id="spirit-beast-note-legacy",
        numeric_id=32001,
        legacy_id="spirit-beast-note-legacy",
        user_id=fanxiu_user.id,
        title="旧灵兽",
        content="<p>旧描述</p>",
        weight=1,
        node_type="memo",
        note_types=[],
        note_kind=fanxiu_api.FANXIU_SPIRIT_BEAST_KIND,
        node_status="idea",
        weight_mode=None,
        custom_fields={},
        history=[],
        start_at=100.0,
        created_at=100.0,
        updated_at=100.0,
    )
    session.add(existing_note)
    session.commit()

    spirit_beast_hall = {
        "beasts": [
            {
                "id": "beast-1",
                "name": "玄霜灵鹿",
                "rank": 8,
                "date": "2026-06-04",
                "note_id": note_public_id(existing_note),
            }
        ]
    }
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_spirit_beast_hall", lambda: spirit_beast_hall)
    monkeypatch.setattr(fanxiu_api, "save_spirit_beast_hall", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/inventory/spirit-beast-notes/beast-1",
            json={
                "content": "<p>更新灵兽描述</p>",
                "node_status": "done",
                "custom_fields": [["速度", "number", 88]],
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == existing_note.numeric_id
    assert payload["title"] == "玄霜灵鹿"
    assert payload["content"] == "<p>更新灵兽描述</p>"
    assert payload["weight"] == 8
    assert payload["note_kind"] == fanxiu_api.FANXIU_SPIRIT_BEAST_KIND
    assert payload["node_status"] == "done"
    assert payload["custom_fields"] == [["速度", "number", 88]]

    session.refresh(existing_note)
    assert existing_note.title == "玄霜灵鹿"
    assert existing_note.content == "<p>更新灵兽描述</p>"
    assert existing_note.weight == 8
    assert existing_note.note_kind == fanxiu_api.FANXIU_SPIRIT_BEAST_KIND
    assert existing_note.weight_mode == NOTE_WEIGHT_MODE_LINEAR
    assert existing_note.node_type == fanxiu_api.FANXIU_SPIRIT_BEAST_TYPE
    assert existing_note.note_types == [{"key": fanxiu_api.FANXIU_SPIRIT_BEAST_TYPE, "weight": 100}]
    assert existing_note.node_status == "done"
    assert existing_note.custom_fields == [["速度", "number", 88]]

    assert len(session.exec(select(NoteNode).where(NoteNode.title == "玄霜灵鹿")).all()) == 1
    assert len(saved_payloads) == 1
    assert spirit_beast_hall["beasts"][0]["note_id"] == note_public_id(existing_note)


def test_fanxiu_magic_treasure_note_put_creates_note_and_persists_note_id(client, session, monkeypatch):
    magic_treasure_hall = {
        "treasures": [
            {
                "id": "treasure-1",
                "name": "青玉灵珠",
                "rank": 6,
                "date": "2026-06-05",
            }
        ]
    }
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_magic_treasure_hall", lambda: magic_treasure_hall)
    monkeypatch.setattr(fanxiu_api, "save_magic_treasure_hall", lambda payload: saved_payloads.append(payload) or payload)

    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    _override_user(fanxiu_user)

    try:
        response = client.put(
            "/api/fanxiu/inventory/magic-treasure-notes/treasure-1",
            json={
                "content": "<p>适合主力输出</p>",
                "note_types": [{"key": fanxiu_api.FANXIU_MAGIC_TREASURE_TYPE, "weight": 100}],
                "node_status": "active",
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "青玉灵珠"
    assert payload["content"] == "<p>适合主力输出</p>"
    assert payload["weight"] == 6
    assert payload["note_kind"] == fanxiu_api.FANXIU_MAGIC_TREASURE_KIND
    assert payload["weight_mode"] == NOTE_WEIGHT_MODE_LINEAR

    created_note = session.exec(select(NoteNode).where(NoteNode.title == "青玉灵珠")).one()
    assert created_note.user_id == fanxiu_user.id
    assert created_note.content == "<p>适合主力输出</p>"
    assert created_note.weight == 6
    assert created_note.note_kind == fanxiu_api.FANXIU_MAGIC_TREASURE_KIND
    assert created_note.node_type == fanxiu_api.FANXIU_MAGIC_TREASURE_TYPE
    assert created_note.note_types == [{"key": fanxiu_api.FANXIU_MAGIC_TREASURE_TYPE, "weight": 100}]
    assert created_note.node_status == "active"
    assert created_note.custom_fields == []

    assert len(saved_payloads) == 1
    assert magic_treasure_hall["treasures"][0]["note_id"] == note_public_id(created_note)
    assert payload["id"] == created_note.numeric_id


def test_fanxiu_magic_treasure_note_put_updates_existing_note_and_keeps_identity(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    existing_note = NoteNode(
        id="magic-treasure-note-legacy",
        numeric_id=33001,
        legacy_id="magic-treasure-note-legacy",
        user_id=fanxiu_user.id,
        title="旧法宝",
        content="<p>旧法宝描述</p>",
        weight=1,
        node_type="memo",
        note_types=[],
        note_kind=fanxiu_api.FANXIU_MAGIC_TREASURE_KIND,
        node_status="idea",
        weight_mode=None,
        custom_fields={},
        history=[],
        start_at=100.0,
        created_at=100.0,
        updated_at=100.0,
    )
    session.add(existing_note)
    session.commit()

    magic_treasure_hall = {
        "treasures": [
            {
                "id": "treasure-1",
                "name": "青玉灵珠",
                "rank": 10,
                "date": "2026-06-06",
                "note_id": note_public_id(existing_note),
            }
        ]
    }
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_magic_treasure_hall", lambda: magic_treasure_hall)
    monkeypatch.setattr(fanxiu_api, "save_magic_treasure_hall", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/inventory/magic-treasure-notes/treasure-1",
            json={
                "content": "<p>更新法宝描述</p>",
                "node_status": "done",
                "custom_fields": [["附伤", "number", 60000]],
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == existing_note.numeric_id
    assert payload["title"] == "青玉灵珠"
    assert payload["content"] == "<p>更新法宝描述</p>"
    assert payload["weight"] == 10
    assert payload["note_kind"] == fanxiu_api.FANXIU_MAGIC_TREASURE_KIND
    assert payload["node_status"] == "done"
    assert payload["custom_fields"] == [["附伤", "number", 60000]]

    session.refresh(existing_note)
    assert existing_note.title == "青玉灵珠"
    assert existing_note.content == "<p>更新法宝描述</p>"
    assert existing_note.weight == 10
    assert existing_note.note_kind == fanxiu_api.FANXIU_MAGIC_TREASURE_KIND
    assert existing_note.weight_mode == NOTE_WEIGHT_MODE_LINEAR
    assert existing_note.node_type == fanxiu_api.FANXIU_MAGIC_TREASURE_TYPE
    assert existing_note.note_types == [{"key": fanxiu_api.FANXIU_MAGIC_TREASURE_TYPE, "weight": 100}]
    assert existing_note.node_status == "done"
    assert existing_note.custom_fields == [["附伤", "number", 60000]]

    assert len(session.exec(select(NoteNode).where(NoteNode.title == "青玉灵珠")).all()) == 1
    assert len(saved_payloads) == 1
    assert magic_treasure_hall["treasures"][0]["note_id"] == note_public_id(existing_note)


def test_fanxiu_activity_note_put_creates_note_and_persists_note_id(client, session, monkeypatch):
    activity_list = [
        {
            "id": "activity-1",
            "name": "丹道问鼎",
            "start_date": "2026-06-07",
            "end_date": "2026-06-08",
        }
    ]
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_activity_list", lambda: activity_list)
    monkeypatch.setattr(fanxiu_api, "save_activity_list", lambda payload: saved_payloads.append(payload) or payload)

    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    _override_user(fanxiu_user)

    try:
        response = client.put(
            "/api/fanxiu/activity-notes/activity-1",
            json={
                "content": "<p>活动攻略记录</p>",
                "note_types": [{"key": fanxiu_api.FANXIU_ACTIVITY_TYPE, "weight": 100}],
                "node_status": "active",
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "丹道问鼎"
    assert payload["content"] == "<p>活动攻略记录</p>"
    assert payload["weight"] == 0
    assert payload["note_kind"] == fanxiu_api.FANXIU_ACTIVITY_KIND
    assert payload["weight_mode"] == NOTE_WEIGHT_MODE_LINEAR

    created_note = session.exec(select(NoteNode).where(NoteNode.title == "丹道问鼎")).one()
    assert created_note.user_id == fanxiu_user.id
    assert created_note.content == "<p>活动攻略记录</p>"
    assert created_note.weight == 0
    assert created_note.start_at == fanxiu_api.activity_item_start_to_timestamp("2026-06-07")
    assert created_note.note_kind == fanxiu_api.FANXIU_ACTIVITY_KIND
    assert created_note.node_type == fanxiu_api.FANXIU_ACTIVITY_TYPE
    assert created_note.note_types == [{"key": fanxiu_api.FANXIU_ACTIVITY_TYPE, "weight": 100}]
    assert created_note.node_status == "active"
    assert created_note.custom_fields == []

    assert len(saved_payloads) == 1
    assert activity_list[0]["note_id"] == note_public_id(created_note)
    assert payload["id"] == created_note.numeric_id


def test_fanxiu_activity_note_put_updates_existing_note_and_keeps_identity(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    existing_note = NoteNode(
        id="activity-note-legacy",
        numeric_id=34001,
        legacy_id="activity-note-legacy",
        user_id=fanxiu_user.id,
        title="旧活动",
        content="<p>旧活动描述</p>",
        weight=3,
        node_type="memo",
        note_types=[],
        note_kind=fanxiu_api.FANXIU_ACTIVITY_KIND,
        node_status="idea",
        weight_mode=None,
        custom_fields={},
        history=[],
        start_at=100.0,
        created_at=100.0,
        updated_at=100.0,
    )
    session.add(existing_note)
    session.commit()

    activity_list = [
        {
            "id": "activity-1",
            "name": "丹道问鼎",
            "start_date": "2026-06-09",
            "end_date": "2026-06-10",
            "note_id": note_public_id(existing_note),
        }
    ]
    saved_payloads = []

    monkeypatch.setattr(fanxiu_api, "load_activity_list", lambda: activity_list)
    monkeypatch.setattr(fanxiu_api, "save_activity_list", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/activity-notes/activity-1",
            json={
                "content": "<p>更新活动描述</p>",
                "node_status": "done",
                "custom_fields": [["跨服数", "number", 8]],
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == existing_note.numeric_id
    assert payload["title"] == "丹道问鼎"
    assert payload["content"] == "<p>更新活动描述</p>"
    assert payload["weight"] == 3
    assert payload["note_kind"] == fanxiu_api.FANXIU_ACTIVITY_KIND
    assert payload["node_status"] == "done"
    assert payload["custom_fields"] == [["跨服数", "number", 8]]

    session.refresh(existing_note)
    assert existing_note.title == "丹道问鼎"
    assert existing_note.content == "<p>更新活动描述</p>"
    assert existing_note.weight == 3
    assert existing_note.start_at == fanxiu_api.activity_item_start_to_timestamp("2026-06-09")
    assert existing_note.note_kind == fanxiu_api.FANXIU_ACTIVITY_KIND
    assert existing_note.weight_mode == NOTE_WEIGHT_MODE_LINEAR
    assert existing_note.node_type == fanxiu_api.FANXIU_ACTIVITY_TYPE
    assert existing_note.note_types == [{"key": fanxiu_api.FANXIU_ACTIVITY_TYPE, "weight": 100}]
    assert existing_note.node_status == "done"
    assert existing_note.custom_fields == [["跨服数", "number", 8]]

    assert len(session.exec(select(NoteNode).where(NoteNode.title == "丹道问鼎")).all()) == 1
    assert len(saved_payloads) == 1
    assert activity_list[0]["note_id"] == note_public_id(existing_note)


def test_fanxiu_activity_list_put_syncs_existing_note_reference(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    existing_note = NoteNode(
        id="activity-list-note",
        numeric_id=36001,
        legacy_id="activity-list-note",
        user_id=fanxiu_user.id,
        title="旧活动名",
        content="<p>保留活动内容</p>",
        weight=7,
        node_type=fanxiu_api.FANXIU_ACTIVITY_TYPE,
        note_types=[{"key": fanxiu_api.FANXIU_ACTIVITY_TYPE, "weight": 100}],
        note_kind=fanxiu_api.FANXIU_ACTIVITY_KIND,
        node_status="active",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        custom_fields=[],
        history=[],
        start_at=100.0,
        created_at=100.0,
        updated_at=100.0,
    )
    session.add(existing_note)
    session.commit()

    saved_payloads = []
    monkeypatch.setattr(fanxiu_api, "save_activity_list", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/activity-list",
            json={
                "items": [
                    {
                        "id": "activity-1",
                        "name": "丹道问鼎",
                        "start_date": "2026-06-12",
                        "end_date": "2026-06-13",
                        "note_id": note_public_id(existing_note),
                    }
                ]
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    assert len(saved_payloads) == 1
    assert saved_payloads[0][0]["note_id"] == note_public_id(existing_note)

    session.refresh(existing_note)
    assert existing_note.title == "丹道问鼎"
    assert existing_note.content == "<p>保留活动内容</p>"
    assert existing_note.weight == 7
    assert existing_note.start_at == fanxiu_api.activity_item_start_to_timestamp("2026-06-12")


def test_fanxiu_activity_list_put_removes_stale_note_reference(client, session, monkeypatch):
    fanxiu_user = fanxiu_api.get_fanxiu_user(session)
    saved_payloads = []
    monkeypatch.setattr(fanxiu_api, "save_activity_list", lambda payload: saved_payloads.append(payload) or payload)

    _override_user(fanxiu_user)
    try:
        response = client.put(
            "/api/fanxiu/activity-list",
            json={
                "items": [
                    {
                        "id": "activity-1",
                        "name": "丹道问鼎",
                        "start_date": "2026-06-12",
                        "end_date": "2026-06-13",
                        "note_id": "missing-note-id",
                    }
                ]
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    assert len(saved_payloads) == 1
    assert "note_id" not in saved_payloads[0][0]
    assert response.json()["items"][0]["note_id"] is None
