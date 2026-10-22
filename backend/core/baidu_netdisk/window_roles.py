"""Pure declared window-role selection for asset scenes (no UI access, no writes).

A scene declares ``window_role`` (default ``main``) and exact ``window_title``.
Builtin roles keep their public titles; any other role slug is accepted only
with a non-empty exact title and every profile fact (``client_version``,
``width``, ``height``, ``dpi``) explicitly valid.  Windows are selected by exact
title plus the *live* client facts returned by the caller's ``fact_reader``
(never outer list-row sizes); a fact read failure or a scene without declared
facts is never a match.  All
matches are returned so the caller can refuse ambiguity.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Mapping

from .client import FILE_DIALOG_TITLE

PROFILE_FACTS = ("client_version", "width", "height", "dpi")
MAIN_TITLE = "百度网盘"
SETTINGS_TITLE = "设置"
WINDOW_ROLES = {"main": MAIN_TITLE, "settings": SETTINGS_TITLE, "file-dialog": FILE_DIALOG_TITLE}
ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
UNDECLARED, UNKNOWN = "undeclared", "unknown"

FactReader = Callable[[str], Mapping[str, object]]


def _facts(body: Mapping) -> dict | None:
    """Declared profile facts, None when none declared; ValueError when partial or malformed."""
    present = [k for k in PROFILE_FACTS if body.get(k) is not None]
    if not present:
        return None
    if len(present) != len(PROFILE_FACTS):
        raise ValueError(f"scene must declare all of {', '.join(PROFILE_FACTS)}")
    version = body["client_version"]
    if not isinstance(version, str) or not version.strip():
        raise ValueError("client_version must be a non-empty string")
    for key in ("width", "height", "dpi"):
        value = body[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
    return {k: body[k] for k in PROFILE_FACTS}


def scene_window(body: Mapping) -> tuple[str, str]:
    """(window_role, exact window title) a scene declares; ValueError when undeclared or inconsistent."""
    role = body.get("window_role", "main")
    if not isinstance(role, str) or not ROLE_RE.match(role):
        raise ValueError(f"malformed window_role: {role!r}")
    title = body.get("window_title", MAIN_TITLE if role == "main" else None)
    if role in WINDOW_ROLES:
        if title != WINDOW_ROLES[role]:
            raise ValueError(f"window_title must be exactly the {role} window title")
    elif not isinstance(title, str) or not title or title != title.strip():
        raise ValueError(f"{role} scene must declare a non-empty exact window_title")
    facts = _facts(body)
    if role != "main" and facts is None:
        raise ValueError(f"{role} scene must declare {', '.join(PROFILE_FACTS)}")
    return role, title


def window_specs(scene_bodies: Iterable[Mapping], profile: str) -> list[dict]:
    """Normalized ``{role, title, facts}`` specs of the profile's scenes; nothing is fabricated for absent roles.

    A main scene without profile facts keeps a spec (``facts`` None) that never selects a window."""
    specs: list[dict] = []
    titles: dict[str, str] = {}
    for body in scene_bodies:
        if body.get("profile") != profile:
            continue
        role, title = scene_window(body)
        if titles.setdefault(role, title) != title:
            raise ValueError(f"conflicting window_title for role {role}")
        spec = {"role": role, "title": title, "facts": _facts(body)}
        if spec not in specs:
            specs.append(spec)
    return specs


def _live(fact_reader: FactReader, window_id: str) -> dict | None:
    try:
        facts = dict(fact_reader(window_id))
        return facts if all(k in facts for k in PROFILE_FACTS) else None
    except Exception:  # window gone, generation changed, no driver support: no facts
        return None


def _matches(spec: dict, title: object, live: dict | None) -> bool:
    """Exact title and every declared fact equal to the live one; no declared or no live facts: never."""
    if title != spec["title"] or spec["facts"] is None or live is None:
        return False
    return all(live[k] == v for k, v in spec["facts"].items())


def role_rows(windows: Iterable[Mapping], specs: list[dict], role: str, fact_reader: FactReader) -> list[dict]:
    """Every window row matching one of ``role``'s specs by exact title and live client facts."""
    role_specs = [s for s in specs if s["role"] == role]
    if not role_specs:
        raise ValueError(f"window_role not declared: {role!r}")
    rows = []
    for w in windows:
        if not any(s["title"] == w.get("title") for s in role_specs):
            continue
        live = _live(fact_reader, w["id"])
        if any(_matches(s, w.get("title"), live) for s in role_specs):
            rows.append(dict(w))
    return rows


def interfering_windows(windows: Iterable[Mapping], specs: list[dict], main_window_id: str,
                        fact_reader: FactReader) -> list[str]:
    """Sorted labels (declared roles, ``undeclared`` or ``unknown``) of visible non-minimized other windows.

    ``windows`` is the caller's official-client list; no title text is reported.
    """
    found: set[str] = set()
    for w in windows:
        if w.get("id") == main_window_id:
            continue
        live = _live(fact_reader, w["id"])
        visible = None if live is None else live.get("visible")
        minimized = None if live is None else live.get("minimized")
        if not isinstance(visible, bool) or not isinstance(minimized, bool):
            found.add(UNKNOWN)
            continue
        if not visible or minimized:
            continue
        roles = {s["role"] for s in specs if _matches(s, w.get("title"), live)}
        found |= roles or {UNDECLARED}
    return sorted(found)


__all__ = ["MAIN_TITLE", "PROFILE_FACTS", "SETTINGS_TITLE", "UNDECLARED", "UNKNOWN", "WINDOW_ROLES",
           "interfering_windows", "role_rows", "scene_window", "window_specs"]
