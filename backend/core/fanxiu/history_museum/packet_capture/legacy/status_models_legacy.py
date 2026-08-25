from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class FanxiuProcessItem(BaseModel):
    pid: int
    parent_pid: Optional[int] = None
    name: str
    command_line: str
    created_at: Optional[str] = None
    matched_reason: str


class FanxiuProcessListResponse(BaseModel):
    items: List[FanxiuProcessItem] = Field(default_factory=list)


class FanxiuPacketCaptureSnapshotRequest(BaseModel):
    dns_hosts: List[str] = Field(default_factory=list)
    resolve_dns: bool = True


class FanxiuPacketCaptureDnsMapping(BaseModel):
    host: str
    ips: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class FanxiuPacketCaptureAddress(BaseModel):
    ip: str
    port: int
    label: str


class FanxiuPacketCaptureProcess(BaseModel):
    pid: int
    name: str
    exe: Optional[str] = None
    command_line: str = ""
    group: str


class FanxiuPacketCaptureConnection(BaseModel):
    pid: int
    process_name: str
    process_group: str
    protocol: str
    status: str
    local: Optional[FanxiuPacketCaptureAddress] = None
    remote: Optional[FanxiuPacketCaptureAddress] = None
    mapped_hosts: List[str] = Field(default_factory=list)
    is_fake_ip: bool = False
    remote_scope: str = ""
    signal_score: int = 0
    signal_label: str = ""
    signal_reason: str = ""


class FanxiuPacketCaptureSnapshot(BaseModel):
    captured_at: str
    dns_server: str
    dns_mappings: List[FanxiuPacketCaptureDnsMapping] = Field(default_factory=list)
    processes: List[FanxiuPacketCaptureProcess] = Field(default_factory=list)
    connections: List[FanxiuPacketCaptureConnection] = Field(default_factory=list)
    listeners: List[FanxiuPacketCaptureConnection] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class FanxiuTcpCaptureFile(BaseModel):
    name: str
    path: str
    relative_path: str
    size: int = 0
    modified_at: str = ""
    decoded_path: str = ""
    decoded: bool = False
    capture_sha256: str = ""
    record_id: str = ""
    record_dir: str = ""
    stored_pcap: str = ""
    stored_decoded_path: str = ""
    stored: bool = False


class FanxiuTcpCaptureListResponse(BaseModel):
    export_root: str
    capture_dir: str
    store_capture_dir: str = ""
    items: List[FanxiuTcpCaptureFile] = Field(default_factory=list)


class FanxiuTcpRecordItem(BaseModel):
    record_id: str
    record_dir: str
    pcap_name: str = ""
    source_pcap: str = ""
    stored_pcap: str = ""
    decoded_path: str = ""
    decoded: bool = False
    stream: int = 0
    server_host: str = ""
    capture_sha256: str = ""
    created_at: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)


class FanxiuTcpRecordListResponse(BaseModel):
    store_root: str
    items: List[FanxiuTcpRecordItem] = Field(default_factory=list)


class FanxiuTcpBusinessEntry(BaseModel):
    id: str
    decoded_at: str = ""
    record_id: str = ""
    pcap_name: str = ""
    source_kind: str = ""
    direction: str = ""
    name: str = ""
    category: str = ""
    meaning: str = ""
    protocol_meaning: str = ""
    pro_id: int = 0
    sn: int = 0
    frame_index: int = 0
    display_text: str = ""
    display_segments: List[dict[str, Any]] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)


class FanxiuTcpBusinessCategorySummary(BaseModel):
    category: str = ""
    meaning: str = ""
    count: int = 0
    protocols: List[str] = Field(default_factory=list)


class FanxiuTcpBusinessProtocolSample(BaseModel):
    id: str = ""
    decoded_at: str = ""
    direction: str = ""
    display_text: str = ""
    display_segments: List[dict[str, Any]] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    field_labels: dict[str, dict[str, str]] = Field(default_factory=dict)


class FanxiuTcpBusinessProtocolSummary(BaseModel):
    name: str = ""
    category: str = ""
    meaning: str = ""
    count: int = 0
    samples: List[FanxiuTcpBusinessProtocolSample] = Field(default_factory=list)


class FanxiuTcpBusinessEntryListResponse(BaseModel):
    page: int = 1
    page_size: int = 50
    total: int = 0
    category_summary: List[FanxiuTcpBusinessCategorySummary] = Field(default_factory=list)
    protocol_summary: List[FanxiuTcpBusinessProtocolSummary] = Field(default_factory=list)
    items: List[FanxiuTcpBusinessEntry] = Field(default_factory=list)


class FanxiuTcpDecodeRequest(BaseModel):
    pcap: str
    stream: int = Field(34, ge=-1)
    server_host: str = "1.12.44.63"
    persist: bool = True


class FanxiuTcpDecodeResponse(BaseModel):
    export_root: str
    pcap: str
    stream: int
    server_host: str
    text_assets: str
    output_path: str = ""
    capture_sha256: str = ""
    stream_candidates: List[dict[str, Any]] = Field(default_factory=list)
    record_id: str = ""
    record_dir: str = ""
    stored_pcap: str = ""
    stored_decoded_path: str = ""
    meta_path: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    frames: List[dict[str, Any]] = Field(default_factory=list)


class FanxiuPacketProxyStartRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8899, ge=1, le=65535)
    device_id: str = ""


class FanxiuPacketProxyStatus(BaseModel):
    running: bool
    host: str = ""
    port: int = 0
    addresses: List[str] = Field(default_factory=list)
    event_count: int = 0
    last_error: str = ""


class FanxiuAndroidProxyStatus(BaseModel):
    available: bool = False
    adb_path: str = ""
    device_id: str = ""
    devices: List[str] = Field(default_factory=list)
    http_proxy: str = ""
    enabled: bool = False
    target_proxy: str = ""
    matches_target: bool = False
    last_error: str = ""


class FanxiuPacketCaptureSessionStatus(BaseModel):
    active: bool = False
    target_proxy: str = ""
    proxy: FanxiuPacketProxyStatus
    android: FanxiuAndroidProxyStatus
    last_error: str = ""


class FanxiuPacketActivityStartRequest(BaseModel):
    bind_ip: str = ""


class FanxiuCaptureRuntimeRequest(BaseModel):
    reason: str = "manual"


class FanxiuCaptureRuntimeStatus(BaseModel):
    state: str = "stopped"
    running: bool = False
    game_running: bool = False
    adb_connected: bool = False
    root_ready: bool = False
    tcpdump_ready: bool = False
    active_reasons: List[str] = Field(default_factory=list)
    current_pcap_path: str = ""
    current_pcap_size: int = 0
    current_remote_pcap_path: str = ""
    started_at: str = ""
    last_error: str = ""
    last_recover_at: str = ""
    tcpdump_started_at: str = ""
    device_id: str = ""
    package_name: str = ""
    watchdog_running: bool = False
    watchdog_started_at: str = ""
    watchdog_interval_seconds: float = 0
    watchdog_last_check_at: str = ""
    watchdog_last_action: str = ""
    watchdog_last_error: str = ""


class FanxiuActivityPacketSyncRequest(BaseModel):
    force: bool = False


class FanxiuActivityPacketSyncResponse(BaseModel):
    ok: bool = True
    state_path: str = ""
    records_path: str = ""
    rank_records_path: str = ""
    cursor: dict[str, Any] = Field(default_factory=dict)
    rank_cursor: dict[str, Any] = Field(default_factory=dict)
    scanned_packets: int = 0
    matched_packets: int = 0
    matched_rank_packets: int = 0
    inserted: int = 0
    updated: int = 0
    rank_inserted: int = 0
    rank_updated: int = 0
    skipped_duplicates: int = 0
    rank_skipped_duplicates: int = 0
    record_count: int = 0
    rank_record_count: int = 0


class FanxiuPacketInsightSyncRequest(BaseModel):
    force: bool = False


class FanxiuPacketInsightResponse(BaseModel):
    ok: bool = True
    changed: bool = False
    stale: bool = False
    state_schema_version: int = 0
    schema_version: int = 0
    state_path: str = ""
    snapshot_path: str = ""
    source_signature: dict[str, Any] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)


class FanxiuPlayerProfileRecordListResponse(BaseModel):
    ok: bool = True
    count: int = 0
    records: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuServerRelationTreeResponse(BaseModel):
    ok: bool = True
    version: int = 1
    ordering: str = "protection_desc"
    groups: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuServerRelationTreeUpdateRequest(BaseModel):
    version: int = 1
    ordering: str = "protection_desc"
    groups: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuMailRecordListResponse(BaseModel):
    ok: bool = True
    count: int = 0
    total: int = 0
    offset: int = 0
    limit: int = 0
    records: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuMailRecordUpdateRequest(BaseModel):
    status: str


class FanxiuMailRecordUpdateResponse(BaseModel):
    ok: bool = True
    record: dict[str, Any] = Field(default_factory=dict)


class FanxiuMailRuntimeSyncResponse(BaseModel):
    ok: bool = True
    complete: bool = False
    source: str = "runtime_memory"
    reason: str = ""
    inserted: int = 0
    updated: int = 0
    absent: int = 0
    record_count: int = 0
    captured_at: str = ""


class FanxiuPacketStorageBagResponse(BaseModel):
    ok: bool = True
    changed: bool = False
    stale: bool = False
    state_schema_version: int = 0
    schema_version: int = 0
    state_path: str = ""
    snapshot_path: str = ""
    source_signature: dict[str, Any] = Field(default_factory=dict)
    bag: dict[str, Any] | None = None
    worship: dict[str, Any] | None = None


class FanxiuPacketPayloadDirection(BaseModel):
    length: int = 0
    hex: str = ""
    ascii: str = ""
    text: str = ""
    printable_ratio: float = 0
    guess: str = "无负载"


class FanxiuPacketPayloadPreview(BaseModel):
    up: FanxiuPacketPayloadDirection = Field(default_factory=FanxiuPacketPayloadDirection)
    down: FanxiuPacketPayloadDirection = Field(default_factory=FanxiuPacketPayloadDirection)


class FanxiuPacketActivityFlow(BaseModel):
    key: str
    protocol: str
    remote: FanxiuPacketCaptureAddress
    packets_up: int = 0
    packets_down: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    payload_bytes_up: int = 0
    payload_bytes_down: int = 0
    payload_preview: FanxiuPacketPayloadPreview = Field(default_factory=FanxiuPacketPayloadPreview)
    first_seen: str = ""
    last_seen: str = ""


class FanxiuPacketActivityStatus(BaseModel):
    running: bool = False
    bind_ip: str = ""
    interfaces: List[str] = Field(default_factory=list)
    started_at: str = ""
    last_error: str = ""
    total_packets: int = 0
    total_bytes: int = 0
    history_total: int = 0
    history_capacity: int = 0
    pcap_path: str = ""
    pcap_size: int = 0
    items: List[FanxiuPacketActivityFlow] = Field(default_factory=list)


class FanxiuPacketActivityPayloadEvent(BaseModel):
    id: int = 0
    captured_at: str = ""
    key: str = ""
    protocol: str = ""
    remote: FanxiuPacketCaptureAddress
    direction: str = ""
    packet_bytes: int = 0
    payload_bytes: int = 0
    payload_preview: FanxiuPacketPayloadDirection = Field(default_factory=FanxiuPacketPayloadDirection)


class FanxiuPacketActivityHistoryResponse(BaseModel):
    items: List[FanxiuPacketActivityPayloadEvent] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50
    history_capacity: int = 0


class FanxiuPacketActivityStreamDirection(BaseModel):
    packet_count: int = 0
    payload_bytes: int = 0
    sampled_bytes: int = 0
    dropped_bytes: int = 0
    truncated_packets: int = 0
    first_seen: str = ""
    last_seen: str = ""
    preview: FanxiuPacketPayloadDirection = Field(default_factory=FanxiuPacketPayloadDirection)


class FanxiuPacketActivityStreamResponse(BaseModel):
    key: str = ""
    max_bytes: int = 0
    up: FanxiuPacketActivityStreamDirection = Field(default_factory=FanxiuPacketActivityStreamDirection)
    down: FanxiuPacketActivityStreamDirection = Field(default_factory=FanxiuPacketActivityStreamDirection)


class FanxiuPacketProxyEvent(BaseModel):
    id: int = 0
    timeline_id: str = ""
    source: str = ""
    source_label: str = ""
    started_at: str = ""
    finished_at: Optional[str] = None
    active: bool = False
    error: str = ""
    client: str = ""
    event_type: str = "unknown"
    method: str = ""
    target: str = ""
    url: str = ""
    request_headers: str = ""
    request_body_text: str = ""
    request_body_hex: str = ""
    response_status: str = ""
    response_headers: str = ""
    response_body_text: str = ""
    response_body_hex: str = ""
    bytes_up: int = 0
    bytes_down: int = 0
    plaintext_state: str = "unknown"
    semantic_role: str = "unknown"
    signal_score: int = 0
    signal_label: str = "未判断"
    signal_reason: str = ""


class FanxiuPacketProxyEventListResponse(BaseModel):
    items: List[FanxiuPacketProxyEvent] = Field(default_factory=list)
    status: FanxiuPacketProxyStatus


class FanxiuPacketProxyTimelineResponse(BaseModel):
    items: List[FanxiuPacketProxyEvent] = Field(default_factory=list)
    status: FanxiuPacketProxyStatus
    total: int = 0
    offset: int = 0
    limit: int = 50
    summary: dict[str, int] = Field(default_factory=dict)
    log_directory: str = ""


class FanxiuPacketProxySaveRequest(BaseModel):
    label: str = ""


class FanxiuPacketProxySaveResponse(BaseModel):
    saved_at: str
    path: str
    event_count: int
    status: FanxiuPacketProxyStatus


class FanxiuPacketProxyLogFile(BaseModel):
    name: str
    path: str
    size: int
    modified_at: str
    event_count: int


class FanxiuPacketProxyLogListResponse(BaseModel):
    items: List[FanxiuPacketProxyLogFile] = Field(default_factory=list)
    directory: str


class FanxiuPacketProxyLogLoadResponse(BaseModel):
    log: FanxiuPacketProxyLogFile
    items: List[FanxiuPacketProxyEvent] = Field(default_factory=list)


class LocalScriptProcessItem(BaseModel):
    pid: int
    parent_pid: Optional[int] = None
    name: str
    kind: str
    script: str
    script_path: Optional[str] = None
    command_line: str
    cwd: Optional[str] = None
    created_at: Optional[str] = None
    runtime_seconds: Optional[int] = None
    project_hint: str = ""
    is_fanxiu: bool = False


class LocalScriptProcessListResponse(BaseModel):
    items: List[LocalScriptProcessItem] = Field(default_factory=list)


class FanxiuProcessTerminateError(BaseModel):
    pid: int
    error: str


class FanxiuProcessTerminateResponse(BaseModel):
    matched: List[FanxiuProcessItem] = Field(default_factory=list)
    terminated: List[FanxiuProcessItem] = Field(default_factory=list)
    remaining: List[FanxiuProcessItem] = Field(default_factory=list)
    errors: List[FanxiuProcessTerminateError] = Field(default_factory=list)


