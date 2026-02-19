import sqlite3
import os
import json
import shutil
import time

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "codeyun.db")
BACKUP_PATH = os.path.join(BASE_DIR, "data", f"codeyun.db.bak.{int(time.time())}")

def migrate_db():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    # 1. Backup
    print(f"Backing up database to {BACKUP_PATH}...")
    shutil.copy2(DB_PATH, BACKUP_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 2. Add columns to userdevice
        print("Adding new columns to userdevice...")
        try:
            cursor.execute("ALTER TABLE userdevice ADD COLUMN url TEXT")
        except sqlite3.OperationalError:
            print("Column 'url' might already exist.")
            
        try:
            cursor.execute("ALTER TABLE userdevice ADD COLUMN name TEXT")
        except sqlite3.OperationalError:
            print("Column 'name' might already exist.")

        try:
            cursor.execute("ALTER TABLE userdevice ADD COLUMN type TEXT DEFAULT 'RemoteDevice'")
        except sqlite3.OperationalError:
            print("Column 'type' might already exist.")

        # 3. Migrate data from device table
        print("Migrating data from device table...")
        
        # Get all userdevices
        cursor.execute("SELECT * FROM userdevice")
        user_devices = cursor.fetchall()
        
        for ud in user_devices:
            device_id = ud['device_id']
            user_id = ud['user_id']
            
            # Find corresponding device
            cursor.execute("SELECT * FROM device WHERE id = ?", (device_id,))
            device = cursor.fetchone()
            
            if device:
                # Merge data
                # Use alias if present, otherwise device name
                name = ud['alias'] if ud['alias'] else device['name']
                url = device['url']
                # python_exec = device['python_exec'] # Removed
                dev_type = device['type']
                
                print(f"Updating UserDevice {user_id}-{device_id}: name={name}, url={url}, type={dev_type}")
                
                cursor.execute("""
                    UPDATE userdevice 
                    SET url = ?, name = ?, type = ?
                    WHERE user_id = ? AND device_id = ?
                """, (url, name, dev_type, user_id, device_id))
        
        # 4. Handle orphaned devices? (Devices with no user)
        # We can ignore them as per new design, they are lost if no one claimed them.
        
        # 5. Commit
        conn.commit()
        print("Migration successful!")
        
        # Note: We are NOT dropping the 'device' table yet to be safe.
        # User can drop it manually later or we can do it in a cleanup script.
        
    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_db()
