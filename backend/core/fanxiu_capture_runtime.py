from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.fanxiu_android_proxy import DEFAULT_ADB_CANDIDATES
from backend.core.fanxiu_tcp_flow import resolve_fanxiu_tcp_live_capture_dir

FANXIU_CAPTURE_RUNTIME_SERVICE_KEY = "fanxiu-capture-runtime"
FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON = "auto-watchdog"
DEFAULT_FANXIU_DEVICE_ID = "127.0.0.1:7555"
FANXIU_DEVICE_ID_ENV_KEYS = ("FANXIU_CAPTURE_DEVICE_ID", "FANXIU_ADB_DEVICE_ID")
DEFAULT_FANXIU_PACKAGE_NAME = "com.frxxcrjpwssc3.ggws"
DEFAULT_REMOTE_CAPTURE_DIR = "/data/local/tmp"
DEFAULT_CAPTURE_IDLE_FINALIZE_SECONDS = 6.0
DEFAULT_CAPTURE_WATCHDOG_INTERVAL_SECONDS = 60.0
MIN_CAPTURE_PCAP_BYTES = 24


def _now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _completed_text(process: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        part.strip()
        for part in (process.stdout, process.stderr)
        if part and part.strip()
    )


class FanxiuCaptureRuntimeService:
    def __init__(
        self,
        *,
        device_id: str = DEFAULT_FANXIU_DEVICE_ID,
        package_name: str = DEFAULT_FANXIU_PACKAGE_NAME,
        supervisor_interval: float = 5.0,
        idle_finalize_seconds: float = DEFAULT_CAPTURE_IDLE_FINALIZE_SECONDS,
    ) -> None:
        self.device_id = self._configured_device_id() or str(device_id or DEFAULT_FANXIU_DEVICE_ID).strip()
        self.package_name = package_name
        self.supervisor_interval = supervisor_interval
        self.idle_finalize_seconds = max(1.0, float(idle_finalize_seconds))
        self._lock = threading.RLock()
        self._active_reasons: set[str] = set()
        self._state = "stopped"
        self._started_at = ""
        self._last_error = ""
        self._last_recover_at = ""
        self._game_running = False
        self._adb_connected = False
        self._root_ready = False
        self._tcpdump_ready = False
        self._tcpdump_process: subprocess.Popen[str] | None = None
        self._tcpdump_started_at = ""
        self._current_remote_pcap_path = ""
        self._current_pcap_path = ""
        self._current_pcap_size = 0
        self._last_remote_pcap_size = 0
        self._last_remote_pcap_size_seen_at = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog_stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_interval = DEFAULT_CAPTURE_WATCHDOG_INTERVAL_SECONDS
        self._watchdog_started_at = ""
        self._watchdog_last_check_at = ""
        self._watchdog_last_action = ""
        self._watchdog_last_error = ""
        self._logs: deque[str] = deque(maxlen=500)

    def ensure_running(self, reason: str = "manual") -> dict[str, Any]:
        normalized_reason = self._normalize_reason(reason)
        with self._lock:
            self._active_reasons.add(normalized_reason)
            if not self._started_at:
                self._started_at = _now_label()
            self._log(f"ensure {normalized_reason}")
            self._ensure_supervisor_locked()
            return self.status()

    def release(self, reason: str = "manual") -> dict[str, Any]:
        normalized_reason = self._normalize_reason(reason)
        with self._lock:
            self._active_reasons.discard(normalized_reason)
            self._log(f"release {normalized_reason}")
            if not self._active_reasons:
                self._force_stop_locked(clear_reasons=False, state="stopped")
            return self.status()

    def force_stop(self, reason: str = "runtime-stop") -> dict[str, Any]:
        with self._lock:
            self._log(f"force stop {self._normalize_reason(reason)}")
            self._force_stop_locked(clear_reasons=True, state="stopped")
            return self.status()

    def flush_recent_capture(self, reason: str = "manual-flush", *, restart: bool = True) -> dict[str, Any]:
        """Seal the current capture segment without decoding it in this service.

        This is the collaboration point for behavior-tree jobs that need the
        packet pipeline to catch up on very recent facts. The capture service
        only produces a sealed pcap; packet decoding and DB upserts remain the
        packet worker's responsibility.
        """
        normalized_reason = self._normalize_reason(reason)
        with self._lock:
            running = self._tcpdump_process_alive_locked()
            if not running:
                self._log(f"flush skipped {normalized_reason}: tcpdump not running")
                return {"ok": True, "flushed": False, "reason": normalized_reason, "status": self.status()}
            self._log(f"flush requested: {normalized_reason}")
            local_path = self._current_pcap_path
            self._stop_tcpdump_locked(queue_sync=False)
            local_size = Path(local_path).stat().st_size if local_path and Path(local_path).exists() else 0
            if restart and self._active_reasons:
                try:
                    self._start_tcpdump_locked()
                except Exception as exc:
                    self._mark_error_locked(exc)
            return {
                "ok": True,
                "flushed": bool(local_path and local_size > MIN_CAPTURE_PCAP_BYTES),
                "reason": normalized_reason,
                "restarted": bool(restart and self._active_reasons and self._tcpdump_process_alive_locked()),
                "pcap_path": local_path,
                "pcap_size": local_size,
                "status": self.status(),
            }

    def start_watchdog(self, *, interval_seconds: float = DEFAULT_CAPTURE_WATCHDOG_INTERVAL_SECONDS) -> dict[str, Any]:
        with self._lock:
            self._watchdog_interval = max(10.0, float(interval_seconds))
            self._watchdog_stop_event.clear()
            if self._watchdog_thread and self._watchdog_thread.is_alive():
                return self.status()
            self._watchdog_started_at = _now_label()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                name="fanxiu-capture-runtime-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()
            self._log(f"watchdog started: every {self._watchdog_interval:g}s")
            return self.status()

    def stop_watchdog(self) -> dict[str, Any]:
        with self._lock:
            self._watchdog_stop_event.set()
            self._active_reasons.discard(FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON)
            if not self._active_reasons:
                self._force_stop_locked(clear_reasons=False, state="stopped")
            self._watchdog_started_at = ""
            self._watchdog_last_action = "stopped"
            self._log("watchdog stopped")
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._tcpdump_process_alive_locked()
            self._refresh_current_pcap_size_locked()
            return {
                "state": self._state,
                "running": running,
                "game_running": self._game_running,
                "adb_connected": self._adb_connected,
                "root_ready": self._root_ready,
                "tcpdump_ready": self._tcpdump_ready,
                "active_reasons": sorted(self._active_reasons),
                "current_pcap_path": self._current_pcap_path,
                "current_pcap_size": self._current_pcap_size,
                "current_remote_pcap_path": self._current_remote_pcap_path,
                "started_at": self._started_at,
                "last_error": self._last_error,
                "last_recover_at": self._last_recover_at,
                "tcpdump_started_at": self._tcpdump_started_at,
                "device_id": self.device_id,
                "package_name": self.package_name,
                "watchdog_running": bool(self._watchdog_thread and self._watchdog_thread.is_alive()),
                "watchdog_started_at": self._watchdog_started_at,
                "watchdog_interval_seconds": self._watchdog_interval,
                "watchdog_last_check_at": self._watchdog_last_check_at,
                "watchdog_last_action": self._watchdog_last_action,
                "watchdog_last_error": self._watchdog_last_error,
            }

    def log_lines(self, limit: int = 500) -> list[str]:
        with self._lock:
            return list(self._logs)[-max(1, int(limit)):]

    def _ensure_supervisor_locked(self) -> None:
        self._stop_event.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._supervise_loop,
            name="fanxiu-capture-runtime",
            daemon=True,
        )
        self._thread.start()
        self._log("supervisor started")

    def _supervise_loop(self) -> None:
        while not self._stop_event.wait(0.1):
            with self._lock:
                has_consumers = bool(self._active_reasons)
            if not has_consumers:
                with self._lock:
                    self._force_stop_locked(clear_reasons=False, state="stopped")
                return
            try:
                self._supervise_once()
            except Exception as exc:
                with self._lock:
                    self._mark_error_locked(exc)
            self._stop_event.wait(self.supervisor_interval)

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop_event.is_set():
            self.watchdog_once()
            self._watchdog_stop_event.wait(self._watchdog_interval)

    def watchdog_once(self) -> dict[str, Any]:
        try:
            game_running = self.probe_game_running()
            if game_running:
                self.ensure_running(FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON)
                with self._lock:
                    self._watchdog_last_action = "ensure_running"
                    self._watchdog_last_error = ""
                    return self.status()
            self.release(FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON)
            with self._lock:
                self._watchdog_last_action = "skip_no_game"
                self._watchdog_last_error = ""
                return self.status()
        except Exception as exc:
            with self._lock:
                self._watchdog_last_check_at = _now_label()
                self._watchdog_last_action = "error"
                self._watchdog_last_error = str(exc)
                self._log(f"watchdog failed: {exc}")
                return self.status()

    def probe_game_running(self) -> bool:
        with self._lock:
            self._watchdog_last_check_at = _now_label()
        try:
            self._connect_adb()
            game_running = self._detect_game_running()
        except Exception as exc:
            with self._lock:
                self._adb_connected = False
                self._game_running = False
                self._watchdog_last_error = str(exc)
            return False
        with self._lock:
            self._game_running = game_running
        return game_running

    def _supervise_once(self) -> None:
        self._connect_adb()
        self._ensure_root()
        game_running = self._detect_game_running()
        with self._lock:
            self._game_running = game_running
            if not game_running:
                self._state = "waiting_game"
                self._last_error = ""
                if self._tcpdump_process_alive_locked():
                    self._log("game not running; stop tcpdump")
                    self._stop_tcpdump_locked()
                return

        if not self._check_tcpdump_available():
            raise RuntimeError("tcpdump 不可用")

        with self._lock:
            if self._tcpdump_process_alive_locked():
                self._state = "running"
                self._last_error = ""
                self._finalize_idle_capture_locked()
                return
            if self._tcpdump_process is not None:
                self._log("tcpdump exited; finalizing current pcap")
                self._stop_tcpdump_locked()
            self._start_tcpdump_locked()

    def _connect_adb(self) -> None:
        errors: list[str] = []
        for candidate in self._adb_device_candidates():
            try:
                output = self._ensure_adb_device_connected(candidate)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue
            with self._lock:
                previous_device_id = self.device_id
                self.device_id = candidate
                self._adb_connected = True
                if previous_device_id != candidate:
                    self._log(f"adb device switched: {previous_device_id} -> {candidate}")
                self._log(f"adb connect ok: {candidate}; {self._compact_output(output)}")
            return

        with self._lock:
            self._adb_connected = False
        detail = "；".join(errors) if errors else "没有可用 ADB 设备"
        raise RuntimeError(f"adb connect 未确认：{detail}")

    def _ensure_adb_device_connected(self, device_id: str) -> str:
        output_parts: list[str] = []
        if self._looks_like_adb_host_port(device_id):
            output_parts.append(self._run_adb(["connect", device_id], timeout=8))
        devices_output = self._run_adb(["devices"], timeout=8)
        output_parts.append(devices_output)
        if device_id in self._parse_adb_devices(devices_output):
            return "\n".join(part for part in output_parts if part)
        raise RuntimeError(f"{device_id} 不在 adb devices 在线列表中")

    def _adb_device_candidates(self) -> list[str]:
        candidates: list[str] = []
        configured_device_id = self._configured_device_id()
        if configured_device_id:
            candidates.append(configured_device_id)
        elif self.device_id != DEFAULT_FANXIU_DEVICE_ID:
            candidates.append(self.device_id)
        try:
            devices_output = self._run_adb(["devices"], timeout=8)
            candidates.extend(self._parse_adb_devices(devices_output))
        except Exception as exc:
            self._log(f"adb devices scan failed: {exc}")
        candidates.append(self.device_id)
        candidates.append(DEFAULT_FANXIU_DEVICE_ID)
        return self._dedupe_device_ids(candidates)

    def _configured_device_id(self) -> str:
        for key in FANXIU_DEVICE_ID_ENV_KEYS:
            value = os.environ.get(key)
            if value and value.strip():
                return value.strip()
        return ""

    def _parse_adb_devices(self, output: str) -> list[str]:
        devices: list[str] = []
        for line in str(output or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("list of devices"):
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def _dedupe_device_ids(self, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            device_id = str(value or "").strip()
            if not device_id or device_id in seen:
                continue
            seen.add(device_id)
            result.append(device_id)
        return result

    def _looks_like_adb_host_port(self, value: str) -> bool:
        host, sep, port = str(value or "").rpartition(":")
        return bool(sep and host and port.isdigit())

    def _ensure_root(self) -> None:
        output = self._run_adb(["-s", self.device_id, "root"], timeout=8)
        with self._lock:
            self._root_ready = True
            self._log(f"adb root: {self._compact_output(output)}")

    def _detect_game_running(self) -> bool:
        try:
            output = self._run_adb(["-s", self.device_id, "shell", "pidof", self.package_name], timeout=6)
            return bool(output.strip())
        except Exception:
            return False

    def _check_tcpdump_available(self) -> bool:
        output = self._run_adb(["-s", self.device_id, "shell", "command", "-v", "tcpdump"], timeout=6)
        ready = bool(output.strip())
        with self._lock:
            self._tcpdump_ready = ready
            if ready:
                self._log(f"tcpdump ready: {self._compact_output(output)}")
        return ready

    def _start_tcpdump_locked(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        capture_dir = resolve_fanxiu_tcp_live_capture_dir()
        local_path = capture_dir / f"fanxiu_runtime_{timestamp}.pcap"
        remote_path = f"{DEFAULT_REMOTE_CAPTURE_DIR}/codeyun_fanxiu_runtime_{timestamp}.pcap"
        command = [
            str(self._adb_path()),
            "-s",
            self.device_id,
            "shell",
            "tcpdump",
            "-i",
            "wlan0",
            "-s",
            "0",
            "-w",
            remote_path,
            "tcp",
            "and",
            "not",
            "port",
            "5555",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._tcpdump_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        self._current_remote_pcap_path = remote_path
        self._current_pcap_path = str(local_path)
        self._current_pcap_size = 0
        self._last_remote_pcap_size = 0
        self._last_remote_pcap_size_seen_at = time.monotonic()
        self._tcpdump_started_at = _now_label()
        self._state = "running"
        self._last_error = ""
        self._log(f"tcpdump started: {remote_path} -> {local_path}")
        self._verify_remote_capture_file(remote_path)

    def _verify_remote_capture_file(self, remote_path: str) -> None:
        deadline = time.time() + 2.5
        while time.time() < deadline:
            if self._tcpdump_process_alive_locked():
                return
            time.sleep(0.25)
        proc = self._tcpdump_process
        output = ""
        if proc is not None:
            try:
                stdout, stderr = proc.communicate(timeout=1)
                output = _completed_text(subprocess.CompletedProcess(proc.args, proc.returncode or 0, stdout, stderr))
            except Exception as exc:
                output = str(exc)
        raise RuntimeError(f"tcpdump 启动后立即退出：{remote_path}；{self._compact_output(output)}")

    def _stop_tcpdump_locked(self, *, queue_sync: bool = True) -> None:
        remote_path = self._current_remote_pcap_path
        local_path = self._current_pcap_path
        proc = self._tcpdump_process
        self._tcpdump_process = None
        if remote_path:
            try:
                self._run_adb(["-s", self.device_id, "shell", "pkill", "-INT", "tcpdump"], timeout=5)
            except Exception as exc:
                self._log(f"pkill tcpdump failed: {exc}")
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if remote_path and local_path:
            try:
                self._run_adb(["-s", self.device_id, "pull", remote_path, local_path], timeout=60)
                self._run_adb(["-s", self.device_id, "shell", "rm", "-f", remote_path], timeout=5)
                self._current_pcap_size = Path(local_path).stat().st_size if Path(local_path).exists() else 0
                self._log(f"pcap pulled: {local_path} ({self._current_pcap_size} bytes)")
                if queue_sync and self._current_pcap_size > 0:
                    self._start_runtime_packet_sync_thread(local_path)
            except Exception as exc:
                self._log(f"pcap pull failed: {exc}")
        self._tcpdump_started_at = ""
        self._last_remote_pcap_size = 0
        self._last_remote_pcap_size_seen_at = 0.0

    def _start_runtime_packet_sync_thread(self, local_path: str) -> None:
        thread = threading.Thread(
            target=self._decode_and_sync_runtime_packets,
            args=(local_path,),
            name="fanxiu-runtime-packet-sync",
            daemon=True,
        )
        thread.start()
        self._log(f"runtime packet sync queued: {local_path}")

    def _decode_and_sync_runtime_packets(self, local_path: str) -> None:
        try:
            from backend.core.fanxiu_packet_insight_worker import sync_fanxiu_capture_paths

            result = sync_fanxiu_capture_paths([local_path], max_streams=4)
            mail_sync = result.get("mail_packet_sync") or {}
            self._log(
                "runtime packet sync done: "
                f"decoded={result.get('decoded_count') or 0}, "
                f"skipped={result.get('skipped_count') or 0}, "
                f"errors={result.get('error_count') or 0}, "
                f"mail_records={mail_sync.get('record_count') or 0}, "
                f"mail_inserted={mail_sync.get('inserted') or 0}, "
                f"mail_updated={mail_sync.get('updated') or 0}"
            )
        except Exception as exc:
            self._log(f"runtime packet sync failed: {exc}")

    def _force_stop_locked(self, *, clear_reasons: bool, state: str) -> None:
        self._stop_event.set()
        if clear_reasons:
            self._active_reasons.clear()
        if self._tcpdump_process_alive_locked():
            self._stop_tcpdump_locked()
        self._state = state
        self._started_at = ""
        self._game_running = False

    def _mark_error_locked(self, exc: Exception) -> None:
        self._state = "recovering"
        self._last_error = str(exc)
        self._last_recover_at = _now_label()
        if self._tcpdump_process and self._tcpdump_process.poll() is not None:
            self._tcpdump_process = None
        self._log(f"recovering: {self._last_error}")

    def _tcpdump_process_alive_locked(self) -> bool:
        return bool(self._tcpdump_process and self._tcpdump_process.poll() is None)

    def _finalize_idle_capture_locked(self) -> None:
        remote_path = self._current_remote_pcap_path
        if not remote_path:
            return
        size = self._remote_capture_size(remote_path)
        now = time.monotonic()
        if size != self._last_remote_pcap_size:
            self._last_remote_pcap_size = size
            self._last_remote_pcap_size_seen_at = now
            return
        if size <= 24 or now - self._last_remote_pcap_size_seen_at < self.idle_finalize_seconds:
            return
        self._log(f"capture idle; finalizing current pcap ({size} bytes)")
        self._stop_tcpdump_locked()
        self._start_tcpdump_locked()

    def _remote_capture_size(self, remote_path: str) -> int:
        output = self._run_adb(
            [
                "-s",
                self.device_id,
                "shell",
                "sh",
                "-c",
                f"stat -c %s {remote_path} 2>/dev/null || wc -c < {remote_path} 2>/dev/null || echo 0",
            ],
            timeout=5,
        )
        for token in output.split():
            try:
                return int(token)
            except ValueError:
                continue
        return 0

    def _refresh_current_pcap_size_locked(self) -> None:
        path = Path(self._current_pcap_path) if self._current_pcap_path else None
        if path and path.exists():
            self._current_pcap_size = path.stat().st_size

    def _adb_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        env_path = os.environ.get("FANXIU_ADB_PATH")
        if env_path:
            candidates.append(Path(env_path))
        path_adb = shutil.which("adb")
        if path_adb:
            candidates.append(Path(path_adb))
        candidates.extend(Path(item) for item in DEFAULT_ADB_CANDIDATES)
        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _adb_path(self) -> Path:
        for candidate in self._adb_candidates():
            if candidate.exists() and candidate.is_file():
                return candidate
        raise RuntimeError("找不到 adb.exe。可设置 FANXIU_ADB_PATH 指向 MuMu/TapTap 的 adb.exe。")

    def _run_adb(self, args: list[str], timeout: float = 8) -> str:
        process = subprocess.run(
            [str(self._adb_path()), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = _completed_text(process)
        if process.returncode != 0:
            raise RuntimeError(output or f"adb 命令退出码 {process.returncode}")
        return output

    def _normalize_reason(self, reason: str) -> str:
        value = str(reason or "").strip()
        return value or "manual"

    def _compact_output(self, output: str) -> str:
        text = " ".join(str(output or "").split())
        return text[:240]

    def _log(self, message: str) -> None:
        self._logs.append(f"{_now_label()} {message}")


fanxiu_capture_runtime_service = FanxiuCaptureRuntimeService()
