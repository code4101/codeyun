import os

from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine, text

from backend.core.settings import get_settings
import backend.models  # Ensure table metadata is registered before create_all.
from backend.maintenance.db_cleanup import cleanup_legacy_sqlite_artifacts
from backend.maintenance.db_views import refresh_sqlite_readable_views

settings = get_settings()
DATA_DIR = os.fspath(settings.data_dir)
settings.data_dir.mkdir(parents=True, exist_ok=True)

DATABASE_URL = settings.database_url

connect_args = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
pool_args = {}
if ":memory:" not in DATABASE_URL:
    pool_args = {
        "pool_pre_ping": True,
        "pool_size": int(os.getenv("CODEYUN_DB_POOL_SIZE", "20")),
        "max_overflow": int(os.getenv("CODEYUN_DB_MAX_OVERFLOW", "20")),
        "pool_timeout": int(os.getenv("CODEYUN_DB_POOL_TIMEOUT", "10")),
    }
engine = create_engine(DATABASE_URL, connect_args=connect_args, **pool_args)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000")
            if ":memory:" not in DATABASE_URL:
                cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

from backend.migrations.manager import (
    run_migrations as migrate_db_manager,
    run_startup_schema_repairs,
)

def migrate_db():
    """Perform automatic database migrations for schema changes."""
    print("Initializing Database Migration Manager...")
    try:
        run_startup_schema_repairs(engine)
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
