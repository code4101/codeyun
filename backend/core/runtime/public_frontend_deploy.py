from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import posixpath
import shlex
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from backend.core.runtime.subprocess_utils import hidden_subprocess_kwargs, node_script_command
from backend.core.settings import ROOT_DIR, get_settings


PUBLIC_FRONTEND_DEPLOY_TASK_KEY = "public_frontend_deploy"
PUBLIC_FRONTEND_DEPLOY_TITLE = "公网前端发布"

FRONTEND_INCLUDE_ROOTS = (
    "src",
    "attendance-feedback",
    "public",
)
FRONTEND_INCLUDE_FILES = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
)
FRONTEND_EXCLUDED_DIRS = {
    ".codeyun-state",
    ".vite",
    "dist",
    "node_modules",
}
FRONTEND_EXCLUDED_FILES = {
    "src/components.d.ts",
    "src/auto-imports.d.ts",
    "components.d.ts",
    "auto-imports.d.ts",
}


class PublicFrontendDeployError(RuntimeError):
    pass


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(ROOT_DIR / ".env")


def _state_path() -> Path:
    return (get_settings().data_dir / "deploy" / "public_frontend_deploy_state.json").resolve(strict=False)


def _log_path() -> Path:
    return (get_settings().data_dir / "logs" / "public-frontend-deploy.log").resolve(strict=False)


def _frontend_dir() -> Path:
    return (ROOT_DIR / "frontend").resolve(strict=False)


def _dist_dir() -> Path:
    return (_frontend_dir() / "dist").resolve(strict=False)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _now_text() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _iter_frontend_inputs() -> list[Path]:
    frontend_dir = _frontend_dir()
    files: list[Path] = []
    for relative_root in FRONTEND_INCLUDE_ROOTS:
        root = frontend_dir / relative_root
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(frontend_dir).parts
            relative_path = path.relative_to(frontend_dir).as_posix()
            if any(part in FRONTEND_EXCLUDED_DIRS for part in relative_parts):
                continue
            if relative_path in FRONTEND_EXCLUDED_FILES:
                continue
            files.append(path)
    for relative_file in FRONTEND_INCLUDE_FILES:
        path = frontend_dir / relative_file
        if path.is_file():
            files.append(path)
    return sorted(set(files), key=lambda item: item.relative_to(frontend_dir).as_posix())


def compute_frontend_fingerprint() -> dict[str, Any]:
    frontend_dir = _frontend_dir()
    digest = hashlib.sha256()
    file_count = 0
    total_size = 0
    newest_mtime = 0.0
    for path in _iter_frontend_inputs():
        stat = path.stat()
        relative = path.relative_to(frontend_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\n")
        file_count += 1
        total_size += int(stat.st_size)
        newest_mtime = max(newest_mtime, float(stat.st_mtime))
    return {
        "fingerprint": digest.hexdigest(),
        "file_count": file_count,
        "total_size": total_size,
        "newest_mtime": newest_mtime,
        "newest_mtime_text": (
            dt.datetime.fromtimestamp(newest_mtime).replace(microsecond=0).isoformat(sep=" ")
            if newest_mtime
            else ""
        ),
    }


def _append_log(title: str, lines: list[str] | None = None) -> None:
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as file:
        file.write(f"\n[{_now_text()}] {title}\n")
        for line in lines or []:
            file.write(f"{line}\n")


def _build_frontend(timeout_seconds: float = 300.0) -> None:
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    vite_entry = _frontend_dir() / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_entry.is_file():
        raise PublicFrontendDeployError(f"前端构建失败：缺少 Vite 入口 {vite_entry}，请先安装前端依赖。")
    command = node_script_command(vite_entry, "build", "--manifest")
    with log_path.open("ab") as log_file:
        log_file.write(f"\n[{_now_text()}] node vite.js build --manifest\n".encode("utf-8"))
        log_file.flush()
        try:
            result = subprocess.run(
                command,
                cwd=os.fspath(_frontend_dir()),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                shell=False,
                timeout=max(1.0, float(timeout_seconds)),
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PublicFrontendDeployError(f"前端构建失败：{exc}") from exc
    if result.returncode != 0:
        raise PublicFrontendDeployError(f"前端构建失败，退出码 {result.returncode}，请查看日志：{log_path}")


def _create_dist_zip(release_id: str) -> Path:
    dist_dir = _dist_dir()
    if not (dist_dir / "index.html").is_file():
        raise PublicFrontendDeployError("前端构建产物缺少 index.html，取消上传。")
    assets_dir = dist_dir / "assets"
    if not assets_dir.is_dir() or not any(assets_dir.iterdir()):
        raise PublicFrontendDeployError("前端构建产物缺少 assets，取消上传。")
    zip_path = Path(tempfile.gettempdir()) / f"codeyun-frontend-dist-{release_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in dist_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(dist_dir).as_posix())
    return zip_path


def _ssh_config() -> dict[str, Any]:
    _load_env_file()
    host = os.getenv("YUN_SERVER_HOST")
    port = int(os.getenv("YUN_SERVER_PORT") or "22")
    username = os.getenv("YUN_USER_CHENKUNZE") or os.getenv("YUN_SERVER_USER") or "chenkunze"
    password = os.getenv("YUN_USER_PASS_CHENKUNZE") or os.getenv("YUN_SERVER_PASS")
    if not host or not password:
        raise PublicFrontendDeployError("缺少 yun 服务器环境变量，无法上传前端 dist。")
    return {"host": host, "port": port, "username": username, "password": password}


def _run_ssh(client: Any, command: str, *, sudo: bool = False, password: str = "", timeout: float = 120.0) -> tuple[str, str]:
    full_command = command
    if sudo:
        full_command = "sudo -S -p '' bash -lc " + shlex.quote(command)
    stdin, stdout, stderr = client.exec_command(full_command, timeout=timeout)
    if sudo:
        stdin.write(password + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise PublicFrontendDeployError(f"远端命令失败：{command}\n{err or out}".strip())
    return out, err


def _deploy_zip_to_yun(zip_path: Path, release_id: str, keep_releases: int = 8) -> str:
    try:
        import paramiko
    except Exception as exc:
        raise PublicFrontendDeployError(f"缺少 paramiko，无法上传到 yun：{exc}") from exc

    config = _ssh_config()
    remote_base = "/var/www/codeyun-frontend"
    remote_release = posixpath.join(remote_base, "releases", release_id)
    remote_zip = f"/tmp/codeyun-frontend-dist-{release_id}.zip"
    username = str(config["username"])
    password = str(config["password"])

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            str(config["host"]),
            port=int(config["port"]),
            username=username,
            password=password,
            timeout=15,
        )
        _run_ssh(
            client,
            f"mkdir -p {shlex.quote(remote_base + '/releases')} && chown -R {shlex.quote(username)}:{shlex.quote(username)} {shlex.quote(remote_base)}",
            sudo=True,
            password=password,
        )
        sftp = client.open_sftp()
        try:
            sftp.put(os.fspath(zip_path), remote_zip)
        finally:
            sftp.close()
        extract = "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])"
        _run_ssh(
            client,
            " && ".join(
                [
                    f"rm -rf {shlex.quote(remote_release)}",
                    f"mkdir -p {shlex.quote(remote_release)}",
                    f"python3 -c {shlex.quote(extract)} {shlex.quote(remote_zip)} {shlex.quote(remote_release)}",
                    f"test -f {shlex.quote(posixpath.join(remote_release, 'index.html'))}",
                    f"test -d {shlex.quote(posixpath.join(remote_release, 'assets'))}",
                    f"rm -f {shlex.quote(remote_zip)}",
                    f"ln -sfn {shlex.quote(remote_release)} {shlex.quote(remote_base + '/current.tmp')}",
                    f"mv -Tf {shlex.quote(remote_base + '/current.tmp')} {shlex.quote(remote_base + '/current')}",
                ]
            ),
            timeout=180,
        )
        _run_ssh(
            client,
            (
                f"cd {shlex.quote(remote_base + '/releases')} && "
                f"ls -1dt */ 2>/dev/null | tail -n +{max(2, int(keep_releases) + 1)} | xargs -r rm -rf"
            ),
            timeout=60,
        )
    finally:
        client.close()
    return remote_release


def run_public_frontend_deploy_check(*, force: bool = False) -> dict[str, Any]:
    state_path = _state_path()
    state = _read_json(state_path)
    fingerprint = compute_frontend_fingerprint()
    current = str(fingerprint["fingerprint"])
    deployed = str(state.get("deployed_fingerprint") or "")
    if not force and current and current == deployed:
        message = "前端源文件无变化，跳过构建和上传。"
        _append_log(message, [f"fingerprint={current}", f"files={fingerprint['file_count']}"])
        state.update({
            "last_checked_at": _now_text(),
            "last_result": "skipped",
            "last_message": message,
            "last_fingerprint": current,
            "last_scan": fingerprint,
        })
        _write_json(state_path, state)
        print(message)
        return {"status": "skipped", "message": message, "fingerprint": current}

    release_id = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    state.update({
        "last_checked_at": _now_text(),
        "last_result": "building",
        "last_message": "检测到前端变化，开始构建。",
        "last_fingerprint": current,
        "last_scan": fingerprint,
    })
    _write_json(state_path, state)
    _append_log("检测到前端变化，开始构建。", [f"release={release_id}", f"fingerprint={current}"])

    zip_path: Path | None = None
    try:
        _build_frontend()
        zip_path = _create_dist_zip(release_id)
        remote_release = _deploy_zip_to_yun(zip_path, release_id)
    except Exception as exc:
        message = str(exc)
        state.update({
            "last_finished_at": _now_text(),
            "last_result": "failed",
            "last_error": message,
            "last_failed_fingerprint": current,
        })
        _write_json(state_path, state)
        _append_log("公网前端发布失败", [message])
        raise
    finally:
        if zip_path is not None:
            try:
                zip_path.unlink()
            except FileNotFoundError:
                pass

    message = f"公网前端发布成功：{remote_release}"
    state.update({
        "deployed_at": _now_text(),
        "deployed_fingerprint": current,
        "deployed_release": remote_release,
        "last_finished_at": _now_text(),
        "last_result": "deployed",
        "last_message": message,
        "last_error": "",
    })
    _write_json(state_path, state)
    _append_log("公网前端发布成功", [f"release={remote_release}", f"fingerprint={current}"])
    print(message)
    return {
        "status": "deployed",
        "message": message,
        "fingerprint": current,
        "release": remote_release,
    }


def build_public_frontend_deploy_log_lines(limit: int = 120) -> list[str]:
    state = _read_json(_state_path())
    fingerprint = compute_frontend_fingerprint()
    lines = [
        f"名称：{PUBLIC_FRONTEND_DEPLOY_TITLE}",
        f"状态：{state.get('last_result') or '未运行'}",
        f"当前指纹：{fingerprint.get('fingerprint') or '-'}",
        f"已发布指纹：{state.get('deployed_fingerprint') or '-'}",
        f"已发布版本：{state.get('deployed_release') or '-'}",
        f"上次检查：{state.get('last_checked_at') or '-'}",
        f"上次完成：{state.get('last_finished_at') or state.get('deployed_at') or '-'}",
        f"文件数：{fingerprint.get('file_count')}",
        f"日志：{_log_path()}",
    ]
    if state.get("last_message"):
        lines.append(f"消息：{state.get('last_message')}")
    if state.get("last_error"):
        lines.extend(["", f"最近错误：{state.get('last_error')}"])
    log_path = _log_path()
    if log_path.is_file():
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)) :]
        except OSError:
            tail = []
        if tail:
            lines.extend(["", "最近日志：", *tail])
    return lines
