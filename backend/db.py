from sqlmodel import SQLModel, create_engine, Session, text
import os

# Database file path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Data directory inside backend folder (legacy location to preserve data)
DATA_DIR = os.path.join(BASE_DIR, "data")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

DB_FILE = os.path.join(DATA_DIR, "codeyun.db")
DATABASE_URL = f"sqlite:///{DB_FILE}"

# check_same_thread=False is needed for SQLite when used with FastAPI (multi-threaded)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def migrate_db():
    """Perform automatic database migrations for schema changes."""
    with Session(engine) as session:
        try:
            # Check columns of notenode table
            res = session.exec(text("PRAGMA table_info(notenode)")).all()
            if not res:
                return # Table doesn't exist yet, SQLModel will create it
            
            columns = [row[1] for row in res]
            
            # 1. Migrate task_status -> node_type
            if "node_type" not in columns and "task_status" in columns:
                print("Auto-migration: Adding 'node_type' column to 'notenode' table...")
                session.exec(text("ALTER TABLE notenode ADD COLUMN node_type VARCHAR"))
                session.commit()
                print("Auto-migration: Copying data from 'task_status' to 'node_type'...")
                session.exec(text("UPDATE notenode SET node_type = task_status WHERE node_type IS NULL"))
                session.commit()
                print("Auto-migration: Completed successfully.")
            
        except Exception as e:
            print(f"Auto-migration failed: {e}")

def init_db():
    SQLModel.metadata.create_all(engine)
    migrate_db()

def get_session():
    with Session(engine) as session:
        yield session
