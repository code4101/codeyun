from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import backend.app as app_module


def test_development_backend_restart_api_queues_request(monkeypatch):
    monkeypatch.setattr(app_module, "settings", SimpleNamespace(is_development=True))
    monkeypatch.setattr(
        app_module,
        "request_backend_restart",
        lambda **kwargs: {"request_id": "request-1", **kwargs},
    )

    response = asyncio.run(app_module.restart_development_backend())

    assert response == {
        "status": "accepted",
        "request_id": "request-1",
        "message": "Backend restart requested",
    }


def test_backend_restart_api_is_unavailable_outside_development(monkeypatch):
    monkeypatch.setattr(app_module, "settings", SimpleNamespace(is_development=False))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(app_module.restart_development_backend())

    assert exc_info.value.status_code == 404
