from __future__ import annotations

from backend.core.services.launcher import install_child_process_no_window_default

install_child_process_no_window_default()

from backend.core.fanxiu.instrumentation.spirit_artifact_collector import (  # noqa: E402
    run_spirit_artifact_collector_loop,
)


def main() -> int:
    run_spirit_artifact_collector_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
