"""审计或重建 m2510mn 编号媒体目录的 DeviceFile 索引。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.filesystem import DeviceFileScanRequest, _resolve_media_kind, scan_device_file_records
from backend.core.devices.device import get_device_id
from backend.db import engine


NUMBERED_DIRECTORY_RE = re.compile(r"^[123]、")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-dir", default=r"E:\data\m2510mn")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def numbered_directories(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and NUMBERED_DIRECTORY_RE.match(path.name)),
        key=lambda path: path.name.lower(),
    )


def count_disk_media(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file() and _resolve_media_kind(path))


def main() -> int:
    args = parse_args()
    root = Path(args.root_dir).expanduser().resolve()
    device_id = get_device_id()
    results = []
    with Session(engine) as session:
        for directory in numbered_directories(root):
            disk_media_count = count_disk_media(directory)
            result = None
            if args.apply:
                result = scan_device_file_records(
                    DeviceFileScanRequest(
                        absolute_path=str(directory),
                        recursive=True,
                        hash_mode="never",
                        mark_missing_as_dangling=True,
                    ),
                    session,
                    device_id=device_id,
                )
            results.append(
                {
                    "directory": directory.name,
                    "disk_media_count": disk_media_count,
                    **(
                        {
                            key: value
                            for key, value in result.items()
                            if key.endswith("_count") and key != "items"
                        }
                        if result
                        else {}
                    ),
                }
            )
    print(json.dumps({"mode": "apply" if args.apply else "audit", "device_id": device_id, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
