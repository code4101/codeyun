from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import tempfile
from typing import Any
from urllib.request import ProxyHandler, build_opener

from filelock import FileLock

from backend.core.settings import get_settings


DP_BROWSER_DEFAULT_HOST = "127.0.0.1"
DP_BROWSER_DEFAULT_PORT = 9222
DP_BROWSER_FALLBACK_PORT_COUNT = 10


@dataclass(frozen=True)
class DpBrowserEndpointProbe:
    """Classify one local port before DrissionPage decides whether to launch."""

    address: str
    status: str
    detail: str = ""


def get_dp_browser_runtime_state_path() -> Path:
    return get_settings().data_dir / "browser" / "dp_browser_runtime.json"


def get_dp_browser_profile_path() -> Path:
    configured = str(os.getenv("CODEYUN_DP_BROWSER_USER_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # This is DrissionPage's historical default for port 9222. Reusing it keeps
    # existing logins when an unrelated process happens to occupy that port.
    return Path(tempfile.gettempdir()) / "DrissionPage" / "userData" / str(DP_BROWSER_DEFAULT_PORT)


def configured_dp_browser_address() -> str:
    host = str(os.getenv("CODEYUN_DP_BROWSER_DEBUG_HOST") or DP_BROWSER_DEFAULT_HOST).strip()
    raw_port = str(os.getenv("CODEYUN_DP_BROWSER_DEBUG_PORT") or DP_BROWSER_DEFAULT_PORT).strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = DP_BROWSER_DEFAULT_PORT
    return f"{host or DP_BROWSER_DEFAULT_HOST}:{port}"


def probe_dp_browser_endpoint(address: str, *, timeout_seconds: float = 0.5) -> DpBrowserEndpointProbe:
    """Return ``ready``, ``available`` or ``occupied_non_cdp`` for an address."""

    host, raw_port = _split_address(address)
    try:
        with socket.create_connection((host, raw_port), timeout=max(0.05, float(timeout_seconds))):
            pass
    except OSError as exc:
        return DpBrowserEndpointProbe(address, "available", str(exc))

    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(
            f"http://{host}:{raw_port}/json/version",
            timeout=max(0.05, float(timeout_seconds)),
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        if isinstance(payload, dict) and str(payload.get("webSocketDebuggerUrl") or "").startswith("ws"):
            return DpBrowserEndpointProbe(address, "ready")
        return DpBrowserEndpointProbe(address, "occupied_non_cdp", "missing webSocketDebuggerUrl")
    except Exception as exc:
        return DpBrowserEndpointProbe(address, "occupied_non_cdp", str(exc))


def resolve_dp_browser_debug_address() -> str:
    """Resolve the live managed CDP endpoint without starting a browser."""

    configured = configured_dp_browser_address()
    state = _read_runtime_state()
    remembered = str(state.get("address") or "")
    for address in dict.fromkeys((remembered, configured)):
        if address and probe_dp_browser_endpoint(address).status == "ready":
            return address
    return configured


def connect_codeyun_dp_browser(*, headless: bool = False) -> Any:
    """Connect to CodeYun's shared DrissionPage browser, recovering port collisions.

    Port 9222 remains the preferred public address. If another local service
    occupies it without exposing Chrome DevTools Protocol, the provider reuses
    the same persistent DrissionPage profile on the first free managed fallback
    port and records that address for other CodeYun processes.
    """

    from DrissionPage import Chromium, ChromiumOptions

    state_path = get_dp_browser_runtime_state_path()
    lock_path = state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=15):
        configured = configured_dp_browser_address()
        host, preferred_port = _split_address(configured)
        state = _read_runtime_state()
        remembered = str(state.get("address") or "")
        candidates = list(
            dict.fromkeys(
                [
                    remembered,
                    configured,
                    *(f"{host}:{preferred_port + offset}" for offset in range(1, DP_BROWSER_FALLBACK_PORT_COUNT + 1)),
                ]
            )
        )
        probes = [probe_dp_browser_endpoint(address) for address in candidates if address]
        ready = next((probe for probe in probes if probe.status == "ready"), None)
        if ready is not None:
            browser = Chromium(ready.address)
            _write_runtime_state(ready.address, get_dp_browser_profile_path())
            return browser

        available = next((probe for probe in probes if probe.status == "available"), None)
        if available is None:
            details = "; ".join(f"{probe.address}={probe.status}" for probe in probes)
            raise RuntimeError(f"CodeYun DrissionPage 没有可用调试端口：{details}")

        defaults = ChromiumOptions()
        options = ChromiumOptions(read_file=False)
        options.set_address(available.address)
        options.set_browser_path(defaults.browser_path)
        options.set_user_data_path(get_dp_browser_profile_path())
        for argument in defaults.arguments:
            options.set_argument(argument)
        if headless:
            options.headless(True)
        browser = Chromium(options)
        _write_runtime_state(available.address, get_dp_browser_profile_path())
        return browser


def _split_address(address: str) -> tuple[str, int]:
    host, separator, raw_port = str(address or "").rpartition(":")
    if not separator or not host:
        raise ValueError(f"无效的浏览器调试地址：{address!r}")
    return host, int(raw_port)


def _read_runtime_state() -> dict[str, Any]:
    try:
        payload = json.loads(get_dp_browser_runtime_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_runtime_state(address: str, profile_path: Path) -> None:
    path = get_dp_browser_runtime_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"version": 1, "address": address, "profile_path": str(profile_path)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "DpBrowserEndpointProbe",
    "configured_dp_browser_address",
    "connect_codeyun_dp_browser",
    "get_dp_browser_profile_path",
    "probe_dp_browser_endpoint",
    "resolve_dp_browser_debug_address",
]
