import sqlite3
import os
import shutil
import time

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "codeyun.db")
BACKUP_PATH = os.path.join(BASE_DIR, "data", f"codeyun.db.bak.{int(time.time())}.drop_device")

def drop_device_table():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    # 1. Backup
    print(f"Backing up database to {BACKUP_PATH}...")
    shutil.copy2(DB_PATH, BACKUP_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 2. Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='device'")
        if not cursor.fetchone():
            print("Table 'device' does not exist. Skipping.")
            return

        # 3. Drop table
        print("Dropping table 'device'...")
        cursor.execute("DROP TABLE device")
        
        conn.commit()
        print("Table 'device' dropped successfully!")
        
    except Exception as e:
        print(f"Failed to drop table: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    drop_device_table()
