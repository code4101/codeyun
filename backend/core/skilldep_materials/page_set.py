"""Fetch an explicit list of URLs (pages and attachments) of one SkillDep material, resumably.

The "open page by page" tier: the caller names every URL. Files land in
``<root>/<material_id>/files/``; ``manifest.json`` there lists each with source URL, SHA-256
and size, and a re-run skips files that still match. Requests to one site are serial with a
random ``gap`` (default 10-20 s); the CodeHub site breaker is checked first and a throttle
answer (403/412/429, throttling text, a blank page) trips it and stops the run. A captcha
page stops the run without tripping. ``downloading`` is registered at the start and after
each file; the end registers ``downloaded`` (or ``partial`` with ``scope_note``, or
``failed`` with the reason when nothing was fetched).

CLI: ``uv run python -m backend.core.skilldep_materials.page_set <material_id> URL... [--partial NOTE]``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .acquire import DEFAULT_ROOT, register_and_verify
from .batch import first_batch_limit_bytes
from .download import USER_AGENT, filename_for
from .site_guard import SiteBlocked, assert_open, site_name, throttle_reason, trip

CAPTCHA_TEXTS = ('验证码', '人机验证', '安全验证', '滑动验证', 'captcha')
ATTACHMENT_EXTS = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.zip', '.rar', '.7z', '.json', '.txt',
                   '.ppt', '.pptx', '.wps', '.et', '.parquet', '.jsonl', '.gz', '.tar')
DEFAULT_CAP = 200 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Stop(RuntimeError):
    pass


def _name(url: str, index: int, ctype: str, used: set[str]) -> str:
    base = filename_for(url, f'item-{index}')
    if '.' not in base:
        base += '.html' if 'html' in ctype else '.json' if 'json' in ctype else ''
    name, n = base, 1
    while name in used:
        stem, dot, ext = base.rpartition('.')
        name = f'{stem or base}-{n}{dot}{ext}' if dot else f'{base}-{n}'
        n += 1
    return name


MIN_FILE_BYTES = 1024
MIN_TEXT_CHARS = 200


def _has_records(data: Any) -> bool:
    """True when JSON holds at least one non-empty list (the data rows)."""
    if isinstance(data, list):
        return bool(data)
    if isinstance(data, dict):
        return any(_has_records(v) for v in data.values())
    return False


def empty_reason(body: bytes, ctype: str, url: str = '') -> str | None:
    """Why a 200 answer is not material content: tiny file, JSON without rows, page with little text."""
    if len(body) < MIN_FILE_BYTES:
        return f'文件只有 {len(body)} 字节'
    head = body.lstrip()[:1]
    if 'json' in ctype or head in (b'{', b'['):
        try:
            data = json.loads(body.decode('utf-8-sig', errors='ignore'))
        except ValueError:
            data = None
        if data is not None and not _has_records(data):
            return 'JSON 没有数据记录'
    elif 'html' in ctype:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body, 'html.parser')
        for t in soup(['script', 'style', 'noscript']):
            t.decompose()
        text = ''.join(soup.get_text(' ').split())
        if len(text) < MIN_TEXT_CHARS:
            return f'正文只有 {len(text)} 字'
    return None


class RenderSession:
    """``requests``-like ``get`` that renders the page in a headless controlled Chromium (DrissionPage).

    For JS-rendered pages whose data API could not be used directly; the rendered DOM is saved.
    """

    def __init__(self, wait: float = 6):
        from DrissionPage import ChromiumOptions, ChromiumPage
        self.page = ChromiumPage(ChromiumOptions().headless().auto_port().set_argument('--no-sandbox'))
        self.wait = wait
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: float = 60):
        from types import SimpleNamespace
        self.page.get(url, timeout=timeout)
        self.page.wait(self.wait)
        body = self.page.html.encode('utf-8')
        return SimpleNamespace(status_code=200, content=body, url=self.page.url,
                               headers={'Content-Type': 'text/html; charset=utf-8'})

    def close(self) -> None:
        self.page.quit()


def expand_links(base_url: str, body: bytes, *, path_prefix: str | None = None) -> list[str]:
    """Content links of an entry page: attachments (any host) first, then same-host pages under ``path_prefix``."""
    from urllib.parse import urljoin, urlsplit

    from bs4 import BeautifulSoup
    host = urlsplit(base_url).netloc
    prefix = path_prefix if path_prefix is not None else urlsplit(base_url).path.rsplit('/', 1)[0] + '/'
    files, pages = [], []
    for a in BeautifulSoup(body, 'html.parser').find_all('a', href=True):
        href = a['href'].strip()
        if not href or href.startswith(('#', 'javascript:', 'mailto:')):
            continue
        u = urljoin(base_url, href).split('#')[0]
        parts = urlsplit(u)
        if parts.scheme not in ('http', 'https') or u == base_url:
            continue
        if parts.path.lower().endswith(ATTACHMENT_EXTS):
            files.append(u)
        elif parts.netloc == host and parts.path.startswith(prefix):
            pages.append(u)
    return list(dict.fromkeys(files + pages))


def fetch_set(registry, material_id: str, urls: list[str], *, root: Path = DEFAULT_ROOT,
              session: requests.Session | None = None, gap: tuple[float, float] = (10, 20),
              max_bytes: int = DEFAULT_CAP, partial_note: str | None = None,
              headers: dict[str, str] | None = None, sleep: Callable[[float], Any] = time.sleep,
              rand: Callable[[float, float], float] = random.uniform,
              entry_urls: set[str] | None = None, expand: int = 0,
              expand_prefix: str | None = None) -> dict[str, Any]:
    """``entry_urls`` are index/landing pages: they alone never count as material content, so a run
    that got nothing else stays ``downloading`` instead of ``partial``. ``expand`` follows up to that
    many content links (attachments, same-section pages) found on the fetched entry pages."""
    entry_urls = set(entry_urls or ())
    urls = list(urls)
    out = Path(root) / material_id
    files_dir = out / 'files'
    manifest_path = out / 'manifest.json'
    manifest = json.loads(manifest_path.read_text('utf-8')) if manifest_path.is_file() else {
        'material_id': material_id, 'files': {}}
    http = session or requests.Session()
    http.headers.update({'User-Agent': USER_AGENT.replace(' CodeYun-skilldep-materials/1.0', ''), **(headers or {})})
    state: dict[str, Any] = {'status': 'done', 'skipped': []}
    first_request = True

    def used_bytes() -> int:
        return sum(e['size_bytes'] for e in manifest['files'].values())

    def progress() -> None:
        from .progress import report_downloading
        report_downloading(registry, material_id, out, used_bytes(), source_url=urls[0])

    def save() -> None:
        manifest.update(file_count=len(manifest['files']), payload_bytes=used_bytes(), updated_at=_now())
        out.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_name('manifest.json.tmp')
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, manifest_path)

    try:
        for url in urls:
            assert_open(registry, url)
        progress()
        expanded = 0
        i = 0
        while i < len(urls):
            url = urls[i]
            i += 1
            e = manifest['files'].get(url)
            if e and (out / e['path']).is_file() and (out / e['path']).stat().st_size == e['size_bytes']:
                if expand and url in entry_urls and expanded < expand and 'html' in e.get('content_type', ''):
                    new = [u for u in expand_links(e.get('final_url') or url, (out / e['path']).read_bytes(),
                                                   path_prefix=expand_prefix) if u not in urls][:expand - expanded]
                    urls += new
                    expanded += len(new)
                continue
            try:
                assert_open(registry, url)
            except SiteBlocked as exc:
                state['skipped'].append({'url': url, 'reason': f'站点熔断：{exc}'})
                continue
            if used_bytes() >= max_bytes:
                state['stopped_reason'] = 'max_bytes'
                break
            if not first_request:
                sleep(rand(*gap))
            first_request = False
            try:
                resp = http.get(url, timeout=60)
            except requests.RequestException as exc:
                state['skipped'].append({'url': url, 'reason': f'{type(exc).__name__}'})
                continue
            body = resp.content
            if reason := throttle_reason(resp.status_code, body):
                state['site_block'] = trip(registry, url, reason)
                raise Stop(f'限流（{reason}）：{url}')
            ctype = resp.headers.get('Content-Type', '')
            low = body[:200000].decode('utf-8', errors='ignore').lower() if 'html' in ctype else ''
            if low and len(low) < 20000 and any(t.lower() in low for t in CAPTCHA_TEXTS):
                raise Stop(f'验证码页，停止且不绕过：{url}')
            if resp.status_code >= 400:
                state['skipped'].append({'url': url, 'reason': f'HTTP {resp.status_code}'})
                continue
            if used_bytes() + len(body) > max_bytes:
                state['stopped_reason'] = 'max_bytes'
                break
            names = {Path(x['path']).name for x in manifest['files'].values()}
            rel = f'files/{_name(resp.url or url, i, ctype, names)}'
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_bytes(body)
            manifest['files'][url] = {'path': rel, 'source_url': url, 'final_url': resp.url,
                                      'size_bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                                      'content_type': ctype, 'fetched_at': _now()}
            if empty := empty_reason(body, ctype, url):
                manifest['files'][url]['empty'] = empty
                print(f'{_now()} 空内容不计入素材（{empty}）：{url}', flush=True)
            save()
            progress()
            if expand and url in entry_urls and expanded < expand and 'html' in ctype:
                new = [u for u in expand_links(resp.url or url, body, path_prefix=expand_prefix)
                       if u not in urls][:expand - expanded]
                urls += new
                expanded += len(new)
    except SiteBlocked as exc:
        state.update(status='refused', error=str(exc))
    except Stop as exc:
        state.update(status='stopped', error=str(exc))
    save()
    files = manifest['files']
    rec: dict[str, Any] = {'device_id': registry.device_id, 'device_name': registry.device_name,
                           'availability': 'windows_local', 'source_url': urls[0], 'finished_at': _now()}
    content = [u for u, e in files.items() if u not in entry_urls and not e.get('empty')]
    if files and not content and state['status'] != 'refused':
        # Only landing pages so far: not material content, keep it ``downloading``.
        from .progress import report_downloading
        rep = report_downloading(registry, material_id, out, used_bytes(), source_url=urls[0])
        return {'material_id': material_id, **state, 'record_status': 'downloading', 'size_bytes': used_bytes(),
                'files': len(files), 'note': '只有入口页或空内容，保持 downloading', 'registered': bool(rep.get('reported')),
                **{k: v for k, v in rep.items() if k == 'register_error'}}
    if not files:
        rec.update(status='failed', error=(state.get('error') or '; '.join(
            f"{s['url']} {s['reason']}" for s in state['skipped']) or '没有取到文件')[:500])
    else:
        man_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        complete = state['status'] == 'done' and not state['skipped'] and not state.get('stopped_reason') \
            and not partial_note and not any(e.get('empty') for e in files.values())
        rec.update(status='downloaded' if complete else 'partial', path=out.as_posix(), size_bytes=used_bytes(),
                   manifest_path=manifest_path.as_posix(), manifest_sha256=man_sha)
        if not complete:
            note = partial_note or '只取到部分文件'
            if state['skipped']:
                note += '；未取到：' + '，'.join(f"{s['url']}（{s['reason']}）" for s in state['skipped'])
            if state.get('error'):
                note += f"；{state['error']}"
            rec['scope'] = {'items': len(content), 'entry_pages': len(files) - len(content), 'note': note[:1000]}
    if state['status'] == 'refused':
        return {'material_id': material_id, **state, 'registered': False}
    return {'material_id': material_id, **state, 'record_status': rec['status'], 'size_bytes': used_bytes(),
            'files': len(files), **register_and_verify(registry, material_id, rec)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.page_set',
                                 description='逐个下载一份素材的指定 URL（可续传、查/登记熔断、逐条登记）')
    ap.add_argument('material_id')
    ap.add_argument('urls', nargs='+')
    ap.add_argument('--partial', metavar='NOTE', help='只取一部分时写 scope 范围说明（登记 partial）')
    ap.add_argument('--gap', type=float, nargs=2, default=(10, 20))
    ap.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    ap.add_argument('--referer')
    ap.add_argument('--no-proxy', action='store_true', help='直连，不走系统代理（本地代理断 TLS 时用）')
    ap.add_argument('--expand', type=int, default=0, metavar='N',
                    help='从入口页（素材 url 与命令行第一个 URL）展开最多 N 个正文/附件链接')
    ap.add_argument('--expand-prefix', help='展开同站页面时只取这个路径前缀（默认入口页所在目录）')
    ap.add_argument('--render', action='store_true',
                    help='JS 渲染页且无可用数据接口时，用本机受控无头浏览器（DrissionPage）渲染后保存并展开链接')
    ap.add_argument('--system-ca', action='store_true', help='用 Windows 证书库校验（站点缺中间证书时用）')
    args = ap.parse_args(argv)
    from .codehub import CodeHubSkillDep

    if args.system_ca:
        import truststore
        truststore.inject_into_ssl()
    hub = CodeHubSkillDep()
    material = next((m for m in hub.materials() if m['id'] == args.material_id), None)
    if material is None:
        raise SystemExit(f'方案中没有 {args.material_id}')
    cap = first_batch_limit_bytes(material) or DEFAULT_CAP
    if len({site_name(u) for u in args.urls}) > 1:
        print('注意：多个站点的 URL 也按串行处理', flush=True)
    if args.render:
        session = RenderSession()
    else:
        session = requests.Session()
        session.trust_env = not args.no_proxy
    out = fetch_set(hub, args.material_id, args.urls, root=args.root, gap=tuple(args.gap), max_bytes=cap,
                    session=session,
                    partial_note=args.partial, headers={'Referer': args.referer} if args.referer else None,
                    entry_urls={u for u in (material.get('url'), args.urls[0] if args.expand else None) if u},
                    expand=args.expand, expand_prefix=args.expand_prefix)
    if args.render:
        session.close()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get('registered') else 1


if __name__ == '__main__':
    raise SystemExit(main())
