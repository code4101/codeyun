import subprocess

from scripts.check_publication import check


def test_deleted_content_and_commit_messages_remain_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def git(*args):
        return subprocess.check_output(["git", *args])
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    file = tmp_path / "example.txt"
    file.write_text("local-only-marker")
    git("add", ".")
    assert check(["local-only-marker"])
    git("commit", "-qm", "local-only-marker")
    file.write_text("public")
    git("add", ".")
    git("commit", "-qm", "clean current content")
    assert not check(["local-only-marker"])
    failures = check(["local-only-marker"], refs=["HEAD"])
    assert any("blob" in item for item in failures)
    assert any("commit" in item for item in failures)
