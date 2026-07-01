from types import SimpleNamespace

from backend import db as db_module


def test_should_run_startup_migrations_defaults_off_in_test(monkeypatch):
    monkeypatch.delenv("CODEYUN_RUN_STARTUP_MIGRATIONS", raising=False)
    monkeypatch.setattr(db_module, "settings", SimpleNamespace(is_test=True))

    assert db_module._should_run_startup_migrations() is False


def test_should_run_startup_migrations_can_be_forced_on_in_test(monkeypatch):
    monkeypatch.setenv("CODEYUN_RUN_STARTUP_MIGRATIONS", "1")
    monkeypatch.setattr(db_module, "settings", SimpleNamespace(is_test=True))

    assert db_module._should_run_startup_migrations() is True


def test_migrate_db_skips_manager_when_disabled(monkeypatch):
    monkeypatch.setattr(db_module, "_should_run_startup_migrations", lambda: False)
    monkeypatch.setattr(
        db_module,
        "run_startup_schema_repairs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run schema repairs")),
    )
    monkeypatch.setattr(
        db_module,
        "migrate_db_manager",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run migration manager")),
    )

    db_module.migrate_db()
