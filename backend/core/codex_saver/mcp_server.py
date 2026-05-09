from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from urllib.parse import parse_qs
from typing import Any

from fastapi import status
from starlette.types import ASGIApp, Receive, Scope, Send
from sqlmodel import Session

from backend.core.auth import extract_api_token, validate_api_token_value
from backend.core.codex_saver.service import execute_codex_saver_task
from backend.db import engine, init_db


CODEX_SAVER_MCP_INSTRUCTIONS = (
    "CodexSaver is CodeYun's DeepSeek gateway for text and code tasks only. "
    "For each user request, call delegate_task first when the request can be represented as text, "
    "code, diff, or JSON context. Pass the request, cwd, known files, context, input kinds, "
    "and verification commands. "
    "Use its result when status is 'handled' or 'applied'. "
    "Handle the request directly when status is 'codex_required' or 'failed'. "
    "Do not send image, screenshot, video, audio, browser-visual, document, file attachment, "
    "or other multimodal tasks to DeepSeek; those should return codex_required and be handled "
    "by outer Codex. Call Codex tools first only when local inspection or browser/UI actions "
    "are needed before delegation."
)
_codex_saver_mcp = None


class CodexSaverTokenMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        final_token = extract_api_token(
            authorization=headers.get("authorization"),
            x_device_token=headers.get("x-device-token"),
            token=(query.get("token") or [None])[0],
            sec_websocket_protocol=headers.get("sec-websocket-protocol"),
        )
        try:
            validate_api_token_value(final_token)
        except Exception as exc:
            status_code = getattr(exc, "status_code", status.HTTP_401_UNAUTHORIZED)
            detail = getattr(exc, "detail", "Unauthorized")
            body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": status_code,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


def delegate_task(
    task: str,
    cwd: str = "",
    context: str = "",
    files: list[str] | None = None,
    input_kinds: list[str] | None = None,
    verification_commands: list[str] | None = None,
    allow_auto_apply: bool | None = None,
) -> dict[str, Any]:
    """DeepSeek gateway for text and code CodeYun work.

    Call this first for each user request. Use handled/applied results. Continue
    directly in Codex for codex_required/failed results. Multimodal, visual,
    browser/UI, and unavailable-context tasks should be handled by outer Codex.
    """
    init_db()
    with Session(engine) as session:
        return execute_codex_saver_task(
            session,
            {
                "task": task,
                "cwd": cwd,
                "context": context,
                "files": files or [],
                "input_kinds": input_kinds or ["text"],
                "verification_commands": verification_commands or [],
                "allow_auto_apply": allow_auto_apply,
            },
        )


def create_codex_saver_mcp(*, streamable_http_path: str = "/mcp"):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "codeyun-codex-saver",
        instructions=CODEX_SAVER_MCP_INSTRUCTIONS,
        streamable_http_path=streamable_http_path,
        stateless_http=True,
        json_response=True,
    )
    mcp.tool()(delegate_task)
    return mcp


def get_codex_saver_mcp():
    global _codex_saver_mcp
    if _codex_saver_mcp is None:
        _codex_saver_mcp = create_codex_saver_mcp(streamable_http_path="/")
    return _codex_saver_mcp


def create_codex_saver_streamable_http_app():
    return CodexSaverTokenMiddleware(get_codex_saver_mcp().streamable_http_app())


@asynccontextmanager
async def codex_saver_mcp_lifespan():
    async with get_codex_saver_mcp().session_manager.run():
        yield


def _run_fastmcp() -> bool:
    try:
        mcp = create_codex_saver_mcp(streamable_http_path="/mcp")
    except Exception:
        return False
    mcp.run()
    return True


def _run_single_json_call() -> None:
    raw = sys.stdin.read().strip()
    payload = json.loads(raw or "{}")
    result = delegate_task(**payload)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


def main() -> None:
    if _run_fastmcp():
        return
    _run_single_json_call()


if __name__ == "__main__":
    main()
