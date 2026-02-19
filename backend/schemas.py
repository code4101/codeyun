from typing import Optional, List
from pydantic import BaseModel, ConfigDict

class Token(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    access_token: str
    token_type: str
    refresh_token: Optional[str] = None

class TokenRefresh(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    refresh_token: str

class UserCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    username: str
    password: str
    email: Optional[str] = None

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool
    is_superuser: bool

class UserLogin(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    username: str
    password: str

# Device Schemas
class DeviceBase(BaseModel):
    name: str
    type: str = "RemoteDevice"
    url: Optional[str] = None
    api_token: Optional[str] = None # Only for admin/local use
    order_index: int = 0

class DeviceCreate(DeviceBase):
    pass

class DeviceRead(DeviceBase):
    id: str
    created_at: float
    updated_at: float

class DeviceUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: Optional[str] = None
    url: Optional[str] = None
    api_token: Optional[str] = None
    order_index: Optional[int] = None

# UserDevice Schemas
class UserDeviceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: int
    device_id: str
    alias: Optional[str] = None
    is_active: bool = True
    
class UserDeviceCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    device_id: str
    token: str # User provided token
    alias: Optional[str] = None
    url: Optional[str] = None # Allow passing URL when creating new device entry

class UserDeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: int
    device_id: str
    token: str
    alias: Optional[str] = None
    name: Optional[str] = None # Added for compatibility
    is_active: bool
    created_at: float
    updated_at: float
    device: Optional[DeviceRead] = None

class UserDeviceUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    token: Optional[str] = None
    alias: Optional[str] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None # For device renaming
