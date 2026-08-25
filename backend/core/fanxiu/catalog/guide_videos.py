from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from backend.core.settings import get_settings


WANG_LAOMO_MID = 678872453
WANG_LAOMO_NAME = "凡人修仙传王老魔"
WANG_LAOMO_SPACE_URL = f"https://space.bilibili.com/{WANG_LAOMO_MID}"
WANG_LAOMO_BILIBILI_SOURCE_ID = f"bilibili:{WANG_LAOMO_MID}"
DOUYIN_FOLLOW_URL = "https://www.douyin.com/follow"
DOUYIN_OFFICIAL_NAME = "凡人修仙传人界篇"
DOUYIN_ALWAYS_INCLUDE = {"思瓜", WANG_LAOMO_NAME, "王老魔"}
GUIDE_VIDEO_SCHEMA_VERSION = 3

_SYNC_LOCK = threading.Lock()
_SYNC_THREAD: threading.Thread | None = None


def guide_video_snapshot_path() -> Path:
    return get_settings().data_dir / "fanxiu" / "guide-videos" / "catalog.json"


def _legacy_snapshot_path() -> Path:
    return get_settings().data_dir / "fanxiu" / "guide-videos" / "wang-laomo-videos.json"


def _empty_snapshot() -> dict[str, Any]:
    return {
        "schema_version": GUIDE_VIDEO_SCHEMA_VERSION,
        "status": "idle",
        "target_count": 0,
        "done_count": 0,
        "updated_at": 0.0,
        "error": "",
        "sources": [],
        "items": [],
    }


def _bilibili_source(*, status: str = "idle", target_count: int = 0, done_count: int = 0, error: str = "") -> dict[str, Any]:
    return {
        "source_id": WANG_LAOMO_BILIBILI_SOURCE_ID,
        "platform": "bilibili",
        "role": "clip",
        "identity_key": "wang_laomo",
        "uploader_id": str(WANG_LAOMO_MID),
        "uploader_name": WANG_LAOMO_NAME,
        "profile_url": WANG_LAOMO_SPACE_URL,
        "target_count": target_count,
        "done_count": done_count,
        "status": status,
        "error": error,
        "collections": [],
    }


def _normalize_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = _empty_snapshot()
    snapshot.update(payload)
    snapshot["schema_version"] = GUIDE_VIDEO_SCHEMA_VERSION
    snapshot["items"] = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    sources = [item for item in payload.get("sources") or [] if isinstance(item, dict)]
    legacy_source = payload.get("source")
    if not sources and isinstance(legacy_source, dict):
        sources = [
            {
                **_bilibili_source(
                    status=str(payload.get("status") or "idle"),
                    target_count=int(payload.get("target_count") or 0),
                    done_count=int(payload.get("done_count") or 0),
                    error=str(payload.get("error") or ""),
                ),
                "uploader_name": str(legacy_source.get("uploader_name") or WANG_LAOMO_NAME),
                "profile_url": str(legacy_source.get("space_url") or WANG_LAOMO_SPACE_URL),
            }
        ]
    snapshot["sources"] = sources
    return snapshot


def _research_payload(item_id: str, research: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    record = research.get(item_id)
    if record is None:
        return None
    public = dict(record)
    base = f"/api/fanxiu/wiki/guide-videos/research-file?item_id={item_id}&kind="
    public["media_url"] = f"{base}media" if record.get("local_video_path") else ""
    public["document_url"] = f"{base}document" if record.get("document_path") else ""
    public["transcript_url"] = f"{base}transcript" if record.get("transcript_path") else ""
    return public


def load_guide_video_snapshot(path: str | Path | None = None) -> dict[str, Any]:
    snapshot_path = Path(path) if path is not None else guide_video_snapshot_path()
    if not snapshot_path.is_file() and path is None and _legacy_snapshot_path().is_file():
        snapshot_path = _legacy_snapshot_path()
    if not snapshot_path.is_file():
        return _empty_snapshot()
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_snapshot()
    return _normalize_snapshot(payload) if isinstance(payload, dict) else _empty_snapshot()


def save_guide_video_snapshot(snapshot: dict[str, Any], path: str | Path | None = None) -> Path:
    snapshot_path = Path(path) if path is not None else guide_video_snapshot_path()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = snapshot_path.with_name(f".{snapshot_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp_path.write_text(json.dumps(_normalize_snapshot(snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, snapshot_path)
    return snapshot_path


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _parse_dynamic_video(item: dict[str, Any]) -> dict[str, Any] | None:
    if str(item.get("type") or "") != "DYNAMIC_TYPE_AV":
        return None
    archive = _nested(item, "modules", "module_dynamic", "major", "archive")
    author = _nested(item, "modules", "module_author")
    if not isinstance(archive, dict) or not isinstance(author, dict):
        return None
    bvid = str(archive.get("bvid") or "").strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
        return None
    stat = archive.get("stat") if isinstance(archive.get("stat"), dict) else {}
    uploader_name = str(author.get("name") or WANG_LAOMO_NAME).strip()
    return {
        "item_id": f"bilibili:{bvid}",
        "platform": "bilibili",
        "source_id": WANG_LAOMO_BILIBILI_SOURCE_ID,
        "source_role": "clip",
        "identity_key": "wang_laomo",
        "video_id": bvid,
        "bvid": bvid,
        "url": f"https://www.bilibili.com/video/{bvid}/",
        "title": str(archive.get("title") or "").strip(),
        "description": str(archive.get("desc") or "").strip(),
        "cover_url": str(archive.get("cover") or "").replace("http://", "https://", 1),
        "duration_text": str(archive.get("duration_text") or "").strip(),
        "play_text": str(stat.get("play") or "").strip(),
        "published_at": int(float(author.get("pub_ts") or 0)),
        "dynamic_id": str(item.get("id_str") or "").strip(),
        "uploader_id": str(author.get("mid") or WANG_LAOMO_MID),
        "uploader_mid": int(author.get("mid") or WANG_LAOMO_MID),
        "uploader_name": uploader_name,
    }


def _video_key(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or "").strip() or f"{item.get('platform') or 'bilibili'}:{item.get('video_id') or item.get('bvid') or ''}"


def _merge_video_items(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for item in [*previous, *current]:
        key = _video_key(item)
        if key and not key.endswith(":"):
            normalized = dict(item)
            normalized.setdefault("item_id", key)
            normalized.setdefault("video_id", str(item.get("bvid") or ""))
            normalized.setdefault("platform", "bilibili" if item.get("bvid") else "")
            normalized.setdefault("source_id", WANG_LAOMO_BILIBILI_SOURCE_ID if item.get("bvid") else "")
            normalized.setdefault("source_role", "clip" if item.get("bvid") else "guide")
            by_key[key] = normalized
    return sorted(
        by_key.values(),
        key=lambda item: (int(item.get("published_at") or 0), _video_key(item)),
        reverse=True,
    )


def _browser_fetch_json(tab: Any, url: str, *, platform: str) -> dict[str, Any]:
    result = tab.run_js(
        "return fetch(arguments[0], {credentials: 'include'}).then(async response => "
        "({status: response.status, payload: await response.json()}))",
        url,
    )
    if not isinstance(result, dict) or int(result.get("status") or 0) != 200:
        raise RuntimeError(f"{platform} 接口请求失败：HTTP {result.get('status') if isinstance(result, dict) else 'unknown'}")
    payload = result.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError(f"{platform} 接口响应格式错误")
    return payload


def _reported_video_count(tab: Any) -> int:
    body = tab.ele("tag:body")
    text = str(body.text if body else "")
    match = re.search(r"(?:视频|作品)\s*(\d+)", text)
    return int(match.group(1)) if match else 0


def collect_wang_laomo_video_catalog(
    *,
    browser: Any | None = None,
    snapshot_path: str | Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    save_snapshot: bool = True,
) -> dict[str, Any]:
    """Collect Wang Laomo's complete Bilibili history through the shared DP browser."""

    if browser is None:
        from DrissionPage import Chromium

        browser = Chromium()

    previous = load_guide_video_snapshot(snapshot_path)
    previous_items = [item for item in previous.get("items") or [] if item.get("platform", "bilibili") == "bilibili"]
    collected: list[dict[str, Any]] = []
    seen_offsets: set[str] = set()
    offset = ""
    page_index = 0
    tab = browser.new_tab()

    def write_progress(*, status: str, target_count: int, error: str = "") -> dict[str, Any]:
        merged = _merge_video_items(collected, previous_items if status in {"running", "error"} else [])
        source = _bilibili_source(
            status=status,
            target_count=max(int(target_count or 0), len(merged)),
            done_count=len(collected) if status == "running" else len(merged),
            error=error,
        )
        snapshot = {
            **_empty_snapshot(),
            "status": status,
            "target_count": source["target_count"],
            "done_count": source["done_count"],
            "updated_at": time.time(),
            "error": error,
            "sources": [source],
            "items": merged,
        }
        if save_snapshot:
            save_guide_video_snapshot(snapshot, snapshot_path)
        if progress_callback is not None:
            progress_callback(snapshot)
        return snapshot

    target_count = len(previous_items)
    try:
        if not tab.get(f"{WANG_LAOMO_SPACE_URL}/dynamic", timeout=25):
            raise RuntimeError("无法打开王老魔的 Bilibili 空间")
        target_count = _reported_video_count(tab) or target_count
        write_progress(status="running", target_count=target_count)

        while page_index < 500:
            params = {"host_mid": WANG_LAOMO_MID}
            if offset:
                params["offset"] = offset
            url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?" + urlencode(params)
            payload = _browser_fetch_json(tab, url, platform="Bilibili")
            if int(payload.get("code") or 0) != 0:
                raise RuntimeError(f"Bilibili 空间接口失败：{payload.get('message') or '未知错误'}")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            for raw_item in data.get("items") or []:
                if isinstance(raw_item, dict):
                    parsed = _parse_dynamic_video(raw_item)
                    if parsed is not None:
                        collected.append(parsed)
            page_index += 1
            write_progress(status="running", target_count=target_count)
            next_offset = str(data.get("offset") or "").strip()
            if not data.get("has_more") or not next_offset or next_offset in seen_offsets:
                break
            seen_offsets.add(next_offset)
            offset = next_offset
        else:
            raise RuntimeError("Bilibili 空间分页超过安全上限")
        return write_progress(status="done", target_count=len(_merge_video_items(collected, [])))
    except Exception as exc:
        write_progress(status="error", target_count=target_count, error=str(exc))
        raise
    finally:
        tab.close()


def _douyin_role(user: dict[str, Any]) -> str:
    name = str(user.get("nickname") or "").strip()
    signature = str(user.get("signature") or "")
    if name == DOUYIN_OFFICIAL_NAME:
        return "official"
    if "叶钦" in name:
        return "original"
    if name in {WANG_LAOMO_NAME, "王老魔"}:
        return "clip"
    if name in DOUYIN_ALWAYS_INCLUDE or any(marker in f"{name}\n{signature}" for marker in ("凡人修仙", "凡修", "人界篇")):
        return "guide"
    return ""


def _douyin_identity_key(name: str) -> str:
    if "叶钦" in name:
        return "ye_qin"
    if "王老魔" in name:
        return "wang_laomo"
    if name == DOUYIN_OFFICIAL_NAME:
        return "official"
    return f"douyin:{name}"


def _find_resource_url(tab: Any, marker: str) -> str:
    urls = tab.run_js("return performance.getEntriesByType('resource').map(item => item.name)") or []
    return next((str(url) for url in reversed(urls) if marker in str(url)), "")


def _load_douyin_followings(tab: Any) -> list[dict[str, Any]]:
    tab.run_js("performance.clearResourceTimings()")
    if not tab.get(DOUYIN_FOLLOW_URL, timeout=25):
        raise RuntimeError("无法打开已登录的抖音关注页")
    deadline = time.monotonic() + 15
    request_url = ""
    while time.monotonic() < deadline:
        request_url = _find_resource_url(tab, "/user/following/list/")
        if request_url:
            break
        time.sleep(0.25)
    if not request_url:
        raise RuntimeError("抖音关注列表未加载，请确认 DP 浏览器仍保持登录")
    payload = _browser_fetch_json(tab, request_url, platform="抖音")
    if int(payload.get("status_code") or 0) != 0:
        raise RuntimeError(f"抖音关注列表接口失败：{payload.get('status_msg') or '未知错误'}")
    return [user for user in payload.get("followings") or [] if isinstance(user, dict)]


_EXTRACT_DOUYIN_CARDS_JS = r"""
const authorName = arguments[0];
const result = [];
for (const anchor of document.querySelectorAll('a[href*="/video/"]')) {
  const match = (anchor.getAttribute('href') || '').match(/\/video\/(\d+)/);
  const image = anchor.querySelector('img[alt]');
  if (!match || !image) continue;
  const alt = image.getAttribute('alt') || '';
  if (!(alt.startsWith(authorName + '：') || alt.startsWith(authorName + ':'))) continue;
  const paragraphs = [...anchor.querySelectorAll('p')].map(node => (node.innerText || '').trim()).filter(Boolean);
  const title = paragraphs[0] || (alt.includes('：') ? alt.split('：').slice(1).join('：') : alt);
  const lines = (anchor.innerText || '').split('\n').map(item => item.trim()).filter(Boolean);
  const playText = lines.find(item => item !== '置顶' && item !== title && !paragraphs.includes(item)) || '';
  result.push({
    video_id: match[1],
    url: 'https://www.douyin.com/video/' + match[1],
    title,
    cover_url: image.currentSrc || image.src || '',
    play_text: playText,
    is_pinned: lines.includes('置顶'),
  });
}
return result;
"""


def _extract_douyin_cards(tab: Any, source: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = tab.run_js(_EXTRACT_DOUYIN_CARDS_JS, source["uploader_name"]) or []
    items: list[dict[str, Any]] = []
    for rank, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        video_id = str(raw.get("video_id") or "")
        if not video_id.isdigit():
            continue
        items.append(
            {
                "item_id": f"douyin:{video_id}",
                "platform": "douyin",
                "source_id": source["source_id"],
                "source_role": source["role"],
                "identity_key": source["identity_key"],
                "video_id": video_id,
                "bvid": "",
                "url": str(raw.get("url") or f"https://www.douyin.com/video/{video_id}"),
                "title": str(raw.get("title") or "").strip(),
                "description": "",
                "cover_url": str(raw.get("cover_url") or ""),
                "duration_text": "",
                "play_text": str(raw.get("play_text") or "").strip(),
                "published_at": int(video_id) >> 32,
                "dynamic_id": "",
                "uploader_id": source["uploader_id"],
                "uploader_mid": 0,
                "uploader_name": source["uploader_name"],
                "is_pinned": bool(raw.get("is_pinned")),
                "source_rank": rank,
            }
        )
    return _merge_video_items(items, [])


def _scroll_douyin_profile(tab: Any, source: dict[str, Any], *, max_scrolls: int = 300) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    stagnant_rounds = 0
    for _ in range(max_scrolls):
        current = _extract_douyin_cards(tab, source)
        merged = _merge_video_items(current, collected)
        if len(merged) > len(collected):
            collected = merged
            stagnant_rounds = 0
        else:
            stagnant_rounds += 1

        scroll_state = tab.run_js(
            "const el=document.querySelector('.route-scroll-container');"
            "if(!el)return null; const before={top:el.scrollTop,height:el.scrollHeight};"
            "el.scrollTop=el.scrollHeight; el.dispatchEvent(new Event('scroll',{bubbles:true}));"
            "return {...before,after:el.scrollTop};"
        )
        if not isinstance(scroll_state, dict):
            raise RuntimeError("抖音作品页缺少可滚动容器")

        before_count = len(collected)
        before_height = int(scroll_state.get("height") or 0)
        deadline = time.monotonic() + 6
        changed = False
        while time.monotonic() < deadline:
            time.sleep(0.25)
            next_items = _extract_douyin_cards(tab, source)
            height = int(
                tab.run_js("const el=document.querySelector('.route-scroll-container'); return el ? el.scrollHeight : 0") or 0
            )
            if len(_merge_video_items(next_items, collected)) > before_count or height > before_height:
                collected = _merge_video_items(next_items, collected)
                changed = True
                break
        if not changed and stagnant_rounds >= 2:
            return collected
    raise RuntimeError(f"{source['uploader_name']} 作品分页超过安全上限")


def _extract_douyin_collections(tab: Any, profile_url: str) -> list[dict[str, Any]]:
    tab.get(f"{profile_url}?showSubTab=compilation", timeout=25)
    deadline = time.monotonic() + 10
    collections: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        raw_items = tab.run_js(
            "return [...document.querySelectorAll('a[href*=\"/collection/\"]')].map(a=>({url:a.href,text:(a.innerText||'').trim()}))"
        ) or []
        if raw_items:
            for raw in raw_items:
                text = str(raw.get("text") or "").strip()
                episode_match = re.search(r"更新至\s*(\d+)\s*集", text)
                play_match = re.search(r"([^\s]+)\s*播放", text)
                title = text.splitlines()[0].strip() if text else ""
                collections.append(
                    {
                        "collection_id": str(raw.get("url") or "").split("/collection/")[-1].split("/")[0],
                        "title": title,
                        "url": str(raw.get("url") or "").split("?")[0],
                        "episode_count": int(episode_match.group(1)) if episode_match else 0,
                        "play_text": play_match.group(1) if play_match else "",
                    }
                )
            break
        time.sleep(0.25)
    return list({item["collection_id"]: item for item in collections if item["collection_id"]}.values())


def collect_douyin_guide_catalog(
    *,
    browser: Any | None = None,
    progress_callback: Callable[[dict[str, Any], list[dict[str, Any]]], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if browser is None:
        from DrissionPage import Chromium

        browser = Chromium()
    tab = browser.new_tab()
    all_items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    try:
        followings = _load_douyin_followings(tab)
        candidates = [(user, _douyin_role(user)) for user in followings]
        candidates = [(user, role) for user, role in candidates if role]
        for user, role in candidates:
            name = str(user.get("nickname") or "").strip()
            sec_uid = str(user.get("sec_uid") or "").strip()
            if not sec_uid:
                continue
            profile_url = f"https://www.douyin.com/user/{sec_uid}"
            source = {
                "source_id": f"douyin:{sec_uid}",
                "platform": "douyin",
                "role": role,
                "identity_key": _douyin_identity_key(name),
                "uploader_id": sec_uid,
                "uploader_name": name,
                "profile_url": profile_url,
                "target_count": int(user.get("aweme_count") or 0),
                "done_count": 0,
                "status": "running",
                "error": "",
                "collections": [],
            }
            sources.append(source)
            if progress_callback:
                progress_callback(source, all_items)
            try:
                tab.get(profile_url, timeout=25)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not _extract_douyin_cards(tab, source):
                    time.sleep(0.25)
                items = _scroll_douyin_profile(tab, source)
                source["collections"] = _extract_douyin_collections(tab, profile_url)
                source["done_count"] = len(items)
                source["target_count"] = max(source["target_count"], len(items))
                source["status"] = "done"
                all_items = _merge_video_items(items, all_items)
            except Exception as exc:
                source["status"] = "error"
                source["error"] = str(exc)
            if progress_callback:
                progress_callback(source, all_items)
        return sources, all_items
    finally:
        tab.close()


def collect_guide_video_catalog(
    *, browser: Any | None = None, snapshot_path: str | Path | None = None
) -> dict[str, Any]:
    if browser is None:
        from DrissionPage import Chromium

        browser = Chromium()
    previous = load_guide_video_snapshot(snapshot_path)
    sources: list[dict[str, Any]] = []
    current_items: list[dict[str, Any]] = []

    def write_progress() -> dict[str, Any]:
        errors = [f"{source['uploader_name']}：{source['error']}" for source in sources if source.get("error")]
        items = _merge_video_items(current_items, list(previous.get("items") or []))
        snapshot = {
            **_empty_snapshot(),
            "status": "running",
            "target_count": sum(int(source.get("target_count") or 0) for source in sources),
            "done_count": sum(int(source.get("done_count") or 0) for source in sources),
            "updated_at": time.time(),
            "error": "\n".join(errors),
            "sources": sources,
            "items": items,
        }
        save_guide_video_snapshot(snapshot, snapshot_path)
        return snapshot

    try:
        bilibili = collect_wang_laomo_video_catalog(browser=browser, save_snapshot=False)
        sources.extend(bilibili.get("sources") or [])
        current_items = _merge_video_items(list(bilibili.get("items") or []), current_items)
    except Exception as exc:
        previous_source = next(
            (source for source in previous.get("sources") or [] if source.get("source_id") == WANG_LAOMO_BILIBILI_SOURCE_ID),
            _bilibili_source(),
        )
        sources.append({**previous_source, "status": "error", "error": str(exc)})
    write_progress()

    def douyin_progress(_source: dict[str, Any], items: list[dict[str, Any]]) -> None:
        nonlocal current_items
        if not any(source.get("source_id") == _source.get("source_id") for source in sources):
            sources.append(_source)
        current_items = _merge_video_items(items, current_items)
        write_progress()

    douyin_sources, douyin_items = collect_douyin_guide_catalog(browser=browser, progress_callback=douyin_progress)
    for source in douyin_sources:
        if not any(current.get("source_id") == source.get("source_id") for current in sources):
            sources.append(source)
    current_items = _merge_video_items(douyin_items, current_items)
    errors = [f"{source['uploader_name']}：{source['error']}" for source in sources if source.get("error")]
    final = {
        **_empty_snapshot(),
        "status": "error" if errors else "done",
        "target_count": sum(int(source.get("target_count") or 0) for source in sources),
        "done_count": sum(int(source.get("done_count") or 0) for source in sources),
        "updated_at": time.time(),
        "error": "\n".join(errors),
        "sources": sources,
        "items": _merge_video_items(current_items, []),
    }
    save_guide_video_snapshot(final, snapshot_path)
    return final


def query_guide_videos(
    *,
    query: str = "",
    source_id: str = "",
    platform: str = "",
    role: str = "",
    page: int = 1,
    page_size: int = 20,
    snapshot_path: str | Path | None = None,
    research_snapshot_path: str | Path | None = None,
    download_snapshot_path: str | Path | None = None,
) -> dict[str, Any]:
    from backend.core.fanxiu.catalog.guide_video_downloads import (
        download_record_by_item_id,
        load_guide_video_download_snapshot,
    )
    from backend.core.fanxiu.catalog.guide_video_research import (
        load_guide_video_research_snapshot,
        research_by_item_id,
    )

    snapshot = load_guide_video_snapshot(snapshot_path)
    research_snapshot = load_guide_video_research_snapshot(research_snapshot_path)
    research = research_by_item_id(research_snapshot)
    download_snapshot = load_guide_video_download_snapshot(download_snapshot_path)
    downloads = download_record_by_item_id(download_snapshot)
    needle = str(query or "").strip().casefold()
    items = [
        {
            **item,
            "research": _research_payload(_video_key(item), research),
            "download": downloads.get(_video_key(item)),
        }
        for item in snapshot.get("items") or []
    ]
    if source_id:
        items = [item for item in items if str(item.get("source_id") or "") == source_id]
    if platform:
        items = [item for item in items if str(item.get("platform") or "") == platform]
    if role:
        items = [item for item in items if str(item.get("source_role") or "") == role]
    if needle:
        items = [
            item
            for item in items
            if needle
            in "\n".join(
                str(item.get(key) or "")
                for key in ("title", "description", "video_id", "bvid", "uploader_name", "platform")
            ).casefold()
        ]
    normalized_page = max(int(page or 1), 1)
    normalized_page_size = min(max(int(page_size or 20), 1), 200)
    start = (normalized_page - 1) * normalized_page_size
    return {
        **{key: value for key, value in snapshot.items() if key != "items"},
        "query": str(query or "").strip(),
        "source_id": source_id,
        "platform": platform,
        "role": role,
        "page": normalized_page,
        "page_size": normalized_page_size,
        "total": len(items),
        "research_count": int(research_snapshot.get("done_count") or 0),
        "download_status": str(download_snapshot.get("status") or "idle"),
        "download_target_count": int(download_snapshot.get("target_count") or 0),
        "download_done_count": int(download_snapshot.get("done_count") or 0),
        "download_failed_count": int(download_snapshot.get("failed_count") or 0),
        "download_current_item_id": str(download_snapshot.get("current_item_id") or ""),
        "items": items[start : start + normalized_page_size],
    }


def is_guide_video_sync_running() -> bool:
    with _SYNC_LOCK:
        return _SYNC_THREAD is not None and _SYNC_THREAD.is_alive()


def start_guide_video_sync() -> dict[str, Any]:
    global _SYNC_THREAD
    with _SYNC_LOCK:
        if _SYNC_THREAD is not None and _SYNC_THREAD.is_alive():
            return query_guide_videos()
        previous = load_guide_video_snapshot()
        save_guide_video_snapshot({**previous, "status": "running", "done_count": 0, "updated_at": time.time(), "error": ""})

        def worker() -> None:
            try:
                collect_guide_video_catalog()
            except Exception as exc:
                snapshot = load_guide_video_snapshot()
                save_guide_video_snapshot({**snapshot, "status": "error", "updated_at": time.time(), "error": str(exc)})

        _SYNC_THREAD = threading.Thread(target=worker, name="fanxiu-guide-video-sync", daemon=True)
        _SYNC_THREAD.start()
    return query_guide_videos()
