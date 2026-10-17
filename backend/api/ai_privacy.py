"""Owner-only DS privacy operations. No HTTP endpoint reveals .env values."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from backend.core.access.auth import get_current_active_superuser
from backend.core.ai import privacy

class PrivacyRoute(APIRoute):
    """Pydantic errors normally echo invalid input, which may be a password."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "invalid_privacy_request"})
        return safe_handler


router = APIRouter(dependencies=[Depends(get_current_active_superuser)], route_class=PrivacyRoute)


class PrepareRequest(BaseModel):
    text: str = Field(min_length=1, max_length=80000)
    session_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")


class RedactRequest(BaseModel):
    text: str = Field(max_length=200000)


@router.post("/prepare")
def prepare_private_task(request: PrepareRequest):
    try:
        return privacy.prepare(request.text, session_id=request.session_id)
    except privacy.PrivacyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/{session_id}")
def get_private_handoff(session_id: str):
    try:
        return privacy.handoff(session_id)
    except privacy.PrivacyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/{session_id}/redact")
def redact_private_result(session_id: str, request: RedactRequest):
    try:
        return {"text": privacy.redact(session_id, request.text)}
    except privacy.PrivacyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/{session_id}")
def forget_private_session(session_id: str):
    try:
        privacy.forget(session_id)
        return {"ok": True}
    except privacy.PrivacyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
