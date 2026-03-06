from typing import Any, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, func, or_
from sqlalchemy.orm.attributes import flag_modified
from backend.db import get_session
from backend.models import NoteNode, NoteEdge, User
from backend.schemas import (
    NoteCreate,
    NoteRead,
    NoteUpdate,
    EdgeCreate,
    EdgeRead,
    NoteListRead,
    GraphData,
    NoteFilterRule,
    NoteQueryRequest,
    NoteQueryResponse,
    NoteProgramRequest,
    NoteProgramResponse,
)
from backend.core.auth import get_current_active_user
from backend.core.note_walker import NoteGraphContext, NoteWalker
import time
import uuid

router = APIRouter()

ALLOWED_ORDER_FIELDS = {"updated_at", "created_at", "start_at", "weight", "title", "private_level"}


def _get_filtered_edge_pool(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> List[NoteEdge]:
    edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    if mode == "satellite":
        edges = [edge for edge in edges if edge.target_id != seed_note_id]
    return edges


def _get_component_note_ids(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> Tuple[set[str], List[NoteEdge]]:
    start_note = session.exec(
        select(NoteNode).where(NoteNode.id == seed_note_id, NoteNode.user_id == user_id)
    ).first()
    if not start_note:
        raise HTTPException(status_code=404, detail="Note not found")

    edges = _get_filtered_edge_pool(seed_note_id, mode, user_id, session)
    adj: dict[str, list[str]] = {}
    for edge in edges:
        u, v = edge.source_id, edge.target_id
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)

    visited = {seed_note_id}
    queue = [seed_note_id]
    while queue:
        curr = queue.pop(0)
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    return visited, edges


def _get_rule_value(note: NoteNode, field: str):
    if field.startswith("custom_fields."):
        key = field.split(".", 1)[1]
        custom_fields = note.custom_fields or []
        if isinstance(custom_fields, list):
            for item in custom_fields:
                if isinstance(item, list) and len(item) >= 3 and item[0] == key:
                    return item[2]
        elif isinstance(custom_fields, dict):
            return custom_fields.get(key)
        return None

    return getattr(note, field, None)


def _matches_rule(note: NoteNode, rule: NoteFilterRule) -> bool:
    field_value = _get_rule_value(note, rule.field)
    op = rule.op

    if op == "eq":
        return field_value == rule.value
    if op == "neq":
        return field_value != rule.value
    if op == "in":
        return field_value in rule.values
    if op == "not_in":
        return field_value not in rule.values
    if op == "contains":
        if field_value is None:
            return False
        return str(rule.value or "").lower() in str(field_value).lower()
    if op == "gte":
        return field_value is not None and field_value >= rule.value
    if op == "lte":
        return field_value is not None and field_value <= rule.value
    if op == "between":
        if len(rule.values) < 2 or field_value is None:
            return False
        start, end = rule.values[0], rule.values[1]
        return start <= field_value <= end

    return True


def _apply_sql_rule(query, rule: NoteFilterRule):
    if rule.field.startswith("custom_fields."):
        return query, False

    column = getattr(NoteNode, rule.field, None)
    if column is None:
        return query, False

    if rule.op == "eq":
        return query.where(column == rule.value), True
    if rule.op == "neq":
        return query.where(column != rule.value), True
    if rule.op == "in" and rule.values:
        return query.where(column.in_(rule.values)), True
    if rule.op == "not_in" and rule.values:
        return query.where(~column.in_(rule.values)), True
    if rule.op == "gte":
        return query.where(column >= rule.value), True
    if rule.op == "lte":
        return query.where(column <= rule.value), True
    if rule.op == "between" and len(rule.values) >= 2:
        return query.where(column >= rule.values[0]).where(column <= rule.values[1]), True
    if rule.op == "contains" and rule.field == "title" and rule.value is not None:
        return query.where(NoteNode.title.contains(str(rule.value))), True

    return query, False


def _sort_notes(notes: List[NoteNode], order_by: str, order_desc: bool) -> List[NoteNode]:
    sort_field = order_by if order_by in ALLOWED_ORDER_FIELDS else "updated_at"
    return sorted(
        notes,
        key=lambda note: (_get_rule_value(note, sort_field) is None, _get_rule_value(note, sort_field)),
        reverse=order_desc
    )


def _load_note_context(user_id: int, session: Session, *, include_edges: bool) -> NoteGraphContext:
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == user_id)).all()
    edges = []
    if include_edges:
        edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    return NoteGraphContext.from_items(notes, edges)


def _apply_program_matcher(builder, matcher) -> None:
    if matcher.kind == "all":
        builder.all()
        return
    if matcher.kind == "none":
        builder.none()
        return
    if matcher.kind == "id":
        builder.match_id(matcher.ids)
        return
    if matcher.kind == "field":
        if not matcher.field:
            raise HTTPException(status_code=400, detail="field matcher requires a field name")
        is_time_field = matcher.field in {"start_at", "updated_at"}
        if is_time_field and (matcher.time_value is not None or matcher.time_values):
            builder.match_time_field(
                matcher.field,
                matcher.op or "eq",
                time_value=matcher.time_value,
                time_values=matcher.time_values,
            )
            return
        builder.match_field(
            matcher.field,
            matcher.op or "eq",
            value=matcher.value,
            values=matcher.values,
        )
        return
    if matcher.kind == "title_contains":
        builder.match_title(str(matcher.value or ""), ignore_case=matcher.ignore_case)
        return
    if matcher.kind == "seed":
        builder.is_seed()
        return
    if matcher.kind == "depth":
        builder.match_depth(min_depth=matcher.min_depth, max_depth=matcher.max_depth)
        return
    if matcher.kind == "relative_month_window":
        builder.relative_month_window(
            field=matcher.field or "start_at",
            start_month_offset=matcher.start_month_offset,
            end_month_offset=matcher.end_month_offset,
        )
        return

    raise HTTPException(status_code=400, detail=f"Unsupported matcher kind: {matcher.kind}")


def _build_program_walker(context: NoteGraphContext, request: NoteProgramRequest) -> NoteWalker:
    walker = NoteWalker(
        context,
        expand=request.program.expand.default,
        select=request.program.select.default,
    )

    for rule in request.program.expand.rules:
        builder = walker.expand if rule.action == "include" else walker.skip_expand
        _apply_program_matcher(builder, rule.matcher)

    for rule in request.program.select.rules:
        builder = walker.include if rule.action == "include" else walker.exclude
        _apply_program_matcher(builder, rule.matcher)

    return walker


def _ensure_seed_ids_exist(context: NoteGraphContext, seed_ids: List[str]) -> None:
    missing = [seed_id for seed_id in seed_ids if context.get_note(seed_id) is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Seed notes not found: {', '.join(missing)}")


def _execute_note_program(
    request: NoteProgramRequest,
    *,
    user_id: int,
    session: Session,
):
    need_edges = request.result.include_edges or request.executor.kind == "component"
    context = _load_note_context(user_id, session, include_edges=need_edges)
    walker = _build_program_walker(context, request)

    if request.executor.kind == "scan":
        walk_result = walker.collect_all(include_edges=request.result.include_edges)
    elif request.executor.kind == "component":
        if not request.executor.seed_ids:
            raise HTTPException(status_code=400, detail="component executor requires seed_ids")
        _ensure_seed_ids_exist(context, request.executor.seed_ids)
        walk_result = walker.collect_component(
            request.executor.seed_ids,
            mode=request.executor.mode,
            max_depth=request.executor.max_depth,
            include_edges=request.result.include_edges,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported executor kind: {request.executor.kind}")

    sorted_nodes = _sort_notes(walk_result.nodes, request.result.order_by, request.result.order_desc)
    total_nodes = len(sorted_nodes)
    visible_nodes = sorted_nodes[request.result.skip: request.result.skip + request.result.limit]
    visible_ids = {node.id for node in visible_nodes}

    if request.result.include_edges:
        visible_edges = [
            edge for edge in walk_result.edges
            if edge.source_id in visible_ids and edge.target_id in visible_ids
        ]
    else:
        visible_edges = []

    return {
        "nodes": visible_nodes,
        "edges": visible_edges,
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges),
    }

# --- Notes ---

@router.get("/", response_model=List[NoteListRead])
def read_notes(
    skip: int = 0,
    limit: int = 128, # Default to 128 as requested
    created_start: Optional[float] = Query(None, description="Filter by start_at >= start"),
    created_end: Optional[float] = Query(None, description="Filter by start_at <= end"),
    updated_start: Optional[float] = Query(None, description="Filter by updated_at >= start"),
    updated_end: Optional[float] = Query(None, description="Filter by updated_at <= end"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve notes for the current user.
    Supports filtering by start_at (mapped from created_*) and update time range.
    Default limit is 128.
    """
    query = select(NoteNode).where(NoteNode.user_id == current_user.id)
    
    # Apply Time Filters
    # Note: 'created_start/end' params now filter by 'start_at' field
    if created_start is not None:
        query = query.where(NoteNode.start_at >= created_start)
    if created_end is not None:
        query = query.where(NoteNode.start_at <= created_end)
        
    if updated_start is not None:
        query = query.where(NoteNode.updated_at >= updated_start)
    if updated_end is not None:
        query = query.where(NoteNode.updated_at <= updated_end)
        
    # Order by updated_at desc to get the "latest" ones first
    query = query.order_by(NoteNode.updated_at.desc())
    
    statement = query.offset(skip).limit(limit)
    notes = session.exec(statement).all()
    return notes


@router.post("/query", response_model=NoteQueryResponse)
def query_notes(
    request: NoteQueryRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Query a reusable note set using a generic scope + rules model.
    """
    scope = request.scope
    query = select(NoteNode).where(NoteNode.user_id == current_user.id)

    edge_pool: List[NoteEdge] = []
    if scope.mode in {"planetary", "satellite"}:
        if not scope.seed_note_id:
            raise HTTPException(status_code=400, detail="seed_note_id is required for graph scopes")
        note_ids, edge_pool = _get_component_note_ids(
            scope.seed_note_id,
            scope.mode,
            current_user.id,
            session
        )
        if not note_ids:
            return {"nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0}
        query = query.where(NoteNode.id.in_(note_ids))

    python_rules: List[NoteFilterRule] = []
    for rule in request.rules:
        query, handled = _apply_sql_rule(query, rule)
        if not handled:
            python_rules.append(rule)

    candidate_notes = session.exec(query).all()
    filtered_notes = [note for note in candidate_notes if all(_matches_rule(note, rule) for rule in python_rules)]
    sorted_notes = _sort_notes(filtered_notes, request.order_by, request.order_desc)

    total_nodes = len(sorted_notes)
    visible_notes = sorted_notes[request.skip: request.skip + request.limit]
    visible_ids = {note.id for note in visible_notes}

    if request.include_edges:
        if not edge_pool:
            edge_pool = session.exec(select(NoteEdge).where(NoteEdge.user_id == current_user.id)).all()
        visible_edges = [
            edge for edge in edge_pool
            if edge.source_id in visible_ids and edge.target_id in visible_ids
        ]
    else:
        visible_edges = []

    return {
        "nodes": visible_notes,
        "edges": visible_edges,
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges)
    }


@router.post("/query-program", response_model=NoteProgramResponse)
def query_note_program(
    request: NoteProgramRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Execute a walker-style filtering program over the current user's note graph.
    """
    return _execute_note_program(request, user_id=current_user.id, session=session)

@router.post("/", response_model=NoteRead)
def create_note(
    note: NoteCreate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a new note.
    """
    current_time = time.time()
    db_note = NoteNode(
        id=str(uuid.uuid4()), # Generate UUID
        user_id=current_user.id,
        title=note.title,
        content=note.content,
        weight=note.weight,
        node_type=note.node_type,
        node_status=note.node_status,
        private_level=note.private_level,
        custom_fields=note.custom_fields,
        # parent_id=note.parent_id, # Deprecated
        created_at=current_time,
        updated_at=current_time,
        start_at=note.start_at if note.start_at is not None else current_time,
        history=[]
    )
    
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    return db_note

@router.get("/{note_id}", response_model=NoteRead)
def read_note(
    note_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get a specific note.
    """
    statement = select(NoteNode).where(NoteNode.id == note_id, NoteNode.user_id == current_user.id)
    note = session.exec(statement).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    # Calculate edge count
    edge_count = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == current_user.id,
            or_(NoteEdge.source_id == note_id, NoteEdge.target_id == note_id)
        )
    ).one()
    
    # Calculate out_degree (for Satellite mode)
    out_degree = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == current_user.id,
            NoteEdge.source_id == note_id
        )
    ).one()

    # --- Inherited Custom Fields Logic ---
    # 1. Fetch direct parents (incoming edges)
    direct_parent_edges = session.exec(
        select(NoteEdge).where(
            NoteEdge.user_id == current_user.id,
            NoteEdge.target_id == note_id
        )
    ).all()
    
    parent_ids = [e.source_id for e in direct_parent_edges]
    
    # 2. Fetch parent nodes
    parent_nodes = []
    if parent_ids:
        parent_nodes = session.exec(
            select(NoteNode).where(
                NoteNode.id.in_(parent_ids),
                NoteNode.user_id == current_user.id
            )
        ).all()

    # 3. Ancestors (Simplified: just one level up for now or BFS for full ancestors?)
    # User asked for "Direct Parent" and "Other Indirect Ancestors".
    # For performance, let's go up 2-3 levels or fetch all reachable ancestors?
    # Fetching all ancestors in a graph can be heavy.
    # Let's implement a limited BFS upstream (e.g., max depth 3) to find ancestors.
    
    ancestor_fields = {} # Key -> [Key, Type, Value]
    direct_parent_fields = {} # Key -> [Key, Type, Value]
    
    # Process Direct Parents first
    for p_node in parent_nodes:
        if p_node.custom_fields:
            if isinstance(p_node.custom_fields, list):
                for field_item in p_node.custom_fields:
                    # field_item is [key, type, value] or {"key":...} (if old format lingers?)
                    # We migrated, so assume list [k, t, v]
                    if isinstance(field_item, list) and len(field_item) >= 3:
                        k, t, v = field_item[0], field_item[1], field_item[2]
                        direct_parent_fields[k] = [k, t, v]
            elif isinstance(p_node.custom_fields, dict):
                # Fallback for unmigrated (shouldn't happen if migration ran)
                for k, v in p_node.custom_fields.items():
                    direct_parent_fields[k] = [k, "string", v]
    
    # Process Ancestors (BFS Upstream)
    # We already have parents. Let's find their parents.
    visited_ancestors = set(parent_ids)
    queue = list(parent_ids)
    max_depth = 3 # Limit depth to prevent performance issues
    current_depth = 0
    
    while queue and current_depth < max_depth:
        # Get next level parents
        next_level_ids = []
        if queue:
            # Find edges where target is in queue (incoming to current level)
            upstream_edges = session.exec(
                select(NoteEdge).where(
                    NoteEdge.user_id == current_user.id,
                    NoteEdge.target_id.in_(queue)
                )
            ).all()
            
            new_ancestor_ids = []
            for edge in upstream_edges:
                if edge.source_id not in visited_ancestors and edge.source_id != note_id:
                    visited_ancestors.add(edge.source_id)
                    new_ancestor_ids.append(edge.source_id)
            
            if new_ancestor_ids:
                # Fetch these ancestor nodes
                ancestor_nodes = session.exec(
                    select(NoteNode).where(
                        NoteNode.id.in_(new_ancestor_ids),
                        NoteNode.user_id == current_user.id
                    )
                ).all()
                
                for anc_node in ancestor_nodes:
                    if anc_node.custom_fields:
                        if isinstance(anc_node.custom_fields, list):
                            for field_item in anc_node.custom_fields:
                                if isinstance(field_item, list) and len(field_item) >= 3:
                                    k, t, v = field_item[0], field_item[1], field_item[2]
                                    ancestor_fields[k] = [k, t, v]
                        elif isinstance(anc_node.custom_fields, dict):
                            for k, v in anc_node.custom_fields.items():
                                ancestor_fields[k] = [k, "string", v]
                
                queue = new_ancestor_ids
            else:
                queue = []
        current_depth += 1

    # Remove keys from ancestor_fields that are already in direct_parent_fields
    # User wants: 1. Own, 2. Direct Parent (but not own), 3. Indirect (but not own or direct)
    
    # We will return these as separate Lists (of Lists) in the response.
    # We need to filter out duplicates.
    
    # Direct fields are prioritized over Ancestor fields
    final_direct_fields = []
    final_ancestor_fields = []
    
    # But wait, frontend also checks against "Own" fields.
    # The API just returns parents/ancestors. Frontend does the "Own" check?
    # No, API should probably filter? Or return raw context?
    # Previous implementation returned raw context for parents/ancestors, frontend filtered against own.
    # Let's keep that.
    
    # Convert dicts back to lists
    final_direct_fields = list(direct_parent_fields.values())
    
    # Filter ancestors: if in direct, remove from ancestor
    for k in list(ancestor_fields.keys()):
        if k in direct_parent_fields:
            del ancestor_fields[k]
            
    final_ancestor_fields = list(ancestor_fields.values())
    
    note_dict = note.model_dump()
    note_dict['edge_count'] = edge_count
    note_dict['out_degree'] = out_degree
    note_dict['inherited_fields'] = {
        "direct": final_direct_fields,
        "ancestors": final_ancestor_fields
    }
    return note_dict

@router.get("/{note_id}/connected-component", response_model=GraphData)
def get_connected_component(
    note_id: str,
    mode: str = Query("planetary", description="Mode: 'planetary' (default) or 'satellite'"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get the weakly connected component containing the given note.
    mode='satellite': Ignore incoming edges to the center note (only outgoing).
    """
    note_ids, edges = _get_component_note_ids(note_id, mode, current_user.id, session)
    nodes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == current_user.id,
            NoteNode.id.in_(note_ids)
        )
    ).all()
    component_edges = [edge for edge in edges if edge.source_id in note_ids and edge.target_id in note_ids]
    return {"nodes": nodes, "edges": component_edges}

@router.put("/{note_id}", response_model=NoteRead)
def update_note(
    note_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Update a note.
    """
    statement = select(NoteNode).where(NoteNode.id == note_id, NoteNode.user_id == current_user.id)
    db_note = session.exec(statement).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    note_data = note_in.model_dump(exclude_unset=True)
    
    # --- History Logging Logic ---
    now_ts = int(time.time())
    one_hour = 3600
    field_map = {"node_type": "n", "node_status": "s", "title": "t", "weight": "w", "content": "c", "private_level": "p"}
    
    if db_note.history is None:
        db_note.history = []
    
    for field, new_val in note_data.items():
        if field not in field_map:
            continue
            
        old_val = getattr(db_note, field)
        if old_val == new_val:
            continue
            
        f_code = field_map[field]
        # Find last entry for this field to check for merging
        last_entry = None
        for entry in reversed(db_note.history):
            if entry.get("f") == f_code:
                last_entry = entry
                break
        
        is_mergeable = False
        if last_entry and (now_ts - last_entry["ts"]) < one_hour:
            # Type/Status changes are always logged separately
            if field != "node_type" and field != "node_status":
                is_mergeable = True
        
        if is_mergeable:
            if field == "content":
                old_len = len(old_val) if old_val else 0
                new_len = len(new_val) if new_val else 0
                diff = new_len - old_len
                try:
                    current_delta = int(last_entry["v"].replace("+", ""))
                except:
                    current_delta = 0
                last_entry["v"] = f"{current_delta + diff:+d}"
            else:
                last_entry["v"] = new_val
            last_entry["ts"] = now_ts
        else:
            v_to_log = new_val
            if field == "content":
                old_len = len(old_val) if old_val else 0
                new_len = len(new_val) if new_val else 0
                v_to_log = f"{new_len - old_len:+d}"
            
            db_note.history.append({"ts": now_ts, "f": f_code, "v": v_to_log})
    
    # Trigger SQLAlchemy change detection for JSON
    flag_modified(db_note, "history")
    # --- End History Logging ---

    for key, value in note_data.items():
        setattr(db_note, key, value)
    
    db_note.updated_at = time.time()
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    return db_note

@router.delete("/{note_id}")
def delete_note(
    note_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete a note.
    """
    statement = select(NoteNode).where(NoteNode.id == note_id, NoteNode.user_id == current_user.id)
    db_note = session.exec(statement).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    session.delete(db_note)
    session.commit()
    return {"ok": True}

# --- Edges ---

@router.get("/edges/", response_model=List[EdgeRead])
def read_edges(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve all edges for the current user.
    """
    statement = select(NoteEdge).where(NoteEdge.user_id == current_user.id)
    edges = session.exec(statement).all()
    return edges

@router.post("/edges/", response_model=EdgeRead)
def create_edge(
    edge: EdgeCreate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a directed edge between two notes.
    Idempotent: If edge exists, return existing one (or update timestamp).
    """
    # Verify nodes exist and belong to user
    source = session.exec(select(NoteNode).where(NoteNode.id == edge.source_id, NoteNode.user_id == current_user.id)).first()
    target = session.exec(select(NoteNode).where(NoteNode.id == edge.target_id, NoteNode.user_id == current_user.id)).first()
    
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or Target node not found")

    # Prevent self-loop if desired (optional)
    if edge.source_id == edge.target_id:
        raise HTTPException(status_code=400, detail="Self-loops are not allowed")

    # Check if edge already exists
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id == edge.source_id,
        NoteEdge.target_id == edge.target_id
    )
    existing_edge = session.exec(statement).first()
    
    if existing_edge:
        # Idempotent: Return existing edge
        # Optionally update label if provided
        if edge.label is not None and edge.label != existing_edge.label:
            existing_edge.label = edge.label
            session.add(existing_edge)
            session.commit()
            session.refresh(existing_edge)
        return existing_edge

    db_edge = NoteEdge(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        source_id=edge.source_id,
        target_id=edge.target_id,
        label=edge.label,
        created_at=time.time()
    )
    session.add(db_edge)
    session.commit()
    session.refresh(db_edge)
    return db_edge

@router.delete("/edges/")
def delete_edge_by_nodes(
    source: str,
    target: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete an edge by source and target IDs.
    Robust: If edge doesn't exist, return success anyway (idempotent delete).
    """
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id == source,
        NoteEdge.target_id == target
    )
    db_edges = session.exec(statement).all()
    
    if not db_edges:
        # Already deleted or never existed, return success
        return {"ok": True, "message": "Edge not found, treated as deleted"}
    
    for edge in db_edges:
        session.delete(edge)
    
    session.commit()
    return {"ok": True}

@router.delete("/edges/{edge_id}")
def delete_edge(
    edge_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete an edge by ID.
    Robust: If edge doesn't exist, return success.
    """
    statement = select(NoteEdge).where(NoteEdge.id == edge_id, NoteEdge.user_id == current_user.id)
    db_edge = session.exec(statement).first()
    if not db_edge:
        # Robustness: Don't error on missing delete target
        return {"ok": True, "message": "Edge not found, treated as deleted"}
    
    session.delete(db_edge)
    session.commit()
    return {"ok": True}
