from __future__ import annotations

from sqlalchemy import text


LEGACY_TABLE_REPLACEMENTS = {
    "userdevice": "userdeviceentry",
    "dbversion": "system_version",
}


def cleanup_legacy_sqlite_artifacts(engine) -> None:
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.begin() as conn:
        existing_objects = {
            (name, obj_type)
            for name, obj_type in conn.execute(
                text(
                    """
                    SELECT name, type
                    FROM sqlite_master
                    WHERE type IN ('table', 'view')
                    """
                )
            ).fetchall()
        }

        for legacy_table, replacement_table in LEGACY_TABLE_REPLACEMENTS.items():
            if (replacement_table, "table") not in existing_objects:
                continue
            conn.exec_driver_sql(f'DROP VIEW IF EXISTS "{legacy_table}_readable"')
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{legacy_table}"')
