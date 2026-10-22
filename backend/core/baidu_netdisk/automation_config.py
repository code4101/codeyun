"""Baidu Netdisk automation config and root-derived paths.

Pure stdlib at import time: no settings, env or GUI access. The runtime
environment is bootstrapped lazily only when the default config is requested.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath

CONFIG_VERSION = 1
_SCENE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class AutomationConfigError(ValueError):
    """Config file missing, malformed or semantically invalid."""


class DestinationError(AutomationConfigError):
    """Destination escapes the configured local_root."""


def _absolute(name: str, value) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise AutomationConfigError(f"{name} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        raise AutomationConfigError(f"{name} must be absolute: {value}")
    return path


@dataclass(frozen=True)
class AutomationPaths:
    """Single owner of every path derived from the automation root."""
    root: Path

    def __post_init__(self):
        object.__setattr__(self, "root", _absolute("root", self.root))

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def transfers_file(self) -> Path:
        return self.root / "transfers.json"

    @property
    def operations_dir(self) -> Path:
        return self.root / "operations"

    @property
    def failures_dir(self) -> Path:
        return self.root / "failures"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"


@dataclass(frozen=True)
class AutomationConfig:
    profile: str
    scene_ids: tuple[str, ...]
    client_download_dir: Path
    local_root: Path
    paths: AutomationPaths

    def __post_init__(self):
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise AutomationConfigError("profile must be a non-empty string")
        if isinstance(self.scene_ids, str) or not isinstance(self.scene_ids, (list, tuple)):
            raise AutomationConfigError("scene_ids must be a list of strings")
        if not self.scene_ids:
            raise AutomationConfigError("scene_ids must be non-empty")
        for scene in self.scene_ids:
            if not isinstance(scene, str) or not _SCENE_ID.match(scene):
                raise AutomationConfigError(f"invalid scene id: {scene!r}")
        if len(set(self.scene_ids)) != len(self.scene_ids):
            raise AutomationConfigError("scene_ids must be unique")
        object.__setattr__(self, "scene_ids", tuple(self.scene_ids))
        object.__setattr__(self, "client_download_dir",
                           _absolute("client_download_dir", self.client_download_dir))
        object.__setattr__(self, "local_root", _absolute("local_root", self.local_root))
        if not isinstance(self.paths, AutomationPaths):
            raise AutomationConfigError("paths must be AutomationPaths")

    def resolve_destination(self, relative: str | PurePath) -> Path:
        """Return local_root/relative; reject absolute paths and traversal."""
        text = str(relative)
        if not text.strip():
            raise DestinationError("destination must be non-empty")
        rel = PurePath(text.replace("\\", "/"))
        if rel.is_absolute() or rel.drive or rel.root or ".." in rel.parts:
            raise DestinationError(f"destination escapes local_root: {relative}")
        root = self.local_root.resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise DestinationError(f"destination escapes local_root: {relative}")
        return target

    def to_json(self) -> dict:
        return {"version": CONFIG_VERSION, "root": str(self.paths.root),
                "profile": self.profile, "scene_ids": list(self.scene_ids),
                "client_download_dir": str(self.client_download_dir),
                "local_root": str(self.local_root)}

    @classmethod
    def from_json(cls, data) -> "AutomationConfig":
        if not isinstance(data, dict):
            raise AutomationConfigError("config must be a JSON object")
        if data.get("version") != CONFIG_VERSION:
            raise AutomationConfigError(f"unsupported config version: {data.get('version')!r}")
        missing = [k for k in ("root", "profile", "scene_ids", "client_download_dir",
                               "local_root") if k not in data]
        if missing:
            raise AutomationConfigError(f"missing config keys: {missing}")
        return cls(profile=data["profile"], scene_ids=data["scene_ids"],
                   client_download_dir=data["client_download_dir"],
                   local_root=data["local_root"],
                   paths=AutomationPaths(data["root"]))


def bootstrap_runtime():
    """Load xlproject env, then return CodeYun settings. Never prints env values."""
    try:
        importlib.import_module("xlproject.loadenv")
    except ModuleNotFoundError as exc:
        if exc.name not in ("xlproject", "xlproject.loadenv"):
            raise  # a dependency missing inside loadenv is a real error, not a path issue
        from backend.core.agents.policy import load_config
        src = str(Path(load_config()["codeyun_root"]).parent / "xlproject" / "src")
        if src not in sys.path:
            sys.path.append(src)  # only xlproject; never shadows the calling worktree
        importlib.import_module("xlproject.loadenv")
    from backend.core.settings import get_settings
    return get_settings()


def default_paths() -> AutomationPaths:
    return AutomationPaths(Path(bootstrap_runtime().data_dir) / "baidu-netdisk" / "automation")


def load_automation_config(path: str | Path | None = None) -> AutomationConfig:
    """Read config; path=None bootstraps runtime and uses the default root."""
    file = Path(path) if path is not None else default_paths().config_file
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AutomationConfigError(f"config not found: {file}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AutomationConfigError(f"config is not valid JSON: {file}: {exc}") from exc
    return AutomationConfig.from_json(data)


def write_automation_config(config: AutomationConfig, overwrite: bool = False) -> Path:
    """Atomically write config to config.paths.config_file.

    overwrite=False claims the target with a same-directory hardlink, so a file
    created concurrently by another initializer is never clobbered.
    """
    file = config.paths.config_file
    if file.exists() and not overwrite:
        raise FileExistsError(f"config exists, pass overwrite=True: {file}")
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config.to_json(), fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, file)
        else:
            try:
                os.link(tmp, file)
            except FileExistsError:
                raise FileExistsError(f"config exists, pass overwrite=True: {file}") from None
    finally:
        Path(tmp).unlink(missing_ok=True)
    return file
