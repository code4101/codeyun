"""Check Bilibili research access in an existing DrissionPage browser.

Uses one existing Bilibili tab and page-origin fetch with its normal login state.
Does not launch Chrome, extract credentials, navigate existing tabs, download
media, or change account state. Other tabs are left untouched. API failures are
reported rather than bypassed. Results are observations, not video analysis.

uv run python scripts/probe_bilibili_access.py --address 127.0.0.1:9222
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / "xlproject" / "src")]
import xlproject.loadenv  # noqa: E402,F401

from backend.core.media_download.bilibili import parse_bvid  # noqa: E402
from backend.core.temp_paths import codeyun_temp_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="127.0.0.1:9222")
    parser.add_argument("--bvid", default="BV1KBZkB6EJF")
    parser.add_argument("--keyword", default="大模型 横评")
    args = parser.parse_args()
    bvid = parse_bvid(args.bvid)
    from DrissionPage import Chromium, ChromiumOptions

    browser = Chromium(ChromiumOptions(read_file=False).set_address(args.address).existing_only())
    tabs = [tab for tab in browser.get_tabs()
            if (urlparse(tab.url).hostname or "") in {"www.bilibili.com", "bilibili.com"}]
    if not tabs:
        raise SystemExit("现有 DP 浏览器中没有 B 站标签页；请在该浏览器打开并登录 B 站。")
    tab = tabs[0]

    def get_json(url: str, *, authenticated: bool = True) -> dict:
        # Arguments are passed as values; no URL or data is interpolated into JS.
        return tab.run_js("return fetch(arguments[0],{credentials:arguments[1]})"
                          ".then(async r=>({http_status:r.status,payload:await r.json()}));",
                          url, "include" if authenticated else "omit")

    def api(path: str, **params) -> dict:
        return get_json("https://api.bilibili.com" + path + ("?" + urlencode(params) if params else ""))

    login = api("/x/web-interface/nav")
    nav = login["payload"]
    report = {"browser_address": args.address, "tab_url": tab.url,
              "logged_in": bool(nav.get("data", {}).get("isLogin")),
              "login_code": nav.get("code"), "bvid": bvid}
    search = api("/x/web-interface/search/type", search_type="video", keyword=args.keyword, page=1)
    report["search"] = {"http_status": search["http_status"], "code": search["payload"].get("code"),
                        "items": search["payload"].get("data", {}).get("result", [])}
    view = api("/x/web-interface/view", bvid=bvid)
    report["video_code"] = view["payload"].get("code")
    root = codeyun_temp_root("bilibili-dp-access", bvid)
    if view["payload"].get("code") == 0:
        video = view["payload"]["data"]
        report["video"] = {k: video.get(k) for k in ["bvid", "title", "duration", "pubdate", "owner", "stat", "pages"]}
        player = api("/x/player/wbi/v2", aid=video["aid"], cid=video["cid"])
        report["player_code"] = player["payload"].get("code")
        subtitles = (player["payload"].get("data") or {}).get("subtitle", {}).get("subtitles", [])
        report["subtitles"] = [{k: sub.get(k) for k in ["id", "lan", "lan_doc", "ai_type"]} for sub in subtitles]
        chosen = next((sub for sub in subtitles if sub.get("lan") in {"zh-CN", "zh-Hans", "ai-zh"}), None)
        if chosen:
            url = chosen["subtitle_url"]
            url = "https:" + url if url.startswith("//") else url
            # Fetch only the subtitle URL actually returned by the player.
            # The subtitle CDN uses a returned URL and does not need the site's
            # account cookies; credentialed cross-origin fetch may be rejected.
            subtitle = get_json(url, authenticated=False)
            path = root / "subtitle.zh.json"
            path.write_text(json.dumps(subtitle["payload"], ensure_ascii=False, indent=2), encoding="utf-8")
            report["subtitle_segments"] = len(subtitle["payload"].get("body", []))
            report["subtitle_path"] = str(path)
        comments = api("/x/v2/reply/main", oid=video["aid"], type=1, mode=3)
        data = comments["payload"].get("data") or {}
        report["comments"] = {"http_status": comments["http_status"], "code": comments["payload"].get("code"),
                              "returned_count": len(data.get("replies") or []),
                              "reported_total": (data.get("cursor") or {}).get("all_count")}
    path = root / "access.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in {"video", "search"}}
    summary["search"] = {k: v for k, v in report["search"].items() if k != "items"}
    summary["search"]["returned_count"] = len(report["search"]["items"])
    summary["output"] = str(path)
    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
