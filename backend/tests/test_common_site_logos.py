from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import common_sites


class _LogoResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.headers = {"Content-Type": "image/png"}

    def raise_for_status(self) -> None:
        return None


def test_common_site_logo_uses_persistent_cache_and_explicit_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(common_sites, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    downloaded: list[bytes] = []

    def fake_get(*_args, **_kwargs):
        content = f"logo-{len(downloaded) + 1}".encode()
        downloaded.append(content)
        return _LogoResponse(content)

    monkeypatch.setattr(common_sites.requests, "get", fake_get)

    first = common_sites._logo_response("https://example.com/articles/1", refresh=False)
    second = common_sites._logo_response("https://example.com/articles/2", refresh=False)

    assert len(downloaded) == 1
    assert first.path == second.path
    assert Path(first.path).read_bytes() == b"logo-1"

    refreshed = common_sites._logo_response("https://example.com", refresh=True)

    assert len(downloaded) == 2
    assert refreshed.path == first.path
    assert Path(refreshed.path).read_bytes() == b"logo-2"


def test_common_site_logo_rejects_non_http_urls():
    with pytest.raises(HTTPException) as error:
        common_sites._normalize_site_origin("file:///etc/passwd")
    assert error.value.status_code == 422
