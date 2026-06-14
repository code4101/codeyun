import importlib.util
import json
import os
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("manual_zen_attendance_refresh_20260614.py")
COURSE = "d260517修道班7期5阶"


def main() -> int:
    spec = importlib.util.spec_from_file_location("manual_zen_refresh", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mod.RUN_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(mod.KQ_WORK_ROOT)

    before = mod._snapshot_refund_files()
    result = mod.run_codeyun_course(COURSE)
    after = mod._snapshot_refund_files()

    payload = {
        "result": result,
        "new_refund_files": sorted(str(x) for x in (after - before)),
        "new_refund_file_count": len(after - before),
    }
    out = mod.RUN_DIR / "codeyun_retry_summary.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") and not payload["new_refund_file_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
