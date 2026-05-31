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
DEFAULT_FANXIU_DEVICE_ID = "127.0.0.1:7555"
DEFAULT_FANXIU_PACKAGE_NAME = "com.frxxcrjpwssc3.ggws"
DEFAULT_REMOTE_CAPTURE_DIR = "/data/local/tmp"


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
    ) -> None:
        self.device_id = device_id
        self.package_name = package_name
        self.supervisor_interval = supervisor_interval
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
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
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
                return
            if self._tcpdump_process is not None:
                self._log("tcpdump exited; finalizing current pcap")
                self._stop_tcpdump_locked()
            self._start_tcpdump_locked()

    def _connect_adb(self) -> None:
        output = self._run_adb(["connect", self.device_id], timeout=8)
        connected = (
            "connected" in output.lower()
            or "already connected" in output.lower()
            or self.device_id in self._run_adb(["devices"], timeout=8)
        )
        with self._lock:
            self._adb_connected = connected
            if connected:
                self._log(f"adb connect ok: {self._compact_output(output)}")
            else:
                raise RuntimeError(f"adb connect 未确认：{output}")

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

    def _stop_tcpdump_locked(self) -> None:
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
                if self._current_pcap_size > 0:
                    self._start_activity_packet_sync_thread(local_path)
            except Exception as exc:
                self._log(f"pcap pull failed: {exc}")
        self._tcpdump_started_at = ""

    def _start_activity_packet_sync_thread(self, local_path: str) -> None:
        thread = threading.Thread(
            target=self._decode_and_sync_activity_packets,
            args=(local_path,),
            name="fanxiu-activity-packet-sync",
            daemon=True,
        )
        thread.start()
        self._log(f"activity packet sync queued: {local_path}")

    def _decode_and_sync_activity_packets(self, local_path: str) -> None:
        try:
            from backend.core.fanxiu_activity_packet_sync import decode_and_sync_fanxiu_activity_capture

            result = decode_and_sync_fanxiu_activity_capture(local_path)
            sync = result.get("activity_packet_sync") or {}
            self._log(
                "activity packet sync done: "
                f"decoded={result.get('decoded_count') or 0}, "
                f"rank_packets={sync.get('matched_rank_packets') or 0}, "
                f"rank_records={sync.get('rank_record_count') or 0}"
            )
        except Exception as exc:
            self._log(f"activity packet sync failed: {exc}")

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
