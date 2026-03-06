from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import inspect
import re
from typing import Any, Callable, Dict, Iterable, Iterator, List, Literal, Optional, Sequence, Tuple

from backend.models import NoteEdge, NoteNode

Predicate = Callable[["NoteVisit"], bool]
TransitionFilter = Callable[["NoteVisit", NoteEdge, "NoteVisit"], bool]
TraversalDirection = Literal["both", "incoming", "outgoing"]
ComponentMode = Literal["planetary", "satellite"]
CompareOp = Literal["eq", "neq", "in", "not_in", "contains", "not_contains", "regex_search", "gte", "lte", "between"]


def _to_list(values: str | Sequence[Any]) -> List[Any]:
    if isinstance(values, str):
        return [values]
    return list(values)


def _normalize_text(value: Any, ignore_case: bool) -> str:
    text = "" if value is None else str(value)
    return text.lower() if ignore_case else text


def _get_note_value(note: NoteNode, field_name: str) -> Any:
    if field_name.startswith("custom_fields."):
        key = field_name.split(".", 1)[1]
        custom_fields = note.custom_fields or []
        if isinstance(custom_fields, list):
            for item in custom_fields:
                if isinstance(item, list) and len(item) >= 3 and item[0] == key:
                    return item[2]
        elif isinstance(custom_fields, dict):
            return custom_fields.get(key)
        return None

    return getattr(note, field_name, None)


def _matches_value(field_value: Any, op: CompareOp, value: Any = None, values: Optional[Sequence[Any]] = None) -> bool:
    values = list(values or [])

    if op == "eq":
        return field_value == value
    if op == "neq":
        return field_value != value
    if op == "in":
        return field_value in values
    if op == "not_in":
        return field_value not in values
    if op == "contains":
        if field_value is None:
            return False
        return _normalize_text(value, True) in _normalize_text(field_value, True)
    if op == "not_contains":
        if field_value is None:
            return True
        return _normalize_text(value, True) not in _normalize_text(field_value, True)
    if op == "regex_search":
        if field_value is None:
            return False
        try:
            return re.search(str(value or ""), str(field_value)) is not None
        except re.error:
            return False
    if op == "gte":
        return field_value is not None and field_value >= value
    if op == "lte":
        return field_value is not None and field_value <= value
    if op == "between":
        if len(values) < 2 or field_value is None:
            return False
        return values[0] <= field_value <= values[1]

    raise ValueError(f"Unsupported compare op: {op}")


def _shift_month(year: int, month: int, offset: int) -> Tuple[int, int]:
    month_index = (year * 12 + (month - 1)) + offset
    shifted_year, shifted_month_index = divmod(month_index, 12)
    return shifted_year, shifted_month_index + 1


def _resolve_relative_time_point(
    unit: Literal["day", "week", "month"],
    offset: int,
    boundary: Literal["start", "end"],
    *,
    base_time: Optional[datetime] = None,
) -> float:
    current = base_time or datetime.now().astimezone()
    tzinfo = current.tzinfo

    if unit == "month":
        start_year, start_month = _shift_month(current.year, current.month, offset)
        end_year, end_month = _shift_month(current.year, current.month, offset + 1)
        start_at = datetime(start_year, start_month, 1, 0, 0, 0, 0, tzinfo=tzinfo)
        end_at = datetime(end_year, end_month, 1, 0, 0, 0, 0, tzinfo=tzinfo)
        return start_at.timestamp() if boundary == "start" else end_at.timestamp() - 0.001

    if unit == "week":
        week_start = datetime(current.year, current.month, current.day, 0, 0, 0, 0, tzinfo=tzinfo)
        week_start = week_start - timedelta(days=week_start.weekday())
        start_at = week_start + timedelta(weeks=offset)
        end_at = start_at + timedelta(weeks=1)
        return start_at.timestamp() if boundary == "start" else end_at.timestamp() - 0.001

    day_start = datetime(current.year, current.month, current.day, 0, 0, 0, 0, tzinfo=tzinfo) + timedelta(days=offset)
    day_end = day_start + timedelta(days=1)
    return day_start.timestamp() if boundary == "start" else day_end.timestamp() - 0.001


def _relative_month_window_bounds(
    start_offset: int,
    end_offset: int,
    *,
    base_time: Optional[datetime] = None,
) -> Tuple[float, float]:
    start_at = _resolve_relative_time_point("month", start_offset, "start", base_time=base_time)
    end_at = _resolve_relative_time_point("month", end_offset, "end", base_time=base_time)
    return start_at, end_at


def _get_expr_attr(expr: Any, key: str, default: Any = None) -> Any:
    if expr is None:
        return default
    if isinstance(expr, dict):
        return expr.get(key, default)
    return getattr(expr, key, default)


def _resolve_time_point_expr(expr: Any, *, base_time: Optional[datetime] = None) -> Optional[float]:
    kind = _get_expr_attr(expr, "kind", "absolute")

    if kind == "relative":
        unit = _get_expr_attr(expr, "unit", "month")
        if unit not in {"day", "week", "month"}:
            unit = "month"
        boundary = _get_expr_attr(expr, "boundary", "start")
        if boundary not in {"start", "end"}:
            boundary = "start"
        offset = _get_expr_attr(expr, "offset", 0)
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            offset = 0
        return _resolve_relative_time_point(unit, offset, boundary, base_time=base_time)

    value = _get_expr_attr(expr, "value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class NoteVisit:
    node: NoteNode
    context: "NoteGraphContext" = field(repr=False, compare=False)
    depth: int = 0
    parent_id: Optional[str] = None
    seed_id: Optional[str] = None
    via_edge: Optional[NoteEdge] = None

    @property
    def node_id(self) -> str:
        return str(self.node.id)

    @property
    def is_seed(self) -> bool:
        return self.depth == 0


@dataclass(slots=True)
class NoteWalkResult:
    visits: List[NoteVisit]
    nodes: List[NoteNode]
    edges: List[NoteEdge]

    @property
    def node_ids(self) -> List[str]:
        return [str(node.id) for node in self.nodes]

    @property
    def edge_ids(self) -> List[str]:
        return [str(edge.id) for edge in self.edges]


@dataclass(slots=True)
class NoteGraphContext:
    notes_by_id: Dict[str, NoteNode]
    edges: List[NoteEdge]
    outgoing_edges: Dict[str, List[NoteEdge]] = field(init=False, repr=False)
    incoming_edges: Dict[str, List[NoteEdge]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        outgoing: Dict[str, List[NoteEdge]] = defaultdict(list)
        incoming: Dict[str, List[NoteEdge]] = defaultdict(list)

        for edge in self.edges:
            source_id = str(edge.source_id)
            target_id = str(edge.target_id)
            if source_id not in self.notes_by_id or target_id not in self.notes_by_id:
                continue
            outgoing[source_id].append(edge)
            incoming[target_id].append(edge)

        self.outgoing_edges = dict(outgoing)
        self.incoming_edges = dict(incoming)

    @classmethod
    def from_items(cls, notes: Iterable[NoteNode], edges: Iterable[NoteEdge]) -> "NoteGraphContext":
        note_map = {str(note.id): note for note in notes}
        return cls(notes_by_id=note_map, edges=list(edges))

    def get_note(self, note_id: str) -> Optional[NoteNode]:
        return self.notes_by_id.get(str(note_id))

    def iter_notes(self, note_ids: Optional[Iterable[str]] = None) -> Iterator[NoteNode]:
        if note_ids is None:
            yield from self.notes_by_id.values()
            return

        for note_id in note_ids:
            note = self.get_note(str(note_id))
            if note is not None:
                yield note

    def iter_neighbors(self, note_id: str, direction: TraversalDirection = "both") -> Iterator[Tuple[NoteEdge, NoteNode]]:
        note_id = str(note_id)

        if direction in {"both", "outgoing"}:
            for edge in self.outgoing_edges.get(note_id, []):
                neighbor = self.get_note(str(edge.target_id))
                if neighbor is not None:
                    yield edge, neighbor

        if direction in {"both", "incoming"}:
            for edge in self.incoming_edges.get(note_id, []):
                neighbor = self.get_note(str(edge.source_id))
                if neighbor is not None:
                    yield edge, neighbor

    def iter_component_neighbors(
        self,
        note_id: str,
        *,
        blocked_target_ids: Optional[Iterable[str]] = None,
    ) -> Iterator[Tuple[NoteEdge, NoteNode]]:
        note_id = str(note_id)
        blocked_ids = {str(note_id) for note_id in blocked_target_ids or []}

        for edge in self.outgoing_edges.get(note_id, []):
            if str(edge.target_id) in blocked_ids:
                continue
            neighbor = self.get_note(str(edge.target_id))
            if neighbor is not None:
                yield edge, neighbor

        for edge in self.incoming_edges.get(note_id, []):
            if str(edge.target_id) in blocked_ids:
                continue
            neighbor = self.get_note(str(edge.source_id))
            if neighbor is not None:
                yield edge, neighbor

    def induced_edges(self, node_ids: Iterable[str]) -> List[NoteEdge]:
        selected_ids = {str(note_id) for note_id in node_ids}
        return [
            edge for edge in self.edges
            if str(edge.source_id) in selected_ids and str(edge.target_id) in selected_ids
        ]


class NoteFilterFactory:
    """Build reusable predicates for NoteVisit objects."""

    @classmethod
    def custom(cls, func: Predicate) -> Predicate:
        return func

    @classmethod
    def all(cls) -> Predicate:
        return lambda visit: True

    @classmethod
    def none(cls) -> Predicate:
        return lambda visit: False

    @classmethod
    def is_seed(cls) -> Predicate:
        return lambda visit: visit.is_seed

    @classmethod
    def match_id(cls, note_ids: str | Sequence[str]) -> Predicate:
        note_ids = {str(note_id) for note_id in _to_list(note_ids)}
        return lambda visit: visit.node_id in note_ids

    @classmethod
    def match_field(
        cls,
        field_name: str,
        op: CompareOp = "eq",
        *,
        value: Any = None,
        values: Optional[Sequence[Any]] = None,
    ) -> Predicate:
        return lambda visit: _matches_value(_get_note_value(visit.node, field_name), op, value=value, values=values)

    @classmethod
    def match_custom_field(
        cls,
        key: str,
        op: CompareOp = "eq",
        *,
        value: Any = None,
        values: Optional[Sequence[Any]] = None,
    ) -> Predicate:
        return cls.match_field(f"custom_fields.{key}", op, value=value, values=values)

    @classmethod
    def match_title(cls, text: str, *, ignore_case: bool = True) -> Predicate:
        expected = _normalize_text(text, ignore_case)

        def _check(visit: NoteVisit) -> bool:
            title = _normalize_text(visit.node.title, ignore_case)
            return expected in title

        return _check

    @classmethod
    def match_depth(cls, *, min_depth: int = 0, max_depth: Optional[int] = None) -> Predicate:
        def _check(visit: NoteVisit) -> bool:
            if visit.depth < min_depth:
                return False
            if max_depth is not None and visit.depth > max_depth:
                return False
            return True

        return _check

    @classmethod
    def relative_month_window(
        cls,
        *,
        field: str = "start_at",
        start_month_offset: int = -1,
        end_month_offset: int = 1,
        base_time: Optional[datetime] = None,
    ) -> Predicate:
        start_at, end_at = _relative_month_window_bounds(
            start_month_offset,
            end_month_offset,
            base_time=base_time,
        )

        return cls.match_field(field, "between", values=[start_at, end_at])

    @classmethod
    def match_time_field(
        cls,
        field_name: str,
        op: CompareOp = "eq",
        *,
        time_value: Any = None,
        time_values: Optional[Sequence[Any]] = None,
        base_time: Optional[datetime] = None,
    ) -> Predicate:
        if op == "between":
            resolved_values = [
                resolved for resolved in (
                    _resolve_time_point_expr(expr, base_time=base_time) for expr in (time_values or [])
                )
                if resolved is not None
            ]
            if len(resolved_values) < 2:
                return cls.none()
            return cls.match_field(field_name, op, values=resolved_values[:2])

        resolved_value = _resolve_time_point_expr(time_value, base_time=base_time)
        if resolved_value is None:
            return cls.none()
        return cls.match_field(field_name, op, value=resolved_value)

    @classmethod
    def has_neighbor(cls, note_ids: str | Sequence[str], *, direction: TraversalDirection = "both") -> Predicate:
        note_ids = {str(note_id) for note_id in _to_list(note_ids)}

        def _check(visit: NoteVisit) -> bool:
            for _, neighbor in visit.context.iter_neighbors(visit.node_id, direction):
                if str(neighbor.id) in note_ids:
                    return True
            return False

        return _check


class RuleBuilder:
    """Compose predicates onto a target decision channel."""

    def __init__(
        self,
        parent: "NoteWalker",
        rules: List[Tuple[Predicate, bool]],
        target: bool,
        base_condition: Optional[Predicate] = None,
    ) -> None:
        self.parent = parent
        self.rules = rules
        self.target = target
        self.base_condition = base_condition

    def __getattr__(self, method: str):
        rule_factory = getattr(NoteFilterFactory, method)

        def wrapper(*args, **kwargs):
            sig = inspect.signature(rule_factory)
            if "context" in sig.parameters and "context" not in kwargs:
                kwargs["context"] = self.parent.context

            predicate = rule_factory(*args, **kwargs)

            if self.base_condition:
                base_condition = self.base_condition
                original_predicate = predicate

                def composite_predicate(visit: NoteVisit) -> bool:
                    return base_condition(visit) and original_predicate(visit)

                final_predicate = composite_predicate
            else:
                final_predicate = predicate

            self.rules.append((final_predicate, self.target))
            return self.parent

        return wrapper


class NoteWalker:
    """
    walker.py inspired note filtering engine.

    The core is still `_check`: each channel starts from a default decision,
    then later rules can flip that decision back and forth in order.
    """

    def __init__(
        self,
        context: NoteGraphContext,
        *,
        expand: bool = False,
        select: bool = False,
    ) -> None:
        self.context = context
        self.default_expand = expand
        self.default_select = select
        self.expand_rules: List[Tuple[Predicate, bool]] = []
        self.select_rules: List[Tuple[Predicate, bool]] = []

    @property
    def expand(self) -> RuleBuilder:
        return RuleBuilder(self, self.expand_rules, True)

    @property
    def skip_expand(self) -> RuleBuilder:
        return RuleBuilder(self, self.expand_rules, False)

    @property
    def enter(self) -> RuleBuilder:
        return self.expand

    @property
    def skip(self) -> RuleBuilder:
        return self.skip_expand

    @property
    def include(self) -> RuleBuilder:
        return RuleBuilder(self, self.select_rules, True)

    @property
    def exclude(self) -> RuleBuilder:
        return RuleBuilder(self, self.select_rules, False)

    @property
    def include_seed(self) -> RuleBuilder:
        return RuleBuilder(self, self.select_rules, True, base_condition=NoteFilterFactory.is_seed())

    @property
    def exclude_seed(self) -> RuleBuilder:
        return RuleBuilder(self, self.select_rules, False, base_condition=NoteFilterFactory.is_seed())

    def should_expand(self, visit: NoteVisit) -> bool:
        return self._check(visit, self.expand_rules, default=self.default_expand)

    def should_select(self, visit: NoteVisit) -> bool:
        return self._check(visit, self.select_rules, default=self.default_select)

    def _check(self, visit: NoteVisit, rules: List[Tuple[Predicate, bool]], *, default: bool) -> bool:
        decision = default
        for predicate, target_action in rules:
            if decision == target_action:
                continue
            if predicate(visit):
                decision = target_action
        return decision

    def iter_all(self, note_ids: Optional[Iterable[str]] = None) -> Iterator[NoteVisit]:
        for note in self.context.iter_notes(note_ids):
            visit = NoteVisit(
                node=note,
                context=self.context,
                depth=0,
                parent_id=None,
                seed_id=str(note.id),
                via_edge=None,
            )
            if self.should_select(visit):
                yield visit

    def collect_all(self, note_ids: Optional[Iterable[str]] = None, *, include_edges: bool = False) -> NoteWalkResult:
        visits = list(self.iter_all(note_ids))
        return self._build_result(visits, include_edges=include_edges)

    def iter_graph(
        self,
        seed_ids: Iterable[str],
        *,
        direction: TraversalDirection = "both",
        max_depth: Optional[int] = None,
        transition_filter: Optional[TransitionFilter] = None,
    ) -> Iterator[NoteVisit]:
        queue: deque[NoteVisit] = deque()
        visited: set[str] = set()

        for seed_id in seed_ids:
            note = self.context.get_note(str(seed_id))
            if note is None:
                continue
            note_id = str(note.id)
            if note_id in visited:
                continue
            visited.add(note_id)
            queue.append(
                NoteVisit(
                    node=note,
                    context=self.context,
                    depth=0,
                    parent_id=None,
                    seed_id=note_id,
                    via_edge=None,
                )
            )

        while queue:
            visit = queue.popleft()

            if self.should_select(visit):
                yield visit

            if max_depth is not None and visit.depth >= max_depth:
                continue
            if not self.should_expand(visit):
                continue

            for edge, neighbor in self.context.iter_neighbors(visit.node_id, direction):
                next_id = str(neighbor.id)
                if next_id in visited:
                    continue

                next_visit = NoteVisit(
                    node=neighbor,
                    context=self.context,
                    depth=visit.depth + 1,
                    parent_id=visit.node_id,
                    seed_id=visit.seed_id or visit.node_id,
                    via_edge=edge,
                )

                if transition_filter and not transition_filter(visit, edge, next_visit):
                    continue

                visited.add(next_id)
                queue.append(next_visit)

    def collect_graph(
        self,
        seed_ids: Iterable[str],
        *,
        direction: TraversalDirection = "both",
        max_depth: Optional[int] = None,
        transition_filter: Optional[TransitionFilter] = None,
        include_edges: bool = True,
    ) -> NoteWalkResult:
        visits = list(
            self.iter_graph(
                seed_ids,
                direction=direction,
                max_depth=max_depth,
                transition_filter=transition_filter,
            )
        )
        return self._build_result(visits, include_edges=include_edges)

    def iter_component(
        self,
        seed_ids: Iterable[str],
        *,
        mode: ComponentMode = "planetary",
        max_depth: Optional[int] = None,
        transition_filter: Optional[TransitionFilter] = None,
    ) -> Iterator[NoteVisit]:
        queue: deque[NoteVisit] = deque()
        visited: set[str] = set()
        seed_ids = [str(seed_id) for seed_id in seed_ids]
        blocked_target_ids = set(seed_ids) if mode == "satellite" else set()

        for seed_id in seed_ids:
            note = self.context.get_note(seed_id)
            if note is None or seed_id in visited:
                continue
            visited.add(seed_id)
            queue.append(
                NoteVisit(
                    node=note,
                    context=self.context,
                    depth=0,
                    parent_id=None,
                    seed_id=seed_id,
                    via_edge=None,
                )
            )

        while queue:
            visit = queue.popleft()

            if self.should_select(visit):
                yield visit

            if max_depth is not None and visit.depth >= max_depth:
                continue
            if not self.should_expand(visit):
                continue

            for edge, neighbor in self.context.iter_component_neighbors(
                visit.node_id,
                blocked_target_ids=blocked_target_ids,
            ):
                next_id = str(neighbor.id)
                if next_id in visited:
                    continue

                next_visit = NoteVisit(
                    node=neighbor,
                    context=self.context,
                    depth=visit.depth + 1,
                    parent_id=visit.node_id,
                    seed_id=visit.seed_id or visit.node_id,
                    via_edge=edge,
                )

                if transition_filter and not transition_filter(visit, edge, next_visit):
                    continue

                visited.add(next_id)
                queue.append(next_visit)

    def collect_component(
        self,
        seed_ids: Iterable[str],
        *,
        mode: ComponentMode = "planetary",
        max_depth: Optional[int] = None,
        transition_filter: Optional[TransitionFilter] = None,
        include_edges: bool = True,
    ) -> NoteWalkResult:
        visits = list(
            self.iter_component(
                seed_ids,
                mode=mode,
                max_depth=max_depth,
                transition_filter=transition_filter,
            )
        )
        return self._build_result(visits, include_edges=include_edges)

    def _build_result(self, visits: List[NoteVisit], *, include_edges: bool) -> NoteWalkResult:
        nodes = [visit.node for visit in visits]
        node_ids = [visit.node_id for visit in visits]
        edges = self.context.induced_edges(node_ids) if include_edges else []
        return NoteWalkResult(visits=visits, nodes=nodes, edges=edges)
