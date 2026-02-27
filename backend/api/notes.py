from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
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
        # parent_id=note.parent_id, # Deprecated
        created_at=current_time,
        updated_at=current_time,
        start_at=note.start_at if note.start_at is not None else current_time
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
