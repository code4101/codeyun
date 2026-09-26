"""Body formats are independent of note taxonomy and graph structure.

HTML remains native HTML. Plate uses a versioned JSON envelope in the existing
content field, so permissions, history and field-level conflict handling stay shared.
No implicit HTML/Markdown conversion takes place during reads or writes.
"""
import json


def validate_note_body(format_type: str, content: str) -> None:
    if format_type not in {"html", "markdown", "plate"}:
        raise ValueError("不支持的正文格式")
    if format_type != "plate":
        return
    try:
        body = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise ValueError("Plate 正文必须是有效 JSON") from exc
    if not isinstance(body, dict) or body.get("schema") != "codeyun.plate" or body.get("version") != 1:
        raise ValueError("不支持的 Plate 正文版本")
    value = body.get("value")
    if not isinstance(value, list) or not value:
        raise ValueError("Plate 正文需要至少一个段落")

    def check(node, depth=0):
        if depth > 64 or not isinstance(node, dict):
            raise ValueError("Plate 节点结构无效")
        if "children" in node:
            if not isinstance(node.get("type"), str) or not isinstance(node["children"], list) or not node["children"]:
                raise ValueError("Plate 元素需要类型和子节点")
            for child in node["children"]:
                check(child, depth + 1)
        elif not isinstance(node.get("text"), str):
            raise ValueError("Plate 文本节点无效")
    for node in value:
        if not isinstance(node, dict) or "children" not in node:
            raise ValueError("Plate 顶层必须是元素")
        check(node)
