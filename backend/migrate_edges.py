import sqlite3
import os

def migrate():
    # Database is in backend/data/codeyun.db
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, "data", "codeyun.db")
    if not os.path.exists(db_path):
        # Try relative to CWD if above fails
        db_path = os.path.join("backend", "data", "codeyun.db")
        if not os.path.exists(db_path):
            print(f"Database {db_path} not found.")
            return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Adding source_handle and target_handle to noteedge table...")
    try:
        cursor.execute("ALTER TABLE noteedge ADD COLUMN source_handle TEXT")
    except sqlite3.OperationalError:
        print("Column source_handle already exists.")
    
    try:
        cursor.execute("ALTER TABLE noteedge ADD COLUMN target_handle TEXT")
    except sqlite3.OperationalError:
        print("Column target_handle already exists.")

    conn.commit()
    conn.close()
    print("Migration finished.")

if __name__ == "__main__":
    migrate()
