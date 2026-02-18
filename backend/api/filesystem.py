import os
import sys
import shutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

class PathRequest(BaseModel):
    path: str

@router.post("/list_dir")
def list_directory(req: PathRequest):
    """List files in a directory using standard os"""
    try:
        path = req.path
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Path not found")
        
        items = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    items.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "path": entry.path
                    })
        except PermissionError:
             raise HTTPException(status_code=403, detail="Permission denied")
             
        return {"items": items, "current_path": path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/info")
def get_system_info():
    return {
        "platform": sys.platform,
        "python_version": sys.version,
        "cwd": os.getcwd()
    }
