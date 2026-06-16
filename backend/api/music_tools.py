from __future__ import annotations

import os
import json
import math
import shutil
import struct
import subprocess
import time
import mimetypes
import re
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.core.runtime.long_tasks import LongTaskContext, LongTaskManager, LongTaskNotFoundError
from backend.core.runtime.subprocess_utils import hidden_subprocess_kwargs
from backend.core.freebill.open_score_library import MidiParseError, get_open_score_work, list_open_score_works
from backend.core.settings import get_settings
from backend.core.temp_paths import codeyun_temp_root

try:
    import numpy as np
except Exception:  # pragma: no cover - optional spectral analysis
    np = None

router = APIRouter()

SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".wav",
    ".wma",
}
SUPPORTED_VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".webm",
}
SUPPORTED_MEDIA_EXTENSIONS = SUPPORTED_AUDIO_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
STEM_ORDER = ("original", "vocals", "other", "bass", "drums", "guitar", "piano")
MULTITRACK_AUDIO_EXTENSIONS = SUPPORTED_AUDIO_EXTENSIONS | {".aif", ".aiff"}
MAX_MULTITRACK_IMPORT_FILES = 32
MAX_MULTITRACK_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
SEPARATED_STEMS_BY_ENGINE = {
    "demucs": ("vocals", "other", "bass", "drums"),
    "audio_separator_6s": ("vocals", "drums", "bass", "guitar", "piano", "other"),
}
MUSIC_TOOLS_ROOT = Path(r"D:\home\chenkunze\slns+\music-tools")
TRANSCRIPTION_ROOT = MUSIC_TOOLS_ROOT / "outputs" / "transcriptions"
INSTRUMENT_REGISTRY_PATH = MUSIC_TOOLS_ROOT / "data" / "instrument-registry" / "instrument-registry.json"
DEMUCS_PYTHON = MUSIC_TOOLS_ROOT / ".venvs" / "demucs" / "Scripts" / "python.exe"
DEMUCS_MODEL = "htdemucs"
AUDIO_SEPARATOR_EXE = MUSIC_TOOLS_ROOT / ".venvs" / "audio-separator" / "Scripts" / "audio-separator.exe"
AUDIO_SEPARATOR_PYTHON = MUSIC_TOOLS_ROOT / ".venvs" / "audio-separator" / "Scripts" / "python.exe"
BASIC_PITCH_PYTHON = MUSIC_TOOLS_ROOT / ".venvs" / "basic-pitch" / "Scripts" / "python.exe"
AUDIO_SEPARATOR_MODEL = "htdemucs_6s.yaml"
AUDIO_SEPARATOR_MODEL_DIR = MUSIC_TOOLS_ROOT / "models" / "audio-separator"
PIANO_TRANSCRIPTION_SCRIPT = MUSIC_TOOLS_ROOT / "scripts" / "transcribe-piano-stem.py"
HUMMING_TRANSCRIPTION_TIMEOUT_SECONDS = 20 * 60
ORPHANED_JOB_GRACE_SECONDS = 120.0

_task_manager = LongTaskManager("music-separation", max_workers=1, max_records=32, record_ttl_seconds=6 * 60 * 60)
_index_lock = RLock()


class MusicToolInfo(BaseModel):
    demucs_installed: bool
    demucs_python: str
    audio_separator_installed: bool
    audio_separator_exe: str
    work_root: str


class MusicScoreFile(BaseModel):
    key: str
    filename: str
    url: str
    size: int
    modified_at: float


class MusicScoreInfo(BaseModel):
    id: str
    title: str
    version: str
    kind: str
    source_stem: str | None = None
    tempo_bpm: float | None = None
    beats_per_bar: int | None = None
    measures: int | None = None
    files: list[MusicScoreFile]


class MusicScoreList(BaseModel):
    scores: list[MusicScoreInfo]


class MusicInstrumentRegistry(BaseModel):
    version: int
    generated_at: str
    sources: dict[str, str]
    source_counts: dict[str, int]
    total: int
    instruments: list[dict[str, Any]]


class OpenScoreWorkList(BaseModel):
    works: list[dict[str, Any]]


class MultitrackLibraryList(BaseModel):
    sources: list[dict[str, Any]]


class MusicAnalysisCapabilityList(BaseModel):
    capabilities: list[dict[str, Any]]


class MusicJobUpdate(BaseModel):
    filename: str


class MultitrackUrlImportRequest(BaseModel):
    url: str
    source_id: str | None = None
    filename: str | None = None


class MusicCreativeBrief(BaseModel):
    job_id: str
    title: str
    duration_seconds: float | None
    available_stems: list[str]
    audio_features: dict[str, Any]
    description_zh: str
    suno_prompt_zh: str
    suno_prompt_en: str
    prompt_variants: list[dict[str, str]]
    style_directions: list[dict[str, Any]]
    stem_insights: list[dict[str, str]]
    arrangement_plan: list[dict[str, str]]
    suno_fields: dict[str, Any]
    style_profile: dict[str, Any]
    style_presets: list[dict[str, Any]]
    creative_recipes: list[dict[str, Any]]
    tags: list[str]
    cautions: list[str]


class MusicCreativePromptRecord(BaseModel):
    id: str
    job_id: str
    name: str
    prompt_zh: str
    prompt_en: str | None = None
    source: str
    created_at: float
    audio_features: dict[str, Any] = {}


class MusicCreativePromptRecordList(BaseModel):
    records: list[MusicCreativePromptRecord]


class MusicCreativePromptSaveRequest(BaseModel):
    name: str
    prompt_zh: str
    prompt_en: str | None = None
    source: str = "manual"
    audio_features: dict[str, Any] = {}


def _normalize_engine(engine: str | None) -> str:
    normalized = str(engine or "demucs").strip().lower()
    if normalized in SEPARATED_STEMS_BY_ENGINE:
        return normalized
    return "demucs"


def _expected_stems_for_job(job: dict[str, Any]) -> tuple[str, ...]:
    raw_stems = job.get("expected_stems")
    if isinstance(raw_stems, list):
        stems = tuple(str(stem) for stem in raw_stems if str(stem) in STEM_ORDER and str(stem) != "original")
        if stems:
            return stems
    return SEPARATED_STEMS_BY_ENGINE[_normalize_engine(str(job.get("engine") or "demucs"))]


def _storage_root() -> Path:
    path = get_settings().data_dir / "music-tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _jobs_root() -> Path:
    path = _storage_root() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    return _storage_root() / "jobs.json"


def _load_index() -> dict[str, Any]:
    path = _index_path()
    if not path.exists():
        return {"version": 1, "jobs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "jobs": []}
    if not isinstance(payload, dict):
        return {"version": 1, "jobs": []}
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        payload["jobs"] = []
    payload["version"] = 1
    return payload


def _write_index(payload: dict[str, Any]) -> None:
    path = _index_path()
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _upsert_job(job: dict[str, Any]) -> dict[str, Any]:
    normalized_job = dict(job)
    normalized_job["files"] = _list_job_files(str(normalized_job.get("job_id") or ""))
    with _index_lock:
        payload = _load_index()
        jobs = [item for item in payload.get("jobs", []) if isinstance(item, dict)]
        next_jobs = [item for item in jobs if item.get("job_id") != normalized_job.get("job_id")]
        next_jobs.insert(0, normalized_job)
        payload["jobs"] = next_jobs[:200]
        _write_index(payload)
    return normalized_job


def _get_indexed_job(job_id: str) -> dict[str, Any] | None:
    with _index_lock:
        payload = _load_index()
        for item in payload.get("jobs", []):
            if isinstance(item, dict) and item.get("job_id") == job_id:
                job = dict(item)
                return _refresh_indexed_job_runtime_state(job, persist=True)
    return None


def _update_indexed_job(job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    with _index_lock:
        payload = _load_index()
        jobs = [item for item in payload.get("jobs", []) if isinstance(item, dict)]
        updated_job: dict[str, Any] | None = None
        for index, item in enumerate(jobs):
            if item.get("job_id") != job_id:
                continue
            next_item = dict(item)
            next_item.update(updates)
            next_item["updated_at"] = time.time()
            jobs[index] = next_item
            updated_job = next_item
            break
        if updated_job is None:
            return None
        payload["jobs"] = jobs
        _write_index(payload)
    return _refresh_indexed_job_runtime_state(dict(updated_job), persist=False)


def _list_indexed_jobs() -> list[dict[str, Any]]:
    with _index_lock:
        payload = _load_index()
        jobs = [dict(item) for item in payload.get("jobs", []) if isinstance(item, dict)]
    return [_refresh_indexed_job_runtime_state(job, persist=True) for job in jobs]


def _has_all_separated_stems(files: list[dict[str, Any]], expected_stems: tuple[str, ...]) -> bool:
    stems = {str(item.get("stem") or "") for item in files}
    return set(expected_stems).issubset(stems)


def _refresh_indexed_job_runtime_state(job: dict[str, Any], *, persist: bool = False) -> dict[str, Any]:
    job_id = str(job.get("job_id") or "")
    job["files"] = _list_job_files(job_id) if job_id else []
    status = str(job.get("status") or "")
    if status not in {"queued", "running"}:
        return job

    changed = False
    if _has_all_separated_stems(job["files"], _expected_stems_for_job(job)):
        job.update({"status": "completed", "updated_at": time.time()})
        changed = True
    else:
        now = time.time()
        task_id = str(job.get("task_id") or "")
        if task_id:
            try:
                task = _task_manager.serialize_task(task_id, include_result=False)
            except LongTaskNotFoundError:
                task = None
            if task is None:
                if now - float(job.get("updated_at") or job.get("created_at") or now) > ORPHANED_JOB_GRACE_SECONDS:
                    job.update({
                        "status": "failed",
                        "error": "后台任务已中断，通常是开发服务重启导致；请重新分离。",
                        "updated_at": now,
                    })
                    changed = True
            elif task.get("status") in {"queued", "running"}:
                job.update({
                    "status": task.get("status"),
                    "task_message": task.get("message"),
                    "updated_at": task.get("updated_at") or now,
                })
            elif task.get("status") == "failed":
                job.update({
                    "status": "failed",
                    "error": task.get("error") or task.get("message") or "分离失败",
                    "updated_at": task.get("updated_at") or now,
                })
                changed = True
        elif now - float(job.get("updated_at") or job.get("created_at") or now) > ORPHANED_JOB_GRACE_SECONDS:
            job.update({
                "status": "failed",
                "error": "后台任务记录缺失，可能已被开发服务重启中断；请重新分离。",
                "updated_at": now,
            })
            changed = True

    if changed and persist:
        _upsert_job(job)
    return job


def _safe_filename(filename: str) -> str:
    raw = Path(filename or "audio").name.strip()
    stem = Path(raw).stem.strip() or "audio"
    suffix = Path(raw).suffix.lower()
    safe_stem = "".join(char if char.isalnum() or char in "._- " else "_" for char in stem).strip(" ._-")
    if not safe_stem:
        safe_stem = "audio"
    if suffix not in SUPPORTED_MEDIA_EXTENSIONS:
        suffix = ".mp3"
    return f"{safe_stem}{suffix}"


def _safe_stem_key(name: str, used: set[str] | None = None) -> str:
    used = used if used is not None else set()
    raw = Path(name or "track").stem.strip().lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw, flags=re.IGNORECASE).strip("-")
    key = raw or "track"
    key = key[:48]
    base = key
    index = 2
    while key in STEM_ORDER or key in used:
        suffix = f"-{index}"
        key = f"{base[: max(1, 48 - len(suffix))]}{suffix}"
        index += 1
    used.add(key)
    return key


def _clean_track_label(name: str) -> str:
    label = Path(name or "Track").stem.replace("_", " ").replace("-", " ")
    label = re.sub(r"\s+", " ", label).strip()
    return label[:80] or "Track"


def _resolve_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="处理视频输入需要 ffmpeg，请先安装 ffmpeg 并加入 PATH")
    return ffmpeg


def _job_dir(job_id: str) -> Path:
    safe_job_id = "".join(char for char in str(job_id) if char.isalnum() or char in "-_")
    if not safe_job_id:
        raise HTTPException(status_code=404, detail="Invalid job id")
    path = _jobs_root() / safe_job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _custom_stems_path(job_id: str) -> Path:
    return _job_dir(job_id) / "custom-stems.json"


def _load_custom_stems(job_id: str) -> list[dict[str, Any]]:
    path = _custom_stems_path(job_id)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    stems = payload.get("stems") if isinstance(payload, dict) else None
    if not isinstance(stems, list):
        return []
    return [item for item in stems if isinstance(item, dict)]


def _write_custom_stems(job_id: str, stems: list[dict[str, Any]]) -> None:
    _write_json_atomic(_custom_stems_path(job_id), {"version": 1, "stems": stems})


def _creative_prompt_path(job_id: str) -> Path:
    return _job_dir(job_id) / "creative-prompts.json"


def _load_creative_prompt_records(job_id: str) -> list[dict[str, Any]]:
    path = _creative_prompt_path(job_id)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _save_creative_prompt_record(job_id: str, request: MusicCreativePromptSaveRequest) -> MusicCreativePromptRecord:
    if _get_indexed_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    prompt_zh = request.prompt_zh.strip()
    if not prompt_zh:
        raise HTTPException(status_code=400, detail="Prompt is empty")
    record = {
        "id": uuid4().hex,
        "job_id": job_id,
        "name": (request.name.strip() or "提示词版本")[:80],
        "prompt_zh": prompt_zh,
        "prompt_en": request.prompt_en.strip() if request.prompt_en else None,
        "source": (request.source.strip() or "manual")[:40],
        "created_at": time.time(),
        "audio_features": request.audio_features if isinstance(request.audio_features, dict) else {},
    }
    records = _load_creative_prompt_records(job_id)
    records.insert(0, record)
    _write_json_atomic(_creative_prompt_path(job_id), {"version": 1, "records": records[:80]})
    return MusicCreativePromptRecord(**record)


def _stem_file(job_id: str, stem: str) -> Path:
    job_dir = _job_dir(job_id)
    if stem not in STEM_ORDER:
        safe_stem = _safe_stem_key(stem)
        if safe_stem != stem:
            raise HTTPException(status_code=404, detail="Unknown stem")
        for item in _load_custom_stems(job_id):
            if str(item.get("stem") or "") != stem:
                continue
            filename = Path(str(item.get("filename") or "")).name
            if not filename:
                break
            path = job_dir / "stems" / filename
            if path.is_file():
                return path
        return job_dir / "stems" / f"{stem}.mp3"
    if stem == "original":
        for path in job_dir.glob("original.*"):
            if path.is_file():
                return path
        return job_dir / "original.mp3"
    candidates = sorted(
        (path for path in job_dir.glob(f"{stem}*.mp3") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    return job_dir / f"{stem}.mp3"


def _stem_output_file(job_id: str, stem: str) -> Path:
    default_path = _job_dir(job_id) / f"{stem}.mp3"
    if not default_path.exists():
        return default_path
    return _job_dir(job_id) / f"{stem}-{uuid4().hex[:8]}.mp3"


def _clear_generated_audio_outputs(job_id: str) -> None:
    job_dir = _job_dir(job_id)
    for stem in STEM_ORDER:
        if stem == "original":
            continue
        for path in job_dir.glob(f"{stem}*.mp3"):
            if path.is_file():
                path.unlink(missing_ok=True)
    for directory_name in ("demucs", "audio-separator"):
        path = job_dir / directory_name
        if path.exists():
            shutil.rmtree(path)
    for log_name in ("demucs.log", "audio-separator.log"):
        (job_dir / log_name).unlink(missing_ok=True)


def _clear_generated_score_outputs(job_id: str) -> None:
    if not TRANSCRIPTION_ROOT.exists():
        return
    for path in TRANSCRIPTION_ROOT.glob("*/score-manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and str(payload.get("job_id") or "") == job_id:
            shutil.rmtree(path.parent, ignore_errors=True)


def _public_file(job_id: str, stem: str, path: Path, *, label: str | None = None, role: str | None = None) -> dict[str, Any]:
    stat = path.stat()
    payload = {
        "stem": stem,
        "label": label or stem,
        "role": role or "",
        "filename": path.name,
        "url": f"/api/music-tools/jobs/{job_id}/audio/{stem}",
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }
    return payload


def _list_job_files(job_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for stem in STEM_ORDER:
        path = _stem_file(job_id, stem)
        if path.exists():
            files.append(_public_file(job_id, stem, path))
    for item in _load_custom_stems(job_id):
        stem = str(item.get("stem") or "")
        if not stem or stem in STEM_ORDER:
            continue
        path = _stem_file(job_id, stem)
        if path.exists():
            files.append(
                _public_file(
                    job_id,
                    stem,
                    path,
                    label=str(item.get("label") or stem),
                    role=str(item.get("role") or "multitrack"),
                )
            )
    return files


def _score_manifest_id(directory: Path, manifest: dict[str, Any]) -> str:
    raw_id = str(manifest.get("id") or "").strip()
    return raw_id or directory.name


def _score_manifest_candidates(job_id: str, kind: str | None = None) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in TRANSCRIPTION_ROOT.glob("*/score-manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or str(payload.get("job_id") or "") != job_id:
            continue
        if kind and str(payload.get("kind") or "") != kind:
            continue
        candidates.append((path.parent, payload))
    return sorted(candidates, key=lambda item: item[0].stat().st_mtime, reverse=True)


def _get_score_manifest(job_id: str, kind: str | None = None) -> tuple[Path, dict[str, Any]] | None:
    candidates = _score_manifest_candidates(job_id, kind)
    return candidates[0] if candidates else None


def _score_file_payload(job_id: str, key: str, filename: str, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "key": key,
        "filename": filename,
        "url": f"/api/music-tools/jobs/{job_id}/score/file/{quote(filename)}",
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def _score_files(job_id: str, directory: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, dict):
        return []
    result: list[dict[str, Any]] = []
    for key, raw_filename in files.items():
        filename = Path(str(raw_filename or "")).name
        if not filename:
            continue
        path = directory / filename
        if path.is_file():
            result.append(_score_file_payload(job_id, str(key), filename, path))
    return result


def _score_info(job_id: str, directory: Path, manifest: dict[str, Any]) -> MusicScoreInfo | None:
    files = _score_files(job_id, directory, manifest)
    if not files:
        return None
    source_stem = manifest.get("source_stem")
    return MusicScoreInfo(
        id=_score_manifest_id(directory, manifest),
        title=str(manifest.get("title") or "钢琴独奏谱"),
        version=str(manifest.get("version") or ""),
        kind=str(manifest.get("kind") or "piano_solo_score"),
        source_stem=str(source_stem) if source_stem else None,
        tempo_bpm=manifest.get("tempo_bpm") if isinstance(manifest.get("tempo_bpm"), (int, float)) else None,
        beats_per_bar=manifest.get("beats_per_bar") if isinstance(manifest.get("beats_per_bar"), int) else None,
        measures=manifest.get("measures") if isinstance(manifest.get("measures"), int) else None,
        files=files,
    )


def _score_file(job_id: str, filename: str) -> Path:
    requested = Path(filename or "").name
    for directory, manifest in _score_manifest_candidates(job_id):
        allowed = {
            Path(str(item or "")).name
            for item in (manifest.get("files") or {}).values()
            if Path(str(item or "")).name
        }
        if requested in allowed:
            path = directory / requested
            if path.is_file():
                return path
    raise HTTPException(status_code=404, detail="Score file not found")


def _probe_audio_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return None
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            os.fspath(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    try:
        return round(float((completed.stdout or "").strip()), 3)
    except ValueError:
        return None


def _analyze_audio_features(path: Path, *, max_seconds: int = 120, sample_rate: int = 22050) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not path.exists():
        return {"available": False, "reason": "ffmpeg unavailable or audio missing"}
    output_dir = codeyun_temp_root("music_audio_features")
    raw_path = output_dir / f"{uuid4().hex}.s16le"
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-t",
                str(max_seconds),
                "-i",
                os.fspath(path),
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-f",
                "s16le",
                os.fspath(raw_path),
            ],
            capture_output=True,
            text=True,
            timeout=max_seconds + 60,
            check=False,
        )
        if completed.returncode != 0 or not raw_path.exists():
            return {"available": False, "reason": (completed.stderr or completed.stdout or "ffmpeg decode failed").strip()[:300]}
        data = raw_path.read_bytes()
    finally:
        raw_path.unlink(missing_ok=True)
    if len(data) < 4:
        return {"available": False, "reason": "decoded audio is empty"}

    sample_count = len(data) // 2
    samples = struct.unpack(f"<{sample_count}h", data[: sample_count * 2])
    if not samples:
        return {"available": False, "reason": "decoded audio has no samples"}

    abs_values = [abs(sample) for sample in samples]
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    peak = max(abs_values) / 32768
    zero_crossings = sum(1 for left, right in zip(samples, samples[1:]) if (left < 0 <= right) or (left >= 0 > right))
    zcr = zero_crossings / max(1, len(samples) - 1)
    window_size = sample_rate
    windows: list[float] = []
    for start in range(0, len(samples), window_size):
        chunk = samples[start : start + window_size]
        if not chunk:
            continue
        windows.append(math.sqrt(sum(sample * sample for sample in chunk) / len(chunk)) / 32768)
    if not windows:
        windows = [rms]
    sorted_windows = sorted(windows)
    low = sorted_windows[max(0, int(len(sorted_windows) * 0.1) - 1)]
    high = sorted_windows[min(len(sorted_windows) - 1, int(len(sorted_windows) * 0.9))]
    silence_threshold = max(0.006, rms * 0.18)
    silence_ratio = sum(1 for value in windows if value < silence_threshold) / len(windows)
    bpm = _estimate_bpm(samples, sample_rate)
    transient_density = _estimate_transient_density(windows)
    pulse_strength = _estimate_pulse_strength(windows)
    energy_label = _energy_label(rms, high, zcr)
    mood_label = _mood_label(energy_label, bpm, silence_ratio)
    peak_second = max(range(len(windows)), key=lambda index: windows[index])
    arrangement_shape = _arrangement_shape(windows)
    sections = _summarize_energy_sections(windows)
    spectral_profile = _spectral_music_profile(samples, sample_rate)
    return {
        "available": True,
        "analyzed_seconds": round(len(samples) / sample_rate, 2),
        "sample_rate": sample_rate,
        "rms": round(rms, 4),
        "peak": round(peak, 4),
        "zero_crossing_rate": round(zcr, 4),
        "dynamic_range": round(max(0.0, high - low), 4),
        "silence_ratio": round(silence_ratio, 4),
        "estimated_bpm": bpm,
        "transient_density": transient_density,
        "pulse_strength": pulse_strength,
        "density_label": _density_label(transient_density, pulse_strength),
        "energy_label": energy_label,
        "mood_label": mood_label,
        "peak_second": peak_second,
        "arrangement_shape": arrangement_shape,
        "sections": sections,
        **spectral_profile,
    }


def _spectral_music_profile(samples: tuple[int, ...], sample_rate: int) -> dict[str, Any]:
    if np is None or len(samples) < sample_rate:
        return {}
    try:
        audio = np.asarray(samples, dtype=np.float32) / 32768.0
        if audio.size < 4096:
            return {}
        audio = audio[: min(audio.size, sample_rate * 90)]
        frame_size = 4096
        hop = 2048
        if audio.size < frame_size:
            return {}
        window = np.hanning(frame_size).astype(np.float32)
        freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
        chroma = np.zeros(12, dtype=np.float64)
        band_energy = {
            "sub_bass": 0.0,
            "bass": 0.0,
            "low_mid": 0.0,
            "mid": 0.0,
            "high_mid": 0.0,
            "air": 0.0,
        }
        total_energy = 0.0
        centroid_sum = 0.0
        frame_count = 0
        for start in range(0, audio.size - frame_size + 1, hop):
            frame = audio[start : start + frame_size] * window
            spectrum = np.abs(np.fft.rfft(frame))
            power = spectrum * spectrum
            energy = float(power.sum())
            if energy <= 1e-10:
                continue
            total_energy += energy
            centroid_sum += float((freqs * power).sum() / max(power.sum(), 1e-10))
            frame_count += 1
            band_energy["sub_bass"] += float(power[(freqs >= 20) & (freqs < 60)].sum())
            band_energy["bass"] += float(power[(freqs >= 60) & (freqs < 250)].sum())
            band_energy["low_mid"] += float(power[(freqs >= 250) & (freqs < 500)].sum())
            band_energy["mid"] += float(power[(freqs >= 500) & (freqs < 2000)].sum())
            band_energy["high_mid"] += float(power[(freqs >= 2000) & (freqs < 6000)].sum())
            band_energy["air"] += float(power[(freqs >= 6000) & (freqs < min(12000, sample_rate / 2))].sum())

            tonal_mask = (freqs >= 55) & (freqs <= 5000)
            tonal_freqs = freqs[tonal_mask]
            tonal_power = power[tonal_mask]
            if tonal_freqs.size:
                midi = np.rint(69 + 12 * np.log2(np.maximum(tonal_freqs, 1e-6) / 440.0)).astype(np.int32)
                pitch_classes = np.mod(midi, 12)
                for pitch_class in range(12):
                    chroma[pitch_class] += float(tonal_power[pitch_classes == pitch_class].sum())
        if total_energy <= 0 or frame_count <= 0:
            return {}
        band_ratio = {key: round(value / total_energy, 4) for key, value in band_energy.items()}
        centroid = centroid_sum / frame_count
        chroma_sum = float(chroma.sum())
        tonal = _estimate_key_from_chroma(chroma / chroma_sum) if chroma_sum > 0 else {}
        low_ratio = band_ratio["sub_bass"] + band_ratio["bass"]
        mid_ratio = band_ratio["low_mid"] + band_ratio["mid"]
        high_ratio = band_ratio["high_mid"] + band_ratio["air"]
        return {
            "spectral_centroid_hz": round(centroid, 1),
            "brightness_label": _brightness_label(centroid, high_ratio),
            "frequency_balance": band_ratio,
            "low_frequency_ratio": round(low_ratio, 4),
            "mid_frequency_ratio": round(mid_ratio, 4),
            "high_frequency_ratio": round(high_ratio, 4),
            **tonal,
        }
    except Exception:
        return {}


def _estimate_key_from_chroma(chroma: Any) -> dict[str, Any]:
    if np is None:
        return {}
    major_profile = np.asarray([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.asarray([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    names_en = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    names_zh = ["C", "升C", "D", "降E", "E", "F", "升F", "G", "降A", "A", "降B", "B"]
    chroma = np.asarray(chroma, dtype=np.float64)
    if float(chroma.sum()) <= 0:
        return {}
    chroma = chroma / max(float(chroma.sum()), 1e-9)
    scores: list[tuple[float, int, str]] = []
    for tonic in range(12):
      scores.append((_profile_similarity(chroma, np.roll(major_profile, tonic)), tonic, "major"))
      scores.append((_profile_similarity(chroma, np.roll(minor_profile, tonic)), tonic, "minor"))
    scores.sort(reverse=True, key=lambda item: item[0])
    best, second = scores[0], scores[1]
    confidence = max(0.0, min(1.0, (best[0] - second[0] + 0.08) / 0.35))
    mode_zh = "大调" if best[2] == "major" else "小调"
    mode_en = "major" if best[2] == "major" else "minor"
    top_pitch_classes = [
        {"name": names_en[index], "name_zh": names_zh[index], "weight": round(float(chroma[index]), 4)}
        for index in np.argsort(chroma)[::-1][:5]
    ]
    return {
        "estimated_key": f"{names_en[best[1]]} {mode_en}",
        "estimated_key_zh": f"{names_zh[best[1]]}{mode_zh}",
        "key_confidence": round(confidence, 3),
        "tonal_center": names_en[best[1]],
        "mode": mode_en,
        "top_pitch_classes": top_pitch_classes,
    }


def _profile_similarity(chroma: Any, profile: Any) -> float:
    chroma_centered = chroma - np.mean(chroma)
    profile_centered = profile - np.mean(profile)
    denom = float(np.linalg.norm(chroma_centered) * np.linalg.norm(profile_centered))
    if denom <= 1e-9:
        return 0.0
    return float(np.dot(chroma_centered, profile_centered) / denom)


def _brightness_label(centroid: float, high_ratio: float) -> str:
    if centroid >= 2600 or high_ratio >= 0.38:
        return "明亮通透"
    if centroid <= 1200 and high_ratio < 0.18:
        return "温暖厚实"
    return "均衡自然"


def _estimate_transient_density(windows: list[float]) -> float:
    if len(windows) < 3:
        return 0.0
    deltas = [max(0.0, windows[index] - windows[index - 1]) for index in range(1, len(windows))]
    if not deltas:
        return 0.0
    threshold = max(0.004, sum(deltas) / len(deltas) * 1.45)
    return round(sum(1 for value in deltas if value >= threshold) / len(deltas), 4)


def _estimate_pulse_strength(windows: list[float]) -> float:
    if len(windows) < 6:
        return 0.0
    average = sum(windows) / len(windows)
    if average <= 0:
        return 0.0
    variance = sum((value - average) ** 2 for value in windows) / len(windows)
    return round(min(1.0, math.sqrt(variance) / average), 4)


def _density_label(transient_density: float, pulse_strength: float) -> str:
    if transient_density > 0.28 or pulse_strength > 0.7:
        return "节奏密集"
    if transient_density > 0.14 or pulse_strength > 0.38:
        return "节奏清晰"
    return "铺陈舒缓"


def _estimate_bpm(samples: tuple[int, ...], sample_rate: int) -> float | None:
    frame_size = max(1, sample_rate // 10)
    energies: list[float] = []
    for start in range(0, len(samples), frame_size):
        chunk = samples[start : start + frame_size]
        if len(chunk) < frame_size // 2:
            continue
        energies.append(math.sqrt(sum(sample * sample for sample in chunk) / len(chunk)))
    if len(energies) < 20:
        return None
    onset = [max(0.0, energies[index] - energies[index - 1]) for index in range(1, len(energies))]
    if not onset or max(onset) <= 0:
        return None
    best_lag = 0
    best_score = 0.0
    for bpm in range(60, 181):
        lag = round(600 / bpm)
        if lag <= 0 or lag >= len(onset):
            continue
        score = sum(onset[index] * onset[index - lag] for index in range(lag, len(onset)))
        if score > best_score:
            best_score = score
            best_lag = lag
    if best_lag <= 0:
        return None
    return round(600 / best_lag, 1)


def _energy_label(rms: float, high_energy: float, zcr: float) -> str:
    if high_energy > 0.18 or rms > 0.095:
        return "高能量"
    if high_energy > 0.08 or zcr > 0.08:
        return "中等能量"
    return "低能量"


def _mood_label(energy_label: str, bpm: float | None, silence_ratio: float) -> str:
    if energy_label == "高能量" and (bpm or 0) >= 120:
        return "激昂推进"
    if silence_ratio > 0.35 or energy_label == "低能量":
        return "安静抒情"
    if bpm and bpm < 85:
        return "慢速叙事"
    return "中速展开"


def _arrangement_shape(windows: list[float]) -> str:
    if len(windows) < 4:
        return "结构样本较短"
    thirds = [_segment_average(windows, index * len(windows) // 3, (index + 1) * len(windows) // 3) for index in range(3)]
    if thirds[2] > thirds[0] * 1.45 and thirds[1] >= thirds[0] * 0.9:
        return "渐强推进"
    if thirds[0] > thirds[1] * 1.25 and thirds[0] > thirds[2] * 1.25:
        return "前段高能"
    if max(thirds) - min(thirds) < max(0.015, sum(thirds) / len(thirds) * 0.22):
        return "平稳循环"
    if thirds[1] > thirds[0] * 1.25 and thirds[1] > thirds[2] * 1.15:
        return "中段抬升"
    if thirds[2] < thirds[1] * 0.72:
        return "高潮后回落"
    return "段落起伏"


def _summarize_energy_sections(windows: list[float]) -> list[dict[str, Any]]:
    if not windows:
        return []
    section_names = ["开头", "前段", "中段", "后段"]
    ordered = sorted(windows)
    low_mark = ordered[max(0, int(len(ordered) * 0.33) - 1)]
    high_mark = ordered[min(len(ordered) - 1, int(len(ordered) * 0.72))]
    sections: list[dict[str, Any]] = []
    for index, name in enumerate(section_names):
        start = index * len(windows) // len(section_names)
        end = (index + 1) * len(windows) // len(section_names)
        average = _segment_average(windows, start, end)
        energy = _section_energy_label(average, low_mark, high_mark)
        sections.append(
            {
                "name": name,
                "start_second": start,
                "end_second": max(start + 1, end),
                "energy": energy,
                "average": round(average, 4),
                "width": round(max(1, end - start) / len(windows), 4),
            }
        )
    return sections


def _segment_average(values: list[float], start: int, end: int) -> float:
    segment = values[start:end] or values[start : start + 1] or values[-1:]
    return sum(segment) / len(segment)


def _section_energy_label(value: float, low_mark: float, high_mark: float) -> str:
    if value >= high_mark:
        return "高"
    if value <= low_mark:
        return "低"
    return "中"


def _creative_style_directions(structure_instruction: str, arrangement_shape: str) -> list[dict[str, Any]]:
    return [
        {
            "key": "base",
            "name": "基准影视纯音乐",
            "prompt_zh": (
                "创作一首旋律清晰的中文纯音乐/影视配乐，以钢琴和弦乐为核心，加入适度东方器乐色彩，"
                f"{structure_instruction}，旋律要能被哼唱，配器层次由少到多，避免直接模仿任何具体歌手或在世艺术家的个人风格。"
            ),
            "prompt_en": (
                "Create an original instrumental cinematic track with a clear memorable melody, centered on piano and strings, "
                f"subtle East Asian colors, arrangement shape: {arrangement_shape}; {structure_instruction}. Do not imitate any specific living artist."
            ),
            "palette": ["piano", "strings", "soft percussion", "subtle East Asian instruments"],
            "use_case": "通用纯音乐、视频配乐、先出稳定版本",
        },
        {
            "key": "gufeng",
            "name": "古风纯音乐",
            "prompt_zh": (
                "原创古风纯音乐，钢琴与弦乐打底，加入笛子、箫、古筝、琵琶或低音大鼓点缀，"
                f"{structure_instruction}，旋律清雅但有记忆点，节奏不要过度流行化，突出东方画面感和留白。"
            ),
            "prompt_en": (
                "Original East Asian instrumental, piano and strings foundation with dizi or xiao, guzheng, pipa and low ceremonial drums, "
                f"{structure_instruction}, elegant memorable melody, cinematic space, no direct artist imitation."
            ),
            "palette": ["dizi/xiao", "guzheng", "pipa", "strings", "low drums"],
            "use_case": "古风、国风、东方奇幻、山水/江湖画面",
        },
        {
            "key": "warm_animation",
            "name": "温暖动画电影感",
            "prompt_zh": (
                "原创温暖动画电影感纯音乐，钢琴、木管、弦乐、钟琴和轻柔打击乐，旋律朴素明亮，"
                f"{structure_instruction}，带童话感、怀旧感和治愈感，避免过度厚重或电子化。"
            ),
            "prompt_en": (
                "Original warm animated-film-like instrumental with piano, woodwinds, strings, glockenspiel and gentle percussion, "
                f"{structure_instruction}, simple bright melody, nostalgic fairytale feeling, healing atmosphere."
            ),
            "palette": ["piano", "woodwinds", "strings", "glockenspiel", "gentle percussion"],
            "use_case": "温暖叙事、动画感、童话/怀旧/治愈场景",
        },
        {
            "key": "epic_cinematic",
            "name": "宏大燃向影视配乐",
            "prompt_zh": (
                "原创宏大燃向影视配乐，弦乐 ostinato、低鼓、合唱垫底、铜管远景和强烈主旋律，"
                f"{structure_instruction}，中后段逐步打开成宽阔高潮，节奏有推进感但保持旋律可辨。"
            ),
            "prompt_en": (
                "Original epic cinematic instrumental, string ostinato, low drums, choir pads, distant brass and a strong memorable main theme, "
                f"{structure_instruction}, gradually opens into a broad climax while keeping the melody clear."
            ),
            "palette": ["string ostinato", "low drums", "choir pads", "brass", "large percussion"],
            "use_case": "燃向、战斗/决意、宏大展开、预告片式能量",
        },
    ]


def _stem_label(stem: str, files: list[dict[str, Any]]) -> str:
    item = next((file for file in files if str(file.get("stem") or "") == stem), None)
    if item:
        return str(item.get("label") or item.get("filename") or stem)
    return {
        "original": "原曲",
        "vocals": "人声",
        "drums": "鼓",
        "bass": "贝斯",
        "guitar": "吉他",
        "piano": "钢琴",
        "other": "伴奏/其他",
    }.get(stem, stem)


def _stem_insights(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    for file in files:
        stem = str(file.get("stem") or "")
        if not stem or stem == "original":
            continue
        label = str(file.get("label") or file.get("filename") or stem)
        role = str(file.get("role") or "")
        normalized = stem.lower()
        role_text = role or normalized
        if normalized == "vocals" or role == "vocals":
            focus = "听旋律入口、句尾停顿、情绪强弱和是否适合作为主旋律参考。"
            usage = "生成纯音乐时可把人声线改写成笛子、二胡、小提琴或钢琴主旋律。"
        elif normalized == "drums" or role == "drums":
            focus = "听重拍、切分、镲片密度和高潮前的鼓点递进。"
            usage = "可决定提示词里的 percussion intensity、low drums、taiko-like hits 或保持无鼓。"
        elif normalized == "bass" or role == "bass":
            focus = "听根音走向、低频停顿和副歌/高潮是否靠贝斯推动。"
            usage = "可转成低弦、低鼓或合成低频，支撑古风/影视配乐的地基。"
        elif normalized == "piano" or role == "piano":
            focus = "听和弦节奏、分解织体和是否承担可哼唱旋律。"
            usage = "适合保留为钢琴+弦乐骨架，也可转成古筝/竖琴式分解。"
        elif normalized == "guitar" or role == "guitar":
            focus = "听扫弦/拨弦节奏、和声色彩和人声之间的空隙。"
            usage = "可转成琵琶、阮、古筝拨弦或轻民谣吉他质感。"
        else:
            focus = "听它是在补和声、铺氛围、做节奏点缀还是承担副旋律。"
            usage = "根据听感决定归入 pad、strings、woodwinds、plucked instruments 或 sound design。"
        insights.append({"stem": stem, "label": label, "role": role_text, "focus": focus, "usage": usage})
    return insights


def _arrangement_plan(audio_features: dict[str, Any], stems: list[str]) -> list[dict[str, str]]:
    sections = audio_features.get("sections")
    if not isinstance(sections, list) or not sections:
        sections = [
            {"name": "开头", "energy": "低"},
            {"name": "前段", "energy": "中"},
            {"name": "中段", "energy": "中"},
            {"name": "后段", "energy": "高"},
        ]
    has_drums = "drums" in stems
    has_bass = "bass" in stems
    has_piano = "piano" in stems
    has_vocals = "vocals" in stems
    plan: list[dict[str, str]] = []
    for section in sections[:4]:
        name = str(section.get("name") or "段落")
        energy = str(section.get("energy") or "中")
        if energy == "高":
            texture = "打开弦乐群、低鼓和宽阔和声，主旋律完整出现。"
            listen = "重点听哪些轨道在同时加厚，以及高潮是否靠鼓/低频/高音旋律抬起。"
        elif energy == "低":
            texture = "保留少量钢琴、拨弦、笛箫或环境铺底，留出旋律呼吸。"
            listen = "重点听静音、留白和第一个动机，判断主题从哪里开始。"
        else:
            texture = "逐步增加内声部、低音和轻打击，保持主题可辨。"
            listen = "重点听重复动机如何变形，以及伴奏是否开始形成推进。"
        if has_vocals and name in {"开头", "前段"}:
            texture += " 如果参考人声线，可先用木管或小提琴替代。"
        if has_piano:
            texture += " 钢琴可作为和声骨架。"
        if has_drums or has_bass:
            texture += " 鼓和贝斯只在需要推进时逐步加入。"
        plan.append({"section": name, "energy": energy, "listen": listen, "arrange": texture})
    return plan


def _suno_fields(
    title: str,
    audio_features: dict[str, Any],
    style_directions: list[dict[str, Any]],
    structure_instruction: str,
) -> dict[str, Any]:
    bpm = audio_features.get("estimated_bpm")
    density = str(audio_features.get("density_label") or "铺陈舒缓")
    mood = str(audio_features.get("mood_label") or "中速展开")
    key_hint = str(audio_features.get("estimated_key") or "")
    brightness = str(audio_features.get("brightness_label") or "")
    base_tags = ["instrumental", "cinematic", "clear melody", "piano and strings"]
    if bpm:
        base_tags.append(f"{bpm} BPM")
    if key_hint:
        base_tags.append(key_hint)
    if brightness:
        base_tags.append(brightness)
    if density == "节奏密集":
        base_tags.extend(["driving percussion", "ostinato"])
    elif density == "铺陈舒缓":
        base_tags.extend(["rubato feeling", "soft dynamics"])
    return {
        "title_ideas": [
            f"{Path(title).stem or 'Untitled'} - 古风纯音乐版",
            f"{Path(title).stem or 'Untitled'} - 温暖动画电影版",
            f"{Path(title).stem or 'Untitled'} - 宏大燃向版",
        ],
        "style_tags": base_tags,
        "mood_tags": [mood, str(audio_features.get("energy_label") or "中等能量"), density],
        "structure_tags": [
            "intro - motif - development - climax - outro",
            structure_instruction,
            "keep the main melody singable and easy to remember",
        ],
        "negative_prompt": "avoid direct imitation of specific living artists, avoid copyrighted character names, avoid messy vocals, avoid noisy mix, avoid abrupt ending",
        "instrumental_hint": "建议开启 instrumental / no lyrics；如果平台没有该开关，就在提示词开头写 Original instrumental。",
        "copy_order": [
            "先复制选中的风格提示词",
            "再追加 structure_tags 的第二条结构约束",
            "最后追加 negative_prompt 里的规避项",
        ],
        "style_count": len(style_directions),
    }


def _creative_style_profile(audio_features: dict[str, Any], stems: list[str], title: str) -> dict[str, Any]:
    density = str(audio_features.get("density_label") or "铺陈舒缓")
    energy = str(audio_features.get("energy_label") or "中等能量")
    mood = str(audio_features.get("mood_label") or "中速展开")
    shape = str(audio_features.get("arrangement_shape") or "段落起伏")
    brightness = str(audio_features.get("brightness_label") or "")
    key_zh = str(audio_features.get("estimated_key_zh") or "")
    bpm = audio_features.get("estimated_bpm")
    low_ratio = audio_features.get("low_frequency_ratio")
    high_ratio = audio_features.get("high_frequency_ratio")

    scores = {
        "古风纯音乐": 0,
        "温暖动画电影感": 0,
        "宏大燃向影视配乐": 0,
        "钢琴弦乐抒情": 0,
    }
    reasons: list[str] = []
    if density == "铺陈舒缓":
        scores["古风纯音乐"] += 2
        scores["温暖动画电影感"] += 2
        scores["钢琴弦乐抒情"] += 2
        reasons.append("节奏密度偏舒展，适合保留旋律和留白。")
    elif density == "节奏密集":
        scores["宏大燃向影视配乐"] += 3
        reasons.append("节奏密度较高，适合做 ostinato、低鼓和推进型编曲。")
    if "高能量" in energy or shape in {"渐强推进", "前段高能", "中段抬升"}:
        scores["宏大燃向影视配乐"] += 2
        reasons.append(f"能量/结构呈“{shape}”，可设计中后段打开的高潮。")
    if "温暖" in brightness or "明亮" in brightness:
        scores["温暖动画电影感"] += 2
        reasons.append(f"音色倾向“{brightness}”，适合木管、钢琴、钟琴和暖弦乐。")
    if isinstance(low_ratio, (int, float)) and low_ratio >= 0.34:
        scores["宏大燃向影视配乐"] += 1
        reasons.append("低频占比较高，适合低鼓、低弦或大编制支撑。")
    if isinstance(high_ratio, (int, float)) and high_ratio >= 0.18:
        scores["温暖动画电影感"] += 1
        reasons.append("高频信息较明显，适合加入木管、钟琴或清亮拨弦点缀。")
    if "piano" in stems:
        scores["钢琴弦乐抒情"] += 2
        scores["温暖动画电影感"] += 1
        reasons.append("已有钢琴/键盘线索，可作为和声骨架或主旋律入口。")
    if "guitar" in stems:
        scores["古风纯音乐"] += 1
        reasons.append("拨弦/扫弦线索可转译成古筝、琵琶、阮或竖琴式织体。")
    if "vocals" in stems:
        scores["古风纯音乐"] += 1
        scores["温暖动画电影感"] += 1
        reasons.append("人声线可改写为笛箫、二胡、小提琴或钢琴主旋律。")
    if "drums" in stems or "bass" in stems:
        scores["宏大燃向影视配乐"] += 1
        reasons.append("鼓/贝斯分轨可用来判断推进和低频骨架。")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_fit = ranked[0][0] if ranked else "古风纯音乐"
    tempo_text = f"{bpm} BPM" if bpm else "natural tempo"
    key_text = f"，建议围绕 {key_zh}" if key_zh else ""
    title_stem = Path(title).stem or "Untitled"
    prompt_blueprint = {
        "suno_style": [
            "instrumental",
            "clear memorable melody",
            tempo_text,
            "piano and strings",
            "East Asian colors",
            "cinematic arrangement",
            best_fit,
        ],
        "prompt_core_zh": (
            f"把《{title_stem}》作为参考情绪，创作原创{best_fit}。"
            f"保留一个可哼唱主旋律{key_text}，根据当前结构“{shape}”安排起承转合；"
            "主旋律不要被鼓点或氛围音淹没，结尾自然收束。"
        ),
        "prompt_core_en": (
            f"Create an original {best_fit} instrumental inspired by the reference mood of {title_stem}. "
            f"Keep one singable main theme, {tempo_text}, arrangement shape: {shape}. "
            "Use piano and strings as the backbone, add tasteful East Asian colors, and avoid direct imitation of any specific living artist."
        ),
    }
    return {
        "best_fit": best_fit,
        "style_scores": [{"name": name, "score": score} for name, score in ranked],
        "why": reasons[:6] or ["当前音频特征较中性，优先按旋律导向的纯音乐工作流处理。"],
        "analysis_tags": [tag for tag in [density, energy, mood, shape, brightness, key_zh] if tag],
        "prompt_blueprint": prompt_blueprint,
        "workflow": [
            "先复制 Style Tags，确认 instrumental/no lyrics。",
            "再复制 Prompt Core，按平台字数限制删减。",
            "如果想古风，优先保留笛箫/二胡/古筝/琵琶；如果想燃向，优先保留 ostinato/low drums/brass。",
            "生成后回听主旋律是否清楚，再决定是否加强鼓、低频或木管回应。",
        ],
        "negative": [
            "不要直接模仿具体在世艺术家或歌手",
            "不要使用受版权保护的角色/作品名",
            "不要让鼓点淹没主旋律",
            "不要突然断尾或过度堆叠噪声",
        ],
    }


def _creative_recipes(
    style_directions: list[dict[str, Any]],
    audio_features: dict[str, Any],
    arrangement_plan: list[dict[str, str]],
    stem_insights: list[dict[str, str]],
) -> list[dict[str, Any]]:
    density = str(audio_features.get("density_label") or "铺陈舒缓")
    mood = str(audio_features.get("mood_label") or "中速展开")
    bpm = audio_features.get("estimated_bpm")
    tempo_hint = f"{bpm} BPM" if bpm else "自然速度"
    key_hint = str(audio_features.get("estimated_key") or "")
    brightness = str(audio_features.get("brightness_label") or "")
    stem_roles = [item["label"] for item in stem_insights[:5] if item.get("label")]
    first_section = arrangement_plan[0]["arrange"] if arrangement_plan else "先建立清晰主题，再逐步增加配器。"
    climax_section = next((item["arrange"] for item in arrangement_plan if item.get("energy") == "高"), "")
    if not climax_section and arrangement_plan:
        climax_section = arrangement_plan[-1]["arrange"]
    recipes: list[dict[str, Any]] = []
    recipe_specs = {
        "gufeng": {
            "title": "古风纯音乐改写",
            "goal": "把参考音频转成东方画面感更强、旋律可哼唱的纯音乐版本。",
            "hook": "笛箫/二胡主旋律 + 古筝/琵琶拨弦 + 钢琴弦乐骨架",
            "instrumentation": ["dizi or xiao", "erhu", "guzheng", "pipa", "piano", "strings", "low ceremonial drums"],
            "avoid": ["过重电子音色", "现代流行鼓组过满", "直接模仿具体在世艺术家"],
        },
        "warm_animation": {
            "title": "温暖动画电影感",
            "goal": "做成明亮、治愈、童话感的叙事型纯音乐。",
            "hook": "钢琴主题 + 木管回应 + 弦乐铺底 + 钟琴点光",
            "instrumentation": ["piano", "woodwinds", "strings", "glockenspiel", "soft percussion", "warm room ambience"],
            "avoid": ["过度史诗化", "强攻击性鼓点", "过暗的合成器低频"],
        },
        "epic_cinematic": {
            "title": "宏大燃向影视配乐",
            "goal": "保留旋律识别度，同时把中后段推成宽阔、有决意感的高潮。",
            "hook": "弦乐 ostinato + 低鼓 + 铜管远景 + 合唱垫底",
            "instrumentation": ["string ostinato", "low drums", "brass", "choir pads", "taiko-like hits", "piano motif"],
            "avoid": ["旋律被鼓点淹没", "全程高能无对比", "预告片噪声堆叠过多"],
        },
    }
    for direction in style_directions:
        key = str(direction.get("key") or "")
        if key not in recipe_specs:
            continue
        spec = recipe_specs[key]
        style_tags = [
            "instrumental",
            "clear memorable melody",
            tempo_hint,
            mood,
            density,
            *([key_hint] if key_hint else []),
            *([brightness] if brightness else []),
            *spec["instrumentation"][:4],
        ]
        prompt_core = str(direction.get("prompt_en") or direction.get("prompt_zh") or "")
        platform_prompt = (
            f"{prompt_core} "
            f"{'Suggested tonal center: ' + key_hint + '. ' if key_hint else ''}"
            f"Instrumentation: {', '.join(spec['instrumentation'])}. "
            f"Structure: intro motif, development, contrast, climax, quiet outro. "
            f"Keep one singable main theme and avoid direct imitation of any specific living artist."
        )
        recipes.append(
            {
                "key": key,
                "title": spec["title"],
                "goal": spec["goal"],
                "hook": spec["hook"],
                "style_tags": style_tags,
                "instrumentation": spec["instrumentation"],
                "arrangement_moves": [
                    first_section,
                    "中段保留主旋律动机，用内声部、低音或节奏型制造变化。",
                    climax_section or "后段打开弦乐群和低频，形成可辨认的高潮。",
                ],
                "listen_first": stem_roles or ["原曲主旋律", "低频根音", "节奏密度", "和声铺底"],
                "platform_prompts": {
                    "suno_style": ", ".join(style_tags[:10]),
                    "suno_prompt": platform_prompt,
                    "udio_prompt": platform_prompt,
                    "negative": ", ".join(spec["avoid"]),
                },
            }
        )
    return recipes


def _creative_style_presets(audio_features: dict[str, Any], stems: list[str], title: str) -> list[dict[str, Any]]:
    title_stem = Path(title).stem or "Untitled"
    bpm = audio_features.get("estimated_bpm")
    tempo_text = f"{bpm} BPM" if bpm else "natural tempo"
    key_hint = str(audio_features.get("estimated_key_zh") or audio_features.get("estimated_key") or "")
    energy = str(audio_features.get("energy_label") or "中等能量")
    density = str(audio_features.get("density_label") or "铺陈舒缓")
    shape = str(audio_features.get("arrangement_shape") or "段落起伏")
    brightness = str(audio_features.get("brightness_label") or "")
    stem_text = "、".join(stems) if stems else "原曲"
    common_negative = "avoid direct imitation of specific living artists, avoid copyrighted names, avoid messy vocals, avoid over-compressed drums, avoid abrupt ending"
    base_context_zh = (
        f"参考《{title_stem}》的情绪和轮廓，当前线索：{stem_text}；"
        f"能量 {energy}，密度 {density}，结构 {shape}"
        f"{'，音色' + brightness if brightness else ''}"
        f"{'，建议调性感围绕' + key_hint if key_hint else ''}。"
    )
    base_context_en = (
        f"Use the reference mood and contour of {title_stem}. "
        f"Detected clues: {stem_text}; energy: {energy}; density: {density}; structure: {shape}; {tempo_text}. "
    )
    presets = [
        {
            "key": "gufeng_instrumental",
            "name": "古风纯音乐",
            "fit": "旋律清楚、适合改成东方器乐叙事时优先用。",
            "palette": ["笛箫", "二胡", "古筝", "琵琶", "钢琴", "弦乐", "低鼓"],
            "listen_check": ["先确认主旋律是否能被笛箫/二胡承接", "再确认低频是否适合换成鼓和低弦", "最后检查拨弦织体是否太满"],
            "suno_style": "instrumental, gufeng, East Asian orchestral, dizi, xiao, erhu, guzheng, pipa, piano, strings, cinematic, clear melody",
            "suno_prompt": (
                f"{base_context_zh} 创作原创古风纯音乐，不要歌词。用笛箫或二胡承担主旋律，"
                "古筝/琵琶做拨弦织体，钢琴与弦乐托住和声，中后段可以加入低鼓但不要盖住旋律。"
            ),
            "udio_prompt": (
                f"{base_context_en} Create an original instrumental gufeng cinematic piece with dizi/xiao or erhu lead, "
                "guzheng and pipa patterns, piano and strings as the harmonic backbone, restrained low ceremonial drums, and one singable theme."
            ),
            "negative": common_negative,
        },
        {
            "key": "warm_animation",
            "name": "温暖动画电影感",
            "fit": "想要治愈、明亮、叙事型纯音乐时用。",
            "palette": ["钢琴", "木管", "弦乐", "钟琴", "竖琴", "轻打击"],
            "listen_check": ["钢琴先给出主题", "木管回应主题尾句", "弦乐只在中后段打开", "钟琴只做少量点光"],
            "suno_style": "instrumental, warm animation film score, piano, woodwinds, strings, glockenspiel, harp, gentle percussion, nostalgic, bright, storybook",
            "suno_prompt": (
                f"{base_context_zh} 创作原创温暖动画电影感纯音乐，不要歌词。"
                "钢琴先给出简单可记的主题，木管做轻柔回应，弦乐逐步铺开，少量钟琴/竖琴增加童话感，整体温暖但不要过度甜腻。"
            ),
            "udio_prompt": (
                f"{base_context_en} Create an original warm animated-film instrumental cue: piano theme, woodwind answers, soft strings, small glockenspiel and harp highlights, "
                "gentle nostalgic mood, clear narrative arc, no vocals."
            ),
            "negative": common_negative + ", avoid aggressive trailer percussion",
        },
        {
            "key": "epic_cinematic",
            "name": "宏大燃向影视配乐",
            "fit": "想把中后段做成有决意感、推进感、宽阔高潮时用。",
            "palette": ["弦乐 ostinato", "低鼓", "铜管", "合唱垫底", "钢琴动机", "低弦"],
            "listen_check": ["先确认低音/鼓是否形成推进", "再确认主旋律没有被 ostinato 淹没", "高潮后要留回落空间"],
            "suno_style": "instrumental, epic cinematic, string ostinato, low drums, brass, choir pads, piano motif, heroic, dramatic build, wide orchestral climax",
            "suno_prompt": (
                f"{base_context_zh} 创作原创宏大燃向影视配乐，不要歌词。"
                "用钢琴或低弦给出短动机，弦乐 ostinato 推进，低鼓和铜管在中后段打开，合唱只做远景垫底；保持主旋律清楚，高潮后自然回落。"
            ),
            "udio_prompt": (
                f"{base_context_en} Create an original epic cinematic instrumental: piano motif, string ostinato, low drums, brass swells, subtle choir pads, "
                "dramatic build into a wide climax, then a natural release. Keep the main theme clear."
            ),
            "negative": common_negative + ", avoid nonstop climax, avoid trailer noise pileup",
        },
    ]
    for preset in presets:
        preset["copy_order"] = ["复制 Suno Style 到风格栏", "复制 Suno Prompt 到歌词/描述栏", "开启 instrumental/no lyrics", "生成后回听主旋律是否清楚"]
    return presets


def _creative_brief_for_job(job: dict[str, Any]) -> MusicCreativeBrief:
    job_id = str(job.get("job_id") or "")
    title = str(job.get("filename") or "未命名音频")
    files = _list_job_files(job_id)
    stems = [str(item.get("stem") or "") for item in files if str(item.get("stem") or "")]
    duration = _probe_audio_duration(_stem_file(job_id, "original"))
    audio_features = _analyze_audio_features(_stem_file(job_id, "original"))
    has_vocals = "vocals" in stems
    has_drums = "drums" in stems
    has_bass = "bass" in stems
    has_piano = "piano" in stems
    has_guitar = "guitar" in stems

    texture_parts = []
    if has_vocals:
        texture_parts.append("有人声主线")
    else:
        texture_parts.append("偏纯音乐或无人声音频")
    if has_piano:
        texture_parts.append("可突出钢琴/键盘织体")
    if has_guitar:
        texture_parts.append("可突出吉他拨弦或扫弦")
    if has_bass:
        texture_parts.append("低频支撑明确")
    if has_drums:
        texture_parts.append("鼓组节奏可单独检查")
    if not texture_parts:
        texture_parts.append("当前只有原始音频，建议先做六轨分离再生成更准的描述")

    mood = str(audio_features.get("mood_label") or "抒情、画面感、旋律清晰")
    if has_drums and has_bass:
        mood = "节奏明确、层次完整、适合分析编曲推进"
    if has_piano and not has_drums:
        mood = "钢琴/弦乐感、抒情、适合纯音乐或影视配乐方向"

    tags = [
        "纯音乐参考",
        "影视配乐感",
        "旋律导向",
        "古风/东方器乐可改写",
        "钢琴与弦乐",
    ]
    if has_drums:
        tags.append("节奏驱动")
    if has_vocals:
        tags.append("有人声")

    feature_bits = []
    if audio_features.get("available"):
        if audio_features.get("estimated_bpm"):
            feature_bits.append(f"估计速度约 {audio_features['estimated_bpm']} BPM")
        if audio_features.get("estimated_key_zh"):
            confidence = audio_features.get("key_confidence")
            confidence_text = f"（置信度 {confidence}）" if isinstance(confidence, (int, float)) else ""
            feature_bits.append(f"可能调性：{audio_features['estimated_key_zh']}{confidence_text}")
        feature_bits.append(f"能量：{audio_features.get('energy_label')}")
        if audio_features.get("brightness_label"):
            feature_bits.append(f"音色明暗：{audio_features.get('brightness_label')}")
        if isinstance(audio_features.get("low_frequency_ratio"), (int, float)):
            feature_bits.append(f"低频占比：{audio_features.get('low_frequency_ratio')}")
        feature_bits.append(f"结构：{audio_features.get('arrangement_shape')}")
        feature_bits.append(f"动态范围：{audio_features.get('dynamic_range')}")
        if isinstance(audio_features.get("peak_second"), int):
            feature_bits.append(f"峰值约在 {audio_features['peak_second']} 秒")
    description = (
        f"《{title}》当前可分析声部：{', '.join(stems) or 'original'}。"
        f"整体可先按“{mood}”理解；分轨线索显示：{'、'.join(texture_parts)}。"
        f"{'；'.join(feature_bits) if feature_bits else '尚未取得有效音频特征'}。"
    )
    arrangement_shape = str(audio_features.get("arrangement_shape") or "渐强推进")
    structure_instruction = {
        "渐强推进": "结构上保持由弱到强的渐进推进，中后段打开配器并形成宽阔高潮",
        "前段高能": "开头直接进入高能量主题，中段保留动机变化，后段做短暂回收",
        "平稳循环": "使用稳定循环型伴奏，靠配器层次和旋律变化制造推进",
        "中段抬升": "前段克制叙事，中段明显抬升，后段保留余韵",
        "高潮后回落": "中后段形成高潮后自然回落，结尾留出安静空间",
        "段落起伏": "做出清晰段落对比，安静段和展开段交替出现",
    }.get(arrangement_shape, "结构上先建立主题，再逐步增加层次并进入开阔段落")
    prompt_zh = (
        "创作一首旋律清晰的中文纯音乐/影视配乐，"
        "以钢琴和弦乐为核心，加入适度东方器乐色彩，情绪从安静叙事逐步推向开阔高潮，"
        f"{structure_instruction}，"
        "避免直接模仿任何具体歌手或在世艺术家的个人风格。"
    )
    prompt_en = (
        "Create an original instrumental cinematic track with a clear memorable melody, "
        "centered on piano and strings, with subtle East Asian instrumental colors. "
        f"Arrangement shape: {arrangement_shape}; {structure_instruction}. "
        "Do not imitate any specific living artist."
    )
    style_directions = _creative_style_directions(structure_instruction, arrangement_shape)
    stem_insights = _stem_insights(files)
    arrangement_plan = _arrangement_plan(audio_features, stems)
    suno_fields = _suno_fields(title, audio_features, style_directions, structure_instruction)
    style_profile = _creative_style_profile(audio_features, stems, title)
    style_presets = _creative_style_presets(audio_features, stems, title)
    creative_recipes = _creative_recipes(style_directions, audio_features, arrangement_plan, stem_insights)
    prompt_variants = [
        {"name": item["name"], "prompt_zh": item["prompt_zh"], "prompt_en": item["prompt_en"]}
        for item in style_directions
        if item["key"] != "base"
    ]
    cautions = [
        "这是基于文件名、时长和分轨结果生成的创作简报；还不是深度音频理解模型。",
        "可以参考“宏大管弦、影视配乐、东方器乐、钢琴弦乐抒情”等特征，但不要要求仿冒具体在世歌手。",
        "Suno 等第三方平台仍需用户自己授权登录；系统可以帮你整理提示词和版本记录。",
    ]
    return MusicCreativeBrief(
        job_id=job_id,
        title=title,
        duration_seconds=duration,
        available_stems=stems,
        audio_features=audio_features,
        description_zh=description,
        suno_prompt_zh=prompt_zh,
        suno_prompt_en=prompt_en,
        prompt_variants=prompt_variants,
        style_directions=style_directions,
        stem_insights=stem_insights,
        arrangement_plan=arrangement_plan,
        suno_fields=suno_fields,
        style_profile=style_profile,
        style_presets=style_presets,
        creative_recipes=creative_recipes,
        tags=tags,
        cautions=cautions,
    )


def _clear_separated_stem_files(job_id: str) -> None:
    for stem in STEM_ORDER:
        if stem == "original":
            continue
        for path in _job_dir(job_id).glob(f"{stem}*.mp3"):
            if not path.is_file():
                continue
            try:
                path.unlink()
            except PermissionError:
                continue


def _extract_video_audio(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            _resolve_ffmpeg(),
            "-y",
            "-i",
            os.fspath(input_path),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            os.fspath(output_path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
        timeout=30 * 60,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout or "ffmpeg audio extraction failed").strip())


def _multitrack_library_sources() -> list[dict[str, Any]]:
    return [
        {
            "id": "cambridge-mt",
            "name": "Cambridge-MT Mixing Secrets",
            "kind": "完整真实歌曲 multitrack",
            "url": "https://cambridge-mt.com/ms3/mtk/",
            "import_hint": "优先下载 Full Multitrack ZIP；导入后可以逐个乐器 solo / mute，再听完整合奏。",
            "fit": "最接近你说的“完整真实曲子 + 每个乐器可独奏”。",
            "strengths": ["真实完整歌曲", "鼓/贝斯/吉他/键盘/人声等原始轨道多", "最适合学习编曲层次和混音关系"],
            "cautions": ["站点有访问保护，系统不自动绕过下载页", "不同项目授权和用途说明需按源站核对"],
            "featured_works": [
                {
                    "title": "3D-MARCo Project - String Quartet",
                    "level": "入门",
                    "focus": "弦乐四重奏声部拆解",
                    "instruments": ["Violin", "Viola", "Cello"],
                    "why": "轨道数量少，适合先听旋律、内声部、低音根基如何互相支撑。",
                    "study": "先 solo 最高声部找主旋律，再开中声部听和声填充，最后只听低音判断和声方向。",
                    "style_bridge": "可迁移到古风弦乐、二胡/中胡/大提琴式分层写法。",
                },
                {
                    "title": "Piano Solo 1",
                    "level": "入门",
                    "focus": "钢琴独奏与踏板空间",
                    "instruments": ["Piano"],
                    "why": "单一主乐器，适合先理解旋律、和弦和低音如何由一件乐器同时承担。",
                    "study": "重点听左手低音、右手旋律、和弦填充是否在同一拍型里交替出现。",
                    "style_bridge": "可转成钢琴+古筝/竖琴分解，作为纯音乐骨架。",
                },
                {
                    "title": "The Abletones Big Band - Corine, Corine",
                    "level": "进阶",
                    "focus": "大编制声部进入与铜管/节奏组关系",
                    "instruments": ["Drums", "Bass", "Piano", "Brass", "Woodwinds"],
                    "why": "适合练习从完整编制里识别节奏组、低频和旋律声部的层级。",
                    "study": "先只开鼓和贝斯，再逐步加入钢琴、铜管、木管，听每层何时占据前景。",
                    "style_bridge": "可借鉴到影视配乐的分层推进和大合奏高潮。",
                },
            ],
        },
        {
            "id": "cambridge-backing-stems",
            "name": "Cambridge-MT Backing Stems",
            "kind": "真实 backing stems",
            "url": "https://cambridge-mt.com/rs1/bkg/",
            "import_hint": "适合先用较少轨道理解伴奏分层，再进入 Full Multitrack。",
            "fit": "更像“低难度分轨练习曲”，轨道少，适合先练独听。",
            "strengths": ["轨道数量较少", "更接近伴奏分层", "适合初学独听"],
            "cautions": ["通常不是完整乐谱", "仍需用户从源站下载 ZIP"],
            "featured_works": [
                {
                    "title": "Backing Stems 任意 4-8 轨项目",
                    "level": "入门",
                    "focus": "少轨伴奏分层",
                    "instruments": ["Drums", "Bass", "Guitar", "Keys"],
                    "why": "轨道数少，不会一上来被二三十条录音轨淹没。",
                    "study": "按低音、节奏、和声、旋律点缀的顺序逐个打开，记录每轨在合奏里的职责。",
                    "style_bridge": "适合先建立“鼓/低音/和声/旋律”的编曲听感框架。",
                },
            ],
        },
        {
            "id": "telefunken-live-from-the-lab",
            "name": "Telefunken Live From The Lab",
            "kind": "现场乐队真实 multitrack",
            "url": "https://www.telefunken-elektroakustik.com/multitracks/",
            "import_hint": "适合挑真实乐队 session；下载 multitrack 包后导入逐轨试听。",
            "fit": "真实乐手同场演奏，适合听一首歌里每件乐器如何互相咬合。",
            "strengths": ["真实乐队同场录音", "适合听鼓/贝斯/吉他/键盘/人声如何组合", "更接近现场演奏质感"],
            "cautions": ["素材授权以源站为准", "文件通常较大"],
            "featured_works": [
                {
                    "title": "Joshua Quimby - Ol Self Control / To The Choir",
                    "level": "入门",
                    "focus": "民谣小编制与弦乐点缀",
                    "instruments": ["Vocal", "Acoustic Guitar", "Fiddle", "Room"],
                    "why": "声部少而真实，适合听人声、木吉他、fiddle 和房间声的关系。",
                    "study": "先听木吉他节奏，再加 fiddle，最后开人声和房间声，观察空间感如何成形。",
                    "style_bridge": "fiddle 的线条可类比二胡/笛箫副旋律，适合古风小编制参考。",
                },
                {
                    "title": "Ryan Montbleau - Affected / Nervous",
                    "level": "进阶",
                    "focus": "完整乐队律动",
                    "instruments": ["Drums", "Bass", "Guitar", "Organ", "Keys", "Vocals"],
                    "why": "鼓、贝斯、吉他、键盘、人声都比较完整，适合学习真实乐队如何共同推进。",
                    "study": "先只开鼓和贝斯找 groove，再加吉他/organ/keys，最后听人声如何坐在伴奏上方。",
                    "style_bridge": "可借鉴到燃向影视配乐里的节奏组和 ostinato 推进。",
                },
                {
                    "title": "Bernard Purdie & Friends - It's Your Thing",
                    "level": "进阶",
                    "focus": "鼓手律动与 funk 编制",
                    "instruments": ["Drums", "Bass", "Guitar", "Keys", "Sax", "Vocals"],
                    "why": "适合专门训练鼓、贝斯和切分节奏的听感。",
                    "study": "只开鼓听 ghost notes 和 hi-hat/ride，再开贝斯，最后加吉他与键盘的切分。",
                    "style_bridge": "虽然不是古风，但能训练“节奏如何让音乐动起来”的底层听感。",
                },
                {
                    "title": "Renesans - Split Brow / Less Than Nothing / Labor Of Hate",
                    "level": "高能",
                    "focus": "摇滚/金属密集编制",
                    "instruments": ["Drums", "Bass", "Guitars", "Vocals", "Room"],
                    "why": "适合听高密度鼓、失真吉他和低频如何堆出强能量。",
                    "study": "先分辨 kick/snare/toms/overheads，再听左右吉他墙，最后观察人声如何穿透。",
                    "style_bridge": "可作为宏大燃向配乐的能量层参考，但旋律写法需另行改写。",
                },
            ],
        },
        {
            "id": "lewitt-dirty",
            "name": "LEWITT - Dirty Multitrack Session",
            "kind": "单曲完整真实工程",
            "url": "https://my.lewitt-audio.com/downloads",
            "import_hint": "下载 Dirty 的 multitrack session；导入后按鼓、贝斯、吉他、铜管/键盘、人声逐轨试听。",
            "fit": "一首歌的完整录音工程，适合做“从零拆一首真实歌”的样板。",
            "strengths": ["单曲目标清晰", "轨道较完整", "适合逐轨 solo 后再回到整曲"],
            "cautions": ["下载入口可能需要源站账号或表单", "具体使用权限以源站说明为准"],
            "featured_works": [
                {
                    "title": "Marina & the Kats - Dirty",
                    "level": "入门",
                    "focus": "一首完整歌曲的 28 轨录音工程",
                    "instruments": ["Drums", "Bass", "Guitar", "Keys", "Brass", "Vocals"],
                    "why": "目标比大素材库更明确，适合固定拿一首歌反复 solo、mute、对比完整合奏。",
                    "study": "先只开鼓和贝斯，再加和声乐器，最后打开人声与点缀声部，记录每轨职责。",
                    "style_bridge": "可训练真实编曲层级，再把节奏组/和声/点缀声部替换成古风或影视配器。",
                },
            ],
        },
        {
            "id": "medleydb",
            "name": "MedleyDB",
            "kind": "免版税研究级 multitrack",
            "url": "https://medleydb.weebly.com/",
            "import_hint": "适合挑含 stems/raw audio 的 full-length tracks；本工具可导入整理后的音频 ZIP 做逐轨试听。",
            "fit": "比普通练习素材更规整，适合研究旋律、乐器进入、自动扒谱和分轨算法。",
            "strengths": ["免版税 multitrack", "含混音、stems/raw audio、metadata", "覆盖流行、摇滚、爵士、古典、世界音乐等"],
            "cautions": ["偏研究数据集，下载和目录结构不如练习站直观", "不一定每首都像商业发行曲那样完整制作"],
            "featured_works": [
                {
                    "title": "MedleyDB full-length tracks",
                    "level": "进阶",
                    "focus": "真实曲目 stems/raw audio 与旋律标注",
                    "instruments": ["Vocals", "Drums", "Bass", "Guitar", "Piano", "Strings"],
                    "why": "它不是单纯 MIDI 谱，而是可拿到真实音频分轨和元数据，适合做系统化听辨训练。",
                    "study": "优先选轨道数 5-12 的歌曲，先听主旋律声部，再听低音、节奏和和声层。",
                    "style_bridge": "适合给后续“自动找主旋律/自动标注乐器职责”功能提供样本。",
                },
            ],
        },
        {
            "id": "urmp",
            "name": "URMP Dataset",
            "kind": "古典多乐器独奏轨",
            "url": "https://datadryad.org/dataset/doi:10.5061/dryad.ng3r749",
            "import_hint": "适合研究每个古典乐器的单独演奏轨、合奏视频、MIDI 乐谱和标注。",
            "fit": "最接近“每个乐器自己录了一条独奏轨，然后拼成合奏”的古典学习材料。",
            "strengths": ["独立乐器录音", "有合奏版本", "含 MIDI 乐谱和标注", "适合古典/室内乐声部学习"],
            "cautions": ["曲子较短、偏数据集", "不是流行歌曲制作工程"],
            "featured_works": [
                {
                    "title": "URMP multi-instrument classical pieces",
                    "level": "入门",
                    "focus": "独立乐器轨 + 合奏 + 乐谱对照",
                    "instruments": ["Violin", "Viola", "Cello", "Flute", "Clarinet", "Trumpet"],
                    "why": "每件乐器单独录制，更容易听清“这条旋律到底是谁在演奏”。",
                    "study": "先听单轨，再听合奏，再对照 MIDI/谱面，看声部如何从独奏线条变成整体音乐。",
                    "style_bridge": "适合古风、动画配乐、室内乐式写作的基础拆解。",
                },
            ],
        },
    ]


def _guess_multitrack_role(label: str) -> str:
    text = label.lower()
    if any(token in text for token in ("vocal", "vox", "voice", "lead")):
        return "vocals"
    if any(token in text for token in ("drum", "kick", "snare", "tom", "overhead", "oh ", "room")):
        return "drums"
    if "bass" in text:
        return "bass"
    if any(token in text for token in ("gtr", "guitar")):
        return "guitar"
    if any(token in text for token in ("piano", "keys", "keyboard", "synth", "organ")):
        return "piano"
    if any(token in text for token in ("string", "violin", "viola", "cello")):
        return "strings"
    return "instrument"


def _convert_audio_to_mp3(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            _resolve_ffmpeg(),
            "-y",
            "-v",
            "error",
            "-i",
            os.fspath(input_path),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            os.fspath(output_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
        timeout=30 * 60,
        check=False,
    )
    if completed.returncode != 0 or not output_path.exists():
        raise RuntimeError((completed.stderr or completed.stdout or "ffmpeg conversion failed").strip())


def _mix_multitrack_original(stem_paths: list[Path], output_path: Path) -> None:
    if not stem_paths:
        raise RuntimeError("没有可混音的分轨")
    if len(stem_paths) == 1:
        shutil.copy2(stem_paths[0], output_path)
        return
    inputs: list[str] = []
    for path in stem_paths:
        inputs.extend(["-i", os.fspath(path)])
    filter_complex = f"amix=inputs={len(stem_paths)}:duration=longest:normalize=1"
    completed = subprocess.run(
        [
            _resolve_ffmpeg(),
            "-y",
            "-v",
            "error",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            os.fspath(output_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
        timeout=45 * 60,
        check=False,
    )
    if completed.returncode != 0 or not output_path.exists():
        raise RuntimeError((completed.stderr or completed.stdout or "ffmpeg stem mix failed").strip())


def _download_multitrack_zip(url: str) -> tuple[Path, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="请输入有效的 http/https ZIP 直链")

    download_root = codeyun_temp_root("music_multitrack_url_import")
    download_root.mkdir(parents=True, exist_ok=True)
    request = Request(
        parsed.geturl(),
        headers={
            "User-Agent": "CodeYun-MusicTools/1.0",
            "Accept": "application/zip, application/octet-stream, */*",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise HTTPException(status_code=400, detail=f"下载失败：HTTP {status}")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_MULTITRACK_DOWNLOAD_BYTES:
                        raise HTTPException(status_code=400, detail="分轨 ZIP 过大，已拒绝下载")
                except ValueError:
                    pass
            disposition = response.headers.get("Content-Disposition", "")
            filename = _filename_from_content_disposition(disposition) or Path(unquote(parsed.path)).name or "multitrack.zip"
            if not filename.lower().endswith(".zip"):
                filename = f"{Path(filename).stem or 'multitrack'}.zip"
            output_path = download_root / f"{uuid4().hex}-{Path(filename).name}"
            total = 0
            with output_path.open("wb") as target:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_MULTITRACK_DOWNLOAD_BYTES:
                        raise HTTPException(status_code=400, detail="分轨 ZIP 过大，已停止下载")
                    target.write(chunk)
            if total <= 0:
                raise HTTPException(status_code=400, detail="下载结果为空")
            return output_path, filename
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"下载 ZIP 失败：{exc}") from exc


def _filename_from_content_disposition(disposition: str) -> str:
    if not disposition:
        return ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, flags=re.I)
    if match:
        return Path(unquote(match.group(1).strip().strip('"'))).name
    match = re.search(r'filename="?([^";]+)"?', disposition, flags=re.I)
    if match:
        return Path(match.group(1).strip()).name
    return ""


def _import_multitrack_zip(zip_path: Path, *, filename: str, source_id: str | None = None) -> dict[str, Any]:
    job_id = uuid4().hex
    job_dir = _job_dir(job_id)
    stems_dir = job_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    temp_root = codeyun_temp_root("music_multitrack_import") / job_id
    temp_root.mkdir(parents=True, exist_ok=True)

    used_keys: set[str] = set()
    custom_stems: list[dict[str, Any]] = []
    stem_paths: list[Path] = []
    try:
        with ZipFile(zip_path) as archive:
            audio_infos = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and Path(info.filename).suffix.lower() in MULTITRACK_AUDIO_EXTENSIONS
                and "__macosx" not in info.filename.lower()
            ]
            audio_infos.sort(key=lambda info: info.filename.lower())
            if not audio_infos:
                raise HTTPException(status_code=400, detail="ZIP 中没有可导入的音频文件")
            if len(audio_infos) > MAX_MULTITRACK_IMPORT_FILES:
                audio_infos = audio_infos[:MAX_MULTITRACK_IMPORT_FILES]
            for index, info in enumerate(audio_infos, start=1):
                source_name = Path(info.filename).name
                label = _clean_track_label(source_name)
                stem = _safe_stem_key(label or f"track-{index}", used_keys)
                temp_input = temp_root / f"{index:02d}-{source_name}"
                temp_input.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, temp_input.open("wb") as target:
                    shutil.copyfileobj(source, target)
                output_name = f"{stem}.mp3"
                output_path = stems_dir / output_name
                _convert_audio_to_mp3(temp_input, output_path)
                role = _guess_multitrack_role(label)
                custom_stems.append({
                    "stem": stem,
                    "label": label,
                    "role": role,
                    "filename": output_name,
                    "source_filename": source_name,
                    "size": output_path.stat().st_size,
                })
                stem_paths.append(output_path)
    except BadZipFile as exc:
        raise HTTPException(status_code=400, detail="ZIP 文件无效") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    original_path = job_dir / "original.mp3"
    _mix_multitrack_original(stem_paths, original_path)
    _write_custom_stems(job_id, custom_stems)
    now = time.time()
    source = next((item for item in _multitrack_library_sources() if item["id"] == source_id), None)
    job = {
        "job_id": job_id,
        "filename": Path(filename or "multitrack.zip").stem[:120] or "真实分轨素材",
        "status": "completed",
        "engine": "multitrack_zip",
        "model": "user_multitrack_zip",
        "expected_stems": [item["stem"] for item in custom_stems],
        "input_kind": "multitrack_zip",
        "source_id": source_id or "",
        "source_name": source["name"] if source else "",
        "created_at": now,
        "updated_at": now,
        "elapsed_ms": None,
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
        "task_message": f"已导入 {len(custom_stems)} 条真实分轨",
        "error": None,
    }
    return _upsert_job(job)


def _run_piano_stem_transcription(job_id: str, piano_path: Path, job: dict[str, Any], context: LongTaskContext) -> bool:
    if not piano_path.exists() or not BASIC_PITCH_PYTHON.exists() or not PIANO_TRANSCRIPTION_SCRIPT.exists():
        return False

    context.heartbeat(stage="transcribing", message="正在生成钢琴轨扒谱")
    raw_title = Path(str(job.get("filename") or "音频")).stem.strip() or "音频"
    title = f"{raw_title} 钢琴轨扒谱"
    version = time.strftime("v%Y%m%d-%H%M%S")
    output_dir = TRANSCRIPTION_ROOT / f"{job_id}-piano-stem"
    clean_output_dir = TRANSCRIPTION_ROOT / f"{job_id}-piano-stem-clean"
    melody_output_dir = TRANSCRIPTION_ROOT / f"{job_id}-melody-skeleton"
    command = [
        os.fspath(BASIC_PITCH_PYTHON),
        os.fspath(PIANO_TRANSCRIPTION_SCRIPT),
        "--input",
        os.fspath(piano_path),
        "--output-dir",
        os.fspath(output_dir),
        "--clean-output-dir",
        os.fspath(clean_output_dir),
        "--melody-output-dir",
        os.fspath(melody_output_dir),
        "--job-id",
        job_id,
        "--title",
        title,
        "--version",
        version,
        "--tempo-bpm",
        "80.75",
        "--beats-per-bar",
        "8",
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    completed = subprocess.run(
        command,
        cwd=os.fspath(MUSIC_TOOLS_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
        timeout=20 * 60,
    )
    log_text = completed.stdout or ""
    (_job_dir(job_id) / "piano-transcription.log").write_text(log_text, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(log_text.strip() or f"piano transcription failed with exit code {completed.returncode}")
    return (
        (output_dir / "score-manifest.json").is_file()
        or (clean_output_dir / "score-manifest.json").is_file()
        or (melody_output_dir / "score-manifest.json").is_file()
    )


def _run_humming_transcription(
    job_id: str,
    input_path: Path,
    job: dict[str, Any],
    context: LongTaskContext,
    *,
    tempo_bpm: float,
    beats_per_bar: int,
) -> dict[str, Any]:
    if not BASIC_PITCH_PYTHON.exists() or not PIANO_TRANSCRIPTION_SCRIPT.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Basic Pitch 转写工具未安装：{BASIC_PITCH_PYTHON}",
        )

    context.heartbeat(stage="transcribing", message="正在把哼唱转成主旋律草稿")
    raw_title = Path(str(job.get("filename") or "哼唱")).stem.strip() or "哼唱"
    title = f"{raw_title} 哼唱转谱"
    version = time.strftime("v%Y%m%d-%H%M%S")
    output_dir = TRANSCRIPTION_ROOT / f"{job_id}-humming-raw"
    clean_output_dir = TRANSCRIPTION_ROOT / f"{job_id}-humming-clean"
    melody_output_dir = TRANSCRIPTION_ROOT / f"{job_id}-humming-melody"
    command = [
        os.fspath(BASIC_PITCH_PYTHON),
        os.fspath(PIANO_TRANSCRIPTION_SCRIPT),
        "--input",
        os.fspath(input_path),
        "--output-dir",
        os.fspath(output_dir),
        "--clean-output-dir",
        os.fspath(clean_output_dir),
        "--melody-output-dir",
        os.fspath(melody_output_dir),
        "--job-id",
        job_id,
        "--title",
        title,
        "--version",
        version,
        "--tempo-bpm",
        str(tempo_bpm),
        "--beats-per-bar",
        str(beats_per_bar),
        "--onset-threshold",
        "0.35",
        "--frame-threshold",
        "0.25",
        "--minimum-note-length",
        "60",
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    started_at = time.time()
    completed = subprocess.run(
        command,
        cwd=os.fspath(MUSIC_TOOLS_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
        timeout=HUMMING_TRANSCRIPTION_TIMEOUT_SECONDS,
    )
    log_text = completed.stdout or ""
    (_job_dir(job_id) / "humming-transcription.log").write_text(log_text, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(log_text.strip() or f"humming transcription failed with exit code {completed.returncode}")
    if not (melody_output_dir / "score-manifest.json").is_file():
        raise RuntimeError("哼唱转写没有生成主旋律草稿")

    completed_job = _get_indexed_job(job_id) or {"job_id": job_id}
    completed_job.update({
        "job_id": job_id,
        "status": "completed",
        "task_id": context.task_id,
        "engine": "basic_pitch_humming",
        "model": "basic_pitch",
        "expected_stems": [],
        "elapsed_ms": int(round((time.time() - started_at) * 1000)),
        "files": _list_job_files(job_id),
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
        "error": None,
        "task_message": "哼唱转谱完成，已生成主旋律草稿",
        "task_stage": "completed",
        "updated_at": time.time(),
    })
    _upsert_job(completed_job)
    return completed_job


def _run_demucs(job_id: str, input_path: Path, context: LongTaskContext) -> dict[str, Any]:
    if not DEMUCS_PYTHON.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Demucs 未安装：{DEMUCS_PYTHON}",
        )

    job_dir = _job_dir(job_id)
    output_root = job_dir / "demucs"
    output_root.mkdir(parents=True, exist_ok=True)

    context.heartbeat(stage="separating", message="正在使用 Demucs 分离音轨")
    command = [
        os.fspath(DEMUCS_PYTHON),
        "-m",
        "demucs",
        "-n",
        DEMUCS_MODEL,
        "--mp3",
        "--out",
        os.fspath(output_root),
        "--filename",
        "{stem}.{ext}",
        os.fspath(input_path),
    ]
    started_at = time.time()
    completed = subprocess.run(
        command,
        cwd=os.fspath(MUSIC_TOOLS_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
        timeout=30 * 60,
    )
    log_text = completed.stdout or ""
    (job_dir / "demucs.log").write_text(log_text, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(log_text.strip() or f"Demucs failed with exit code {completed.returncode}")

    context.heartbeat(stage="collecting", message="正在整理输出音轨")
    source_dir = output_root / DEMUCS_MODEL
    for stem in ("vocals", "other", "bass", "drums"):
        source_file = source_dir / f"{stem}.mp3"
        if not source_file.exists():
            raise RuntimeError(f"Demucs 输出缺少 {stem}.mp3")
        shutil.copy2(source_file, _stem_output_file(job_id, stem))

    job = _get_indexed_job(job_id) or {"job_id": job_id}
    job.update({
        "job_id": job_id,
        "status": "completed",
        "task_id": context.task_id,
        "engine": "demucs",
        "model": DEMUCS_MODEL,
        "expected_stems": list(SEPARATED_STEMS_BY_ENGINE["demucs"]),
        "elapsed_ms": int(round((time.time() - started_at) * 1000)),
        "files": _list_job_files(job_id),
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
        "error": None,
        "task_message": "分轨完成",
        "task_stage": "completed",
        "updated_at": time.time(),
    })
    _upsert_job(job)
    return job


def _run_audio_separator_6s(job_id: str, input_path: Path, context: LongTaskContext) -> dict[str, Any]:
    if not AUDIO_SEPARATOR_EXE.exists():
        raise HTTPException(
            status_code=503,
            detail=f"audio-separator 未安装：{AUDIO_SEPARATOR_EXE}",
        )

    job_dir = _job_dir(job_id)
    output_root = job_dir / "audio-separator"
    output_root.mkdir(parents=True, exist_ok=True)
    AUDIO_SEPARATOR_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    context.heartbeat(stage="separating", message="正在使用 audio-separator 六轨细分")
    command = [
        os.fspath(AUDIO_SEPARATOR_EXE),
        "--model_file_dir",
        os.fspath(AUDIO_SEPARATOR_MODEL_DIR),
        "--output_dir",
        os.fspath(output_root),
        "--output_format",
        "MP3",
        "-m",
        AUDIO_SEPARATOR_MODEL,
        os.fspath(input_path),
    ]
    started_at = time.time()
    completed = subprocess.run(
        command,
        cwd=os.fspath(MUSIC_TOOLS_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
        timeout=60 * 60,
    )
    log_text = completed.stdout or ""
    (job_dir / "audio-separator.log").write_text(log_text, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(log_text.strip() or f"audio-separator failed with exit code {completed.returncode}")

    context.heartbeat(stage="collecting", message="正在整理六轨输出")
    output_stems = {
        "vocals": "Vocals",
        "drums": "Drums",
        "bass": "Bass",
        "guitar": "Guitar",
        "piano": "Piano",
        "other": "Other",
    }
    for stem, output_label in output_stems.items():
        matches = sorted(output_root.glob(f"*({output_label})_htdemucs_6s.mp3"))
        if not matches:
            raise RuntimeError(f"audio-separator 输出缺少 {output_label}")
        shutil.copy2(matches[0], _stem_output_file(job_id, stem))

    job = _get_indexed_job(job_id) or {"job_id": job_id}
    transcription_done = False
    transcription_error: str | None = None
    try:
        transcription_done = _run_piano_stem_transcription(job_id, _stem_file(job_id, "piano"), job, context)
    except Exception as exc:
        transcription_error = str(exc)
    if transcription_error:
        task_message = f"六轨细分完成，钢琴轨扒谱失败：{transcription_error[:120]}"
    elif transcription_done:
        task_message = "六轨细分完成，钢琴轨扒谱已生成"
    else:
        task_message = "六轨细分完成"
    job.update({
        "job_id": job_id,
        "status": "completed",
        "task_id": context.task_id,
        "engine": "audio_separator_6s",
        "model": "htdemucs_6s",
        "expected_stems": list(SEPARATED_STEMS_BY_ENGINE["audio_separator_6s"]),
        "elapsed_ms": int(round((time.time() - started_at) * 1000)),
        "files": _list_job_files(job_id),
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
        "error": None,
        "task_message": task_message,
        "task_stage": "completed",
        "updated_at": time.time(),
    })
    _upsert_job(job)
    return job


@router.get("/info", response_model=MusicToolInfo)
def get_music_tool_info():
    return MusicToolInfo(
        demucs_installed=DEMUCS_PYTHON.exists(),
        demucs_python=os.fspath(DEMUCS_PYTHON),
        audio_separator_installed=AUDIO_SEPARATOR_EXE.exists(),
        audio_separator_exe=os.fspath(AUDIO_SEPARATOR_EXE),
        work_root=os.fspath(_storage_root()),
    )


@router.get("/instrument-registry", response_model=MusicInstrumentRegistry)
def get_music_instrument_registry():
    if not INSTRUMENT_REGISTRY_PATH.exists():
        raise HTTPException(status_code=404, detail="乐器资料表尚未生成")
    try:
        payload = json.loads(INSTRUMENT_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="乐器资料表读取失败") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("instruments"), list):
        raise HTTPException(status_code=500, detail="乐器资料表格式无效")
    return payload


@router.get("/open-scores", response_model=OpenScoreWorkList)
def list_open_scores():
    return OpenScoreWorkList(works=list_open_score_works())


@router.get("/open-scores/{work_id}")
def get_open_score(work_id: str):
    try:
        return get_open_score_work(work_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Open score work not found") from exc
    except MidiParseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail="Open score download or cache failed") from exc


@router.get("/multitrack-library", response_model=MultitrackLibraryList)
def list_multitrack_library():
    return MultitrackLibraryList(sources=_multitrack_library_sources())


@router.post("/multitrack-import")
async def import_multitrack_zip(file: UploadFile = File(...), source_id: str = Form("")):
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="请上传包含多个音轨的 ZIP 文件")
    temp_dir = codeyun_temp_root("music_multitrack_uploads")
    temp_path = temp_dir / f"{uuid4().hex}.zip"
    try:
        with temp_path.open("wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
        return _import_multitrack_zip(temp_path, filename=file.filename or "multitrack.zip", source_id=source_id.strip() or None)
    finally:
        temp_path.unlink(missing_ok=True)


@router.post("/multitrack-import-url")
def import_multitrack_zip_url(payload: MultitrackUrlImportRequest):
    temp_path: Path | None = None
    try:
        temp_path, downloaded_name = _download_multitrack_zip(payload.url)
        filename = (payload.filename or downloaded_name or "multitrack.zip").strip()
        return _import_multitrack_zip(temp_path, filename=filename, source_id=(payload.source_id or "").strip() or None)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@router.get("/jobs")
def list_music_jobs():
    return {"jobs": _list_indexed_jobs()}


@router.get("/jobs/{job_id}")
def get_music_job(job_id: str):
    job = _get_indexed_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    return job


@router.patch("/jobs/{job_id}")
def update_music_job(job_id: str, payload: MusicJobUpdate):
    filename = payload.filename.strip()
    if not filename:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if len(filename) > 160:
        raise HTTPException(status_code=400, detail="名称不能超过 160 个字符")
    updated = _update_indexed_job(job_id, {"filename": filename})
    if updated is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    return updated


@router.get("/jobs/{job_id}/scores", response_model=MusicScoreList)
def list_music_job_scores(job_id: str):
    if _get_indexed_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    scores: list[MusicScoreInfo] = []
    for directory, manifest in _score_manifest_candidates(job_id):
        info = _score_info(job_id, directory, manifest)
        if info is not None:
            scores.append(info)
    return MusicScoreList(scores=scores)


@router.get("/jobs/{job_id}/score", response_model=MusicScoreInfo)
def get_music_job_score(job_id: str, kind: str | None = None):
    if _get_indexed_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    score = _get_score_manifest(job_id, kind)
    if score is None:
        raise HTTPException(status_code=404, detail="Score not found")
    directory, manifest = score
    info = _score_info(job_id, directory, manifest)
    if info is None:
        raise HTTPException(status_code=404, detail="Score files not found")
    return info


@router.get("/jobs/{job_id}/creative-brief", response_model=MusicCreativeBrief)
def get_music_job_creative_brief(job_id: str):
    job = _get_indexed_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    return _creative_brief_for_job(job)


@router.get("/jobs/{job_id}/creative-prompts", response_model=MusicCreativePromptRecordList)
def list_music_job_creative_prompts(job_id: str):
    if _get_indexed_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    return MusicCreativePromptRecordList(records=[MusicCreativePromptRecord(**record) for record in _load_creative_prompt_records(job_id)])


@router.post("/jobs/{job_id}/creative-prompts", response_model=MusicCreativePromptRecord)
def save_music_job_creative_prompt(job_id: str, payload: MusicCreativePromptSaveRequest):
    return _save_creative_prompt_record(job_id, payload)


@router.post("/separate")
async def start_music_separation(file: UploadFile = File(...), engine: str = Form("demucs")):
    raw_extension = Path(file.filename or "").suffix.lower()
    if raw_extension not in SUPPORTED_MEDIA_EXTENSIONS:
        raise HTTPException(status_code=400, detail="不支持的音频或视频格式")

    selected_engine = _normalize_engine(engine)
    original_name = _safe_filename(file.filename or "audio.mp3")
    extension = Path(original_name).suffix.lower()
    is_video_input = extension in SUPPORTED_VIDEO_EXTENSIONS

    job_id = uuid4().hex
    job_dir = _job_dir(job_id)
    input_path = job_dir / original_name
    original_path = job_dir / ("original.mp3" if is_video_input else f"original{extension}")

    with input_path.open("wb") as target:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
    if is_video_input:
        _extract_video_audio(input_path, original_path)
    else:
        shutil.copy2(input_path, original_path)
    now = time.time()
    _upsert_job({
        "job_id": job_id,
        "filename": original_name,
        "status": "queued",
        "engine": selected_engine,
        "model": "htdemucs_6s" if selected_engine == "audio_separator_6s" else DEMUCS_MODEL,
        "expected_stems": list(SEPARATED_STEMS_BY_ENGINE[selected_engine]),
        "input_kind": "video" if is_video_input else "audio",
        "created_at": now,
        "updated_at": now,
        "elapsed_ms": None,
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
    })

    def run(context: LongTaskContext) -> dict[str, Any]:
        job = _get_indexed_job(job_id) or {"job_id": job_id, "filename": original_name}
        job.update({"status": "running", "updated_at": time.time()})
        _upsert_job(job)
        try:
            if selected_engine == "audio_separator_6s":
                return _run_audio_separator_6s(job_id, original_path, context)
            return _run_demucs(job_id, original_path, context)
        except Exception:
            failed = _get_indexed_job(job_id) or {"job_id": job_id, "filename": original_name}
            failed.update({"status": "failed", "updated_at": time.time()})
            _upsert_job(failed)
            raise

    task_payload = _task_manager.start(
        run,
        stage="queued",
        message="分轨任务已排队",
        metadata={
            "job_id": job_id,
            "filename": original_name,
            "engine": selected_engine,
            "expected_stems": list(SEPARATED_STEMS_BY_ENGINE[selected_engine]),
            "input_kind": "video" if is_video_input else "audio",
            "files": _list_job_files(job_id),
        },
    )
    job = _get_indexed_job(job_id) or {"job_id": job_id, "filename": original_name}
    job["task_id"] = task_payload["task_id"]
    _upsert_job(job)
    return task_payload


@router.post("/humming-transcribe")
async def start_humming_transcription(
    file: UploadFile = File(...),
    tempo_bpm: float = Form(96.0),
    beats_per_bar: int = Form(4),
):
    raw_extension = Path(file.filename or "").suffix.lower()
    if raw_extension not in SUPPORTED_MEDIA_EXTENSIONS:
        raise HTTPException(status_code=400, detail="不支持的音频或视频格式")
    if tempo_bpm < 40 or tempo_bpm > 240:
        raise HTTPException(status_code=400, detail="BPM 需要在 40-240 之间")
    if beats_per_bar < 2 or beats_per_bar > 12:
        raise HTTPException(status_code=400, detail="每小节拍数需要在 2-12 之间")

    original_name = _safe_filename(file.filename or "humming.webm")
    extension = Path(original_name).suffix.lower()
    is_video_input = extension in SUPPORTED_VIDEO_EXTENSIONS

    job_id = uuid4().hex
    job_dir = _job_dir(job_id)
    input_path = job_dir / original_name
    original_path = job_dir / ("original.mp3" if is_video_input else f"original{extension}")

    with input_path.open("wb") as target:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
    if is_video_input:
        _extract_video_audio(input_path, original_path)
    else:
        shutil.copy2(input_path, original_path)

    now = time.time()
    _upsert_job({
        "job_id": job_id,
        "filename": original_name,
        "status": "queued",
        "engine": "basic_pitch_humming",
        "model": "basic_pitch",
        "expected_stems": [],
        "input_kind": "humming",
        "created_at": now,
        "updated_at": now,
        "elapsed_ms": None,
        "task_message": "哼唱转谱任务已排队",
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
    })

    def run(context: LongTaskContext) -> dict[str, Any]:
        job = _get_indexed_job(job_id) or {"job_id": job_id, "filename": original_name}
        job.update({
            "status": "running",
            "task_id": context.task_id,
            "updated_at": time.time(),
            "task_message": "正在分析哼唱旋律",
        })
        _upsert_job(job)
        try:
            return _run_humming_transcription(
                job_id,
                original_path,
                job,
                context,
                tempo_bpm=tempo_bpm,
                beats_per_bar=beats_per_bar,
            )
        except Exception:
            failed = _get_indexed_job(job_id) or {"job_id": job_id, "filename": original_name}
            failed.update({"status": "failed", "task_id": context.task_id, "updated_at": time.time()})
            _upsert_job(failed)
            raise

    task_payload = _task_manager.start(
        run,
        stage="queued",
        message="哼唱转谱任务已排队",
        metadata={
            "job_id": job_id,
            "filename": original_name,
            "engine": "basic_pitch_humming",
            "expected_stems": [],
            "input_kind": "humming",
            "files": _list_job_files(job_id),
        },
    )
    job = _get_indexed_job(job_id) or {"job_id": job_id, "filename": original_name}
    job["task_id"] = task_payload["task_id"]
    _upsert_job(job)
    return task_payload


@router.post("/jobs/{job_id}/rerun")
def rerun_music_job(job_id: str, engine: str = Form("audio_separator_6s")):
    job = _get_indexed_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    if str(job.get("status") or "") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="当前任务仍在运行")

    selected_engine = _normalize_engine(engine)
    original_path = _stem_file(job_id, "original")
    if not original_path.exists():
        raise HTTPException(status_code=404, detail="原始音频不存在，无法重新分离")
    _clear_generated_audio_outputs(job_id)
    _clear_generated_score_outputs(job_id)

    now = time.time()
    job.update({
        "status": "queued",
        "engine": selected_engine,
        "model": "htdemucs_6s" if selected_engine == "audio_separator_6s" else DEMUCS_MODEL,
        "expected_stems": list(SEPARATED_STEMS_BY_ENGINE[selected_engine]),
        "task_id": None,
        "updated_at": now,
        "elapsed_ms": None,
        "error": None,
        "task_message": "重新解析任务已排队",
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
    })
    _upsert_job(job)

    def run(context: LongTaskContext) -> dict[str, Any]:
        running_job = _get_indexed_job(job_id) or dict(job)
        running_job.update({
            "status": "running",
            "task_id": context.task_id,
            "updated_at": time.time(),
            "task_message": "正在重新解析",
        })
        _upsert_job(running_job)
        try:
            if selected_engine == "audio_separator_6s":
                return _run_audio_separator_6s(job_id, original_path, context)
            return _run_demucs(job_id, original_path, context)
        except Exception:
            failed = _get_indexed_job(job_id) or dict(job)
            failed.update({"status": "failed", "task_id": context.task_id, "updated_at": time.time()})
            _upsert_job(failed)
            raise

    task_payload = _task_manager.start(
        run,
        stage="queued",
        message="重新解析任务已排队",
        metadata={
            "job_id": job_id,
            "filename": str(job.get("filename") or original_path.name),
            "engine": selected_engine,
            "expected_stems": list(SEPARATED_STEMS_BY_ENGINE[selected_engine]),
            "files": _list_job_files(job_id),
        },
    )
    job["task_id"] = task_payload["task_id"]
    _upsert_job(job)
    return task_payload


@router.get("/tasks/{task_id}")
def get_music_separation_task(task_id: str):
    try:
        payload = _task_manager.serialize_task(task_id)
    except LongTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Music separation task not found") from exc
    job_id = str(payload.get("metadata", {}).get("job_id") or payload.get("result", {}).get("job_id") or "")
    if job_id:
        payload.setdefault("metadata", {})["files"] = _list_job_files(job_id)
    return payload


@router.get("/jobs/{job_id}/audio/{stem}")
def get_music_job_audio(job_id: str, stem: str):
    if _get_indexed_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    path = _stem_file(job_id, stem)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/jobs/{job_id}/score/file/{filename}")
def get_music_job_score_file(job_id: str, filename: str):
    if _get_indexed_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    path = _score_file(job_id, filename)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/jobs/{job_id}/log")
def get_music_job_log(job_id: str):
    job = _get_indexed_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    engine = str(job.get("engine") or "")
    if engine == "audio_separator_6s":
        log_name = "audio-separator.log"
    elif engine == "basic_pitch_humming":
        log_name = "humming-transcription.log"
    else:
        log_name = "demucs.log"
    path = _job_dir(job_id) / log_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)
