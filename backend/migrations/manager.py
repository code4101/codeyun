import json
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

    from backend.core.note_semantics import NOTE_TYPE_DEFAULT, normalize_note_types

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

    from backend.core.note_semantics import derive_note_taxonomy_from_legacy

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
    from backend.core.note_semantics import (
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
