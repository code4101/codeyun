from __future__ import annotations

import ast
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"
DEPLOY_DIR = ROOT_DIR / "deploy"
STARTUP_TIMEOUT_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 2
ASSET_PATH_RE = re.compile(r"""(?:src|href)=["']([^"']*assets/[^"']+)["']""")
IMPORT_SPEC_RE = re.compile(
    r"""(?:import|export)\s+(?:[^'"]+?\s+from\s+)?["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)"""
)
FRONTEND_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".vue"}
FRONTEND_MODULE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".vue", ".json")
PYTHON_LOCAL_PREFIXES = {"backend", "scripts", "tests"}


@dataclass(frozen=True)
class CheckIssue:
    category: str
    file: Path
    detail: str


def resolve_npm() -> str:
    if os.name != "nt":
        return shutil.which("npm") or "npm"

    return shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"


def run_command(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f">>> Running: {' '.join(command)}")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_process(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    log_prefix: str,
) -> tuple[subprocess.Popen, Path]:
    fd, log_path = tempfile.mkstemp(prefix=log_prefix, suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as log_file:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creationflags,
        )
    return process, Path(log_path)


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def fetch(url: str) -> tuple[int | None, str]:
    request = Request(url, headers={"User-Agent": "codeyun-prod-check"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.getcode(), body
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body
    except URLError:
        return None, ""


def tail_log(log_path: Path, max_lines: int = 40) -> str:
    if not log_path.exists():
        return "(log file not found)"

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(log file is empty)"
    return "\n".join(lines[-max_lines:])


def wait_for_status(
    url: str,
    expected_statuses: set[int],
    process: subprocess.Popen,
    log_path: Path,
    label: str,
) -> str:
    deadline = time.time() + STARTUP_TIMEOUT_SECONDS
    while time.time() < deadline:
        status, body = fetch(url)
        if status in expected_statuses:
            return body
        if process.poll() is not None:
            break
        time.sleep(1)

    raise RuntimeError(
        f"{label} did not become ready.\n"
        f"Expected statuses: {sorted(expected_statuses)}\n"
        f"URL: {url}\n"
        f"Log tail:\n{tail_log(log_path)}"
    )


def assert_status(url: str, expected_status: int, label: str) -> str:
    status, body = fetch(url)
    if status != expected_status:
        raise RuntimeError(f"{label} returned {status}, expected {expected_status}: {url}")
    return body


@lru_cache(maxsize=None)
def _dir_entries(directory: Path) -> dict[str, Path]:
    return {child.name: child for child in directory.iterdir()}


def _follow_repo_path(relative_path: Path) -> tuple[Path | None, list[tuple[str, str]]]:
    current = ROOT_DIR
    mismatches: list[tuple[str, str]] = []

    for part in relative_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
            continue

        if not current.exists() or not current.is_dir():
            return None, mismatches

        entries = _dir_entries(current)
        exact = entries.get(part)
        if exact is not None:
            current = exact
            continue

        lower_matches = [child for name, child in entries.items() if name.lower() == part.lower()]
        if len(lower_matches) != 1:
            return None, mismatches

        current = lower_matches[0]
        mismatches.append((part, current.name))

    return current, mismatches


def _normalize_relative_path(path: Path) -> Path | None:
    normalized = Path(os.path.normpath(str(path)))
    if normalized.is_absolute():
        try:
            normalized = normalized.relative_to(ROOT_DIR)
        except ValueError:
            return None

    if normalized.parts and normalized.parts[0] == "..":
        return None
    return normalized


def _module_reference_candidates(target: Path, suffixes: tuple[str, ...]) -> list[Path]:
    normalized = _normalize_relative_path(target)
    if normalized is None:
        return []

    if normalized.suffix:
        return [normalized]

    candidates = [normalized]
    candidates.extend(normalized.with_suffix(suffix) for suffix in suffixes)
    candidates.extend(normalized / f"index{suffix}" for suffix in suffixes)
    return candidates


def _python_module_candidates(module_path: Path) -> list[Path]:
    normalized = _normalize_relative_path(module_path)
    if normalized is None:
        return []

    return [
        normalized,
        normalized.with_suffix(".py"),
        normalized / "__init__.py",
    ]


def _validate_candidates(
    source_file: Path,
    display_spec: str,
    candidates: list[Path],
    category: str,
) -> list[CheckIssue]:
    resolved, mismatches = _resolve_candidates(candidates)
    if resolved is not None:
        if mismatches:
            return [
                CheckIssue(
                    category=category,
                    file=source_file,
                    detail=f"{display_spec} uses wrong case; expected filesystem path {resolved.relative_to(ROOT_DIR).as_posix()}",
                )
            ]
        return []

    return [
        CheckIssue(
            category=category,
            file=source_file,
            detail=f"{display_spec} could not be resolved under the repository root",
        )
    ]


def _resolve_candidates(candidates: list[Path]) -> tuple[Path | None, list[tuple[str, str]]]:
    for candidate in candidates:
        resolved, mismatches = _follow_repo_path(candidate)
        if resolved is None:
            continue
        return resolved, mismatches
    return None, []


def _extract_import_specs(text: str) -> list[str]:
    specs: list[str] = []
    for match in IMPORT_SPEC_RE.finditer(text):
        spec = match.group(1) or match.group(2)
        if spec:
            specs.append(spec)
    return specs


def _frontend_target_path(source_file: Path, spec: str) -> Path | None:
    source_rel = source_file.relative_to(ROOT_DIR)
    if spec.startswith("@/"):
        return Path("frontend", "src") / Path(spec[2:])
    if spec.startswith(("./", "../")):
        return source_rel.parent / Path(spec)
    return None


def check_frontend_case_sensitive_imports() -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    seen: set[tuple[Path, str]] = set()

    frontend_sources = list((FRONTEND_DIR / "src").rglob("*"))
    frontend_sources.append(FRONTEND_DIR / "vite.config.ts")

    for source_file in frontend_sources:
        if not source_file.is_file() or source_file.suffix not in FRONTEND_SOURCE_SUFFIXES:
            continue

        text = source_file.read_text(encoding="utf-8", errors="replace")
        for spec in _extract_import_specs(text):
            target = _frontend_target_path(source_file, spec)
            if target is None:
                continue

            key = (source_file, spec)
            if key in seen:
                continue
            seen.add(key)

            issues.extend(
                _validate_candidates(
                    source_file=source_file,
                    display_spec=f"Frontend import '{spec}'",
                    candidates=_module_reference_candidates(target, FRONTEND_MODULE_SUFFIXES),
                    category="frontend-import",
                )
            )

    return issues


def _current_python_package(source_file: Path) -> tuple[str, ...]:
    source_rel = source_file.relative_to(ROOT_DIR)
    if source_rel.name == "__init__.py":
        return source_rel.parent.parts
    return source_rel.with_suffix("").parts[:-1]


def _validate_python_module(
    source_file: Path,
    module_path: Path,
    display_spec: str,
) -> list[CheckIssue]:
    return _validate_candidates(
        source_file=source_file,
        display_spec=display_spec,
        candidates=_python_module_candidates(module_path),
        category="python-import",
    )


def check_python_case_sensitive_imports() -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    python_roots = [ROOT_DIR / "backend", ROOT_DIR / "scripts", ROOT_DIR / "tests"]

    for root in python_roots:
        for source_file in root.rglob("*.py"):
            if "__pycache__" in source_file.parts:
                continue

            tree = ast.parse(source_file.read_text(encoding="utf-8", errors="replace"), filename=str(source_file))
            current_package = _current_python_package(source_file)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        prefix = alias.name.split(".", 1)[0]
                        if prefix not in PYTHON_LOCAL_PREFIXES:
                            continue
                        issues.extend(
                            _validate_python_module(
                                source_file,
                                Path(*alias.name.split(".")),
                                f"Python import '{alias.name}'",
                            )
                        )

                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        if not node.module:
                            continue
                        prefix = node.module.split(".", 1)[0]
                        if prefix not in PYTHON_LOCAL_PREFIXES:
                            continue
                        module_parts = tuple(node.module.split("."))
                    else:
                        if node.level - 1 > len(current_package):
                            continue
                        base_parts = current_package[: len(current_package) - (node.level - 1)]
                        module_parts = base_parts + tuple(node.module.split(".")) if node.module else base_parts

                    module_path = Path(*module_parts) if module_parts else Path()
                    display_spec = (
                        f"Python import '{'.' * node.level}{node.module or ''}'"
                        if node.level
                        else f"Python import '{node.module}'"
                    )
                    issues.extend(_validate_python_module(source_file, module_path, display_spec))

                    for alias in node.names:
                        if alias.name == "*" or not module_parts:
                            continue
                        submodule_path = Path(*module_parts, alias.name)
                        resolved, _ = _resolve_candidates(_python_module_candidates(submodule_path))
                        if resolved is None:
                            continue
                        issues.extend(
                            _validate_python_module(
                                source_file,
                                submodule_path,
                                f"{display_spec} -> {alias.name}",
                            )
                        )

    return issues


def check_deploy_line_endings() -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    for path in DEPLOY_DIR.rglob("*"):
        if not path.is_file() or path.suffix not in {".sh", ".conf", ".service"}:
            continue
        if b"\r\n" in path.read_bytes():
            issues.append(
                CheckIssue(
                    category="deploy-eol",
                    file=path,
                    detail="Deploy file uses CRLF; Ubuntu shell/systemd/nginx files should stay LF",
                )
            )
    return issues


def check_gitattributes() -> list[CheckIssue]:
    path = ROOT_DIR / ".gitattributes"
    if not path.exists():
        return [
            CheckIssue(
                category="deploy-eol",
                file=path,
                detail="Missing .gitattributes; enforce LF for deploy files to avoid /bin/bash^M on Ubuntu",
            )
        ]

    text = path.read_text(encoding="utf-8", errors="replace")
    required_rules = {
        "*.sh text eol=lf",
        "*.service text eol=lf",
        "*.conf text eol=lf",
    }
    missing = sorted(rule for rule in required_rules if rule not in text)
    if not missing:
        return []

    return [
        CheckIssue(
            category="deploy-eol",
            file=path,
            detail=f"Missing LF rules: {', '.join(missing)}",
        )
    ]


def check_ubuntu_deploy_templates() -> list[CheckIssue]:
    issues: list[CheckIssue] = []

    nginx_path = DEPLOY_DIR / "nginx" / "codeyun.conf"
    nginx_text = nginx_path.read_text(encoding="utf-8", errors="replace")
    if "__CODEYUN_PROJECT_DIR__/frontend/dist" not in nginx_text:
        issues.append(
            CheckIssue(
                category="deploy-template",
                file=nginx_path,
                detail="Nginx template should use __CODEYUN_PROJECT_DIR__ placeholder for the frontend dist root",
            )
        )

    setup_path = DEPLOY_DIR / "setup_server.sh"
    setup_text = setup_path.read_text(encoding="utf-8", errors="replace")
    required_snippets = {
        'ROOT_ENV_FILE="$PROJECT_DIR/.env"': "setup_server.sh should create/read the root .env file",
        "__CODEYUN_PROJECT_DIR__": "setup_server.sh should replace the Nginx project-dir placeholder",
    }
    for snippet, detail in required_snippets.items():
        if snippet not in setup_text:
            issues.append(CheckIssue(category="deploy-template", file=setup_path, detail=detail))

    return issues


def run_ubuntu_compatibility_checks() -> None:
    issues = []
    issues.extend(check_gitattributes())
    issues.extend(check_deploy_line_endings())
    issues.extend(check_ubuntu_deploy_templates())
    issues.extend(check_python_case_sensitive_imports())
    issues.extend(check_frontend_case_sensitive_imports())

    deduped: list[CheckIssue] = []
    seen: set[tuple[str, Path, str]] = set()
    for issue in issues:
        key = (issue.category, issue.file, issue.detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)

    if not deduped:
        print(">>> Ubuntu deployment compatibility checks passed")
        return

    lines = ["Ubuntu deployment compatibility checks failed:"]
    for issue in deduped:
        rel_path = issue.file.relative_to(ROOT_DIR) if issue.file.exists() else issue.file.name
        lines.append(f"- [{issue.category}] {rel_path}: {issue.detail}")
    raise RuntimeError("\n".join(lines))


def check_backend_production() -> None:
    port = allocate_port()
    env = os.environ.copy()
    env["CODEYUN_ENV"] = "production"
    env["CODEYUN_BACKEND_HOST"] = "127.0.0.1"
    env["CODEYUN_BACKEND_PORT"] = str(port)
    env["PYTHONPATH"] = str(ROOT_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    process, log_path = start_process(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT_DIR,
        env=env,
        log_prefix="codeyun-backend-prod-",
    )

    try:
        root_url = f"http://127.0.0.1:{port}/"
        wait_for_status(root_url, {200}, process, log_path, "Backend root")
        assert_status(root_url, 200, "Backend root")
        assert_status(f"http://127.0.0.1:{port}/docs", 404, "Production docs endpoint")
        assert_status(f"http://127.0.0.1:{port}/openapi.json", 404, "Production openapi endpoint")
        print(f">>> Backend production smoke passed on port {port}")
    finally:
        stop_process(process)


def check_frontend_preview(npm_exec: str) -> None:
    port = allocate_port()
    env = os.environ.copy()

    process, log_path = start_process(
        [npm_exec, "run", "preview", "--", "--host", "127.0.0.1", "--port", str(port)],
        cwd=FRONTEND_DIR,
        env=env,
        log_prefix="codeyun-frontend-preview-",
    )

    try:
        index_url = f"http://127.0.0.1:{port}/"
        html = wait_for_status(index_url, {200}, process, log_path, "Frontend preview")

        asset_paths = []
        for match in ASSET_PATH_RE.findall(html):
            if match not in asset_paths:
                asset_paths.append(match)

        if not asset_paths:
            raise RuntimeError("Frontend preview HTML did not reference built assets.")

        for asset_path in asset_paths[:3]:
            asset_url = urljoin(index_url, asset_path)
            assert_status(asset_url, 200, f"Preview asset {asset_path}")

        print(f">>> Frontend production preview passed on port {port}")
    finally:
        stop_process(process)


def main() -> None:
    npm_exec = resolve_npm()

    print(">>> Running Ubuntu deployment compatibility checks")
    run_ubuntu_compatibility_checks()

    print(">>> Running frontend production checks")
    run_command([npm_exec, "run", "check"], cwd=FRONTEND_DIR)

    print(">>> Running backend production smoke test")
    check_backend_production()

    print(">>> Running frontend preview smoke test")
    check_frontend_preview(npm_exec)

    print(">>> Production-oriented checks passed")


if __name__ == "__main__":
    main()
