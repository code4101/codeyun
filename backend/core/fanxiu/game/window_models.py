from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class FanxiuGameWindow2StreamTokenRequest(BaseModel):
    entry_id: str


class FanxiuGameWindow2StreamTokenResponse(BaseModel):
    token: str
    expires_in_seconds: int


class FanxiuGameWindow2ClickRequest(BaseModel):
    entry_id: str
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    frame_width: Optional[int] = Field(None, ge=1, le=8192)
    frame_height: Optional[int] = Field(None, ge=1, le=8192)
    input_backend: str = Field("adb", pattern="^(desktop|adb)$")


class FanxiuGameWindow2ServiceClickRequest(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    frame_width: Optional[int] = Field(None, ge=1, le=8192)
    frame_height: Optional[int] = Field(None, ge=1, le=8192)
    input_backend: str = Field("adb", pattern="^(desktop|adb)$")


class FanxiuGameWindow2ActivateRequest(BaseModel):
    entry_id: str
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    click_title: bool = True


class FanxiuGameWindow2ServiceActivateRequest(BaseModel):
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    click_title: bool = True


class FanxiuGameWindow2DragRequest(BaseModel):
    entry_id: str
    start_x: float = Field(ge=0)
    start_y: float = Field(ge=0)
    end_x: float = Field(ge=0)
    end_y: float = Field(ge=0)
    duration_ms: int = Field(300, ge=50, le=3000)
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    frame_width: Optional[int] = Field(None, ge=1, le=8192)
    frame_height: Optional[int] = Field(None, ge=1, le=8192)
    input_backend: str = Field("adb", pattern="^(desktop|adb)$")


class FanxiuGameWindow2ServiceDragRequest(BaseModel):
    start_x: float = Field(ge=0)
    start_y: float = Field(ge=0)
    end_x: float = Field(ge=0)
    end_y: float = Field(ge=0)
    duration_ms: int = Field(300, ge=50, le=3000)
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    frame_width: Optional[int] = Field(None, ge=1, le=8192)
    frame_height: Optional[int] = Field(None, ge=1, le=8192)
    input_backend: str = Field("adb", pattern="^(desktop|adb)$")


class FanxiuGameWindow2KeyeventRequest(BaseModel):
    entry_id: str
    key: Optional[str] = None
    keys: Optional[list[str]] = None


class FanxiuGameWindow2ServiceKeyeventRequest(BaseModel):
    key: Optional[str] = None
    keys: Optional[list[str]] = None


class FanxiuGameWindow2TextRequest(BaseModel):
    entry_id: str
    text: str = Field(min_length=1, max_length=256)


class FanxiuGameWindow2ServiceTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=256)


class FanxiuGameWindow2ScreencapRequest(BaseModel):
    entry_id: str
    prefer_cached: bool = False
    cached_only: bool = False
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)


class FanxiuGameWindow2SaveFrameRequest(BaseModel):
    entry_id: str
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    quality: int = Field(82, ge=1, le=100)
    current_frame_data_url: Optional[str] = None
    overwrite_filename: Optional[str] = None


class FanxiuGameWindow2ServiceSaveFrameRequest(BaseModel):
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    quality: int = Field(82, ge=1, le=100)
    current_frame_data_url: Optional[str] = None
    overwrite_filename: Optional[str] = None


class FanxiuGameWindow2BurstFrameRequest(FanxiuGameWindow2SaveFrameRequest):
    pass


class FanxiuGameWindow2ServiceBurstFrameRequest(FanxiuGameWindow2ServiceSaveFrameRequest):
    pass


class FanxiuDataAnnotationAssetTreeRequest(BaseModel):
    entry_id: str
    tree: list[dict[str, Any]] = Field(default_factory=list)


class FanxiuGameWindow2BurstListRequest(BaseModel):
    entry_id: str
    page: int = Field(1, ge=1)
    page_size: int = Field(24, ge=1, le=100)


class FanxiuGameWindow2ServiceBurstListRequest(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(24, ge=1, le=100)


class FanxiuGameWindow2BurstClearRequest(BaseModel):
    entry_id: str


class FanxiuGameWindow2ServiceBurstClearRequest(BaseModel):
    pass


class FanxiuGameWindow2BurstImportRequest(BaseModel):
    entry_id: str
    filenames: list[str] = Field(default_factory=list)


class FanxiuGameWindow2ServiceBurstImportRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list)


class FanxiuGameWindow2MatchBox(BaseModel):
    name: str = ""
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class FanxiuGameWindow2MatchRequest(BaseModel):
    entry_id: str
    filename: str
    box: FanxiuGameWindow2MatchBox
    scan: bool = False
    scan_box: Optional[FanxiuGameWindow2MatchBox] = None
    pixel_tolerance: int = Field(5, ge=0, le=255)
    alpha_mask_data_url: Optional[str] = None
    tolerance_min_data_url: Optional[str] = None
    tolerance_max_data_url: Optional[str] = None
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    quality: int = Field(82, ge=1, le=100)
    current_frame_data_url: Optional[str] = None
    prefer_cached: bool = True
    match_strategy: str = Field("auto", pattern="^(auto|anchor_pixel)$")
    match_search_radius: Optional[int] = Field(None, ge=0, le=64)
    ocr_enabled: bool = False
    ocr_text: str = Field("", max_length=200)
    ocr_match_mode: str = Field("contains", pattern="^(contains|exact|wildcard|regex)$")
    ocr_min_confidence: float = Field(0.0, ge=0.0, le=1.0)
    read_only_cache: bool = False
    save_match_frame: bool = True
    debug_match: bool = False


class FanxiuGameWindow2ServiceMatchRequest(BaseModel):
    filename: str
    box: FanxiuGameWindow2MatchBox
    scan: bool = False
    scan_box: Optional[FanxiuGameWindow2MatchBox] = None
    pixel_tolerance: int = Field(5, ge=0, le=255)
    alpha_mask_data_url: Optional[str] = None
    tolerance_min_data_url: Optional[str] = None
    tolerance_max_data_url: Optional[str] = None
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    current_frame_data_url: Optional[str] = None
    prefer_cached: bool = True
    quality: int = Field(82, ge=1, le=100)
    match_strategy: str = Field("auto", pattern="^(auto|anchor_pixel)$")
    match_search_radius: Optional[int] = Field(None, ge=0, le=64)
    ocr_enabled: bool = False
    ocr_text: str = Field("", max_length=200)
    ocr_match_mode: str = Field("contains", pattern="^(contains|exact|wildcard|regex)$")
    ocr_min_confidence: float = Field(0.0, ge=0.0, le=1.0)
    read_only_cache: bool = False
    save_match_frame: bool = True
    debug_match: bool = False


class FanxiuPseudoCodeCardRead(BaseModel):
    id: str
    scope: str
    title: str
    body: str
    enabled: bool
    order_index: int
    created_at: float
    updated_at: float


class FanxiuPseudoCodeCardListResponse(BaseModel):
    items: List[FanxiuPseudoCodeCardRead] = Field(default_factory=list)


class FanxiuPseudoCodeCardCreateRequest(BaseModel):
    scope: str = Field("action", pattern="^(guard|action)$")
    title: str = ""
    body: str = ""
    enabled: bool = True
    order_index: Optional[int] = Field(None, ge=0)


class FanxiuPseudoCodeCardUpdateRequest(BaseModel):
    scope: Optional[str] = Field(None, pattern="^(guard|action)$")
    title: Optional[str] = None
    body: Optional[str] = None
    enabled: Optional[bool] = None
    order_index: Optional[int] = Field(None, ge=0)


class FanxiuPseudoCodeCompileRequest(BaseModel):
    entry_id: str = ""
    model: str = ""
    timeout: int = Field(300, ge=30, le=1200)


class FanxiuPseudoCodeStartRequest(BaseModel):
    timeout: int = Field(120, ge=5, le=1200)


class FanxiuVisualScriptRunRequest(BaseModel):
    entry_id: str
    card_id: str
    timeout: int = Field(0, ge=0)
    tick_interval: float = Field(1.0, ge=0.1, le=10.0)
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field("screen", pattern="^(auto|printwindow|screen)$")
    area: str = Field("client", pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field("0", pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    frame_width: Optional[int] = Field(None, ge=1, le=8192)
    frame_height: Optional[int] = Field(None, ge=1, le=8192)
    quality: int = Field(82, ge=1, le=100)


class FanxiuVisualScriptStopRequest(BaseModel):
    entry_id: str
    card_id: str


class FanxiuPseudoCodeRunResponse(BaseModel):
    ok: bool
    status: str
    script_path: str = ""
    cache_hits: int = 0
    cache_misses: int = 0
    compiled_cards: int = 0
    log: str = ""
    result: str = ""
    updated_at: float = 0


class FanxiuGameWindow2ScreenshotListRequest(BaseModel):
    entry_id: str


class FanxiuGameWindow2ScreenshotPreLabelRequest(BaseModel):
    entry_id: str
    filename: str


class FanxiuGameWindow2ScreenshotDeleteRequest(BaseModel):
    entry_id: str
    filename: str


class FanxiuGameWindow2ServiceScreenshotPreLabelRequest(BaseModel):
    filename: str


class FanxiuGameWindow2ServiceScreenshotDeleteRequest(BaseModel):
    filename: str


class FanxiuGameWindow2ScreenshotPreLabelSaveRequest(BaseModel):
    entry_id: str
    filename: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FanxiuGameWindow2ServiceScreenshotPreLabelSaveRequest(BaseModel):
    filename: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationOcrFrameRequest(BaseModel):
    image_data_url: str
    options: dict[str, Any] = Field(default_factory=dict)


class FanxiuDataAnnotationOcrFrameLine(BaseModel):
    text: str
    x: float
    y: float
    w: float
    h: float


class FanxiuDataAnnotationOcrFrameWord(BaseModel):
    text: str
    x: float
    y: float
    w: float
    h: float
    line_index: int | None = None


class FanxiuDataAnnotationOcrFrameResponse(BaseModel):
    lines: list[FanxiuDataAnnotationOcrFrameLine] = Field(default_factory=list)
    words: list[FanxiuDataAnnotationOcrFrameWord] = Field(default_factory=list)


class FanxiuDataAnnotationRemoveBackgroundRequest(BaseModel):
    image_data_url: str
    model: str = Field("isnet-general-use", min_length=1, max_length=80)
    alpha_matting: bool = False
    post_process_mask: bool = True


class FanxiuDataAnnotationRemoveBackgroundResponse(BaseModel):
    ok: bool = True
    model: str = ""
    width: int = 0
    height: int = 0
    alpha_mask_data_url: str = ""
    result_data_url: str = ""


class FanxiuDataAnnotationMacroPoint(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)


class FanxiuDataAnnotationMacroAnnotateRequest(BaseModel):
    image_data_url: str
    action: str = Field("click", pattern="^(click|drag)$")
    start: FanxiuDataAnnotationMacroPoint
    end: Optional[FanxiuDataAnnotationMacroPoint] = None
    fallback_box: FanxiuGameWindow2MatchBox
    frame_width: int = Field(gt=0, le=4096)
    frame_height: int = Field(gt=0, le=4096)
    duration_ms: int = Field(0, ge=0, le=120000)
    direction: Optional[str] = Field(None, pattern="^(up|down|left|right|none)$")


class FanxiuDataAnnotationMacroAnnotateResponse(BaseModel):
    ok: bool = True
    used_ai: bool = False
    box: FanxiuGameWindow2MatchBox
    confidence: float = Field(0, ge=0, le=1)
    label: str = ""
    reason: str = ""
    raw: str = ""
