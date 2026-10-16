"""Health check of the official client's loopback HTTPS bridge (YunDetectService).

Only the read-only ``GetVersion`` method is used.  The bridge serves a
certificate for ``localhost.pan.baidu.com`` that may be expired, so the TLS
context here is local to this call, connects to 127.0.0.1 only, and reports
certificate facts instead of trusting them.  It is a liveness diagnostic, not
an authenticated channel; never send account data through it.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORTS = tuple(range(10000, 10006))  # official web JS scans 10000..10005
BRIDGE_ORIGIN = "https://pan.baidu.com"
EXPECTED_CERT_NAME = "localhost.pan.baidu.com"
RATE_LIMIT_ERROR = 31034
_JSONP_RE = re.compile(r"^\s*cb\((.*)\)\s*;?\s*$", re.S)


@dataclass(frozen=True)
class ClientVersion:
    ok: bool
    port: int | None = None
    version: str | None = None
    error: str | None = None
    rate_limited: bool = False
    cert_name_ok: bool | None = None
    cert_expired: bool | None = None
    cert_sha256: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _cert_facts(der: bytes | None) -> tuple[bool | None, bool | None, str | None]:
    if not der:
        return None, None, None
    digest = hashlib.sha256(der).hexdigest()
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.DNSName
        )
        expired = cert.not_valid_after_utc < datetime.now(timezone.utc)
        return EXPECTED_CERT_NAME in names, expired, digest
    except Exception:
        return None, None, digest


def parse_bridge_body(body: str) -> dict:
    """Decode the bridge's JSONP (``cb({...})``) or plain JSON body."""

    match = _JSONP_RE.match(body or "")
    return json.loads(match.group(1) if match else body)


def probe_client_version(ports: tuple[int, ...] = BRIDGE_PORTS, *, timeout: float = 3.0) -> ClientVersion:
    """Return the first port that answers ``GetVersion`` with ``errorno == 0``.

    No retries: the bridge rate-limits (error 31034) and callers should wait
    several seconds before asking again.
    """

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # local to this loopback probe; facts reported below
    last = ClientVersion(ok=False, error="no_bridge_port")
    for port in ports:
        conn = http.client.HTTPSConnection(BRIDGE_HOST, port, timeout=timeout, context=context)
        try:
            conn.request(
                "GET",
                "/guanjia?method=GetVersion&callback=cb",
                headers={"Referer": BRIDGE_ORIGIN + "/disk/main", "Origin": BRIDGE_ORIGIN},
            )
            der = conn.sock.getpeercert(binary_form=True) if conn.sock else None
            response = conn.getresponse()
            payload = parse_bridge_body(response.read(4096).decode("utf-8", "replace"))
        except (OSError, ValueError, http.client.HTTPException) as exc:
            last = ClientVersion(ok=False, port=port, error=type(exc).__name__)
            continue
        finally:
            conn.close()
        name_ok, expired, digest = _cert_facts(der)
        facts = {"port": port, "cert_name_ok": name_ok, "cert_expired": expired, "cert_sha256": digest}
        if payload.get("errorno") == 0 and isinstance(payload.get("version"), str):
            return ClientVersion(ok=True, version=payload["version"], **facts)
        limited = payload.get("error") == RATE_LIMIT_ERROR
        return ClientVersion(ok=False, error=str(payload.get("info") or payload), rate_limited=limited, **facts)
    return last
