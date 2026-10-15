"""Engineering verifiers that gate incident acceptance beyond the agent's receipt.

A verifier is a provider-owned read-only check named in the request; the record
persists only the name, never a callable. Verifiers return (ok, detail) and must
not mutate business state. Unknown names fail closed at dispatch time.
"""
from __future__ import annotations

from importlib import import_module
from typing import Callable

VERIFIERS: dict[str, str] = {
    # Engineering (not AI) owns Fanxiu dispatch again after a repair.
    "fanxiu.engineering_control_returned":
        "backend.core.fanxiu.data_annotation.ai_assistance:verify_engineering_control_returned",
    # Framework smoke: an exact marker file written by the agent.
    "smoke.marker_file": "backend.core.agents.smoke:verify_marker_file",
    'fanxiu.storage_research_conclusion':
        'backend.core.fanxiu.storage_bag_unknown_research:verify_storage_research_conclusion',
}


def validate_checks(names) -> tuple[str, ...]:
    names = tuple(str(name) for name in names or ())
    unknown = [name for name in names if name not in VERIFIERS]
    if unknown:
        raise ValueError(f"未注册的验收检查：{unknown}")
    return names


def _resolve(name: str) -> Callable[[dict], tuple[bool, str]]:
    module, attribute = VERIFIERS[name].split(":")
    return getattr(import_module(module), attribute)


def run_checks(names, record: dict) -> dict[str, dict]:
    """Run every named check; an exception is a failed check, not a pass."""
    results = {}
    for name in names:
        try:
            ok, detail = _resolve(name)(record)
        except Exception as exc:  # noqa: BLE001 - diagnostic only, fails closed
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results[name] = {"ok": bool(ok), "detail": str(detail)[:500]}
    return results
