import os

from sqlmodel import SQLModel, Session, create_engine, text

from backend.core.settings import DATA_DIR as SETTINGS_DATA_DIR, get_settings

settings = get_settings()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.fspath(SETTINGS_DATA_DIR)

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

DATABASE_URL = settings.database_url

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

from backend.migrations.manager import run_migrations as migrate_db_manager

def migrate_db():
    """Perform automatic database migrations for schema changes."""
    print("Initializing Database Migration Manager...")
    try:
        migrate_db_manager(engine)
    except Exception as e:
        print(f"Migration Manager failed: {e}")

def init_db():
    SQLModel.metadata.create_all(engine)
    migrate_db()

def get_session():
    with Session(engine) as session:
        yield session
