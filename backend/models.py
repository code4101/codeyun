from typing import Optional, List
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column, JSON, String
import time
import socket
import uuid

# --- User Models ---

class User(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: Optional[str] = Field(default=None, index=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

# --- Device Models ---
# Removed global Device table as it is no longer used.
# UserDevice now contains all necessary information for connection and configuration.

class UserDevice(SQLModel, table=True):
    """
    User-owned connection entry to a device.
    Multiple entries may point at the same physical device_id.
    """
    __tablename__ = "userdeviceentry"
    __table_args__ = {'extend_existing': True}

    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    device_id: str = Field(index=True)

    name: str
    mode: str = Field(default="remote", index=True)  # 'local' or 'remote'
    server_url: Optional[str] = Field(
        default=None,
        sa_column=Column("url", String, nullable=True),
    )
    token: str

    is_active: bool = Field(default=True)
    order_index: int = Field(default=0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

# --- Task Models ---

class Task(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    id: str = Field(primary_key=True)
    name: str
    command: str
    cwd: Optional[str] = None
    
    description: Optional[str] = None
    device_id: str = Field(index=True) # Removed foreign key to device table
    schedule: Optional[str] = None 
    timeout: Optional[int] = None 
    order: Optional[int] = Field(default=0)
    created_at: float = Field(default_factory=time.time)


class TaskRuntime(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    task_id: str = Field(primary_key=True)
    device_id: str = Field(index=True)
    pid: Optional[int] = Field(default=None, index=True)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float = Field(default_factory=time.time)


class AppSetting(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    key: str = Field(primary_key=True)
    value: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: float = Field(default_factory=time.time)

# --- Note Models ---

class NoteNode(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    id: Optional[str] = Field(default=None, primary_key=True) # Using UUID string usually, or int? Frontend used string timestamp. Let's use string for flexibility.
    user_id: int = Field(foreign_key="user.id", index=True)
    title: Optional[str] = Field(default="Untitled")
    content: str = Field(default="") # HTML content
    
    # Weight for node size scaling (area based). Default 100.
    weight: int = Field(default=100)
    
    # Node type: project, module, task, bug, note, doc, memo
    node_type: Optional[str] = Field(default="note", index=True)

    # Node status: idea, todo, doing, predone, done, delete
    node_status: Optional[str] = Field(default="idea", index=True)

    # Private marker for doc-like notes. Kept as int to allow future levels.
    private_level: int = Field(default=0, index=True)
    
    # Visual coordinates for graph are dynamically calculated by frontend layout algorithm
    # No persistent storage for position in backend as requested.
    
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    start_at: float = Field(default_factory=time.time)
    
    # Operation logs: list of {"ts": int, "f": str, "v": any}
    history: List[dict] = Field(default=[], sa_column=Column(JSON))

    # Custom attributes: dictionary of key-value pairs
    custom_fields: dict = Field(default={}, sa_column=Column(JSON))

class NoteEdge(SQLModel, table=True):
    """
    Directed edge between two NoteNodes.
    """
    __table_args__ = {'extend_existing': True}
    id: Optional[str] = Field(default=None, primary_key=True) # UUID
    user_id: int = Field(foreign_key="user.id", index=True)
    
    source_id: str = Field(foreign_key="notenode.id", index=True)
    target_id: str = Field(foreign_key="notenode.id", index=True)
    
    label: Optional[str] = None # Edge label (optional)
    
    created_at: float = Field(default_factory=time.time)

# --- Pydantic Models for API (Optional, if we want strict separation) ---
# For simplicity, we can reuse SQLModel classes as Pydantic models in FastAPI
