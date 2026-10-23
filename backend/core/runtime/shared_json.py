"""Bounded JSON state IO for cooperating Windows worker processes.

Readers may briefly hold a destination open during atomic replacement. Retry
sharing/access failures; never turn an unreadable reservation into an empty queue.
Callers still own their workflow locks and JSON schema validation.
"""
import json
import os
import time
import uuid
from pathlib import Path


def retry_shared_io(action, *, attempts=20, interval=.05):
    """Retry transient access/sharing violations, propagating the final error."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(interval)


def read_json(path):
    """Read one complete JSON snapshot; corruption and missing files propagate."""
    return json.loads(retry_shared_io(Path(path).read_bytes))


def write_json(path, value):
    """Publish atomically using a unique sibling temp file and bounded retries."""
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        retry_shared_io(lambda: os.replace(temporary, path))
    finally:
        temporary.unlink(missing_ok=True)
