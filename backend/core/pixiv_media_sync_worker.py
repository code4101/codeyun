"""Compatibility facade for the former Pixiv-only worker module.

New code should import :mod:`backend.core.media_sync_worker`.  Keeping this
module avoids breaking old scripts while the worker itself is now platform
neutral.
"""

from backend.core.media_sync_worker import (  # noqa: F401
    _read_json,
    _run_worker,
    _state_root,
    _write_json,
    launch_media_sync_worker,
    main,
    read_worker_snapshot,
    request_worker_cancel,
    worker_state_path,
)

launch_pixiv_worker = launch_media_sync_worker


if __name__ == "__main__":
    raise SystemExit(main())
