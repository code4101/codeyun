"""In-flight ``downloading`` progress reports for SkillDep materials (collection convention).

Rule for every acquisition batch on this device (direct downloads, crawlers, runners,
external clients such as the netdisk app):

1. A material expected to take more than ~5 minutes or more than 100MB is reported while
   it downloads: every ``INTERVAL_SECONDS`` (10 min) or every ``STEP_BYTES`` (1GB) of new
   bytes, whichever comes first, one PUT with ``status=downloading``,
   ``availability=windows_local``, ``path`` = the material's directory on disk and
   ``size_bytes`` = bytes already on disk for it in total (earlier batches included).
   ``downloading`` carries no sha256/manifest.
2. The final record (``downloaded``/``partial`` with sha256 or manifest, or ``failed`` with
   ``error``) is registered as before and replaces the ``downloading`` record.
3. A material already ``downloaded`` for this device on CodeHub is never turned into
   ``downloading``; a ``partial`` being extended may be, with the cumulative size.

Denominator (CodeHub's 「大小」 column is downloaded/total). CodeHub picks the total by trust:
actual size once complete > exact ``size_bytes`` of a netdisk transfer record >
``total_bytes`` of a download record > ``size_label`` of a netdisk transfer > plan estimate.
So, as precisely as the source allows:

4. Every ``downloading``/``partial`` record carries ``total_bytes`` whenever the source states
   the whole item's byte count directly (sum of Content-Length over all of its files, a
   size field of the source's own listing/API). Never derive it (item count x average size,
   estimates); without a direct figure the field is left out.
5. A netdisk transfer (``PUT .../plan/materials/{id}/cloud-transfers/{account_id}``) carries
   the exact ``size_bytes`` (+ ``file_count``) of its cloud directory, summed recursively
   from the netdisk's own file listing or taken from the exact byte count in its properties;
   only when no exact value is obtainable, at least ``size_label`` (the size the netdisk UI
   shows, e.g. "约 1.2G"). ``cloud_transfer_size_update`` builds such a re-PUT from the stored
   record: other fields unchanged, ``expected_resource_version`` = its ``resource_version``.

``downloading`` is transient and goes straight to CodeHub through the public
``register`` + read-back; it is never written to the local catalog/outbox (the catalog
only keeps ``LOCAL_STATUSES``). A failed report never stops a download.

``ProgressReporter`` is the in-process hook (``acquire`` feeds it from ``fetch_to_file``);
for downloads driven by other processes use the CLI:
``uv run python -m backend.core.skilldep_materials.progress report|watch <material_id> --path DIR``.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

GB = 1024 ** 3
INTERVAL_SECONDS = 10 * 60
STEP_BYTES = GB
# Above either estimate a material announces itself as downloading right at the start.
ANNOUNCE_BYTES = 100 * 1024 * 1024
ANNOUNCE_SECONDS = 5 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def bytes_on_disk(path: Path) -> int:
    """Bytes of every file under ``path`` (``.part`` files included), or of ``path`` itself."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(base, name)).st_size
            except OSError:  # a part file renamed meanwhile
                pass
    return total


def remote_status(registry, material_id: str) -> str | None:
    mine = [r for r in registry.acquisitions(material_id) if r.get('device_id') == registry.device_id]
    return mine[0].get('status') if mine else None


def downloading_record(registry, path: Path, size_bytes: int, source_url: str | None = None,
                       total_bytes: int | None = None) -> dict[str, Any]:
    record = {'device_id': registry.device_id, 'device_name': registry.device_name, 'status': 'downloading',
              'availability': 'windows_local', 'path': Path(path).as_posix(), 'size_bytes': int(size_bytes),
              'finished_at': _now()}
    if source_url:
        record['source_url'] = source_url
    if total_bytes:
        record['total_bytes'] = int(total_bytes)
    return record


# Server-maintained fields of a stored cloud transfer record that are not part of its PUT body.
CLOUD_TRANSFER_SERVER_KEYS = ('material_id', 'account_id', 'resource_version', 'recorded_at', 'recorded_by')


def cloud_transfer_size_update(stored: dict[str, Any], *, size_bytes: int | None = None,
                               file_count: int | None = None, size_label: str | None = None) -> dict[str, Any]:
    """PUT body re-registering ``stored`` with its size filled in (rule 5); every other field kept."""
    if size_bytes is None and not size_label:
        raise ValueError('size_bytes or size_label required')
    body = {k: v for k, v in stored.items() if k not in CLOUD_TRANSFER_SERVER_KEYS}
    body['expected_resource_version'] = stored['resource_version']
    if size_bytes is not None:
        body['size_bytes'] = int(size_bytes)
    if file_count is not None:
        body['file_count'] = int(file_count)
    if size_label:
        body['size_label'] = size_label
    return body


def report_downloading(registry, material_id: str, path: Path, size_bytes: int | None = None, *,
                       source_url: str | None = None, total_bytes: int | None = None) -> dict[str, Any]:
    """One ``downloading`` PUT + read-back; skipped when CodeHub already has ``downloaded``. Never raises."""
    from .acquire import register_and_verify

    size = bytes_on_disk(path) if size_bytes is None else int(size_bytes)
    try:
        if remote_status(registry, material_id) == 'downloaded':
            return {'material_id': material_id, 'reported': False, 'skipped': 'already downloaded', 'size_bytes': size}
    except Exception as exc:  # noqa: BLE001 - an unreachable CodeHub only loses this report
        return {'material_id': material_id, 'reported': False, 'size_bytes': size,
                'register_error': f'{type(exc).__name__}: {exc}'[:500]}
    outcome = register_and_verify(registry, material_id,
                                  downloading_record(registry, path, size, source_url, total_bytes))
    return {'material_id': material_id, 'reported': outcome['registered'], 'size_bytes': size,
            **{k: v for k, v in outcome.items() if k == 'register_error'}}


class ProgressReporter:
    """Throttled ``downloading`` reports for one material.

    ``update(new_bytes)`` takes bytes written by the current transfer; ``baseline_bytes``
    (default: what is already under ``path``) is added so the size is cumulative. A report
    goes out when ``interval`` seconds or ``step_bytes`` new bytes passed since the last one;
    ``announce`` also reports immediately on the first update. After CodeHub answers
    ``downloaded`` (or the reporter is closed) it stays silent.
    """

    def __init__(self, registry, material_id: str, path: Path, *, source_url: str | None = None,
                 total_bytes: int | None = None, baseline_bytes: int | None = None, announce: bool = False, interval: float = INTERVAL_SECONDS,
                 step_bytes: int = STEP_BYTES, clock: Callable[[], float] = time.monotonic):
        self.registry, self.material_id, self.path, self.source_url = registry, material_id, Path(path), source_url
        self.total_bytes = total_bytes
        self.baseline = bytes_on_disk(self.path) if baseline_bytes is None and self.path.exists() else int(baseline_bytes or 0)
        self.interval, self.step_bytes, self.clock = interval, step_bytes, clock
        self._last_at = None if announce else clock()
        self._last_bytes = 0
        self.closed = False
        self.reports: list[dict[str, Any]] = []

    def update(self, new_bytes: int) -> dict[str, Any] | None:
        if self.closed:
            return None
        now = self.clock()
        due = (self._last_at is None or now - self._last_at >= self.interval
               or new_bytes - self._last_bytes >= self.step_bytes)
        if not due:
            return None
        self._last_at, self._last_bytes = now, new_bytes
        result = report_downloading(self.registry, self.material_id, self.path, self.baseline + new_bytes,
                                    source_url=self.source_url, total_bytes=self.total_bytes)
        self.reports.append(result)
        if result.get('skipped'):
            self.closed = True
        return result

    def close(self) -> None:
        self.closed = True


def should_announce(size_mb_est: float | None = None, seconds_est: float | None = None) -> bool:
    return bool((size_mb_est and size_mb_est * 1024 * 1024 > ANNOUNCE_BYTES)
                or (seconds_est and seconds_est > ANNOUNCE_SECONDS))


def watch(registry, material_id: str, path: Path, *, until_pid: int | None = None, total_bytes: int | None = None,
          interval: float = INTERVAL_SECONDS, step_bytes: int = STEP_BYTES,
          sleep: Callable[[float], None] = time.sleep, poll: float = 30) -> list[dict[str, Any]]:
    """Report the bytes under ``path`` (cumulative) for a download run by another process.

    Reports at once, then per the interval/step rule until ``until_pid`` exits (or forever).
    The final ``downloaded``/``partial``/``failed`` record stays the downloader's job.
    """
    reporter = ProgressReporter(registry, material_id, path, total_bytes=total_bytes, baseline_bytes=0, announce=True,
                                interval=interval, step_bytes=step_bytes)
    while not reporter.closed:
        reporter.update(bytes_on_disk(path) if Path(path).exists() else 0)
        if until_pid is not None and not _pid_alive(until_pid):
            break
        sleep(poll)
    return reporter.reports


def _pid_alive(pid: int) -> bool:
    import psutil

    return psutil.pid_exists(pid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.progress',
                                     description='SkillDep 下载中进度上报（status=downloading，size_bytes=已落盘累计字节）')
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name, help_text in (('report', '立即上报一次 downloading'),
                            ('watch', '按每 10 分钟或每新增 1GB 持续上报，直到 --until-pid 退出')):
        p = sub.add_parser(name, help=help_text)
        p.add_argument('material_id')
        p.add_argument('--path', required=True, type=Path, help='该素材落盘目录')
        p.add_argument('--source-url')
        p.add_argument('--total-bytes', type=int, help='来源直接给出的整项总字节（Content-Length 合计等）；没有就不填')
        if name == 'report':
            p.add_argument('--size-bytes', type=int, help='默认统计 --path 下所有文件（含 .part）')
        else:
            p.add_argument('--until-pid', type=int, help='下载进程 PID；退出后停止')
    args = parser.parse_args(argv)

    from .codehub import CodeHubSkillDep

    hub = CodeHubSkillDep()
    if args.cmd == 'report':
        out = report_downloading(hub, args.material_id, args.path, args.size_bytes, source_url=args.source_url,
                                 total_bytes=args.total_bytes)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out['reported'] or out.get('skipped') else 1
    out = watch(hub, args.material_id, args.path, until_pid=args.until_pid, total_bytes=args.total_bytes)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
