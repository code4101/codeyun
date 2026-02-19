from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
import sys
import os

from backend.db import get_session
from backend.models import UserDevice, User
from backend.schemas import DeviceCreate, DeviceRead, DeviceUpdate, UserDeviceCreate, UserDeviceRead, UserDeviceUpdate
from backend.core.auth import verify_api_token, get_current_user_from_token
from backend.core.device import device_manager

import uuid
import time
import requests

router = APIRouter()

@router.get("/", response_model=List[UserDeviceRead])
def read_user_devices(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token)
):
    """
    Get all devices configured for the current user.
    """
    statement = select(UserDevice).where(UserDevice.user_id == current_user.id).order_by(UserDevice.order_index)
    user_devices = session.exec(statement).all()
    
    # Enrich with Device info (Construct DeviceRead from UserDevice)
    results = []
    for ud in user_devices:
        # Construct a virtual Device object from UserDevice data
        # This maintains API compatibility without needing the Device table
        
        # Determine token: use stored token. 
        # For local device, we might want to check config? 
        # But UserDevice should be the source of truth for the client.
        
        # Compatibility mapping
        device_read = DeviceRead(
            id=ud.device_id,
            name=ud.name, # Name is now in UserDevice
            type=ud.type,
            url=ud.url,
            order_index=ud.order_index,
            created_at=ud.created_at, # Fill from UserDevice
            updated_at=ud.updated_at  # Fill from UserDevice
        )
        
        ud_read = UserDeviceRead(
            user_id=ud.user_id,
            device_id=ud.device_id,
            alias=ud.name, # Alias is now name
            name=ud.name,  # New field in schema? UserDeviceRead might need update
            is_active=ud.is_active,
            token=ud.token,
            created_at=ud.created_at,
            updated_at=ud.updated_at,
            device=device_read
        )
        results.append(ud_read)
            
    return results

@router.post("/add", response_model=UserDeviceRead)
def add_user_device(
    device_in: UserDeviceCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token)
):
    """
    Add a device to the user's list.
    Verify connection and get real device ID from remote.
    """
    # 0. Validate URL
    if not device_in.url:
         raise HTTPException(status_code=400, detail="URL is required for adding a remote device")
    
    # 1. Verify Connection and Get Real ID
    real_device_id = None
    real_device_name = None
    # real_python_exec removed
    
    try:
        # Normalize URL
        url = device_in.url.rstrip('/')
        if not url.startswith('http'):
            url = 'http://' + url
            
        # Call /api/agent/status
        # We use the token provided by the user to authenticate against the remote device
        headers = {}
        if device_in.token:
            headers["Authorization"] = f"Bearer {device_in.token}"
            headers["X-Device-Token"] = device_in.token # Support both
            
        resp = requests.get(f"{url}/api/agent/status", headers=headers, timeout=5)
        
        if resp.status_code == 200:
            data = resp.json()
            real_device_id = data.get("id")
            real_device_name = data.get("hostname")
            # real_python_exec removed
        elif resp.status_code == 401:
             raise HTTPException(status_code=400, detail="Token rejected by remote device")
        else:
             raise HTTPException(status_code=400, detail=f"Remote device returned status {resp.status_code}")
             
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to connect to device: {str(e)}")
        
    if not real_device_id:
        raise HTTPException(status_code=400, detail="Could not retrieve device ID from remote")

    # 2. Create/Update UserDevice Association
    existing_link = session.get(UserDevice, (current_user.id, real_device_id))
    
    # Prepare data
    name = device_in.alias or real_device_name or "Unknown Device"
    
    if existing_link:
        # Update existing link
        existing_link.token = device_in.token
        existing_link.name = name
        existing_link.url = url
        existing_link.type = "RemoteDevice" # Assume remote if adding via URL
        # python_exec removed
            
        existing_link.updated_at = time.time()
        existing_link.is_active = True
        session.add(existing_link)
        session.commit()
        session.refresh(existing_link)
        
        device_read = DeviceRead(
            id=existing_link.device_id,
            name=existing_link.name,
            type=existing_link.type,
            url=existing_link.url,
            order_index=existing_link.order_index,
            created_at=existing_link.created_at,
            updated_at=existing_link.updated_at
        )
        
        return UserDeviceRead(
            **existing_link.dict(),
            alias=existing_link.name,
            device=device_read
        )

    new_link = UserDevice(
        user_id=current_user.id,
        device_id=real_device_id,
        token=device_in.token,
        name=name,
        url=url,
        type="RemoteDevice",
        # python_exec removed
        is_active=True
    )
    session.add(new_link)
    session.commit()
    session.refresh(new_link)
    
    device_read = DeviceRead(
        id=new_link.device_id,
        name=new_link.name,
        type=new_link.type,
        url=new_link.url,
        order_index=new_link.order_index,
        created_at=new_link.created_at,
        updated_at=new_link.updated_at
    )
    
    return UserDeviceRead(
        **new_link.dict(),
        alias=new_link.name,
        device=device_read
    )

@router.put("/{device_id}", response_model=UserDeviceRead)
def update_user_device(
    device_id: str,
    device_in: UserDeviceUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token)
):
    """
    Update configuration for a user's device (e.g. rotate token, change alias).
    """
    link = session.get(UserDevice, (current_user.id, device_id))
    if not link:
        raise HTTPException(status_code=404, detail="Device not found in your list")
        
    if device_in.token is not None:
        link.token = device_in.token
    if device_in.alias is not None:
        link.name = device_in.alias # Alias is name now
    if device_in.name is not None:
        link.name = device_in.name
    if device_in.is_active is not None:
        link.is_active = device_in.is_active
    # python_exec removed
          
    link.updated_at = time.time()
    session.add(link)
    session.commit()
    session.refresh(link)

    device_read = DeviceRead(
        id=link.device_id,
        name=link.name,
        type=link.type,
        url=link.url,
        order_index=link.order_index,
        created_at=link.created_at,
        updated_at=link.updated_at
    )
    
    return UserDeviceRead(
        **link.dict(),
        alias=link.name,
        device=device_read
    )

@router.delete("/{device_id}")
def remove_user_device(
    device_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token)
):
    """
    Remove a device from the user's list.
    Does NOT delete the Device record itself.
    """
    link = session.get(UserDevice, (current_user.id, device_id))
    if not link:
        raise HTTPException(status_code=404, detail="Device not found in your list")
        
    session.delete(link)
    session.commit()
    
    return {"ok": True}

@router.post("/reorder")
def reorder_user_devices(
    device_ids: List[str],
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_from_token)
):
    """
    Reorder the user's devices.
    """
    for idx, dev_id in enumerate(device_ids):
        link = session.get(UserDevice, (current_user.id, dev_id))
        if link:
            link.order_index = idx
            session.add(link)
    session.commit()
    return {"status": "reordered"}
