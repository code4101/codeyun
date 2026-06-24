from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil
import os
import uuid
import time
from backend.core.storage import build_attachment_url, get_attachments_dir

router = APIRouter()

ATTACHMENTS_DIR = os.fspath(get_attachments_dir())
if not os.path.exists(ATTACHMENTS_DIR):
    os.makedirs(ATTACHMENTS_DIR)

@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    """
    Upload an image file.
    Returns:
    {
        "errno": 0, // WangEditor format
        "data": {
            "url": "http://...",
            "alt": "...",
            "href": "..."
        }
    }
    """
    try:
        # Validate file type
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="File must be an image")
            
        # Generate unique filename
        ext = os.path.splitext(file.filename)[1]
        if not ext:
            ext = ".png" # Default
            
        filename = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(ATTACHMENTS_DIR, filename)
        
        # Save file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Construct URL (Relative path for frontend proxy to handle, or absolute if needed)
        # Frontend proxy: /api -> http://localhost:8000
        # Static mount exposes the data-dir attachments at /static/attachments.
        url = build_attachment_url(filename)
        
        return {
            "errno": 0,
            "data": {
                "url": url,
                "alt": file.filename,
                "href": url
            }
        }
    except Exception as e:
        print(f"Upload error: {e}")
        return {
            "errno": 1,
            "message": str(e)
        }
