"""Download small public SkillDep plan materials to this device and register them on CodeHub.

CLI: ``uv run python -m backend.core.skilldep_materials --help``.
"""
from .acquire import DEFAULT_ROOT, HARD_TOTAL_LIMIT, acquire, direct_bytes, materials_root, register_and_verify
from .codehub import CodeHubError, CodeHubSkillDep
from .download import DownloadTooLarge, fetch_to_file, sha256_file
from .samples import DEFAULT_SAMPLES_ROOT, sample, sample_and_register

__all__ = ['DEFAULT_ROOT', 'DEFAULT_SAMPLES_ROOT', 'HARD_TOTAL_LIMIT', 'CodeHubError', 'CodeHubSkillDep',
           'DownloadTooLarge', 'acquire', 'direct_bytes', 'fetch_to_file', 'materials_root',
           'register_and_verify', 'sample', 'sample_and_register', 'sha256_file']
