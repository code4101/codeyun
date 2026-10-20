"""Download small public SkillDep plan materials to this device and register them on CodeHub.

CLI: ``uv run python -m backend.core.skilldep_materials --help``.
"""
from .acquire import HARD_TOTAL_LIMIT, acquire, materials_root
from .codehub import CodeHubError, CodeHubSkillDep
from .download import DownloadTooLarge, fetch_to_file, sha256_file

__all__ = ['HARD_TOTAL_LIMIT', 'CodeHubError', 'CodeHubSkillDep', 'DownloadTooLarge',
           'acquire', 'fetch_to_file', 'materials_root', 'sha256_file']
