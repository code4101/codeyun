import socket

import pytest

import dev


def test_read_backend_port_defaults_when_unset():
    assert dev.read_backend_port({}) == 8000


def test_read_backend_port_accepts_valid_env_value():
    assert dev.read_backend_port({"CODEYUN_BACKEND_PORT": "8010"}) == 8010


def test_read_backend_port_rejects_invalid_env_value():
    assert dev.read_backend_port({"CODEYUN_BACKEND_PORT": "bad"}) == 8000
    assert dev.read_backend_port({"CODEYUN_BACKEND_PORT": "70000"}) == 8000


def test_local_address_uses_port_matches_ipv4_and_ipv6():
    assert dev._local_address_uses_port("0.0.0.0:8000", 8000)
    assert dev._local_address_uses_port("[::]:8000", 8000)
    assert not dev._local_address_uses_port("127.0.0.1:8001", 8000)


def test_tcp_port_can_bind_detects_bound_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]

        assert not dev.tcp_port_can_bind("127.0.0.1", port)


def test_ensure_backend_port_available_reports_listener_pids(monkeypatch):
    monkeypatch.setattr(dev, "tcp_port_can_bind", lambda host, port: False)
    monkeypatch.setattr(dev, "find_tcp_listener_pids", lambda port: [123, 456])

    with pytest.raises(dev.PortInUseError) as exc_info:
        dev.ensure_backend_port_available("0.0.0.0", 8000)

    message = str(exc_info.value)
    assert "0.0.0.0:8000" in message
    assert "Listening PID(s): 123, 456." in message
