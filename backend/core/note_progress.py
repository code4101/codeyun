from __future__ import annotations

import ast
import math
import re
from typing import Any

NOTE_COMPLETION_PROGRESS_EXPR_FIELD = "__completion_progress_expr"

_PERCENT_TOKEN_RE = re.compile(r"(?P<number>(?:\d+(?:\.\d*)?)|(?:\.\d+))\s*%")


def is_note_system_custom_field_key(key: Any) -> bool:
    return isinstance(key, str) and key.startswith("__")


def normalize_completion_progress_expr(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _replace_percent_tokens(text: str) -> str:
    return _PERCENT_TOKEN_RE.sub(lambda match: f"({match.group('number')}/100)", text)


def _eval_numeric_expression(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_numeric_expression(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ValueError("Only numeric constants are allowed")

    if isinstance(node, ast.Num):
        return float(node.n)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_numeric_expression(node.operand)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError("Unsupported unary operator")

    if isinstance(node, ast.BinOp):
        left = _eval_numeric_expression(node.left)
        right = _eval_numeric_expression(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("Division by zero")
            return left / right
        raise ValueError("Unsupported binary operator")

    raise ValueError("Unsupported expression")


def evaluate_completion_progress_expr(value: Any) -> float | None:
    text = normalize_completion_progress_expr(value)
    if not text:
        return None

    try:
        parsed = ast.parse(_replace_percent_tokens(text), mode="eval")
        result = _eval_numeric_expression(parsed)
    except Exception:
        return None

    if not math.isfinite(result):
        return None

    return max(0.0, min(1.0, float(result)))


def get_custom_field_value(custom_fields: Any, key: str) -> Any:
    if isinstance(custom_fields, list):
        for item in custom_fields:
            if isinstance(item, list) and len(item) >= 3 and item[0] == key:
                return item[2]
            if isinstance(item, tuple) and len(item) >= 3 and item[0] == key:
                return item[2]
            if isinstance(item, dict) and item.get("key") == key:
                return item.get("value")
        return None

    if isinstance(custom_fields, dict):
        return custom_fields.get(key)

    return None


def get_completion_progress_expr(custom_fields: Any) -> str | None:
    return normalize_completion_progress_expr(
        get_custom_field_value(custom_fields, NOTE_COMPLETION_PROGRESS_EXPR_FIELD)
    )


def set_completion_progress_expr(custom_fields: Any, expr: Any) -> Any:
    normalized_expr = normalize_completion_progress_expr(expr)

    if isinstance(custom_fields, dict):
        next_fields = dict(custom_fields)
        if normalized_expr is None:
            next_fields.pop(NOTE_COMPLETION_PROGRESS_EXPR_FIELD, None)
        else:
            next_fields[NOTE_COMPLETION_PROGRESS_EXPR_FIELD] = normalized_expr
        return next_fields

    next_fields: list[list[Any]] = []
    if isinstance(custom_fields, list):
        for item in custom_fields:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                key = item[0]
                if key == NOTE_COMPLETION_PROGRESS_EXPR_FIELD:
                    continue
                next_fields.append([item[0], item[1], item[2]])
            elif isinstance(item, dict):
                key = item.get("key")
                if key == NOTE_COMPLETION_PROGRESS_EXPR_FIELD:
                    continue
                if isinstance(key, str) and key.strip():
                    next_fields.append([
                        key,
                        item.get("type", "string"),
                        item.get("value"),
                    ])

    if normalized_expr is not None:
        next_fields.append([NOTE_COMPLETION_PROGRESS_EXPR_FIELD, "string", normalized_expr])

    return next_fields
