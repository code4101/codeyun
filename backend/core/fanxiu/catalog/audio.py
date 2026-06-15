from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pyxllib.file.game_assets import extract_wwise_wem_entries, parse_wwise_bnk_chunks, parse_wwise_didx_entries

from backend.core.fanxiu.catalog.resources import FanxiuResourceError, resolve_fanxiu_export_root, resolve_fanxiu_resource_root


FANXIU_VGMSTREAM_CLI_ENV = "FANXIU_VGMSTREAM_CLI"
FANXIU_FFMPEG_ENV = "FANXIU_FFMPEG"
DEFAULT_VGMSTREAM_CLI = Path(r"D:\tools\vgmstream\nightly\vgmstream-cli.exe")
_AUDIO_MEDIA_SUFFIXES = {".mp3"}


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _audio_kind(path: Path) -> str:
    name = path.stem.lower()
    if name.startswith("bgm_"):
        return "bgm"
    if name.startswith("amb_"):
        return "ambient"
    if name.startswith("vo_") or name.startswith("voice"):
        return "voice"
    if name.startswith("ui_") or "ui" in name:
        return "ui"
    if name.startswith("sfx_") or "effect" in name or name.startswith("eff_"):
        return "sfx"
    return "audio"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_path_part(value: Any, fallback: str = "asset") -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "").strip()).strip("._")
    return text[:96] if text else fallback


def _resolve_vgmstream_cli(path: str | Path | None = None) -> Path:
    value = path or os.environ.get(FANXIU_VGMSTREAM_CLI_ENV) or DEFAULT_VGMSTREAM_CLI
    resolved = Path(value).expanduser().resolve()
    if not resolved.is_file():
        raise FanxiuResourceError(f"vgmstream-cli 不存在：{resolved}")
    return resolved


def _resolve_ffmpeg(path: str | Path | None = None) -> str:
    value = str(path or os.environ.get(FANXIU_FFMPEG_ENV) or "").strip()
    if value:
        resolved = Path(value).expanduser().resolve()
        if not resolved.is_file():
            raise FanxiuResourceError(f"ffmpeg 不存在：{resolved}")
        return str(resolved)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FanxiuResourceError("找不到 ffmpeg，请先安装或设置 FANXIU_FFMPEG")
    return ffmpeg


def _run_command(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _read_vgmstream_info(vgmstream_cli: Path, wem_path: Path) -> dict[str, Any]:
    result = _run_command([str(vgmstream_cli), "-I", str(wem_path)], timeout=60)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "vgmstream metadata failed").strip())
    return json.loads(result.stdout)


def _decode_wem_to_wav(vgmstream_cli: Path, wem_path: Path, wav_path: Path) -> None:
    result = _run_command([str(vgmstream_cli), "-i", "-o", str(wav_path), str(wem_path)], timeout=300)
    if result.returncode != 0 or not wav_path.is_file():
        raise RuntimeError((result.stderr or result.stdout or "vgmstream decode failed").strip())


def _convert_wav_to_mp3(ffmpeg_path: str, wav_path: Path, mp3_path: Path, *, mp3_quality: int) -> None:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_command(
        [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            str(mp3_quality),
            str(mp3_path),
        ],
        timeout=300,
    )
    if result.returncode != 0 or not mp3_path.is_file():
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg mp3 conversion failed").strip())


def _mp3_relative_output_path(relative_bank_path: str, wem_id: Any) -> Path:
    source = Path(relative_bank_path).with_suffix("")
    safe_parts = [_safe_path_part(part) for part in source.parts]
    return Path(*safe_parts) / f"{_safe_path_part(wem_id, 'wem')}.mp3"


def _duration_seconds(info: dict[str, Any]) -> float:
    samples = float(info.get("playSamples") or info.get("numberOfSamples") or 0)
    sample_rate = float(info.get("sampleRate") or 0)
    return round(samples / sample_rate, 6) if samples > 0 and sample_rate > 0 else 0.0


def _write_audio_catalog_report(
    path: Path,
    *,
    stats: dict[str, Any],
    files: dict[str, str],
    kind_counts: dict[str, int],
    top_banks: list[dict[str, Any]],
) -> None:
    lines = [
        "# Fanxiu Wwise audio catalog",
        "",
        "Static read-only catalog for local Wwise `.bnk` and `.wem` resources.",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Kind Counts", ""])
    for key, value in sorted(kind_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Top Banks By Embedded WEM Entries", ""])
    for row in top_banks[:40]:
        lines.append(
            f"- `{row.get('relative_path')}` kind `{row.get('kind')}` wem entries `{row.get('wem_entry_count')}`, chunks `{row.get('chunk_fourccs')}`, bytes `{row.get('bytes')}`"
        )
    lines.extend(["", "## Files", ""])
    for key, value in files.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report indexes local audio container metadata only. It does not decode, play, upload, or extract audio payloads unless a separate explicit WEM extraction command is used.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_wwise_audio_catalog(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_resource_root(resource_root)
    if not root.exists() or not root.is_dir():
        raise FanxiuResourceError(f"资源根目录不存在：{root}")
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "parsed_configs" / "audio_catalog"
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_files = sorted(
        path
        for path in (root / "Audio").rglob("*")
        if path.is_file() and path.suffix.lower() in {".bnk", ".wem"}
    )
    bank_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    wem_rows: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []

    for path in audio_files:
        relative_path = path.relative_to(root).as_posix()
        kind = _audio_kind(path)
        size = path.stat().st_size
        suffix = path.suffix.lower()
        chunk_fourccs: list[str] = []
        wem_entry_count = 0
        didx_payload_bytes = 0
        if suffix == ".bnk":
            try:
                chunks = parse_wwise_bnk_chunks(path)
                entries = parse_wwise_didx_entries(path)
            except Exception as exc:
                chunks = []
                entries = []
                parse_errors.append(
                    {
                        "relative_path": relative_path,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            for chunk in chunks:
                row = chunk.to_dict()
                chunk_fourcc = str(row.get("fourcc") or "")
                chunk_fourccs.append(chunk_fourcc)
                chunk_rows.append(
                    {
                        "relative_path": relative_path,
                        "kind": kind,
                        "fourcc": chunk_fourcc,
                        "offset": row.get("offset", ""),
                        "size": row.get("size", ""),
                    }
                )
            for entry in entries:
                row = entry.to_dict()
                wem_entry_count += 1
                try:
                    didx_payload_bytes += int(row.get("size") or 0)
                except (TypeError, ValueError):
                    pass
                wem_rows.append(
                    {
                        "relative_path": relative_path,
                        "kind": kind,
                        "wem_id": row.get("wem_id", ""),
                        "offset": row.get("offset", ""),
                        "size": row.get("size", ""),
                    }
                )
        bank_rows.append(
            {
                "relative_path": relative_path,
                "kind": kind,
                "suffix": suffix,
                "bytes": size,
                "chunk_count": len(chunk_fourccs),
                "chunk_fourccs": ",".join(chunk_fourccs),
                "wem_entry_count": wem_entry_count,
                "didx_payload_bytes": didx_payload_bytes,
            }
        )

    bank_tsv = output_dir / "wwise_bank_catalog.tsv"
    chunk_tsv = output_dir / "wwise_bank_chunks.tsv"
    wem_tsv = output_dir / "wwise_wem_entries.tsv"
    error_tsv = output_dir / "wwise_parse_errors.tsv"
    report_path = output_dir / "wwise_audio_catalog_report.md"
    json_path = output_dir / "wwise_audio_catalog.json"

    _write_tsv(
        bank_tsv,
        bank_rows,
        [
            "relative_path",
            "kind",
            "suffix",
            "bytes",
            "chunk_count",
            "chunk_fourccs",
            "wem_entry_count",
            "didx_payload_bytes",
        ],
    )
    _write_tsv(chunk_tsv, chunk_rows, ["relative_path", "kind", "fourcc", "offset", "size"])
    _write_tsv(wem_tsv, wem_rows, ["relative_path", "kind", "wem_id", "offset", "size"])
    _write_tsv(error_tsv, parse_errors, ["relative_path", "error"])

    kind_counts = Counter(row["kind"] for row in bank_rows)
    top_banks = sorted(bank_rows, key=lambda row: (-int(row.get("wem_entry_count") or 0), str(row.get("relative_path") or "")))
    stats = {
        "resource_root": str(root),
        "export_root": str(export_base),
        "audio_file_count": len(audio_files),
        "bank_file_count": sum(1 for path in audio_files if path.suffix.lower() == ".bnk"),
        "standalone_wem_file_count": sum(1 for path in audio_files if path.suffix.lower() == ".wem"),
        "chunk_row_count": len(chunk_rows),
        "wem_entry_count": len(wem_rows),
        "parse_error_count": len(parse_errors),
        "metadata_only_no_audio_payload_exported": True,
    }
    files = {
        "banks": str(bank_tsv),
        "chunks": str(chunk_tsv),
        "wem_entries": str(wem_tsv),
        "errors": str(error_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    _write_audio_catalog_report(
        report_path,
        stats=stats,
        files=files,
        kind_counts=dict(kind_counts),
        top_banks=top_banks,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "kind_counts": dict(kind_counts),
                "top_banks": top_banks[:120],
                "sample_wem_entries": wem_rows[:120],
                "parse_errors": parse_errors[:120],
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "kind_counts": dict(kind_counts),
        "top_banks": top_banks[:40],
        "files": files,
    }


def build_fanxiu_wwise_mp3_export(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    vgmstream_cli: str | Path | None = None,
    ffmpeg_path: str | Path | None = None,
    max_banks: int | None = None,
    max_entries: int | None = None,
    overwrite: bool = False,
    mp3_quality: int = 4,
) -> dict[str, Any]:
    root = resolve_fanxiu_resource_root(resource_root)
    if not root.exists() or not root.is_dir():
        raise FanxiuResourceError(f"资源根目录不存在：{root}")
    resolved_vgmstream = _resolve_vgmstream_cli(vgmstream_cli)
    resolved_ffmpeg = _resolve_ffmpeg(ffmpeg_path)
    mp3_quality = max(0, min(int(mp3_quality), 9))
    bank_limit = None if max_banks is None else max(0, int(max_banks))
    entry_limit = None if max_entries is None else max(0, int(max_entries))
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "parsed_configs" / "audio_catalog"
    mp3_root = output_dir / "mp3"
    mp3_root.mkdir(parents=True, exist_ok=True)

    bank_paths = sorted(
        path
        for path in (root / "Audio").rglob("*.bnk")
        if path.is_file()
    )
    if bank_limit is not None:
        bank_paths = bank_paths[:bank_limit]

    rows: list[dict[str, Any]] = []
    converted_count = 0
    skipped_count = 0
    failed_count = 0
    extracted_wem_count = 0
    processed_bank_count = 0
    stop_after_entry = False

    with tempfile.TemporaryDirectory(prefix="fanxiu_wem_") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        for bank_path in bank_paths:
            if stop_after_entry:
                break
            processed_bank_count += 1
            relative_bank_path = bank_path.relative_to(root).as_posix()
            kind = _audio_kind(bank_path)
            bank_wem_dir = temp_dir / _safe_path_part(str(processed_bank_count))
            bank_wem_dir.mkdir(parents=True, exist_ok=True)
            try:
                entries = extract_wwise_wem_entries(bank_path, bank_wem_dir)
            except Exception as exc:
                failed_count += 1
                rows.append(
                    {
                        "source_bank": relative_bank_path,
                        "kind": kind,
                        "wem_id": "",
                        "entry_index": "",
                        "wem_size": "",
                        "sample_rate": "",
                        "channels": "",
                        "duration_seconds": "",
                        "encoding": "",
                        "mp3_path": "",
                        "relative_mp3_path": "",
                        "status": "bank_extract_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            for entry_index, entry in enumerate(entries):
                if entry_limit is not None and extracted_wem_count >= entry_limit:
                    stop_after_entry = True
                    break
                item = entry.to_dict()
                extracted_wem_count += 1
                wem_id = item.get("wem_id", "")
                wem_path = Path(item.get("output_path") or "")
                rel_mp3 = _mp3_relative_output_path(relative_bank_path, wem_id)
                mp3_path = mp3_root / rel_mp3
                row = {
                    "source_bank": relative_bank_path,
                    "kind": kind,
                    "wem_id": wem_id,
                    "entry_index": entry_index,
                    "wem_size": item.get("size", ""),
                    "sample_rate": "",
                    "channels": "",
                    "duration_seconds": "",
                    "encoding": "",
                    "mp3_path": str(mp3_path),
                    "relative_mp3_path": rel_mp3.as_posix(),
                    "status": "",
                    "error": "",
                }
                wav_path = bank_wem_dir / f"{_safe_path_part(wem_id, 'wem')}.wav"
                metadata_error: Exception | None = None
                try:
                    info = _read_vgmstream_info(resolved_vgmstream, wem_path)
                    row["sample_rate"] = info.get("sampleRate", "")
                    row["channels"] = info.get("channels", "")
                    row["duration_seconds"] = _duration_seconds(info)
                    row["encoding"] = info.get("encoding", "")
                except Exception as exc:
                    metadata_error = exc

                if mp3_path.is_file() and not overwrite:
                    row["status"] = "skipped_existing"
                    if metadata_error is not None:
                        row["error"] = f"{type(metadata_error).__name__}: {metadata_error}"
                    skipped_count += 1
                    rows.append(row)
                    continue

                try:
                    if metadata_error is not None:
                        raise metadata_error
                    _decode_wem_to_wav(resolved_vgmstream, wem_path, wav_path)
                    _convert_wav_to_mp3(resolved_ffmpeg, wav_path, mp3_path, mp3_quality=mp3_quality)
                    row["status"] = "converted"
                    converted_count += 1
                except Exception as exc:
                    row["status"] = "failed"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    failed_count += 1
                finally:
                    try:
                        if wav_path.exists():
                            wav_path.unlink()
                    except OSError:
                        pass
                rows.append(row)

    manifest_tsv = output_dir / "wwise_mp3_manifest.tsv"
    report_path = output_dir / "wwise_mp3_export_report.md"
    json_path = output_dir / "wwise_mp3_export.json"
    _write_tsv(
        manifest_tsv,
        rows,
        [
            "source_bank",
            "kind",
            "wem_id",
            "entry_index",
            "wem_size",
            "sample_rate",
            "channels",
            "duration_seconds",
            "encoding",
            "mp3_path",
            "relative_mp3_path",
            "status",
            "error",
        ],
    )
    stats = {
        "resource_root": str(root),
        "export_root": str(export_base),
        "vgmstream_cli": str(resolved_vgmstream),
        "ffmpeg": resolved_ffmpeg,
        "mp3_root": str(mp3_root),
        "bank_scan_count": len(bank_paths),
        "processed_bank_count": processed_bank_count,
        "extracted_wem_count": extracted_wem_count,
        "converted_count": converted_count,
        "skipped_existing_count": skipped_count,
        "failed_count": failed_count,
        "mp3_quality": mp3_quality,
        "overwrite": overwrite,
        "audio_payload_exported_as_mp3": True,
    }
    files = {
        "manifest": str(manifest_tsv),
        "markdown": str(report_path),
        "json": str(json_path),
        "mp3_root": str(mp3_root),
    }
    status_counts = Counter(row["status"] for row in rows)
    lines = [
        "# Fanxiu Wwise MP3 export",
        "",
        "Local conversion of Wwise BNK/WEM audio to MP3 for frontend playback.",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Status Counts", ""])
    for key, value in sorted(status_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Sample MP3 Rows", ""])
    for row in rows[:60]:
        lines.append(
            f"- `{row.get('status')}` `{row.get('source_bank')}` wem `{row.get('wem_id')}` duration `{row.get('duration_seconds')}` -> `{row.get('relative_mp3_path')}`"
        )
    lines.extend(["", "## Files", ""])
    for key, value in files.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This is a local derived-audio export for in-workspace playback. Do not redistribute converted game audio without the appropriate rights.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "status_counts": dict(status_counts),
                "sample_rows": rows[:120],
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "status_counts": dict(status_counts),
        "files": files,
    }


def resolve_fanxiu_audio_media_path(path: str, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    media_root = root / "parsed_configs" / "audio_catalog" / "mp3"
    raw_path = Path(path)
    media_path = raw_path.expanduser().resolve() if raw_path.is_absolute() else (media_root / raw_path).resolve()
    if not _is_relative_to(media_path, media_root.resolve()):
        raise FanxiuResourceError(f"音频路径必须位于 MP3 导出目录内：{media_root}")
    if not media_path.is_file() or media_path.suffix.lower() not in _AUDIO_MEDIA_SUFFIXES:
        raise FanxiuResourceError(f"音频文件不存在或格式不支持：{media_path}")
    return media_path


def load_fanxiu_wwise_mp3_manifest(
    *,
    export_root: str | Path | None = None,
    query: str | None = None,
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    manifest_path = root / "parsed_configs" / "audio_catalog" / "wwise_mp3_manifest.tsv"
    if not manifest_path.is_file():
        raise FanxiuResourceError(f"MP3 manifest 不存在，请先运行 Wwise MP3 导出：{manifest_path}")

    with manifest_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    normalized_query = (query or "").strip().lower()
    normalized_kind = (kind or "").strip().lower()
    if normalized_kind == "amb":
        normalized_kind = "ambient"

    def row_matches_query(row: dict[str, str]) -> bool:
        if not normalized_query:
            return True
        haystack = " ".join(
            str(row.get(field, ""))
            for field in (
                "source_bank",
                "kind",
                "wem_id",
                "duration_seconds",
                "encoding",
                "relative_mp3_path",
                "status",
            )
        ).lower()
        return normalized_query in haystack

    query_rows = [row for row in rows if row_matches_query(row)]
    kind_counts = Counter(str(row.get("kind", "")).lower() or "audio" for row in rows)
    query_kind_counts = Counter(str(row.get("kind", "")).lower() or "audio" for row in query_rows)

    def row_matches(row: dict[str, str]) -> bool:
        if normalized_kind and str(row.get("kind", "")).lower() != normalized_kind:
            return False
        return row_matches_query(row)

    filtered_rows = [row for row in rows if row_matches(row)]
    safe_limit = max(1, min(int(limit), 5000))
    safe_offset = max(0, int(offset))
    page_rows = filtered_rows[safe_offset : safe_offset + safe_limit]
    return {
        "manifest": str(manifest_path),
        "total": len(rows),
        "filtered": len(filtered_rows),
        "offset": safe_offset,
        "limit": safe_limit,
        "stats": {
            "kinds": dict(kind_counts),
            "query_kinds": dict(query_kind_counts),
            "query_total": len(query_rows),
        },
        "rows": page_rows,
    }
