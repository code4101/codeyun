from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal


ServiceCommand = Callable[[], dict[str, Any]]
ServiceStatusProbe = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class ServiceDefinition:
    """Code catalog entry for an independently running service."""

    key: str
    title: str
    start: ServiceCommand
    stop: ServiceCommand
    status: ServiceStatusProbe


@dataclass(frozen=True)
class ServicePolicy:
    autostart: bool = False
    restart: Literal["never", "on-failure", "always"] = "never"


@dataclass(frozen=True)
class ServiceObservedState:
    key: str
    running: bool
    pid: int | None = None
    health: str = "unknown"
    detail: dict[str, Any] | None = None

