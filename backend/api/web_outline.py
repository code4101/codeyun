from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from backend.core.tools.web_outline import (
    WebOutlineError,
    build_rule_outline,
    extract_source_headings,
    fetch_public_html,
    number_outline,
    render_markdown,
)


router = APIRouter()


class WebOutlineRequest(BaseModel):
    url: HttpUrl


class WebOutlineSourceHeading(BaseModel):
    source_index: int
    title: str
    html_level: int = Field(ge=1, le=6)
    context: str = ""


class WebOutlineItem(BaseModel):
    title: str
    level: int = Field(ge=1, le=6)
    number: str = ""
    source_index: int | None = None
    inferred: bool = False


class WebOutlineResponse(BaseModel):
    url: str
    title: str
    source_headings: list[WebOutlineSourceHeading]
    items: list[WebOutlineItem]
    markdown: str


@router.post("/extract", response_model=WebOutlineResponse)
def extract_web_outline(
    payload: WebOutlineRequest,
):
    try:
        html, final_url, _ = fetch_public_html(str(payload.url))
        page_title, source_headings = extract_source_headings(html, final_url)
        if not source_headings:
            raise WebOutlineError("正文中没有找到可识别的标题；该网页可能依赖登录或 JavaScript 渲染")

        items = number_outline(build_rule_outline(page_title, source_headings))
        return WebOutlineResponse(
            url=final_url,
            title=page_title,
            source_headings=[WebOutlineSourceHeading(**item.__dict__) for item in source_headings],
            items=[WebOutlineItem(**item) for item in items],
            markdown=render_markdown(items),
        )
    except WebOutlineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
