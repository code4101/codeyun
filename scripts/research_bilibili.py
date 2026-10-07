"""Archive Bilibili search, timed subtitles, and playable media via logged-in DP.

Attach only to an existing browser; never launch or change login state. Search
results are candidates, not content evidence. Video analysis uses archived
subtitles and frames. Downloads use CodeYun's public media API with its dedup.

uv run python scripts/research_bilibili.py --root-dir C:/path/research --keyword 大模型横评
uv run python scripts/research_bilibili.py --root-dir C:/path/research --bvid BV... --download
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / "xlproject" / "src")]
import xlproject.loadenv  # noqa: E402,F401
from backend.core.media_download import download_bilibili_media, parse_bvid  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="127.0.0.1:9222")
    parser.add_argument("--root-dir", type=Path, required=True)
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--bvid", action="append", default=[])
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--frame", action="append", type=float, default=[],
                        help="Extract this video time (seconds); repeat for multiple frames")
    args = parser.parse_args()
    if not (args.keyword or args.bvid):
        parser.error("Provide --keyword or --bvid")
    from DrissionPage import Chromium, ChromiumOptions
    browser = Chromium(ChromiumOptions(read_file=False).set_address(args.address).existing_only())
    tab = next((t for t in browser.get_tabs() if urlparse(t.url).hostname in
                {"www.bilibili.com", "bilibili.com"}), None)
    if tab is None:
        raise SystemExit("Existing browser has no Bilibili tab")
    root = args.root_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)

    def fetch(url: str, authenticated: bool = True) -> dict:
        result = tab.run_js("return fetch(arguments[0],{credentials:arguments[1]})"
                            ".then(async r=>({status:r.status,data:await r.json()}));",
                            url, "include" if authenticated else "omit")
        if result["status"] != 200:
            raise RuntimeError(f"HTTP {result['status']}: {urlparse(url).path}")
        return result["data"]

    def api(path: str, **params) -> dict:
        data = fetch("https://api.bilibili.com" + path + "?" + urlencode(params))
        if data.get("code") != 0:
            raise RuntimeError(f"{path}: {data.get('code')} {data.get('message')}")
        return data["data"]

    def save(path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if not api("/x/web-interface/nav").get("isLogin"):
        raise SystemExit("Bilibili tab is not logged in")
    stamp = datetime.now(timezone.utc).isoformat()
    if args.keyword:
        results = []
        for keyword in args.keyword:
            data = api("/x/web-interface/search/type", search_type="video", keyword=keyword, page=1)
            items = []
            for item in data.get("result", []):
                if not item.get("bvid"):
                    continue
                entry = {k: item.get(k) for k in ["bvid", "author", "duration", "pubdate", "play", "description"]}
                entry["title"] = html.unescape(re.sub("<[^>]+>", "", item["title"]))
                items.append(entry)
            results.append({"keyword": keyword, "items": items})
            print(json.dumps({"keyword": keyword, "candidates": len(items)}, ensure_ascii=True), flush=True)
        save(root / "search.json", {"retrieved_at": stamp, "results": results})
    for supplied in args.bvid:
        bvid = parse_bvid(supplied)
        directory = root / bvid
        directory.mkdir(exist_ok=True)
        video = api("/x/web-interface/view", bvid=bvid)
        record = {"retrieved_at": stamp, "url": f"https://www.bilibili.com/video/{bvid}/",
                  **{k: video.get(k) for k in ["bvid", "title", "desc", "duration", "pubdate", "owner", "stat", "pages"]},
                  "subtitle_files": []}
        previous = directory / "metadata.json"
        if previous.is_file():
            old_record = json.loads(previous.read_text(encoding="utf-8"))
            old_media = old_record.get("media")
            if old_media and Path(old_media["path"]).is_file():
                record["media"] = old_media
            if old_record.get("analysis"):
                record["analysis"] = old_record["analysis"]
        for part in video["pages"]:
            player = api("/x/player/wbi/v2", aid=video["aid"], cid=part["cid"])
            available = player.get("subtitle", {}).get("subtitles", [])
            chosen = next((s for s in available if s.get("lan") in {"zh-CN", "zh-Hans", "ai-zh"}), None)
            if chosen:
                url = chosen["subtitle_url"]
                data = fetch("https:" + url if url.startswith("//") else url, False)
                filename = f"subtitle.p{part['page']}.zh.json"
                save(directory / filename, data)
                lines = [f"[{s['from']:.2f}–{s['to']:.2f}] {s['content']}" for s in data.get("body", [])]
                (directory / f"transcript.p{part['page']}.txt").write_text("\n".join(lines), encoding="utf-8")
                record["subtitle_files"].append({"file": filename, "language": chosen["lan"],
                                                "segments": len(lines), "part": part["page"]})
        save(directory / "metadata.json", record)
        print(json.dumps({k: record[k] for k in ["bvid", "title", "duration", "subtitle_files"]}, ensure_ascii=True), flush=True)
        if args.download:
            if len(video["pages"]) != 1:
                raise RuntimeError("Multipart media requires explicit part handling; subtitles archived")
            downloaded = download_bilibili_media(record["url"], root_dir=root, browser=browser,
                                                 log=lambda message: print(message, flush=True))
            record["media"] = {"path": str(downloaded.video_path), "duration": downloaded.duration,
                               "width": downloaded.width, "height": downloaded.height,
                               "reused": downloaded.reused}
            save(directory / "metadata.json", record)
            print(json.dumps(record["media"], ensure_ascii=True), flush=True)
        if args.frame:
            media = record.get("media")
            if not media:
                raise RuntimeError("Frames require archived media; use --download")
            out = directory / "frames"
            out.mkdir(exist_ok=True)
            frame_index = directory / "frames.json"
            frames = json.loads(frame_index.read_text(encoding="utf-8")) if frame_index.is_file() else []
            for seconds in args.frame:
                if not 0 <= seconds < media["duration"]:
                    raise ValueError(f"Frame outside media duration: {seconds}")
                name = f"{int(seconds):04d}" if seconds.is_integer() else f"{seconds:08.3f}"
                filename = f"frames/{name}.jpg"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(seconds),
                                "-i", media["path"], "-frames:v", "1", "-vf", "scale=1920:-2",
                                "-y", str(directory / filename)], check=True)
                frames = [f for f in frames if f["time"] != seconds]
                frames.append({"time": seconds, "file": filename})
            save(frame_index, sorted(frames, key=lambda f: f["time"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
