from backend.core.storage_health import build_storage_health_report
from backend.core.storage_usage import collect_directory_usage


def test_source_health_flags_misplaced_data_directory(tmp_path):
    source_dir = tmp_path / "codeyun"
    attachment_dir = source_dir / "attachments"
    attachment_dir.mkdir(parents=True)
    (attachment_dir / "a.png").write_bytes(b"a" * 16)

    usage = collect_directory_usage(source_dir).to_dict()
    report = build_storage_health_report(
        scope="source_dir",
        label="源码目录",
        root_path=source_dir,
        usage=usage,
        data_workspace_path=tmp_path / "m2603codeyun",
    )

    assert report.health_score < 100
    assert any(issue.severity == "critical" for issue in report.issues)
    assert any(candidate.cleanup_kind == "move_to_data_workspace" for candidate in report.slimming_candidates)


def test_data_workspace_health_tracks_attachments_and_source_markers(tmp_path):
    workspace_dir = tmp_path / "m2603codeyun"
    attachments_dir = workspace_dir / "codepc_mf" / "attachments"
    source_marker = workspace_dir / "frontend"
    attachments_dir.mkdir(parents=True)
    source_marker.mkdir(parents=True)
    (attachments_dir / "a.png").write_bytes(b"a" * 16)
    (source_marker / "package.json").write_text("{}", encoding="utf-8")

    usage = collect_directory_usage(workspace_dir).to_dict()
    report = build_storage_health_report(
        scope="data_workspace",
        label="数据工作区",
        root_path=workspace_dir,
        usage=usage,
        attachments_dir=attachments_dir,
    )

    assert any(candidate.category == "attachments" for candidate in report.slimming_candidates)
    assert any(issue.id.startswith("data_source_marker") for issue in report.issues)
