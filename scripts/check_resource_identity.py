from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from sqlmodel import Session, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db import engine


RESOURCE_TABLES = [
    ("sheet", "sheetdocument"),
    ("workbook", "workbookdocument"),
    ("pdf", "pdfdocument"),
    ("document_asset", "documentasset"),
    ("note", "notenode"),
    ("device_file", "devicefile"),
]

INTERNAL_RESOURCE_REFS = [
    ("noteedge", "source_id"),
    ("noteedge", "target_id"),
    ("workbooksheetlink", "workbook_id"),
    ("workbooksheetlink", "sheet_id"),
    ("pdfuserstate", "pdf_document_id"),
    ("pdfpagenote", "pdf_document_id"),
    ("documentreductionrun", "document_id"),
    ("documentqueryhistory", "document_id"),
    ("notemetadatafeedback", "note_id"),
]

REF_TARGETS = {
    ("noteedge", "source_id"): ("notenode", "numeric_id"),
    ("noteedge", "target_id"): ("notenode", "numeric_id"),
    ("workbooksheetlink", "workbook_id"): ("workbookdocument", "numeric_id"),
    ("workbooksheetlink", "sheet_id"): ("sheetdocument", "numeric_id"),
    ("pdfuserstate", "pdf_document_id"): ("pdfdocument", "numeric_id"),
    ("pdfpagenote", "pdf_document_id"): ("pdfdocument", "numeric_id"),
    ("documentreductionrun", "document_id"): ("documentasset", "numeric_id"),
    ("documentqueryhistory", "document_id"): ("documentasset", "numeric_id"),
    ("notemetadatafeedback", "note_id"): ("notenode", "numeric_id"),
}

GRANT_TARGETS = {
    "sheet": ("sheetdocument", "numeric_id"),
    "workbook": ("workbookdocument", "numeric_id"),
    "pdf": ("pdfdocument", "numeric_id"),
    "document_asset": ("documentasset", "numeric_id"),
    "note": ("notenode", "numeric_id"),
    "device_file": ("devicefile", "numeric_id"),
}


def _table_exists(session: Session, table_name: str) -> bool:
    return session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
        {"table_name": table_name},
    ).first() is not None


def _column_exists(session: Session, table_name: str, column_name: str) -> bool:
    if not _table_exists(session, table_name):
        return False
    return any(row[1] == column_name for row in session.execute(text(f"PRAGMA table_info({table_name})")).all())


def _first(value: Any) -> Any:
    mapping = getattr(value, "_mapping", None)
    if mapping is not None:
        values = list(mapping.values())
        return values[0] if values else None
    if isinstance(value, tuple):
        return value[0] if value else None
    try:
        return value[0]
    except (TypeError, KeyError, IndexError):
        pass
    return value


def _resource_table_report(session: Session, resource_type: str, table_name: str) -> dict[str, Any]:
    public_id_scope = "workbook_route_id" if resource_type == "workbook" else "global_resource_id"
    id_column_type = None
    id_is_integer_pk = False
    if not _table_exists(session, table_name):
        return {
            "resource_type": resource_type,
            "table": table_name,
            "public_id_scope": public_id_scope,
            "identity_id_matches_public_id": resource_type != "workbook",
            "id_column_type": id_column_type,
            "id_is_integer_pk": id_is_integer_pk,
            "exists": False,
            "count": 0,
            "missing_numeric_id": 0,
            "missing_identity": 0,
        }
    for column in session.execute(text(f"PRAGMA table_info({table_name})")).all():
        if str(column[1]) == "id":
            id_column_type = str(column[2] or "")
            id_is_integer_pk = int(column[5] or 0) > 0 and "INT" in id_column_type.upper()
            break

    count, missing_numeric_id, min_id, max_id = session.execute(
        text(
            f"""
            SELECT COUNT(*) AS count,
                   SUM(CASE WHEN numeric_id IS NULL THEN 1 ELSE 0 END) AS missing_numeric_id,
                   MIN(numeric_id) AS min_id,
                   MAX(numeric_id) AS max_id
            FROM {table_name}
            """
        )
    ).one()
    legacy_pk_expr = (
        "COALESCE(NULLIF(t.legacy_id, ''), CAST(t.id AS TEXT))"
        if _column_exists(session, table_name, "legacy_id")
        else "CAST(t.id AS TEXT)"
    )
    identity_join = (
        f"r.resource_type = :resource_type AND r.legacy_pk = {legacy_pk_expr}"
        if resource_type == "workbook"
        else f"r.resource_type = :resource_type AND r.legacy_pk = {legacy_pk_expr} AND r.id = t.numeric_id"
    )
    missing_identity = session.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM {table_name} t
            LEFT JOIN resourceidentity r
              ON {identity_join}
            WHERE t.numeric_id IS NOT NULL AND r.id IS NULL
            """
        ),
        {"resource_type": resource_type},
    ).one()[0]
    return {
        "resource_type": resource_type,
        "table": table_name,
        "public_id_scope": public_id_scope,
        "identity_id_matches_public_id": resource_type != "workbook",
        "id_column_type": id_column_type,
        "id_is_integer_pk": id_is_integer_pk,
        "exists": True,
        "count": int(count or 0),
        "missing_numeric_id": int(missing_numeric_id or 0),
        "missing_identity": int(missing_identity or 0),
        "min": int(min_id) if min_id is not None else None,
        "max": int(max_id) if max_id is not None else None,
    }


def _dangling_ref_count(
    session: Session,
    *,
    table_name: str,
    column_name: str,
    target_table: str,
    target_column: str,
) -> int:
    if (
        not _column_exists(session, table_name, column_name)
        or not _column_exists(session, target_table, target_column)
    ):
        return 0
    return int(
        _first(
            session.execute(
                text(
                    f"""
                    SELECT COUNT(*)
                    FROM {table_name} source
                    LEFT JOIN {target_table} target
                      ON CAST(source.{column_name} AS INTEGER) = target.{target_column}
                    WHERE source.{column_name} IS NOT NULL
                      AND source.{column_name} != ''
                      AND source.{column_name} NOT GLOB '*[^0-9]*'
                      AND target.{target_column} IS NULL
                    """
                )
            ).one()
        )
        or 0
    )


def _dangling_grant_report(session: Session) -> list[dict[str, Any]]:
    if not _table_exists(session, "resourceaccessgrant"):
        return []
    reports: list[dict[str, Any]] = []
    for resource_type, (target_table, target_column) in GRANT_TARGETS.items():
        if not _column_exists(session, target_table, target_column):
            continue
        dangling = int(
            _first(
                session.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM resourceaccessgrant grant_row
                        LEFT JOIN {target_table} target
                          ON CAST(grant_row.resource_id AS INTEGER) = target.{target_column}
                        WHERE grant_row.resource_type = :resource_type
                          AND grant_row.resource_id IS NOT NULL
                          AND grant_row.resource_id != ''
                          AND grant_row.resource_id NOT GLOB '*[^0-9]*'
                          AND target.{target_column} IS NULL
                        """
                    ),
                    {"resource_type": resource_type},
                ).one()
            )
            or 0
        )
        reports.append({
            "resource_type": resource_type,
            "target_table": target_table,
            "dangling": dangling,
        })
    return reports


def _codex_note_json_ref_report(session: Session, column_name: str) -> dict[str, int]:
    if not _column_exists(session, "codexdiaryimportrun", column_name):
        return {"non_numeric": 0, "dangling": 0}
    non_numeric = int(
        _first(
            session.execute(
                text(
                    f"""
                    SELECT COUNT(*) FROM codexdiaryimportrun
                    WHERE json_array_length({column_name}) > 0
                      AND EXISTS (
                        SELECT 1 FROM json_each({column_name})
                        WHERE CAST(value AS TEXT) GLOB '*[^0-9]*'
                      )
                    """
                )
            ).one()
        )
        or 0
    )
    dangling = int(
        _first(
            session.execute(
                text(
                    f"""
                    SELECT COUNT(*) FROM codexdiaryimportrun
                    WHERE json_array_length({column_name}) > 0
                      AND EXISTS (
                        SELECT 1
                        FROM json_each({column_name}) item
                        LEFT JOIN notenode note
                          ON CAST(item.value AS INTEGER) = note.numeric_id
                        WHERE CAST(item.value AS TEXT) NOT GLOB '*[^0-9]*'
                          AND note.numeric_id IS NULL
                      )
                    """
                )
            ).one()
        )
        or 0
    )
    return {"non_numeric": non_numeric, "dangling": dangling}


def _collect_note_id_refs(value: Any, refs: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_note_id_refs(item, refs)
        return
    if not isinstance(value, dict):
        return
    raw_note_id = str(value.get("note_id") or "").strip()
    if raw_note_id:
        refs.append(raw_note_id)
    for child in value.values():
        if isinstance(child, (dict, list)):
            _collect_note_id_refs(child, refs)


def _fanxiu_inventory_note_ref_report(session: Session) -> dict[str, Any]:
    try:
        from backend.core.fanxiu_inventory import get_inventory_storage_path
    except Exception as exc:
        return {"exists": False, "error": str(exc), "total": 0, "non_numeric": 0, "dangling": 0}

    storage_path = get_inventory_storage_path()
    if not storage_path.exists():
        return {"exists": False, "path": str(storage_path), "total": 0, "non_numeric": 0, "dangling": 0}

    try:
        payload = json.loads(storage_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "path": str(storage_path), "error": str(exc), "total": 0, "non_numeric": 0, "dangling": 0}

    refs: list[str] = []
    _collect_note_id_refs(payload, refs)
    if not refs:
        return {"exists": True, "path": str(storage_path), "total": 0, "non_numeric": 0, "dangling": 0}

    valid_note_ids = {
        str(int(_first(row) or 0))
        for row in session.execute(text("SELECT numeric_id FROM notenode WHERE numeric_id IS NOT NULL")).all()
        if int(_first(row) or 0) > 0
    }
    non_numeric = sum(1 for ref in refs if not ref.isdecimal())
    dangling = sum(1 for ref in refs if ref.isdecimal() and ref not in valid_note_ids)
    return {
        "exists": True,
        "path": str(storage_path),
        "total": len(refs),
        "non_numeric": non_numeric,
        "dangling": dangling,
    }


def main() -> int:
    issues: list[str] = []
    with Session(engine) as session:
        table_reports = [
            _resource_table_report(session, resource_type, table_name)
            for resource_type, table_name in RESOURCE_TABLES
        ]
        resource_rows = sum(item["count"] for item in table_reports)
        identity_count = _first(session.execute(text("SELECT COUNT(*) FROM resourceidentity")).one())
        duplicate_identity_ids = session.execute(
            text("SELECT id, COUNT(*) AS c FROM resourceidentity GROUP BY id HAVING c > 1")
        ).all()
        cross_resource_conflicts = session.execute(
            text(
                """
                SELECT numeric_id, COUNT(*) AS c FROM (
                    SELECT numeric_id FROM sheetdocument WHERE numeric_id IS NOT NULL
                    UNION ALL SELECT numeric_id FROM pdfdocument WHERE numeric_id IS NOT NULL
                    UNION ALL SELECT numeric_id FROM documentasset WHERE numeric_id IS NOT NULL
                    UNION ALL SELECT numeric_id FROM notenode WHERE numeric_id IS NOT NULL
                    UNION ALL SELECT numeric_id FROM devicefile WHERE numeric_id IS NOT NULL
                ) ids GROUP BY numeric_id HAVING c > 1
                """
            )
        ).all()
        non_numeric_grants = _first(
            session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM resourceaccessgrant
                    WHERE resource_id IS NOT NULL AND resource_id GLOB '*[^0-9]*'
                    """
                )
            ).one()
        )
        codex_created_refs = _codex_note_json_ref_report(session, "created_note_ids")
        codex_duplicate_refs = _codex_note_json_ref_report(session, "duplicate_note_ids")
        fanxiu_inventory_note_refs = _fanxiu_inventory_note_ref_report(session)
        internal_ref_reports = []
        for table_name, column_name in INTERNAL_RESOURCE_REFS:
            if not _table_exists(session, table_name):
                internal_ref_reports.append({
                    "table": table_name,
                    "column": column_name,
                    "exists": False,
                    "non_numeric": 0,
                    "dangling": 0,
                })
                continue
            target_table, target_column = REF_TARGETS[(table_name, column_name)]
            non_numeric = _first(
                session.execute(
                    text(
                        f"""
                        SELECT COUNT(*) FROM {table_name}
                        WHERE {column_name} IS NOT NULL
                          AND {column_name} != ''
                          AND {column_name} GLOB '*[^0-9]*'
                        """
                    )
                ).one()
            )
            internal_ref_reports.append({
                "table": table_name,
                "column": column_name,
                "exists": True,
                "non_numeric": int(non_numeric or 0),
                "target": f"{target_table}.{target_column}",
                "dangling": _dangling_ref_count(
                    session,
                    table_name=table_name,
                    column_name=column_name,
                    target_table=target_table,
                    target_column=target_column,
                ),
            })
        dangling_grant_reports = _dangling_grant_report(session)

    for item in table_reports:
        if item["missing_numeric_id"]:
            issues.append(f"{item['table']} has rows without numeric_id")
        if item["missing_identity"]:
            issues.append(f"{item['table']} has rows without matching resourceidentity")
        if item["resource_type"] in {"sheet", "workbook", "pdf", "document_asset", "note"} and item["exists"] and not item["id_is_integer_pk"]:
            issues.append(f"{item['table']}.id is not an integer primary key")
    if identity_count != resource_rows:
        issues.append(f"resourceidentity row count {identity_count} != resource row count {resource_rows}")
    if duplicate_identity_ids:
        issues.append("resourceidentity contains duplicate ids")
    if cross_resource_conflicts:
        issues.append("resource numeric ids conflict across resource tables")
    if non_numeric_grants:
        issues.append("resourceaccessgrant contains non-numeric resource_id values")
    if codex_created_refs["non_numeric"] or codex_duplicate_refs["non_numeric"]:
        issues.append("codexdiaryimportrun contains non-numeric note id JSON refs")
    if codex_created_refs["dangling"] or codex_duplicate_refs["dangling"]:
        issues.append("codexdiaryimportrun contains dangling note id JSON refs")
    if fanxiu_inventory_note_refs["non_numeric"]:
        issues.append("Fanxiu inventory contains non-numeric note_id JSON refs")
    if fanxiu_inventory_note_refs["dangling"]:
        issues.append("Fanxiu inventory contains dangling note_id JSON refs")
    for item in internal_ref_reports:
        if item["non_numeric"]:
            issues.append(f"{item['table']}.{item['column']} contains non-numeric resource refs")
        if item["dangling"]:
            issues.append(f"{item['table']}.{item['column']} contains dangling resource refs")
    for item in dangling_grant_reports:
        if item["dangling"]:
            issues.append(f"resourceaccessgrant contains dangling {item['resource_type']} resource_id values")

    report = {
        "ok": not issues,
        "issues": issues,
        "resource_rows": resource_rows,
        "identity_count": int(identity_count or 0),
        "public_id_note": (
            "sheet/pdf/document_asset/note/device_file expose global numeric ids; "
            "workbook exposes preserved route ids, so its resourceidentity.id may differ."
        ),
        "tables": table_reports,
        "duplicate_identity_ids": len(duplicate_identity_ids),
        "cross_resource_conflicts": len(cross_resource_conflicts),
        "non_numeric_grants": int(non_numeric_grants or 0),
        "codex_created_note_refs": codex_created_refs,
        "codex_duplicate_note_refs": codex_duplicate_refs,
        "fanxiu_inventory_note_refs": fanxiu_inventory_note_refs,
        "dangling_grants": dangling_grant_reports,
        "internal_refs": internal_ref_reports,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
