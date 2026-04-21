import sqlite3
import shutil
import os
from backend.core.settings import get_settings

def migrate_old_db_data():
    settings = get_settings()
    # The new DB
    target_db_path = settings.database_url.replace("sqlite:///", "")
    
    # The old DB path (hardcoded or from env/args)
    old_db_path = r"c:\home\chenkunze\slns\codeyun\backend\data\codeyun.db"
    
    if not os.path.exists(old_db_path):
        print(f"Old DB not found at {old_db_path}")
        return

    print(f"Migrating from: {old_db_path}")
    print(f"To: {target_db_path}")
    
    conn = sqlite3.connect(target_db_path)
    cursor = conn.cursor()
    
    # Attach old DB
    cursor.execute(f"ATTACH DATABASE '{old_db_path}' AS old_db")
    
    # 1. Migrate Users (INSERT OR IGNORE based on unique constraint usually username)
    print("Migrating 'user' table...")
    try:
        # Check if target table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user'")
        if cursor.fetchone():
             # Assumes schema compatibility or handles missing columns by selecting specific ones if needed.
             # Simple approach: SELECT *
             cursor.execute("INSERT OR IGNORE INTO main.user SELECT * FROM old_db.user")
             print(f"  Users migrated (rows affected: {cursor.rowcount})")
        else:
             print("  Target 'user' table does not exist.")
    except Exception as e:
        print(f"  Error migrating users: {e}")

    # 2. Migrate UserDevice (Old table) -> UserDeviceEntry (New table)
    # The old DB has 'userdevice', the new one has 'userdeviceentry'.
    # We should use the logic from migration V7 in manager.py, but reading from attached DB.
    print("Migrating 'userdevice' to 'userdeviceentry'...")
    try:
        # Check if source table exists
        cursor.execute("SELECT name FROM old_db.sqlite_master WHERE type='table' AND name='userdevice'")
        if cursor.fetchone():
             # Read from old_db.userdevice
             cursor.execute("SELECT user_id, device_id, name, type, url, token, is_active, order_index, created_at, updated_at FROM old_db.userdevice")
             devices = cursor.fetchall()
             
             import uuid
             import time
             
             count = 0
             for row in devices:
                 user_id, device_id, name, type_, url, token, is_active, order_index, created_at, updated_at = row
                 
                 # Check if already exists in target
                 cursor.execute("SELECT 1 FROM main.userdeviceentry WHERE device_id = ?", (device_id,))
                 if cursor.fetchone():
                     continue
                     
                 mode = "local" if type_ == "LocalDevice" else "remote"
                 entry_id = str(uuid.uuid4())
                 
                 # Check columns in target table
                 # In models.py: server_url is mapped to sa_column=Column("url", String, nullable=True)
                 # So the column name in DB is likely "url", not "server_url"
                 
                 cursor.execute("PRAGMA main.table_info(userdeviceentry)")
                 cols = [c[1] for c in cursor.fetchall()]
                 url_col = "url" if "url" in cols else "server_url"
                 
                 sql = f"""
                    INSERT INTO main.userdeviceentry (
                        entry_id, user_id, device_id, name, mode, {url_col}, token,
                        is_active, order_index, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 """
                 
                 cursor.execute(sql, (
                     entry_id, user_id, device_id, name or device_id, mode, url, token,
                     is_active, order_index or 0, created_at or time.time(), updated_at or time.time()
                 ))
                 count += 1
             
             print(f"  User devices migrated: {count}")
        else:
             print("  Source 'userdevice' table not found in old DB.")
    except Exception as e:
        print(f"  Error migrating devices: {e}")

    # 3. Migrate Tasks (Merge)
    print("Migrating 'task' table...")
    try:
        # Check source table
        cursor.execute("SELECT name FROM old_db.sqlite_master WHERE type='table' AND name='task'")
        if cursor.fetchone():
             # Columns in old DB might differ slightly?
             # Old DB schema from check_tables.py: id, name, command, cwd, description, device_id, schedule, timeout, order, created_at
             # New DB schema from models.py: id, name, command, cwd, description, device_id, schedule, timeout, order, created_at
             # They look identical.
             
             cursor.execute("INSERT OR IGNORE INTO main.task SELECT * FROM old_db.task")
             print(f"  Tasks migrated (rows affected: {cursor.rowcount})")
        else:
             print("  Source 'task' table not found.")
    except Exception as e:
        print(f"  Error migrating tasks: {e}")
        
    # 4. Migrate NoteNodes
    print("Migrating 'notenode' table...")
    try:
        cursor.execute("SELECT name FROM old_db.sqlite_master WHERE type='table' AND name='notenode'")
        if cursor.fetchone():
             # Get columns from old and new to map correctly
             cursor.execute("PRAGMA old_db.table_info(notenode)")
             old_cols = [c[1] for c in cursor.fetchall()]
             
             cursor.execute("PRAGMA main.table_info(notenode)")
             new_cols = [c[1] for c in cursor.fetchall()]
             
             # Construct INSERT based on common columns
             common_cols = [c for c in old_cols if c in new_cols]
             cols_str = ", ".join(common_cols)
             placeholders = ", ".join(["?"] * len(common_cols))
             
             cursor.execute(f"SELECT {cols_str} FROM old_db.notenode")
             rows = cursor.fetchall()
             
             if rows:
                 cursor.executemany(f"INSERT OR IGNORE INTO main.notenode ({cols_str}) VALUES ({placeholders})", rows)
                 print(f"  Notes migrated (rows affected: {cursor.rowcount})")
             else:
                 print("  No notes found in old DB.")
    except Exception as e:
        print(f"  Error migrating notes: {e}")
        
    # 5. Migrate NoteEdges
    print("Migrating 'noteedge' table...")
    try:
        cursor.execute("SELECT name FROM old_db.sqlite_master WHERE type='table' AND name='noteedge'")
        if cursor.fetchone():
             cursor.execute("PRAGMA old_db.table_info(noteedge)")
             old_cols = [c[1] for c in cursor.fetchall()]
             
             cursor.execute("PRAGMA main.table_info(noteedge)")
             new_cols = [c[1] for c in cursor.fetchall()]
             
             common_cols = [c for c in old_cols if c in new_cols]
             cols_str = ", ".join(common_cols)
             placeholders = ", ".join(["?"] * len(common_cols))
             
             cursor.execute(f"SELECT {cols_str} FROM old_db.noteedge")
             rows = cursor.fetchall()
             
             if rows:
                 cursor.executemany(f"INSERT OR IGNORE INTO main.noteedge ({cols_str}) VALUES ({placeholders})", rows)
                 print(f"  Edges migrated (rows affected: {cursor.rowcount})")
             else:
                 print("  No edges found in old DB.")
    except Exception as e:
        print(f"  Error migrating edges: {e}")

    conn.commit()
    conn.close()
    print("Migration completed.")

if __name__ == "__main__":
    migrate_old_db_data()
