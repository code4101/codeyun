from typing import Any, Optional, List
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column, JSON, String, Text, UniqueConstraint
import time
import socket
import uuid
import secrets


_SHEET_DOCUMENT_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def generate_sheet_document_id(length: int = 12) -> str:
    return "".join(secrets.choice(_SHEET_DOCUMENT_ID_ALPHABET) for _ in range(length))

# --- User Models ---

class User(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    nickname: str = Field(default="")
    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = Field(default=None, index=True)
    hashed_password: str
    password_plain: str = Field(default="未知")
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


class DeviceFile(SQLModel, table=True):
    """
    Track a logical file on a device, even if it later moves, renames, or
    temporarily loses a concrete path match.
    """
    __tablename__ = "devicefile"
    __table_args__ = (
        UniqueConstraint("device_id", "absolute_path", name="uq_devicefile_device_path"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    numeric_id: Optional[int] = Field(default=None, index=True, unique=True)
    device_id: str = Field(index=True)
    absolute_path: Optional[str] = Field(default=None, index=True)
    last_known_path: Optional[str] = Field(default=None, index=True)
    content_hash: Optional[str] = Field(default=None, index=True)
    hash_algorithm: str = Field(default="sha256")
    visual_hash: Optional[str] = Field(default=None)
    visual_hash_algorithm: str = Field(default="dhash-8")
    file_size: Optional[int] = Field(default=None, index=True)
    modified_at_ms: Optional[int] = Field(default=None, index=True)
    duration_ms: Optional[int] = Field(default=None, index=True)
    width_px: Optional[int] = Field(default=None)
    height_px: Optional[int] = Field(default=None)
    media_kind: Optional[str] = Field(default=None, index=True)
    mime_type: Optional[str] = Field(default=None)
    match_status: str = Field(default="matched", index=True)
    cover_path: Optional[str] = Field(default=None)
    cover_mime_type: Optional[str] = Field(default=None)
    cover_source: Optional[str] = Field(default=None, index=True)
    cover_updated_at: Optional[float] = None
    weight: int = Field(default=0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_seen_at: Optional[float] = None
    hash_updated_at: Optional[float] = None
    visual_hash_updated_at: Optional[float] = None

# --- Task Models ---

class Task(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    id: str = Field(primary_key=True)
    name: str
    command: str
    cwd: Optional[str] = None
    
    description: Optional[str] = None
    device_id: str = Field(index=True) # Removed foreign key to device table
    runtime_kind: Optional[str] = Field(default=None, index=True)
    schedule: Optional[str] = None 
    schedule_policy: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    schedule_state: dict = Field(default_factory=dict, sa_column=Column(JSON))
    next_run_at: Optional[str] = Field(default=None, index=True)
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


class ServiceAccessToken(SQLModel, table=True):
    __tablename__ = "serviceaccesstoken"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    label: str = Field(default="未命名服务 Token", index=True)
    secret_hash: str = Field(index=True, unique=True)
    secret_encrypted: str = Field(default="", sa_column=Column(Text))
    masked_value: str = Field(default="")
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    enabled: bool = Field(default=True, index=True)
    is_legacy: bool = Field(default=False, index=True)
    notes: str = Field(default="", sa_column=Column(Text))
    call_count: int = Field(default=0)
    last_used_at: Optional[float] = Field(default=None, index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class MobileSmsMessage(SQLModel, table=True):
    __tablename__ = "mobilesmsmessage"
    __table_args__ = (
        UniqueConstraint("device_id", "sms_id", name="uq_mobilesmsmessage_device_sms"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    device_id: str = Field(index=True)
    sms_id: str = Field(index=True)
    thread_id: str = Field(default="", index=True)
    address: str = Field(default="", index=True)
    person: str = Field(default="")
    body: str = Field(default="", sa_column=Column(Text))
    date_ms: int = Field(default=0, index=True)
    date_sent_ms: Optional[int] = Field(default=None, index=True)
    message_type: str = Field(default="inbox", index=True)
    read: Optional[bool] = Field(default=None, index=True)
    seen: Optional[bool] = Field(default=None, index=True)
    status: Optional[int] = Field(default=None, index=True)
    service_center: str = Field(default="")
    subscription_id: Optional[int] = Field(default=None, index=True)
    sim_slot_index: Optional[int] = Field(default=None, index=True)
    sim_display_name: str = Field(default="")
    sim_carrier_name: str = Field(default="")
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    source: str = Field(default="android", index=True)
    first_seen_at: float = Field(default_factory=time.time, index=True)
    last_seen_at: float = Field(default_factory=time.time, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class FanxiuPseudoCodeCard(SQLModel, table=True):
    __tablename__ = "fanxiupseudocodecard"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    scope: str = Field(default="action", index=True)
    title: str = Field(default="", sa_column=Column(Text))
    body: str = Field(default="", sa_column=Column(Text))
    enabled: bool = Field(default=True, index=True)
    order_index: int = Field(default=0, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class FanxiuPlayerProfileRecord(SQLModel, table=True):
    __tablename__ = "fanxiuplayerprofilerecord"
    __table_args__ = (
        UniqueConstraint("packet_id", name="uq_fanxiuplayerprofilerecord_packet_id"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    packet_id: str = Field(index=True)
    protocol: str = Field(default="", index=True)
    source_kind: str = Field(default="", index=True)
    role_id: str = Field(default="", index=True)
    role_id_text: str = Field(default="", index=True)
    name: str = Field(default="", index=True)
    server: Optional[int] = Field(default=None, index=True)
    region_number: Optional[int] = Field(default=None, index=True)
    region_name: str = Field(default="", index=True)
    server_order: Optional[int] = Field(default=None, index=True)
    server_name: str = Field(default="", index=True)
    cultivation_level: Optional[int] = Field(default=None, index=True)
    cultivation_level_text: str = Field(default="", index=True)
    attack_value: Optional[float] = Field(default=None, index=True)
    attack_text: str = Field(default="")
    captured_at: str = Field(default="", index=True)
    captured_date: str = Field(default="", index=True)
    battle_score: Optional[float] = Field(default=None)
    battle_score_text: str = Field(default="")
    special_attributes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    immortal_attributes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    combat_attributes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    attributes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class FanxiuPacketBusinessRecord(SQLModel, table=True):
    __tablename__ = "fanxiupacketbusinessrecord"
    __table_args__ = (
        UniqueConstraint("domain", "record_key", name="uq_fanxiupacketbusinessrecord_domain_key"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    domain: str = Field(index=True)
    record_key: str = Field(index=True)
    protocol: str = Field(default="", index=True)
    packet_id: str = Field(default="", index=True)
    source_kind: str = Field(default="", index=True)
    entity_id: str = Field(default="", index=True)
    entity_name: str = Field(default="", index=True)
    captured_at: str = Field(default="", index=True)
    captured_date: str = Field(default="", index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class FanxiuPacketDecodedRecord(SQLModel, table=True):
    __tablename__ = "fanxiupacketdecodedrecord"
    __table_args__ = (
        UniqueConstraint("packet_id", name="uq_fanxiupacketdecodedrecord_packet_id"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    packet_id: str = Field(index=True)
    record_id: str = Field(default="", index=True)
    pcap_name: str = Field(default="", index=True)
    capture_sha256: str = Field(default="", index=True)
    stream: int = Field(default=0, index=True)
    direction: str = Field(default="", index=True)
    frame_index: int = Field(default=0, index=True)
    offset: Optional[int] = Field(default=None, index=True)
    sn: Optional[int] = Field(default=None, index=True)
    pro_id: Optional[int] = Field(default=None, index=True)
    name: str = Field(default="", index=True)
    captured_at: str = Field(default="", index=True)
    captured_date: str = Field(default="", index=True)
    payload_len: Optional[int] = Field(default=None)
    decode_error: str = Field(default="", sa_column=Column(Text))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class FanxiuMailRecord(SQLModel, table=True):
    __tablename__ = "fanxiumailrecord"
    __table_args__ = (
        UniqueConstraint("mail_key", name="uq_fanxiumailrecord_mail_key"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    mail_key: str = Field(index=True)
    mail_id: str = Field(default="", index=True)
    title: str = Field(default="", index=True)
    normalized_title: str = Field(default="", index=True)
    mail_type: str = Field(default="", index=True)
    create_time_text: str = Field(default="", index=True)
    create_time_ms: Optional[int] = Field(default=None, index=True)
    source: str = Field(default="", index=True)
    status: str = Field(default="seen", index=True)
    locked: bool = Field(default=False, index=True)
    action_policy: str = Field(default="", index=True)
    last_action_error: str = Field(default="", sa_column=Column(Text))
    seen_count: int = Field(default=0)
    first_seen_at: float = Field(default_factory=time.time)
    last_seen_at: float = Field(default_factory=time.time, index=True)
    last_seen_capture_at: str = Field(default="", index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class FeatureAccessPolicy(SQLModel, table=True):
    __tablename__ = "featureaccesspolicy"
    __table_args__ = {'extend_existing': True}

    subject_key: str = Field(primary_key=True)
    subject_type: str = Field(index=True)
    subject_user_id: Optional[int] = Field(default=None, index=True)
    overrides: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    updated_by_user_id: Optional[int] = Field(default=None, index=True)


class ResourceIdentity(SQLModel, table=True):
    __tablename__ = "resourceidentity"
    __table_args__ = (
        UniqueConstraint("resource_type", "legacy_pk", name="uq_resourceidentity_type_legacy"),
        {"extend_existing": True},
    )

    id: int = Field(primary_key=True)
    resource_type: str = Field(index=True)
    legacy_pk: str = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ResourceAccessGrant(SQLModel, table=True):
    __tablename__ = "resourceaccessgrant"
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", "subject_key", name="uq_resourceaccessgrant_resource_subject"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    resource_type: str = Field(index=True)
    resource_id: str = Field(index=True)
    subject_key: str = Field(index=True)
    subject_type: str = Field(index=True)
    subject_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    role: str = Field(default="viewer", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)


class GithubProject(SQLModel, table=True):
    __tablename__ = "githubproject"
    __table_args__ = (
        UniqueConstraint("github_repo_id", name="uq_githubproject_repo_id"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    github_repo_id: int = Field(index=True)
    full_name: str = Field(index=True)
    html_url: str = Field(default="")
    default_branch: str = Field(default="")
    description: str = Field(default="", sa_column=Column(Text))
    homepage: str = Field(default="")
    language: str = Field(default="", index=True)
    license_spdx_id: str = Field(default="", index=True)
    topics: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    stars: int = Field(default=0, index=True)
    forks: int = Field(default=0, index=True)
    open_issues: int = Field(default=0, index=True)
    archived: bool = Field(default=False, index=True)
    disabled: bool = Field(default=False, index=True)
    private: bool = Field(default=False, index=True)
    created_at_github: str = Field(default="", index=True)
    pushed_at: str = Field(default="", index=True)
    updated_at_github: str = Field(default="", index=True)
    last_seen_at: float = Field(default_factory=time.time, index=True)
    last_checked_at: Optional[float] = Field(default=None, index=True)
    needs_review: bool = Field(default=True, index=True)
    analysis_note: str = Field(default="", sa_column=Column(Text))
    source_refs: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    update_notes: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)


class PdfDocument(SQLModel, table=True):
    __tablename__ = "pdfdocument"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "source_device_id", "source_absolute_path", name="uq_pdfdocument_owner_source"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    numeric_id: Optional[int] = Field(default=None, index=True, unique=True)
    legacy_id: Optional[str] = Field(default=None, index=True, unique=True)
    title: str = Field(default="")
    source_device_file_id: Optional[int] = Field(default=None, foreign_key="devicefile.id", index=True)
    source_entry_id: str = Field(default="", index=True)
    source_device_id: str = Field(default="", index=True)
    source_absolute_path: str = Field(default="", index=True)
    mime_type: str = Field(default="application/pdf", index=True)
    size_bytes: Optional[int] = Field(default=None, index=True)
    content_hash: Optional[str] = Field(default=None, index=True)
    hash_algorithm: str = Field(default="sha256")
    owner_user_id: int = Field(foreign_key="user.id", index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PdfUserState(SQLModel, table=True):
    __tablename__ = "pdfuserstate"
    __table_args__ = (
        UniqueConstraint("pdf_document_id", "user_id", name="uq_pdfuserstate_document_user"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    pdf_document_id: str = Field(index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    current_page: int = Field(default=1)
    zoom: str = Field(default="auto")
    sidebar_open: bool = Field(default=True)
    state_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PdfPageNote(SQLModel, table=True):
    __tablename__ = "pdfpagenote"
    __table_args__ = (
        UniqueConstraint("pdf_document_id", "user_id", "page_number", name="uq_pdfpagenote_document_user_page"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    pdf_document_id: str = Field(index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    page_number: int = Field(index=True)
    content_html: str = Field(default="", sa_column=Column(Text))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class DocumentAsset(SQLModel, table=True):
    __tablename__ = "documentasset"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)
    numeric_id: Optional[int] = Field(default=None, index=True, unique=True)
    legacy_id: Optional[str] = Field(default=None, index=True, unique=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    title: str = Field(default="")
    original_filename: str = Field(index=True)
    media_type: str = Field(default="text/plain", index=True)
    file_ext: str = Field(default="", index=True)
    size_bytes: int = Field(default=0)
    sha256: str = Field(index=True)
    source_char_count: int = Field(default=0)
    status: str = Field(default="uploaded", index=True)
    latest_run_id: Optional[str] = Field(default=None, index=True)
    latest_summary: str = Field(default="")
    latest_query_at: Optional[float] = Field(default=None, index=True)
    run_count: int = Field(default=0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class DocumentReductionRun(SQLModel, table=True):
    __tablename__ = "documentreductionrun"
    __table_args__ = {'extend_existing': True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    document_id: str = Field(index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str = Field(default="", index=True)
    model: str = Field(default="", index=True)
    task_type: str = Field(default="document_index", index=True)
    status: str = Field(default="pending", index=True)
    branch_factor: int = Field(default=8)
    source_unit_count: int = Field(default=0)
    source_unit_truncated_count: int = Field(default=0)
    estimated_level_count: int = Field(default=0)
    current_level_index: int = Field(default=0)
    current_level_chunk_count: int = Field(default=0)
    current_level_completed_chunk_count: int = Field(default=0)
    completed_chunk_count: int = Field(default=0)
    level_count: int = Field(default=0)
    node_count: int = Field(default=0)
    top_summary: str = Field(default="")
    error_message: Optional[str] = Field(default=None)
    created_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = Field(default=None, index=True)
    updated_at: float = Field(default_factory=time.time)


class DocumentQueryHistory(SQLModel, table=True):
    __tablename__ = "documentqueryhistory"
    __table_args__ = {'extend_existing': True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    document_id: str = Field(index=True)
    run_id: Optional[str] = Field(default=None, index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str = Field(default="", index=True)
    model: str = Field(default="", index=True)
    query_text: str = Field(default="")
    answer_text: str = Field(default="")
    status: str = Field(default="completed", index=True)
    matched_node_count: int = Field(default=0)
    matched_source_count: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    created_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = Field(default=None, index=True)
    updated_at: float = Field(default_factory=time.time)


class GitReductionRun(SQLModel, table=True):
    __tablename__ = "gitreductionrun"
    __table_args__ = {'extend_existing': True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    entry_id: str = Field(index=True)
    cwd: str = Field(default="", index=True)
    provider: str = Field(default="", index=True)
    model: str = Field(default="", index=True)
    style: str = Field(default="summary")
    include_body: bool = Field(default=True)
    branch_factor: int = Field(default=10)
    auto_commit: bool = Field(default=False, index=True)
    add_all: bool = Field(default=True)
    status: str = Field(default="pending", index=True)
    repo_root: str = Field(default="")
    branch: str = Field(default="")
    source_unit_count: int = Field(default=0)
    source_unit_truncated_count: int = Field(default=0)
    estimated_level_count: int = Field(default=0)
    current_level_index: int = Field(default=0)
    current_level_chunk_count: int = Field(default=0)
    current_level_completed_chunk_count: int = Field(default=0)
    completed_chunk_count: int = Field(default=0)
    level_count: int = Field(default=0)
    node_count: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    result_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    commit_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = Field(default=None, index=True)
    updated_at: float = Field(default_factory=time.time)


class AutoGitCommitRun(SQLModel, table=True):
    __tablename__ = "autogitcommitrun"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    status: str = Field(default="pending", index=True)
    trigger_reason: str = Field(default="scheduled", index=True)
    run_date: str = Field(default="", index=True)
    stage: str = Field(default="pending", index=True)
    stage_label: str = Field(default="等待中")
    repo_count: int = Field(default=0)
    changed_repo_count: int = Field(default=0)
    committed_repo_count: int = Field(default=0)
    skipped_repo_count: int = Field(default=0)
    failed_repo_count: int = Field(default=0)
    result_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error_message: Optional[str] = Field(default=None)
    queue_task_id: Optional[str] = Field(default=None, index=True)
    heartbeat_at: Optional[float] = Field(default=None, index=True)
    started_at: Optional[float] = Field(default=None, index=True)
    finished_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class CodexDailySummaryRun(SQLModel, table=True):
    __tablename__ = "codexdailysummaryrun"
    __table_args__ = {'extend_existing': True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    scope_key: str = Field(default="", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    root_key: str = Field(default="", index=True)
    root_dir: str = Field(default="", index=True)
    summary_date: str = Field(default="", index=True)
    timezone: str = Field(default="Asia/Shanghai")
    provider: str = Field(default="", index=True)
    generated_by: str = Field(default="deepseek", index=True)
    model: str = Field(default="", index=True)
    prompt_version: str = Field(default="", index=True)
    force_requested: bool = Field(default=False, index=True)
    status: str = Field(default="pending", index=True)
    stage: str = Field(default="pending", index=True)
    stage_label: str = Field(default="等待中")
    thread_count: int = Field(default=0)
    turn_count: int = Field(default=0)
    user_message_count: int = Field(default=0)
    assistant_message_count: int = Field(default=0)
    summary_text: str = Field(default="")
    error_message: Optional[str] = Field(default=None)
    result_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    heartbeat_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    finished_at: Optional[float] = Field(default=None, index=True)
    updated_at: float = Field(default_factory=time.time)


class CodexDiaryImportRun(SQLModel, table=True):
    __tablename__ = "codexdiaryimportrun"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    diary_date: str = Field(default="", index=True)
    timezone: str = Field(default="Asia/Shanghai")
    scope_key: str = Field(default="", index=True)
    entry_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    entry_snapshot: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    confirm_duplicate: bool = Field(default=False, index=True)
    status: str = Field(default="pending", index=True)
    stage: str = Field(default="pending", index=True)
    stage_label: str = Field(default="等待中")
    source_thread_count: int = Field(default=0)
    source_turn_count: int = Field(default=0)
    source_user_message_count: int = Field(default=0)
    source_assistant_message_count: int = Field(default=0)
    created_note_count: int = Field(default=0)
    created_note_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    duplicate_note_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    error_message: Optional[str] = Field(default=None)
    result_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    heartbeat_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    finished_at: Optional[float] = Field(default=None, index=True)
    updated_at: float = Field(default_factory=time.time)


class NoteMetadataFeedback(SQLModel, table=True):
    __tablename__ = "notemetadatafeedback"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    note_id: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    source_kind: str = Field(default="manual_update", index=True)
    source_kinds: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    source_ref_id: Optional[str] = Field(default=None, index=True)
    field_signature: str = Field(default="", index=True)
    field_names: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    before_snapshot: Optional[dict[str, Any]] = Field(default_factory=dict, sa_column=Column(JSON, nullable=True))
    after_snapshot: Optional[dict[str, Any]] = Field(default_factory=dict, sa_column=Column(JSON, nullable=True))
    title_sample: str = Field(default="")
    content_summary: str = Field(default="")
    content_hash: str = Field(default="", index=True)
    content_length: int = Field(default=0)
    event_count: int = Field(default=1)
    consumer_run_id: Optional[str] = Field(default=None, index=True)
    first_event_at: float = Field(default_factory=time.time, index=True)
    last_event_at: float = Field(default_factory=time.time, index=True)
    consumed_at: Optional[float] = Field(default=None, index=True)
    compressed_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class NoteMetadataFeedbackOptimizationRun(SQLModel, table=True):
    __tablename__ = "notemetadatafeedbackoptimizationrun"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    status: str = Field(default="pending", index=True)
    trigger_reason: str = Field(default="manual", index=True)
    stage: str = Field(default="pending", index=True)
    stage_label: str = Field(default="等待中")
    provider: str = Field(default="codex_cli", index=True)
    model: str = Field(default="", index=True)
    sample_count: int = Field(default=0)
    consumed_feedback_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    changed_files: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    backup_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result_text: str = Field(default="")
    test_results: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error_message: Optional[str] = Field(default=None)
    queue_task_id: Optional[str] = Field(default=None, index=True)
    heartbeat_at: Optional[float] = Field(default=None, index=True)
    started_at: Optional[float] = Field(default=None, index=True)
    finished_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class CodexMaintenanceFeedback(SQLModel, table=True):
    __tablename__ = "codexmaintenancefeedback"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    status: str = Field(default="pending", index=True)
    source_kind: str = Field(default="", index=True)
    source_ref_id: str = Field(default="", index=True)
    source_date: str = Field(default="", index=True)
    stage: str = Field(default="", index=True)
    error_type: str = Field(default="", index=True)
    error_message: str = Field(default="")
    context_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    event_count: int = Field(default=1)
    consumer_run_id: Optional[str] = Field(default=None, index=True)
    first_event_at: float = Field(default_factory=time.time, index=True)
    last_event_at: float = Field(default_factory=time.time, index=True)
    consumed_at: Optional[float] = Field(default=None, index=True)
    compressed_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class AttendanceServiceConfig(SQLModel, table=True):
    __tablename__ = "attendanceserviceconfig"
    __table_args__ = {"extend_existing": True}

    id: int = Field(default=1, primary_key=True)
    current_wjx_account_id: Optional[str] = Field(default=None, index=True)
    execution_device_entry_id: Optional[str] = Field(default=None, index=True)
    granted_user_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class CodexTextCacheRoot(SQLModel, table=True):
    __tablename__ = "codextextcacheroot"
    __table_args__ = {"extend_existing": True}

    root_key: str = Field(primary_key=True)
    root_dir: str = Field(index=True)
    default_root_dir: str = Field(default="")
    state_db_path: str = Field(default="")
    session_index_path: str = Field(default="")
    global_state_path: str = Field(default="")
    workspace_roots: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    state_db_mtime_ns: Optional[int] = Field(default=None)
    state_db_size: Optional[int] = Field(default=None)
    session_index_mtime_ns: Optional[int] = Field(default=None)
    session_index_size: Optional[int] = Field(default=None)
    global_state_mtime_ns: Optional[int] = Field(default=None)
    global_state_size: Optional[int] = Field(default=None)
    refreshed_at: float = Field(default_factory=time.time)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class CodexTextCacheThread(SQLModel, table=True):
    __tablename__ = "codextextcachethread"
    __table_args__ = (
        UniqueConstraint("root_key", "thread_id", name="uq_codextextcachethread_root_thread"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    root_key: str = Field(index=True)
    thread_id: str = Field(index=True)
    title: str = Field(default="")
    preview: Optional[str] = Field(default=None)
    cwd: Optional[str] = Field(default=None, index=True)
    original_cwd: Optional[str] = Field(default=None)
    rollout_path: Optional[str] = Field(default=None)
    created_at_source: Optional[float] = Field(default=None, index=True)
    updated_at_source: Optional[float] = Field(default=None, index=True)
    archived: bool = Field(default=False, index=True)
    project_label: str = Field(default="", index=True)
    project_secondary_label: Optional[str] = Field(default=None)
    workspace_root: Optional[str] = Field(default=None)
    rollout_mtime_ns: Optional[int] = Field(default=None)
    rollout_size: Optional[int] = Field(default=None)
    message_count: int = Field(default=0)
    user_message_count: int = Field(default=0)
    assistant_message_count: int = Field(default=0)
    refreshed_at: float = Field(default_factory=time.time)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class CodexTextCacheMessage(SQLModel, table=True):
    __tablename__ = "codextextcachemessage"
    __table_args__ = (
        UniqueConstraint("root_key", "thread_id", "seq", name="uq_codextextcachemessage_root_thread_seq"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    root_key: str = Field(index=True)
    thread_id: str = Field(index=True)
    seq: int = Field(index=True)
    timestamp: Optional[str] = Field(default=None)
    role: str = Field(default="", index=True)
    phase: Optional[str] = Field(default=None, index=True)
    text: str = Field(default="")
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class CodexTextCacheTurn(SQLModel, table=True):
    __tablename__ = "codextextcacheturn"
    __table_args__ = (
        UniqueConstraint("root_key", "thread_id", "turn_index", name="uq_codextextcacheturn_root_thread_turn"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    root_key: str = Field(index=True)
    thread_id: str = Field(index=True)
    turn_index: int = Field(index=True)
    user_seq: int = Field(default=0)
    assistant_seq: Optional[int] = Field(default=None)
    start_at: float = Field(default=0, index=True)
    end_at: float = Field(default=0, index=True)
    duration_seconds: float = Field(default=0)
    completed: bool = Field(default=False, index=True)
    preview: Optional[str] = Field(default=None)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class AttendanceAccountAsset(SQLModel, table=True):
    __tablename__ = "attendanceaccountasset"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    provider: str = Field(default="wjx", index=True)
    name: str = Field(index=True)
    login_username: str
    password_encrypted: str = Field(default="")
    is_active: bool = Field(default=True, index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class AttendanceOrderRefundHistory(SQLModel, table=True):
    __tablename__ = "attendanceorderrefundhistory"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    requested_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    operator_username: str = Field(default="", index=True)
    operator_nickname: str = Field(default="", index=True)
    execution_device_entry_id: Optional[str] = Field(default=None, index=True)
    student_name: str = Field(default="", index=True)
    wechat_order_id: str = Field(default="", index=True)
    merchant_order_id: str = Field(default="", index=True)
    order_amount: str = Field(default="")
    refunded_amount: str = Field(default="")
    remaining_amount: str = Field(default="")
    refund_amount: str = Field(default="")
    refund_reason: str = Field(default="")
    result_text: str = Field(default="")
    raw_row_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class SheetDocument(SQLModel, table=True):
    __tablename__ = "sheetdocument"
    __table_args__ = (
        UniqueConstraint("scope", "owner_type", "owner_key", "sheet_key", name="uq_sheetdocument_owner_locator"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=generate_sheet_document_id, primary_key=True)
    numeric_id: Optional[int] = Field(default=None, index=True, unique=True)
    legacy_id: Optional[str] = Field(default=None, index=True, unique=True)
    scope: str = Field(default="", index=True)
    owner_type: str = Field(default="", index=True)
    owner_key: str = Field(default="", index=True)
    sheet_key: str = Field(default="", index=True)
    title: str = Field(default="")
    engine: str = Field(default="handsontable", index=True)
    document_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    version: int = Field(default=1)
    owner_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    deleted_at: Optional[float] = Field(default=None, index=True)
    deleted_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)


class WorkbookDocument(SQLModel, table=True):
    __tablename__ = "workbookdocument"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=generate_sheet_document_id, primary_key=True)
    numeric_id: Optional[int] = Field(default=None, index=True, unique=True)
    legacy_id: Optional[str] = Field(default=None, index=True, unique=True)
    title: str = Field(default="")
    owner_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    deleted_at: Optional[float] = Field(default=None, index=True)
    deleted_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)


class WorkbookSheetLink(SQLModel, table=True):
    __tablename__ = "workbooksheetlink"
    __table_args__ = (
        UniqueConstraint("workbook_id", "sheet_id", name="uq_workbooksheetlink_workbook_sheet"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    workbook_id: str = Field(index=True)
    sheet_id: str = Field(index=True)
    order_index: int = Field(default=0, index=True)
    created_at: float = Field(default_factory=time.time)


class AttendanceWjxDataSyncState(SQLModel, table=True):
    __tablename__ = "attendancewjxdatasyncstate"
    __table_args__ = {"extend_existing": True}

    activity_id: str = Field(primary_key=True)
    template_id: str = Field(default="wjx-course-catalog", index=True)
    last_max_seq: int = Field(default=0)
    last_incremental_count: int = Field(default=0)
    stored_count: int = Field(default=0)
    last_used_all_pages: bool = Field(default=False)
    last_sync_at: Optional[float] = Field(default=None, index=True)
    last_success_at: Optional[float] = Field(default=None, index=True)
    last_error: Optional[str] = Field(default=None)
    execution_device_entry_id: Optional[str] = Field(default=None, index=True)
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    updated_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class AttendanceWjxDataEntry(SQLModel, table=True):
    __tablename__ = "attendancewjxdataentry"
    __table_args__ = (
        UniqueConstraint("activity_id", "seq", name="uq_attendancewjxdataentry_activity_seq"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    activity_id: str = Field(index=True)
    seq: int = Field(index=True)
    submitted_at_text: str = Field(default="", index=True)
    duration_text: str = Field(default="")
    source: str = Field(default="", index=True)
    source_detail: str = Field(default="")
    source_ip: str = Field(default="", index=True)
    course_name: str = Field(default="", index=True)
    student_id_text: str = Field(default="", index=True)
    student_name: str = Field(default="", index=True)
    correction_request: str = Field(default="")
    extra_note: str = Field(default="")
    process_status: str = Field(default="", index=True)
    process_note: str = Field(default="")
    match_result_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    revision_result_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    raw_row_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    synced_at: float = Field(default_factory=time.time, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class EastmoneyTradeSyncRun(SQLModel, table=True):
    __tablename__ = "eastmoneytradesyncrun"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    account_label: str = Field(default="", index=True)
    start_date: str = Field(default="", index=True)
    end_date: str = Field(default="", index=True)
    status: str = Field(default="running", index=True)
    captured_at: Optional[float] = Field(default=None, index=True)
    inserted_count: int = Field(default=0)
    updated_count: int = Field(default=0)
    trade_record_count: int = Field(default=0)
    position_count: int = Field(default=0)
    asset_summary_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    started_at: float = Field(default_factory=time.time, index=True)
    finished_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class EastmoneyTradeRecord(SQLModel, table=True):
    __tablename__ = "eastmoneytraderecord"
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uq_eastmoneytraderecord_user_source_key"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    sync_run_id: str = Field(default="", foreign_key="eastmoneytradesyncrun.id", index=True)
    account_label: str = Field(default="", index=True)
    source: str = Field(default="", index=True)
    source_key: str = Field(index=True)
    market: str = Field(default="", index=True)
    trade_date: str = Field(default="", index=True)
    trade_time: str = Field(default="", index=True)
    security_code: str = Field(default="", index=True)
    security_name: str = Field(default="", index=True)
    direction: str = Field(default="", index=True)
    quantity: str = Field(default="")
    price: str = Field(default="")
    occurrence_date: str = Field(default="", index=True)
    occurrence_time: str = Field(default="", index=True)
    occurrence_amount: str = Field(default="")
    amount: str = Field(default="")
    fee: str = Field(default="")
    commission: str = Field(default="")
    stamp_tax: str = Field(default="")
    transfer_fee: str = Field(default="")
    other_fee: str = Field(default="")
    currency: str = Field(default="", index=True)
    deal_id: str = Field(default="", index=True)
    shareholder_account: str = Field(default="", index=True)
    share_balance: str = Field(default="")
    fund_balance: str = Field(default="")
    extended_name: str = Field(default="", index=True)
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    raw_text: str = Field(default="", sa_column=Column(Text))
    quantity_value: Optional[float] = Field(default=None, index=True)
    price_value: Optional[float] = Field(default=None, index=True)
    occurrence_amount_value: Optional[float] = Field(default=None, index=True)
    amount_value: Optional[float] = Field(default=None, index=True)
    fee_value: Optional[float] = Field(default=None, index=True)
    commission_value: Optional[float] = Field(default=None, index=True)
    stamp_tax_value: Optional[float] = Field(default=None, index=True)
    transfer_fee_value: Optional[float] = Field(default=None, index=True)
    other_fee_value: Optional[float] = Field(default=None, index=True)
    share_balance_value: Optional[float] = Field(default=None, index=True)
    fund_balance_value: Optional[float] = Field(default=None, index=True)
    first_seen_at: float = Field(default_factory=time.time, index=True)
    last_seen_at: float = Field(default_factory=time.time, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class EastmoneyAssetSnapshot(SQLModel, table=True):
    __tablename__ = "eastmoneyassetsnapshot"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    sync_run_id: str = Field(foreign_key="eastmoneytradesyncrun.id", index=True)
    account_label: str = Field(default="", index=True)
    captured_at: float = Field(default_factory=time.time, index=True)
    total_asset: str = Field(default="")
    market_value: str = Field(default="")
    cash_available: str = Field(default="")
    cash_balance: str = Field(default="")
    withdrawable: str = Field(default="")
    frozen: str = Field(default="")
    pnl: str = Field(default="")
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class EastmoneyPositionSnapshot(SQLModel, table=True):
    __tablename__ = "eastmoneypositionsnapshot"
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    sync_run_id: str = Field(foreign_key="eastmoneytradesyncrun.id", index=True)
    account_label: str = Field(default="", index=True)
    source: str = Field(default="", index=True)
    market: str = Field(default="", index=True)
    captured_at: float = Field(default_factory=time.time, index=True)
    security_code: str = Field(default="", index=True)
    security_name: str = Field(default="", index=True)
    quantity: str = Field(default="")
    available_quantity: str = Field(default="")
    cost_price: str = Field(default="")
    current_price: str = Field(default="")
    market_value: str = Field(default="")
    pnl: str = Field(default="")
    pnl_ratio: str = Field(default="")
    currency: str = Field(default="", index=True)
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class EastmoneyStatementImport(SQLModel, table=True):
    __tablename__ = "eastmoneystatementimport"
    __table_args__ = (
        UniqueConstraint("user_id", "file_sha256", name="uq_eastmoneystatementimport_user_file_sha256"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    sync_run_id: str = Field(default="", foreign_key="eastmoneytradesyncrun.id", index=True)
    account_label: str = Field(default="", index=True)
    source: str = Field(default="pdf_statement", index=True)
    file_name: str = Field(default="", index=True)
    file_path: str = Field(default="")
    file_size: int = Field(default=0)
    file_mtime: float = Field(default=0.0, index=True)
    file_sha256: str = Field(index=True)
    print_time: str = Field(default="", index=True)
    printed_at: Optional[float] = Field(default=None, index=True)
    query_start_date: str = Field(default="", index=True)
    query_end_date: str = Field(default="", index=True)
    customer_name: str = Field(default="", index=True)
    customer_no: str = Field(default="", index=True)
    fund_account: str = Field(default="", index=True)
    sh_account: str = Field(default="", index=True)
    sz_account: str = Field(default="", index=True)
    asset_summary_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    position_count: int = Field(default=0)
    flow_count: int = Field(default=0)
    trade_record_count: int = Field(default=0)
    raw_text: str = Field(default="", sa_column=Column(Text))
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    imported_at: float = Field(default_factory=time.time, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class EastmoneyFundFlowRecord(SQLModel, table=True):
    __tablename__ = "eastmoneyfundflowrecord"
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uq_eastmoneyfundflowrecord_user_source_key"),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    statement_import_id: str = Field(default="", foreign_key="eastmoneystatementimport.id", index=True)
    sync_run_id: str = Field(default="", foreign_key="eastmoneytradesyncrun.id", index=True)
    account_label: str = Field(default="", index=True)
    source: str = Field(default="pdf_statement", index=True)
    source_key: str = Field(index=True)
    flow_date: str = Field(default="", index=True)
    flow_category: str = Field(default="", index=True)
    market: str = Field(default="", index=True)
    security_code: str = Field(default="", index=True)
    security_name: str = Field(default="", index=True)
    quantity: str = Field(default="")
    price: str = Field(default="")
    occurrence_amount: str = Field(default="")
    fee: str = Field(default="")
    stamp_tax: str = Field(default="")
    transfer_fee: str = Field(default="")
    fund_balance: str = Field(default="")
    currency: str = Field(default="人民币", index=True)
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    raw_text: str = Field(default="", sa_column=Column(Text))
    quantity_value: Optional[float] = Field(default=None, index=True)
    price_value: Optional[float] = Field(default=None, index=True)
    occurrence_amount_value: Optional[float] = Field(default=None, index=True)
    fee_value: Optional[float] = Field(default=None, index=True)
    stamp_tax_value: Optional[float] = Field(default=None, index=True)
    transfer_fee_value: Optional[float] = Field(default=None, index=True)
    fund_balance_value: Optional[float] = Field(default=None, index=True)
    first_seen_at: float = Field(default_factory=time.time, index=True)
    last_seen_at: float = Field(default_factory=time.time, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

# --- Note Models ---

class NoteNode(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    # Runtime migrations rebuild this column as INTEGER; the Python type stays string-tolerant
    # during the legacy_id transition window for old tests/import helpers that construct refs.
    id: Optional[str] = Field(default=None, primary_key=True)
    numeric_id: Optional[int] = Field(default=None, index=True, unique=True)
    legacy_id: Optional[str] = Field(default=None, index=True, unique=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    title: Optional[str] = Field(default="Untitled")
    content: str = Field(default="") # HTML content
    version: int = Field(default=1)
    
    # Visual weight level for notes. Non-memo nodes interpret this exponentially.
    weight: int = Field(default=0)
    
    # Legacy primary type mirror kept for transition compatibility.
    node_type: Optional[str] = Field(default="note", index=True)

    # Legacy weighted type system mirror. New code should prefer note_categories.
    note_types: List[dict] = Field(default_factory=list, sa_column=Column(JSON))

    # New naming-aligned category system. Derived from note_types during transition.
    note_categories: List[dict] = Field(default_factory=list, sa_column=Column(JSON))
    primary_category: Optional[str] = Field(default="general", index=True)

    # Content form: note / document / memo.
    note_form: Optional[str] = Field(default="note", index=True)

    # Semantic kind for specialized note flows. Kept separate from visual type.
    note_kind: Optional[str] = Field(default="note", index=True)
    note_scene: Optional[str] = Field(default="note", index=True)

    # Legacy lifecycle stage mirror kept for transition compatibility.
    node_status: Optional[str] = Field(default="idea", index=True)
    lifecycle_stage: Optional[str] = Field(default="idea", index=True)

    # Legacy per-node color override retained for future migration into tags/types.
    color: Optional[str] = Field(default=None)

    # Independent weight semantics so runtime behavior no longer depends on node_type.
    weight_mode: Optional[str] = Field(default=None, index=True)

    # Private marker for doc-like notes. Kept as int to allow future levels.
    private_level: int = Field(default=0, index=True)
    
    # Visual coordinates for graph are dynamically calculated by frontend layout algorithm
    # No persistent storage for position in backend as requested.
    
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    start_at: float = Field(default_factory=time.time)
    deleted_at: Optional[float] = Field(default=None, index=True)
    deleted_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    
    # Operation logs: list of {"ts": int, "f": str, "v": any}
    history: List[dict] = Field(default=[], sa_column=Column(JSON))

    # Custom attributes: currently stored as list rows [key, type, value], with legacy dict rows tolerated.
    custom_fields: Any = Field(default_factory=list, sa_column=Column(JSON))

class NoteEdge(SQLModel, table=True):
    """
    Directed edge between two NoteNodes.
    """
    __table_args__ = {'extend_existing': True}
    id: Optional[str] = Field(default=None, primary_key=True) # UUID
    user_id: int = Field(foreign_key="user.id", index=True)
    
    source_id: str = Field(index=True)
    target_id: str = Field(index=True)
    
    label: Optional[str] = None # Edge label (optional)
    
    created_at: float = Field(default_factory=time.time)

# --- Pydantic Models for API (Optional, if we want strict separation) ---
# For simplicity, we can reuse SQLModel classes as Pydantic models in FastAPI
