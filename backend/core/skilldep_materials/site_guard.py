"""Site circuit breakers shared by every device through CodeHub (``/plan/site-blocks``).

Before a crawl starts, ``assert_open`` reads the breakers and refuses a site whose record
is ``active``. A throttle response (403/412/429, a "访问过于频繁" body, a blank page with no
hint) is reported once with ``trip``: CodeHub stores it under the site's short name with a
24h default cooldown, and every crawler of that site must stop. A captcha is no throttle:
it is tried once, then the run stops and is reported, without tripping the breaker.

Short name = the main-domain label: ``www.cninfo.com.cn`` -> ``cninfo``,
``search.ccgp.gov.cn`` -> ``ccgp``, ``item.taobao.com`` -> ``taobao``. Older records keyed by
a full host (``travel.state.gov``) still match any URL under that host.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

THROTTLE_STATUSES = frozenset({403, 412, 429})
THROTTLE_TEXTS = ('访问过于频繁', '请求过于频繁', '操作过于频繁', '限制访问')
# Second-level suffixes under which the registrable name sits one label further left.
_SECOND_LEVEL = {'com', 'net', 'org', 'gov', 'edu', 'ac', 'co', 'mil'}


class SiteBlocked(RuntimeError):
    def __init__(self, site: str, reason: str):
        super().__init__(f'站点 {site} 熔断中：{reason}')
        self.site, self.reason = site, reason


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or '').lower().rstrip('.')


def site_name(url_or_host: str) -> str:
    host = host_of(url_or_host) if '/' in url_or_host else url_or_host.lower().rstrip('.')
    labels = [x for x in host.split('.') if x]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else host


def throttle_reason(status: int, body: bytes | None, *, expect_body: bool = True) -> str | None:
    """Why a response looks like throttling, or None. ``expect_body``: an empty 200 counts as a silent block."""
    if status in THROTTLE_STATUSES:
        return f'HTTP {status}'
    head = (body or b'')[:65536].decode('utf-8', errors='ignore')
    if hit := next((t for t in THROTTLE_TEXTS if t in head), None):
        return f'正文含“{hit}”'
    if expect_body and status == 200 and not head.strip():
        return '无提示空白页'
    return None


def matching_block(blocks: dict[str, dict[str, Any]], url: str) -> tuple[str, dict[str, Any]] | None:
    host, short = host_of(url), site_name(url)
    for key, rec in blocks.items():
        if not rec.get('active'):
            continue
        if key == short or host == key or host.endswith('.' + key):
            return key, rec
    return None


def active_block(registry, url: str) -> dict[str, Any] | None:
    hit = matching_block(registry.site_blocks(), url)
    return {'site': hit[0], **hit[1]} if hit else None


def assert_open(registry, url: str) -> None:
    """Raise ``SiteBlocked`` when ``url``'s site has an active breaker. CodeHub errors propagate (fail closed)."""
    if hit := active_block(registry, url):
        raise SiteBlocked(hit['site'], hit.get('reason') or '')


def trip(registry, url: str, reason: str) -> dict[str, Any]:
    """Report a throttle on ``url``'s site; never raises (the caller stops the site anyway)."""
    site = site_name(url)
    try:
        return {'site': site, 'tripped': True, 'record': registry.put_site_block(site, f'{reason}：{url}'[:500])}
    except Exception as exc:  # noqa: BLE001 - the local stop still holds
        return {'site': site, 'tripped': False, 'error': f'{type(exc).__name__}: {exc}'[:300]}
