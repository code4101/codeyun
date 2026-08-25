from __future__ import annotations

import argparse

from backend.core.jobs.local_runtime import run_local_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one durable CodeYun Local Job.")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    return run_local_job(str(args.run_id))


if __name__ == "__main__":
    raise SystemExit(main())
