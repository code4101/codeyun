
import time
from typing import Optional
from sqlmodel import Field, SQLModel, Session, select, text
from sqlalchemy import create_engine

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

# --- Migration Registry ---
# List of (version, description, function)
MIGRATIONS = [
    (1, "Add node_type column", v1_add_node_type),
    (2, "Add node_status column", v2_add_node_status),
    (3, "Add custom_fields column", v3_add_custom_fields),
    (4, "Convert custom_fields to List", v4_migrate_custom_fields_to_list),
    (5, "Fix custom_fields List format", v5_fix_custom_fields_format),
    (6, "Add private_level column", v6_add_private_level),
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
