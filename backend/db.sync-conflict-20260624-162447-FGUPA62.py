import os

from sqlmodel import SQLModel, Session, create_engine, text

from backend.core.settings import get_settings
import backend.models  # Ensure table metadata is registered before create_all.
from backend.db_cleanup import cleanup_legacy_sqlite_artifacts
from backend.db_views import refresh_sqlite_readable_views

settings = get_settings()
DATA_DIR = os.fspath(settings.data_dir)
settings.data_dir.mkdir(parents=True, exist_ok=True)

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
    cleanup_legacy_sqlite_artifacts(engine)
    refresh_sqlite_readable_views(engine)

def get_session():
    with Session(engine) as session:
        yield session
