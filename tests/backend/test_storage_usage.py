from backend.core.resources.storage_usage import collect_directory_usage


def test_collect_directory_usage_counts_whole_tree(tmp_path):
    alpha = tmp_path / "alpha"
    nested = alpha / "nested"
    nested.mkdir(parents=True)
    (alpha / "a.txt").write_bytes(b"a" * 10)
    (nested / "b.txt").write_bytes(b"b" * 5)
    (tmp_path / "root.bin").write_bytes(b"r" * 7)

    summary = collect_directory_usage(tmp_path, top_limit=10)

    assert summary.logical_size_bytes == 22
    assert summary.file_count == 3
    assert summary.directory_count == 2
    assert summary.inaccessible_count == 0

    entries = {entry.name: entry for entry in summary.top_entries}
    assert entries["alpha"].logical_size_bytes == 15
    assert entries["alpha"].file_count == 2
    assert entries["root.bin"].logical_size_bytes == 7
    assert entries["root.bin"].file_count == 1


def test_collect_directory_usage_limits_top_entries(tmp_path):
    (tmp_path / "small.txt").write_bytes(b"s")
    (tmp_path / "large.txt").write_bytes(b"l" * 20)

    summary = collect_directory_usage(tmp_path, top_limit=1)

    assert [entry.name for entry in summary.top_entries] == ["large.txt"]


def test_collect_directory_usage_prefers_treesize_listing(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()

    def fake_list_directory_items(root_key=None, rel_path="", absolute_path="", *, sort_program=None, session=None):
        assert root_key is None
        assert rel_path == ""
        assert absolute_path == str(root.resolve())
        assert sort_program is None
        return {
            "root": None,
            "current_path": str(root),
            "absolute_path": str(root),
            "items": [
                {
                    "name": "large",
                    "path": str(root / "large"),
                    "is_dir": True,
                    "size": None,
                    "modified_at": 1000,
                    "recursive_total_bytes": 4096,
                    "recursive_file_count": 3,
                },
                {
                    "name": "file.txt",
                    "path": str(root / "file.txt"),
                    "is_dir": False,
                    "size": 5,
                    "modified_at": 2000,
                },
            ],
        }

    monkeypatch.setattr("backend.api.filesystem.list_directory_items", fake_list_directory_items)

    summary = collect_directory_usage(root, top_limit=10)

    assert summary.source == "treesize"
    assert summary.logical_size_bytes == 4101
    assert summary.allocated_size_bytes == 4101
    assert summary.file_count == 4
    assert summary.directory_count == 1
    assert [entry.name for entry in summary.top_entries] == ["large", "file.txt"]
