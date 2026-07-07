from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from backend.maintenance import db_views


class _DummyUrl:
    @staticmethod
    def get_backend_name() -> str:
        return "sqlite"


class _DummyEngine:
    url = _DummyUrl()


def _sqlite_lock_error() -> OperationalError:
    return OperationalError("DROP VIEW", {}, Exception("database is locked"))


def test_refresh_sqlite_readable_views_retries_transient_lock_then_succeeds(monkeypatch):
    engine = _DummyEngine()
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_refresh_once(_engine):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _sqlite_lock_error()

    monkeypatch.setattr(db_views, "_refresh_sqlite_readable_views_once", fake_refresh_once)
    monkeypatch.setattr(db_views.time, "sleep", sleeps.append)

    db_views.refresh_sqlite_readable_views(engine)

    assert attempts["count"] == 3
    assert sleeps == [0.2, 0.5]


def test_refresh_sqlite_readable_views_skips_after_retry_budget(monkeypatch, capsys):
    engine = _DummyEngine()
    sleeps: list[float] = []

    monkeypatch.setattr(
        db_views,
        "_refresh_sqlite_readable_views_once",
        lambda _engine: (_ for _ in ()).throw(_sqlite_lock_error()),
    )
    monkeypatch.setattr(db_views.time, "sleep", sleeps.append)

    db_views.refresh_sqlite_readable_views(engine)

    assert sleeps == list(db_views.READABLE_VIEW_LOCK_RETRY_DELAYS_SECONDS)
    assert "Skipping sqlite readable view refresh" in capsys.readouterr().out


def test_refresh_sqlite_readable_views_raises_non_lock_errors(monkeypatch):
    engine = _DummyEngine()

    monkeypatch.setattr(
        db_views,
        "_refresh_sqlite_readable_views_once",
        lambda _engine: (_ for _ in ()).throw(
            OperationalError("DROP VIEW", {}, Exception("no such table"))
        ),
    )

    with pytest.raises(OperationalError):
        db_views.refresh_sqlite_readable_views(engine)
