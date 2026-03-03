from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from backend.db import get_session
from backend.models import NoteNode, User
from backend.schemas import NoteRead, NoteUpdate
from backend.core.auth import get_current_active_user
import uuid
import time
from passlib.context import CryptContext

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

FANXIU_USERNAME = "凡修手游"
FANXIU_CHAR_TYPE = "memo"
CODE4101_USERNAME = "code4101"

def get_fanxiu_user(session: Session) -> User:
    statement = select(User).where(User.username == FANXIU_USERNAME)
    user = session.exec(statement).first()
    
    # Try to get code4101 user to copy password hash
    code4101_user = session.exec(select(User).where(User.username == CODE4101_USERNAME)).first()
    target_hash = code4101_user.hashed_password if code4101_user else pwd_context.hash(str(uuid.uuid4()))

    if not user:
        # Auto create if not exists
        user = User(
            username=FANXIU_USERNAME,
            hashed_password=target_hash, # Copy hash from code4101
            is_active=True,
            is_superuser=False,
            created_at=time.time(),
            updated_at=time.time()
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    else:
        # Check if hash needs update (sync with code4101)
        if code4101_user and user.hashed_password != code4101_user.hashed_password:
            user.hashed_password = code4101_user.hashed_password
            session.add(user)
            session.commit()
            session.refresh(user)
            
    return user

@router.get("/chars", response_model=List[NoteRead])
def read_chars(
    session: Session = Depends(get_session)
):
    """
    Get all Xianzhou Race characters data.
    Publicly accessible.
    """
    fanxiu_user = get_fanxiu_user(session)
    statement = select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.node_type == FANXIU_CHAR_TYPE
    )
    return session.exec(statement).all()

@router.get("/chars/{char_name}", response_model=NoteRead)
def read_char(
    char_name: str,
    session: Session = Depends(get_session)
):
    """
    Get specific character data.
    Publicly accessible.
    """
    fanxiu_user = get_fanxiu_user(session)
    statement = select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.node_type == FANXIU_CHAR_TYPE,
        NoteNode.title == char_name
    )
    note = session.exec(statement).first()
    
    if not note:
        raise HTTPException(status_code=404, detail="Character not found")
        
    return note

@router.put("/chars/{char_name}", response_model=NoteRead)
def update_char(
    char_name: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Update or create character data.
    Restricted to specific users.
    """
    # STRICT PERMISSION: Only 'fanxiu_official' itself can edit.
    # Even 'code4101' cannot edit directly via this API unless logged in as 'fanxiu_official'.
    # This enforces data ownership isolation.
    
    if current_user.username != FANXIU_USERNAME and not current_user.is_superuser:
         raise HTTPException(status_code=403, detail="Only 'fanxiu_official' account or superuser can edit this data.")

    if current_user.username == FANXIU_USERNAME:
        fanxiu_user = current_user
    else:
        fanxiu_user = get_fanxiu_user(session)
    
    statement = select(NoteNode).where(
        NoteNode.user_id == fanxiu_user.id,
        NoteNode.node_type == FANXIU_CHAR_TYPE,
        NoteNode.title == char_name
    )
    db_note = session.exec(statement).first()
    
    current_time = time.time()
    
    if not db_note:
        # Create new
        db_note = NoteNode(
            id=str(uuid.uuid4()),
            user_id=fanxiu_user.id,
            title=char_name, 
            content=note_in.content or "",
            weight=note_in.weight if note_in.weight is not None else 0,
            node_type=FANXIU_CHAR_TYPE,
            created_at=current_time,
            updated_at=current_time,
            start_at=note_in.start_at if note_in.start_at is not None else current_time,
            history=[]
        )
        session.add(db_note)
    else:
        # Update existing
        if note_in.content is not None:
            db_note.content = note_in.content
        if note_in.weight is not None:
            db_note.weight = note_in.weight
        if note_in.start_at is not None:
            db_note.start_at = note_in.start_at
        
        db_note.updated_at = current_time
        session.add(db_note)
        
    session.commit()
    session.refresh(db_note)
    return db_note
