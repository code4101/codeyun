from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from backend.db_cleanup import cleanup_legacy_sqlite_artifacts


def test_cleanup_legacy_sqlite_artifacts_drops_legacy_tables_when_replacements_exist():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE userdevice (device_id TEXT)")
        conn.exec_driver_sql("CREATE TABLE userdeviceentry (entry_id TEXT)")
        conn.exec_driver_sql("CREATE TABLE dbversion (version INTEGER)")
        conn.exec_driver_sql("CREATE TABLE system_version (version INTEGER)")
        conn.exec_driver_sql("CREATE VIEW userdevice_readable AS SELECT * FROM userdevice")
        conn.exec_driver_sql("CREATE VIEW dbversion_readable AS SELECT * FROM dbversion")

    cleanup_legacy_sqlite_artifacts(engine)

    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            """
            SELECT name, type
            FROM sqlite_master
            WHERE name IN (
                'userdevice', 'userdeviceentry',
                'dbversion', 'system_version',
                'userdevice_readable', 'dbversion_readable'
            )
            ORDER BY type, name
            """
        ).fetchall()

    assert ("system_version", "table") in rows
    assert ("userdeviceentry", "table") in rows
    assert ("dbversion", "table") not in rows
    assert ("userdevice", "table") not in rows
    assert ("dbversion_readable", "view") not in rows
    assert ("userdevice_readable", "view") not in rows


def test_cleanup_legacy_sqlite_artifacts_keeps_legacy_tables_without_replacements():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE userdevice (device_id TEXT)")
        conn.exec_driver_sql("CREATE TABLE dbversion (version INTEGER)")

    cleanup_legacy_sqlite_artifacts(engine)

    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            """
            SELECT name, type
            FROM sqlite_master
            WHERE name IN ('userdevice', 'dbversion')
            ORDER BY type, name
            """
        ).fetchall()

    assert ("dbversion", "table") in rows
    assert ("userdevice", "table") in rows
