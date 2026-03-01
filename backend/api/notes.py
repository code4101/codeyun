from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from sqlalchemy.orm.attributes import flag_modified
from backend.db import get_session
from backend.models import NoteNode, NoteEdge, User
from backend.schemas import NoteCreate, NoteRead, NoteUpdate, EdgeCreate, EdgeRead, NoteListRead
from backend.core.auth import get_current_active_user
import time
import uuid

router = APIRouter()

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
        # parent_id=note.parent_id, # Deprecated
        created_at=current_time,
        updated_at=current_time,
        start_at=note.start_at if note.start_at is not None else current_time,
        history=[]
    )
    
    # Initialize history if node type is provided
    if note.node_type:
        db_note.history.append({
            "ts": int(current_time),
            "f": "n",
            "v": note.node_type
        })
        
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
    return note

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
    field_map = {"node_type": "n", "title": "t", "weight": "w", "content": "c"}
    
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
            if field != "node_type":
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
