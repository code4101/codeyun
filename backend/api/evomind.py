from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.ai.evomind import (
    clear_evomind_pending_imports,
    derive_evomind_case_card,
    generate_evomind_rule_proposal,
    read_evomind_pending_imports,
    scan_evomind_cases_from_codex,
)
from backend.core.access.feature_access_guard import require_feature_access_dependency
from backend.db import get_session


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("tools.ai-evomind"))],
)


class EvoMindCodexScanRequest(BaseModel):
    root_dir: str | None = None
    max_threads: int = Field(default=120, ge=1, le=500)
    max_cases: int = Field(default=40, ge=1, le=120)
    min_score: int = Field(default=55, ge=0, le=200)
    signal_type: str | None = Field(
        default=None,
        pattern="^(explicit_learning_marker|friction|repeated_correction|final_artifact_delta)$",
    )
    use_codex_cli: bool = True
    codex_cli_limit: int = Field(default=40, ge=1, le=120)
    reset_cache: bool = False
    scan_rule_text: str | None = Field(default=None, max_length=20000)


class EvoMindCaseSource(BaseModel):
    root_dir: str
    thread_id: str | None = None
    thread_title: str | None = None
    message_seq: int | None = None
    timestamp: str | None = None
    project_label: str | None = None
    workspace_root: str | None = None
    score: int = 0


class EvoMindCaseCandidate(BaseModel):
    id: str
    title: str
    domain: str
    signal_type: str
    evidence_strength: str
    friction_level: str
    original_task: str
    bad_attempt: str
    user_corrections: str
    final_pattern: str
    inferred_rule: str
    anti_patterns: list[str]
    positive_patterns: list[str]
    evidence_turns: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    source: EvoMindCaseSource


class EvoMindCodexScanResponse(BaseModel):
    root_dir: str
    total_threads: int
    scanned_threads: int
    skipped_threads: int
    scanned_messages: int
    heuristic_candidate_count: int = 0
    analysis_mode: str = "heuristic"
    codex_cli_used: bool = False
    codex_cli_invoked: bool = False
    cache_hit_count: int = 0
    cache_miss_count: int = 0
    cache_rule_hash: str = ""
    cache_rule_mismatch: bool = False
    cache_reset: bool = False
    items: list[EvoMindCaseCandidate]


class EvoMindProposalCaseInput(BaseModel):
    id: str
    title: str
    domain: str = ""
    signal_type: str = "repeated_correction"
    evidence_strength: str = "p2"
    friction_level: str = "medium"
    original_task: str = ""
    bad_attempt: str = ""
    user_corrections: str = ""
    final_pattern: str = ""
    inferred_rule: str = ""
    anti_patterns: list[str] = Field(default_factory=list)
    positive_patterns: list[str] = Field(default_factory=list)
    evidence_turns: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any] | None = None


class EvoMindProposalRequest(BaseModel):
    case: EvoMindProposalCaseInput
    target: str = Field(default="skill", pattern="^(skill|agents|docs)$")
    use_codex_cli: bool = True
    proposal_rule_text: str | None = Field(default=None, max_length=20000)


class EvoMindCaseCardRequest(BaseModel):
    case: EvoMindProposalCaseInput
    case_rule_text: str | None = Field(default=None, max_length=20000)


class EvoMindCaseCardResponse(BaseModel):
    id: str
    title: str
    domain: str
    signal_type: str
    evidence_strength: str
    friction_level: str
    original_task: str
    bad_attempt: str
    user_corrections: str
    final_pattern: str
    inferred_rule: str
    anti_patterns: list[str]
    positive_patterns: list[str]
    evidence_turns: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    generation_mode: str


class EvoMindProposalResponse(BaseModel):
    id: str
    source_case_id: str
    target_type: str
    target: str
    target_path: str
    target_status: str
    lifecycle: str
    title: str
    trigger: str
    rule_text: str
    scope: str
    anti_scope: str
    risk: str
    anti_patterns: list[str]
    positive_patterns: list[str]
    verification_plan: list[str]
    content: str
    created_at: str
    generation_mode: str
    warning: str = ""


@router.post("/cases/scan-codex", response_model=EvoMindCodexScanResponse)
def scan_codex_cases(
    payload: EvoMindCodexScanRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return scan_evomind_cases_from_codex(
            session=session,
            root_dir=payload.root_dir,
            max_threads=payload.max_threads,
            max_cases=payload.max_cases,
            min_score=payload.min_score,
            signal_type_filter=payload.signal_type,
            use_codex_cli=payload.use_codex_cli,
            codex_cli_limit=payload.codex_cli_limit,
            reset_cache=payload.reset_cache,
            scan_rule_text=payload.scan_rule_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"扫描 Codex 会话失败：{exc}") from exc


@router.get("/cases/pending-imports", response_model=EvoMindCodexScanResponse)
def get_pending_case_imports() -> dict[str, Any]:
    try:
        return read_evomind_pending_imports()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取 EvoMind 待导入案例失败：{exc}") from exc


@router.post("/cases/pending-imports/consume")
def consume_pending_case_imports() -> dict[str, bool]:
    try:
        clear_evomind_pending_imports()
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"清理 EvoMind 待导入案例失败：{exc}") from exc


@router.post("/cases/derive-card", response_model=EvoMindCaseCardResponse)
def derive_case_card(payload: EvoMindCaseCardRequest) -> dict[str, Any]:
    try:
        return derive_evomind_case_card(
            case=payload.case.model_dump(),
            case_rule_text=payload.case_rule_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"生成 EvoMind 案例卡失败：{exc}") from exc


@router.post("/proposals/from-case", response_model=EvoMindProposalResponse)
def generate_proposal_from_case(payload: EvoMindProposalRequest) -> dict[str, Any]:
    try:
        return generate_evomind_rule_proposal(
            case=payload.case.model_dump(),
            target=payload.target,
            use_codex_cli=payload.use_codex_cli,
            proposal_rule_text=payload.proposal_rule_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"生成 EvoMind 提案失败：{exc}") from exc
