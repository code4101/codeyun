from typing import Optional, List, Any, Dict, Literal
from pydantic import BaseModel, ConfigDict, Field


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
    server_url: Optional[str] = None
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
    server_url: Optional[str] = None
    api_token: Optional[str] = None
    order_index: Optional[int] = None

# UserDevice Schemas
class UserDeviceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: int
    device_id: str
    mode: Literal["local", "remote"]
    alias: Optional[str] = None
    name: Optional[str] = None
    server_url: Optional[str] = None
    is_active: bool = True
    
class UserDeviceCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    mode: Literal["local", "remote"]
    device_id: Optional[str] = None
    token: str
    alias: Optional[str] = None
    name: Optional[str] = None
    server_url: Optional[str] = None

class UserDeviceRead(UserDeviceBase):
    token: str
    created_at: float
    updated_at: float
    device: Optional[DeviceRead] = None

class UserDeviceUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    token: Optional[str] = None
    alias: Optional[str] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None
    server_url: Optional[str] = None

# Note Schemas
class NoteCreate(BaseModel):
    title: str = "Untitled"
    content: str = ""
    weight: int = 100
    start_at: Optional[float] = None
    node_type: Optional[str] = "note"
    node_status: Optional[str] = "idea"
    private_level: int = 0
    custom_fields: Optional[List[List[Any]]] = []

class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    weight: Optional[int] = None
    start_at: Optional[float] = None
    node_type: Optional[str] = None
    node_status: Optional[str] = None
    private_level: Optional[int] = None
    custom_fields: Optional[List[List[Any]]] = None

class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: int
    title: str
    content: str
    weight: int = 100
    start_at: float
    created_at: float
    updated_at: float
    node_type: Optional[str] = None
    node_status: Optional[str] = None
    private_level: int = 0
    custom_fields: List[List[Any]] = []
    
    inherited_fields: Optional[Dict[str, List[List[Any]]]] = None 
    history: List[dict] = []
    edge_count: int = 0
    out_degree: int = 0

class NoteListRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: int
    title: str
    weight: int = 100
    node_type: Optional[str] = None
    node_status: Optional[str] = None
    private_level: int = 0
    custom_fields: List[List[Any]] = []
    created_at: float
    updated_at: float
    start_at: float
    history: List[dict] = []

class NoteFilterRule(BaseModel):
    field: str
    op: Literal["eq", "neq", "in", "not_in", "contains", "not_contains", "regex_search", "gte", "lte", "between"]
    value: Optional[Any] = None
    values: List[Any] = Field(default_factory=list)

class NoteQueryScope(BaseModel):
    mode: Literal["all", "planetary", "satellite"] = "all"
    seed_note_id: Optional[str] = None

class NoteQueryRequest(BaseModel):
    scope: NoteQueryScope = Field(default_factory=NoteQueryScope)
    rules: List[NoteFilterRule] = Field(default_factory=list)
    order_by: str = "updated_at"
    order_desc: bool = True
    skip: int = 0
    limit: int = 1000
    include_edges: bool = True

# Edge Schemas
class EdgeCreate(BaseModel):
    source_id: str
    target_id: str
    label: Optional[str] = None

class EdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: int
    source_id: str
    target_id: str
    label: Optional[str]
    created_at: float

class GraphData(BaseModel):
    nodes: List[NoteListRead]
    edges: List[EdgeRead]

class NoteQueryResponse(BaseModel):
    nodes: List[NoteListRead]
    edges: List[EdgeRead]
    total_nodes: int
    total_edges: int


class NoteTimePointExpr(BaseModel):
    kind: Literal["absolute", "relative"] = "absolute"
    value: Optional[float] = None
    unit: Literal["day", "week", "month"] = "month"
    offset: int = 0
    boundary: Literal["start", "end"] = "start"


class NoteProgramMatcher(BaseModel):
    kind: Literal["all", "none", "id", "field", "title_contains", "seed", "depth", "relative_month_window"]
    ids: List[str] = Field(default_factory=list)
    field: Optional[str] = None
    op: Optional[Literal["eq", "neq", "in", "not_in", "contains", "not_contains", "regex_search", "gte", "lte", "between"]] = None
    value: Optional[Any] = None
    values: List[Any] = Field(default_factory=list)
    ignore_case: bool = True
    min_depth: int = 0
    max_depth: Optional[int] = None
    start_month_offset: int = -1
    end_month_offset: int = 1
    time_value: Optional[NoteTimePointExpr] = None
    time_values: List[NoteTimePointExpr] = Field(default_factory=list)


class NoteProgramRule(BaseModel):
    action: Literal["include", "exclude"]
    matcher: NoteProgramMatcher


class NoteProgramChannel(BaseModel):
    default: bool = False
    rules: List[NoteProgramRule] = Field(default_factory=list)


class NoteProgramChannels(BaseModel):
    select: NoteProgramChannel = Field(default_factory=NoteProgramChannel)
    expand: NoteProgramChannel = Field(default_factory=NoteProgramChannel)


class NoteProgramExecutor(BaseModel):
    kind: Literal["scan", "component"] = "scan"
    seed_ids: List[str] = Field(default_factory=list)
    mode: Literal["planetary", "satellite"] = "planetary"
    max_depth: Optional[int] = None


class NoteProgramResultOptions(BaseModel):
    include_edges: bool = True
    order_by: str = "updated_at"
    order_desc: bool = True
    skip: int = 0
    limit: int = 1000


class NoteProgramRequest(BaseModel):
    executor: NoteProgramExecutor = Field(default_factory=NoteProgramExecutor)
    program: NoteProgramChannels = Field(default_factory=NoteProgramChannels)
    result: NoteProgramResultOptions = Field(default_factory=NoteProgramResultOptions)


class NoteProgramResponse(BaseModel):
    nodes: List[NoteListRead]
    edges: List[EdgeRead]
    total_nodes: int
    total_edges: int


class NoteBatchPatch(BaseModel):
    private_level: Optional[int] = None


class NoteBatchUpdateRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)
    patch: NoteBatchPatch = Field(default_factory=NoteBatchPatch)


class NoteBatchUpdateResponse(BaseModel):
    updated_count: int
    notes: List[NoteListRead]
