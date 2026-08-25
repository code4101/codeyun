from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_backend_startup_does_not_launch_spirit_artifact_collector() -> None:
    source = (REPO_ROOT / "backend/app.py").read_text(encoding="utf-8")

    assert "ensure_spirit_artifact_collector_service" not in source
    assert "fanxiu_spirit_artifact_collector" not in source
