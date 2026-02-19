from sqlmodel import SQLModel, create_engine, Session
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

def init_db():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
