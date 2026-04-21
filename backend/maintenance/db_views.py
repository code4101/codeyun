from __future__ import annotations

from sqlalchemy import text


READABLE_VIEW_SUFFIX = "_readable"
TIME_TEXT_SUFFIX = "_local_text"
LEGACY_VIEW_EXCLUDE_TABLES = {"userdevice", "dbversion"}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_time_like_column(column_name: str, column_type: str | None) -> bool:
    normalized_type = (column_type or "").upper()
    numeric_markers = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")
    return column_name.endswith("_at") and any(marker in normalized_type for marker in numeric_markers)


def refresh_sqlite_readable_views(engine) -> None:
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.begin() as conn:
        table_rows = conn.execute(
            text(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ).fetchall()

        for (table_name,) in table_rows:
            if table_name in LEGACY_VIEW_EXCLUDE_TABLES:
                continue
            pragma_sql = f"PRAGMA table_info({_quote_ident(table_name)})"
            columns = conn.exec_driver_sql(pragma_sql).fetchall()
            time_columns = [
                column_name
                for _, column_name, column_type, *_ in columns
                if _is_time_like_column(column_name, column_type)
            ]
            if not time_columns:
                continue

            view_name = f"{table_name}{READABLE_VIEW_SUFFIX}"
            conn.exec_driver_sql(f"DROP VIEW IF EXISTS {_quote_ident(view_name)}")

            computed_columns = []
            for column_name in time_columns:
                quoted_column = _quote_ident(column_name)
                alias_name = _quote_ident(f"{column_name}{TIME_TEXT_SUFFIX}")
                computed_columns.append(
                    "CASE "
                    f"WHEN base.{quoted_column} IS NULL THEN NULL "
                    "ELSE strftime('%Y-%m-%d %H:%M:%f', "
                    f"base.{quoted_column}, 'unixepoch', 'localtime') "
                    f"END AS {alias_name}"
                )

            view_sql = (
                f"CREATE VIEW {_quote_ident(view_name)} AS "
                f"SELECT base.*, {', '.join(computed_columns)} "
                f"FROM {_quote_ident(table_name)} AS base"
            )
            conn.exec_driver_sql(view_sql)
