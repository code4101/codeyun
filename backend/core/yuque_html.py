from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Doctype, Tag


LEGACY_YUQUE_LAKE_RE = re.compile(
    r"<!DOCTYPE\s+lake|data-lake-id\s*=|data-lake-|class=[\"'][^\"']*lake-|name=[\"']doc-version[\"']",
    re.IGNORECASE,
)


def looks_like_legacy_yuque_lake_html(value: Any) -> bool:
    text = str(value or "")
    return bool(text and LEGACY_YUQUE_LAKE_RE.search(text))


def _clean_tag_attributes(tag: Tag) -> None:
    for attr_name in list(tag.attrs.keys()):
        name = str(attr_name or "").lower()
        if (
            name == "id"
            or name == "fid"
            or name == "list"
            or name == "spellcheck"
            or name == "data-lake-id"
            or name.startswith("data-lake-")
        ):
            del tag.attrs[attr_name]

    class_value = tag.get("class")
    if class_value:
        classes = class_value if isinstance(class_value, list) else str(class_value).split()
        kept = [item for item in classes if item and not str(item).startswith("lake-")]
        if kept:
            tag["class"] = kept
        elif "class" in tag.attrs:
            del tag.attrs["class"]


def normalize_legacy_yuque_lake_html(value: Any) -> str:
    source = str(value or "")
    if not source or not looks_like_legacy_yuque_lake_html(source):
        return source

    soup = BeautifulSoup(source, "html.parser")

    for child in list(soup.contents):
        if isinstance(child, Doctype):
            child.extract()

    for tag in soup.find_all(["script", "style", "link", "meta", "title"]):
        tag.decompose()

    for tag in soup.find_all(True):
        _clean_tag_attributes(tag)

    root = soup.body if soup.body is not None else soup
    html = "".join(str(child) for child in root.contents).strip()
    return html or "<p><br></p>"
