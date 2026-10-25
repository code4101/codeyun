import json
from urllib.parse import parse_qsl, urlsplit

import pytest

from backend.core.baidu_netdisk import listing
from backend.core.baidu_netdisk.listing_parse import (
    compare_scans,
    page_url,
    parse_page,
    PageError,
    prove_child,
    scan_listing,
    validate_seed,
)

BIG = 2 ** 63 - 7  # beyond JS Number precision


@pytest.mark.parametrize("stage", ["parent_before", "parent_after"])
def test_parent_read_timeout_remains_actionable_without_leaking_payload(monkeypatch, stage):
    failed = {"status": "unavailable", "reason": "fetch_failed", "error": "TimeoutError",
              "errno": 5, "http_status": 504, "path": "/p", "text": "private payload"}
    good = {"status": "listed", "entries": [
        {"id": "12", "path": "/p/dir", "name": "dir", "is_dir": True}]}
    scans = iter([failed] if stage == "parent_before" else [good, failed])

    class Reader:
        def __init__(self, *args): pass
        def capture_seed(self, path): pass
        def scan(self, path): return next(scans)
        def stable(self, path): return {"status": "listed", "complete": True}

    monkeypatch.setattr(listing, "_Reader", Reader)
    monkeypatch.setattr(listing, "_in_own_tab", lambda base, context, body: body(None, None))
    result = listing.list_directory("/p/dir", expected_id="12")
    assert result["reason"] == "fetch_failed" and result["error"] == "TimeoutError"
    assert result["status"] == "unavailable" and result["read_stage"] == stage
    assert result["errno"] == 5 and result["http_status"] == 504
    assert "private payload" not in json.dumps(result)


def test_true_parent_identity_mismatch_still_stops(monkeypatch):
    class Reader:
        def __init__(self, *args): pass
        def capture_seed(self, path): pass
        def scan(self, path):
            return {"status": "listed", "entries": [
                {"id": "wrong", "path": "/p/dir", "name": "dir", "is_dir": True}]}
        def stable(self, path): raise AssertionError("must not read mismatched child")

    monkeypatch.setattr(listing, "_Reader", Reader)
    monkeypatch.setattr(listing, "_in_own_tab", lambda base, context, body: body(None, None))
    result = listing.list_directory("/p/dir", expected_id="12")
    assert result["status"] == "id_mismatch" and result["reason"] == "identity_not_proven"
    assert "error" not in result and "parent_read" not in result


def _row(i, parent="/", *, name=None, isdir=0, fs_id=None, size=None, mtime=1_700_000_000):
    name = name or f"f{i:02d}"
    return {"fs_id": fs_id or BIG - i, "server_filename": name,
            "path": f"{parent.rstrip('/')}/{name}", "isdir": isdir,
            "size": i if size is None else size, "server_mtime": mtime}


def _resp(rows, errno=0, status=200, **extra):
    return {"status": status, "text": json.dumps({"errno": errno, "guid": 0, "guid_info": "",
                                                    "request_id": 1, "list": rows, **extra})}


def _pages(*pages, parent="/"):
    """fetch_page over fixed bodies; records each (page, num) asked once."""
    calls = []

    def fetch(page, num):
        calls.append((page, num))
        return pages[page - 1] if page <= len(pages) else _resp([])
    fetch.calls = calls
    return fetch


def _forced_paging(parent="/"):
    rows = [_row(i, parent) for i in range(27)]
    return [_resp(rows[:10]), _resp(rows[10:20]), _resp(rows[20:27]), _resp([])]


def test_pages_until_empty_terminal_page_not_short_page():
    fetch = _pages(*_forced_paging())
    out = scan_listing(fetch, "/", page_size=10, max_pages=10)
    assert out["status"] == "listed" and out["complete"] is True
    assert out["count"] == 27 and len({e["id"] for e in out["entries"]}) == 27
    assert out["complete_basis"] == {"terminal_empty_page": 4, "page_counts": [10, 10, 7, 0]}
    assert fetch.calls == [(1, 10), (2, 10), (3, 10), (4, 10)]
    assert len(out["snapshot_sha256"]) == 64 and out["observed_at"]


def test_fs_id_keeps_full_precision_and_entry_shape():
    out = scan_listing(_pages(_resp([_row(0, "/a", isdir=1, size=0)]), _resp([])), "/a", 10, 5)
    entry = out["entries"][0]
    assert entry == {"id": str(BIG), "name": "f00", "path": "/a/f00", "is_dir": True,
                     "size": 0, "version": 1_700_000_000}


def test_short_page_without_terminal_page_is_not_listed():
    pages = _forced_paging()[:3]

    def fetch(page, num):
        if page > 3:
            raise TimeoutError
        return pages[page - 1]
    out = scan_listing(fetch, "/", 10, 10)
    assert out["status"] == "unavailable" and out["reason"] == "fetch_failed"
    assert out["page_counts"] == [10, 10, 7] and "entries" not in out


def test_page_budget_exhausted_is_incomplete():
    out = scan_listing(_pages(*_forced_paging()), "/", 10, 3)
    assert out["status"] == "incomplete" and out["reason"] == "page_budget_exhausted"
    assert "entries" not in out


@pytest.mark.parametrize("second,reason", [
    ([_row(5)], "duplicate_id"),
    ([_row(99, name="f05")], "duplicate_path"),
])
def test_cross_page_duplicates_are_unstable(second, reason):
    out = scan_listing(_pages(_resp([_row(i) for i in range(10)]), _resp(second)), "/", 10, 5)
    assert out["status"] == "unstable" and out["reason"] == reason


@pytest.mark.parametrize("row,reason", [
    ({**_row(1, "/b")}, "row_wrong_parent"),
    ({**_row(1), "path": "/other"}, "row_name_path_conflict"),
    ({**_row(1), "server_filename": "a/b", "path": "/a/b"}, "row_bad_name"),
    ({**_row(1), "isdir": 2}, "row_bad_isdir"),
    ({**_row(1), "isdir": True}, "row_bad_isdir"),
    ({**_row(1), "fs_id": "123"}, "row_bad_fs_id"),
    ({**_row(1), "fs_id": 1.5}, "row_bad_fs_id"),
    ({**_row(1), "size": -1}, "row_bad_size_or_mtime"),
    ({k: v for k, v in _row(1).items() if k != "server_mtime"}, "row_bad_size_or_mtime"),
])
def test_invalid_rows_reject_listing(row, reason):
    out = scan_listing(_pages(_resp([row])), "/", 10, 5)
    assert out["status"] == "unavailable" and out["reason"] == reason


@pytest.mark.parametrize("response,reason", [
    (_resp([], errno=-9), "errno"),
    (_resp([], status=403), "http_status"),
    ({"status": 200, "text": "<html>"}, "body_not_json"),
    ({"status": 200, "text": json.dumps({"errno": 0})}, "list_missing"),
    ({"status": 200, "text": json.dumps({"list": []})}, "errno_missing"),
    (None, "fetch_result_invalid"),
])
def test_refusals_are_unavailable_and_not_retried(response, reason):
    fetch = _pages(response)
    out = scan_listing(fetch, "/", 10, 5)
    assert out["status"] == "unavailable" and out["reason"] == reason
    assert fetch.calls == [(1, 10)]


def test_errno_value_reported_without_interpretation():
    with pytest.raises(PageError) as exc:
        parse_page(_resp([], errno=31066), "/")
    assert exc.value.detail == {"errno": 31066}


def test_oversize_page_rejected():
    out = scan_listing(_pages(_resp([_row(i) for i in range(11)])), "/", 10, 5)
    assert out["reason"] == "page_oversize"


def test_two_scans_must_match():
    a = scan_listing(_pages(*_forced_paging()), "/", 10, 10)
    b = scan_listing(_pages(*_forced_paging()), "/", 10, 10)
    assert compare_scans(a, b)["status"] == "listed"
    assert compare_scans(a, b)["complete_basis"]["consistent_scans"] == 2
    changed = _forced_paging()
    rows = json.loads(changed[2]["text"])["list"]
    rows[0]["server_mtime"] += 1
    changed[2] = _resp(rows)
    c = scan_listing(_pages(*changed), "/", 10, 10)
    assert compare_scans(a, c)["status"] == "unstable"


def test_prove_child_by_exact_path_type_and_id():
    rows = [_row(1, "/p", name="dir", isdir=1), _row(2, "/p", name="file")]
    parent = scan_listing(_pages(_resp(rows)), "/p", 10, 5)
    ok = prove_child(parent, "/p/dir", str(BIG - 1))
    assert ok["status"] == "ok" and ok["entry"]["id"] == str(BIG - 1)
    assert prove_child(parent, "/p/dir", "1")["status"] == "id_mismatch"
    assert prove_child(parent, "/p/file")["status"] == "type_mismatch"
    assert prove_child(parent, "/p/file", want_dir=None)["status"] == "ok"
    assert prove_child(parent, "/p/Dir")["status"] == "missing"
    incomplete = scan_listing(_pages(_resp(rows)), "/p", 10, 1)
    assert prove_child(incomplete, "/p/dir")["reason"] == "parent_not_listed"


SEED = ("https://pan.baidu.com/api/list?clienttype=0&app_id=X&web=1&dp-logid=L"
        "&order=time&desc=1&dir=%2F&num=100&page=1")


def test_page_url_changes_only_dir_page_num():
    url = page_url(SEED, "/中文 a&b", 3, 10)
    parts = urlsplit(url)
    assert (parts.scheme, parts.hostname, parts.path) == ("https", "pan.baidu.com", "/api/list")
    got = dict(parse_qsl(parts.query, keep_blank_values=True))
    seed = dict(parse_qsl(urlsplit(SEED).query))
    assert got == {**seed, "dir": "/中文 a&b", "page": "3", "num": "10"}


@pytest.mark.parametrize("url", [
    SEED.replace("https", "http"),
    SEED.replace("pan.baidu.com", "pan.baidu.com.evil.test"),
    SEED.replace("pan.baidu.com", "pan.baidu.com:8443"),
    SEED.replace("/api/list", "/api/filemetas"),
    SEED.replace("&dir=%2F", ""),
    SEED + "&page=2",
    None,
])
def test_seed_validation(url):
    assert validate_seed(url) is None
    assert validate_seed(SEED) == SEED


@pytest.mark.parametrize("path", ["", "rel", "/a/../b", "/a/./b"])
def test_invalid_arguments_touch_nothing(path):
    def context():
        raise AssertionError("context must not be entered")
    assert listing.list_directory(path, context=context)["status"] == "invalid"
    assert listing.resolve_remote(path, context=context)["status"] == "invalid"
    assert listing.list_directory("/a", page_size=0, context=context)["status"] == "invalid"


def test_root_is_explicit_not_fake_id():
    def context():
        raise AssertionError("root resolution needs no account access")
    assert listing.resolve_remote("/", context=context) == {
        "status": "resolved", "path": "/", "is_root": True, "id": None, "is_dir": True, "chain": []}
    assert listing.list_directory("/", expected_id="1", context=context)["reason"] == "root_has_no_id"
