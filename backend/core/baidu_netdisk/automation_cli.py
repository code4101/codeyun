"""Command line entry for :class:`~.automation.NetdiskAutomation`.

    uv run python -m backend.core.baidu_netdisk.automation_cli [--config FILE] COMMAND ...

Commands map one-to-one onto public service contracts:

* ``config-show``: the loaded config and its derived paths (no service, no GUI).
* ``inspect`` / ``reconcile`` / ``verify``: read-only, filesystem only.
* ``recover``: the one explicit filesystem recovery (never GUI, never resubmits).
* ``list`` / ``resolve``: read-only remote listing in the provider's own browser tab.
* ``observe``: GUI observation under the native lease (no input).
* ``action``: one capability-bound shape action under the native lease.
* ``download``: one file; operation id, remote path, resource id, size and
  destination are explicit, SHA-256/version optional.  Called once, never retried;
  a repeated operation id is only reconciled by the service.
* ``download-dir``: one folder; operation id, remote path, resource id,
  destination and the trusted ``--manifest`` JSON are required, version optional.  Called once,
  never retried; a repeated operation id is only reconciled by the service.
* ``upload``: one file into an existing remote directory; operation id, source
  and remote directory are explicit.  Called once, never retried; a repeated
  operation id or a live native key is only reconciled by the service.
* ``inspect-upload`` / ``reconcile-upload``: stored upload intent / read-only
  remote readback (never GUI, never resubmits).  Upload results carry codes,
  keys and remote facts only, never the local path or source hash.
* ``ensure-directory``: one remote directory below a proven anchor (operation
  id, path and anchor path explicit; anchor id required by the provider unless
  the anchor is the root).  Mutates at most once; a repeated id only reads back.
  Succeeds only on ``completed``.
* ``inspect-directory`` / ``reconcile-directory``: stored record (success on
  ``found``) / read-only remote readback (success on ``completed``, never resubmits).
* ``maintenance-list`` / ``maintenance-inspect CASE_ID``: the sanitized
  repeated-failure queue every :class:`~.automation.AutomationFailure` feeds.
* ``maintenance-dispatch CASE_ID --workspace WORKTREE``: the only command that
  may reach the CC dispatcher, once per case at threshold; never implicit.

Stdout is exactly one JSON object ``{"command", "ok", "result"|"error"}``.  Exit
codes: 0 success, 1 completed without the success status (needs review, not
found, unverified...), 2 usage or input error, 3 config error, 4 sanitized
:class:`~.automation.AutomationFailure` (receipt in ``error``), 5 unexpected
error (type only).  The config is read only when a command runs; ``--help``
never bootstraps the runtime env, the service or the GUI.  Only
``maintenance-dispatch`` may start a model task; directory upload is not exposed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from .automation_config import AutomationConfigError, load_automation_config

EXIT_OK, EXIT_NOT_OK, EXIT_USAGE, EXIT_CONFIG, EXIT_FAILURE, EXIT_ERROR = 0, 1, 2, 3, 4, 5
OWNER = "baidu-netdisk-automation-cli"
_VERIFIED_LOCAL = {"content_verified", "structure_verified", "local_hash_recorded"}
_QUEUE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _InputError(ValueError):
    """Bad CLI input found after argparse (e.g. an unreadable manifest)."""


def _open_service(config):
    """Lazily import the service so ``--help`` and ``config-show`` never load GUI providers."""
    from .automation import NetdiskAutomation
    return NetdiskAutomation(config)


def _is_automation_failure(exc: BaseException) -> bool:
    """Checked without importing: a failure can only come from an already imported service."""
    module = sys.modules.get(f"{__package__}.automation")
    return module is not None and isinstance(exc, module.AutomationFailure)


def _read_manifest(path: str | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _InputError(f"manifest unreadable: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise _InputError("manifest must be a JSON object")
    return data


def _status_is(*ok: str):
    return lambda result: isinstance(result, dict) and result.get("status") in ok


# -- commands: each returns (result, success) ---------------------------------------


def _config_show(args, config):
    paths = config.paths
    return {**config.to_json(), "paths": {name: str(getattr(paths, name)) for name in
                                          ("assets_dir", "transfers_file", "operations_dir", "failures_dir",
                                           "config_file")}}, True


def _inspect(args, config):
    found = _open_service(config).inspect(args.operation_id)
    if found is None:
        return {"operation_id": args.operation_id, "found": False}, False
    return found, True


def _reconcile(args, config):
    result = _open_service(config).reconcile(args.operation_id)
    return result, _status_is("verified")(result)


def _recover(args, config):
    result = _open_service(config).recover(args.operation_id)
    return result, _status_is("verified")(result)


def _verify(args, config):
    manifest = _read_manifest(args.manifest)
    result = _open_service(config).verify(args.destination, manifest)
    return result, _status_is(*_VERIFIED_LOCAL)(result)


def _list(args, config):
    result = _open_service(config).list_directory(args.path, args.expected_id)
    return result, _status_is("listed")(result)


def _resolve(args, config):
    want_dir = {"dir": True, "file": False, "any": None}[args.type]
    result = _open_service(config).resolve(args.path, args.expected_id, want_dir)
    return result, _status_is("resolved")(result)


def _observe(args, config):
    with _open_service(config).native(args.owner, lease_token=args.lease_token) as ctx:
        return ctx.observe(args.role), True


def _action(args, config):
    with _open_service(config).native(args.owner, lease_token=args.lease_token) as ctx:
        result = ctx.action(args.scene_id, args.shape_id, args.expected, discovery=args.discovery)
    return result, _status_is("verified")(result)


def _download(args, config):
    result = _open_service(config).download(
        args.operation_id, remote_path=args.remote_path, resource_id=args.resource_id, expected_size=args.size,
        destination=args.destination, expected_sha256=args.sha256, resource_version=args.version,
        owner=args.owner, lease_token=args.lease_token)
    return result, _status_is("verified")(result)


def _retry_download(args, config):
    result = _open_service(config).retry_not_submitted(args.operation_id, owner=args.owner,
                                                      lease_token=args.lease_token)
    return result, _status_is("verified")(result)


def _download_dir(args, config):
    manifest = _read_manifest(args.manifest)
    result = _open_service(config).download_dir(
        args.operation_id, remote_path=args.remote_path, resource_id=args.resource_id, destination=args.destination,
        expected_manifest=manifest, resource_version=args.version, owner=args.owner, lease_token=args.lease_token)
    return result, _status_is("verified")(result)


def _upload(args, config):
    result = _open_service(config).upload(args.operation_id, args.source, args.remote_dir, owner=args.owner,
                                          lease_token=args.lease_token)
    return result, _status_is("remote_present")(result)


def _inspect_upload(args, config):
    found = _open_service(config).inspect_upload(args.operation_id)
    if found is None:
        return {"operation_id": args.operation_id, "found": False}, False
    return found, True


def _reconcile_upload(args, config):
    result = _open_service(config).reconcile_upload(args.operation_id)
    return result, _status_is("remote_present")(result)


def _ensure_directory(args, config):
    result = _open_service(config).ensure_directory(args.operation_id, args.path, args.anchor_path,
                                                    args.expected_anchor_id)
    return result, _status_is("completed")(result)


def _inspect_directory(args, config):
    result = _open_service(config).inspect_directory(args.operation_id)
    return result, _status_is("found")(result)


def _reconcile_directory(args, config):
    result = _open_service(config).reconcile_directory(args.operation_id)
    return result, _status_is("completed")(result)


@contextmanager
def _queue_input():
    """Queue refusals (bad/unknown case id, missing or non-worktree workspace) are typed input errors."""
    try:
        yield
    except (ValueError, KeyError, OSError) as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], str) and _QUEUE_CODE.match(exc.args[0]) else None
        raise _InputError(code or ("workspace_unavailable" if isinstance(exc, OSError) else type(exc).__name__)) \
            from None


def _maintenance_list(args, config):
    return _open_service(config).list_maintenance(), True


def _maintenance_inspect(args, config):
    with _queue_input():
        found = _open_service(config).inspect_maintenance(args.case_id)
    if found is None:
        return {"case_id": args.case_id, "found": False}, False
    return found, True


def _maintenance_dispatch(args, config):
    with _queue_input():
        result = _open_service(config).dispatch_maintenance(args.case_id, args.workspace)
    return result, isinstance(result, dict) and (result.get("dispatch") or {}).get("state") == "dispatched"


# -- parser ---------------------------------------------------------------------------------


def _native_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--owner", default=OWNER, help="lease owner label")
    p.add_argument("--lease-token", help="reuse an already held gui:desktop:local lease")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.core.baidu_netdisk.automation_cli",
                                     description="Bounded Baidu Netdisk automation (JSON on stdout)")
    parser.add_argument("--config", help="config.json path (default: <data_dir>/baidu-netdisk/automation)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("config-show", help="print the config and derived paths").set_defaults(run=_config_show)
    for name, run, text in (("inspect", _inspect, "stored operation record (read-only)"),
                            ("reconcile", _reconcile, "ledger/filesystem evidence (read-only)"),
                            ("recover", _recover, "move a proven finished download into place (no GUI)"),
                            ("inspect-upload", _inspect_upload, "stored upload intent (read-only)"),
                            ("reconcile-upload", _reconcile_upload, "upload ledger/remote readback (read-only)"),
                            ("inspect-directory", _inspect_directory, "stored directory-ensure record (read-only)"),
                            ("reconcile-directory", _reconcile_directory,
                             "directory-ensure remote readback (read-only, never resubmits)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("operation_id")
        p.set_defaults(run=run)

    p = sub.add_parser("verify", help="hash local_root/DESTINATION (read-only)")
    p.add_argument("destination")
    p.add_argument("--manifest", help="expected manifest JSON file {files:[{rel,size,sha256?}]}")
    p.set_defaults(run=_verify)

    p = sub.add_parser("list", help="complete remote directory listing (read-only)")
    p.add_argument("path")
    p.add_argument("--expected-id")
    p.set_defaults(run=_list)

    p = sub.add_parser("resolve", help="exact remote object at PATH (read-only)")
    p.add_argument("path")
    p.add_argument("--expected-id")
    p.add_argument("--type", choices=("any", "file", "dir"), default="any")
    p.set_defaults(run=_resolve)

    p = sub.add_parser("observe", help="observe a client window (no input)")
    p.add_argument("--role", default="main")
    _native_args(p)
    p.set_defaults(run=_observe)

    p = sub.add_parser("action", help="one capability-bound shape action")
    p.add_argument("scene_id")
    p.add_argument("shape_id")
    p.add_argument("--expected", action="append", metavar="SCENE_ID", help="expected scene after the action")
    p.add_argument("--discovery", action="store_true")
    _native_args(p)
    p.set_defaults(run=_action)

    p = sub.add_parser("download", help="download one file once (never retried)")
    p.add_argument("--operation-id", required=True)
    p.add_argument("--remote-path", required=True)
    p.add_argument("--resource-id", required=True)
    p.add_argument("--size", type=int, required=True, help="expected size in bytes")
    p.add_argument("--destination", required=True, help="directory relative to local_root")
    p.add_argument("--sha256", help="trusted SHA-256 (never an MD5)")
    p.add_argument("--version", help="trusted resource version")
    _native_args(p)
    p.set_defaults(run=_download)

    p = sub.add_parser("retry-not-submitted", help="same file/folder intent, only with proof of failure before submission")
    p.add_argument("operation_id")
    _native_args(p)
    p.set_defaults(run=_retry_download)

    p = sub.add_parser("download-dir", help="download one folder once against a trusted manifest (never retried)")
    p.add_argument("--operation-id", required=True)
    p.add_argument("--remote-path", required=True)
    p.add_argument("--resource-id", required=True)
    p.add_argument("--version", help="trusted resource version (listing version); omit when unavailable")
    p.add_argument("--destination", required=True, help="directory relative to local_root")
    p.add_argument("--manifest", required=True, help="trusted manifest JSON file {files:[{rel,size,sha256?}]}")
    _native_args(p)
    p.set_defaults(run=_download_dir)

    p = sub.add_parser("upload", help="upload one file once into an existing remote directory (never retried)")
    p.add_argument("--operation-id", required=True)
    p.add_argument("--source", required=True, help="absolute local file path")
    p.add_argument("--remote-dir", required=True, help="existing remote directory")
    _native_args(p)
    p.set_defaults(run=_upload)

    p = sub.add_parser("ensure-directory",
                       help="ensure one remote directory below a proven anchor, mutating at most once")
    p.add_argument("--operation-id", required=True)
    p.add_argument("--path", required=True, help="remote directory to ensure")
    p.add_argument("--anchor-path", required=True, help="existing remote ancestor directory")
    p.add_argument("--expected-anchor-id", help="anchor id (required by the provider unless the anchor is /)")
    p.set_defaults(run=_ensure_directory)

    sub.add_parser("maintenance-list", help="queued repeated-failure cases (read-only)").set_defaults(
        run=_maintenance_list)
    p = sub.add_parser("maintenance-inspect", help="one queued case and its dispatch status (read-only)")
    p.add_argument("case_id")
    p.set_defaults(run=_maintenance_inspect)
    p = sub.add_parser("maintenance-dispatch",
                       help="explicitly dispatch one case at threshold to CC once (never retried)")
    p.add_argument("case_id")
    p.add_argument("--workspace", required=True, help="separate Git worktree for the repair")
    p.set_defaults(run=_maintenance_dispatch)
    return parser


def _emit(payload: dict) -> None:
    # ASCII-only JSON: redirected Windows stdout defaults to the ANSI codepage (e.g. cp936), so raw
    # non-ASCII text would reach UTF-8 pipe consumers as undecodable bytes (or fail to encode at all).
    json.dump(payload, sys.stdout, ensure_ascii=True, indent=2, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # --help (0) or usage error (2); argparse already wrote to stdout/stderr
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    command = args.command
    try:
        config = load_automation_config(args.config)
        result, ok = args.run(args, config)
    except AutomationConfigError as exc:
        _emit({"command": command, "ok": False, "error": {"code": "config_invalid", "message": str(exc)}})
        return EXIT_CONFIG
    except _InputError as exc:
        _emit({"command": command, "ok": False, "error": {"code": "input_invalid", "message": str(exc)}})
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - provider text is never echoed
        if _is_automation_failure(exc):
            _emit({"command": command, "ok": False, "error": {"code": exc.code, "receipt": exc.receipt}})
            return EXIT_FAILURE
        _emit({"command": command, "ok": False, "error": {"code": "unexpected_error", "error_type": type(exc).__name__}})
        return EXIT_ERROR
    _emit({"command": command, "ok": ok, "result": result})
    return EXIT_OK if ok else EXIT_NOT_OK


if __name__ == "__main__":
    raise SystemExit(main())
