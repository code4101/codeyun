import json
import sqlite3
import time
import uuid
from typing import Any, Optional
from sqlmodel import Field, SQLModel, Session, select, text
from sqlalchemy import JSON, Column, create_engine, inspect


def _table_exists(session: Session, table_name: str) -> bool:
    return (
        session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
            {"table_name": table_name},
        ).first()
        is not None
    )


def _get_table_columns(session: Session, table_name: str) -> set[str]:
    if not _table_exists(session, table_name):
        return set()
    return {str(row[1]) for row in session.exec(text(f"PRAGMA table_info({table_name})")).all()}


def _get_table_column_types(session: Session, table_name: str) -> dict[str, str]:
    if not _table_exists(session, table_name):
        return {}
    return {
        str(row[1]): str(row[2] or "").upper()
        for row in session.exec(text(f"PRAGMA table_info({table_name})")).all()
    }


def _load_json_value(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _dump_json_value(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _first_scalar(row):
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    if mapping:
        values = list(mapping.values())
        if values:
            return values[0]
    if isinstance(row, (list, tuple)):
        return row[0] if row else None
    try:
        return row[0]
    except (TypeError, KeyError, IndexError):
        return row

# --- System Version Model ---
class SystemVersion(SQLModel, table=True):
    __tablename__ = "system_version" # Explicit table name
    version: int = Field(primary_key=True)
    applied_at: float = Field(default_factory=time.time)
    description: Optional[str] = None

# --- Migration Steps Definition ---
# Each migration should be idempotent if possible, but the version check prevents re-run.

def v1_add_node_type(session: Session):
    """
    Migration V1: Add 'node_type' column and migrate data from 'task_status'.
    """
    print("Running System Upgrade V1: Add 'node_type'...")
    # Check if column exists first to be safe (idempotency within step if needed)
    res = session.exec(text("PRAGMA table_info(notenode)")).all()
    columns = [row[1] for row in res]
    
    if "node_type" not in columns:
        session.exec(text("ALTER TABLE notenode ADD COLUMN node_type VARCHAR"))
        session.commit()
        
        if "task_status" in columns:
            print("  Copying data from 'task_status' to 'node_type'...")
            session.exec(text("UPDATE notenode SET node_type = task_status WHERE node_type IS NULL"))
            session.commit()
    else:
        print("  Column 'node_type' already exists, skipping schema change.")

def v2_add_node_status(session: Session):
    """
    Migration V2: Add 'node_status' column.
    """
    print("Running System Upgrade V2: Add 'node_status'...")
    res = session.exec(text("PRAGMA table_info(notenode)")).all()
    columns = [row[1] for row in res]
    
    if "node_status" not in columns:
        session.exec(text("ALTER TABLE notenode ADD COLUMN node_status VARCHAR DEFAULT 'idea'"))
        session.commit()
    else:
        print("  Column 'node_status' already exists, skipping.")

def v3_add_custom_fields(session: Session):
    """
    Migration V3: Add 'custom_fields' column (JSON).
    """
    print("Running System Upgrade V3: Add 'custom_fields'...")
    res = session.exec(text("PRAGMA table_info(notenode)")).all()
    columns = [row[1] for row in res]
    
    if "custom_fields" not in columns:
        try:
            # Try using JSON type first
            session.exec(text("ALTER TABLE notenode ADD COLUMN custom_fields JSON DEFAULT '{}'"))
        except Exception:
            # Fallback for older SQLite versions
            print("  JSON type not supported, falling back to TEXT.")
            session.exec(text("ALTER TABLE notenode ADD COLUMN custom_fields TEXT DEFAULT '{}'"))
        session.commit()
    else:
        print("  Column 'custom_fields' already exists, skipping.")

from sqlmodel import Field, SQLModel, Session, select, text
# from backend.models import NoteNode # Circular dependency risk if not careful
# We need NoteNode for migration, but manager.py might be imported by models.py or db.py?
# Usually manager.py is standalone. But db.py imports it.
# db.py imports models.py.
# So manager.py -> models.py -> db.py -> manager.py (Cycle!)
# Solution: Define a minimal model here or import inside function.

def v4_migrate_custom_fields_to_list(session: Session):
    """
    Migration V4: Convert 'custom_fields' from Dict to List[Dict].
    Old: {"k": "v", "k2": "v2"}
    New: [{"key": "k", "value": "v", "type": "string"}, ...]
    """
    # Import inside to avoid cycle
    try:
        from backend.models import NoteNode
    except ImportError:
        # If model import fails (e.g. during early init), define minimal
        class NoteNode(SQLModel, table=True):
             id: str = Field(primary_key=True)
             custom_fields: Optional[Any] = Field(default={}, sa_column=Column(JSON))

    print("Running System Upgrade V4: Migrate custom_fields to List...")
    
    # We need to iterate all nodes and update them.
    # SQLModel session might cache things, but we are doing bulk update row by row.
    try:
        nodes = session.exec(select(NoteNode)).all()
        
        for node in nodes:
            # Check if it's a dict (old format)
            # JSON field in SQLite might come back as dict or string depending on driver/ORM version
            current_fields = node.custom_fields
            
            if isinstance(current_fields, str):
                import json
                try:
                    current_fields = json.loads(current_fields)
                except:
                    current_fields = {}

            if isinstance(current_fields, dict):
                 # It's a dict, needs migration
                 new_list = []
                 for k, v in current_fields.items():
                     # Infer type
                     f_type = "string"
                     if isinstance(v, bool):
                         f_type = "boolean"
                     elif isinstance(v, (int, float)):
                         f_type = "number"
                         v = str(v) # Normalize to string storage
                     
                     # Using List format for storage: [key, type, value]
                     new_list.append([k, f_type, v])
                 
                 # Update the node
                 node.custom_fields = new_list
                 session.add(node)
                
        session.commit()
        print("  Converted custom_fields for all nodes.")
    except Exception as e:
        print(f"  Migration V4 error (non-fatal if table empty): {e}")

def v5_fix_custom_fields_format(session: Session):
    """
    Migration V5: Fix 'custom_fields' format from List[Dict] to List[List].
    Fix data that might have been migrated to List[Dict] in early V4 runs.
    Input: [{"key": "k", "value": "v", "type": "t"}]
    Output: [["k", "t", "v"]]
    """
    # Import inside to avoid cycle
    try:
        from backend.models import NoteNode
    except ImportError:
        class NoteNode(SQLModel, table=True):
             id: str = Field(primary_key=True)
             custom_fields: Optional[Any] = Field(default={}, sa_column=Column(JSON))

    print("Running System Upgrade V5: Fix custom_fields format...")
    
    try:
        nodes = session.exec(select(NoteNode)).all()
        
        for node in nodes:
            current_fields = node.custom_fields
            
            if isinstance(current_fields, str):
                import json
                try:
                    current_fields = json.loads(current_fields)
                except:
                    current_fields = []

            # If it's a list, check if elements are dicts
            if isinstance(current_fields, list):
                new_list = []
                needs_update = False
                
                for item in current_fields:
                    if isinstance(item, dict) and "key" in item:
                        # Convert Dict item to List item
                        # Dict: {"key": "k", "value": "v", "type": "t"}
                        # List: ["k", "t", "v"]
                        k = item.get("key", "")
                        v = item.get("value", "")
                        t = item.get("type", "string")
                        new_list.append([k, t, v])
                        needs_update = True
                    elif isinstance(item, list):
                        # Already correct
                        new_list.append(item)
                    else:
                        # Unknown format?
                        pass
                
                if needs_update:
                    node.custom_fields = new_list
                    session.add(node)
                
        session.commit()
        print("  Fixed custom_fields format for all nodes.")
    except Exception as e:
        print(f"  Migration V5 error: {e}")

def v6_add_private_level(session: Session):
    """
    Migration V6: Add 'private_level' column.
    """
    print("Running System Upgrade V6: Add 'private_level'...")
    res = session.exec(text("PRAGMA table_info(notenode)")).all()
    columns = [row[1] for row in res]

    if "private_level" not in columns:
        session.exec(text("ALTER TABLE notenode ADD COLUMN private_level INTEGER DEFAULT 0"))
        session.commit()
    else:
        print("  Column 'private_level' already exists, skipping.")


def v7_migrate_userdevice_entries(session: Session):
    """
    Migration V7: Move user device assets into userdeviceentry with entry_id primary key.
    """
    print("Running System Upgrade V7: Migrate user device assets...")

    old_exists = session.exec(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='userdevice'")
    ).first()
    new_exists = session.exec(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='userdeviceentry'")
    ).first()

    if not old_exists or not new_exists:
        print("  Source or target table missing, skipping.")
        return

    new_count = session.exec(text("SELECT COUNT(*) FROM userdeviceentry")).one()
    if new_count and new_count[0] > 0:
        print("  userdeviceentry already populated, skipping.")
        return

    rows = session.exec(
        text(
            """
            SELECT user_id, device_id, name, type, url, token, is_active,
                   COALESCE(order_index, 0) AS order_index, created_at, updated_at
            FROM userdevice
            """
        )
    ).all()

    for row in rows:
        mode = "local" if row.type == "LocalDevice" else "remote"
        session.exec(
            text(
                """
                INSERT INTO userdeviceentry (
                    entry_id, user_id, device_id, name, mode, url, token,
                    is_active, order_index, created_at, updated_at
                ) VALUES (
                    :entry_id, :user_id, :device_id, :name, :mode, :url, :token,
                    :is_active, :order_index, :created_at, :updated_at
                )
                """
            ),
            params={
                "entry_id": str(uuid.uuid4()),
                "user_id": row.user_id,
                "device_id": row.device_id,
                "name": row.name or row.device_id,
                "mode": mode,
                "url": row.url,
                "token": row.token,
                "is_active": row.is_active,
                "order_index": row.order_index,
                "created_at": row.created_at or time.time(),
                "updated_at": row.updated_at or time.time(),
            },
        )
    session.commit()
    print(f"  Migrated {len(rows)} user device entries.")


def v8_backfill_userdevice_entries(session: Session):
    """
    Migration V8: Re-run userdeviceentry backfill for instances that reached V7
    before the new table was actually created.
    """
    print("Running System Upgrade V8: Backfill user device assets...")
    v7_migrate_userdevice_entries(session)


def v9_add_note_color(session: Session):
    """
    Migration V9: Add optional per-note color override.
    """
    print("Running System Upgrade V9: Add 'color'...")
    res = session.exec(text("PRAGMA table_info(notenode)")).all()
    columns = [row[1] for row in res]

    if "color" not in columns:
        session.exec(text("ALTER TABLE notenode ADD COLUMN color VARCHAR"))
        session.commit()
    else:
        print("  Column 'color' already exists, skipping.")


def v10_add_device_file_table(session: Session):
    """
    Migration V10: Add devicefile table for per-device file metadata.
    """
    print("Running System Upgrade V10: Add 'devicefile' table...")
    bind = session.get_bind()
    inspector = inspect(bind)

    if "devicefile" in inspector.get_table_names():
        print("  Table 'devicefile' already exists, skipping.")
        return

    from backend.models import DeviceFile

    DeviceFile.__table__.create(bind, checkfirst=True)
    session.commit()
    print("  Table 'devicefile' created.")


def v11_upgrade_device_file_identity_schema(session: Session):
    """
    Migration V11: Upgrade devicefile from path-only records to rematchable
    file identity records.
    """
    print("Running System Upgrade V11: Upgrade 'devicefile' schema...")
    bind = session.get_bind()
    inspector = inspect(bind)

    if "devicefile" not in inspector.get_table_names():
        print("  Table 'devicefile' missing, skipping.")
        return

    columns = {column["name"] for column in inspector.get_columns("devicefile")}
    expected_columns = {
        "id",
        "device_id",
        "absolute_path",
        "last_known_path",
        "content_hash",
        "hash_algorithm",
        "file_size",
        "match_status",
        "cover_path",
        "cover_mime_type",
        "cover_source",
        "cover_updated_at",
        "weight",
        "created_at",
        "updated_at",
        "last_seen_at",
        "hash_updated_at",
    }

    if expected_columns.issubset(columns):
        print("  'devicefile' already uses the new identity schema, skipping.")
        return

    legacy_table = "devicefile_legacy_v10"
    session.exec(text(f"DROP TABLE IF EXISTS {legacy_table}"))
    session.exec(text(f"ALTER TABLE devicefile RENAME TO {legacy_table}"))
    session.commit()

    from backend.models import DeviceFile

    DeviceFile.__table__.create(bind, checkfirst=True)
    session.exec(
        text(
            f"""
            INSERT INTO devicefile (
                id,
                device_id,
                absolute_path,
                last_known_path,
                content_hash,
                hash_algorithm,
                visual_hash_algorithm,
                file_size,
                match_status,
                weight,
                created_at,
                updated_at,
                last_seen_at,
                hash_updated_at
            )
            SELECT
                id,
                device_id,
                absolute_path,
                absolute_path,
                NULL,
                'sha256',
                'dhash-8',
                NULL,
                'matched',
                COALESCE(weight, 0),
                created_at,
                updated_at,
                updated_at,
                NULL
            FROM {legacy_table}
            """
        )
    )
    session.exec(text(f"DROP TABLE {legacy_table}"))
    session.commit()
    print("  'devicefile' upgraded to identity schema.")


def v12_add_device_file_cover_fields(session: Session):
    """
    Migration V12: Add cover cache metadata columns to devicefile.
    """
    print("Running System Upgrade V12: Add devicefile cover fields...")
    res = session.exec(text("PRAGMA table_info(devicefile)")).all()
    columns = [row[1] for row in res]

    statements = []
    if "cover_path" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN cover_path VARCHAR")
    if "cover_mime_type" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN cover_mime_type VARCHAR")
    if "cover_source" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN cover_source VARCHAR")
    if "cover_updated_at" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN cover_updated_at FLOAT")

    if not statements:
        print("  Devicefile cover fields already exist, skipping.")
        return

    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print(f"  Added {len(statements)} devicefile cover columns.")


def v13_add_device_file_metadata_fields(session: Session):
    """
    Migration V13: Add media metadata columns to devicefile.
    """
    print("Running System Upgrade V13: Add devicefile media metadata fields...")
    res = session.exec(text("PRAGMA table_info(devicefile)")).all()
    columns = [row[1] for row in res]

    statements = []
    if "modified_at_ms" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN modified_at_ms INTEGER")
    if "media_kind" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN media_kind VARCHAR")
    if "mime_type" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN mime_type VARCHAR")

    if not statements:
        print("  Devicefile media metadata fields already exist, skipping.")
        return

    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print(f"  Added {len(statements)} devicefile media metadata columns.")


def v14_add_device_file_duration_field(session: Session):
    """
    Migration V14: Add cached media duration column to devicefile.
    """
    print("Running System Upgrade V14: Add devicefile duration_ms field...")
    res = session.exec(text("PRAGMA table_info(devicefile)")).all()
    columns = [row[1] for row in res]

    if "duration_ms" in columns:
        print("  Devicefile duration_ms already exists, skipping.")
        return

    session.exec(text("ALTER TABLE devicefile ADD COLUMN duration_ms INTEGER"))
    session.commit()
    print("  Added devicefile duration_ms column.")


def v15_add_device_file_dimensions_fields(session: Session):
    """
    Migration V15: Add cached media dimension columns to devicefile.
    """
    print("Running System Upgrade V15: Add devicefile width_px/height_px fields...")
    res = session.exec(text("PRAGMA table_info(devicefile)")).all()
    columns = [row[1] for row in res]

    statements = []
    if "width_px" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN width_px INTEGER")
    if "height_px" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN height_px INTEGER")

    if not statements:
        print("  Devicefile width_px/height_px already exist, skipping.")
        return

    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print(f"  Added {len(statements)} devicefile dimension columns.")

def v16_migrate_note_weight_levels(session: Session):
    """
    Migration V16: Convert legacy note weights from 100-based scale to integer levels.
    Memo nodes keep their existing linear semantics because they are used as counts.
    """
    print("Running System Upgrade V16: Migrate note weight levels...")
    columns = _get_table_columns(session, "notenode")
    if "id" not in columns or "weight" not in columns:
        print("  Missing id/weight columns on notenode, skipping.")
        return

    select_sql = "SELECT id, weight, node_type FROM notenode" if "node_type" in columns else "SELECT id, weight, '' AS node_type FROM notenode"
    notes = session.exec(text(select_sql)).all()
    updated_count = 0

    for note in notes:
        note_id = note[0]
        raw_weight = note[1]
        node_type = note[2]

        if str(node_type or "").lower() == "memo":
            continue

        numeric_weight = raw_weight if isinstance(raw_weight, (int, float)) else 0
        normalized_weight = max(0, int(numeric_weight // 100) - 1)
        if raw_weight == normalized_weight:
            continue

        session.execute(
            text("UPDATE notenode SET weight = :weight WHERE id = :id"),
            {"weight": normalized_weight, "id": note_id},
        )
        updated_count += 1

    if updated_count > 0:
        session.commit()
    print(f"  Migrated {updated_count} note weights to integer levels.")


def v17_add_note_semantics_fields(session: Session):
    """
    Migration V17: Decouple historical note semantics from node_type.
    Adds note_kind / weight_mode and backfills legacy memo behavior explicitly.
    """
    print("Running System Upgrade V17: Add note_kind / weight_mode...")
    columns = _get_table_columns(session, "notenode")
    if not columns:
        print("  Table 'notenode' is missing, skipping.")
        return

    statements = []
    if "note_kind" not in columns:
        statements.append("ALTER TABLE notenode ADD COLUMN note_kind VARCHAR DEFAULT 'note'")
        columns.add("note_kind")
    if "weight_mode" not in columns:
        statements.append("ALTER TABLE notenode ADD COLUMN weight_mode VARCHAR")
        columns.add("weight_mode")

    for statement in statements:
        session.exec(text(statement))

    if statements:
        session.commit()

    session.exec(text("UPDATE notenode SET note_kind = 'note' WHERE note_kind IS NULL OR TRIM(note_kind) = ''"))
    if "node_type" in columns:
        session.exec(text("UPDATE notenode SET weight_mode = 'linear' WHERE weight_mode IS NULL AND LOWER(COALESCE(node_type, '')) = 'memo'"))

    user_columns = _get_table_columns(session, "user")
    if "user_id" in columns and {"id", "username"}.issubset(user_columns):
        session.exec(
            text(
                """
                UPDATE notenode
                SET note_kind = 'fanxiu_char',
                    weight_mode = COALESCE(weight_mode, 'linear')
                WHERE user_id IN (
                    SELECT id FROM user WHERE username = '凡修手游'
                )
                  AND LOWER(COALESCE(node_type, '')) = 'memo'
                """
            )
        )
    session.commit()
    print("  note_kind / weight_mode ready.")


def v18_add_note_types(session: Session):
    """
    Migration V18: Add weighted note_types and backfill from legacy node_type.
    """
    print("Running System Upgrade V18: Add note_types...")
    columns = _get_table_columns(session, "notenode")
    if "id" not in columns:
        print("  Missing id column on notenode, skipping.")
        return

    if "note_types" not in columns:
        try:
            session.exec(text("ALTER TABLE notenode ADD COLUMN note_types JSON DEFAULT '[]'"))
        except Exception:
            session.exec(text("ALTER TABLE notenode ADD COLUMN note_types TEXT DEFAULT '[]'"))
        session.commit()
        columns.add("note_types")

    from backend.core.notes.semantics import NOTE_TYPE_DEFAULT, normalize_note_types

    select_sql = "SELECT id, note_types, node_type FROM notenode" if "node_type" in columns else "SELECT id, note_types, '' AS node_type FROM notenode"
    notes = session.exec(text(select_sql)).all()
    updated_count = 0
    for note in notes:
        note_id = note[0]
        note_types = _load_json_value(note[1], [])
        node_type = note[2]

        if note_types:
            continue

        fallback_type = (str(node_type or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT)
        normalized_note_types = normalize_note_types([], fallback_type=fallback_type)
        session.execute(
            text("UPDATE notenode SET note_types = :note_types WHERE id = :id"),
            {"note_types": _dump_json_value(normalized_note_types), "id": note_id},
        )
        updated_count += 1

    if updated_count > 0:
        session.commit()
    print(f"  Backfilled note_types for {updated_count} notes.")


def v19_add_note_taxonomy_fields(session: Session):
    """
    Migration V19: Add naming-aligned taxonomy fields and backfill from legacy semantics.
    """
    print("Running System Upgrade V19: Add note_categories / primary_category / note_form / lifecycle_stage / note_scene...")
    columns = _get_table_columns(session, "notenode")
    if "id" not in columns:
        print("  Missing id column on notenode, skipping.")
        return

    statements = []
    if "note_categories" not in columns:
        try:
            statements.append("ALTER TABLE notenode ADD COLUMN note_categories JSON DEFAULT '[]'")
        except Exception:
            statements.append("ALTER TABLE notenode ADD COLUMN note_categories TEXT DEFAULT '[]'")
        columns.add("note_categories")
    if "primary_category" not in columns:
        statements.append("ALTER TABLE notenode ADD COLUMN primary_category VARCHAR DEFAULT 'general'")
        columns.add("primary_category")
    if "note_form" not in columns:
        statements.append("ALTER TABLE notenode ADD COLUMN note_form VARCHAR DEFAULT 'note'")
        columns.add("note_form")
    if "lifecycle_stage" not in columns:
        statements.append("ALTER TABLE notenode ADD COLUMN lifecycle_stage VARCHAR DEFAULT 'idea'")
        columns.add("lifecycle_stage")
    if "note_scene" not in columns:
        statements.append("ALTER TABLE notenode ADD COLUMN note_scene VARCHAR DEFAULT 'note'")
        columns.add("note_scene")

    for statement in statements:
        session.exec(text(statement))

    if statements:
        session.commit()

    from backend.core.notes.semantics import derive_note_taxonomy_from_legacy

    select_sql = (
        "SELECT id, "
        + ("note_types, " if "note_types" in columns else "'[]' AS note_types, ")
        + ("node_type, " if "node_type" in columns else "'' AS node_type, ")
        + ("note_kind, " if "note_kind" in columns else "'' AS note_kind, ")
        + ("node_status, " if "node_status" in columns else "'' AS node_status, ")
        + "note_categories, primary_category, note_form, lifecycle_stage, note_scene "
        + "FROM notenode"
    )
    notes = session.exec(text(select_sql)).all()
    updated_count = 0
    for note in notes:
        note_id = note[0]
        note_types = _load_json_value(note[1], [])
        node_type = note[2]
        note_kind = note[3]
        node_status = note[4]
        note_categories = _load_json_value(note[5], [])
        primary_category = note[6]
        note_form = note[7]
        lifecycle_stage = note[8]
        note_scene = note[9]

        taxonomy = derive_note_taxonomy_from_legacy(
            note_types,
            node_type=node_type,
            note_kind=note_kind,
            node_status=node_status,
        )
        changed = (
            note_categories != taxonomy["note_categories"]
            or (primary_category or "") != str(taxonomy["primary_category"])
            or (note_form or "") != str(taxonomy["note_form"])
            or (lifecycle_stage or "") != str(taxonomy["lifecycle_stage"])
            or (note_scene or "") != str(taxonomy["note_scene"])
        )

        if changed:
            session.execute(
                text(
                    """
                    UPDATE notenode
                    SET note_categories = :note_categories,
                        primary_category = :primary_category,
                        note_form = :note_form,
                        lifecycle_stage = :lifecycle_stage,
                        note_scene = :note_scene
                    WHERE id = :id
                    """
                ),
                {
                    "note_categories": _dump_json_value(taxonomy["note_categories"]),
                    "primary_category": str(taxonomy["primary_category"]),
                    "note_form": str(taxonomy["note_form"]),
                    "lifecycle_stage": str(taxonomy["lifecycle_stage"]),
                    "note_scene": str(taxonomy["note_scene"]),
                    "id": note_id,
                },
            )
            updated_count += 1

    if updated_count > 0:
        session.commit()
    print(f"  Backfilled taxonomy fields for {updated_count} notes.")


def v20_repair_note_category_drift(session: Session):
    """
    Migration V20: repair primary_category drift and old carried-over builtin labels.
    """
    print("Running System Upgrade V20: Repair note category drift...")

    from backend.models import AppSetting
    from backend.core.notes.semantics import (
        NOTE_CATEGORY_DEFAULT,
        NOTE_FORM_DEFAULT,
        NOTE_LIFECYCLE_STAGE_DEFAULT,
        NOTE_SCENE_DEFAULT,
        derive_legacy_semantics_from_taxonomy,
        derive_note_taxonomy_from_legacy,
    )

    note_columns = _get_table_columns(session, "notenode")
    notes = []
    if "id" in note_columns:
        notes = session.exec(
            text(
                "SELECT id, "
                + ("note_categories, " if "note_categories" in note_columns else "'[]' AS note_categories, ")
                + ("primary_category, " if "primary_category" in note_columns else "'' AS primary_category, ")
                + ("note_form, " if "note_form" in note_columns else "'' AS note_form, ")
                + ("note_scene, " if "note_scene" in note_columns else "'' AS note_scene, ")
                + ("lifecycle_stage, " if "lifecycle_stage" in note_columns else "'' AS lifecycle_stage, ")
                + ("note_types, " if "note_types" in note_columns else "'[]' AS note_types, ")
                + ("node_type, " if "node_type" in note_columns else "'' AS node_type, ")
                + ("note_kind, " if "note_kind" in note_columns else "'' AS note_kind, ")
                + ("node_status " if "node_status" in note_columns else "'' AS node_status ")
                + "FROM notenode"
            )
        ).all()

    updated_note_count = 0
    for note in notes:
        note_id = note[0]
        note_categories = _load_json_value(note[1], [])
        primary_category = note[2]
        note_form = note[3]
        note_scene = note[4]
        lifecycle_stage = note[5]
        note_types = _load_json_value(note[6], [])
        node_type = note[7]
        note_kind = note[8]
        node_status = note[9]

        if note_categories or primary_category or note_form or note_scene or lifecycle_stage:
            repaired = derive_legacy_semantics_from_taxonomy(
                note_categories,
                primary_category=primary_category or NOTE_CATEGORY_DEFAULT,
                note_form=note_form or NOTE_FORM_DEFAULT,
                note_scene=note_scene or note_kind or NOTE_SCENE_DEFAULT,
                lifecycle_stage=lifecycle_stage or node_status or NOTE_LIFECYCLE_STAGE_DEFAULT,
            )
        else:
            repaired = derive_note_taxonomy_from_legacy(
                note_types,
                node_type=node_type,
                note_kind=note_kind,
                node_status=node_status,
            )

        changed = (
            note_types != repaired["note_types"]
            or (node_type or "") != str(repaired["node_type"])
            or (note_kind or "") != str(repaired["note_kind"])
            or (node_status or "") != str(repaired["node_status"])
            or (note_form or "") != str(repaired["note_form"])
            or note_categories != repaired["note_categories"]
            or (primary_category or "") != str(repaired["primary_category"])
            or (note_scene or "") != str(repaired["note_scene"])
            or (lifecycle_stage or "") != str(repaired["lifecycle_stage"])
        )

        if changed:
            session.execute(
                text(
                    """
                    UPDATE notenode
                    SET note_types = :note_types,
                        node_type = :node_type,
                        note_kind = :note_kind,
                        node_status = :node_status,
                        note_form = :note_form,
                        note_categories = :note_categories,
                        primary_category = :primary_category,
                        note_scene = :note_scene,
                        lifecycle_stage = :lifecycle_stage
                    WHERE id = :id
                    """
                ),
                {
                    "note_types": _dump_json_value(repaired["note_types"]),
                    "node_type": str(repaired["node_type"]),
                    "note_kind": str(repaired["note_kind"]),
                    "node_status": str(repaired["node_status"]),
                    "note_form": str(repaired["note_form"]),
                    "note_categories": _dump_json_value(repaired["note_categories"]),
                    "primary_category": str(repaired["primary_category"]),
                    "note_scene": str(repaired["note_scene"]),
                    "lifecycle_stage": str(repaired["lifecycle_stage"]),
                    "id": note_id,
                },
            )
            updated_note_count += 1

    updated_palette_count = 0
    if _table_exists(session, "appsetting"):
        palette_rows = session.exec(
            select(AppSetting).where(
                AppSetting.key.like("note.category_palette.user.%"),
                AppSetting.value.is_not(None),
            )
        ).all()
    else:
        palette_rows = []

    for row in palette_rows:
        value = row.value if isinstance(row.value, dict) else {}
        items = value.get("items")
        if not isinstance(items, list):
            continue

        changed = False
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            next_item = dict(item)
            key = str(next_item.get("key") or "").strip()
            label = str(next_item.get("label") or "").strip()
            source = str(next_item.get("source") or "").strip()
            if key == NOTE_CATEGORY_DEFAULT and label == "笔记" and source == "builtin":
                next_item["label"] = "综合"
                changed = True
            normalized_items.append(next_item)

        if changed:
            row.value = {**value, "items": normalized_items}
            row.updated_at = time.time()
            session.add(row)
            updated_palette_count += 1

    if updated_note_count or updated_palette_count:
        session.commit()
    print(f"  Repaired {updated_note_count} notes and {updated_palette_count} category palettes.")


def v21_merge_predone_into_done(session: Session):
    """
    Migration V21: merge legacy predone lifecycle stage into done.
    """
    print("Running System Upgrade V21: Merge predone lifecycle stage into done...")
    columns = _get_table_columns(session, "notenode")
    conditions = []
    if "node_status" in columns:
        conditions.append("LOWER(COALESCE(node_status, '')) = 'predone'")
    if "lifecycle_stage" in columns:
        conditions.append("LOWER(COALESCE(lifecycle_stage, '')) = 'predone'")
    if not conditions:
        print("  Missing predone-related columns on notenode, skipping.")
        return

    matched = session.exec(
        text(
            "SELECT COUNT(*) FROM notenode "
            f"WHERE {' OR '.join(conditions)}"
        )
    ).first()
    updated = int(_first_scalar(matched) or 0)
    if updated > 0:
        if "node_status" in columns:
            session.exec(text("UPDATE notenode SET node_status = 'done' WHERE LOWER(COALESCE(node_status, '')) = 'predone'"))
        if "lifecycle_stage" in columns:
            session.exec(text("UPDATE notenode SET lifecycle_stage = 'done' WHERE LOWER(COALESCE(lifecycle_stage, '')) = 'predone'"))
        session.commit()
    print(f"  Merged {updated} notes from predone to done.")


def v22_add_document_reduction_progress_fields(session: Session):
    """
    Migration V22: add progress fields for long-running document reduction runs.
    """
    print("Running System Upgrade V22: Add document reduction progress fields...")
    columns = _get_table_columns(session, "documentreductionrun")
    if "id" not in columns:
        print("  Table 'documentreductionrun' missing, skipping.")
        return

    statements = []
    if "estimated_level_count" not in columns:
        statements.append("ALTER TABLE documentreductionrun ADD COLUMN estimated_level_count INTEGER DEFAULT 0")
    if "current_level_index" not in columns:
        statements.append("ALTER TABLE documentreductionrun ADD COLUMN current_level_index INTEGER DEFAULT 0")
    if "current_level_chunk_count" not in columns:
        statements.append("ALTER TABLE documentreductionrun ADD COLUMN current_level_chunk_count INTEGER DEFAULT 0")
    if "current_level_completed_chunk_count" not in columns:
        statements.append("ALTER TABLE documentreductionrun ADD COLUMN current_level_completed_chunk_count INTEGER DEFAULT 0")
    if "completed_chunk_count" not in columns:
        statements.append("ALTER TABLE documentreductionrun ADD COLUMN completed_chunk_count INTEGER DEFAULT 0")

    if not statements:
        print("  Document reduction progress fields already exist, skipping.")
        return

    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print(f"  Added {len(statements)} document reduction progress columns.")


def v23_add_git_reduction_run_table(session: Session):
    """
    Migration V23: add git reduction run table for async progress tracking.
    """
    print("Running System Upgrade V23: Add git reduction run table...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS gitreductionrun (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                entry_id VARCHAR NOT NULL,
                cwd VARCHAR NOT NULL DEFAULT '',
                provider VARCHAR NOT NULL DEFAULT '',
                model VARCHAR NOT NULL DEFAULT '',
                style VARCHAR NOT NULL DEFAULT 'summary',
                include_body BOOLEAN NOT NULL DEFAULT 1,
                branch_factor INTEGER NOT NULL DEFAULT 10,
                auto_commit BOOLEAN NOT NULL DEFAULT 0,
                add_all BOOLEAN NOT NULL DEFAULT 1,
                status VARCHAR NOT NULL DEFAULT 'pending',
                repo_root VARCHAR NOT NULL DEFAULT '',
                branch VARCHAR NOT NULL DEFAULT '',
                source_unit_count INTEGER NOT NULL DEFAULT 0,
                source_unit_truncated_count INTEGER NOT NULL DEFAULT 0,
                estimated_level_count INTEGER NOT NULL DEFAULT 0,
                current_level_index INTEGER NOT NULL DEFAULT 0,
                current_level_chunk_count INTEGER NOT NULL DEFAULT 0,
                current_level_completed_chunk_count INTEGER NOT NULL DEFAULT 0,
                completed_chunk_count INTEGER NOT NULL DEFAULT 0,
                level_count INTEGER NOT NULL DEFAULT 0,
                node_count INTEGER NOT NULL DEFAULT 0,
                error_message VARCHAR,
                result_json JSON,
                commit_json JSON,
                created_at FLOAT NOT NULL,
                finished_at FLOAT,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )
    index_statements = [
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_user_id ON gitreductionrun (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_entry_id ON gitreductionrun (entry_id)",
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_cwd ON gitreductionrun (cwd)",
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_provider ON gitreductionrun (provider)",
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_model ON gitreductionrun (model)",
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_auto_commit ON gitreductionrun (auto_commit)",
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_status ON gitreductionrun (status)",
        "CREATE INDEX IF NOT EXISTS ix_gitreductionrun_finished_at ON gitreductionrun (finished_at)",
    ]
    for statement in index_statements:
        session.exec(text(statement))
    session.commit()
    print("  gitreductionrun ready.")


def v24_add_device_file_visual_hash_fields(session: Session):
    """
    Migration V24: Add cached image duplicate-cluster hash columns to devicefile.
    """
    print("Running System Upgrade V24: Add devicefile visual hash fields...")
    res = session.exec(text("PRAGMA table_info(devicefile)")).all()
    columns = [row[1] for row in res]

    statements = []
    if "visual_hash" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN visual_hash VARCHAR")
    if "visual_hash_algorithm" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN visual_hash_algorithm VARCHAR NOT NULL DEFAULT 'dhash-8'")
    if "visual_hash_updated_at" not in columns:
        statements.append("ALTER TABLE devicefile ADD COLUMN visual_hash_updated_at FLOAT")

    if not statements:
        print("  Devicefile visual hash fields already exist, skipping.")
        return

    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print(f"  Added {len(statements)} devicefile visual hash columns.")


def v25_add_user_plain_password_field(session: Session):
    """
    Migration V25: Add plaintext password storage for user accounts.
    Old users without recoverable plaintext are marked as '未知'.
    """
    print("Running System Upgrade V25: Add user plaintext password field...")
    columns = _get_table_columns(session, "user")
    if "id" not in columns:
        print("  Table 'user' missing, skipping.")
        return

    if "password_plain" not in columns:
        session.exec(
            text("ALTER TABLE user ADD COLUMN password_plain VARCHAR NOT NULL DEFAULT '未知'")
        )
        session.commit()
        print("  Added user.password_plain column.")

    updated = session.exec(
        text(
            """
            UPDATE user
            SET password_plain = '未知'
            WHERE password_plain IS NULL OR TRIM(password_plain) = ''
            """
        )
    )
    session.commit()
    print(f"  Backfilled plaintext password for {updated.rowcount or 0} users.")


def v26_add_user_nickname_field(session: Session):
    """
    Migration V26: Add optional nickname field for user notes/remarks.
    """
    print("Running System Upgrade V26: Add user nickname field...")
    columns = _get_table_columns(session, "user")
    if "id" not in columns:
        print("  Table 'user' missing, skipping.")
        return

    if "nickname" not in columns:
        session.exec(
            text("ALTER TABLE user ADD COLUMN nickname VARCHAR NOT NULL DEFAULT ''")
        )
        session.commit()
        print("  Added user.nickname column.")

    updated = session.exec(
        text(
            """
            UPDATE user
            SET nickname = ''
            WHERE nickname IS NULL
            """
        )
    )
    session.commit()
    print(f"  Backfilled nickname for {updated.rowcount or 0} users.")


def v27_add_user_phone_field(session: Session):
    """
    Migration V27: Add optional phone field for users.
    """
    print("Running System Upgrade V27: Add user phone field...")
    columns = _get_table_columns(session, "user")
    if "id" not in columns:
        print("  Table 'user' missing, skipping.")
        return

    if "phone" not in columns:
        session.exec(text("ALTER TABLE user ADD COLUMN phone VARCHAR"))
        session.commit()
        print("  Added user.phone column.")

    updated = session.exec(
        text(
            """
            UPDATE user
            SET phone = NULL
            WHERE TRIM(COALESCE(phone, '')) = ''
            """
        )
    )
    session.commit()
    print(f"  Normalized phone for {updated.rowcount or 0} users.")


def v28_add_sheetdocument_owner_user_id(session: Session):
    """
    Migration V28: Add 'owner_user_id' column to sheetdocument.
    """
    print("Running System Upgrade V28: Add 'owner_user_id' to sheetdocument...")
    columns = _get_table_columns(session, "sheetdocument")
    if not columns:
        print("  Table 'sheetdocument' does not exist yet, skipping.")
        return

    statements: list[str] = []
    if "owner_user_id" not in columns:
        statements.append("ALTER TABLE sheetdocument ADD COLUMN owner_user_id INTEGER")
    statements.append("CREATE INDEX IF NOT EXISTS ix_sheetdocument_owner_user_id ON sheetdocument (owner_user_id)")

    if not statements:
        print("  owner_user_id column already exists, skipping.")
        return

    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print("  Added sheetdocument owner_user_id support.")


def v29_migrate_attendance_course_sheets_to_notes_workbook(session: Session):
    """
    Migration V29: Move the legacy attendance course sheets into the notes workbook MVP.
    """
    print("Running System Upgrade V29: Migrate attendance course sheets to notes workbook...")

    required_tables = {"sheetdocument", "workbookdocument", "workbooksheetlink"}
    if not all(_table_exists(session, table_name) for table_name in required_tables):
        print("  Required sheet/workbook tables are not ready yet, skipping.")
        return

    try:
        from backend.models import SheetDocument, WorkbookDocument, WorkbookSheetLink
    except ImportError:
        print("  Failed to import sheet/workbook models, skipping.")
        return

    def _get_next_numeric_id(table_name: str) -> int:
        row = session.exec(
            text(f"SELECT COALESCE(MAX(numeric_id), 0) FROM {table_name}")
        ).first()
        return max(int(_first_scalar(row) or 0), 0) + 1

    course_owner_key = "20260412-chanzong-12qi-1jie"
    workbook_title = "20260412禅宗12期一阶"
    target_owner_type = "course_workbook"
    target_scope = "notes"
    target_sheet_specs = (
        (
            "registration",
            "报名表",
            {
                "schema_version": 1,
                "columns": [
                    "组号",
                    "序号",
                    "备注",
                    "提交时间",
                    "姓名",
                    "微信昵称",
                    "手机号",
                    "错误手机号",
                    "微信支付订单号",
                    "订单日期",
                    "商户订单号",
                    "订单金额",
                    "已返款",
                    "用户ID",
                    "匹配得分",
                    "参考信息",
                ],
                "rows": [],
            },
        ),
        (
            "attendance",
            "考勤表",
            {
                "schema_version": 1,
                "columns": ["列1", "列2", "列3"],
                "rows": [],
            },
        ),
    )
    target_sheet_keys = tuple(item[0] for item in target_sheet_specs)

    source_documents = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == "attendance")
        .where(SheetDocument.owner_type == "course_session")
        .where(SheetDocument.owner_key == course_owner_key)
        .where(SheetDocument.sheet_key.in_(target_sheet_keys))
    ).all()
    target_documents = session.exec(
        select(SheetDocument)
        .where(SheetDocument.scope == target_scope)
        .where(SheetDocument.owner_type == target_owner_type)
        .where(SheetDocument.owner_key == course_owner_key)
        .where(SheetDocument.sheet_key.in_(target_sheet_keys))
    ).all()

    if not source_documents and not target_documents:
        print("  No legacy attendance course sheet data found, skipping.")
        return

    source_map = {str(document.sheet_key): document for document in source_documents}
    target_map = {str(document.sheet_key): document for document in target_documents}

    owner_candidates: list[int] = []
    for document in [*source_documents, *target_documents]:
        for candidate in (
            document.owner_user_id,
            document.updated_by_user_id,
            document.created_by_user_id,
        ):
            if isinstance(candidate, int) and candidate > 0 and candidate not in owner_candidates:
                owner_candidates.append(candidate)

    existing_workbook: Any = None
    if target_documents:
        target_document_ids = [document.id for document in target_documents]
        links = session.exec(
            select(WorkbookSheetLink)
            .where(WorkbookSheetLink.sheet_id.in_(target_document_ids))
            .order_by(WorkbookSheetLink.created_at)
        ).all()
        if links:
            workbook_ids = [link.workbook_id for link in links]
            existing_workbook = session.exec(
                select(WorkbookDocument)
                .where(WorkbookDocument.id.in_(workbook_ids))
                .order_by(WorkbookDocument.created_at)
            ).first()
            if existing_workbook is not None and isinstance(existing_workbook.owner_user_id, int):
                owner_user_id = int(existing_workbook.owner_user_id)
                if owner_user_id not in owner_candidates:
                    owner_candidates.insert(0, owner_user_id)

    owner_user_id = owner_candidates[0] if owner_candidates else None
    if owner_user_id is None:
        print("  Could not determine workbook owner, skipping.")
        return

    workbook = existing_workbook
    if workbook is None:
        workbook = session.exec(
            select(WorkbookDocument)
            .where(WorkbookDocument.owner_user_id == owner_user_id)
            .where(WorkbookDocument.title == workbook_title)
            .order_by(WorkbookDocument.created_at)
        ).first()

    now = time.time()
    mutated = False
    if workbook is None:
        workbook = WorkbookDocument(
            numeric_id=_get_next_numeric_id("workbookdocument"),
            title=workbook_title,
            owner_user_id=owner_user_id,
            created_by_user_id=owner_user_id,
            updated_by_user_id=owner_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(workbook)
        session.flush()
        mutated = True

    existing_links = session.exec(
        select(WorkbookSheetLink)
        .where(WorkbookSheetLink.workbook_id == workbook.id)
        .order_by(WorkbookSheetLink.order_index, WorkbookSheetLink.created_at)
    ).all()
    linked_sheet_ids = {link.sheet_id for link in existing_links}
    next_order_index = max((int(link.order_index or 0) for link in existing_links), default=0)

    for sheet_key, default_title, default_document in target_sheet_specs:
        target_document = target_map.get(sheet_key)
        source_document = source_map.get(sheet_key)

        if target_document is None:
            source_document_json = (
                dict(source_document.document_json or {})
                if source_document is not None and isinstance(source_document.document_json, dict)
                else dict(default_document)
            )
            title = str(source_document.title or "").strip() or default_title if source_document is not None else default_title
            created_by_user_id = (
                source_document.created_by_user_id
                if source_document is not None and isinstance(source_document.created_by_user_id, int)
                else owner_user_id
            )
            updated_by_user_id = (
                source_document.updated_by_user_id
                if source_document is not None and isinstance(source_document.updated_by_user_id, int)
                else owner_user_id
            )
            created_at = float(source_document.created_at or now) if source_document is not None else now
            updated_at = float(source_document.updated_at or now) if source_document is not None else now

            target_document = SheetDocument(
                numeric_id=_get_next_numeric_id("sheetdocument"),
                scope=target_scope,
                owner_type=target_owner_type,
                owner_key=course_owner_key,
                sheet_key=sheet_key,
                title=title,
                engine="handsontable",
                document_json=source_document_json,
                version=1,
                owner_user_id=owner_user_id,
                created_by_user_id=created_by_user_id,
                updated_by_user_id=updated_by_user_id,
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(target_document)
            session.flush()
            target_map[sheet_key] = target_document
            mutated = True

        if target_document.id not in linked_sheet_ids:
            next_order_index += 10
            session.add(
                WorkbookSheetLink(
                    workbook_id=workbook.id,
                    sheet_id=target_document.id,
                    order_index=next_order_index,
                    created_at=now,
                )
            )
            linked_sheet_ids.add(target_document.id)
            mutated = True

    if not mutated:
        print("  Legacy attendance course sheets were already migrated.")
        return

    workbook.updated_by_user_id = owner_user_id
    workbook.updated_at = now
    session.add(workbook)
    session.commit()
    print("  Migrated legacy attendance course sheets into the notes workbook.")


def v30_add_numeric_sheet_and_workbook_ids(session: Session):
    """
    Migration V30: Add sequential numeric ids for sheet/workbook URL access.
    """
    print("Running System Upgrade V30: Add numeric sheet/workbook ids...")

    table_names = ("sheetdocument", "workbookdocument")
    existing_tables = [table_name for table_name in table_names if _table_exists(session, table_name)]
    if not existing_tables:
        print("  No sheet/workbook tables found, skipping.")
        return

    for table_name in existing_tables:
        columns = _get_table_columns(session, table_name)
        if "numeric_id" not in columns:
            session.exec(text(f"ALTER TABLE {table_name} ADD COLUMN numeric_id INTEGER"))

    for table_name in existing_tables:
        row = session.exec(
            text(
                f"""
                SELECT rowid, created_at
                FROM {table_name}
                WHERE numeric_id IS NULL
                ORDER BY created_at ASC, rowid ASC
                """
            )
        ).all()
        if not row:
            continue

        current_max_row = session.exec(
            text(f"SELECT COALESCE(MAX(numeric_id), 0) FROM {table_name}")
        ).first()
        next_numeric_id = max(int(_first_scalar(current_max_row) or 0), 0)
        for record in row:
            next_numeric_id += 1
            session.execute(
                text(f"UPDATE {table_name} SET numeric_id = :numeric_id WHERE rowid = :rowid"),
                {
                    "numeric_id": next_numeric_id,
                    "rowid": int(record[0]),
                },
            )

    if "sheetdocument" in existing_tables:
        session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_sheetdocument_numeric_id ON sheetdocument (numeric_id)"))
    if "workbookdocument" in existing_tables:
        session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_workbookdocument_numeric_id ON workbookdocument (numeric_id)"))

    session.commit()
    print("  Added numeric sheet/workbook ids.")


def v31_finalize_numeric_sheet_id_rollout(session: Session):
    """
    Migration V31: Keep numeric id rollout ordering monotonic.
    """
    print("Running System Upgrade V31: Finalize numeric sheet/workbook id rollout...")
    session.commit()
    print("  Numeric sheet/workbook id rollout finalized.")


def v32_add_codex_daily_summary_run_table(session: Session):
    """
    Migration V32: Add Codex daily summary run table.
    """
    print("Running System Upgrade V32: Add Codex daily summary run table...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS codexdailysummaryrun (
                id VARCHAR PRIMARY KEY NOT NULL,
                scope_key VARCHAR NOT NULL DEFAULT '',
                user_id INTEGER,
                root_key VARCHAR NOT NULL DEFAULT '',
                root_dir VARCHAR NOT NULL DEFAULT '',
                summary_date VARCHAR NOT NULL DEFAULT '',
                timezone VARCHAR NOT NULL DEFAULT 'Asia/Shanghai',
                provider VARCHAR NOT NULL DEFAULT '',
                generated_by VARCHAR NOT NULL DEFAULT 'deepseek',
                model VARCHAR NOT NULL DEFAULT '',
                prompt_version VARCHAR NOT NULL DEFAULT '',
                force_requested BOOLEAN NOT NULL DEFAULT 0,
                status VARCHAR NOT NULL DEFAULT 'pending',
                stage VARCHAR NOT NULL DEFAULT 'pending',
                stage_label VARCHAR NOT NULL DEFAULT '等待中',
                thread_count INTEGER NOT NULL DEFAULT 0,
                turn_count INTEGER NOT NULL DEFAULT 0,
                user_message_count INTEGER NOT NULL DEFAULT 0,
                assistant_message_count INTEGER NOT NULL DEFAULT 0,
                summary_text VARCHAR NOT NULL DEFAULT '',
                error_message VARCHAR,
                result_json JSON NOT NULL DEFAULT '{}',
                heartbeat_at FLOAT,
                created_at FLOAT NOT NULL,
                finished_at FLOAT,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )

    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_scope_key ON codexdailysummaryrun (scope_key)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_user_id ON codexdailysummaryrun (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_root_key ON codexdailysummaryrun (root_key)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_root_dir ON codexdailysummaryrun (root_dir)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_summary_date ON codexdailysummaryrun (summary_date)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_provider ON codexdailysummaryrun (provider)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_generated_by ON codexdailysummaryrun (generated_by)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_model ON codexdailysummaryrun (model)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_prompt_version ON codexdailysummaryrun (prompt_version)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_force_requested ON codexdailysummaryrun (force_requested)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_status ON codexdailysummaryrun (status)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_stage ON codexdailysummaryrun (stage)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_heartbeat_at ON codexdailysummaryrun (heartbeat_at)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_created_at ON codexdailysummaryrun (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_finished_at ON codexdailysummaryrun (finished_at)",
        "CREATE INDEX IF NOT EXISTS ix_codexdailysummaryrun_scope_root_date_created ON codexdailysummaryrun (scope_key, root_key, summary_date, created_at)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added codex daily summary run table.")


def v33_add_fanxiu_region_data_tables(session: Session):
    """
    Migration V33: Add Fanxiu region/server data and character history tables.
    """
    print("Running System Upgrade V33: Add Fanxiu region data tables...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS fanxiuregionarea (
                id VARCHAR PRIMARY KEY NOT NULL,
                number INTEGER NOT NULL,
                name VARCHAR NOT NULL,
                start_date VARCHAR NOT NULL DEFAULT '',
                end_date VARCHAR NOT NULL DEFAULT '',
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                CONSTRAINT uq_fanxiuregionarea_number UNIQUE (number),
                CONSTRAINT uq_fanxiuregionarea_name UNIQUE (name)
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS fanxiuregionserver (
                id VARCHAR PRIMARY KEY NOT NULL,
                region_id VARCHAR NOT NULL DEFAULT '',
                region_name VARCHAR NOT NULL,
                server_order INTEGER NOT NULL,
                name VARCHAR NOT NULL,
                open_date VARCHAR NOT NULL DEFAULT '',
                mark_type VARCHAR NOT NULL DEFAULT '',
                mark_label VARCHAR NOT NULL DEFAULT '',
                mark_title VARCHAR NOT NULL DEFAULT '',
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                CONSTRAINT uq_fanxiuregionserver_region_order UNIQUE (region_name, server_order),
                CONSTRAINT uq_fanxiuregionserver_region_name UNIQUE (region_name, name)
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS fanxiuregioncharacterrecord (
                id VARCHAR PRIMARY KEY NOT NULL,
                region_name VARCHAR NOT NULL,
                server_name VARCHAR NOT NULL,
                guild_name VARCHAR NOT NULL DEFAULT '',
                role_name VARCHAR NOT NULL DEFAULT '',
                attack VARCHAR NOT NULL DEFAULT '',
                recorded_date VARCHAR NOT NULL DEFAULT '',
                disabled BOOLEAN NOT NULL DEFAULT 0,
                disabled_at FLOAT,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )

    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_fanxiuregionarea_number ON fanxiuregionarea (number)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_fanxiuregionarea_name ON fanxiuregionarea (name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregionarea_start_date ON fanxiuregionarea (start_date)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregionserver_region_id ON fanxiuregionserver (region_id)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregionserver_region_name ON fanxiuregionserver (region_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregionserver_server_order ON fanxiuregionserver (server_order)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregionserver_name ON fanxiuregionserver (name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregionserver_open_date ON fanxiuregionserver (open_date)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregionserver_region_order ON fanxiuregionserver (region_name, server_order)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_region_name ON fanxiuregioncharacterrecord (region_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_server_name ON fanxiuregioncharacterrecord (server_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_guild_name ON fanxiuregioncharacterrecord (guild_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_role_name ON fanxiuregioncharacterrecord (role_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_recorded_date ON fanxiuregioncharacterrecord (recorded_date)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_disabled ON fanxiuregioncharacterrecord (disabled)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_disabled_at ON fanxiuregioncharacterrecord (disabled_at)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_identity ON fanxiuregioncharacterrecord (region_name, server_name, guild_name, role_name)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added Fanxiu region data tables.")


def v34_add_fanxiu_region_character_cultivation_level(session: Session):
    """
    Migration V34: Add cultivation level to Fanxiu region character records.
    """
    print("Running System Upgrade V34: Add Fanxiu region character cultivation level...")
    columns = _get_table_columns(session, "fanxiuregioncharacterrecord")
    if "cultivation_level" not in columns:
        session.exec(text("ALTER TABLE fanxiuregioncharacterrecord ADD COLUMN cultivation_level VARCHAR NOT NULL DEFAULT ''"))
    session.exec(
        text(
            "CREATE INDEX IF NOT EXISTS ix_fanxiuregioncharacterrecord_cultivation_level "
            "ON fanxiuregioncharacterrecord (cultivation_level)"
        )
    )
    session.commit()
    print("  Added Fanxiu region character cultivation level.")


def v35_add_resource_access_grant_table(session: Session):
    """
    Migration V35: Add resource-level ACL grants.
    """
    print("Running System Upgrade V35: Add resource access grant table...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS resourceaccessgrant (
                id VARCHAR PRIMARY KEY NOT NULL,
                resource_type VARCHAR NOT NULL,
                resource_id VARCHAR NOT NULL,
                subject_key VARCHAR NOT NULL,
                subject_type VARCHAR NOT NULL,
                subject_user_id INTEGER,
                role VARCHAR NOT NULL DEFAULT 'viewer',
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                updated_by_user_id INTEGER,
                CONSTRAINT uq_resourceaccessgrant_resource_subject
                    UNIQUE (resource_type, resource_id, subject_key)
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_resourceaccessgrant_resource_type ON resourceaccessgrant (resource_type)",
        "CREATE INDEX IF NOT EXISTS ix_resourceaccessgrant_resource_id ON resourceaccessgrant (resource_id)",
        "CREATE INDEX IF NOT EXISTS ix_resourceaccessgrant_subject_key ON resourceaccessgrant (subject_key)",
        "CREATE INDEX IF NOT EXISTS ix_resourceaccessgrant_subject_type ON resourceaccessgrant (subject_type)",
        "CREATE INDEX IF NOT EXISTS ix_resourceaccessgrant_subject_user_id ON resourceaccessgrant (subject_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_resourceaccessgrant_role ON resourceaccessgrant (role)",
        "CREATE INDEX IF NOT EXISTS ix_resourceaccessgrant_resource_lookup ON resourceaccessgrant (resource_type, resource_id)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added resource access grant table.")


def v36_remove_legacy_attendance_questionnaire_config(session: Session):
    """
    Migration V36: Remove legacy attendance questionnaire catalog storage.
    """
    print("Running System Upgrade V36: Remove legacy attendance questionnaire config...")

    if _table_exists(session, "appsetting"):
        row = session.execute(
            text("SELECT value FROM appsetting WHERE key = :key"),
            {"key": "attendance.service.extra"},
        ).first()
        raw_value = _first_scalar(row)
        payload = _load_json_value(raw_value, {})
        if isinstance(payload, dict):
            for obsolete_key in ("feedback_course_names", "feedback_course_names_updated_at"):
                payload.pop(obsolete_key, None)
            if payload:
                session.execute(
                    text("UPDATE appsetting SET value = :value, updated_at = :updated_at WHERE key = :key"),
                    {
                        "key": "attendance.service.extra",
                        "value": _dump_json_value(payload),
                        "updated_at": time.time(),
                    },
                )
            else:
                session.execute(
                    text("DELETE FROM appsetting WHERE key = :key"),
                    {"key": "attendance.service.extra"},
                )

    if _table_exists(session, "featureaccesspolicy"):
        rows = session.exec(text("SELECT subject_key, overrides FROM featureaccesspolicy")).all()
        for row in rows:
            mapping = getattr(row, "_mapping", None)
            if mapping:
                subject_key = mapping.get("subject_key")
                raw_overrides = mapping.get("overrides")
            else:
                subject_key = row[0]
                raw_overrides = row[1]
            overrides = _load_json_value(raw_overrides, {})
            if not isinstance(overrides, dict) or "attendance.wjx-templates" not in overrides:
                continue
            overrides.pop("attendance.wjx-templates", None)
            if overrides:
                session.execute(
                    text("UPDATE featureaccesspolicy SET overrides = :overrides, updated_at = :updated_at WHERE subject_key = :subject_key"),
                    {
                        "subject_key": subject_key,
                        "overrides": _dump_json_value(overrides),
                        "updated_at": time.time(),
                    },
                )
            else:
                session.execute(
                    text("DELETE FROM featureaccesspolicy WHERE subject_key = :subject_key"),
                    {"subject_key": subject_key},
                )

    session.exec(text("DROP TABLE IF EXISTS attendancerun"))
    session.exec(text("DROP TABLE IF EXISTS attendancetemplateasset"))
    session.commit()
    print("  Removed legacy attendance questionnaire config tables and settings.")


def v37_add_note_metadata_feedback_tables(session: Session):
    """
    Migration V37: Add note metadata feedback and optimization run tables.
    """
    print("Running System Upgrade V37: Add note metadata feedback tables...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS notemetadatafeedback (
                id VARCHAR PRIMARY KEY NOT NULL,
                user_id INTEGER NOT NULL,
                note_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                source_kind VARCHAR NOT NULL DEFAULT 'manual_update',
                source_kinds JSON NOT NULL DEFAULT '[]',
                source_ref_id VARCHAR,
                field_signature VARCHAR NOT NULL DEFAULT '',
                field_names JSON NOT NULL DEFAULT '[]',
                before_snapshot JSON,
                after_snapshot JSON,
                title_sample VARCHAR NOT NULL DEFAULT '',
                content_summary VARCHAR NOT NULL DEFAULT '',
                content_hash VARCHAR NOT NULL DEFAULT '',
                content_length INTEGER NOT NULL DEFAULT 0,
                event_count INTEGER NOT NULL DEFAULT 1,
                consumer_run_id VARCHAR,
                first_event_at FLOAT NOT NULL,
                last_event_at FLOAT NOT NULL,
                consumed_at FLOAT,
                compressed_at FLOAT,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS notemetadatafeedbackoptimizationrun (
                id VARCHAR PRIMARY KEY NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                trigger_reason VARCHAR NOT NULL DEFAULT 'manual',
                stage VARCHAR NOT NULL DEFAULT 'pending',
                stage_label VARCHAR NOT NULL DEFAULT '等待中',
                provider VARCHAR NOT NULL DEFAULT 'codex_cli',
                model VARCHAR NOT NULL DEFAULT '',
                sample_count INTEGER NOT NULL DEFAULT 0,
                consumed_feedback_ids JSON NOT NULL DEFAULT '[]',
                changed_files JSON NOT NULL DEFAULT '[]',
                backup_json JSON NOT NULL DEFAULT '{}',
                result_text VARCHAR NOT NULL DEFAULT '',
                test_results JSON NOT NULL DEFAULT '{}',
                error_message VARCHAR,
                queue_task_id VARCHAR,
                heartbeat_at FLOAT,
                started_at FLOAT,
                finished_at FLOAT,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_user_id ON notemetadatafeedback (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_note_id ON notemetadatafeedback (note_id)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_status ON notemetadatafeedback (status)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_source_kind ON notemetadatafeedback (source_kind)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_source_ref_id ON notemetadatafeedback (source_ref_id)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_field_signature ON notemetadatafeedback (field_signature)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_content_hash ON notemetadatafeedback (content_hash)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_consumer_run_id ON notemetadatafeedback (consumer_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_first_event_at ON notemetadatafeedback (first_event_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_last_event_at ON notemetadatafeedback (last_event_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_consumed_at ON notemetadatafeedback (consumed_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_compressed_at ON notemetadatafeedback (compressed_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedback_pending_lookup ON notemetadatafeedback (status, user_id, note_id, field_signature, last_event_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_status ON notemetadatafeedbackoptimizationrun (status)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_trigger_reason ON notemetadatafeedbackoptimizationrun (trigger_reason)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_stage ON notemetadatafeedbackoptimizationrun (stage)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_provider ON notemetadatafeedbackoptimizationrun (provider)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_model ON notemetadatafeedbackoptimizationrun (model)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_queue_task_id ON notemetadatafeedbackoptimizationrun (queue_task_id)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_heartbeat_at ON notemetadatafeedbackoptimizationrun (heartbeat_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_started_at ON notemetadatafeedbackoptimizationrun (started_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_finished_at ON notemetadatafeedbackoptimizationrun (finished_at)",
        "CREATE INDEX IF NOT EXISTS ix_notemetadatafeedbackoptimizationrun_created_at ON notemetadatafeedbackoptimizationrun (created_at)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added note metadata feedback tables.")


def v38_add_auto_git_commit_run_table(session: Session):
    """
    Migration V38: Add automatic Git commit run table.
    """
    print("Running System Upgrade V38: Add auto git commit run table...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS autogitcommitrun (
                id VARCHAR PRIMARY KEY NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                trigger_reason VARCHAR NOT NULL DEFAULT 'scheduled',
                run_date VARCHAR NOT NULL DEFAULT '',
                stage VARCHAR NOT NULL DEFAULT 'pending',
                stage_label VARCHAR NOT NULL DEFAULT '等待中',
                repo_count INTEGER NOT NULL DEFAULT 0,
                changed_repo_count INTEGER NOT NULL DEFAULT 0,
                committed_repo_count INTEGER NOT NULL DEFAULT 0,
                skipped_repo_count INTEGER NOT NULL DEFAULT 0,
                failed_repo_count INTEGER NOT NULL DEFAULT 0,
                result_json JSON NOT NULL DEFAULT '{}',
                error_message VARCHAR,
                queue_task_id VARCHAR,
                heartbeat_at FLOAT,
                started_at FLOAT,
                finished_at FLOAT,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_status ON autogitcommitrun (status)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_trigger_reason ON autogitcommitrun (trigger_reason)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_run_date ON autogitcommitrun (run_date)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_stage ON autogitcommitrun (stage)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_queue_task_id ON autogitcommitrun (queue_task_id)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_heartbeat_at ON autogitcommitrun (heartbeat_at)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_started_at ON autogitcommitrun (started_at)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_finished_at ON autogitcommitrun (finished_at)",
        "CREATE INDEX IF NOT EXISTS ix_autogitcommitrun_created_at ON autogitcommitrun (created_at)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added auto git commit run table.")


def v39_add_pdf_page_note_table(session: Session):
    """
    Migration V39: Add per-user, per-page PDF notes.
    """
    print("Running System Upgrade V39: Add PDF page note table...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS pdfpagenote (
                id VARCHAR NOT NULL PRIMARY KEY,
                pdf_document_id VARCHAR NOT NULL,
                user_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                content_html TEXT NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                CONSTRAINT uq_pdfpagenote_document_user_page
                    UNIQUE (pdf_document_id, user_id, page_number),
                FOREIGN KEY(pdf_document_id) REFERENCES pdfdocument (id),
                FOREIGN KEY(user_id) REFERENCES user (id)
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_pdfpagenote_pdf_document_id ON pdfpagenote (pdf_document_id)",
        "CREATE INDEX IF NOT EXISTS ix_pdfpagenote_user_id ON pdfpagenote (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_pdfpagenote_page_number ON pdfpagenote (page_number)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added PDF page note table.")


def v40_add_eastmoney_trade_sync_tables(session: Session):
    """
    Migration V40: Add Eastmoney trade sync storage.
    """
    print("Running System Upgrade V40: Add Eastmoney trade sync tables...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS eastmoneytradesyncrun (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                account_label VARCHAR NOT NULL DEFAULT '',
                start_date VARCHAR NOT NULL DEFAULT '',
                end_date VARCHAR NOT NULL DEFAULT '',
                status VARCHAR NOT NULL DEFAULT 'running',
                captured_at FLOAT,
                inserted_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                trade_record_count INTEGER NOT NULL DEFAULT 0,
                position_count INTEGER NOT NULL DEFAULT 0,
                asset_summary_json JSON NOT NULL DEFAULT '{}',
                error_message TEXT,
                started_at FLOAT NOT NULL,
                finished_at FLOAT,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES user (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS eastmoneytraderecord (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                sync_run_id VARCHAR NOT NULL DEFAULT '',
                account_label VARCHAR NOT NULL DEFAULT '',
                source VARCHAR NOT NULL DEFAULT '',
                source_key VARCHAR NOT NULL,
                market VARCHAR NOT NULL DEFAULT '',
                trade_date VARCHAR NOT NULL DEFAULT '',
                trade_time VARCHAR NOT NULL DEFAULT '',
                security_code VARCHAR NOT NULL DEFAULT '',
                security_name VARCHAR NOT NULL DEFAULT '',
                direction VARCHAR NOT NULL DEFAULT '',
                quantity VARCHAR NOT NULL DEFAULT '',
                price VARCHAR NOT NULL DEFAULT '',
                occurrence_date VARCHAR NOT NULL DEFAULT '',
                occurrence_time VARCHAR NOT NULL DEFAULT '',
                occurrence_amount VARCHAR NOT NULL DEFAULT '',
                amount VARCHAR NOT NULL DEFAULT '',
                fee VARCHAR NOT NULL DEFAULT '',
                commission VARCHAR NOT NULL DEFAULT '',
                stamp_tax VARCHAR NOT NULL DEFAULT '',
                transfer_fee VARCHAR NOT NULL DEFAULT '',
                other_fee VARCHAR NOT NULL DEFAULT '',
                currency VARCHAR NOT NULL DEFAULT '',
                deal_id VARCHAR NOT NULL DEFAULT '',
                shareholder_account VARCHAR NOT NULL DEFAULT '',
                share_balance VARCHAR NOT NULL DEFAULT '',
                fund_balance VARCHAR NOT NULL DEFAULT '',
                extended_name VARCHAR NOT NULL DEFAULT '',
                raw_json JSON NOT NULL DEFAULT '{}',
                raw_text TEXT NOT NULL DEFAULT '',
                quantity_value FLOAT,
                price_value FLOAT,
                occurrence_amount_value FLOAT,
                amount_value FLOAT,
                fee_value FLOAT,
                commission_value FLOAT,
                stamp_tax_value FLOAT,
                transfer_fee_value FLOAT,
                other_fee_value FLOAT,
                share_balance_value FLOAT,
                fund_balance_value FLOAT,
                first_seen_at FLOAT NOT NULL,
                last_seen_at FLOAT NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                CONSTRAINT uq_eastmoneytraderecord_user_source_key UNIQUE (user_id, source_key),
                FOREIGN KEY(user_id) REFERENCES user (id),
                FOREIGN KEY(sync_run_id) REFERENCES eastmoneytradesyncrun (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS eastmoneyassetsnapshot (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                sync_run_id VARCHAR NOT NULL,
                account_label VARCHAR NOT NULL DEFAULT '',
                captured_at FLOAT NOT NULL,
                total_asset VARCHAR NOT NULL DEFAULT '',
                market_value VARCHAR NOT NULL DEFAULT '',
                cash_available VARCHAR NOT NULL DEFAULT '',
                cash_balance VARCHAR NOT NULL DEFAULT '',
                withdrawable VARCHAR NOT NULL DEFAULT '',
                frozen VARCHAR NOT NULL DEFAULT '',
                pnl VARCHAR NOT NULL DEFAULT '',
                raw_json JSON NOT NULL DEFAULT '{}',
                created_at FLOAT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES user (id),
                FOREIGN KEY(sync_run_id) REFERENCES eastmoneytradesyncrun (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS eastmoneypositionsnapshot (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                sync_run_id VARCHAR NOT NULL,
                account_label VARCHAR NOT NULL DEFAULT '',
                source VARCHAR NOT NULL DEFAULT '',
                market VARCHAR NOT NULL DEFAULT '',
                captured_at FLOAT NOT NULL,
                security_code VARCHAR NOT NULL DEFAULT '',
                security_name VARCHAR NOT NULL DEFAULT '',
                quantity VARCHAR NOT NULL DEFAULT '',
                available_quantity VARCHAR NOT NULL DEFAULT '',
                cost_price VARCHAR NOT NULL DEFAULT '',
                current_price VARCHAR NOT NULL DEFAULT '',
                market_value VARCHAR NOT NULL DEFAULT '',
                pnl VARCHAR NOT NULL DEFAULT '',
                pnl_ratio VARCHAR NOT NULL DEFAULT '',
                currency VARCHAR NOT NULL DEFAULT '',
                raw_json JSON NOT NULL DEFAULT '{}',
                created_at FLOAT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES user (id),
                FOREIGN KEY(sync_run_id) REFERENCES eastmoneytradesyncrun (id)
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_user_id ON eastmoneytradesyncrun (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_account_label ON eastmoneytradesyncrun (account_label)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_start_date ON eastmoneytradesyncrun (start_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_end_date ON eastmoneytradesyncrun (end_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_status ON eastmoneytradesyncrun (status)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_captured_at ON eastmoneytradesyncrun (captured_at)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_started_at ON eastmoneytradesyncrun (started_at)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytradesyncrun_finished_at ON eastmoneytradesyncrun (finished_at)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_user_id ON eastmoneytraderecord (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_sync_run_id ON eastmoneytraderecord (sync_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_account_label ON eastmoneytraderecord (account_label)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_source ON eastmoneytraderecord (source)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_source_key ON eastmoneytraderecord (source_key)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_market ON eastmoneytraderecord (market)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_trade_date ON eastmoneytraderecord (trade_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_trade_time ON eastmoneytraderecord (trade_time)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_occurrence_date ON eastmoneytraderecord (occurrence_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_occurrence_time ON eastmoneytraderecord (occurrence_time)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_security_code ON eastmoneytraderecord (security_code)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_security_name ON eastmoneytraderecord (security_name)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_direction ON eastmoneytraderecord (direction)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_currency ON eastmoneytraderecord (currency)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_deal_id ON eastmoneytraderecord (deal_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_shareholder_account ON eastmoneytraderecord (shareholder_account)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_extended_name ON eastmoneytraderecord (extended_name)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_last_seen_at ON eastmoneytraderecord (last_seen_at)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyassetsnapshot_user_id ON eastmoneyassetsnapshot (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyassetsnapshot_sync_run_id ON eastmoneyassetsnapshot (sync_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyassetsnapshot_account_label ON eastmoneyassetsnapshot (account_label)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyassetsnapshot_captured_at ON eastmoneyassetsnapshot (captured_at)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_user_id ON eastmoneypositionsnapshot (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_sync_run_id ON eastmoneypositionsnapshot (sync_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_account_label ON eastmoneypositionsnapshot (account_label)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_source ON eastmoneypositionsnapshot (source)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_market ON eastmoneypositionsnapshot (market)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_captured_at ON eastmoneypositionsnapshot (captured_at)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_security_code ON eastmoneypositionsnapshot (security_code)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_security_name ON eastmoneypositionsnapshot (security_name)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneypositionsnapshot_currency ON eastmoneypositionsnapshot (currency)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added Eastmoney trade sync tables.")


def v41_add_eastmoney_trade_detail_fields(session: Session):
    """
    Migration V41: Add detailed Eastmoney trade fields from mobile trade details.
    """
    print("Running System Upgrade V41: Add Eastmoney trade detail fields...")
    if not _table_exists(session, "eastmoneytraderecord"):
        print("  eastmoneytraderecord does not exist, skipping.")
        return

    columns = _get_table_columns(session, "eastmoneytraderecord")
    column_defs = {
        "occurrence_date": "VARCHAR NOT NULL DEFAULT ''",
        "occurrence_time": "VARCHAR NOT NULL DEFAULT ''",
        "occurrence_amount": "VARCHAR NOT NULL DEFAULT ''",
        "commission": "VARCHAR NOT NULL DEFAULT ''",
        "stamp_tax": "VARCHAR NOT NULL DEFAULT ''",
        "transfer_fee": "VARCHAR NOT NULL DEFAULT ''",
        "other_fee": "VARCHAR NOT NULL DEFAULT ''",
        "shareholder_account": "VARCHAR NOT NULL DEFAULT ''",
        "share_balance": "VARCHAR NOT NULL DEFAULT ''",
        "fund_balance": "VARCHAR NOT NULL DEFAULT ''",
        "extended_name": "VARCHAR NOT NULL DEFAULT ''",
        "occurrence_amount_value": "FLOAT",
        "commission_value": "FLOAT",
        "stamp_tax_value": "FLOAT",
        "transfer_fee_value": "FLOAT",
        "other_fee_value": "FLOAT",
        "share_balance_value": "FLOAT",
        "fund_balance_value": "FLOAT",
    }
    for name, definition in column_defs.items():
        if name not in columns:
            session.exec(text(f"ALTER TABLE eastmoneytraderecord ADD COLUMN {name} {definition}"))

    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_occurrence_date ON eastmoneytraderecord (occurrence_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_occurrence_time ON eastmoneytraderecord (occurrence_time)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_shareholder_account ON eastmoneytraderecord (shareholder_account)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneytraderecord_extended_name ON eastmoneytraderecord (extended_name)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added Eastmoney trade detail fields.")


def v42_add_eastmoney_pdf_statement_tables(session: Session):
    """
    Migration V42: Add Eastmoney PDF statement import and fund flow tables.
    """
    print("Running System Upgrade V42: Add Eastmoney PDF statement tables...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS eastmoneystatementimport (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                sync_run_id VARCHAR NOT NULL DEFAULT '',
                account_label VARCHAR NOT NULL DEFAULT '',
                source VARCHAR NOT NULL DEFAULT 'pdf_statement',
                file_name VARCHAR NOT NULL DEFAULT '',
                file_path VARCHAR NOT NULL DEFAULT '',
                file_size INTEGER NOT NULL DEFAULT 0,
                file_mtime FLOAT NOT NULL DEFAULT 0,
                file_sha256 VARCHAR NOT NULL,
                print_time VARCHAR NOT NULL DEFAULT '',
                printed_at FLOAT,
                query_start_date VARCHAR NOT NULL DEFAULT '',
                query_end_date VARCHAR NOT NULL DEFAULT '',
                customer_name VARCHAR NOT NULL DEFAULT '',
                customer_no VARCHAR NOT NULL DEFAULT '',
                fund_account VARCHAR NOT NULL DEFAULT '',
                sh_account VARCHAR NOT NULL DEFAULT '',
                sz_account VARCHAR NOT NULL DEFAULT '',
                asset_summary_json JSON NOT NULL DEFAULT '{}',
                position_count INTEGER NOT NULL DEFAULT 0,
                flow_count INTEGER NOT NULL DEFAULT 0,
                trade_record_count INTEGER NOT NULL DEFAULT 0,
                raw_text TEXT NOT NULL DEFAULT '',
                raw_json JSON NOT NULL DEFAULT '{}',
                imported_at FLOAT NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                CONSTRAINT uq_eastmoneystatementimport_user_file_sha256 UNIQUE (user_id, file_sha256),
                FOREIGN KEY(user_id) REFERENCES user (id),
                FOREIGN KEY(sync_run_id) REFERENCES eastmoneytradesyncrun (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS eastmoneyfundflowrecord (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                statement_import_id VARCHAR NOT NULL DEFAULT '',
                sync_run_id VARCHAR NOT NULL DEFAULT '',
                account_label VARCHAR NOT NULL DEFAULT '',
                source VARCHAR NOT NULL DEFAULT 'pdf_statement',
                source_key VARCHAR NOT NULL,
                flow_date VARCHAR NOT NULL DEFAULT '',
                flow_category VARCHAR NOT NULL DEFAULT '',
                market VARCHAR NOT NULL DEFAULT '',
                security_code VARCHAR NOT NULL DEFAULT '',
                security_name VARCHAR NOT NULL DEFAULT '',
                quantity VARCHAR NOT NULL DEFAULT '',
                price VARCHAR NOT NULL DEFAULT '',
                occurrence_amount VARCHAR NOT NULL DEFAULT '',
                fee VARCHAR NOT NULL DEFAULT '',
                stamp_tax VARCHAR NOT NULL DEFAULT '',
                transfer_fee VARCHAR NOT NULL DEFAULT '',
                fund_balance VARCHAR NOT NULL DEFAULT '',
                currency VARCHAR NOT NULL DEFAULT '人民币',
                raw_json JSON NOT NULL DEFAULT '{}',
                raw_text TEXT NOT NULL DEFAULT '',
                quantity_value FLOAT,
                price_value FLOAT,
                occurrence_amount_value FLOAT,
                fee_value FLOAT,
                stamp_tax_value FLOAT,
                transfer_fee_value FLOAT,
                fund_balance_value FLOAT,
                first_seen_at FLOAT NOT NULL,
                last_seen_at FLOAT NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                CONSTRAINT uq_eastmoneyfundflowrecord_user_source_key UNIQUE (user_id, source_key),
                FOREIGN KEY(user_id) REFERENCES user (id),
                FOREIGN KEY(statement_import_id) REFERENCES eastmoneystatementimport (id),
                FOREIGN KEY(sync_run_id) REFERENCES eastmoneytradesyncrun (id)
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_user_id ON eastmoneystatementimport (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_sync_run_id ON eastmoneystatementimport (sync_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_account_label ON eastmoneystatementimport (account_label)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_source ON eastmoneystatementimport (source)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_file_name ON eastmoneystatementimport (file_name)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_file_sha256 ON eastmoneystatementimport (file_sha256)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_printed_at ON eastmoneystatementimport (printed_at)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_query_start_date ON eastmoneystatementimport (query_start_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneystatementimport_query_end_date ON eastmoneystatementimport (query_end_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_user_id ON eastmoneyfundflowrecord (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_statement_import_id ON eastmoneyfundflowrecord (statement_import_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_sync_run_id ON eastmoneyfundflowrecord (sync_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_account_label ON eastmoneyfundflowrecord (account_label)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_source ON eastmoneyfundflowrecord (source)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_source_key ON eastmoneyfundflowrecord (source_key)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_flow_date ON eastmoneyfundflowrecord (flow_date)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_flow_category ON eastmoneyfundflowrecord (flow_category)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_market ON eastmoneyfundflowrecord (market)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_security_code ON eastmoneyfundflowrecord (security_code)",
        "CREATE INDEX IF NOT EXISTS ix_eastmoneyfundflowrecord_security_name ON eastmoneyfundflowrecord (security_name)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added Eastmoney PDF statement tables.")


def v43_add_service_access_token_table(session: Session):
    """
    Migration V43: Add service API token table.
    """
    print("Running System Upgrade V43: Add service access token table...")
    bind = session.get_bind()
    from backend.models import ServiceAccessToken

    ServiceAccessToken.__table__.create(bind, checkfirst=True)
    session.commit()
    print("  Added service access token table.")


def v44_add_note_numeric_ids(session: Session):
    """
    Migration V44: Add sequential numeric ids for note document URLs.
    """
    print("Running System Upgrade V44: Add note numeric ids...")
    if not _table_exists(session, "notenode"):
        print("  notenode table not found, skipping.")
        return

    columns = _get_table_columns(session, "notenode")
    if "numeric_id" not in columns:
        session.exec(text("ALTER TABLE notenode ADD COLUMN numeric_id INTEGER"))

    note_order_clause = "created_at ASC, rowid ASC" if "created_at" in columns else "rowid ASC"
    rows = session.exec(
        text(
            f"""
            SELECT rowid
            FROM notenode
            WHERE numeric_id IS NULL
            ORDER BY {note_order_clause}
            """
        )
    ).all()

    current_max_row = session.exec(text("SELECT COALESCE(MAX(numeric_id), 0) FROM notenode")).first()
    next_numeric_id = max(int(_first_scalar(current_max_row) or 0), 0)
    for record in rows:
        next_numeric_id += 1
        session.execute(
            text("UPDATE notenode SET numeric_id = :numeric_id WHERE rowid = :rowid"),
            {
                "numeric_id": next_numeric_id,
                "rowid": int(record[0]),
            },
        )

    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_notenode_numeric_id ON notenode (numeric_id)"))
    session.commit()
    print("  Added note numeric ids.")


def _migration_table_numeric_max(session: Session, table_name: str) -> int:
    if not _table_exists(session, table_name):
        return 0
    columns = _get_table_columns(session, table_name)
    if "numeric_id" not in columns:
        return 0
    row = session.exec(text(f"SELECT COALESCE(MAX(numeric_id), 0) FROM {table_name}")).first()
    return max(int(_first_scalar(row) or 0), 0)


def _migration_insert_resource_identity(
    session: Session,
    *,
    resource_id: int,
    resource_type: str,
    legacy_pk: str,
    now: float,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO resourceidentity (id, resource_type, legacy_pk, created_at, updated_at)
            VALUES (:id, :resource_type, :legacy_pk, :created_at, :updated_at)
            """
        ),
        {
            "id": int(resource_id),
            "resource_type": resource_type,
            "legacy_pk": str(legacy_pk),
            "created_at": now,
            "updated_at": now,
        },
    )


RESOURCE_IDENTITY_RESOURCE_TABLES = [
    ("sheet", "sheetdocument"),
    ("workbook", "workbookdocument"),
    ("pdf", "pdfdocument"),
    ("document_asset", "documentasset"),
    ("note", "notenode"),
    ("device_file", "devicefile"),
]


def _migration_resource_rows(session: Session, table_name: str) -> list[dict[str, Any]]:
    if not _table_exists(session, table_name):
        return []
    columns = _get_table_columns(session, table_name)
    if "numeric_id" not in columns:
        return []
    created_at_expr = "created_at" if "created_at" in columns else "0 AS created_at"
    created_at_order = "created_at ASC," if "created_at" in columns else ""
    legacy_pk_expr = (
        "COALESCE(NULLIF(legacy_id, ''), CAST(id AS TEXT))"
        if "legacy_id" in columns
        else "CAST(id AS TEXT)"
    )
    rows = session.exec(
        text(
            f"""
            SELECT rowid, {legacy_pk_expr} AS legacy_pk, numeric_id, {created_at_expr}
            FROM {table_name}
            ORDER BY
                CASE WHEN numeric_id IS NULL OR numeric_id <= 0 THEN 1 ELSE 0 END,
                numeric_id ASC,
                {created_at_order}
                rowid ASC
            """
        )
    ).all()
    return [
        {
            "rowid": int(row[0]),
            "legacy_pk": str(row[1] or "").strip(),
            "old_id": int(row[2] or (row[1] if table_name == "devicefile" and str(row[1] or "").isdecimal() else 0)),
            "created_at": float(row[3] or 0),
        }
        for row in rows
        if str(row[1] or "").strip()
    ]


def _migration_next_available_id(used_ids: set[int]) -> int:
    next_id = max(used_ids, default=0) + 1
    while next_id in used_ids:
        next_id += 1
    return next_id


def _migration_assign_resource_ids_by_priority(
    session: Session,
    *,
    force_reassign_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    force_reassign_types = force_reassign_types or set()
    used_ids: set[int] = set()
    assignments: list[dict[str, Any]] = []

    for resource_type, table_name in RESOURCE_IDENTITY_RESOURCE_TABLES:
        rows = _migration_resource_rows(session, table_name)
        if not rows:
            continue

        assigned_by_rowid: dict[int, int] = {}
        if resource_type not in force_reassign_types:
            seen_preferred_ids: set[int] = set()
            for row in rows:
                preferred_id = int(row["old_id"] or 0)
                if preferred_id <= 0 or preferred_id in used_ids or preferred_id in seen_preferred_ids:
                    continue
                assigned_by_rowid[int(row["rowid"])] = preferred_id
                seen_preferred_ids.add(preferred_id)
                used_ids.add(preferred_id)

        for row in rows:
            rowid = int(row["rowid"])
            resource_id = assigned_by_rowid.get(rowid)
            if resource_id is None:
                resource_id = _migration_next_available_id(used_ids)
                used_ids.add(resource_id)
            assignments.append({
                "resource_type": resource_type,
                "table_name": table_name,
                "rowid": rowid,
                "legacy_pk": str(row["legacy_pk"]),
                "old_id": int(row["old_id"] or 0),
                "new_id": int(resource_id),
            })

    return assignments


def _migration_workbook_route_ids_by_rowid(assignments: list[dict[str, Any]]) -> dict[int, int]:
    used_ids: set[int] = set()
    route_ids: dict[int, int] = {}
    for item in assignments:
        if str(item["resource_type"]) != "workbook":
            continue
        old_id = int(item["old_id"] or 0)
        if old_id > 0 and old_id not in used_ids:
            route_id = old_id
        else:
            route_id = _migration_next_available_id(used_ids)
        used_ids.add(route_id)
        route_ids[int(item["rowid"])] = route_id
    return route_ids


def _migration_apply_resource_identity_assignments(
    session: Session,
    assignments: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    now = time.time()
    changed_public_ids: dict[str, dict[str, str]] = {}
    workbook_route_ids = _migration_workbook_route_ids_by_rowid(assignments)
    for item in assignments:
        table_name = str(item["table_name"])
        old_id = int(item["old_id"] or 0)
        identity_id = int(item["new_id"])
        resource_type = str(item["resource_type"])
        public_id = (
            int(workbook_route_ids.get(int(item["rowid"]), old_id or identity_id))
            if resource_type == "workbook"
            else identity_id
        )
        session.execute(
            text(f"UPDATE {table_name} SET numeric_id = :numeric_id WHERE rowid = :rowid"),
            {"numeric_id": public_id, "rowid": int(item["rowid"])},
        )
        if old_id > 0 and old_id != public_id:
            changed_public_ids.setdefault(resource_type, {})[str(old_id)] = str(public_id)
        _migration_insert_resource_identity(
            session,
            resource_id=identity_id,
            resource_type=resource_type,
            legacy_pk=str(item["legacy_pk"]),
            now=now,
        )
    return changed_public_ids


def v45_add_global_resource_identity(session: Session):
    """
    Migration V45: Build a global numeric id namespace for user-facing resources.
    """
    print("Running System Upgrade V45: Add global resource identities...")
    bind = session.get_bind()
    from backend.models import ResourceIdentity

    ResourceIdentity.__table__.create(bind, checkfirst=True)

    if _table_exists(session, "documentasset"):
        columns = _get_table_columns(session, "documentasset")
        if "numeric_id" not in columns:
            session.exec(text("ALTER TABLE documentasset ADD COLUMN numeric_id INTEGER"))
    if _table_exists(session, "devicefile"):
        columns = _get_table_columns(session, "devicefile")
        if "numeric_id" not in columns:
            session.exec(text("ALTER TABLE devicefile ADD COLUMN numeric_id INTEGER"))

    for table_name in ("sheetdocument", "workbookdocument", "pdfdocument", "notenode", "documentasset", "devicefile"):
        if _table_exists(session, table_name) and "numeric_id" in _get_table_columns(session, table_name):
            session.exec(text(f"DROP INDEX IF EXISTS ux_{table_name}_numeric_id"))

    session.exec(text("DELETE FROM resourceidentity"))

    assignments = _migration_assign_resource_ids_by_priority(session)
    _migration_apply_resource_identity_assignments(session, assignments)

    if _table_exists(session, "resourceaccessgrant"):
        identity_rows = session.exec(text("SELECT id, resource_type, legacy_pk FROM resourceidentity")).all()
        identity_map = {
            (str(row[1]), str(row[2])): str(int(row[0]))
            for row in identity_rows
        }
        workbook_route_rows = (
            session.exec(text("SELECT id, numeric_id FROM workbookdocument WHERE numeric_id IS NOT NULL")).all()
            if _table_exists(session, "workbookdocument")
            else []
        )
        workbook_route_map = {
            str(row[0]): str(int(row[1]))
            for row in workbook_route_rows
            if row[0] is not None and row[1] is not None
        }
        grant_rows = session.exec(text("SELECT id, resource_type, resource_id FROM resourceaccessgrant")).all()
        for row in grant_rows:
            grant_id = str(row[0])
            resource_type = str(row[1] or "")
            resource_id = str(row[2] or "")
            canonical_type = "note" if resource_type == "note_doc" else resource_type
            public_resource_id = (
                workbook_route_map.get(resource_id)
                if canonical_type == "workbook"
                else identity_map.get((canonical_type, resource_id))
            )
            if not public_resource_id:
                continue
            session.execute(
                text(
                    """
                    UPDATE resourceaccessgrant
                    SET resource_type = :resource_type, resource_id = :resource_id
                    WHERE id = :id
                    """
                ),
                {
                    "id": grant_id,
                    "resource_type": canonical_type,
                    "resource_id": public_resource_id,
                },
            )

    for table_name in ("sheetdocument", "workbookdocument", "pdfdocument", "notenode", "documentasset", "devicefile"):
        if _table_exists(session, table_name) and "numeric_id" in _get_table_columns(session, table_name):
            session.exec(text(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{table_name}_numeric_id ON {table_name} (numeric_id)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_resourceidentity_type_legacy ON resourceidentity (resource_type, legacy_pk)"))
    session.commit()
    print("  Added global resource identities.")


def _migration_note_refs_to_public_ids(note_id_map: dict[str, str], value: Any) -> list[str]:
    raw_items = _load_json_value(value, [])
    if not isinstance(raw_items, list):
        return []
    converted: list[str] = []
    for item in raw_items:
        raw_id = str(item or "").strip()
        if not raw_id:
            continue
        converted.append(note_id_map.get(raw_id, raw_id))
    return converted


def v46_migrate_resource_json_refs_to_public_ids(session: Session):
    """
    Migration V46: Convert stored JSON note resource refs to public numeric ids.
    """
    print("Running System Upgrade V46: Migrate resource JSON refs to public ids...")
    if not _table_exists(session, "codexdiaryimportrun") or not _table_exists(session, "notenode"):
        print("  Codex diary import run or note table missing, skipping.")
        return

    note_rows = session.exec(text("SELECT id, numeric_id FROM notenode WHERE numeric_id IS NOT NULL")).all()
    note_id_map = {str(row[0]): str(int(row[1])) for row in note_rows if row[0] is not None and row[1] is not None}
    if not note_id_map:
        print("  No note id map available, skipping.")
        return

    run_rows = session.exec(text("SELECT id, created_note_ids, duplicate_note_ids FROM codexdiaryimportrun")).all()
    for row in run_rows:
        run_id = str(row[0])
        created_note_ids = _migration_note_refs_to_public_ids(note_id_map, row[1])
        duplicate_note_ids = _migration_note_refs_to_public_ids(note_id_map, row[2])
        session.execute(
            text(
                """
                UPDATE codexdiaryimportrun
                SET created_note_ids = :created_note_ids,
                    duplicate_note_ids = :duplicate_note_ids
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "created_note_ids": _dump_json_value(created_note_ids),
                "duplicate_note_ids": _dump_json_value(duplicate_note_ids),
            },
        )
    session.commit()
    print("  Migrated Codex diary note refs to public ids.")


def _migration_legacy_to_public_id_map(session: Session, resource_type: str) -> dict[str, str]:
    if not _table_exists(session, "resourceidentity"):
        return {}
    rows = session.execute(
        text(
            """
            SELECT legacy_pk, id
            FROM resourceidentity
            WHERE resource_type = :resource_type
            """
        ),
        {"resource_type": resource_type},
    ).all()
    return {str(row[0]): str(int(row[1])) for row in rows if row[0] is not None and row[1] is not None}


def _migration_table_legacy_to_numeric_id_map(session: Session, table_name: str) -> dict[str, str]:
    if not _table_exists(session, table_name):
        return {}
    columns = _get_table_columns(session, table_name)
    if "id" not in columns or "numeric_id" not in columns:
        return {}
    rows = session.exec(text(f"SELECT id, numeric_id FROM {table_name} WHERE numeric_id IS NOT NULL")).all()
    return {str(row[0]): str(int(row[1])) for row in rows if row[0] is not None and row[1] is not None}


def _migration_convert_ref_column_to_public_ids(
    session: Session,
    *,
    table_name: str,
    column_name: str,
    resource_type: str,
) -> int:
    if not _table_exists(session, table_name):
        return 0
    columns = _get_table_columns(session, table_name)
    if "id" not in columns or column_name not in columns:
        return 0
    id_map = _migration_legacy_to_public_id_map(session, resource_type)
    if not id_map:
        return 0

    updated_count = 0
    rows = session.exec(text(f"SELECT id, {column_name} FROM {table_name}")).all()
    for row in rows:
        row_id = str(row[0])
        raw_ref = str(row[1] or "").strip()
        public_ref = id_map.get(raw_ref)
        if not public_ref or public_ref == raw_ref:
            continue
        session.execute(
            text(f"UPDATE {table_name} SET {column_name} = :public_ref WHERE id = :id"),
            {"id": row_id, "public_ref": public_ref},
        )
        updated_count += 1
    return updated_count


def v47_migrate_internal_resource_refs_to_public_ids(session: Session):
    """
    Migration V47: Move low-risk internal resource refs to public numeric ids.
    """
    print("Running System Upgrade V47: Migrate internal resource refs to public ids...")
    conversions = [
        ("pdfuserstate", "pdf_document_id", "pdf"),
        ("pdfpagenote", "pdf_document_id", "pdf"),
        ("documentreductionrun", "document_id", "document_asset"),
        ("documentqueryhistory", "document_id", "document_asset"),
        ("notemetadatafeedback", "note_id", "note"),
    ]
    updated_total = 0
    for table_name, column_name, resource_type in conversions:
        updated = _migration_convert_ref_column_to_public_ids(
            session,
            table_name=table_name,
            column_name=column_name,
            resource_type=resource_type,
        )
        updated_total += updated
    session.commit()
    print(f"  Migrated {updated_total} internal resource refs.")


def v48_cleanup_unmapped_internal_resource_refs(session: Session):
    """
    Migration V48: Remove orphan metadata feedback rows that cannot map to a note resource id.
    """
    print("Running System Upgrade V48: Cleanup unmapped internal resource refs...")
    if not _table_exists(session, "notemetadatafeedback"):
        print("  Note metadata feedback table missing, skipping.")
        return

    note_id_map = _migration_legacy_to_public_id_map(session, "note")
    converted_count = 0
    deleted_count = 0
    rows = session.exec(text("SELECT id, note_id FROM notemetadatafeedback WHERE note_id GLOB '*[^0-9]*'")).all()
    for row in rows:
        row_id = str(row[0])
        raw_ref = str(row[1] or "").strip()
        public_ref = note_id_map.get(raw_ref)
        if public_ref:
            session.execute(
                text("UPDATE notemetadatafeedback SET note_id = :note_id WHERE id = :id"),
                {"id": row_id, "note_id": public_ref},
            )
            converted_count += 1
            continue
        session.execute(text("DELETE FROM notemetadatafeedback WHERE id = :id"), {"id": row_id})
        deleted_count += 1
    session.commit()
    print(f"  Converted {converted_count} feedback refs and deleted {deleted_count} orphan feedback rows.")


def _migration_public_ref(id_map: dict[str, str], raw_ref: Any) -> str:
    normalized = str(raw_ref or "").strip()
    if not normalized:
        return ""
    return id_map.get(normalized, normalized)


def _migration_quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _migration_drop_readable_views(session: Session) -> None:
    rows = session.exec(
        text("SELECT name FROM sqlite_master WHERE type = 'view' AND name LIKE '%_readable'")
    ).all()
    for row in rows:
        view_name = str(_first_scalar(row) or "")
        if not view_name:
            continue
        session.exec(text(f"DROP VIEW IF EXISTS {_migration_quote_ident(view_name)}"))


def _migration_rebuild_noteedge_public_refs(session: Session) -> tuple[int, int]:
    if not _table_exists(session, "noteedge"):
        return 0, 0
    note_id_map = _migration_legacy_to_public_id_map(session, "note")
    if not note_id_map:
        return 0, 0

    rows = session.exec(text("SELECT id, user_id, source_id, target_id, label, created_at FROM noteedge")).all()
    converted_rows: list[dict[str, Any]] = []
    updated_count = 0
    dropped_orphans = 0
    for row in rows:
        raw_source = str(row[2] or "").strip()
        raw_target = str(row[3] or "").strip()
        source_ref = _migration_public_ref(note_id_map, raw_source)
        target_ref = _migration_public_ref(note_id_map, raw_target)
        if not source_ref.isdecimal() or not target_ref.isdecimal():
            dropped_orphans += 1
            continue
        if source_ref != raw_source or target_ref != raw_target:
            updated_count += 1
        converted_rows.append({
            "id": str(row[0] or uuid.uuid4()),
            "user_id": int(row[1] or 0),
            "source_id": source_ref,
            "target_id": target_ref,
            "label": row[4],
            "created_at": float(row[5] or time.time()),
        })

    session.exec(text("PRAGMA foreign_keys=OFF"))
    session.exec(text("DROP TABLE IF EXISTS noteedge_v49_backup"))
    session.exec(text("ALTER TABLE noteedge RENAME TO noteedge_v49_backup"))
    session.exec(text(
        """
        CREATE TABLE noteedge (
            id VARCHAR PRIMARY KEY,
            user_id INTEGER NOT NULL,
            source_id VARCHAR NOT NULL,
            target_id VARCHAR NOT NULL,
            label VARCHAR,
            created_at FLOAT NOT NULL
        )
        """
    ))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_noteedge_user_id ON noteedge (user_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_noteedge_source_id ON noteedge (source_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_noteedge_target_id ON noteedge (target_id)"))
    for item in converted_rows:
        session.execute(
            text(
                """
                INSERT INTO noteedge (id, user_id, source_id, target_id, label, created_at)
                VALUES (:id, :user_id, :source_id, :target_id, :label, :created_at)
                """
            ),
            item,
        )
    session.exec(text("DROP TABLE IF EXISTS noteedge_v49_backup"))
    session.exec(text("PRAGMA foreign_keys=ON"))
    return updated_count, dropped_orphans


def _migration_rebuild_workbooksheetlink_public_refs(session: Session) -> tuple[int, int]:
    if not _table_exists(session, "workbooksheetlink"):
        return 0, 0
    workbook_id_map = _migration_table_legacy_to_numeric_id_map(session, "workbookdocument")
    sheet_id_map = _migration_table_legacy_to_numeric_id_map(session, "sheetdocument")
    if not workbook_id_map or not sheet_id_map:
        return 0, 0

    rows = session.exec(
        text("SELECT id, workbook_id, sheet_id, order_index, created_at FROM workbooksheetlink ORDER BY order_index, created_at")
    ).all()
    converted_rows: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    updated_count = 0
    dropped_duplicates = 0
    for row in rows:
        raw_workbook = str(row[1] or "").strip()
        raw_sheet = str(row[2] or "").strip()
        workbook_ref = _migration_public_ref(workbook_id_map, raw_workbook)
        sheet_ref = _migration_public_ref(sheet_id_map, raw_sheet)
        if workbook_ref != raw_workbook or sheet_ref != raw_sheet:
            updated_count += 1
        pair = (workbook_ref, sheet_ref)
        if pair in seen_pairs:
            dropped_duplicates += 1
            continue
        seen_pairs.add(pair)
        converted_rows.append({
            "id": str(row[0] or uuid.uuid4().hex),
            "workbook_id": workbook_ref,
            "sheet_id": sheet_ref,
            "order_index": int(row[3] or 0),
            "created_at": float(row[4] or time.time()),
        })

    session.exec(text("PRAGMA foreign_keys=OFF"))
    session.exec(text("DROP TABLE IF EXISTS workbooksheetlink_v49_backup"))
    session.exec(text("ALTER TABLE workbooksheetlink RENAME TO workbooksheetlink_v49_backup"))
    session.exec(text(
        """
        CREATE TABLE workbooksheetlink (
            id VARCHAR PRIMARY KEY,
            workbook_id VARCHAR NOT NULL,
            sheet_id VARCHAR NOT NULL,
            order_index INTEGER NOT NULL,
            created_at FLOAT NOT NULL,
            CONSTRAINT uq_workbooksheetlink_workbook_sheet UNIQUE (workbook_id, sheet_id)
        )
        """
    ))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_workbooksheetlink_workbook_id ON workbooksheetlink (workbook_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_workbooksheetlink_sheet_id ON workbooksheetlink (sheet_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_workbooksheetlink_order_index ON workbooksheetlink (order_index)"))
    for item in converted_rows:
        session.execute(
            text(
                """
                INSERT INTO workbooksheetlink (id, workbook_id, sheet_id, order_index, created_at)
                VALUES (:id, :workbook_id, :sheet_id, :order_index, :created_at)
                """
            ),
            item,
        )
    session.exec(text("DROP TABLE IF EXISTS workbooksheetlink_v49_backup"))
    session.exec(text("PRAGMA foreign_keys=ON"))
    return updated_count, dropped_duplicates


def v49_migrate_graph_and_workbook_links_to_public_ids(session: Session):
    """
    Migration V49: Store high-coupling graph/workbook refs as public numeric resource ids.
    """
    print("Running System Upgrade V49: Migrate graph and workbook links to public ids...")
    _migration_drop_readable_views(session)
    edge_updated, edge_dropped = _migration_rebuild_noteedge_public_refs(session)
    link_updated, link_dropped = _migration_rebuild_workbooksheetlink_public_refs(session)
    session.commit()
    print(
        "  Migrated "
        f"{edge_updated} note edge refs and {link_updated} workbook link refs; "
        f"dropped {edge_dropped} orphan note edges and {link_dropped} duplicate workbook links."
    )


def _migration_delete_nonnumeric_ref_rows(session: Session, table_name: str, columns: tuple[str, ...]) -> int:
    if not _table_exists(session, table_name):
        return 0
    existing_columns = _get_table_columns(session, table_name)
    target_columns = [column for column in columns if column in existing_columns]
    if not target_columns:
        return 0
    predicate = " OR ".join(
        f"({column} IS NOT NULL AND {column} != '' AND {column} GLOB '*[^0-9]*')"
        for column in target_columns
    )
    count = int(_first_scalar(session.exec(text(f"SELECT COUNT(*) FROM {table_name} WHERE {predicate}")).first()) or 0)
    if count:
        session.exec(text(f"DELETE FROM {table_name} WHERE {predicate}"))
    return count


def v50_cleanup_unmapped_graph_and_workbook_refs(session: Session):
    """
    Migration V50: Remove orphan high-coupling refs that could not map to numeric resources.
    """
    print("Running System Upgrade V50: Cleanup unmapped graph and workbook refs...")
    deleted_edges = _migration_delete_nonnumeric_ref_rows(session, "noteedge", ("source_id", "target_id"))
    deleted_links = _migration_delete_nonnumeric_ref_rows(session, "workbooksheetlink", ("workbook_id", "sheet_id"))
    session.commit()
    print(f"  Deleted {deleted_edges} orphan note edges and {deleted_links} orphan workbook links.")


def _migration_update_public_ref_column(
    session: Session,
    *,
    table_name: str,
    column_name: str,
    id_map: dict[str, str],
) -> int:
    if not id_map or not _table_exists(session, table_name) or column_name not in _get_table_columns(session, table_name):
        return 0
    updated = 0
    for old_id, new_id in id_map.items():
        result = session.execute(
            text(f"UPDATE {table_name} SET {column_name} = :new_id WHERE {column_name} = :old_id"),
            {"old_id": str(old_id), "new_id": str(new_id)},
        )
        updated += int(result.rowcount or 0)
    return updated


def _migration_update_resource_grants_for_public_id_changes(
    session: Session,
    changed_public_ids: dict[str, dict[str, str]],
) -> int:
    if not changed_public_ids or not _table_exists(session, "resourceaccessgrant"):
        return 0
    updated = 0
    for resource_type, id_map in changed_public_ids.items():
        for old_id, new_id in id_map.items():
            result = session.execute(
                text(
                    """
                    UPDATE resourceaccessgrant
                    SET resource_id = :new_id
                    WHERE resource_type = :resource_type
                      AND resource_id = :old_id
                    """
                ),
                {"resource_type": resource_type, "old_id": str(old_id), "new_id": str(new_id)},
            )
            updated += int(result.rowcount or 0)
    return updated


def _migration_update_codex_diary_note_refs(
    session: Session,
    note_id_map: dict[str, str],
) -> int:
    if not note_id_map or not _table_exists(session, "codexdiaryimportrun"):
        return 0
    updated = 0
    run_rows = session.exec(text("SELECT id, created_note_ids, duplicate_note_ids FROM codexdiaryimportrun")).all()
    for row in run_rows:
        run_id = str(row[0])
        created_note_ids = _migration_note_refs_to_public_ids(note_id_map, row[1])
        duplicate_note_ids = _migration_note_refs_to_public_ids(note_id_map, row[2])
        old_created = _migration_note_refs_to_public_ids({}, row[1])
        old_duplicate = _migration_note_refs_to_public_ids({}, row[2])
        if created_note_ids == old_created and duplicate_note_ids == old_duplicate:
            continue
        session.execute(
            text(
                """
                UPDATE codexdiaryimportrun
                SET created_note_ids = :created_note_ids,
                    duplicate_note_ids = :duplicate_note_ids
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "created_note_ids": _dump_json_value(created_note_ids),
                "duplicate_note_ids": _dump_json_value(duplicate_note_ids),
            },
        )
        updated += 1
    return updated


def v51_repack_resource_ids_by_priority(session: Session):
    """
    Migration V51: Correct V45's overly-high workbook/pdf/asset ids.
    """
    print("Running System Upgrade V51: Repack resource ids by priority...")
    if not _table_exists(session, "resourceidentity"):
        print("  Resource identity table missing, skipping.")
        return

    for table_name in ("sheetdocument", "workbookdocument", "pdfdocument", "notenode", "documentasset", "devicefile"):
        if _table_exists(session, table_name) and "numeric_id" in _get_table_columns(session, table_name):
            session.exec(text(f"DROP INDEX IF EXISTS ux_{table_name}_numeric_id"))
    session.exec(text("DELETE FROM resourceidentity"))

    assignments = _migration_assign_resource_ids_by_priority(
        session,
        force_reassign_types={"pdf", "document_asset"},
    )
    changed_public_ids = _migration_apply_resource_identity_assignments(session, assignments)

    updated_refs = 0
    updated_refs += _migration_update_resource_grants_for_public_id_changes(session, changed_public_ids)
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="workbooksheetlink",
        column_name="workbook_id",
        id_map=changed_public_ids.get("workbook", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="workbooksheetlink",
        column_name="sheet_id",
        id_map=changed_public_ids.get("sheet", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="noteedge",
        column_name="source_id",
        id_map=changed_public_ids.get("note", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="noteedge",
        column_name="target_id",
        id_map=changed_public_ids.get("note", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="notemetadatafeedback",
        column_name="note_id",
        id_map=changed_public_ids.get("note", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="pdfuserstate",
        column_name="pdf_document_id",
        id_map=changed_public_ids.get("pdf", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="pdfpagenote",
        column_name="pdf_document_id",
        id_map=changed_public_ids.get("pdf", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="documentreductionrun",
        column_name="document_id",
        id_map=changed_public_ids.get("document_asset", {}),
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="documentqueryhistory",
        column_name="document_id",
        id_map=changed_public_ids.get("document_asset", {}),
    )
    updated_runs = _migration_update_codex_diary_note_refs(session, changed_public_ids.get("note", {}))

    for table_name in ("sheetdocument", "workbookdocument", "pdfdocument", "notenode", "documentasset", "devicefile"):
        if _table_exists(session, table_name) and "numeric_id" in _get_table_columns(session, table_name):
            session.exec(text(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{table_name}_numeric_id ON {table_name} (numeric_id)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_resourceidentity_type_legacy ON resourceidentity (resource_type, legacy_pk)"))
    session.commit()
    print(
        "  Repacked resource ids; "
        f"changed {sum(len(item) for item in changed_public_ids.values())} resources, "
        f"updated {updated_refs} scalar refs and {updated_runs} Codex diary runs."
    )


def v52_restore_workbook_route_ids(session: Session):
    """
    Migration V52: Restore workbook-local route ids after V51 moved them into the global namespace.
    """
    print("Running System Upgrade V52: Restore workbook route ids...")
    if not _table_exists(session, "workbookdocument"):
        print("  Workbook table missing, skipping.")
        return
    columns = _get_table_columns(session, "workbookdocument")
    if "numeric_id" not in columns:
        print("  Workbook numeric id column missing, skipping.")
        return

    rows = session.exec(
        text(
            """
            SELECT rowid, id, numeric_id
            FROM workbookdocument
            WHERE numeric_id IS NOT NULL
            ORDER BY numeric_id ASC, rowid ASC
            """
        )
    ).all()
    if not rows:
        print("  No workbook route ids found, skipping.")
        return

    current_ids = [int(row[2]) for row in rows]
    sheet_max = _migration_table_numeric_max(session, "sheetdocument")
    expected_v51_ids = list(range(sheet_max + 1, sheet_max + 1 + len(rows)))
    if min(current_ids) <= sheet_max or current_ids != expected_v51_ids:
        print("  Workbook route ids do not look like V51-shifted ids, skipping.")
        return

    target_ids = [int(row[0]) for row in rows]
    if len(target_ids) != len(set(target_ids)) or any(item <= 0 for item in target_ids):
        print("  Could not derive stable rowid-based workbook ids, skipping.")
        return

    id_map = {
        str(int(row[2])): str(int(row[0]))
        for row in rows
        if int(row[2]) != int(row[0])
    }
    if not id_map:
        print("  Workbook route ids already restored.")
        return

    session.exec(text("DROP INDEX IF EXISTS ux_workbookdocument_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_workbookdocument_numeric_id"))
    for row in rows:
        session.execute(
            text("UPDATE workbookdocument SET numeric_id = :numeric_id WHERE id = :id"),
            {"numeric_id": int(row[0]), "id": str(row[1])},
        )

    updated_refs = 0
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="workbooksheetlink",
        column_name="workbook_id",
        id_map=id_map,
    )
    updated_refs += _migration_update_resource_grants_for_public_id_changes(
        session,
        {"workbook": id_map},
    )

    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_workbookdocument_numeric_id ON workbookdocument (numeric_id)"))
    session.commit()
    print(f"  Restored {len(id_map)} workbook route ids and updated {updated_refs} refs.")


def v53_add_device_file_resource_identities(session: Session):
    """
    Migration V53: Add tracked files/images to the global resource id namespace.
    """
    print("Running System Upgrade V53: Add device file resource identities...")
    if not _table_exists(session, "devicefile"):
        print("  Device file table missing, skipping.")
        return

    bind = session.get_bind()
    from backend.models import ResourceIdentity

    ResourceIdentity.__table__.create(bind, checkfirst=True)

    columns = _get_table_columns(session, "devicefile")
    if "numeric_id" not in columns:
        session.exec(text("ALTER TABLE devicefile ADD COLUMN numeric_id INTEGER"))

    session.exec(text("DROP INDEX IF EXISTS ux_devicefile_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_devicefile_numeric_id"))
    session.exec(text("DELETE FROM resourceidentity WHERE resource_type = 'device_file'"))

    used_ids = {
        int(_first_scalar(row) or 0)
        for row in session.exec(text("SELECT id FROM resourceidentity")).all()
        if int(_first_scalar(row) or 0) > 0
    }

    rows = _migration_resource_rows(session, "devicefile")
    changed_count = 0
    now = time.time()
    for row in rows:
        old_id = int(row["old_id"] or 0)
        if old_id > 0 and old_id not in used_ids:
            public_id = old_id
        else:
            public_id = _migration_next_available_id(used_ids)
        used_ids.add(public_id)
        if old_id != public_id:
            changed_count += 1

        session.execute(
            text("UPDATE devicefile SET numeric_id = :numeric_id WHERE rowid = :rowid"),
            {"numeric_id": public_id, "rowid": int(row["rowid"])},
        )
        _migration_insert_resource_identity(
            session,
            resource_id=public_id,
            resource_type="device_file",
            legacy_pk=str(row["legacy_pk"]),
            now=now,
        )

    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_devicefile_numeric_id ON devicefile (numeric_id)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_resourceidentity_type_legacy ON resourceidentity (resource_type, legacy_pk)"))
    session.commit()
    print(f"  Added {len(rows)} device file identities; reassigned {changed_count} conflicting ids.")


def v54_index_attachment_file_resources(session: Session):
    """
    Migration V54: Index existing upload attachments as device_file resources.
    """
    print("Running System Upgrade V54: Index attachment file resources...")
    if not _table_exists(session, "devicefile"):
        print("  Device file table missing, skipping.")
        return
    columns = _get_table_columns(session, "devicefile")
    if "numeric_id" not in columns:
        print("  Device file numeric id column missing, skipping.")
        return

    from backend.core.resources.attachments import index_existing_attachment_resources

    indexed_count = index_existing_attachment_resources(session)
    print(f"  Indexed {indexed_count} attachment files as device resources.")


def _migration_documentasset_has_numeric_primary_key(session: Session) -> bool:
    if not _table_exists(session, "documentasset"):
        return False
    for row in session.exec(text("PRAGMA table_info(documentasset)")).all():
        if str(row[1]) != "id":
            continue
        column_type = str(row[2] or "").upper()
        return int(row[5] or 0) > 0 and "INT" in column_type
    return False


def _migration_documentasset_rows(session: Session) -> list[dict[str, Any]]:
    columns = _get_table_columns(session, "documentasset")
    legacy_expr = "COALESCE(NULLIF(legacy_id, ''), CAST(id AS TEXT))" if "legacy_id" in columns else "CAST(id AS TEXT)"
    rows = session.exec(
        text(
            f"""
            SELECT rowid, CAST(id AS TEXT) AS raw_id, {legacy_expr} AS legacy_id,
                   numeric_id, user_id
            FROM documentasset
            ORDER BY
                CASE WHEN numeric_id IS NULL OR numeric_id <= 0 THEN 1 ELSE 0 END,
                numeric_id ASC,
                created_at ASC,
                rowid ASC
            """
        )
    ).all()
    return [
        {
            "rowid": int(row[0]),
            "raw_id": str(row[1] or "").strip(),
            "legacy_id": str(row[2] or "").strip(),
            "numeric_id": int(row[3] or 0),
            "user_id": int(row[4] or 0),
        }
        for row in rows
        if str(row[2] or "").strip()
    ]


def _migration_used_global_resource_ids_except(
    session: Session,
    *,
    resource_type: str,
    excluded_table_name: str,
) -> set[int]:
    used_ids = {
        int(_first_scalar(row) or 0)
        for row in session.execute(
            text("SELECT id FROM resourceidentity WHERE resource_type != :resource_type"),
            {"resource_type": resource_type},
        ).all()
        if int(_first_scalar(row) or 0) > 0
    }
    for candidate_table_name in ("sheetdocument", "pdfdocument", "documentasset", "notenode", "devicefile"):
        if candidate_table_name == excluded_table_name:
            continue
        if not _table_exists(session, candidate_table_name) or "numeric_id" not in _get_table_columns(session, candidate_table_name):
            continue
        used_ids.update(
            int(_first_scalar(row) or 0)
            for row in session.exec(text(f"SELECT numeric_id FROM {candidate_table_name} WHERE numeric_id IS NOT NULL")).all()
            if int(_first_scalar(row) or 0) > 0
        )
    return used_ids


def _migration_used_global_resource_ids_except_document_assets(session: Session) -> set[int]:
    return _migration_used_global_resource_ids_except(
        session,
        resource_type="document_asset",
        excluded_table_name="documentasset",
    )


def _migration_assign_documentasset_numeric_ids(session: Session) -> list[dict[str, Any]]:
    from backend.models import ResourceIdentity

    ResourceIdentity.__table__.create(session.get_bind(), checkfirst=True)
    rows = _migration_documentasset_rows(session)
    identity_rows = session.exec(
        text("SELECT id, legacy_pk FROM resourceidentity WHERE resource_type = 'document_asset'")
    ).all()
    identity_by_legacy = {
        str(row[1] or "").strip(): int(row[0])
        for row in identity_rows
        if str(row[1] or "").strip() and int(row[0] or 0) > 0
    }
    used_ids = _migration_used_global_resource_ids_except_document_assets(session)
    assigned_ids: set[int] = set()
    now = time.time()
    assignments: list[dict[str, Any]] = []

    for row in rows:
        legacy_id = str(row["legacy_id"])
        current_numeric_id = int(row["numeric_id"] or 0)
        identity_id = int(identity_by_legacy.get(legacy_id) or 0)
        if identity_id > 0 and identity_id not in used_ids and identity_id not in assigned_ids:
            numeric_id = identity_id
        elif current_numeric_id > 0 and current_numeric_id not in used_ids and current_numeric_id not in assigned_ids:
            numeric_id = current_numeric_id
        else:
            numeric_id = _migration_next_available_id(used_ids | assigned_ids)

        assigned_ids.add(numeric_id)
        session.execute(
            text(
                """
                UPDATE documentasset
                SET numeric_id = :numeric_id,
                    legacy_id = :legacy_id
                WHERE rowid = :rowid
                """
            ),
            {"numeric_id": numeric_id, "legacy_id": legacy_id, "rowid": int(row["rowid"])},
        )
        if identity_id <= 0:
            _migration_insert_resource_identity(
                session,
                resource_id=numeric_id,
                resource_type="document_asset",
                legacy_pk=legacy_id,
                now=now,
            )
        elif identity_id != numeric_id:
            session.execute(
                text(
                    """
                    UPDATE resourceidentity
                    SET id = :numeric_id,
                        updated_at = :updated_at
                    WHERE resource_type = 'document_asset'
                      AND legacy_pk = :legacy_id
                    """
                ),
                {"numeric_id": numeric_id, "updated_at": now, "legacy_id": legacy_id},
            )
        assignments.append({
            **row,
            "numeric_id": numeric_id,
            "old_numeric_id": current_numeric_id,
        })
    return assignments


def _migration_rebuild_documentasset_with_numeric_pk(session: Session) -> None:
    if _migration_documentasset_has_numeric_primary_key(session):
        return

    session.exec(text("DROP TABLE IF EXISTS documentasset_v55"))
    session.exec(
        text(
            """
            CREATE TABLE documentasset_v55 (
                id INTEGER NOT NULL PRIMARY KEY,
                numeric_id INTEGER,
                legacy_id VARCHAR,
                user_id INTEGER NOT NULL,
                title VARCHAR NOT NULL,
                original_filename VARCHAR NOT NULL,
                media_type VARCHAR NOT NULL,
                file_ext VARCHAR NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 VARCHAR NOT NULL,
                source_char_count INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                latest_run_id VARCHAR,
                latest_summary VARCHAR NOT NULL,
                latest_query_at FLOAT,
                run_count INTEGER NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES user (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            INSERT INTO documentasset_v55 (
                id, numeric_id, legacy_id, user_id, title, original_filename,
                media_type, file_ext, size_bytes, sha256, source_char_count,
                status, latest_run_id, latest_summary, latest_query_at,
                run_count, created_at, updated_at
            )
            SELECT
                CAST(numeric_id AS INTEGER),
                CAST(numeric_id AS INTEGER),
                legacy_id,
                user_id,
                COALESCE(title, ''),
                COALESCE(original_filename, ''),
                COALESCE(media_type, 'text/plain'),
                COALESCE(file_ext, ''),
                COALESCE(size_bytes, 0),
                COALESCE(sha256, ''),
                COALESCE(source_char_count, 0),
                COALESCE(status, 'uploaded'),
                latest_run_id,
                COALESCE(latest_summary, ''),
                latest_query_at,
                COALESCE(run_count, 0),
                COALESCE(created_at, 0),
                COALESCE(updated_at, 0)
            FROM documentasset
            WHERE numeric_id IS NOT NULL AND numeric_id > 0
            """
        )
    )
    session.exec(text("ALTER TABLE documentasset RENAME TO documentasset_legacy_v55"))
    session.exec(text("ALTER TABLE documentasset_v55 RENAME TO documentasset"))
    session.exec(text("DROP TABLE documentasset_legacy_v55"))


def _migration_recreate_documentasset_indexes(session: Session) -> None:
    if not _table_exists(session, "documentasset"):
        return
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_documentasset_numeric_id ON documentasset (numeric_id)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_documentasset_legacy_id ON documentasset (legacy_id)"))
    for column_name in (
        "user_id",
        "legacy_id",
        "original_filename",
        "media_type",
        "file_ext",
        "sha256",
        "status",
        "latest_run_id",
        "latest_query_at",
    ):
        session.exec(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_documentasset_{column_name} "
                f"ON documentasset ({column_name})"
            )
        )


def _migration_update_document_cache_document_ids(assignments: list[dict[str, Any]]) -> int:
    try:
        from backend.core.ai.document_reduction_cache import get_document_cache_db_path
    except Exception:
        return 0
    cache_db_path = get_document_cache_db_path()
    if not cache_db_path.exists():
        return 0
    updated = 0
    with sqlite3.connect(cache_db_path) as conn:
        for item in assignments:
            old_ref = str(item["legacy_id"])
            new_ref = str(int(item["numeric_id"]))
            result = conn.execute(
                """
                UPDATE document_node_index
                SET document_id = ?
                WHERE user_id = ?
                  AND document_id = ?
                """,
                (new_ref, int(item["user_id"] or 0), old_ref),
            )
            updated += int(result.rowcount or 0)
        conn.commit()
    return updated


def _migration_migrate_documentasset_dirs(assignments: list[dict[str, Any]]) -> int:
    try:
        from backend.core.ai.document_reduction_storage import migrate_document_asset_dir_id
    except Exception:
        return 0
    moved = 0
    for item in assignments:
        if migrate_document_asset_dir_id(
            user_id=int(item["user_id"] or 0),
            old_document_id=str(item["legacy_id"]),
            new_document_id=str(int(item["numeric_id"])),
        ):
            moved += 1
    return moved


def v55_migrate_documentasset_primary_key_to_numeric(session: Session):
    """
    Migration V55: Make documentasset.id the numeric global resource id.
    """
    print("Running System Upgrade V55: Migrate document assets to numeric primary keys...")
    if not _table_exists(session, "documentasset"):
        print("  Document asset table missing, skipping.")
        return

    _migration_drop_readable_views(session)
    columns = _get_table_columns(session, "documentasset")
    if "numeric_id" not in columns:
        session.exec(text("ALTER TABLE documentasset ADD COLUMN numeric_id INTEGER"))
        columns.add("numeric_id")
    if "legacy_id" not in columns:
        session.exec(text("ALTER TABLE documentasset ADD COLUMN legacy_id VARCHAR"))
        columns.add("legacy_id")
    session.exec(
        text(
            """
            UPDATE documentasset
            SET legacy_id = CAST(id AS TEXT)
            WHERE legacy_id IS NULL OR legacy_id = ''
            """
        )
    )

    session.exec(text("DROP INDEX IF EXISTS ux_documentasset_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_documentasset_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ux_documentasset_legacy_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_documentasset_legacy_id"))

    assignments = _migration_assign_documentasset_numeric_ids(session)

    id_map: dict[str, str] = {}
    for item in assignments:
        public_id = str(int(item["numeric_id"]))
        legacy_id = str(item["legacy_id"])
        raw_id = str(item["raw_id"])
        if legacy_id and legacy_id != public_id:
            id_map[legacy_id] = public_id
        if raw_id and raw_id != public_id:
            id_map[raw_id] = public_id
        old_numeric_id = int(item["old_numeric_id"] or 0)
        if old_numeric_id > 0 and old_numeric_id != int(item["numeric_id"]):
            id_map[str(old_numeric_id)] = public_id

    updated_refs = 0
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="documentreductionrun",
        column_name="document_id",
        id_map=id_map,
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="documentqueryhistory",
        column_name="document_id",
        id_map=id_map,
    )
    updated_refs += _migration_update_resource_grants_for_public_id_changes(
        session,
        {"document_asset": id_map},
    )

    migrated_cache_rows = _migration_update_document_cache_document_ids(assignments)
    moved_dirs = _migration_migrate_documentasset_dirs(assignments)
    _migration_rebuild_documentasset_with_numeric_pk(session)
    _migration_recreate_documentasset_indexes(session)
    session.commit()
    print(
        "  Migrated document assets to numeric primary keys; "
        f"updated {updated_refs} refs, {migrated_cache_rows} cache rows, moved {moved_dirs} asset dirs."
    )


def _migration_pdfdocument_has_numeric_primary_key(session: Session) -> bool:
    if not _table_exists(session, "pdfdocument"):
        return False
    for row in session.exec(text("PRAGMA table_info(pdfdocument)")).all():
        if str(row[1]) != "id":
            continue
        column_type = str(row[2] or "").upper()
        return int(row[5] or 0) > 0 and "INT" in column_type
    return False


def _migration_pdfdocument_rows(session: Session) -> list[dict[str, Any]]:
    columns = _get_table_columns(session, "pdfdocument")
    legacy_expr = "COALESCE(NULLIF(legacy_id, ''), CAST(id AS TEXT))" if "legacy_id" in columns else "CAST(id AS TEXT)"
    rows = session.exec(
        text(
            f"""
            SELECT rowid, CAST(id AS TEXT) AS raw_id, {legacy_expr} AS legacy_id,
                   numeric_id
            FROM pdfdocument
            ORDER BY
                CASE WHEN numeric_id IS NULL OR numeric_id <= 0 THEN 1 ELSE 0 END,
                numeric_id ASC,
                created_at ASC,
                rowid ASC
            """
        )
    ).all()
    return [
        {
            "rowid": int(row[0]),
            "raw_id": str(row[1] or "").strip(),
            "legacy_id": str(row[2] or "").strip(),
            "numeric_id": int(row[3] or 0),
        }
        for row in rows
        if str(row[2] or "").strip()
    ]


def _migration_assign_pdfdocument_numeric_ids(session: Session) -> list[dict[str, Any]]:
    from backend.models import ResourceIdentity

    ResourceIdentity.__table__.create(session.get_bind(), checkfirst=True)
    rows = _migration_pdfdocument_rows(session)
    identity_rows = session.exec(
        text("SELECT id, legacy_pk FROM resourceidentity WHERE resource_type = 'pdf'")
    ).all()
    identity_by_legacy = {
        str(row[1] or "").strip(): int(row[0])
        for row in identity_rows
        if str(row[1] or "").strip() and int(row[0] or 0) > 0
    }
    used_ids = _migration_used_global_resource_ids_except(
        session,
        resource_type="pdf",
        excluded_table_name="pdfdocument",
    )
    assigned_ids: set[int] = set()
    now = time.time()
    assignments: list[dict[str, Any]] = []

    for row in rows:
        legacy_id = str(row["legacy_id"])
        current_numeric_id = int(row["numeric_id"] or 0)
        identity_id = int(identity_by_legacy.get(legacy_id) or 0)
        if identity_id > 0 and identity_id not in used_ids and identity_id not in assigned_ids:
            numeric_id = identity_id
        elif current_numeric_id > 0 and current_numeric_id not in used_ids and current_numeric_id not in assigned_ids:
            numeric_id = current_numeric_id
        else:
            numeric_id = _migration_next_available_id(used_ids | assigned_ids)

        assigned_ids.add(numeric_id)
        session.execute(
            text(
                """
                UPDATE pdfdocument
                SET numeric_id = :numeric_id,
                    legacy_id = :legacy_id
                WHERE rowid = :rowid
                """
            ),
            {"numeric_id": numeric_id, "legacy_id": legacy_id, "rowid": int(row["rowid"])},
        )
        if identity_id <= 0:
            _migration_insert_resource_identity(
                session,
                resource_id=numeric_id,
                resource_type="pdf",
                legacy_pk=legacy_id,
                now=now,
            )
        elif identity_id != numeric_id:
            session.execute(
                text(
                    """
                    UPDATE resourceidentity
                    SET id = :numeric_id,
                        updated_at = :updated_at
                    WHERE resource_type = 'pdf'
                      AND legacy_pk = :legacy_id
                    """
                ),
                {"numeric_id": numeric_id, "updated_at": now, "legacy_id": legacy_id},
            )
        assignments.append({
            **row,
            "numeric_id": numeric_id,
            "old_numeric_id": current_numeric_id,
        })
    return assignments


def _migration_rebuild_pdfdocument_with_numeric_pk(session: Session) -> None:
    if _migration_pdfdocument_has_numeric_primary_key(session):
        return

    session.exec(text("DROP TABLE IF EXISTS pdfdocument_v56"))
    session.exec(
        text(
            """
            CREATE TABLE pdfdocument_v56 (
                id INTEGER NOT NULL PRIMARY KEY,
                numeric_id INTEGER,
                legacy_id VARCHAR,
                title VARCHAR NOT NULL,
                source_device_file_id INTEGER,
                source_entry_id VARCHAR NOT NULL,
                source_device_id VARCHAR NOT NULL,
                source_absolute_path VARCHAR NOT NULL,
                mime_type VARCHAR NOT NULL,
                size_bytes INTEGER,
                content_hash VARCHAR,
                hash_algorithm VARCHAR NOT NULL,
                owner_user_id INTEGER NOT NULL,
                created_by_user_id INTEGER,
                updated_by_user_id INTEGER,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                FOREIGN KEY(source_device_file_id) REFERENCES devicefile (id),
                FOREIGN KEY(owner_user_id) REFERENCES user (id),
                FOREIGN KEY(created_by_user_id) REFERENCES user (id),
                FOREIGN KEY(updated_by_user_id) REFERENCES user (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            INSERT INTO pdfdocument_v56 (
                id, numeric_id, legacy_id, title, source_device_file_id,
                source_entry_id, source_device_id, source_absolute_path,
                mime_type, size_bytes, content_hash, hash_algorithm,
                owner_user_id, created_by_user_id, updated_by_user_id,
                created_at, updated_at
            )
            SELECT
                CAST(numeric_id AS INTEGER),
                CAST(numeric_id AS INTEGER),
                legacy_id,
                COALESCE(title, ''),
                source_device_file_id,
                COALESCE(source_entry_id, ''),
                COALESCE(source_device_id, ''),
                COALESCE(source_absolute_path, ''),
                COALESCE(mime_type, 'application/pdf'),
                size_bytes,
                content_hash,
                COALESCE(hash_algorithm, 'sha256'),
                owner_user_id,
                created_by_user_id,
                updated_by_user_id,
                COALESCE(created_at, 0),
                COALESCE(updated_at, 0)
            FROM pdfdocument
            WHERE numeric_id IS NOT NULL AND numeric_id > 0
            """
        )
    )
    session.exec(text("ALTER TABLE pdfdocument RENAME TO pdfdocument_legacy_v56"))
    session.exec(text("ALTER TABLE pdfdocument_v56 RENAME TO pdfdocument"))
    session.exec(text("DROP TABLE pdfdocument_legacy_v56"))


def _migration_recreate_pdfdocument_indexes(session: Session) -> None:
    if not _table_exists(session, "pdfdocument"):
        return
    session.exec(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pdfdocument_owner_source
            ON pdfdocument (owner_user_id, source_device_id, source_absolute_path)
            """
        )
    )
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_pdfdocument_numeric_id ON pdfdocument (numeric_id)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_pdfdocument_legacy_id ON pdfdocument (legacy_id)"))
    for column_name in (
        "source_device_file_id",
        "source_entry_id",
        "source_device_id",
        "source_absolute_path",
        "mime_type",
        "size_bytes",
        "content_hash",
        "owner_user_id",
        "created_by_user_id",
        "updated_by_user_id",
    ):
        session.exec(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_pdfdocument_{column_name} "
                f"ON pdfdocument ({column_name})"
            )
        )


def v56_migrate_pdfdocument_primary_key_to_numeric(session: Session):
    """
    Migration V56: Make pdfdocument.id the numeric global resource id.
    """
    print("Running System Upgrade V56: Migrate PDF documents to numeric primary keys...")
    if not _table_exists(session, "pdfdocument"):
        print("  PDF document table missing, skipping.")
        return

    _migration_drop_readable_views(session)
    columns = _get_table_columns(session, "pdfdocument")
    if "numeric_id" not in columns:
        session.exec(text("ALTER TABLE pdfdocument ADD COLUMN numeric_id INTEGER"))
        columns.add("numeric_id")
    if "legacy_id" not in columns:
        session.exec(text("ALTER TABLE pdfdocument ADD COLUMN legacy_id VARCHAR"))
        columns.add("legacy_id")
    session.exec(
        text(
            """
            UPDATE pdfdocument
            SET legacy_id = CAST(id AS TEXT)
            WHERE legacy_id IS NULL OR legacy_id = ''
            """
        )
    )

    session.exec(text("DROP INDEX IF EXISTS ux_pdfdocument_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_pdfdocument_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ux_pdfdocument_legacy_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_pdfdocument_legacy_id"))
    session.exec(text("DROP INDEX IF EXISTS uq_pdfdocument_owner_source"))

    assignments = _migration_assign_pdfdocument_numeric_ids(session)
    id_map: dict[str, str] = {}
    for item in assignments:
        public_id = str(int(item["numeric_id"]))
        legacy_id = str(item["legacy_id"])
        raw_id = str(item["raw_id"])
        if legacy_id and legacy_id != public_id:
            id_map[legacy_id] = public_id
        if raw_id and raw_id != public_id:
            id_map[raw_id] = public_id
        old_numeric_id = int(item["old_numeric_id"] or 0)
        if old_numeric_id > 0 and old_numeric_id != int(item["numeric_id"]):
            id_map[str(old_numeric_id)] = public_id

    updated_refs = 0
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="pdfuserstate",
        column_name="pdf_document_id",
        id_map=id_map,
    )
    updated_refs += _migration_update_public_ref_column(
        session,
        table_name="pdfpagenote",
        column_name="pdf_document_id",
        id_map=id_map,
    )
    updated_refs += _migration_update_resource_grants_for_public_id_changes(
        session,
        {"pdf": id_map},
    )

    _migration_rebuild_pdfdocument_with_numeric_pk(session)
    _migration_recreate_pdfdocument_indexes(session)
    session.commit()
    print(
        "  Migrated PDF documents to numeric primary keys; "
        f"updated {updated_refs} refs."
    )


def _migration_backfill_legacy_id_column(session: Session, table_name: str) -> int:
    if not _table_exists(session, table_name):
        return 0
    columns = _get_table_columns(session, table_name)
    if "legacy_id" not in columns:
        session.exec(text(f"ALTER TABLE {table_name} ADD COLUMN legacy_id VARCHAR"))
    result = session.execute(
        text(
            f"""
            UPDATE {table_name}
            SET legacy_id = CAST(id AS TEXT)
            WHERE legacy_id IS NULL OR legacy_id = ''
            """
        )
    )
    session.exec(text(f"DROP INDEX IF EXISTS ux_{table_name}_legacy_id"))
    session.exec(text(f"DROP INDEX IF EXISTS ix_{table_name}_legacy_id"))
    session.exec(text(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{table_name}_legacy_id ON {table_name} (legacy_id)"))
    return int(result.rowcount or 0)


def v57_add_legacy_id_shadow_columns(session: Session):
    """
    Migration V57: Add legacy_id shadow columns before migrating high-risk resource primary keys.
    """
    print("Running System Upgrade V57: Add legacy id shadow columns...")
    updated_counts = {
        table_name: _migration_backfill_legacy_id_column(session, table_name)
        for table_name in ("sheetdocument", "workbookdocument", "notenode")
    }
    session.commit()
    print(
        "  Backfilled legacy ids: "
        + ", ".join(f"{table}={count}" for table, count in updated_counts.items())
    )


def _migration_note_ref_to_public_id_map(session: Session) -> dict[str, str]:
    if not _table_exists(session, "notenode"):
        return {}
    columns = _get_table_columns(session, "notenode")
    if "numeric_id" not in columns:
        return {}
    legacy_expr = "legacy_id" if "legacy_id" in columns else "NULL"
    rows = session.exec(
        text(
            f"""
            SELECT CAST(id AS TEXT) AS raw_id,
                   CAST(numeric_id AS INTEGER) AS numeric_id,
                   {legacy_expr} AS legacy_id
            FROM notenode
            WHERE numeric_id IS NOT NULL AND numeric_id > 0
            """
        )
    ).all()
    id_map: dict[str, str] = {}
    for row in rows:
        public_id = str(int(row[1]))
        for raw_ref in (row[0], row[2]):
            normalized_ref = str(raw_ref or "").strip()
            if normalized_ref and normalized_ref != public_id:
                id_map[normalized_ref] = public_id
    return id_map


def v58_migrate_fanxiu_inventory_note_refs(session: Session):
    """
    Migration V58: Convert Fanxiu inventory/activity JSON note_id refs to public numeric note ids.
    """
    print("Running System Upgrade V58: Migrate Fanxiu inventory note refs...")
    note_id_map = _migration_note_ref_to_public_id_map(session)
    if not note_id_map:
        print("  No note id map available, skipping.")
        return

    try:
        from backend.core.fanxiu.catalog.inventory import migrate_inventory_note_ids
    except Exception as exc:
        print(f"  Unable to load Fanxiu inventory storage helper, skipping: {exc}")
        return

    updated_count = migrate_inventory_note_ids(note_id_map)
    session.commit()
    print(f"  Migrated {updated_count} Fanxiu inventory note refs.")


def _migration_notenode_has_numeric_primary_key(session: Session) -> bool:
    column_types = _get_table_column_types(session, "notenode")
    return column_types.get("id", "").startswith("INTEGER")


def _migration_rebuild_notenode_with_numeric_pk(session: Session) -> None:
    if _migration_notenode_has_numeric_primary_key(session):
        return

    columns = _get_table_columns(session, "notenode")
    source_expr = {
        "user_id": "user_id" if "user_id" in columns else "1",
        "title": "title" if "title" in columns else "NULL",
        "content": "content" if "content" in columns else "''",
        "created_at": "created_at" if "created_at" in columns else "0",
        "updated_at": "updated_at" if "updated_at" in columns else "0",
        "weight": "weight" if "weight" in columns else "0",
        "start_at": "start_at" if "start_at" in columns else "NULL",
        "task_status": "task_status" if "task_status" in columns else "NULL",
        "history": "history" if "history" in columns else "'[]'",
        "node_type": "node_type" if "node_type" in columns else "NULL",
        "node_status": "node_status" if "node_status" in columns else "NULL",
        "custom_fields": "custom_fields" if "custom_fields" in columns else "'[]'",
        "private_level": "private_level" if "private_level" in columns else "0",
        "color": "color" if "color" in columns else "NULL",
        "note_kind": "note_kind" if "note_kind" in columns else "NULL",
        "weight_mode": "weight_mode" if "weight_mode" in columns else "NULL",
        "note_types": "note_types" if "note_types" in columns else "'[]'",
        "note_categories": "note_categories" if "note_categories" in columns else "'[]'",
        "primary_category": "primary_category" if "primary_category" in columns else "NULL",
        "note_form": "note_form" if "note_form" in columns else "NULL",
        "lifecycle_stage": "lifecycle_stage" if "lifecycle_stage" in columns else "NULL",
        "note_scene": "note_scene" if "note_scene" in columns else "NULL",
    }

    session.exec(text("DROP TABLE IF EXISTS notenode_v59"))
    session.exec(
        text(
            """
            CREATE TABLE notenode_v59 (
                id INTEGER NOT NULL PRIMARY KEY,
                numeric_id INTEGER,
                legacy_id VARCHAR,
                user_id INTEGER NOT NULL,
                title VARCHAR,
                content VARCHAR NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                weight INTEGER,
                start_at FLOAT,
                task_status VARCHAR,
                history JSON,
                node_type VARCHAR,
                node_status VARCHAR,
                custom_fields JSON,
                private_level INTEGER,
                color VARCHAR,
                note_kind VARCHAR,
                weight_mode VARCHAR,
                note_types JSON,
                note_categories JSON,
                primary_category VARCHAR,
                note_form VARCHAR,
                lifecycle_stage VARCHAR,
                note_scene VARCHAR,
                FOREIGN KEY(user_id) REFERENCES user (id)
            )
            """
        )
    )
    session.exec(
        text(
            f"""
            INSERT INTO notenode_v59 (
                id, numeric_id, legacy_id, user_id, title, content,
                created_at, updated_at, weight, start_at, task_status,
                history, node_type, node_status, custom_fields, private_level,
                color, note_kind, weight_mode, note_types, note_categories,
                primary_category, note_form, lifecycle_stage, note_scene
            )
            SELECT
                CAST(numeric_id AS INTEGER),
                CAST(numeric_id AS INTEGER),
                legacy_id,
                {source_expr["user_id"]},
                {source_expr["title"]},
                COALESCE({source_expr["content"]}, ''),
                COALESCE({source_expr["created_at"]}, 0),
                COALESCE({source_expr["updated_at"]}, 0),
                COALESCE({source_expr["weight"]}, 0),
                {source_expr["start_at"]},
                {source_expr["task_status"]},
                COALESCE({source_expr["history"]}, '[]'),
                {source_expr["node_type"]},
                {source_expr["node_status"]},
                COALESCE({source_expr["custom_fields"]}, '[]'),
                COALESCE({source_expr["private_level"]}, 0),
                {source_expr["color"]},
                {source_expr["note_kind"]},
                {source_expr["weight_mode"]},
                COALESCE({source_expr["note_types"]}, '[]'),
                COALESCE({source_expr["note_categories"]}, '[]'),
                {source_expr["primary_category"]},
                {source_expr["note_form"]},
                {source_expr["lifecycle_stage"]},
                {source_expr["note_scene"]}
            FROM notenode
            WHERE numeric_id IS NOT NULL AND numeric_id > 0
            """
        )
    )
    session.exec(text("ALTER TABLE notenode RENAME TO notenode_legacy_v59"))
    session.exec(text("ALTER TABLE notenode_v59 RENAME TO notenode"))
    session.exec(text("DROP TABLE notenode_legacy_v59"))


def _migration_recreate_notenode_indexes(session: Session) -> None:
    if not _table_exists(session, "notenode"):
        return
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_notenode_numeric_id ON notenode (numeric_id)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_notenode_legacy_id ON notenode (legacy_id)"))
    for column_name in (
        "user_id",
        "node_type",
        "primary_category",
        "note_form",
        "note_kind",
        "note_scene",
        "node_status",
        "lifecycle_stage",
        "weight_mode",
        "private_level",
    ):
        session.exec(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_notenode_{column_name} "
                f"ON notenode ({column_name})"
            )
        )


def v59_migrate_notenode_primary_key_to_numeric(session: Session):
    """
    Migration V59: Make notenode.id the numeric global resource id.
    """
    print("Running System Upgrade V59: Migrate note nodes to numeric primary keys...")
    if not _table_exists(session, "notenode"):
        print("  Note table missing, skipping.")
        return
    if _migration_notenode_has_numeric_primary_key(session):
        print("  Note table already uses numeric primary keys.")
        return

    _migration_drop_readable_views(session)
    columns = _get_table_columns(session, "notenode")
    if "numeric_id" not in columns:
        raise RuntimeError("notenode.numeric_id is required before numeric primary key migration")
    if "legacy_id" not in columns:
        session.exec(text("ALTER TABLE notenode ADD COLUMN legacy_id VARCHAR"))
        columns.add("legacy_id")
    session.exec(
        text(
            """
            UPDATE notenode
            SET legacy_id = CAST(id AS TEXT)
            WHERE legacy_id IS NULL OR legacy_id = ''
            """
        )
    )
    missing_numeric = int(
        _first_scalar(
            session.exec(
                text("SELECT COUNT(*) FROM notenode WHERE numeric_id IS NULL OR numeric_id <= 0")
            ).first()
        )
        or 0
    )
    if missing_numeric:
        raise RuntimeError(f"notenode contains {missing_numeric} rows without numeric_id")

    session.exec(text("DROP INDEX IF EXISTS ux_notenode_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_notenode_numeric_id"))
    session.exec(text("DROP INDEX IF EXISTS ux_notenode_legacy_id"))
    session.exec(text("DROP INDEX IF EXISTS ix_notenode_legacy_id"))

    _migration_rebuild_notenode_with_numeric_pk(session)
    _migration_recreate_notenode_indexes(session)
    session.commit()
    print("  Migrated note nodes to numeric primary keys.")


def _migration_table_has_numeric_primary_key(session: Session, table_name: str) -> bool:
    column_types = _get_table_column_types(session, table_name)
    return column_types.get("id", "").startswith("INTEGER")


def _migration_rebuild_sheetdocument_with_numeric_pk(session: Session) -> None:
    if _migration_table_has_numeric_primary_key(session, "sheetdocument"):
        return
    session.exec(text("DROP TABLE IF EXISTS sheetdocument_v60"))
    session.exec(
        text(
            """
            CREATE TABLE sheetdocument_v60 (
                id INTEGER NOT NULL PRIMARY KEY,
                numeric_id INTEGER,
                legacy_id VARCHAR,
                scope VARCHAR NOT NULL,
                owner_type VARCHAR NOT NULL,
                owner_key VARCHAR NOT NULL,
                sheet_key VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                engine VARCHAR NOT NULL,
                document_json JSON,
                version INTEGER NOT NULL,
                owner_user_id INTEGER,
                created_by_user_id INTEGER,
                updated_by_user_id INTEGER,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES user (id),
                FOREIGN KEY(created_by_user_id) REFERENCES user (id),
                FOREIGN KEY(updated_by_user_id) REFERENCES user (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            INSERT INTO sheetdocument_v60 (
                id, numeric_id, legacy_id, scope, owner_type, owner_key, sheet_key,
                title, engine, document_json, version, owner_user_id,
                created_by_user_id, updated_by_user_id, created_at, updated_at
            )
            SELECT
                CAST(numeric_id AS INTEGER),
                CAST(numeric_id AS INTEGER),
                legacy_id,
                COALESCE(scope, ''),
                COALESCE(owner_type, ''),
                COALESCE(owner_key, ''),
                COALESCE(sheet_key, ''),
                COALESCE(title, ''),
                COALESCE(engine, 'handsontable'),
                COALESCE(document_json, '{}'),
                COALESCE(version, 1),
                owner_user_id,
                created_by_user_id,
                updated_by_user_id,
                COALESCE(created_at, 0),
                COALESCE(updated_at, 0)
            FROM sheetdocument
            WHERE numeric_id IS NOT NULL AND numeric_id > 0
            """
        )
    )
    session.exec(text("ALTER TABLE sheetdocument RENAME TO sheetdocument_legacy_v60"))
    session.exec(text("ALTER TABLE sheetdocument_v60 RENAME TO sheetdocument"))
    session.exec(text("DROP TABLE sheetdocument_legacy_v60"))


def _migration_rebuild_workbookdocument_with_numeric_pk(session: Session) -> None:
    if _migration_table_has_numeric_primary_key(session, "workbookdocument"):
        return
    session.exec(text("DROP TABLE IF EXISTS workbookdocument_v60"))
    session.exec(
        text(
            """
            CREATE TABLE workbookdocument_v60 (
                id INTEGER NOT NULL PRIMARY KEY,
                numeric_id INTEGER,
                legacy_id VARCHAR,
                title VARCHAR NOT NULL,
                owner_user_id INTEGER,
                created_by_user_id INTEGER,
                updated_by_user_id INTEGER,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES user (id),
                FOREIGN KEY(created_by_user_id) REFERENCES user (id),
                FOREIGN KEY(updated_by_user_id) REFERENCES user (id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            INSERT INTO workbookdocument_v60 (
                id, numeric_id, legacy_id, title, owner_user_id,
                created_by_user_id, updated_by_user_id, created_at, updated_at
            )
            SELECT
                CAST(numeric_id AS INTEGER),
                CAST(numeric_id AS INTEGER),
                legacy_id,
                COALESCE(title, ''),
                owner_user_id,
                created_by_user_id,
                updated_by_user_id,
                COALESCE(created_at, 0),
                COALESCE(updated_at, 0)
            FROM workbookdocument
            WHERE numeric_id IS NOT NULL AND numeric_id > 0
            """
        )
    )
    session.exec(text("ALTER TABLE workbookdocument RENAME TO workbookdocument_legacy_v60"))
    session.exec(text("ALTER TABLE workbookdocument_v60 RENAME TO workbookdocument"))
    session.exec(text("DROP TABLE workbookdocument_legacy_v60"))


def _migration_recreate_sheet_workbook_indexes(session: Session) -> None:
    if _table_exists(session, "sheetdocument"):
        session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_sheetdocument_numeric_id ON sheetdocument (numeric_id)"))
        session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_sheetdocument_legacy_id ON sheetdocument (legacy_id)"))
        session.exec(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_sheetdocument_owner_locator "
                "ON sheetdocument (scope, owner_type, owner_key, sheet_key)"
            )
        )
        for column_name in (
            "scope",
            "owner_type",
            "owner_key",
            "sheet_key",
            "engine",
            "owner_user_id",
            "created_by_user_id",
            "updated_by_user_id",
        ):
            session.exec(text(f"CREATE INDEX IF NOT EXISTS ix_sheetdocument_{column_name} ON sheetdocument ({column_name})"))
    if _table_exists(session, "workbookdocument"):
        session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_workbookdocument_numeric_id ON workbookdocument (numeric_id)"))
        session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_workbookdocument_legacy_id ON workbookdocument (legacy_id)"))
        for column_name in ("owner_user_id", "created_by_user_id", "updated_by_user_id"):
            session.exec(text(f"CREATE INDEX IF NOT EXISTS ix_workbookdocument_{column_name} ON workbookdocument ({column_name})"))


def _migration_prepare_numeric_pk_owner_table(session: Session, table_name: str) -> None:
    columns = _get_table_columns(session, table_name)
    if "numeric_id" not in columns:
        raise RuntimeError(f"{table_name}.numeric_id is required before numeric primary key migration")
    if "legacy_id" not in columns:
        session.exec(text(f"ALTER TABLE {table_name} ADD COLUMN legacy_id VARCHAR"))
        columns.add("legacy_id")
    session.exec(
        text(
            f"""
            UPDATE {table_name}
            SET legacy_id = CAST(id AS TEXT)
            WHERE legacy_id IS NULL OR legacy_id = ''
            """
        )
    )
    missing_numeric = int(
        _first_scalar(
            session.exec(
                text(f"SELECT COUNT(*) FROM {table_name} WHERE numeric_id IS NULL OR numeric_id <= 0")
            ).first()
        )
        or 0
    )
    if missing_numeric:
        raise RuntimeError(f"{table_name} contains {missing_numeric} rows without numeric_id")


def v60_migrate_sheet_workbook_primary_keys_to_numeric(session: Session):
    """
    Migration V60: Make sheet/workbook primary keys numeric while preserving public route ids.
    """
    print("Running System Upgrade V60: Migrate sheet/workbook primary keys to numeric ids...")
    if not _table_exists(session, "sheetdocument") and not _table_exists(session, "workbookdocument"):
        print("  Sheet/workbook tables missing, skipping.")
        return

    _migration_drop_readable_views(session)
    for table_name in ("sheetdocument", "workbookdocument"):
        if _table_exists(session, table_name):
            _migration_prepare_numeric_pk_owner_table(session, table_name)
            session.exec(text(f"DROP INDEX IF EXISTS ux_{table_name}_numeric_id"))
            session.exec(text(f"DROP INDEX IF EXISTS ix_{table_name}_numeric_id"))
            session.exec(text(f"DROP INDEX IF EXISTS ux_{table_name}_legacy_id"))
            session.exec(text(f"DROP INDEX IF EXISTS ix_{table_name}_legacy_id"))
    session.exec(text("DROP INDEX IF EXISTS ux_sheetdocument_owner_locator"))
    session.exec(text("DROP INDEX IF EXISTS uq_sheetdocument_owner_locator"))

    _migration_rebuild_sheetdocument_with_numeric_pk(session)
    _migration_rebuild_workbookdocument_with_numeric_pk(session)
    _migration_recreate_sheet_workbook_indexes(session)
    session.commit()
    print("  Migrated sheet/workbook primary keys to numeric ids.")


def _ensure_task_runtime_kind_column(session: Session) -> bool:
    if not _table_exists(session, "task"):
        return False
    columns = _get_table_columns(session, "task")
    changed = False
    if "runtime_kind" not in columns:
        session.exec(text('ALTER TABLE "task" ADD COLUMN runtime_kind VARCHAR'))
        changed = True
    session.exec(text('CREATE INDEX IF NOT EXISTS ix_task_runtime_kind ON "task" (runtime_kind)'))
    session.commit()
    return changed


def _ensure_task_schedule_policy_columns(session: Session) -> bool:
    if not _table_exists(session, "task"):
        return False
    columns = _get_table_columns(session, "task")
    changed = False
    if "schedule_policy" not in columns:
        session.exec(text('ALTER TABLE "task" ADD COLUMN schedule_policy JSON'))
        changed = True
    if "schedule_state" not in columns:
        session.exec(text("ALTER TABLE \"task\" ADD COLUMN schedule_state JSON NOT NULL DEFAULT '{}'"))
        changed = True
    session.commit()
    return changed


def _ensure_task_next_run_at_column(session: Session) -> bool:
    if not _table_exists(session, "task"):
        return False
    columns = _get_table_columns(session, "task")
    changed = False
    if "next_run_at" not in columns:
        session.exec(text('ALTER TABLE "task" ADD COLUMN next_run_at VARCHAR'))
        changed = True
    session.exec(text('CREATE INDEX IF NOT EXISTS ix_task_next_run_at ON "task" (next_run_at)'))
    session.commit()
    return changed


def v61_add_task_runtime_kind(session: Session):
    """
    Migration V61: Add explicit command runtime kind for service/job grouping.
    """
    print("Running System Upgrade V61: Add task runtime kind...")
    if not _table_exists(session, "task"):
        print("  Task table missing, skipping.")
        return
    if not _ensure_task_runtime_kind_column(session):
        print("  Column 'runtime_kind' already exists, skipping.")


def v62_add_task_schedule_policy(session: Session):
    """
    Migration V62: Add generic schedule policy/state columns for runtime units.
    """
    print("Running System Upgrade V62: Add task schedule policy...")
    if not _table_exists(session, "task"):
        print("  Task table missing, skipping.")
        return
    if not _ensure_task_schedule_policy_columns(session):
        print("  Columns 'schedule_policy' and 'schedule_state' already exist, skipping.")


def v63_add_task_next_run_at(session: Session):
    """
    Migration V63: Add canonical next run time for command tasks.
    """
    print("Running System Upgrade V63: Add task next_run_at...")
    if not _table_exists(session, "task"):
        print("  Task table missing, skipping.")
        return
    if not _ensure_task_next_run_at_column(session):
        print("  Column 'next_run_at' already exists, skipping.")


def v64_add_fanxiu_pseudocode_cards(session: Session):
    """
    Migration V64: Add persistent Fanxiu pseudo-code card table.
    """
    print("Running System Upgrade V64: Add Fanxiu pseudo-code card table...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS fanxiupseudocodecard (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                scope VARCHAR NOT NULL DEFAULT 'action',
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                enabled BOOLEAN NOT NULL DEFAULT 1,
                order_index INTEGER NOT NULL DEFAULT 0,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES user (id)
            )
            """
        )
    )
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_fanxiupseudocodecard_user_id ON fanxiupseudocodecard (user_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_fanxiupseudocodecard_scope ON fanxiupseudocodecard (scope)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_fanxiupseudocodecard_enabled ON fanxiupseudocodecard (enabled)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_fanxiupseudocodecard_order_index ON fanxiupseudocodecard (order_index)"))
    session.commit()


def _ensure_resource_trash_columns(session: Session) -> bool:
    changed = False
    for table_name in ("notenode", "sheetdocument", "workbookdocument"):
        if not _table_exists(session, table_name):
            continue
        columns = _get_table_columns(session, table_name)
        if "deleted_at" not in columns:
            session.exec(text(f"ALTER TABLE {table_name} ADD COLUMN deleted_at FLOAT"))
            changed = True
        if "deleted_by_user_id" not in columns:
            session.exec(text(f"ALTER TABLE {table_name} ADD COLUMN deleted_by_user_id INTEGER"))
            changed = True
        session.exec(text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_deleted_at ON {table_name} (deleted_at)"))
        session.exec(text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_deleted_by_user_id ON {table_name} (deleted_by_user_id)"))
    session.commit()
    return changed


def v65_add_resource_trash_columns(session: Session):
    """
    Migration V65: Add soft-delete markers for note resources.
    """
    print("Running System Upgrade V65: Add resource trash columns...")
    if not _ensure_resource_trash_columns(session):
        print("  Resource trash columns already exist, skipping.")


def v66_migrate_note_sheet_links_to_inline_cells(session: Session):
    """
    Migration V66: Move sheet hyperlinks from legacy metadata maps into the
    canonical inline cell shape: {"value": ..., "link": {"url": ...}}.
    """
    print("Running System Upgrade V66: Migrate note sheet links to inline cells...")
    if not _table_exists(session, "sheetdocument"):
        return

    from backend.core.notes.sheet_inline_links import canonicalize_sheet_document_inline_links

    rows = session.exec(text("SELECT id, document_json FROM sheetdocument")).all()
    updated = 0
    legacy_links = 0
    stripped_meta = 0
    now = time.time()

    for row in rows:
        row_id = row[0]
        document_json = _load_json_value(row[1], {})
        if not isinstance(document_json, dict):
            continue
        next_document, stats = canonicalize_sheet_document_inline_links(
            document_json,
            migrate_legacy_links=True,
            strip_legacy_links=True,
        )
        if not stats.get("changed"):
            continue
        updated += 1
        legacy_links += int(stats.get("legacy") or 0)
        stripped_meta += int(stats.get("stripped_meta") or 0)
        session.exec(
            text(
                """
                UPDATE sheetdocument
                SET document_json = :document_json,
                    version = COALESCE(version, 1) + 1,
                    updated_at = :updated_at
                WHERE id = :id
                """
            ),
            {
                "document_json": _dump_json_value(next_document),
                "updated_at": now,
                "id": row_id,
            },
        )

    session.commit()
    print(
        "V66 migrated "
        f"{updated} sheet documents; moved {legacy_links} legacy links; "
        f"stripped legacy link metadata from {stripped_meta} maps."
    )


def run_startup_schema_repairs(engine):
    """
    Apply small idempotent repairs required before the full migration chain can
    complete. These do not write version markers; normal migrations still record
    the canonical version when reached.
    """
    if engine.url.get_backend_name() != "sqlite":
        return
    with Session(engine) as session:
        _ensure_task_runtime_kind_column(session)
        _ensure_task_schedule_policy_columns(session)
        _ensure_task_next_run_at_column(session)
        _ensure_resource_trash_columns(session)


def v67_drop_fanxiu_region_data_tables(session: Session):
    """
    Migration V67: Remove retired Fanxiu region-data tables.
    """
    print("Running System Upgrade V67: Drop Fanxiu region-data tables...")
    for table_name in (
        "fanxiuregioncharacterrecord",
        "fanxiuregionserver",
        "fanxiuregionarea",
    ):
        session.exec(text(f"DROP TABLE IF EXISTS {table_name}"))
    session.commit()
    print("  Dropped Fanxiu region-data tables.")


def v68_add_fanxiu_player_profile_records(session: Session):
    """
    Migration V68: Add Fanxiu player profile packet-derived records.
    """
    print("Running System Upgrade V68: Add Fanxiu player profile records...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS fanxiuplayerprofilerecord (
                id VARCHAR PRIMARY KEY,
                packet_id VARCHAR NOT NULL,
                protocol VARCHAR NOT NULL DEFAULT '',
                source_kind VARCHAR NOT NULL DEFAULT '',
                role_id VARCHAR NOT NULL DEFAULT '',
                role_id_text VARCHAR NOT NULL DEFAULT '',
                name VARCHAR NOT NULL DEFAULT '',
                server INTEGER,
                region_number INTEGER,
                region_name VARCHAR NOT NULL DEFAULT '',
                server_order INTEGER,
                server_name VARCHAR NOT NULL DEFAULT '',
                attack_value FLOAT,
                attack_text VARCHAR NOT NULL DEFAULT '',
                captured_at VARCHAR NOT NULL DEFAULT '',
                captured_date VARCHAR NOT NULL DEFAULT '',
                battle_score FLOAT,
                battle_score_text VARCHAR NOT NULL DEFAULT '',
                special_attributes JSON DEFAULT '[]',
                immortal_attributes JSON DEFAULT '[]',
                combat_attributes JSON DEFAULT '[]',
                attributes JSON DEFAULT '[]',
                payload JSON DEFAULT '{}',
                evidence JSON DEFAULT '{}',
                created_at FLOAT NOT NULL DEFAULT 0,
                updated_at FLOAT NOT NULL DEFAULT 0,
                CONSTRAINT uq_fanxiuplayerprofilerecord_packet_id UNIQUE (packet_id)
            )
            """
        )
    )
    indexes = (
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_packet_id ON fanxiuplayerprofilerecord (packet_id)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_role_id_text ON fanxiuplayerprofilerecord (role_id_text)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_name ON fanxiuplayerprofilerecord (name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_region_name ON fanxiuplayerprofilerecord (region_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_server_name ON fanxiuplayerprofilerecord (server_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_captured_at ON fanxiuplayerprofilerecord (captured_at)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_captured_date ON fanxiuplayerprofilerecord (captured_date)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_attack_value ON fanxiuplayerprofilerecord (attack_value)",
    )
    for statement in indexes:
        session.exec(text(statement))
    session.commit()
    print("  Added Fanxiu player profile record table.")


def v69_add_fanxiu_packet_business_records(session: Session):
    """
    Migration V69: Add Fanxiu packet-derived business fact records.
    """
    print("Running System Upgrade V69: Add Fanxiu packet business records...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS fanxiupacketbusinessrecord (
                id VARCHAR PRIMARY KEY,
                domain VARCHAR NOT NULL,
                record_key VARCHAR NOT NULL,
                protocol VARCHAR NOT NULL DEFAULT '',
                packet_id VARCHAR NOT NULL DEFAULT '',
                source_kind VARCHAR NOT NULL DEFAULT '',
                entity_id VARCHAR NOT NULL DEFAULT '',
                entity_name VARCHAR NOT NULL DEFAULT '',
                captured_at VARCHAR NOT NULL DEFAULT '',
                captured_date VARCHAR NOT NULL DEFAULT '',
                payload JSON DEFAULT '{}',
                evidence JSON DEFAULT '{}',
                created_at FLOAT NOT NULL DEFAULT 0,
                updated_at FLOAT NOT NULL DEFAULT 0,
                CONSTRAINT uq_fanxiupacketbusinessrecord_domain_key UNIQUE (domain, record_key)
            )
            """
        )
    )
    indexes = (
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_domain ON fanxiupacketbusinessrecord (domain)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_record_key ON fanxiupacketbusinessrecord (record_key)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_protocol ON fanxiupacketbusinessrecord (protocol)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_packet_id ON fanxiupacketbusinessrecord (packet_id)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_source_kind ON fanxiupacketbusinessrecord (source_kind)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_entity_id ON fanxiupacketbusinessrecord (entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_entity_name ON fanxiupacketbusinessrecord (entity_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_captured_at ON fanxiupacketbusinessrecord (captured_at)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketbusinessrecord_captured_date ON fanxiupacketbusinessrecord (captured_date)",
    )
    for statement in indexes:
        session.exec(text(statement))
    session.commit()
    print("  Added Fanxiu packet business record table.")


def v70_add_fanxiu_player_profile_cultivation_fields(session: Session):
    """
    Migration V70: Add packet-derived cultivation fields to Fanxiu player profile records.
    """
    print("Running System Upgrade V70: Add Fanxiu player profile cultivation fields...")
    columns = _get_table_columns(session, "fanxiuplayerprofilerecord")
    if "cultivation_level" not in columns:
        session.exec(text("ALTER TABLE fanxiuplayerprofilerecord ADD COLUMN cultivation_level INTEGER"))
    if "cultivation_level_text" not in columns:
        session.exec(text("ALTER TABLE fanxiuplayerprofilerecord ADD COLUMN cultivation_level_text VARCHAR NOT NULL DEFAULT ''"))

    indexes = (
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_cultivation_level ON fanxiuplayerprofilerecord (cultivation_level)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiuplayerprofilerecord_cultivation_level_text ON fanxiuplayerprofilerecord (cultivation_level_text)",
    )
    for statement in indexes:
        session.exec(text(statement))

    realms = ("炼气", "筑基", "结丹", "元婴", "化神", "炼虚", "合体", "大乘", "真仙", "金仙")
    stages = ("前期", "中期", "后期")
    layers = ("壹", "贰", "叁", "肆", "伍", "陆", "柒", "捌", "玖", "拾")
    anchor_value = 201
    anchor_realm_index = realms.index("大乘")
    realm_span = len(stages) * 10

    def format_level(value: Any) -> str:
        try:
            numeric = int(float(value))
        except (TypeError, ValueError):
            return ""
        offset = numeric - anchor_value
        realm_index = anchor_realm_index + (offset // realm_span)
        if realm_index < 0 or realm_index >= len(realms):
            return str(numeric)
        within_realm = offset % realm_span
        stage_index = within_realm // 10
        layer_index = within_realm % 10
        return f"{realms[realm_index]}{stages[stage_index]}{layers[layer_index]}层"

    rows = session.exec(text("SELECT id, payload FROM fanxiuplayerprofilerecord")).all()
    updated = 0
    for row in rows:
        row_id = row[0]
        payload = _load_json_value(row[1], {})
        if not isinstance(payload, dict):
            continue
        raw_level = payload.get("cultivation_level", payload.get("level"))
        try:
            level = int(float(raw_level))
        except (TypeError, ValueError):
            continue
        level_text = str(payload.get("cultivation_level_text") or format_level(level))
        payload["cultivation_level"] = level
        payload["cultivation_level_text"] = level_text
        session.execute(
            text(
                """
                UPDATE fanxiuplayerprofilerecord
                SET cultivation_level = :level,
                    cultivation_level_text = :level_text,
                    payload = :payload
                WHERE id = :id
                """
            ),
            {"level": level, "level_text": level_text, "payload": json.dumps(payload, ensure_ascii=False), "id": row_id},
        )
        updated += 1

    session.commit()
    print(f"  Added Fanxiu player profile cultivation fields. Backfilled {updated} rows.")


def v71_add_notenode_calendar_time_indexes(session: Session):
    """
    Migration V71: Add note time indexes used by calendar/range scans.
    """
    print("Running System Upgrade V71: Add note calendar time indexes...")
    if not _table_exists(session, "notenode"):
        print("  notenode table not found, skipping.")
        return

    statements = (
        "CREATE INDEX IF NOT EXISTS ix_notenode_start_at ON notenode (start_at)",
        "CREATE INDEX IF NOT EXISTS ix_notenode_updated_at ON notenode (updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_notenode_created_at ON notenode (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_notenode_user_start_at ON notenode (user_id, start_at)",
        "CREATE INDEX IF NOT EXISTS ix_notenode_user_updated_at ON notenode (user_id, updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_notenode_user_created_at ON notenode (user_id, created_at)",
    )
    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print("  Added notenode calendar time indexes.")


def v72_add_noteedge_graph_lookup_indexes(session: Session):
    """
    Migration V72: Add composite edge indexes used by graph scan lookups.
    """
    print("Running System Upgrade V72: Add note edge graph lookup indexes...")
    if not _table_exists(session, "noteedge"):
        print("  noteedge table not found, skipping.")
        return

    statements = (
        "CREATE INDEX IF NOT EXISTS ix_noteedge_user_source_target ON noteedge (user_id, source_id, target_id)",
        "CREATE INDEX IF NOT EXISTS ix_noteedge_user_target_source ON noteedge (user_id, target_id, source_id)",
    )
    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print("  Added noteedge graph lookup indexes.")


def v73_add_fanxiu_packet_decoded_records(session: Session):
    """
    Migration V73: Add Fanxiu decoded packet plaintext records.
    """
    print("Running System Upgrade V73: Add Fanxiu packet decoded records...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS fanxiupacketdecodedrecord (
                id VARCHAR PRIMARY KEY,
                packet_id VARCHAR NOT NULL,
                record_id VARCHAR NOT NULL DEFAULT '',
                pcap_name VARCHAR NOT NULL DEFAULT '',
                capture_sha256 VARCHAR NOT NULL DEFAULT '',
                stream INTEGER NOT NULL DEFAULT 0,
                direction VARCHAR NOT NULL DEFAULT '',
                frame_index INTEGER NOT NULL DEFAULT 0,
                offset INTEGER,
                sn INTEGER,
                pro_id INTEGER,
                name VARCHAR NOT NULL DEFAULT '',
                captured_at VARCHAR NOT NULL DEFAULT '',
                captured_date VARCHAR NOT NULL DEFAULT '',
                payload_len INTEGER,
                decode_error TEXT NOT NULL DEFAULT '',
                payload JSON DEFAULT '{}',
                evidence JSON DEFAULT '{}',
                created_at FLOAT NOT NULL DEFAULT 0,
                updated_at FLOAT NOT NULL DEFAULT 0,
                CONSTRAINT uq_fanxiupacketdecodedrecord_packet_id UNIQUE (packet_id)
            )
            """
        )
    )
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_packet_id ON fanxiupacketdecodedrecord (packet_id)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_record_id ON fanxiupacketdecodedrecord (record_id)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_pcap_name ON fanxiupacketdecodedrecord (pcap_name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_capture_sha256 ON fanxiupacketdecodedrecord (capture_sha256)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_stream ON fanxiupacketdecodedrecord (stream)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_direction ON fanxiupacketdecodedrecord (direction)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_frame_index ON fanxiupacketdecodedrecord (frame_index)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_offset ON fanxiupacketdecodedrecord (offset)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_sn ON fanxiupacketdecodedrecord (sn)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_pro_id ON fanxiupacketdecodedrecord (pro_id)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_name ON fanxiupacketdecodedrecord (name)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_captured_at ON fanxiupacketdecodedrecord (captured_at)",
        "CREATE INDEX IF NOT EXISTS ix_fanxiupacketdecodedrecord_captured_date ON fanxiupacketdecodedrecord (captured_date)",
    )
    for statement in statements:
        session.exec(text(statement))
    session.commit()
    print("  Added Fanxiu decoded packet record table.")


def v74_add_notenode_version(session: Session):
    """
    Migration V74: Add optimistic concurrency version to note documents.
    """
    print("Running System Upgrade V74: Add note node version...")
    if not _table_exists(session, "notenode"):
        print("  notenode table not found, skipping.")
        return
    columns = _get_table_columns(session, "notenode")
    if "version" not in columns:
        session.exec(text("ALTER TABLE notenode ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
    session.exec(text("UPDATE notenode SET version = 1 WHERE version IS NULL OR version <= 0"))
    session.commit()
    print("  Added notenode version column.")


def v75_add_codex_maintenance_feedback_table(session: Session):
    """
    Migration V75: Add Codex maintenance feedback table.
    """
    print("Running System Upgrade V75: Add Codex maintenance feedback table...")
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS codexmaintenancefeedback (
                id VARCHAR PRIMARY KEY NOT NULL,
                user_id INTEGER,
                status VARCHAR NOT NULL DEFAULT 'pending',
                source_kind VARCHAR NOT NULL DEFAULT '',
                source_ref_id VARCHAR NOT NULL DEFAULT '',
                source_date VARCHAR NOT NULL DEFAULT '',
                stage VARCHAR NOT NULL DEFAULT '',
                error_type VARCHAR NOT NULL DEFAULT '',
                error_message VARCHAR NOT NULL DEFAULT '',
                context_json JSON NOT NULL DEFAULT '{}',
                event_count INTEGER NOT NULL DEFAULT 1,
                consumer_run_id VARCHAR,
                first_event_at FLOAT NOT NULL,
                last_event_at FLOAT NOT NULL,
                consumed_at FLOAT,
                compressed_at FLOAT,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_user_id ON codexmaintenancefeedback (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_status ON codexmaintenancefeedback (status)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_source_kind ON codexmaintenancefeedback (source_kind)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_source_ref_id ON codexmaintenancefeedback (source_ref_id)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_source_date ON codexmaintenancefeedback (source_date)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_stage ON codexmaintenancefeedback (stage)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_error_type ON codexmaintenancefeedback (error_type)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_consumer_run_id ON codexmaintenancefeedback (consumer_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_first_event_at ON codexmaintenancefeedback (first_event_at)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_last_event_at ON codexmaintenancefeedback (last_event_at)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_consumed_at ON codexmaintenancefeedback (consumed_at)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_compressed_at ON codexmaintenancefeedback (compressed_at)",
        "CREATE INDEX IF NOT EXISTS ix_codexmaintenancefeedback_pending_lookup ON codexmaintenancefeedback (status, source_kind, source_ref_id)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added Codex maintenance feedback table.")


def v76_add_github_project_created_at(session: Session):
    """
    Migration V76: Add GitHub repository creation time to project pool.
    """
    print("Running System Upgrade V76: Add GitHub project created_at column...")
    if not _table_exists(session, "githubproject"):
        print("  githubproject table not found, skipping.")
        return
    columns = _get_table_columns(session, "githubproject")
    if "created_at_github" not in columns:
        session.exec(text("ALTER TABLE githubproject ADD COLUMN created_at_github VARCHAR NOT NULL DEFAULT ''"))
        session.exec(text("CREATE INDEX IF NOT EXISTS ix_githubproject_created_at_github ON githubproject (created_at_github)"))
    session.commit()
    print("  Added GitHub project created_at column.")


def v77_add_codex_diary_replace_existing(session: Session):
    """
    Migration V77: Track Codex diary reruns that replace existing day summaries.
    """
    print("Running System Upgrade V77: Add Codex diary replace_existing column...")
    if not _table_exists(session, "codexdiaryimportrun"):
        print("  codexdiaryimportrun table not found, skipping.")
        return
    columns = _get_table_columns(session, "codexdiaryimportrun")
    if "replace_existing" not in columns:
        session.exec(text("ALTER TABLE codexdiaryimportrun ADD COLUMN replace_existing BOOLEAN NOT NULL DEFAULT 0"))
        session.exec(text("CREATE INDEX IF NOT EXISTS ix_codexdiaryimportrun_replace_existing ON codexdiaryimportrun (replace_existing)"))
    session.commit()
    print("  Added Codex diary replace_existing column.")


def v78_add_device_agent_tables(session: Session):
    """
    Migration V78: Add Device Agent session and turn tables.
    """
    print("Running System Upgrade V78: Add Device Agent tables...")
    session.exec(text("""
        CREATE TABLE IF NOT EXISTS deviceagentsession (
            id VARCHAR NOT NULL,
            local_device_id VARCHAR NOT NULL DEFAULT '',
            peer_device_id VARCHAR NOT NULL DEFAULT '',
            peer_name VARCHAR NOT NULL DEFAULT '',
            requester_kind VARCHAR NOT NULL DEFAULT 'device',
            title VARCHAR NOT NULL DEFAULT '',
            status VARCHAR NOT NULL DEFAULT 'pending',
            last_turn_id VARCHAR,
            created_at FLOAT NOT NULL,
            updated_at FLOAT NOT NULL,
            PRIMARY KEY (id)
        )
    """))
    session.exec(text("""
        CREATE TABLE IF NOT EXISTS deviceagentturn (
            id VARCHAR NOT NULL,
            session_id VARCHAR NOT NULL,
            role VARCHAR NOT NULL DEFAULT 'requester',
            requester JSON NOT NULL DEFAULT '{}',
            request_type VARCHAR NOT NULL DEFAULT 'ask',
            instruction VARCHAR NOT NULL DEFAULT '',
            context JSON NOT NULL DEFAULT '{}',
            status VARCHAR NOT NULL DEFAULT 'pending',
            stage VARCHAR NOT NULL DEFAULT 'pending',
            stage_label VARCHAR NOT NULL DEFAULT '等待中',
            queue_task_id VARCHAR,
            heartbeat_at FLOAT,
            result_report JSON NOT NULL DEFAULT '{}',
            error_message VARCHAR,
            created_at FLOAT NOT NULL,
            started_at FLOAT,
            finished_at FLOAT,
            updated_at FLOAT NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(session_id) REFERENCES deviceagentsession (id)
        )
    """))
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_deviceagentsession_local_device_id ON deviceagentsession (local_device_id)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentsession_peer_device_id ON deviceagentsession (peer_device_id)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentsession_requester_kind ON deviceagentsession (requester_kind)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentsession_status ON deviceagentsession (status)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentsession_last_turn_id ON deviceagentsession (last_turn_id)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentsession_created_at ON deviceagentsession (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentsession_updated_at ON deviceagentsession (updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_session_id ON deviceagentturn (session_id)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_role ON deviceagentturn (role)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_request_type ON deviceagentturn (request_type)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_status ON deviceagentturn (status)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_stage ON deviceagentturn (stage)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_queue_task_id ON deviceagentturn (queue_task_id)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_heartbeat_at ON deviceagentturn (heartbeat_at)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_created_at ON deviceagentturn (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_started_at ON deviceagentturn (started_at)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_finished_at ON deviceagentturn (finished_at)",
        "CREATE INDEX IF NOT EXISTS ix_deviceagentturn_updated_at ON deviceagentturn (updated_at)",
    ):
        session.exec(text(statement))
    session.commit()
    print("  Added Device Agent tables.")


# --- Migration Registry ---
# List of (version, description, function)
MIGRATIONS = [
    (1, "Add node_type column", v1_add_node_type),
    (2, "Add node_status column", v2_add_node_status),
    (3, "Add custom_fields column", v3_add_custom_fields),
    (4, "Convert custom_fields to List", v4_migrate_custom_fields_to_list),
    (5, "Fix custom_fields List format", v5_fix_custom_fields_format),
    (6, "Add private_level column", v6_add_private_level),
    (7, "Migrate user device assets to userdeviceentry", v7_migrate_userdevice_entries),
    (8, "Backfill user device assets into userdeviceentry", v8_backfill_userdevice_entries),
    (9, "Add note color override column", v9_add_note_color),
    (10, "Add device file metadata table", v10_add_device_file_table),
    (11, "Upgrade device file table for rematching", v11_upgrade_device_file_identity_schema),
    (12, "Add device file cover cache columns", v12_add_device_file_cover_fields),
    (13, "Add device file media metadata columns", v13_add_device_file_metadata_fields),
    (14, "Add device file duration column", v14_add_device_file_duration_field),
    (15, "Add device file dimension columns", v15_add_device_file_dimensions_fields),
    (16, "Migrate note weights to integer levels", v16_migrate_note_weight_levels),
    (17, "Decouple note semantics from node_type", v17_add_note_semantics_fields),
    (18, "Add weighted note types", v18_add_note_types),
    (19, "Add naming-aligned note taxonomy fields", v19_add_note_taxonomy_fields),
    (20, "Repair note category drift", v20_repair_note_category_drift),
    (21, "Merge predone into done", v21_merge_predone_into_done),
    (22, "Add document reduction progress fields", v22_add_document_reduction_progress_fields),
    (23, "Add git reduction run table", v23_add_git_reduction_run_table),
    (24, "Add device file visual hash fields", v24_add_device_file_visual_hash_fields),
    (25, "Add user plaintext password field", v25_add_user_plain_password_field),
    (26, "Add user nickname field", v26_add_user_nickname_field),
    (27, "Add user phone field", v27_add_user_phone_field),
    (28, "Add sheetdocument owner_user_id field", v28_add_sheetdocument_owner_user_id),
    (30, "Add numeric sheet/workbook ids", v30_add_numeric_sheet_and_workbook_ids),
    (29, "Migrate attendance course sheets to notes workbook", v29_migrate_attendance_course_sheets_to_notes_workbook),
    (31, "Finalize numeric sheet/workbook id rollout", v31_finalize_numeric_sheet_id_rollout),
    (32, "Add codex daily summary run table", v32_add_codex_daily_summary_run_table),
    (33, "Add Fanxiu region data tables", v33_add_fanxiu_region_data_tables),
    (34, "Add Fanxiu region character cultivation level", v34_add_fanxiu_region_character_cultivation_level),
    (35, "Add resource access grant table", v35_add_resource_access_grant_table),
    (36, "Remove legacy attendance questionnaire config", v36_remove_legacy_attendance_questionnaire_config),
    (37, "Add note metadata feedback tables", v37_add_note_metadata_feedback_tables),
    (38, "Add auto git commit run table", v38_add_auto_git_commit_run_table),
    (39, "Add PDF page note table", v39_add_pdf_page_note_table),
    (40, "Add Eastmoney trade sync tables", v40_add_eastmoney_trade_sync_tables),
    (41, "Add Eastmoney trade detail fields", v41_add_eastmoney_trade_detail_fields),
    (42, "Add Eastmoney PDF statement tables", v42_add_eastmoney_pdf_statement_tables),
    (43, "Add service access token table", v43_add_service_access_token_table),
    (44, "Add note numeric ids", v44_add_note_numeric_ids),
    (45, "Add global resource identities", v45_add_global_resource_identity),
    (46, "Migrate resource JSON refs to public ids", v46_migrate_resource_json_refs_to_public_ids),
    (47, "Migrate internal resource refs to public ids", v47_migrate_internal_resource_refs_to_public_ids),
    (48, "Cleanup unmapped internal resource refs", v48_cleanup_unmapped_internal_resource_refs),
    (49, "Migrate graph/workbook refs to public ids", v49_migrate_graph_and_workbook_links_to_public_ids),
    (50, "Cleanup unmapped graph/workbook refs", v50_cleanup_unmapped_graph_and_workbook_refs),
    (51, "Repack resource ids by priority", v51_repack_resource_ids_by_priority),
    (52, "Restore workbook route ids", v52_restore_workbook_route_ids),
    (53, "Add device file resource identities", v53_add_device_file_resource_identities),
    (54, "Index attachment file resources", v54_index_attachment_file_resources),
    (55, "Migrate document asset primary keys to numeric ids", v55_migrate_documentasset_primary_key_to_numeric),
    (56, "Migrate PDF document primary keys to numeric ids", v56_migrate_pdfdocument_primary_key_to_numeric),
    (57, "Add legacy id shadow columns for high-risk resources", v57_add_legacy_id_shadow_columns),
    (58, "Migrate Fanxiu inventory note refs to public ids", v58_migrate_fanxiu_inventory_note_refs),
    (59, "Migrate note node primary keys to numeric ids", v59_migrate_notenode_primary_key_to_numeric),
    (60, "Migrate sheet/workbook primary keys to numeric route ids", v60_migrate_sheet_workbook_primary_keys_to_numeric),
    (61, "Add task runtime kind", v61_add_task_runtime_kind),
    (62, "Add task schedule policy", v62_add_task_schedule_policy),
    (63, "Add task next run time", v63_add_task_next_run_at),
    (64, "Add Fanxiu pseudo-code cards", v64_add_fanxiu_pseudocode_cards),
    (65, "Add resource trash columns", v65_add_resource_trash_columns),
    (66, "Migrate note sheet links to inline cells", v66_migrate_note_sheet_links_to_inline_cells),
    (67, "Drop Fanxiu region-data tables", v67_drop_fanxiu_region_data_tables),
    (68, "Add Fanxiu player profile records", v68_add_fanxiu_player_profile_records),
    (69, "Add Fanxiu packet business records", v69_add_fanxiu_packet_business_records),
    (70, "Add Fanxiu player profile cultivation fields", v70_add_fanxiu_player_profile_cultivation_fields),
    (71, "Add notenode calendar time indexes", v71_add_notenode_calendar_time_indexes),
    (72, "Add noteedge graph lookup indexes", v72_add_noteedge_graph_lookup_indexes),
    (73, "Add Fanxiu packet decoded records", v73_add_fanxiu_packet_decoded_records),
    (74, "Add note node version", v74_add_notenode_version),
    (75, "Add Codex maintenance feedback table", v75_add_codex_maintenance_feedback_table),
    (76, "Add GitHub project creation time", v76_add_github_project_created_at),
    (77, "Add Codex diary replace existing flag", v77_add_codex_diary_replace_existing),
    (78, "Add Device Agent tables", v78_add_device_agent_tables),
]

def get_current_version(session: Session) -> int:
    """
    Get the current system version.
    If the version table doesn't exist, try to infer version based on schema state
    to handle legacy databases gracefully.
    """
    # Check if SystemVersion table exists
    try:
        # Check if table exists first using raw SQL to avoid error log noise if not exists
        table_check = session.exec(text("SELECT name FROM sqlite_master WHERE type='table' AND name='system_version'")).first()
        
        # Compatibility: Check for old dbversion table and migrate if needed
        if not table_check:
             old_table_check = session.exec(text("SELECT name FROM sqlite_master WHERE type='table' AND name='dbversion'")).first()
             if old_table_check:
                 print("Migrating legacy 'dbversion' table to 'system_version'...")
                 # Read old version
                 old_ver_res = session.exec(text("SELECT version FROM dbversion ORDER BY version DESC LIMIT 1")).first()
                 old_ver = old_ver_res[0] if old_ver_res else 0
                 
                 # Create new table manually or let SQLModel do it later?
                 # It's better to return the old version, and let run_migrations create the new table
                 # and then we can insert the record.
                 # But get_current_version is read-only usually.
                 # Let's return the old version, and we'll handle the table creation in run_migrations.
                 return old_ver

        if not table_check:
            raise Exception("Table not found")

        statement = select(SystemVersion).order_by(SystemVersion.version.desc())
        result = session.exec(statement).first()
        if result:
            return result.version
        return 0
    except Exception:
        # Table likely doesn't exist.
        # Let's inspect the 'notenode' table to guess the version.
        try:
            res = session.exec(text("PRAGMA table_info(notenode)")).all()
            if not res:
                return 0 # Table doesn't exist, fresh DB
            
            columns = [row[1] for row in res]
            
            inferred_version = 0
            if "node_type" in columns:
                inferred_version = 1
            if "node_status" in columns:
                inferred_version = 2
            if "custom_fields" in columns:
                inferred_version = 3
            if "private_level" in columns:
                inferred_version = 6
            if "color" in columns:
                inferred_version = 9
            if "note_kind" in columns and "weight_mode" in columns:
                inferred_version = 17
            if "note_types" in columns:
                inferred_version = 18

            print(f"Inferred legacy System version: {inferred_version}")
            return inferred_version
            
        except Exception as e:
            print(f"Error inferring version: {e}")
            return 0

def run_migrations(engine):
    """
    Main entry point to run system upgrades.
    """
    # Ensure SystemVersion table exists
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        current_version = get_current_version(session)
        print(f"Current System Version: {current_version}")
        
        latest_version = MIGRATIONS[-1][0] if MIGRATIONS else 0
        
        if current_version >= latest_version:
            print("System is up to date.")
            # Ensure latest version is recorded in new table if we migrated from old table
            # Check if record exists in new table
            try:
                exists = session.exec(select(SystemVersion).where(SystemVersion.version == current_version)).first()
                if not exists and current_version > 0:
                     print(f"Syncing version {current_version} to new system_version table...")
                     session.add(SystemVersion(version=current_version, description="Synced from legacy dbversion"))
                     session.commit()
            except Exception:
                pass
            return

        print(f"Upgrading system from version {current_version} to {latest_version}...")
        
        for version, description, func in MIGRATIONS:
            if version > current_version:
                try:
                    print(f"Applying Upgrade V{version}: {description}...")
                    func(session)
                    
                    # Record the migration
                    sys_version = SystemVersion(version=version, description=description)
                    session.add(sys_version)
                    session.commit()
                    print(f"Successfully applied V{version}.")
                    
                except Exception as e:
                    print(f"Upgrade V{version} failed: {e}")
                    raise e # Stop migration on failure
        
        print("All system upgrades completed successfully.")
