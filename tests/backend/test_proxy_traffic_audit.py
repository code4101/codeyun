from __future__ import annotations

from backend.core.proxy_traffic_audit import _decode_chunked_http_body, is_proxy_connection


def test_decode_chunked_http_body():
    assert _decode_chunked_http_body(b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n") == b"hello world"


def test_is_proxy_connection_uses_actual_outbound_chain_head():
    assert not is_proxy_connection({"chains": ["DIRECT", "哔哩哔哩"]})
    assert is_proxy_connection({"chains": ["美国LA-优化-GPT", "ChatGPT"]})
