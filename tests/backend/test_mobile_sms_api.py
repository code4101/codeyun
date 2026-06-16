from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.api.mobile_sms import router
from backend.core.access.auth import get_current_active_user
from backend.core.access.service_tokens import SERVICE_SCOPE_MOBILE_SMS_UPLOAD, create_service_access_token
from backend.db import get_session
from backend.models import MobileSmsMessage, User


def _build_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _build_client(session: Session) -> TestClient:
    app = FastAPI()

    def override_get_session():
        yield session

    def override_current_user():
        return User(id=1, username="tester", hashed_password="", is_active=True)

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_active_user] = override_current_user
    app.include_router(router, prefix="/api/mobile-sms")
    return TestClient(app)


def test_upload_mobile_sms_batch_upserts_inbox_only():
    session = _build_session()
    client = _build_client(session)
    token = create_service_access_token(
        session,
        label="sms test",
        scopes=[SERVICE_SCOPE_MOBILE_SMS_UPLOAD],
    )["plaintext_value"]

    payload = {
        "device_id": "xiaomi-main",
        "items": [
            {
                "sms_id": "1",
                "address": "95588",
                "body": "hello",
                "date_ms": 1000,
                "message_type": "inbox",
                "subscription_id": 7,
                "sim_slot_index": 1,
                "sim_display_name": "SIM 2",
            },
            {
                "sms_id": "2",
                "address": "10086",
                "body": "outbox should skip",
                "date_ms": 1001,
                "message_type": "sent",
            },
        ],
    }
    response = client.post(
        "/api/mobile-sms/batch",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    assert response.json()["skipped"] == 1
    rows = session.exec(select(MobileSmsMessage)).all()
    assert len(rows) == 1
    assert rows[0].sim_slot_index == 1

    payload["items"][0]["body"] = "updated"
    response = client.post(
        "/api/mobile-sms/batch",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["inserted"] == 0
    assert response.json()["updated"] == 1
    row = session.exec(select(MobileSmsMessage)).one()
    assert row.body == "updated"


def test_upload_mobile_sms_batch_requires_service_token():
    session = _build_session()
    client = _build_client(session)

    response = client.post(
        "/api/mobile-sms/batch",
        json={"device_id": "xiaomi-main", "items": [{"sms_id": "1", "message_type": "inbox"}]},
    )

    assert response.status_code == 401
    assert session.exec(select(MobileSmsMessage)).all() == []


def test_ping_mobile_sms_upload_requires_service_token():
    session = _build_session()
    client = _build_client(session)
    token = create_service_access_token(
        session,
        label="sms test",
        scopes=[SERVICE_SCOPE_MOBILE_SMS_UPLOAD],
    )["plaintext_value"]

    response = client.get("/api/mobile-sms/ping", params={"device_id": "xiaomi-main"})

    assert response.status_code == 401

    response = client.get(
        "/api/mobile-sms/ping",
        params={"device_id": "xiaomi-main"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "device_id": "xiaomi-main",
        "scope": SERVICE_SCOPE_MOBILE_SMS_UPLOAD,
    }


def test_list_and_stats_mobile_sms_messages_for_frontend():
    session = _build_session()
    client = _build_client(session)
    token = create_service_access_token(
        session,
        label="sms test",
        scopes=[SERVICE_SCOPE_MOBILE_SMS_UPLOAD],
    )["plaintext_value"]

    response = client.post(
        "/api/mobile-sms/batch",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": "xiaomi-main",
            "items": [
                {"sms_id": "1", "address": "95588", "body": "bank notice", "date_ms": 1000},
                {"sms_id": "2", "address": "10086", "body": "mobile notice", "date_ms": 2000},
            ],
        },
    )
    assert response.status_code == 200

    list_response = client.get("/api/mobile-sms/messages", params={"keyword": "mobile", "page_size": 20})
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] == 1
    assert list_payload["items"][0]["sms_id"] == "2"

    stats_response = client.get("/api/mobile-sms/stats")
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    assert stats_payload["total"] == 2
    assert stats_payload["latest"]["sms_id"] == "2"
    assert stats_payload["devices"] == [{"device_id": "xiaomi-main", "count": 2}]
