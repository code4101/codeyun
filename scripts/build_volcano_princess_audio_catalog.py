from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_GAME_ROOT = Path(r"D:\SteamLibrary\steamapps\common\VolcanoPrincess")
DEFAULT_REVERSE_ROOT = Path(r"D:\home\chenkunze\data\m2607火山的女儿逆向")
STEAM_MANIFEST = Path(r"D:\SteamLibrary\steamapps\appmanifest_1669980.acf")
SCHEMA_VERSION = 1


@dataclass(slots=True)
class AudioEntry:
    id: str
    path_id: int
    name: str
    category: str
    duration_seconds: float
    channels: int
    frequency_hz: int
    source_asset: str
    media_path: str | None
    media_bytes: int | None
    media_sha256: str | None
    export_status: str
    export_error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a reproducible Volcano Princess Unity AudioClip catalog."
    )
    parser.add_argument("--game-root", type=Path, default=DEFAULT_GAME_ROOT)
    parser.add_argument("--reverse-root", type=Path, default=DEFAULT_REVERSE_ROOT)
    parser.add_argument("--bitrate", default="128k")
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_stem(value: str, max_length: int = 72) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return (value or "unnamed")[:max_length].rstrip(" .")


def classify_audio(name: str, duration: float) -> str:
    if duration >= 30:
        return "music_or_ambience"
    if duration <= 1.2:
        return "short_clip"
    return "voice_or_effect"


def extract_build_id(manifest_text: str) -> str | None:
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest_text)
    return match.group(1) if match else None


def encode_mp3(wav_payload: bytes, output_path: Path, bitrate: str) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(output_path),
    ]
    completed = subprocess.run(command, input=wav_payload, capture_output=True)
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"ffmpeg exited with {completed.returncode}")


def render_html(catalog: dict[str, Any]) -> str:
    payload = json.dumps(catalog, ensure_ascii=False).replace("</", "<\\/")
    title = "火山的女儿 · 音频图鉴"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --bg:#f5f2ed; --card:#fffdfa; --ink:#2b2926; --muted:#77706a; --line:#e7dfd6; --accent:#9e493d; }}
    * {{ box-sizing:border-box }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif }}
    main {{ width:min(1180px,calc(100% - 32px)); margin:36px auto 72px }}
    h1 {{ margin:0 0 6px; font:700 28px/1.25 Georgia,"Microsoft YaHei",sans-serif }}
    .subtitle,.meta {{ color:var(--muted) }}
    .stats {{ display:flex; gap:10px; flex-wrap:wrap; margin:22px 0 }}
    .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width:145px }}
    .stat strong {{ display:block; font-size:20px }}
    .tools {{ display:grid; grid-template-columns:minmax(220px,1fr) 210px 130px; gap:10px; margin-bottom:14px }}
    input,select,button {{ width:100%; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--ink); padding:10px 12px; font:inherit }}
    button {{ cursor:pointer }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden }}
    th,td {{ padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle }}
    th {{ color:var(--muted); font-size:12px; letter-spacing:.03em; background:#faf7f2 }}
    tr:last-child td {{ border-bottom:0 }}
    .name {{ font-weight:600; max-width:390px; overflow-wrap:anywhere }}
    .tag {{ display:inline-block; padding:2px 8px; border-radius:999px; background:#f1e7df; color:var(--accent); white-space:nowrap }}
    audio {{ width:270px; height:34px }}
    .pager {{ display:flex; align-items:center; justify-content:center; gap:12px; margin:16px auto }}
    .pager button {{ width:auto; min-width:88px }}
    .empty {{ padding:48px; text-align:center; color:var(--muted) }}
    @media (max-width:760px) {{ .tools {{ grid-template-columns:1fr }} .optional {{ display:none }} audio {{ width:190px }} main {{ width:calc(100% - 20px); margin-top:20px }} }}
  </style>
</head>
<body><main>
  <h1>{html.escape(title)}</h1>
  <div class="subtitle">Unity AudioClip 静态采集 · Steam Build <span id="build"></span></div>
  <section class="stats" id="stats"></section>
  <section class="tools">
    <input id="query" placeholder="搜索名称或对象 ID…" autocomplete="off">
    <select id="category"><option value="">全部分类</option><option value="music_or_ambience">长音乐 / 环境</option><option value="voice_or_effect">语音 / 音效</option><option value="short_clip">短片段</option></select>
    <select id="pageSize"><option>40</option><option selected>80</option><option>160</option></select>
  </section>
  <div id="result"></div><div class="pager" id="pager"></div>
  <p class="meta">分类仅按时长做首轮中性分组，不把未验证的短语音误标成角色语音。每条记录可回链到 resources.assets 的 Path ID。</p>
</main>
<script id="catalog" type="application/json">{payload}</script>
<script>
const data=JSON.parse(document.querySelector('#catalog').textContent), labels={{music_or_ambience:'长音乐 / 环境',voice_or_effect:'语音 / 音效',short_clip:'短片段'}};
let page=1; const q=document.querySelector('#query'), category=document.querySelector('#category'), pageSize=document.querySelector('#pageSize');
document.querySelector('#build').textContent=data.source.build_id||'unknown';
const fmt=s=>{{const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=Math.round(s%60);return `${{h?`${{h}} 小时 `:''}}${{m}} 分 ${{x}} 秒`}};
document.querySelector('#stats').innerHTML=`<div class="stat"><strong>${{data.summary.entry_count.toLocaleString()}}</strong>音频条目</div><div class="stat"><strong>${{fmt(data.summary.duration_seconds)}}</strong>合计时长</div><div class="stat"><strong>${{data.summary.exported_count.toLocaleString()}}</strong>可播放文件</div><div class="stat"><strong>${{(data.summary.media_bytes/1024/1024).toFixed(1)}} MB</strong>预览体积</div>`;
function filtered(){{const needle=q.value.trim().toLocaleLowerCase(),cat=category.value;return data.entries.filter(x=>(!cat||x.category===cat)&&(!needle||x.name.toLocaleLowerCase().includes(needle)||String(x.path_id).includes(needle)))}}
function render(){{const rows=filtered(),size=Number(pageSize.value),pages=Math.max(1,Math.ceil(rows.length/size));page=Math.min(page,pages);const slice=rows.slice((page-1)*size,page*size);document.querySelector('#result').innerHTML=slice.length?`<table><thead><tr><th>名称</th><th class="optional">分组</th><th class="optional">时长</th><th>试听</th><th class="optional">源对象</th></tr></thead><tbody>${{slice.map(x=>`<tr><td class="name">${{esc(x.name)}}</td><td class="optional"><span class="tag">${{labels[x.category]}}</span></td><td class="optional">${{x.duration_seconds.toFixed(2)}} s</td><td>${{x.media_path?`<audio controls preload="none" src="${{x.browser_media_path}}"></audio>`:'导出失败'}}</td><td class="optional">${{x.path_id}}</td></tr>`).join('')}}</tbody></table>`:'<div class="empty">没有符合条件的音频</div>';document.querySelector('#pager').innerHTML=`<button id="prev" ${{page===1?'disabled':''}}>上一页</button><span>${{page}} / ${{pages}} · ${{rows.length}} 条</span><button id="next" ${{page===pages?'disabled':''}}>下一页</button>`;document.querySelector('#prev').onclick=()=>{{page--;render();scrollTo(0,0)}};document.querySelector('#next').onclick=()=>{{page++;render();scrollTo(0,0)}}}}
function esc(s){{const d=document.createElement('div');d.textContent=s;return d.innerHTML}}
[q,category,pageSize].forEach(el=>el.addEventListener(el===q?'input':'change',()=>{{page=1;render()}}));render();
</script></body></html>"""


def main() -> int:
    args = parse_args()
    try:
        import UnityPy  # type: ignore[import-not-found]
    except ImportError:
        print("UnityPy is required: uv run --with UnityPy python scripts/build_volcano_princess_audio_catalog.py", file=sys.stderr)
        return 2

    game_root = args.game_root.resolve()
    reverse_root = args.reverse_root.resolve()
    source_asset = game_root / "VolcanoPrincess_Data" / "resources.assets"
    source_res = game_root / "VolcanoPrincess_Data" / "resources.assets.resS"
    if not source_asset.is_file() or not source_res.is_file():
        raise FileNotFoundError(f"Unity resource pair not found below {game_root}")
    if not args.metadata_only and shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required for browser MP3 exports")

    raw_dir = reverse_root / "raw_inputs"
    manifest_dir = reverse_root / "manifests"
    catalog_dir = reverse_root / "parsed_configs" / "audio_catalog"
    media_dir = reverse_root / "media" / "audio"
    docs_dir = reverse_root / "docs"
    for path in (raw_dir, manifest_dir, catalog_dir, media_dir, docs_dir):
        path.mkdir(parents=True, exist_ok=True)

    manifest_text = STEAM_MANIFEST.read_text(encoding="utf-8", errors="replace")
    shutil.copy2(STEAM_MANIFEST, raw_dir / STEAM_MANIFEST.name)
    build_id = extract_build_id(manifest_text)
    generated_at = datetime.now(timezone.utc).isoformat()

    environment = UnityPy.load(str(source_asset))
    entries: list[AudioEntry] = []
    audio_objects = [obj for obj in environment.objects if obj.type.name == "AudioClip"]
    if args.limit is not None:
        audio_objects = audio_objects[: args.limit]

    for index, obj in enumerate(audio_objects, start=1):
        clip = obj.read()
        name = str(clip.m_Name)
        duration = float(getattr(clip, "m_Length", 0) or 0)
        filename = f"{obj.path_id}_{safe_stem(name)}.mp3"
        output_path = media_dir / filename
        media_path: str | None = None
        media_bytes: int | None = None
        media_sha256: str | None = None
        status = "metadata_only"
        error: str | None = None
        if not args.metadata_only:
            try:
                if args.force or not output_path.is_file() or output_path.stat().st_size == 0:
                    samples = clip.samples
                    if not samples:
                        raise RuntimeError("UnityPy returned no decoded samples")
                    wav_payload = next(iter(samples.values()))
                    encode_mp3(wav_payload, output_path, args.bitrate)
                media_path = output_path.relative_to(reverse_root).as_posix()
                media_bytes = output_path.stat().st_size
                media_sha256 = sha256_file(output_path)
                status = "exported"
            except Exception as exc:  # continue cataloging isolated malformed clips
                status = "failed"
                error = str(exc)
        entries.append(
            AudioEntry(
                id=f"resources.assets:{obj.path_id}",
                path_id=obj.path_id,
                name=name,
                category=classify_audio(name, duration),
                duration_seconds=round(duration, 6),
                channels=int(getattr(clip, "m_Channels", 0) or 0),
                frequency_hz=int(getattr(clip, "m_Frequency", 0) or 0),
                source_asset="VolcanoPrincess_Data/resources.assets",
                media_path=media_path,
                media_bytes=media_bytes,
                media_sha256=media_sha256,
                export_status=status,
                export_error=error,
            )
        )
        if index == 1 or index % 50 == 0 or index == len(audio_objects):
            print(f"[{index}/{len(audio_objects)}] {name}", flush=True)

    entry_dicts = []
    for entry in entries:
        row = asdict(entry)
        row["browser_media_path"] = (
            f"../../{entry.media_path}" if entry.media_path is not None else None
        )
        entry_dicts.append(row)
    exported = [entry for entry in entries if entry.export_status == "exported"]
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "app_id": "volcano_princess",
        "app_name": "火山的女儿",
        "generated_at": generated_at,
        "source": {
            "platform": "Steam",
            "steam_app_id": "1669980",
            "build_id": build_id,
            "engine": "Unity 6000.0.26f1",
            "architecture": "x86_64",
            "scripting_backend": "Mono",
            "game_root": str(game_root),
            "asset_path": str(source_asset),
        },
        "summary": {
            "entry_count": len(entries),
            "duration_seconds": round(sum(entry.duration_seconds for entry in entries), 6),
            "exported_count": len(exported),
            "failed_count": sum(entry.export_status == "failed" for entry in entries),
            "media_bytes": sum(entry.media_bytes or 0 for entry in exported),
            "category_counts": {
                category: sum(entry.category == category for entry in entries)
                for category in ("music_or_ambience", "voice_or_effect", "short_clip")
            },
        },
        "entries": entry_dicts,
    }
    (catalog_dir / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (catalog_dir / "catalog.tsv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(entries[0]).keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(asdict(entry) for entry in entries)
    (catalog_dir / "index.html").write_text(render_html(catalog), encoding="utf-8")

    source_manifest = {
        "schema_version": SCHEMA_VERSION,
        "app_id": "volcano_princess",
        "app_name": "火山的女儿",
        "batch_id": f"steam-build-{build_id or 'unknown'}",
        "created_at": generated_at,
        "source_version": build_id,
        "engine": "Unity 6000.0.26f1",
        "architecture": "x86_64",
        "protection_or_packaging": "Unity serialized assets with external resS payload",
        "tools": {"UnityPy": getattr(UnityPy, "__version__", "unknown"), "ffmpeg": args.bitrate},
        "source_files": [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "sha256": sha256_file(path),
            }
            for path in (STEAM_MANIFEST, source_asset, source_res)
        ],
        "generated_outputs": [
            str(catalog_dir / "catalog.json"),
            str(catalog_dir / "catalog.tsv"),
            str(catalog_dir / "index.html"),
            str(media_dir),
        ],
        "entity_counts": {"audio_clips": len(entries), "exported_audio": len(exported)},
        "known_gaps": [
            "首轮分类仅按时长中性分组，尚未建立角色、剧情台词与具体音效用途映射。",
            "当前仅处理 resources.assets 中的 AudioClip；后续逆向批次仍需检查其它 assets。",
        ],
        "validation_results": {
            "unity_object_scan": "passed",
            "browser_media_export": "passed" if len(exported) == len(entries) else "partial",
        },
    }
    (manifest_dir / f"steam_build_{build_id or 'unknown'}_audio.json").write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (docs_dir / "audio_catalog_notes.md").write_text(
        "# 火山的女儿音频图鉴\n\n"
        f"- Steam App ID：`1669980`\n- Build ID：`{build_id}`\n"
        "- 引擎：Unity `6000.0.26f1`，Mono\n"
        f"- AudioClip：`{len(entries)}` 条，总时长 `{catalog['summary']['duration_seconds']:.2f}` 秒\n"
        f"- 浏览入口：`{catalog_dir / 'index.html'}`\n\n"
        "首轮只做可追溯采集与中性时长分组；角色、台词、场景和用途关联留给后续批次。\n",
        encoding="utf-8",
    )
    print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
