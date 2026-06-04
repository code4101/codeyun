from pathlib import Path

from backend.core import temp_paths


def test_codeyun_temp_root_uses_system_temp_and_sanitizes_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(temp_paths.tempfile, "gettempdir", lambda: str(tmp_path))

    result = temp_paths.codeyun_temp_root("fanxiu verify", "../bad", create=True)

    assert result == tmp_path / "codeyun" / "fanxiu_verify" / "bad"
    assert result.exists()
    assert Path.cwd().resolve() not in result.resolve().parents

