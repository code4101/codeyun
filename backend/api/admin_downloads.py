"""Superuser download page and streaming downloads with ordinary session identity.

The public HTML shell contains no bundle data. A Bearer login exchanges for a
short-lived HttpOnly cookie restricted to this API, allowing native browser
downloads (including Range resume) without putting credentials in URLs or
buffering large files in JavaScript. Every metadata/file request rechecks the
current account's active/superuser flags through the shared auth implementation.
"""
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from sqlmodel import Session

from backend.core.access.auth import (
    create_user_access_token, get_current_active_superuser,
    get_current_user_from_token, oauth2_scheme_optional,
)
from backend.core.resources.admin_downloads import list_download_bundles, read_download_bundle, resolve_download_file
from backend.db import get_session
from backend.models import User

router = APIRouter()
COOKIE_NAME = "codeyun_admin_download"
COOKIE_PATH = "/api/admin-downloads"
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer"}


def download_user(token: str | None = Depends(oauth2_scheme_optional),
                  cookie: str | None = Cookie(None, alias=COOKIE_NAME),
                  session: Session = Depends(get_session)) -> User:
    credential = token or cookie
    if not credential:
        raise HTTPException(401, "请先登录 CodeYun 超管账号")
    user = get_current_user_from_token(credential, session)
    if not user.is_active or not user.is_superuser:
        raise HTTPException(403, "仅启用的超管账号可下载")
    return user


@router.post("/session")
def create_download_session(response: Response, user: User = Depends(get_current_active_superuser)):
    response.set_cookie(COOKIE_NAME, create_user_access_token(user, timedelta(hours=2)),
                        max_age=7200, path=COOKIE_PATH, secure=True, httponly=True, samesite="strict")
    response.headers.update(PRIVATE_HEADERS)
    return {"ready": True}


@router.get("/catalog")
def software_catalog(response: Response, user: User = Depends(download_user)):
    response.headers.update(PRIVATE_HEADERS)
    return {"items": list_download_bundles(category="software")}


@router.get("/{bundle_id}/metadata")
def download_metadata(bundle_id: str, response: Response, user: User = Depends(download_user)):
    response.headers.update(PRIVATE_HEADERS)
    try:
        return read_download_bundle(bundle_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "下载资源不存在")


@router.get("/{bundle_id}/files/{filename}")
def download_file(bundle_id: str, filename: str, user: User = Depends(download_user)):
    try:
        path = resolve_download_file(bundle_id, filename)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "下载资源不存在")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream", headers=PRIVATE_HEADERS)


@router.get("/{bundle_id}", response_class=HTMLResponse)
def download_page(bundle_id: str):
    # The identifier is read from location by the client; no HTML interpolation.
    return HTMLResponse(Path(__file__).with_name("admin_downloads.html").read_text(encoding="utf-8"),
                        headers={**PRIVATE_HEADERS, "Content-Security-Policy":
                                 "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                 "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
