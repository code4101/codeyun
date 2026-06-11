from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
import mimetypes
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.core.long_tasks import LongTaskContext, LongTaskManager, LongTaskNotFoundError
from backend.core.settings import get_settings

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
SEPARATED_STEMS_BY_ENGINE = {
    "demucs": ("vocals", "other", "bass", "drums"),
    "audio_separator_6s": ("vocals", "drums", "bass", "guitar", "piano", "other"),
}
MUSIC_TOOLS_ROOT = Path(r"D:\home\chenkunze\slns+\music-tools")
TRANSCRIPTION_ROOT = MUSIC_TOOLS_ROOT / "outputs" / "transcriptions"
DEMUCS_PYTHON = MUSIC_TOOLS_ROOT / ".venvs" / "demucs" / "Scripts" / "python.exe"
DEMUCS_MODEL = "htdemucs"
AUDIO_SEPARATOR_EXE = MUSIC_TOOLS_ROOT / ".venvs" / "audio-separator" / "Scripts" / "audio-separator.exe"
AUDIO_SEPARATOR_MODEL = "htdemucs_6s.yaml"
AUDIO_SEPARATOR_MODEL_DIR = MUSIC_TOOLS_ROOT / "models" / "audio-separator"
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


def _stem_file(job_id: str, stem: str) -> Path:
    if stem not in STEM_ORDER:
        raise HTTPException(status_code=404, detail="Unknown stem")
    job_dir = _job_dir(job_id)
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


def _public_file(job_id: str, stem: str, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "stem": stem,
        "filename": path.name,
        "url": f"/api/music-tools/jobs/{job_id}/audio/{stem}",
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def _list_job_files(job_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for stem in STEM_ORDER:
        path = _stem_file(job_id, stem)
        if path.exists():
            files.append(_public_file(job_id, stem, path))
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
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=30 * 60,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout or "ffmpeg audio extraction failed").strip())


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
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
        "task_message": "六轨细分完成",
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


@router.get("/jobs")
def list_music_jobs():
    return {"jobs": _list_indexed_jobs()}


@router.get("/jobs/{job_id}")
def get_music_job(job_id: str):
    job = _get_indexed_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Music job not found")
    return job


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
        "task_message": "重新分轨任务已排队",
        "log_url": f"/api/music-tools/jobs/{job_id}/log",
    })
    _upsert_job(job)

    def run(context: LongTaskContext) -> dict[str, Any]:
        running_job = _get_indexed_job(job_id) or dict(job)
        running_job.update({
            "status": "running",
            "task_id": context.task_id,
            "updated_at": time.time(),
            "task_message": "正在重新分轨",
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
        message="重新分轨任务已排队",
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
    path = _job_dir(job_id) / ("audio-separator.log" if job.get("engine") == "audio_separator_6s" else "demucs.log")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)
