import subprocess
import sys
import time

import pytest

from backend.core.services.launcher import run_quiet_captured


def test_capture_returns_text_and_exit_code():
    result = run_quiet_captured(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"],
        timeout=5,
    )
    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (7, "out", "err")


def test_timeout_does_not_wait_for_descendant_output_handles():
    # A real child inherits the parent's output handles, reproducing the ADB
    # cleanup hazard without a game or mocked subprocess implementation.
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], "
        "stdout=sys.stdout, stderr=sys.stderr, close_fds=False); "
        "print('started', flush=True); time.sleep(5)"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run_quiet_captured([sys.executable, "-c", code], timeout=0.5)
    assert time.monotonic() - started < 2.0
    assert "started" in error.value.output
