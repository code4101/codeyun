from fastapi import Depends, HTTPException, status, Header, Query
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session, select
from backend.db import get_session
from backend.models import User
from backend.core.settings import get_settings
import secrets
# import backend.core.device as device_module # deferred to avoid cycle

settings = get_settings()
SECRET_KEY = settings.secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- New Token Authentication ---

def generate_token() -> str:
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)

async def verify_api_token(
    authorization: Optional[str] = Header(None),
    x_device_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    sec_websocket_protocol: Optional[str] = Header(None),
    session: Session = Depends(get_session)
):
    """
    Verify the token provided in the header or query parameter.
    Supports:
    - Header: 'Authorization: Bearer <token>'
    - Header: 'X-Device-Token: <token>'
    - Header: 'Sec-WebSocket-Protocol: <token>' (Preferred for WebSocket)
    - Query: '?token=<token>' (Fallback)
    
    This verifies if the request comes from a trusted device (using Master Token).
    Used for Server-side validation of incoming requests.
    """
    final_token = None
    if x_device_token:
        final_token = x_device_token
    elif authorization and authorization.startswith("Bearer "):
        final_token = authorization.split(" ")[1]
    elif sec_websocket_protocol:
        # Browser sends list of protocols, usually just the token in our case
        # But it might be comma separated if multiple protocols
        # We assume the token is one of them. 
        # Since token is urlsafe base64, it should be safe in protocol string.
        # Format usually: "token_value"
        final_token = sec_websocket_protocol.split(',')[0].strip()
    elif token:
        final_token = token
    
    if not final_token:
        # Fallback for now: If no token provided, return None or raise error depending on strictness
        # Since we are strict:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )
    
    # Lazy import to avoid cycle
    from backend.core.device import device_manager, get_device_id

    local_id = get_device_id()
    local_dev = device_manager.get_device(local_id)

    if not local_dev or not local_dev.api_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Device control is disabled on this node",
        )

    if final_token == local_dev.api_token:
        return local_dev

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
    )

async def get_current_user_from_token(
    token: str = Depends(oauth2_scheme), 
    session: Session = Depends(get_session)
):
    """
    Authenticate User via JWT.
    Used for frontend user sessions.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    statement = select(User).where(User.username == username)
    user = session.exec(statement).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user_from_token)):
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

async def get_current_active_superuser(current_user: User = Depends(get_current_active_user)):
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )
    return current_user
