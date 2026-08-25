from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote


def video_document_path(video_path: str | Path) -> Path:
    """Return the same-prefix HTML document path for a media file."""

    return Path(video_path).with_suffix(".html")


def _render_paragraphs(paragraphs: Sequence[str]) -> str:
    return "\n".join(f"<p>{html.escape(str(paragraph))}</p>" for paragraph in paragraphs)


def write_video_html_document(
    video_path: str | Path,
    *,
    title: str,
    source_url: str,
    summary: str,
    sections: Sequence[Mapping[str, Any]] = (),
    timeline: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """Create a self-contained document that plays its sibling video.

    The HTML contains no copied media and no remote script dependencies. All
    caller-provided text is escaped before rendering.
    """

    media = Path(video_path)
    if not media.is_file():
        raise FileNotFoundError(media)
    target = video_document_path(media)
    media_src = quote(media.name)
    section_html = []
    for section in sections:
        heading = html.escape(str(section.get("heading") or "内容"))
        paragraphs = [str(value) for value in section.get("paragraphs") or []]
        items = [str(value) for value in section.get("items") or []]
        body = _render_paragraphs(paragraphs)
        if items:
            body += "\n<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"
        section_html.append(f"<section><h2>{heading}</h2>{body}</section>")
    timeline_html = []
    for item in timeline:
        seconds = max(float(item.get("time") or 0), 0)
        label = html.escape(str(item.get("label") or f"{seconds:.1f} 秒"))
        description = html.escape(str(item.get("description") or ""))
        timeline_html.append(
            f'<li><button type="button" data-time="{seconds:.3f}">{label}</button><span>{description}</span></li>'
        )
    timeline_section = (
        "<section><h2>时间点</h2><ol class=\"timeline\">"
        + "".join(timeline_html)
        + "</ol></section>"
        if timeline_html
        else ""
    )
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, "Microsoft YaHei", sans-serif; }}
    body {{ margin: 0 auto; max-width: 980px; padding: 32px 20px 64px; color: #182230; line-height: 1.75; background: #f6f8fb; }}
    article {{ padding: 28px; border: 1px solid #e2e8f0; border-radius: 18px; background: white; box-shadow: 0 12px 40px rgba(15, 23, 42, .06); }}
    h1 {{ margin-top: 0; line-height: 1.3; }} h2 {{ margin-top: 32px; border-bottom: 1px solid #e8edf3; padding-bottom: 8px; }}
    video {{ display: block; width: min(100%, 540px); max-height: 76vh; margin: 20px auto; border-radius: 14px; background: #0b0f15; }}
    .source {{ font-size: .92rem; color: #526173; overflow-wrap: anywhere; }}
    .summary {{ padding: 14px 16px; border-left: 4px solid #3b82f6; background: #eff6ff; }}
    .timeline {{ padding-left: 0; list-style: none; }} .timeline li {{ display: flex; gap: 12px; align-items: baseline; margin: 10px 0; }}
    button {{ flex: 0 0 auto; border: 0; border-radius: 999px; padding: 6px 11px; color: #075985; background: #e0f2fe; cursor: pointer; }}
    button:hover {{ background: #bae6fd; }} li {{ margin: 5px 0; }}
  </style>
</head>
<body>
<article>
  <h1>{html.escape(title)}</h1>
  <p class="source">来源：<a href="{html.escape(source_url, quote=True)}">{html.escape(source_url)}</a></p>
  <video id="source-video" controls preload="metadata" playsinline src="{media_src}">
    当前浏览器无法播放该视频，可直接打开同目录下的 {html.escape(media.name)}。
  </video>
  <p class="summary">{html.escape(summary)}</p>
  {timeline_section}
  {''.join(section_html)}
</article>
<script>
  const video = document.getElementById('source-video');
  document.querySelectorAll('[data-time]').forEach((button) => {{
    button.addEventListener('click', () => {{
      video.currentTime = Number(button.dataset.time || 0);
      video.play().catch(() => {{}});
      video.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }});
  }});
</script>
</body>
</html>
"""
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(target)
    return target
