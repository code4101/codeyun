from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
import uuid
from typing import Any, Literal, Optional, Protocol, Sequence


ReductionInputKind = Literal["source", "summary"]


class HierarchicalReductionError(RuntimeError):
    """Raised when a hierarchical reduction run cannot be completed."""


@dataclass(frozen=True, slots=True)
class ReductionSourceUnit:
    unit_id: str
    content: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReductionChunkItem:
    ref_id: str
    kind: ReductionInputKind
    content: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReductionChunk:
    chunk_id: str
    level: int
    task_type: str
    input_kind: ReductionInputKind
    items: list[ReductionChunkItem]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReductionPrompt:
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True, slots=True)
class ReductionModelResponse:
    model: str
    content: Any
    raw: Any = None


@dataclass(frozen=True, slots=True)
class ReductionSummaryNode:
    node_id: str
    level: int
    task_type: str
    chunk_id: str
    payload: dict[str, Any]
    child_node_ids: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    model: str = ""
    raw_model_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReductionLevelResult:
    level: int
    input_kind: ReductionInputKind
    chunks: list[ReductionChunk]
    nodes: list[ReductionSummaryNode]


@dataclass(frozen=True, slots=True)
class HierarchicalReductionResult:
    run_id: str
    task_type: str
    profile_id: str
    levels: list[ReductionLevelResult]
    root_node: ReductionSummaryNode
    final_result: Any
    root_input_meta: dict[str, Any] = field(default_factory=dict)


class ReductionPromptBuilder(Protocol):
    def __call__(self, chunk: ReductionChunk, *, profile: "ReductionProfile") -> ReductionPrompt: ...


class ReductionChunker(Protocol):
    def __call__(
        self,
        items: Sequence[ReductionSourceUnit | ReductionSummaryNode],
        *,
        level: int,
        input_kind: ReductionInputKind,
        profile: "ReductionProfile",
    ) -> list[ReductionChunk]: ...


class ReductionModelRunner(Protocol):
    def __call__(
        self,
        prompt: ReductionPrompt,
        *,
        chunk: ReductionChunk,
        profile: "ReductionProfile",
    ) -> ReductionModelResponse: ...


class ReductionPayloadParser(Protocol):
    def __call__(
        self,
        response: ReductionModelResponse,
        *,
        chunk: ReductionChunk,
        profile: "ReductionProfile",
    ) -> dict[str, Any]: ...


class ReductionFinalizer(Protocol):
    def __call__(
        self,
        root_node: ReductionSummaryNode,
        *,
        levels: list[ReductionLevelResult],
        profile: "ReductionProfile",
    ) -> Any: ...


class ReductionProgressCallback(Protocol):
    def __call__(self, event: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class ReductionProfile:
    profile_id: str
    task_type: str
    leaf_prompt_builder: ReductionPromptBuilder
    reduce_prompt_builder: ReductionPromptBuilder
    payload_parser: ReductionPayloadParser
    branch_factor: int = 10
    leaf_group_size: int | None = None
    reduce_group_size: int | None = None
    leaf_chunker: Optional[ReductionChunker] = None
    reduce_chunker: Optional[ReductionChunker] = None
    finalizer: Optional[ReductionFinalizer] = None

    def __post_init__(self) -> None:
        if self.branch_factor < 2:
            raise ValueError("branch_factor must be at least 2")
        if self.leaf_group_size is not None and self.leaf_group_size < 1:
            raise ValueError("leaf_group_size must be positive")
        if self.reduce_group_size is not None and self.reduce_group_size < 1:
            raise ValueError("reduce_group_size must be positive")

    @property
    def effective_leaf_group_size(self) -> int:
        return self.leaf_group_size or self.branch_factor

    @property
    def effective_reduce_group_size(self) -> int:
        return self.reduce_group_size or self.branch_factor


def parse_json_object_response(
    response: ReductionModelResponse,
    *,
    chunk: ReductionChunk,
    profile: ReductionProfile,
) -> dict[str, Any]:
    del chunk, profile
    return extract_json_object(response.content)


def extract_json_object(raw_content: Any) -> dict[str, Any]:
    if isinstance(raw_content, dict):
        return dict(raw_content)

    content = str(raw_content or "").strip()
    if not content:
        raise HierarchicalReductionError("模型没有返回可解析的 JSON")

    if content.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", content, re.DOTALL)
        if not match:
            raise HierarchicalReductionError("模型没有返回可解析的 JSON")
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise HierarchicalReductionError("模型返回的 JSON 格式无效") from exc

    if not isinstance(parsed, dict):
        raise HierarchicalReductionError("模型返回的结果不是 JSON 对象")
    return parsed


def run_hierarchical_reduction(
    source_units: Sequence[ReductionSourceUnit],
    *,
    profile: ReductionProfile,
    model_runner: ReductionModelRunner,
    run_id: str | None = None,
    root_input_meta: Optional[dict[str, Any]] = None,
    progress_callback: Optional[ReductionProgressCallback] = None,
) -> HierarchicalReductionResult:
    normalized_units = _normalize_source_units(source_units)
    if not normalized_units:
        raise HierarchicalReductionError("source_units 不能为空")

    current_items: list[ReductionSourceUnit | ReductionSummaryNode] = list(normalized_units)
    current_kind: ReductionInputKind = "source"
    level_index = 0
    levels: list[ReductionLevelResult] = []
    completed_chunk_count = 0

    while True:
        chunks = _build_chunks(current_items, level=level_index, input_kind=current_kind, profile=profile)
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "level_started",
                    "level": level_index,
                    "input_kind": current_kind,
                    "chunk_count": len(chunks),
                    "completed_chunk_count": completed_chunk_count,
                }
            )
        nodes: list[ReductionSummaryNode] = []
        for chunk_index, chunk in enumerate(chunks, start=1):
            node = _execute_chunk(
                chunk,
                profile=profile,
                model_runner=model_runner,
            )
            nodes.append(node)
            completed_chunk_count += 1
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "chunk_completed",
                        "level": level_index,
                        "input_kind": current_kind,
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "completed_level_chunk_count": chunk_index,
                        "completed_chunk_count": completed_chunk_count,
                    }
                )
        levels.append(
            ReductionLevelResult(
                level=level_index,
                input_kind=current_kind,
                chunks=chunks,
                nodes=nodes,
            )
        )

        if len(nodes) == 1:
            root_node = nodes[0]
            break

        current_items = nodes
        current_kind = "summary"
        level_index += 1

    final_result = (
        profile.finalizer(root_node, levels=levels, profile=profile)
        if profile.finalizer is not None
        else root_node.payload
    )
    if progress_callback is not None:
        progress_callback(
            {
                "event": "completed",
                "level": root_node.level,
                "completed_chunk_count": completed_chunk_count,
                "level_count": len(levels),
            }
        )
    return HierarchicalReductionResult(
        run_id=run_id or f"reduce-{uuid.uuid4().hex[:12]}",
        task_type=profile.task_type,
        profile_id=profile.profile_id,
        levels=levels,
        root_node=root_node,
        final_result=final_result,
        root_input_meta=dict(root_input_meta or {}),
    )


def estimate_level_count(unit_count: int, *, branch_factor: int) -> int:
    if unit_count <= 0:
        return 0
    if branch_factor < 2:
        raise ValueError("branch_factor must be at least 2")
    level_count = 1
    current_count = math.ceil(unit_count / branch_factor)
    while current_count > 1:
        current_count = math.ceil(current_count / branch_factor)
        level_count += 1
    return level_count


def _normalize_source_units(source_units: Sequence[ReductionSourceUnit]) -> list[ReductionSourceUnit]:
    normalized: list[ReductionSourceUnit] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(source_units):
        unit_id = str(item.unit_id or "").strip()
        if not unit_id:
            raise HierarchicalReductionError(f"第 {index + 1} 个 source unit 缺少 unit_id")
        if unit_id in seen_ids:
            raise HierarchicalReductionError(f"source unit id 重复：{unit_id}")
        seen_ids.add(unit_id)
        normalized.append(
            ReductionSourceUnit(
                unit_id=unit_id,
                content=item.content,
                metadata=dict(item.metadata or {}),
            )
        )
    return normalized


def _build_chunks(
    items: Sequence[ReductionSourceUnit | ReductionSummaryNode],
    *,
    level: int,
    input_kind: ReductionInputKind,
    profile: ReductionProfile,
) -> list[ReductionChunk]:
    chunker = profile.leaf_chunker if input_kind == "source" else profile.reduce_chunker
    if chunker is not None:
        chunks = chunker(items, level=level, input_kind=input_kind, profile=profile)
    else:
        chunks = _default_chunker(items, level=level, input_kind=input_kind, profile=profile)

    if not chunks:
        raise HierarchicalReductionError(f"第 {level} 层没有生成任何 chunk")
    return chunks


def _default_chunker(
    items: Sequence[ReductionSourceUnit | ReductionSummaryNode],
    *,
    level: int,
    input_kind: ReductionInputKind,
    profile: ReductionProfile,
) -> list[ReductionChunk]:
    group_size = (
        profile.effective_leaf_group_size
        if input_kind == "source"
        else profile.effective_reduce_group_size
    )
    grouped: list[ReductionChunk] = []
    for index in range(0, len(items), group_size):
        batch = list(items[index:index + group_size])
        if not batch:
            continue
        grouped.append(
            ReductionChunk(
                chunk_id=f"{profile.profile_id}:L{level}:C{len(grouped)}",
                level=level,
                task_type=profile.task_type,
                input_kind=input_kind,
                items=[_to_chunk_item(item) for item in batch],
                metadata={
                    "item_count": len(batch),
                },
            )
        )
    return grouped


def _to_chunk_item(item: ReductionSourceUnit | ReductionSummaryNode) -> ReductionChunkItem:
    if isinstance(item, ReductionSourceUnit):
        return ReductionChunkItem(
            ref_id=item.unit_id,
            kind="source",
            content=item.content,
            metadata=dict(item.metadata or {}),
        )
    return ReductionChunkItem(
        ref_id=item.node_id,
        kind="summary",
        content=dict(item.payload or {}),
        metadata={
            **dict(item.metadata or {}),
            "source_refs": list(item.source_refs),
            "child_node_ids": list(item.child_node_ids),
            "payload": dict(item.payload or {}),
        },
    )


def _execute_chunk(
    chunk: ReductionChunk,
    *,
    profile: ReductionProfile,
    model_runner: ReductionModelRunner,
) -> ReductionSummaryNode:
    prompt_builder = profile.leaf_prompt_builder if chunk.input_kind == "source" else profile.reduce_prompt_builder
    prompt = prompt_builder(chunk, profile=profile)
    try:
        response = model_runner(prompt, chunk=chunk, profile=profile)
        payload = profile.payload_parser(response, chunk=chunk, profile=profile)
    except Exception as exc:
        raise HierarchicalReductionError(
            f"第 {chunk.level} 层 chunk {chunk.chunk_id} 执行失败：{exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise HierarchicalReductionError(
            f"第 {chunk.level} 层 chunk {chunk.chunk_id} 返回结果不是 JSON 对象"
        )

    return ReductionSummaryNode(
        node_id=f"{chunk.chunk_id}:N0",
        level=chunk.level,
        task_type=chunk.task_type,
        chunk_id=chunk.chunk_id,
        payload=dict(payload),
        child_node_ids=[item.ref_id for item in chunk.items if item.kind == "summary"],
        source_refs=_collect_source_refs(chunk),
        model=str(response.model or ""),
        raw_model_output=str(response.content or ""),
        metadata={
            "input_kind": chunk.input_kind,
            "item_count": len(chunk.items),
        },
    )


def _collect_source_refs(chunk: ReductionChunk) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for item in chunk.items:
        if item.kind == "source":
            candidates = [item.ref_id]
        else:
            source_refs = item.metadata.get("source_refs")
            if isinstance(source_refs, list) and source_refs:
                candidates = [str(value).strip() for value in source_refs if str(value).strip()]
            else:
                candidates = [item.ref_id]
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                refs.append(candidate)
    return refs
