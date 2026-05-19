import os
import re
import uuid

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlmodel import Session

from backend.core.attachment_resources import index_attachment_file_resource
from backend.core.storage import build_attachment_url, get_attachments_dir
from backend.db import get_session

router = APIRouter()

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_UPLOAD_BYTES = 100 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024
SAFE_EXTENSION_RE = re.compile(r"^\.[a-zA-Z0-9]{1,16}$")
DANGEROUS_ATTACHMENT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".cpl",
    ".dll",
    ".exe",
    ".hta",
    ".htm",
    ".html",
    ".jar",
    ".js",
    ".jse",
    ".lnk",
    ".mjs",
    ".msi",
    ".ps1",
    ".reg",
    ".scr",
    ".sh",
    ".svg",
    ".vbs",
    ".wsf",
    ".xhtml",
    ".xml",
}


def _normalize_original_filename(filename: str | None, fallback: str) -> str:
    normalized = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return normalized or fallback


def _safe_upload_extension(original_filename: str, default_ext: str) -> str:
    ext = os.path.splitext(original_filename)[1].lower()
    if not ext or not SAFE_EXTENSION_RE.match(ext) or ext in DANGEROUS_ATTACHMENT_EXTENSIONS:
        return default_ext
    return ext


def _format_size_limit(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024:
        return f"{max_bytes // 1024 // 1024}MB"
    if max_bytes >= 1024:
        return f"{max_bytes // 1024}KB"
    return f"{max_bytes}B"


def _save_uploaded_file(
    file: UploadFile,
    *,
    default_ext: str,
    max_bytes: int,
    fallback_original_name: str,
) -> dict:
    original_filename = _normalize_original_filename(file.filename, fallback_original_name)
    ext = _safe_upload_extension(original_filename, default_ext)
    filename = f"{uuid.uuid4().hex}{ext}"
    attachments_dir = get_attachments_dir()
    file_path = attachments_dir / filename

    size = 0
    try:
        with file_path.open("wb") as buffer:
            while True:
                chunk = file.file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {_format_size_limit(max_bytes)}",
                    )
                buffer.write(chunk)
    except Exception:
        try:
            file_path.unlink(missing_ok=True)
        finally:
            raise

    url = build_attachment_url(filename)
    return {
        "url": url,
        "filename": filename,
        "original_filename": original_filename,
        "name": original_filename,
        "content_type": file.content_type or "application/octet-stream",
        "size": size,
    }


def _attach_uploaded_resource_id(uploaded: dict, session: Session) -> None:
    file_path = get_attachments_dir() / str(uploaded["filename"])
    record = index_attachment_file_resource(
        session,
        file_path,
        mime_type=str(uploaded.get("content_type") or "application/octet-stream"),
    )
    session.commit()
    session.refresh(record)
    resource_id = int(record.numeric_id or 0)
    if resource_id <= 0:
        raise HTTPException(status_code=500, detail="附件资源编号缺失")
    uploaded["id"] = resource_id


@router.post("/image")
async def upload_image(file: UploadFile = File(...), session: Session = Depends(get_session)):
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
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="File must be an image")

        uploaded = _save_uploaded_file(
            file,
            default_ext=".png",
            max_bytes=MAX_IMAGE_UPLOAD_BYTES,
            fallback_original_name="image.png",
        )
        _attach_uploaded_resource_id(uploaded, session)

        # Construct URL (Relative path for frontend proxy to handle, or absolute if needed)
        # Frontend proxy: /api -> http://localhost:8000
        # Static mount exposes the data-dir attachments at /static/attachments.
        url = uploaded["url"]
        
        return {
            "errno": 0,
            "data": {
                "url": url,
                "alt": uploaded["original_filename"],
                "href": url,
                "filename": uploaded["filename"],
                "id": uploaded["id"],
                "size": uploaded["size"],
                "content_type": uploaded["content_type"],
            }
        }
    except Exception as e:
        print(f"Upload error: {e}")
        return {
            "errno": 1,
            "message": str(e)
        }


@router.post("/file")
async def upload_file(file: UploadFile = File(...), session: Session = Depends(get_session)):
    uploaded = _save_uploaded_file(
        file,
        default_ext=".bin",
        max_bytes=MAX_ATTACHMENT_UPLOAD_BYTES,
        fallback_original_name="attachment",
    )
    _attach_uploaded_resource_id(uploaded, session)
    return {
        "errno": 0,
        "data": uploaded,
    }
