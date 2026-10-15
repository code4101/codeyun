"""Exact framework-only marker verifier; no game or scheduling side effects."""
from pathlib import Path
from backend.core.temp_paths import codeyun_temp_root


def verify_marker_file(record: dict) -> tuple[bool, str]:
    context = record['request'].get('acceptance_context') or {}
    path = Path(context['marker_path']).resolve()
    root = codeyun_temp_root('agent-smoke', create=False).resolve()
    if not path.is_relative_to(root):
        return False, 'Marker outside framework smoke directory'
    return path.read_text(encoding='utf-8').strip() == context['expected_marker'], 'Exact framework marker checked'
