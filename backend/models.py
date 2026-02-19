from typing import Optional, List
from sqlmodel import Field, SQLModel, Relationship
import time
import socket

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
    User's view of a device.
    Now contains ALL connection info and personalization.
    Replaces the global Device table concept for client-side usage.
    """
    __table_args__ = {'extend_existing': True}
    user_id: int = Field(primary_key=True, foreign_key="user.id")
    device_id: str = Field(primary_key=True) # Just a string ID, no FK to device table anymore
    
    name: str # Replaces 'alias', this is the user's name for the device
    type: str = Field(default="RemoteDevice") # 'LocalDevice' or 'RemoteDevice'
    url: Optional[str] = None # Connection URL
    token: str # Access Token
    
    is_active: bool = Field(default=True)
    order_index: int = Field(default=0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    
    # Deprecated fields (kept for migration safety if needed, or remove now?)
    # alias: Optional[str] = None 
    # Let's remove alias as we migrated it to name.

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

# --- Pydantic Models for API (Optional, if we want strict separation) ---
# For simplicity, we can reuse SQLModel classes as Pydantic models in FastAPI
