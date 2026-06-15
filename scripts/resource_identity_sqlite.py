from __future__ import annotations

import mimetypes
import os
import sqlite3
import time
from typing import Any


GLOBAL_NUMERIC_TABLES = ("sheetdocument", "pdfdocument", "documentasset", "notenode", "devicefile")
RESOURCE_NUMERIC_TABLES = {
    "sheet": "sheetdocument",
    "pdf": "pdfdocument",
    "document_asset": "documentasset",
    "note": "notenode",
    "device_file": "devicefile",
}


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    return (
        con.execute(
            "select 1 from sqlite_master where type='table' and name=? limit 1",
            (table_name,),
        ).fetchone()
        is not None
    )


def column_exists(con: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not table_exists(con, table_name):
        return False
    return any(row[1] == column_name for row in con.execute(f"pragma table_info({table_name})"))


def ensure_resource_identity_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        create table if not exists resourceidentity (
            id integer not null primary key,
            resource_type varchar not null,
            legacy_pk varchar not null,
            created_at float not null,
            updated_at float not null
        )
        """
    )
    con.execute(
        "create unique index if not exists ux_resourceidentity_type_legacy "
        "on resourceidentity (resource_type, legacy_pk)"
    )


def _scalar_int(row: Any) -> int:
    try:
        return int((row[0] if row is not None else 0) or 0)
    except (TypeError, ValueError):
        return 0


def _table_numeric_max(con: sqlite3.Connection, table_name: str) -> int:
    if not column_exists(con, table_name, "numeric_id"):
        return 0
    return max(_scalar_int(con.execute(f"select coalesce(max(numeric_id), 0) from {table_name}").fetchone()), 0)


def current_global_resource_id_max(con: sqlite3.Connection) -> int:
    ensure_resource_identity_table(con)
    identity_max = _scalar_int(con.execute("select coalesce(max(id), 0) from resourceidentity").fetchone())
    return max(identity_max, *(_table_numeric_max(con, table_name) for table_name in GLOBAL_NUMERIC_TABLES))


def resource_id_is_available(
    con: sqlite3.Connection,
    resource_id: int,
    *,
    resource_type: str | None = None,
    legacy_pk: str | None = None,
) -> bool:
    ensure_resource_identity_table(con)
    normalized = int(resource_id)
    if con.execute("select 1 from resourceidentity where id=? limit 1", (normalized,)).fetchone():
        return False
    owner_table = RESOURCE_NUMERIC_TABLES.get(str(resource_type or "").strip())
    normalized_legacy_pk = str(legacy_pk or "").strip()
    for table_name in GLOBAL_NUMERIC_TABLES:
        if not column_exists(con, table_name, "numeric_id"):
            continue
        legacy_pk_expr = (
            "coalesce(nullif(legacy_id, ''), cast(id as text))"
            if column_exists(con, table_name, "legacy_id")
            else "cast(id as text)"
        )
        row = con.execute(
            f"select id, {legacy_pk_expr} as legacy_pk from {table_name} where numeric_id=? limit 1",
            (normalized,),
        ).fetchone()
        if row is not None:
            row_legacy_pk = row["legacy_pk"] if isinstance(row, sqlite3.Row) else row[1]
            if table_name == owner_table and str(row_legacy_pk or "").strip() == normalized_legacy_pk:
                continue
            return False
    return True


def allocate_resource_id(
    con: sqlite3.Connection,
    resource_type: str,
    legacy_pk: Any,
    *,
    preferred_id: int | None = None,
) -> int:
    ensure_resource_identity_table(con)
    normalized_type = str(resource_type or "").strip()
    normalized_legacy_pk = str(legacy_pk or "").strip()
    if not normalized_type:
        raise ValueError("resource_type is required")
    if not normalized_legacy_pk:
        raise ValueError("legacy_pk is required")

    existing = con.execute(
        "select id from resourceidentity where resource_type=? and legacy_pk=? limit 1",
        (normalized_type, normalized_legacy_pk),
    ).fetchone()
    if existing is not None:
        return int(existing[0])

    resource_id = int(preferred_id or 0)
    if resource_id <= 0 or not resource_id_is_available(
        con,
        resource_id,
        resource_type=normalized_type,
        legacy_pk=normalized_legacy_pk,
    ):
        resource_id = current_global_resource_id_max(con) + 1

    now = time.time()
    con.execute(
        "insert into resourceidentity(id,resource_type,legacy_pk,created_at,updated_at) values (?,?,?,?,?)",
        (resource_id, normalized_type, normalized_legacy_pk, now, now),
    )
    return resource_id


def allocate_note_numeric_id(
    con: sqlite3.Connection,
    note_id: str,
    *,
    preferred_id: int | None = None,
) -> int:
    return allocate_resource_id(con, "note", note_id, preferred_id=preferred_id)


def resolve_local_device_id() -> str:
    try:
        from backend.core.devices.device import get_device_id

        device_id = str(get_device_id() or "").strip()
        if device_id:
            return device_id
    except Exception:
        pass
    return "local"


def media_kind_from_mime(mime_type: str | None) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    if normalized == "application/pdf":
        return "pdf"
    return "attachment"


def ensure_device_file_resource(
    con: sqlite3.Connection,
    file_path: Any,
    *,
    device_id: str | None = None,
    media_kind: str | None = None,
    mime_type: str | None = None,
) -> int | None:
    if not table_exists(con, "devicefile"):
        return None

    absolute_path = os.path.abspath(os.fspath(file_path))
    if not os.path.isfile(absolute_path):
        return None

    normalized_device_id = str(device_id or "").strip() or resolve_local_device_id()
    resolved_mime_type = mime_type or mimetypes.guess_type(absolute_path)[0] or "application/octet-stream"
    resolved_media_kind = media_kind or media_kind_from_mime(resolved_mime_type)
    stat_result = os.stat(absolute_path)
    modified_at_ms = int(stat_result.st_mtime * 1000)
    now = time.time()

    row = con.execute(
        "select id,numeric_id from devicefile where device_id=? and absolute_path=? limit 1",
        (normalized_device_id, absolute_path),
    ).fetchone()
    if row is None:
        cursor = con.execute(
            """
            insert into devicefile(
                device_id,absolute_path,last_known_path,file_size,modified_at_ms,
                media_kind,mime_type,match_status,created_at,updated_at,last_seen_at
            ) values (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                normalized_device_id,
                absolute_path,
                absolute_path,
                stat_result.st_size,
                modified_at_ms,
                resolved_media_kind,
                resolved_mime_type,
                "matched",
                now,
                now,
                now,
            ),
        )
        row_id = int(cursor.lastrowid or 0)
        existing_numeric_id = 0
    else:
        row_id = int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
        existing_numeric_id = int((row["numeric_id"] if isinstance(row, sqlite3.Row) else row[1]) or 0)
        con.execute(
            """
            update devicefile
            set last_known_path=?,file_size=?,modified_at_ms=?,media_kind=?,mime_type=?,
                match_status='matched',updated_at=?,last_seen_at=?
            where id=?
            """,
            (
                absolute_path,
                stat_result.st_size,
                modified_at_ms,
                resolved_media_kind,
                resolved_mime_type,
                now,
                now,
                row_id,
            ),
        )
    if row_id <= 0:
        return None

    resource_id = allocate_resource_id(
        con,
        "device_file",
        str(row_id),
        preferred_id=existing_numeric_id or row_id,
    )
    con.execute("update devicefile set numeric_id=? where id=?", (resource_id, row_id))
    return resource_id


def ensure_note_numeric_id(con: sqlite3.Connection, note_id: str) -> int | None:
    normalized_id = str(note_id or "").strip()
    if not normalized_id or not table_exists(con, "notenode"):
        return None
    row = con.execute("select numeric_id from notenode where id=? limit 1", (normalized_id,)).fetchone()
    if row is None:
        return None
    numeric_id = int(row[0] or 0)
    if numeric_id <= 0:
        numeric_id = allocate_note_numeric_id(con, normalized_id)
        con.execute("update notenode set numeric_id=? where id=?", (numeric_id, normalized_id))
    else:
        allocate_note_numeric_id(con, normalized_id, preferred_id=numeric_id)
    return numeric_id


def note_public_ref(con: sqlite3.Connection, note_ref: Any) -> str:
    normalized_ref = str(note_ref or "").strip()
    if not normalized_ref:
        return ""
    if normalized_ref.isdecimal():
        return normalized_ref
    numeric_id = ensure_note_numeric_id(con, normalized_ref)
    return str(numeric_id) if numeric_id and numeric_id > 0 else normalized_ref


def insert_note_edge(
    con: sqlite3.Connection,
    *,
    user_id: int,
    source_id: Any,
    target_id: Any,
    edge_id: str,
    label: str | None = None,
) -> bool:
    source_ref = note_public_ref(con, source_id)
    target_ref = note_public_ref(con, target_id)
    if not source_ref or not target_ref or source_ref == target_ref:
        return False
    if not source_ref.isdecimal() or not target_ref.isdecimal():
        return False
    exists = con.execute(
        "select 1 from noteedge where user_id=? and source_id=? and target_id=? limit 1",
        (user_id, source_ref, target_ref),
    ).fetchone()
    if exists:
        return False
    con.execute(
        "insert into noteedge(id,user_id,source_id,target_id,label,created_at) values (?,?,?,?,?,?)",
        (edge_id, user_id, source_ref, target_ref, label, time.time()),
    )
    return True


def note_edge_exists(
    con: sqlite3.Connection,
    *,
    user_id: int,
    source_id: Any,
    target_id: Any,
) -> bool:
    source_ref = note_public_ref(con, source_id)
    target_ref = note_public_ref(con, target_id)
    if not source_ref or not target_ref:
        return False
    return (
        con.execute(
            "select 1 from noteedge where user_id=? and source_id=? and target_id=? limit 1",
            (user_id, source_ref, target_ref),
        ).fetchone()
        is not None
    )
