from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

try:
    import aspose.note as onenote
except ImportError as exc:  # pragma: no cover - exercised by manual script use.
    raise SystemExit(
        "Missing dependency: aspose-note. Run with "
        "`uv run --with aspose-note python scripts/import_onenote_section.py ...`."
    ) from exc


USER_ID = 2
TZ = dt.timezone(dt.timedelta(hours=8))
IMPORT_SOURCE = "onenote-section-file"
DEFAULT_BACKUP_ROOT = Path.home() / "AppData/Local/Microsoft/OneNote/16.0/\u5907\u4efd"
DEFAULT_NOTEBOOK = "\u4ee3\u53f7"
DEFAULT_CATEGORY = "general"
PARAGRAPH_STYLE = "margin:0;line-height:1.45;white-space:pre-wrap;tab-size:4"
TITLE_STYLE = "margin:0 0 10px;line-height:1.3;white-space:pre-wrap;tab-size:4;font-weight:700"
HEADING_STYLE = "margin:16px 0 8px;line-height:1.45;white-space:pre-wrap;tab-size:4;font-weight:400;color:#2f7edb"
TABLE_STYLE = "border-collapse:collapse;table-layout:auto"
TABLE_CELL_STYLE = "vertical-align:middle;padding:2px 6px;line-height:1.45"
MISSING_MEDIA_STYLE = (
    "margin:4px 0;padding:4px 6px;border:1px solid #fed7aa;"
    "background-color:#fff7ed;color:#9a3412;line-height:1.4"
)
OUTLINE_INDENT_EM = 2
ONENOTE_HYPERLINK_FIELD_RE = re.compile(r'^(?:\ufddf\s*)?HYPERLINK\s+"([^"]+)"(.*)$', re.DOTALL)
ONENOTE_HYPERLINK_FIELD_ANY_RE = re.compile(r'(?:\ufddf\s*)?HYPERLINK\s+"([^"]+)"', re.DOTALL)
ONENOTE_HYPERLINK_FIELD_OPEN_RE = re.compile(r'(?:\ufddf\s*)?HYPERLINK\s+"([^"]*)$', re.DOTALL)
ONENOTE_COM_NAMESPACE = "http://schemas.microsoft.com/office/onenote/2013/onenote"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def default_data_dir() -> Path:
    return Path(os.environ.get("CODEYUN_DATA_DIR", r"D:\home\chenkunze\data\m2603codeyun\codepc_mf"))


def db_path(data_dir: Path) -> Path:
    return data_dir / "codeyun.db"


def attachments_dir(data_dir: Path) -> Path:
    return data_dir / "attachments"


def note_categories(category: str) -> str:
    return json.dumps([{"key": category, "weight": 100}], ensure_ascii=False)


def slug_filename(name: str, fallback: str) -> str:
    text = (name or "").strip() or fallback
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120] or fallback


def latest_section_file(backup_root: Path, notebook: str, section: str) -> Path:
    section_dir = backup_root / notebook
    candidates = sorted(
        section_dir.glob(f"{section}*.one"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No .one backup found for {notebook}/{section} under {section_dir}")
    return candidates[0]


def css_color(value: Any) -> str:
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return f"#{value:06x}"
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", text):
        return text
    if re.fullmatch(r"\d+", text):
        number = int(text)
        if 0 <= number <= 0xFFFFFF:
            return f"#{number:06x}"
    return text


def system_windows_powershell() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def run_windows_powershell(script: str, *, timeout: int = 180) -> str:
    powershell = system_windows_powershell()
    if not powershell.exists():
        raise FileNotFoundError(f"Windows PowerShell not found: {powershell}")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"powershell exited {result.returncode}").strip())
    return result.stdout


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def direct_children(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(node) if local_name(child.tag) == name]


def iter_descendants(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in node.iter() if local_name(child.tag) == name]


def has_xml_ancestor(node: ElementTree.Element, name: str, parents: dict[int, ElementTree.Element]) -> bool:
    parent = parents.get(id(node))
    while parent is not None:
        if local_name(parent.tag) == name:
            return True
        parent = parents.get(id(parent))
    return False


def com_xml_style_hints(page_xml: str) -> dict[str, Any]:
    root = ElementTree.fromstring(page_xml)
    parents = {id(child): parent for parent in root.iter() for child in list(parent)}
    quick_style_names: dict[str, str] = {}
    for style_def in iter_descendants(root, "QuickStyleDef"):
        index = style_def.attrib.get("index")
        name = style_def.attrib.get("name")
        if index and name:
            quick_style_names[index] = name

    rich_text_blocks: list[dict[str, str]] = []
    for oe in iter_descendants(root, "OE"):
        if has_xml_ancestor(oe, "Title", parents):
            continue
        if not direct_children(oe, "T"):
            continue
        style_name = quick_style_names.get(oe.attrib.get("quickStyleIndex", ""), "")
        tag = style_name if re.fullmatch(r"h[1-4]", style_name) else ""
        rich_text_blocks.append({"tag": tag, "style_name": style_name})

    table_cell_shadings: list[list[list[str]]] = []
    for table in iter_descendants(root, "Table"):
        table_grid: list[list[str]] = []
        for row in direct_children(table, "Row"):
            row_colors: list[str] = []
            for cell in direct_children(row, "Cell"):
                row_colors.append(css_color(cell.attrib.get("shadingColor", "")))
            table_grid.append(row_colors)
        if any(color for row in table_grid for color in row):
            table_cell_shadings.append(table_grid)
        else:
            table_cell_shadings.append([])
    return {
        "rich_text_blocks": rich_text_blocks,
        "table_cell_shadings": table_cell_shadings,
    }


def load_com_section_style_hints(notebook: str, section: str) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    section_parts = [part for part in re.split(r"[\\/]+", section) if part]
    config_json = json.dumps({"notebook": notebook, "section_parts": section_parts}, ensure_ascii=False)
    script = f"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$config = @'
{config_json}
'@ | ConvertFrom-Json
$app = New-Object -ComObject OneNote.Application
$hierarchyXml = ""
$app.GetHierarchy("", 4, [ref]$hierarchyXml, 2)
[xml]$doc = $hierarchyXml
$ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
$ns.AddNamespace("one", "{ONENOTE_COM_NAMESPACE}")
$targetPath = @($config.notebook) + @($config.section_parts)
$entries = New-Object System.Collections.ArrayList
foreach ($page in $doc.SelectNodes("//one:Page", $ns)) {{
    $path = New-Object System.Collections.ArrayList
    $node = $page
    while ($node -ne $null -and $node.NodeType -ne [System.Xml.XmlNodeType]::Document) {{
        if (@("Notebook", "SectionGroup", "Section", "Page") -contains $node.LocalName) {{
            [void]$path.Insert(0, [string]$node.name)
        }}
        $node = $node.ParentNode
    }}
    if ($path.Count -lt ($targetPath.Count + 1)) {{
        continue
    }}
    $matches = $true
    for ($i = 0; $i -lt $targetPath.Count; $i++) {{
        if ([string]$path[$i] -ne [string]$targetPath[$i]) {{
            $matches = $false
            break
        }}
    }}
    if (-not $matches) {{
        continue
    }}
    try {{
        $pageXml = ""
        $app.GetPageContent([string]$page.ID, [ref]$pageXml, 0)
        [void]$entries.Add([pscustomobject]@{{
            name = [string]$page.name
            id = [string]$page.ID
            path = [string[]]$path
            xml = $pageXml
            error = $null
        }})
    }} catch {{
        [void]$entries.Add([pscustomobject]@{{
            name = [string]$page.name
            id = [string]$page.ID
            path = [string[]]$path
            xml = $null
            error = $_.Exception.Message
        }})
    }}
}}
$entries | ConvertTo-Json -Depth 8 -Compress
"""
    output = run_windows_powershell(script)
    raw_entries = json.loads(output.strip() or "[]")
    if isinstance(raw_entries, dict):
        raw_entries = [raw_entries]
    by_index: dict[int, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for index, entry in enumerate(raw_entries):
        title = str(entry.get("name") or "")
        page_xml = entry.get("xml") or ""
        if entry.get("error"):
            errors.append(f"{title}: {entry['error']}")
            continue
        if not page_xml:
            continue
        try:
            hints = com_xml_style_hints(page_xml)
        except ElementTree.ParseError as exc:
            errors.append(f"{title}: XML parse failed: {exc}")
            continue
        hints["_title"] = title
        hints["_com_page_id"] = entry.get("id")
        by_index[index] = hints
        by_title.setdefault(title, hints)
    return by_index, by_title, errors


def load_com_section_pages(notebook: str, section: str) -> list[dict[str, Any]]:
    section_parts = [part for part in re.split(r"[\\/]+", section) if part]
    config_json = json.dumps({"notebook": notebook, "section_parts": section_parts}, ensure_ascii=False)
    script = f"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$config = @'
{config_json}
'@ | ConvertFrom-Json
$app = New-Object -ComObject OneNote.Application
$hierarchyXml = ""
$app.GetHierarchy("", 4, [ref]$hierarchyXml, 2)
[xml]$doc = $hierarchyXml
$ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
$ns.AddNamespace("one", "{ONENOTE_COM_NAMESPACE}")
$targetPath = @($config.notebook) + @($config.section_parts)
$entries = New-Object System.Collections.ArrayList
foreach ($page in $doc.SelectNodes("//one:Page", $ns)) {{
    $path = New-Object System.Collections.ArrayList
    $node = $page
    while ($node -ne $null -and $node.NodeType -ne [System.Xml.XmlNodeType]::Document) {{
        if (@("Notebook", "SectionGroup", "Section", "Page") -contains $node.LocalName) {{
            [void]$path.Insert(0, [string]$node.name)
        }}
        $node = $node.ParentNode
    }}
    if ($path.Count -lt ($targetPath.Count + 1)) {{
        continue
    }}
    $matches = $true
    for ($i = 0; $i -lt $targetPath.Count; $i++) {{
        if ([string]$path[$i] -ne [string]$targetPath[$i]) {{
            $matches = $false
            break
        }}
    }}
    if (-not $matches) {{
        continue
    }}
    try {{
        $pageXml = ""
        $app.GetPageContent([string]$page.ID, [ref]$pageXml, 0)
        [void]$entries.Add([pscustomobject]@{{
            name = [string]$page.name
            id = [string]$page.ID
            path = [string[]]$path
            xml = $pageXml
            error = $null
        }})
    }} catch {{
        [void]$entries.Add([pscustomobject]@{{
            name = [string]$page.name
            id = [string]$page.ID
            path = [string[]]$path
            xml = $null
            error = $_.Exception.Message
        }})
    }}
}}
$entries | ConvertTo-Json -Depth 8 -Compress
"""
    output = run_windows_powershell(script)
    raw_entries = json.loads(output.strip() or "[]")
    if isinstance(raw_entries, dict):
        raw_entries = [raw_entries]
    return raw_entries


def parse_com_timestamp(value: str | None) -> float:
    if not value:
        return time.time()
    text = value.strip()
    try:
        if text.endswith("Z"):
            return dt.datetime.fromisoformat(text[:-1] + "+00:00").timestamp()
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.timestamp()
    except ValueError:
        return time.time()


def com_quick_style_names(root: ElementTree.Element) -> dict[str, str]:
    names: dict[str, str] = {}
    for style_def in iter_descendants(root, "QuickStyleDef"):
        index = style_def.attrib.get("index")
        name = style_def.attrib.get("name")
        if index and name:
            names[index] = name
    return names


def render_com_text_fragment(text: str | None) -> str:
    raw = text or ""
    if not raw:
        return ""
    field_match = ONENOTE_HYPERLINK_FIELD_RE.match(raw)
    if field_match:
        href = html.escape(field_match.group(1), quote=True)
        label = html.escape(field_match.group(2).strip() or field_match.group(1))
        return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'
    if "<" in raw and ">" in raw:
        return raw.replace("\r\n", "\n").replace("\n", "<br>")
    return html.escape(raw).replace("\r\n", "\n").replace("\n", "<br>")


def normalize_onenote_hyperlink_fields_in_html(content: str) -> str:
    pattern = re.compile(r'(?:\ufddf\s*)?HYPERLINK\s+&quot;([^"]+?)&quot;', re.DOTALL)
    pos = 0
    parts: list[str] = []
    for match in pattern.finditer(content):
        if match.start() < pos:
            continue
        href = match.group(1)
        last_span_open = content.rfind("<span", 0, match.start())
        last_span_close = content.rfind("</span>", 0, match.start())
        end_token = "</span>" if last_span_open > last_span_close else "</p>"
        label_end = content.find(end_token, match.end())
        if label_end < 0:
            label_end = match.end()
        label = content[match.end():label_end].strip() or html.escape(href)
        parts.append(content[pos:match.start()])
        parts.append(
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'
        )
        pos = label_end
    if not parts:
        return content
    parts.append(content[pos:])
    return "".join(parts)


def render_com_t_nodes(node: ElementTree.Element) -> str:
    return "".join(render_com_text_fragment(text_node.text) for text_node in direct_children(node, "T"))


def render_com_missing_image(image: ElementTree.Element) -> str:
    label = image.attrib.get("alt", "").strip()
    ocr_text = ""
    for text_node in iter_descendants(image, "OCRText"):
        if text_node.text and text_node.text.strip():
            ocr_text = text_node.text.strip()
            break
    detail = label or ocr_text[:120]
    placeholder = render_missing_media("image", "图片", detail)
    if ocr_text:
        placeholder += (
            f'<pre style="{PARAGRAPH_STYLE};white-space:pre-wrap">'
            f'{html.escape(ocr_text)}</pre>'
        )
    return placeholder


def render_com_inserted_file(node: ElementTree.Element) -> str:
    name = node.attrib.get("preferredName") or node.attrib.get("pathCache") or "attachment"
    return render_missing_media("attachment", "附件", name)


def render_com_ink(node: ElementTree.Element) -> str:
    size = direct_children(node, "Size")
    details = ""
    if size:
        width = size[0].attrib.get("width")
        height = size[0].attrib.get("height")
        if width and height:
            details = f"{width} x {height}"
    return render_missing_media("ink", "墨迹", details)


def render_com_table(table: ElementTree.Element, quick_styles: dict[str, str], depth: int) -> str:
    html_rows: list[str] = []
    for row in direct_children(table, "Row"):
        cells = []
        for cell in direct_children(row, "Cell"):
            cell_style = TABLE_CELL_STYLE
            color = css_color(cell.attrib.get("shadingColor", ""))
            if color:
                cell_style += f";background-color:{html.escape(color, quote=True)}"
            cells.append(f'<td style="{cell_style}">{render_com_children(cell, quick_styles, depth)}</td>')
        html_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table style="{TABLE_STYLE}"><tbody>' + "".join(html_rows) + "</tbody></table>"


def render_com_oe(oe: ElementTree.Element, quick_styles: dict[str, str], depth: int) -> str:
    parts: list[str] = []
    body = render_com_t_nodes(oe)
    for tag_node in direct_children(oe, "Tag"):
        if tag_node.attrib.get("completed") == "true":
            body = "&#9745; " + body
        else:
            body = "&#9744; " + body
    for child in list(oe):
        name = local_name(child.tag)
        if name in {"T", "Tag", "OEChildren"}:
            continue
        parts.append(render_com_node(child, quick_styles, depth))
    if body.strip():
        style_name = quick_styles.get(oe.attrib.get("quickStyleIndex", ""), "")
        if re.fullmatch(r"h[1-4]", style_name):
            parts.insert(0, f'<{style_name} style="{block_style(HEADING_STYLE, depth)}">{body}</{style_name}>')
        else:
            parts.insert(0, f'<p style="{block_style(PARAGRAPH_STYLE, depth)}">{body}</p>')
    elif not parts:
        parts.append(f'<p style="{block_style(PARAGRAPH_STYLE, depth)}"><br></p>')
    for oe_children in direct_children(oe, "OEChildren"):
        parts.append(render_com_children(oe_children, quick_styles, depth + 1))
    return "".join(parts)


def render_com_node(node: ElementTree.Element, quick_styles: dict[str, str], depth: int = 0) -> str:
    name = local_name(node.tag)
    if name == "Title":
        return ""
    if name == "OE":
        return render_com_oe(node, quick_styles, depth)
    if name == "Table":
        return render_com_table(node, quick_styles, depth)
    if name == "Image":
        return render_com_missing_image(node)
    if name == "InsertedFile":
        return render_com_inserted_file(node)
    if name == "InkDrawing":
        return render_com_ink(node)
    return render_com_children(node, quick_styles, depth)


def render_com_children(node: ElementTree.Element, quick_styles: dict[str, str], depth: int = 0) -> str:
    return "".join(render_com_node(child, quick_styles, depth) for child in list(node))


def render_com_page_title(root: ElementTree.Element, quick_styles: dict[str, str]) -> str:
    for title in direct_children(root, "Title"):
        body = render_com_children(title, quick_styles, 0).strip()
        if body:
            return f'<p style="{TITLE_STYLE}">{body}</p>'
    return ""


def load_pages_from_com_xml(notebook: str, section: str, one_file: Path, media: MediaStore) -> tuple[list[dict[str, Any]], list[str]]:
    entries = load_com_section_pages(notebook, section)
    pages: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, entry in enumerate(entries):
        title = str(entry.get("name") or f"OneNote Page {index + 1}")
        if entry.get("error"):
            errors.append(f"{title}: {entry['error']}")
            continue
        page_xml = entry.get("xml") or ""
        if not page_xml:
            errors.append(f"{title}: empty COM page XML")
            continue
        try:
            root = ElementTree.fromstring(page_xml)
        except ElementTree.ParseError as exc:
            errors.append(f"{title}: XML parse failed: {exc}")
            continue
        before_stats = dict(media.stats)
        quick_styles = com_quick_style_names(root)
        content = (render_com_page_title(root, quick_styles) + render_com_children(root, quick_styles, 0)).strip() or "<p><br></p>"
        content = normalize_onenote_hyperlink_fields_in_html(content)
        image_count = len(iter_descendants(root, "Image"))
        attachment_count = len(iter_descendants(root, "InsertedFile"))
        ink_count = len(iter_descendants(root, "InkDrawing"))
        media.stats["missing_image_data"] += image_count
        media.stats["missing_attachment_data"] += attachment_count
        content_hash = hashlib.sha256((title + "\n" + content).encode("utf-8")).hexdigest()
        pages.append({
            "index": index,
            "title": title,
            "content": content,
            "content_hash": content_hash,
            "start_at": parse_com_timestamp(root.attrib.get("lastModifiedTime") or root.attrib.get("dateTime")),
            "last_modified": root.attrib.get("lastModifiedTime"),
            "rich_text_nodes": len(iter_descendants(root, "OE")),
            "rich_text_length": len("".join(text_node.text or "" for text_node in iter_descendants(root, "T"))),
            "image_count": image_count,
            "rendered_image_count": media.stats["images"] - before_stats["images"],
            "missing_image_data": media.stats["missing_image_data"] - before_stats["missing_image_data"],
            "table_count": len(iter_descendants(root, "Table")),
            "table_cell_background_count": sum(
                1
                for cell in iter_descendants(root, "Cell")
                if css_color(cell.attrib.get("shadingColor", ""))
            ),
            "attachment_count": attachment_count,
            "rendered_attachment_count": media.stats["attachments"] - before_stats["attachments"],
            "missing_attachment_data": media.stats["missing_attachment_data"] - before_stats["missing_attachment_data"],
            "ink_count": ink_count,
        })
    return pages, errors


def html_style(style: Any, *, include_font: bool = False) -> str:
    css: list[str] = []
    if getattr(style, "FontColor", None):
        color = css_color(getattr(style, "FontColor", None))
        if color:
            css.append(f"color:{html.escape(color, quote=True)}")
    if getattr(style, "Highlight", None):
        color = css_color(getattr(style, "Highlight", None))
        if color:
            css.append(f"background-color:{html.escape(color, quote=True)}")
    if include_font and getattr(style, "FontName", None):
        font_name = html.escape(str(style.FontName).replace("'", ""), quote=True)
        css.append(f"font-family:'{font_name}'")
    if include_font and getattr(style, "FontSize", None):
        try:
            font_size = float(style.FontSize)
        except (TypeError, ValueError):
            font_size = 0
        if font_size > 0:
            css.append(f"font-size:{font_size:g}pt")
    return ";".join(css)


def render_rich_text(node: Any, *, include_font: bool = False) -> str:
    parts: list[str] = []
    link_parts: list[str] = []
    pending_href: str | None = None
    active_href: str | None = None
    pending_field_marker = False
    collecting_field_href: str | None = None

    def flush_link() -> None:
        nonlocal active_href
        if active_href and link_parts:
            parts.append(
                f'<a href="{html.escape(active_href, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{"".join(link_parts)}</a>'
            )
        link_parts.clear()
        active_href = None

    def render_run_text(raw_text: str, style: Any) -> str:
        text = html.escape(raw_text)
        if style is not None:
            if getattr(style, "IsBold", False):
                text = f"<strong>{text}</strong>"
            if getattr(style, "IsItalic", False):
                text = f"<em>{text}</em>"
            if getattr(style, "IsUnderline", False):
                text = f"<u>{text}</u>"
            if getattr(style, "IsStrikethrough", False):
                text = f"<s>{text}</s>"
            css = html_style(style, include_font=include_font)
            if css:
                text = f'<span style="{css}">{text}</span>'
        return text

    def append_run_text(raw_text: str, style: Any, href: str | None = None) -> None:
        nonlocal active_href
        text = render_run_text(raw_text, style)
        if not text:
            return
        if href:
            if active_href != href:
                flush_link()
                active_href = href
            link_parts.append(text)
            return
        flush_link()
        parts.append(text)

    for run in node.TextRuns:
        raw_text = str(getattr(run, "Text", "") or "")
        if not raw_text:
            continue
        if pending_field_marker:
            raw_text = "\ufddf" + raw_text
            pending_field_marker = False
        field_marker_index = raw_text.rfind("\ufddf")
        if field_marker_index >= 0 and not re.match(r'\ufddf\s*HYPERLINK\s+"', raw_text[field_marker_index:]):
            pending_field_marker = True
            raw_text = raw_text[:field_marker_index] + raw_text[field_marker_index + 1:]
        style = getattr(run, "Style", None)
        is_hyperlink = bool(getattr(style, "IsHyperlink", False)) if style is not None else False
        href: str | None = None
        if collecting_field_href is not None:
            quote_index = raw_text.find('"')
            if quote_index >= 0:
                href = collecting_field_href + raw_text[:quote_index]
                collecting_field_href = None
                raw_text = raw_text[quote_index + 1:]
                if raw_text:
                    append_run_text(raw_text, style, href)
                else:
                    pending_href = href
                continue
            collecting_field_href += raw_text
            continue
        field_matches = list(ONENOTE_HYPERLINK_FIELD_ANY_RE.finditer(raw_text))
        if field_matches:
            pos = 0
            for index, match in enumerate(field_matches):
                if match.start() > pos:
                    append_run_text(raw_text[pos:match.start()], style)
                href = match.group(1)
                label_start = match.end()
                label_end = field_matches[index + 1].start() if index + 1 < len(field_matches) else len(raw_text)
                label = raw_text[label_start:label_end]
                if label:
                    append_run_text(label, style, href)
                else:
                    pending_href = href
                pos = label_end
            continue
        field_open_match = ONENOTE_HYPERLINK_FIELD_OPEN_RE.search(raw_text)
        if field_open_match:
            if field_open_match.start() > 0:
                append_run_text(raw_text[:field_open_match.start()], style)
            collecting_field_href = field_open_match.group(1)
            continue
        field_match = ONENOTE_HYPERLINK_FIELD_RE.match(raw_text)
        if field_match:
            href = field_match.group(1)
            pending_href = href
            raw_text = field_match.group(2)
            if not raw_text:
                continue
        elif is_hyperlink:
            style_href = getattr(style, "HyperlinkAddress", None)
            if style_href:
                pending_href = str(style_href)
            href = str(style_href or pending_href or "")
        else:
            pending_href = None

        text = render_run_text(raw_text, style)
        if not text:
            continue
        if href:
            if active_href != href:
                flush_link()
                active_href = href
            link_parts.append(text)
        else:
            flush_link()
            parts.append(text)
    flush_link()
    body = "".join(parts)
    if not body:
        fallback = str(getattr(node, "Text", "") or "")
        body = "" if ONENOTE_HYPERLINK_FIELD_RE.match(fallback) else html.escape(fallback)
    span_merge_pattern = re.compile(r"<span([^>]*)>(.*?)</span><span\1>(.*?)</span>", re.DOTALL)
    while True:
        next_body = span_merge_pattern.sub(r"<span\1>\2\3</span>", body)
        if next_body == body:
            break
        body = next_body
    return body.replace("\n", "<br>")


def has_ancestor(node: Any, cls: type) -> bool:
    parent = getattr(node, "ParentNode", None)
    while parent is not None:
        if isinstance(parent, cls):
            return True
        parent = getattr(parent, "ParentNode", None)
    return False


class MediaStore:
    def __init__(self, attach_dir: Path, import_name: str, *, dry_run: bool = False):
        self.attach_dir = attach_dir
        self.import_name = import_name
        self.dry_run = dry_run
        if not self.dry_run:
            self.attach_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {
            "images": 0,
            "attachments": 0,
            "missing_image_data": 0,
            "missing_attachment_data": 0,
            "reused": 0,
            "written": 0,
        }

    def save(self, prefix: str, label: str, data: bytes, fallback_suffix: str) -> tuple[str, str]:
        digest = hashlib.sha1(data).hexdigest()[:24]
        extension = Path(label).suffix.lower() or fallback_suffix
        if not extension:
            extension = ".bin"
        safe_label = slug_filename(Path(label).stem, prefix)
        filename = f"onenote_{self.import_name}_{prefix}_{digest}_{safe_label}{extension}"
        if self.dry_run:
            return f"/static/attachments/{filename}", filename
        target = self.attach_dir / filename
        if target.exists():
            self.stats["reused"] += 1
        else:
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            self.stats["written"] += 1
        return f"/static/attachments/{filename}", filename


def render_missing_media(kind: str, label: str, details: str = "") -> str:
    suffix = f" ({html.escape(details)})" if details else ""
    return (
        f'<p data-onenote-missing-media="{html.escape(kind, quote=True)}" '
        f'style="{MISSING_MEDIA_STYLE}">[OneNote {html.escape(label)}未导出{suffix}]</p>'
    )


def render_image(node: Any, media: MediaStore) -> str:
    data = bytes(getattr(node, "Bytes", b"") or b"")
    label = str(getattr(node, "FileName", "") or "image.png")
    if not data:
        media.stats["missing_image_data"] += 1
        details = []
        if getattr(node, "Width", None) and getattr(node, "Height", None):
            details.append(f"{float(node.Width):g} x {float(node.Height):g}")
        details.append(label)
        return render_missing_media("image", "图片", ", ".join(details))
    extension = Path(label).suffix.lower() or mimetypes.guess_extension(str(getattr(node, "Format", "") or "")) or ".png"
    url, _ = media.save("image", label, data, extension)
    alt = html.escape(str(getattr(node, "AlternativeTextTitle", "") or label), quote=True)
    media.stats["images"] += 1
    return f'<figure><img src="{url}" alt="{alt}"></figure>'


def render_attachment(node: Any, media: MediaStore) -> str:
    data = bytes(getattr(node, "Bytes", b"") or b"")
    label = str(getattr(node, "FileName", "") or "attachment.bin")
    if not data:
        media.stats["missing_attachment_data"] += 1
        return render_missing_media("attachment", "附件", label)
    url, filename = media.save("file", label, data, Path(label).suffix.lower() or ".bin")
    media.stats["attachments"] += 1
    return (
        f'<p><a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'download="{html.escape(filename, quote=True)}">{html.escape(label)}</a></p>'
    )


def children(node: Any) -> list[Any]:
    try:
        return list(node.GetEnumerator())
    except Exception:
        return []


def render_table(table: Any, media: MediaStore, style_hints: dict[str, Any] | None = None) -> str:
    table_index = 0
    table_cell_shadings: list[list[list[str]]] = []
    if style_hints is not None:
        table_index = int(style_hints.get("_table_index", 0) or 0)
        style_hints["_table_index"] = table_index + 1
        table_cell_shadings = style_hints.get("table_cell_shadings") or []
    shading_grid = table_cell_shadings[table_index] if table_index < len(table_cell_shadings) else []
    rows = list(table.GetChildNodes(onenote.TableRow))
    html_rows: list[str] = []
    for row_index, row in enumerate(rows):
        cells = list(row.GetChildNodes(onenote.TableCell))
        rendered_cells = []
        for cell_index, cell in enumerate(cells):
            cell_style = TABLE_CELL_STYLE
            if row_index < len(shading_grid) and cell_index < len(shading_grid[row_index]):
                color = shading_grid[row_index][cell_index]
                if color:
                    cell_style += f";background-color:{html.escape(color, quote=True)}"
            rendered_cells.append(
                f'<td style="{cell_style}">{render_children(cell, media, 0, style_hints)}</td>'
            )
        html_rows.append("<tr>" + "".join(rendered_cells) + "</tr>")
    return f'<table style="{TABLE_STYLE}"><tbody>' + "".join(html_rows) + "</tbody></table>"


def block_style(base_style: str, depth: int) -> str:
    if depth <= 0:
        return base_style
    return f"{base_style};margin-left:{depth * OUTLINE_INDENT_EM}em"


def consume_rich_text_block_style(style_hints: dict[str, Any] | None) -> dict[str, str]:
    if style_hints is None:
        return {}
    index = int(style_hints.get("_rich_text_index", 0) or 0)
    style_hints["_rich_text_index"] = index + 1
    blocks = style_hints.get("rich_text_blocks") or []
    if index >= len(blocks):
        return {}
    block = blocks[index]
    return block if isinstance(block, dict) else {}


def render_node(node: Any, media: MediaStore, depth: int = 0, style_hints: dict[str, Any] | None = None) -> str:
    if isinstance(node, onenote.Title):
        return ""
    if isinstance(node, onenote.RichText):
        block = consume_rich_text_block_style(style_hints)
        body = render_rich_text(node, include_font=True)
        if body.strip():
            tag = str(block.get("tag") or "")
            if re.fullmatch(r"h[1-4]", tag):
                return f'<{tag} style="{block_style(HEADING_STYLE, depth)}">{body}</{tag}>'
            return f'<p style="{block_style(PARAGRAPH_STYLE, depth)}">{body}</p>'
        if has_ancestor(node, onenote.TableCell):
            return ""
        return f'<p style="{block_style(PARAGRAPH_STYLE, depth)}"><br></p>'
    if isinstance(node, onenote.Outline):
        return render_outline(node, media, depth, style_hints)
    if isinstance(node, onenote.OutlineElement):
        return render_outline_element(node, media, depth, style_hints)
    if isinstance(node, onenote.Table):
        return render_table(node, media, style_hints)
    if isinstance(node, onenote.Image):
        if style_hints is not None:
            seen_media_nodes = style_hints.setdefault("_seen_media_nodes", set())
            key = ("image", id(node))
            if key in seen_media_nodes:
                return ""
            seen_media_nodes.add(key)
        return render_image(node, media)
    if isinstance(node, onenote.AttachedFile):
        if style_hints is not None:
            seen_media_nodes = style_hints.setdefault("_seen_media_nodes", set())
            key = ("attachment", id(node))
            if key in seen_media_nodes:
                return ""
            seen_media_nodes.add(key)
        return render_attachment(node, media)
    return render_children(node, media, depth, style_hints)


def render_outline(outline: Any, media: MediaStore, depth: int = 0, style_hints: dict[str, Any] | None = None) -> str:
    return "".join(
        render_outline_element(child, media, depth, style_hints)
        if isinstance(child, onenote.OutlineElement)
        else render_node(child, media, depth, style_hints)
        for child in children(outline)
    )


def render_outline_element(
    element: Any,
    media: MediaStore,
    depth: int,
    style_hints: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    for child in children(element):
        if isinstance(child, onenote.OutlineElement):
            parts.append(render_outline_element(child, media, depth + 1, style_hints))
        else:
            parts.append(render_node(child, media, depth, style_hints))
    return "".join(parts)


def render_children(
    node: Any,
    media: MediaStore,
    depth: int = 0,
    style_hints: dict[str, Any] | None = None,
) -> str:
    return "".join(render_node(child, media, depth, style_hints) for child in children(node))


def page_title(page: Any, index: int) -> str:
    title = getattr(page, "Title", None)
    if title is not None and getattr(title, "TitleText", None) is not None:
        text = str(getattr(title.TitleText, "Text", "") or "").strip()
        if text:
            return text
    rich_texts = list(page.GetChildNodes(onenote.RichText))
    for text_node in rich_texts:
        text = str(getattr(text_node, "Text", "") or "").strip()
        if text:
            return text[:80]
    return f"OneNote Page {index + 1}"


def render_page_title(page: Any) -> str:
    title = getattr(page, "Title", None)
    title_text = getattr(title, "TitleText", None) if title is not None else None
    if title_text is None:
        return ""
    body = render_rich_text(title_text, include_font=True)
    if not body.strip():
        return ""
    return f'<p style="{TITLE_STYLE}">{body}</p>'


def page_timestamp(page: Any) -> float:
    value = getattr(page, "LastModifiedTime", None) or getattr(page, "CreationTime", None)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TZ)
        return value.timestamp()
    return time.time()


def page_payload(
    page: Any,
    index: int,
    media: MediaStore,
    style_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = page_title(page, index)
    before_stats = dict(media.stats)
    style_context = dict(style_hints or {})
    style_context["_table_index"] = 0
    style_context["_rich_text_index"] = 0
    content = (render_page_title(page) + render_children(page, media, style_hints=style_context)).strip() or "<p><br></p>"
    content = normalize_onenote_hyperlink_fields_in_html(content)
    table_cell_background_count = sum(
        1
        for grid in (style_hints or {}).get("table_cell_shadings", [])
        for row in grid
        for color in row
        if color
    )
    rendered_image_count = media.stats["images"] - before_stats["images"]
    rendered_attachment_count = media.stats["attachments"] - before_stats["attachments"]
    missing_image_data = media.stats["missing_image_data"] - before_stats["missing_image_data"]
    missing_attachment_data = media.stats["missing_attachment_data"] - before_stats["missing_attachment_data"]
    text_hash = hashlib.sha256((title + "\n" + content).encode("utf-8")).hexdigest()
    rich_text_len = sum(len(str(getattr(rt, "Text", "") or "")) for rt in page.GetChildNodes(onenote.RichText))
    return {
        "index": index,
        "title": title,
        "content": content,
        "content_hash": text_hash,
        "start_at": page_timestamp(page),
        "last_modified": getattr(page, "LastModifiedTime", None),
        "rich_text_nodes": len(list(page.GetChildNodes(onenote.RichText))),
        "rich_text_length": rich_text_len,
        "image_count": len(list(page.GetChildNodes(onenote.Image))),
        "rendered_image_count": rendered_image_count,
        "missing_image_data": missing_image_data,
        "table_count": len(list(page.GetChildNodes(onenote.Table))),
        "table_cell_background_count": table_cell_background_count,
        "attachment_count": len(list(page.GetChildNodes(onenote.AttachedFile))),
        "rendered_attachment_count": rendered_attachment_count,
        "missing_attachment_data": missing_attachment_data,
    }


def custom_fields(
    *,
    import_name: str,
    source_kind: str,
    source_key: str,
    notebook: str,
    section: str,
    one_file: Path,
    extra: dict[str, Any] | None = None,
) -> str:
    rows: list[list[Any]] = [
        ["source", "string", IMPORT_SOURCE],
        ["source_import", "string", import_name],
        ["source_kind", "string", source_kind],
        ["source_key", "string", source_key],
        ["source_notebook", "string", notebook],
        ["source_section", "string", section],
        ["source_one_file", "string", str(one_file)],
        ["source_one_file_mtime", "number", one_file.stat().st_mtime],
    ]
    for key, value in (extra or {}).items():
        if value is None:
            continue
        value_type = "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "string"
        rows.append([f"source_{key}", value_type, value if value_type == "number" else str(value)])
    return json.dumps(rows, ensure_ascii=False)


def parse_custom_fields(raw: str | None) -> dict[str, Any]:
    try:
        rows = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return {}
    result: dict[str, Any] = {}
    for row in rows:
        if isinstance(row, list) and len(row) >= 3:
            result[str(row[0])] = row[2]
    return result


def as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def existing_by_source_key(con: sqlite3.Connection, source_key: str, user_id: int) -> sqlite3.Row | None:
    return con.execute(
        "select id,title,numeric_id from notenode where user_id=? and custom_fields like ? limit 1",
        (user_id, f"%{source_key}%"),
    ).fetchone()


def next_note_numeric_id(con: sqlite3.Connection) -> int:
    row = con.execute("select coalesce(max(numeric_id), 0) from notenode").fetchone()
    return int(row[0] or 0) + 1


def assign_missing_numeric_ids(con: sqlite3.Connection, user_id: int, import_name: str) -> int:
    rows = con.execute(
        """
        select id from notenode
        where user_id=? and numeric_id is null and custom_fields like ?
        order by created_at,id
        """,
        (user_id, f"%{import_name}%"),
    ).fetchall()
    next_id = next_note_numeric_id(con)
    for row in rows:
        con.execute("update notenode set numeric_id=? where id=?", (next_id, row["id"]))
        next_id += 1
    return len(rows)


def insert_node(
    con: sqlite3.Connection,
    *,
    user_id: int,
    title: str,
    content: str,
    weight: int,
    start_at: float,
    category: str,
    fields: str,
    note_form: str,
) -> str:
    node_id = str(uuid.uuid4())
    numeric_id = next_note_numeric_id(con)
    now = time.time()
    cats = note_categories(category)
    con.execute(
        """
        insert into notenode(
            id,numeric_id,user_id,title,content,created_at,updated_at,weight,start_at,task_status,history,
            node_type,node_status,custom_fields,private_level,color,note_kind,weight_mode,
            note_types,note_categories,primary_category,note_form,lifecycle_stage,note_scene
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            node_id, numeric_id, user_id, title, content, now, now, weight, start_at, None, "[]",
            category, "done", fields, 0, None, "note", None,
            cats, cats, category, note_form, "done", "note",
        ),
    )
    return node_id


def insert_edge(con: sqlite3.Connection, user_id: int, source_id: str, target_id: str) -> bool:
    if source_id == target_id:
        return False
    exists = con.execute(
        "select 1 from noteedge where user_id=? and source_id=? and target_id=? limit 1",
        (user_id, source_id, target_id),
    ).fetchone()
    if exists:
        return False
    con.execute(
        "insert into noteedge(id,user_id,source_id,target_id,label,created_at) values (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, source_id, target_id, None, time.time()),
    )
    return True


def load_pages(
    one_file: Path,
    media: MediaStore,
    *,
    style_hints_by_index: dict[int, dict[str, Any]] | None = None,
    style_hints_by_title: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    doc = onenote.Document(str(one_file))
    pages: list[dict[str, Any]] = []
    style_hints_by_index = style_hints_by_index or {}
    style_hints_by_title = style_hints_by_title or {}
    for index, page in enumerate(doc.GetChildNodes(onenote.Page)):
        title = page_title(page, index)
        style_hints = style_hints_by_index.get(index)
        if style_hints and style_hints.get("_title") and style_hints["_title"] != title:
            style_hints = None
        style_hints = style_hints or style_hints_by_title.get(title)
        pages.append(page_payload(page, index, media, style_hints=style_hints))
    return pages


def import_section(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    one_file = Path(args.one_file) if args.one_file else latest_section_file(Path(args.backup_root), args.notebook, args.section)
    import_name = args.import_name or f"codex-cli-onenote-{args.notebook}-{args.section}"
    category = args.category or DEFAULT_CATEGORY
    media = MediaStore(attachments_dir(data_dir), slug_filename(import_name, "import"), dry_run=bool(args.dry_run))
    style_hints_by_index: dict[int, dict[str, Any]] = {}
    style_hints_by_title: dict[str, dict[str, Any]] = {}
    com_style_errors: list[str] = []
    if not args.no_com_styles and os.name == "nt":
        try:
            style_hints_by_index, style_hints_by_title, com_style_errors = load_com_section_style_hints(
                args.notebook,
                args.section,
            )
        except Exception as exc:
            com_style_errors = [f"COM style extraction failed: {exc}"]
    source_loader = "aspose"
    source_loader_error = ""
    com_content_errors: list[str] = []
    try:
        pages = load_pages(
            one_file,
            media,
            style_hints_by_index=style_hints_by_index,
            style_hints_by_title=style_hints_by_title,
        )
    except Exception as exc:
        if getattr(args, "no_com_fallback", False) or os.name != "nt":
            raise
        source_loader = "com_xml_fallback"
        source_loader_error = repr(exc)
        pages, com_content_errors = load_pages_from_com_xml(args.notebook, args.section, one_file, media)

    section_source_key = f"onenote:{args.notebook}/{args.section}"
    page_items = []
    for page in pages:
        source_key = f"{section_source_key}:page:{page['index']}"
        page_items.append((source_key, page))

    summary: dict[str, Any] = {
        "one_file": str(one_file),
        "notebook": args.notebook,
        "section": args.section,
        "dry_run": bool(args.dry_run),
        "page_count": len(pages),
        "source_loader": source_loader,
        "source_loader_error": source_loader_error,
        "com_content_errors": com_content_errors[:20],
        "com_style_pages": len(style_hints_by_index),
        "com_style_errors": com_style_errors[:20],
        "pages": [
            {
                "index": page["index"],
                "title_length": len(page["title"]),
                "content_length": len(page["content"]),
                "rich_text_nodes": page["rich_text_nodes"],
                "rich_text_length": page["rich_text_length"],
                "image_count": page["image_count"],
                "rendered_image_count": page["rendered_image_count"],
                "missing_image_data": page["missing_image_data"],
                "table_count": page["table_count"],
                "table_cell_background_count": page["table_cell_background_count"],
                "attachment_count": page["attachment_count"],
                "rendered_attachment_count": page["rendered_attachment_count"],
                "missing_attachment_data": page["missing_attachment_data"],
                "ink_count": page.get("ink_count", 0),
            }
            for page in pages
        ],
        "media": media.stats,
    }
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    db = db_path(data_dir)
    backup: Path | None = None
    if not getattr(args, "skip_backup", False):
        backup = Path(os.environ.get("TEMP", str(data_dir))) / f"codeyun_onenote_backup_{dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f')}.db"
        shutil.copy2(db, backup)
    con = sqlite3.connect(db, timeout=60)
    con.row_factory = sqlite3.Row

    inserted: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    edges = 0
    try:
        section_row = existing_by_source_key(con, section_source_key, args.user_id)
        if section_row:
            section_id = section_row["id"]
        else:
            section_fields = custom_fields(
                import_name=import_name,
                source_kind="onenote_section",
                source_key=section_source_key,
                notebook=args.notebook,
                section=args.section,
                one_file=one_file,
                extra={"page_count": len(pages)},
            )
            section_id = insert_node(
                con,
                user_id=args.user_id,
                title=f"{args.notebook} / {args.section}",
                content=f"<p>OneNote \u5206\u533a\u8fc1\u79fb\u5165\u53e3\uff0c\u5305\u542b {len(pages)} \u4e2a\u9875\u9762\u3002</p>",
                weight=1,
                start_at=time.time(),
                category=category,
                fields=section_fields,
                note_form="note",
            )
            inserted.append({"kind": "section", "id": section_id})

        for source_key, page in page_items:
            fields = custom_fields(
                import_name=import_name,
                source_kind="onenote_page",
                source_key=source_key,
                notebook=args.notebook,
                section=args.section,
                one_file=one_file,
                extra={
                    "page_index": page["index"],
                    "content_hash": page["content_hash"],
                    "rich_text_nodes": page["rich_text_nodes"],
                    "rich_text_length": page["rich_text_length"],
                    "image_count": page["image_count"],
                    "rendered_image_count": page["rendered_image_count"],
                    "missing_image_data": page["missing_image_data"],
                    "table_count": page["table_count"],
                    "table_cell_background_count": page["table_cell_background_count"],
                    "attachment_count": page["attachment_count"],
                    "rendered_attachment_count": page["rendered_attachment_count"],
                    "missing_attachment_data": page["missing_attachment_data"],
                    "ink_count": page.get("ink_count", 0),
                    "last_modified": page["last_modified"],
                },
            )
            existing = existing_by_source_key(con, source_key, args.user_id)
            if existing:
                if args.update_existing:
                    con.execute(
                        "update notenode set title=?,content=?,updated_at=?,start_at=?,custom_fields=? where id=?",
                        (page["title"], page["content"], time.time(), page["start_at"], fields, existing["id"]),
                    )
                    skipped.append({"source_key": source_key, "reason": "updated_existing"})
                else:
                    skipped.append({"source_key": source_key, "reason": "source_key_exists"})
                continue
            node_id = insert_node(
                con,
                user_id=args.user_id,
                title=page["title"],
                content=page["content"],
                weight=0,
                start_at=page["start_at"],
                category=category,
                fields=fields,
                note_form="document",
            )
            inserted.append({"kind": "page", "id": node_id})
            if insert_edge(con, args.user_id, section_id, node_id):
                edges += 1

        numeric_assigned = assign_missing_numeric_ids(con, args.user_id, import_name)
        con.commit()
    finally:
        con.close()

    summary.update({
        "backup": str(backup) if backup else "",
        "inserted": len(inserted),
        "skipped": len(skipped),
        "edges": edges,
        "numeric_assigned": numeric_assigned,
        "inserted_items": inserted,
        "skipped_items": skipped,
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def validate(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    import_name = args.import_name or f"codex-cli-onenote-{args.notebook}-{args.section}"
    con = sqlite3.connect(db_path(data_dir), timeout=60)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id,numeric_id,title,content,custom_fields from notenode where user_id=? and custom_fields like ?",
        (args.user_id, f"%{import_name}%"),
    ).fetchall()
    attach_dir = attachments_dir(data_dir)
    missing_files: list[str] = []
    local_refs = 0
    media_mismatches: list[dict[str, Any]] = []
    missing_image_placeholders = 0
    missing_attachment_placeholders = 0
    for row in rows:
        for name in re.findall(r"/static/attachments/([^\"'<>\\s]+)", row["content"] or ""):
            local_refs += 1
            if not (attach_dir / name).exists():
                missing_files.append(name)
        content = row["content"] or ""
        fields = parse_custom_fields(row["custom_fields"])
        expected_images = as_int(fields.get("source_image_count"))
        expected_attachments = as_int(fields.get("source_attachment_count"))
        image_refs = len(re.findall(r"<img\b", content, flags=re.IGNORECASE))
        attachment_refs = len(re.findall(r"<a\b[^>]+href=[\"']/static/attachments/", content, flags=re.IGNORECASE))
        image_placeholders = len(re.findall(r'data-onenote-missing-media=["\']image["\']', content))
        attachment_placeholders = len(re.findall(r'data-onenote-missing-media=["\']attachment["\']', content))
        missing_image_placeholders += image_placeholders
        missing_attachment_placeholders += attachment_placeholders
        if (
            expected_images != image_refs + image_placeholders
            or expected_attachments != attachment_refs + attachment_placeholders
        ):
            media_mismatches.append({
                "numeric_id": row["numeric_id"],
                "title": row["title"],
                "expected_images": expected_images,
                "image_refs": image_refs,
                "missing_image_placeholders": image_placeholders,
                "expected_attachments": expected_attachments,
                "attachment_refs": attachment_refs,
                "missing_attachment_placeholders": attachment_placeholders,
            })
    con.close()
    print(json.dumps({
        "import_name": import_name,
        "node_count": len(rows),
        "local_refs": local_refs,
        "missing_file_count": len(missing_files),
        "missing_files": missing_files[:20],
        "missing_image_placeholders": missing_image_placeholders,
        "missing_attachment_placeholders": missing_attachment_placeholders,
        "media_mismatch_count": len(media_mismatches),
        "media_mismatches": media_mismatches[:20],
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a OneNote .one section backup into CodeYun star notes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--notebook", default=DEFAULT_NOTEBOOK)
    import_parser.add_argument("--section", default="2026")
    import_parser.add_argument("--one-file", default="")
    import_parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    import_parser.add_argument("--data-dir", default="")
    import_parser.add_argument("--import-name", default="")
    import_parser.add_argument("--category", default=DEFAULT_CATEGORY)
    import_parser.add_argument("--user-id", type=int, default=USER_ID)
    import_parser.add_argument("--dry-run", action="store_true")
    import_parser.add_argument("--update-existing", action="store_true")
    import_parser.add_argument("--no-com-styles", action="store_true")
    import_parser.add_argument("--no-com-fallback", action="store_true")
    import_parser.add_argument("--skip-backup", action="store_true")
    import_parser.set_defaults(func=import_section)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--notebook", default=DEFAULT_NOTEBOOK)
    validate_parser.add_argument("--section", default="2026")
    validate_parser.add_argument("--data-dir", default="")
    validate_parser.add_argument("--import-name", default="")
    validate_parser.add_argument("--user-id", type=int, default=USER_ID)
    validate_parser.set_defaults(func=validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
