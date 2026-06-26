from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta, timezone
import html
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import requests
from sqlalchemy import and_, or_
from sqlmodel import Session, select

from backend.core.runtime.background_task_queue import background_task_queue
from backend.core.notes.identity import allocate_new_note_identity
from backend.core.notes.refs import note_public_id
from backend.core.notes.semantics import (
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DEFAULT,
    NOTE_LIFECYCLE_STAGE_DEFAULT,
    NOTE_SCENE_DEFAULT,
    derive_legacy_semantics_from_taxonomy,
)
from backend.core.settings import get_settings
from backend.models import NoteNode


RUANYF_WEEKLY_REPO_OWNER = "ruanyf"
RUANYF_WEEKLY_REPO_NAME = "weekly"
RUANYF_WEEKLY_BRANCH = "master"
RUANYF_WEEKLY_README_URL = (
    f"https://raw.githubusercontent.com/{RUANYF_WEEKLY_REPO_OWNER}/"
    f"{RUANYF_WEEKLY_REPO_NAME}/{RUANYF_WEEKLY_BRANCH}/README.md"
)
RUANYF_WEEKLY_GITHUB_BLOB_BASE_URL = (
    f"https://github.com/{RUANYF_WEEKLY_REPO_OWNER}/"
    f"{RUANYF_WEEKLY_REPO_NAME}/blob/{RUANYF_WEEKLY_BRANCH}"
)
RUANYF_WEEKLY_GITHUB_API_COMMITS_URL = (
    f"https://api.github.com/repos/{RUANYF_WEEKLY_REPO_OWNER}/"
    f"{RUANYF_WEEKLY_REPO_NAME}/commits"
)
RUANYF_WEEKLY_TIMEZONE = ZoneInfo("Asia/Shanghai")

RUANYF_WEEKLY_ISSUE_FIELD = "__ruanyf_weekly_issue_number"
RUANYF_WEEKLY_SOURCE_URL_FIELD = "__ruanyf_weekly_source_url"
RUANYF_WEEKLY_PUBLISHED_AT_FIELD = "__ruanyf_weekly_published_at"
RUANYF_WEEKLY_COMMIT_SHA_FIELD = "__ruanyf_weekly_commit_sha"

RUANYF_WEEKLY_TASK_NAME = "ruanyf_weekly_note"

README_ISSUE_RE = re.compile(
    r"^\s*-\s*第\s*(?P<number>\d+)\s*期[:：]\s*"
    r"\[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)",
    re.MULTILINE,
)
NOTE_TITLE_ISSUE_RE = re.compile(r"周刊.*?(?P<number>\d+)\s*期")
ISSUE_PATH_RE = re.compile(r"issue-(?P<number>\d+)\.md", re.IGNORECASE)
RELEASE_COMMIT_RE_TEMPLATE = r"\brelease\s+issue\s+{issue_number}\b"

weekly_note_scheduler = BackgroundScheduler(timezone=RUANYF_WEEKLY_TIMEZONE)


@dataclass(frozen=True)
class RuanyfWeeklyIssue:
    number: int
    title: str
    path: str

    @property
    def source_url(self) -> str:
        return f"{RUANYF_WEEKLY_GITHUB_BLOB_BASE_URL}/{self.path}"


@dataclass(frozen=True)
class RuanyfWeeklyPublication:
    published_at: datetime
    commit_sha: str


@dataclass(frozen=True)
class RuanyfWeeklyLocalState:
    target_user_id: int | None
    max_issue_number: int
    title_template_note: NoteNode | None


@dataclass(frozen=True)
class RuanyfWeeklyJobResult:
    status: str
    created_note_id: str | None = None
    issue_number: int | None = None
    message: str = ""
    next_run_at: str | None = None


def parse_ruanyf_weekly_readme(text: str) -> RuanyfWeeklyIssue | None:
    issues: list[RuanyfWeeklyIssue] = []
    for match in README_ISSUE_RE.finditer(text or ""):
        path = _normalize_issue_path(match.group("path"))
        if not path:
            continue
        issues.append(
            RuanyfWeeklyIssue(
                number=int(match.group("number")),
                title=match.group("title").strip(),
                path=path,
            )
        )
    if not issues:
        return None
    return max(issues, key=lambda issue: issue.number)


def fetch_latest_ruanyf_weekly_issue() -> RuanyfWeeklyIssue | None:
    response = requests.get(
        RUANYF_WEEKLY_README_URL,
        headers={"User-Agent": "CodeYun ruanyf-weekly-note-scheduler"},
        timeout=20,
    )
    response.raise_for_status()
    return parse_ruanyf_weekly_readme(response.text)


def parse_ruanyf_weekly_publication(
    issue_number: int,
    commits: list[dict[str, Any]],
) -> RuanyfWeeklyPublication | None:
    parsed: list[tuple[datetime, str, str]] = []
    for item in commits:
        if not isinstance(item, dict):
            continue
        commit = item.get("commit")
        if not isinstance(commit, dict):
            continue
        commit_at = _parse_github_commit_datetime(commit)
        if commit_at is None:
            continue
        parsed.append(
            (
                commit_at,
                str(item.get("sha") or "").strip(),
                str(commit.get("message") or ""),
            )
        )
    if not parsed:
        return None

    release_re = re.compile(
        RELEASE_COMMIT_RE_TEMPLATE.format(issue_number=int(issue_number)),
        re.IGNORECASE,
    )
    release_matches = [item for item in parsed if release_re.search(item[2])]
    if release_matches:
        published_at, commit_sha, _ = min(release_matches, key=lambda item: item[0])
        return RuanyfWeeklyPublication(published_at=published_at, commit_sha=commit_sha)

    published_at, commit_sha, _ = min(parsed, key=lambda item: item[0])
    return RuanyfWeeklyPublication(published_at=published_at, commit_sha=commit_sha)


def fetch_ruanyf_weekly_publication(issue: RuanyfWeeklyIssue) -> RuanyfWeeklyPublication | None:
    response = requests.get(
        RUANYF_WEEKLY_GITHUB_API_COMMITS_URL,
        params={"path": issue.path, "per_page": 100},
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CodeYun ruanyf-weekly-note-scheduler",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return None
    return parse_ruanyf_weekly_publication(issue.number, payload)


def build_ruanyf_weekly_note_title(
    issue_number: int,
    issue_title: str,
    template_title: str | None,
) -> str:
    normalized_title = str(issue_title or "").strip() or f"第 {int(issue_number)} 期"
    template = str(template_title or "").strip()
    match = re.match(r"^(?P<prefix>.*?)(?P<number>\d+)(?P<suffix>\s*期[:：]).*$", template)
    if match:
        return f"{match.group('prefix')}{int(issue_number)}{match.group('suffix')}{normalized_title}"
    return f"科技周刊第{int(issue_number)}期：{normalized_title}"


def maybe_create_ruanyf_weekly_note(
    session: Session,
    *,
    now: datetime | None = None,
) -> RuanyfWeeklyJobResult:
    current_time = now or datetime.now(RUANYF_WEEKLY_TIMEZONE)
    window = _resolve_current_friday_window(current_time)
    if window is None:
        return RuanyfWeeklyJobResult(status="outside_schedule_window")
    if ruanyf_weekly_note_completed_for_current_window(session, now=current_time):
        return RuanyfWeeklyJobResult(status="already_completed_window")

    try:
        latest_issue = fetch_latest_ruanyf_weekly_issue()
    except Exception as exc:
        return RuanyfWeeklyJobResult(status="source_unavailable", message=str(exc))
    if latest_issue is None:
        return RuanyfWeeklyJobResult(status="latest_issue_not_found")

    local_state = collect_ruanyf_weekly_local_state(session)
    if local_state.target_user_id is None:
        return RuanyfWeeklyJobResult(
            status="no_target_user",
            issue_number=latest_issue.number,
            message="No existing ruanyf weekly note was found",
        )
    if latest_issue.number <= local_state.max_issue_number:
        return RuanyfWeeklyJobResult(
            status="not_newer_than_local",
            issue_number=latest_issue.number,
        )

    try:
        publication = fetch_ruanyf_weekly_publication(latest_issue)
    except Exception as exc:
        publication = RuanyfWeeklyPublication(
            published_at=current_time,
            commit_sha="",
        )
    if publication is None:
        return RuanyfWeeklyJobResult(
            status="publication_not_found",
            issue_number=latest_issue.number,
        )
    if not _datetime_in_window(publication.published_at, window):
        return RuanyfWeeklyJobResult(
            status="published_outside_window",
            issue_number=latest_issue.number,
            message=publication.published_at.isoformat(),
        )

    if ruanyf_weekly_note_exists(
        session,
        user_id=local_state.target_user_id,
        issue_number=latest_issue.number,
        source_url=latest_issue.source_url,
    ):
        return RuanyfWeeklyJobResult(
            status="already_exists",
            issue_number=latest_issue.number,
        )

    note = create_ruanyf_weekly_note(
        session,
        user_id=local_state.target_user_id,
        issue=latest_issue,
        publication=publication,
        template_note=local_state.title_template_note,
    )
    return RuanyfWeeklyJobResult(
        status="created",
        created_note_id=note_public_id(note),
        issue_number=latest_issue.number,
    )


def collect_ruanyf_weekly_local_state(session: Session) -> RuanyfWeeklyLocalState:
    notes = session.exec(
        select(NoteNode).where(
            or_(
                NoteNode.title.contains("周刊"),
                NoteNode.content.contains("ruanyf/weekly"),
                NoteNode.content.contains("issue-"),
            )
        )
    ).all()
    weekly_notes = [
        (note, issue_number)
        for note in notes
        if (issue_number := extract_ruanyf_weekly_issue_number(note)) is not None
    ]
    if not weekly_notes:
        return RuanyfWeeklyLocalState(
            target_user_id=None,
            max_issue_number=0,
            title_template_note=None,
        )

    template_note, max_issue_number = max(weekly_notes, key=lambda item: item[1])
    return RuanyfWeeklyLocalState(
        target_user_id=int(template_note.user_id),
        max_issue_number=int(max_issue_number),
        title_template_note=template_note,
    )


def extract_ruanyf_weekly_issue_number(note: NoteNode) -> int | None:
    custom_value = _get_custom_field_value(note.custom_fields, RUANYF_WEEKLY_ISSUE_FIELD)
    custom_number = _coerce_positive_int(custom_value)
    if custom_number is not None:
        return custom_number

    title = str(note.title or "")
    if "科技" in title and "周刊" in title:
        title_match = NOTE_TITLE_ISSUE_RE.search(title)
        if title_match:
            return int(title_match.group("number"))

    content = str(note.content or "")
    content_match = ISSUE_PATH_RE.search(content)
    if "ruanyf/weekly" in content and content_match:
        return int(content_match.group("number"))
    return None


def ruanyf_weekly_note_exists(
    session: Session,
    *,
    user_id: int,
    issue_number: int,
    source_url: str,
) -> bool:
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == int(user_id))).all()
    for note in notes:
        if _note_matches_ruanyf_weekly_issue(note, issue_number=issue_number, source_url=source_url):
            return True
    return False


def create_ruanyf_weekly_note(
    session: Session,
    *,
    user_id: int,
    issue: RuanyfWeeklyIssue,
    publication: RuanyfWeeklyPublication,
    template_note: NoteNode | None,
) -> NoteNode:
    taxonomy = derive_legacy_semantics_from_taxonomy(
        [{"key": NOTE_CATEGORY_DEFAULT, "weight": 100}],
        primary_category=NOTE_CATEGORY_DEFAULT,
        note_form=NOTE_FORM_DEFAULT,
        note_scene=NOTE_SCENE_DEFAULT,
        lifecycle_stage=NOTE_LIFECYCLE_STAGE_DEFAULT,
    )
    now = time.time()
    source_url = issue.source_url
    published_at = _format_datetime_utc(publication.published_at)
    note_identity = allocate_new_note_identity(session)
    note = NoteNode(
        id=note_identity.primary_id,
        numeric_id=note_identity.numeric_id,
        legacy_id=note_identity.legacy_id,
        user_id=int(user_id),
        title=build_ruanyf_weekly_note_title(
            issue.number,
            issue.title,
            template_note.title if template_note else None,
        ),
        content=_build_initial_source_url_content(source_url),
        weight=0,
        node_type=taxonomy["node_type"],
        note_types=taxonomy["note_types"],
        note_categories=taxonomy["note_categories"],
        primary_category=taxonomy["primary_category"],
        note_form=taxonomy["note_form"],
        note_kind=taxonomy["note_kind"],
        note_scene=taxonomy["note_scene"],
        node_status=taxonomy["node_status"],
        lifecycle_stage=taxonomy["lifecycle_stage"],
        color=None,
        weight_mode=None,
        private_level=0,
        custom_fields=[
            [RUANYF_WEEKLY_ISSUE_FIELD, "number", int(issue.number)],
            [RUANYF_WEEKLY_SOURCE_URL_FIELD, "string", source_url],
            [RUANYF_WEEKLY_PUBLISHED_AT_FIELD, "string", published_at],
            [RUANYF_WEEKLY_COMMIT_SHA_FIELD, "string", publication.commit_sha],
        ],
        created_at=now,
        updated_at=now,
        start_at=float(publication.published_at.timestamp()),
        history=[],
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def run_ruanyf_weekly_note_job() -> RuanyfWeeklyJobResult:
    from backend.db import engine

    current_time = datetime.now(RUANYF_WEEKLY_TIMEZONE)
    with Session(engine) as session:
        result = maybe_create_ruanyf_weekly_note(session, now=current_time)
        if result.next_run_at is None and _ruanyf_weekly_should_retry(result.status):
            next_run_at = _next_ruanyf_weekly_retry_at(current_time)
            if next_run_at is not None:
                result = RuanyfWeeklyJobResult(
                    status=result.status,
                    created_note_id=result.created_note_id,
                    issue_number=result.issue_number,
                    message=result.message,
                    next_run_at=next_run_at.isoformat(),
                )
        print(
            "Ruanyf weekly note job finished: "
            f"status={result.status} issue={result.issue_number} "
            f"note={result.created_note_id} next_run_at={result.next_run_at}"
        )
        return result


def ruanyf_weekly_note_completed_for_current_window(
    session: Session,
    *,
    now: datetime | None = None,
) -> bool:
    window = _resolve_current_friday_window(now or datetime.now(RUANYF_WEEKLY_TIMEZONE))
    if window is None:
        return False
    return _ruanyf_weekly_note_exists_in_window(session, window)


def enqueue_ruanyf_weekly_note_job(*, now: datetime | None = None) -> str | None:
    from backend.db import engine

    with Session(engine) as session:
        if ruanyf_weekly_note_completed_for_current_window(session, now=now):
            return None
    return background_task_queue.enqueue(
        RUANYF_WEEKLY_TASK_NAME,
        run_ruanyf_weekly_note_job,
    )


def _ruanyf_weekly_should_retry(status: str) -> bool:
    return status not in {
        "created",
        "already_exists",
        "already_completed_window",
        "outside_schedule_window",
    }


def _next_ruanyf_weekly_retry_at(now: datetime, *, interval: timedelta = timedelta(hours=2)) -> datetime | None:
    local_now = _ensure_aware_datetime(now).astimezone(RUANYF_WEEKLY_TIMEZONE)
    if local_now.weekday() != 4:
        return None
    saturday = datetime.combine(
        local_now.date() + timedelta(days=1),
        datetime_time.min,
        tzinfo=RUANYF_WEEKLY_TIMEZONE,
    )
    candidate = local_now + interval
    return candidate if candidate < saturday else None


def init_ruanyf_weekly_note_scheduler() -> None:
    if get_settings().is_test:
        return
    if not weekly_note_scheduler.running:
        weekly_note_scheduler.start()
    weekly_note_scheduler.add_job(
        enqueue_ruanyf_weekly_note_job,
        CronTrigger(day_of_week="fri", hour="0-22/2", minute=0, timezone=RUANYF_WEEKLY_TIMEZONE),
        id="ruanyf_weekly_note_friday",
        replace_existing=True,
        max_instances=1,
    )
    weekly_note_scheduler.add_job(
        enqueue_ruanyf_weekly_note_job,
        CronTrigger(day_of_week="sat", hour=0, minute=0, timezone=RUANYF_WEEKLY_TIMEZONE),
        id="ruanyf_weekly_note_saturday_final",
        replace_existing=True,
        max_instances=1,
    )
    print("Ruanyf weekly note scheduled: Friday every 2 hours from 00:00, plus Saturday 00:00")


def shutdown_ruanyf_weekly_note_scheduler() -> None:
    if weekly_note_scheduler.running:
        weekly_note_scheduler.shutdown(wait=False)


def _normalize_issue_path(value: str) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    if "#" in path:
        path = path.split("#", 1)[0]
    if "?" in path:
        path = path.split("?", 1)[0]
    return path.lstrip("./")


def _parse_github_commit_datetime(commit: dict[str, Any]) -> datetime | None:
    for actor_key in ("author", "committer"):
        actor = commit.get(actor_key)
        if not isinstance(actor, dict):
            continue
        date_text = str(actor.get("date") or "").strip()
        parsed = _parse_iso_datetime(date_text)
        if parsed is not None:
            return parsed
    return None


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_current_friday_window(now: datetime) -> tuple[datetime, datetime] | None:
    local_now = _ensure_aware_datetime(now).astimezone(RUANYF_WEEKLY_TIMEZONE)
    if local_now.weekday() == 4:
        friday_date = local_now.date()
    elif local_now.weekday() == 5 and local_now.hour == 0:
        friday_date = local_now.date() - timedelta(days=1)
    else:
        return None
    start = datetime.combine(friday_date, datetime_time.min, tzinfo=RUANYF_WEEKLY_TIMEZONE)
    return start, start + timedelta(days=1)


def _datetime_in_window(value: datetime, window: tuple[datetime, datetime]) -> bool:
    candidate = _ensure_aware_datetime(value).astimezone(RUANYF_WEEKLY_TIMEZONE)
    start, end = window
    return start <= candidate < end


def _ruanyf_weekly_note_exists_in_window(
    session: Session,
    window: tuple[datetime, datetime],
) -> bool:
    start, end = window
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    candidates = session.exec(
        select(NoteNode).where(
            or_(
                NoteNode.title.contains("周刊"),
                NoteNode.content.contains("ruanyf/weekly"),
                NoteNode.content.contains("github.com/ruanyf/weekly"),
                and_(NoteNode.start_at >= start_ts, NoteNode.start_at < end_ts),
            )
        )
    ).all()
    for note in candidates:
        if extract_ruanyf_weekly_issue_number(note) is None:
            continue
        if not _note_has_ruanyf_weekly_source_marker(note):
            continue
        if _note_datetime_in_window(note, window):
            return True
    return False


def _note_has_ruanyf_weekly_source_marker(note: NoteNode) -> bool:
    source_url = str(_get_custom_field_value(note.custom_fields, RUANYF_WEEKLY_SOURCE_URL_FIELD) or "")
    content = str(note.content or "")
    return bool(source_url) or "ruanyf/weekly" in content or "github.com/ruanyf/weekly" in content


def _note_datetime_in_window(note: NoteNode, window: tuple[datetime, datetime]) -> bool:
    published_value = _get_custom_field_value(note.custom_fields, RUANYF_WEEKLY_PUBLISHED_AT_FIELD)
    published_at = _parse_iso_datetime(str(published_value or ""))
    if published_at is not None and _datetime_in_window(published_at, window):
        return True
    started_at = _parse_timestamp_datetime(getattr(note, "start_at", None))
    return started_at is not None and _datetime_in_window(started_at, window)


def _parse_timestamp_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _ensure_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=RUANYF_WEEKLY_TIMEZONE)
    return value


def _format_datetime_utc(value: datetime) -> str:
    return _ensure_aware_datetime(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_initial_source_url_content(source_url: str) -> str:
    escaped_url = html.escape(source_url, quote=True)
    escaped_text = html.escape(source_url)
    return f'<p><a href="{escaped_url}" target="_blank" rel="noopener noreferrer">{escaped_text}</a></p>'


def _get_custom_field_value(custom_fields: Any, key: str) -> Any:
    if isinstance(custom_fields, dict):
        return custom_fields.get(key)
    if isinstance(custom_fields, list):
        for item in custom_fields:
            if isinstance(item, (list, tuple)) and len(item) >= 3 and item[0] == key:
                return item[2]
            if isinstance(item, dict) and item.get("key") == key:
                return item.get("value")
    return None


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _note_matches_ruanyf_weekly_issue(
    note: NoteNode,
    *,
    issue_number: int,
    source_url: str,
) -> bool:
    if extract_ruanyf_weekly_issue_number(note) == int(issue_number):
        return True
    if source_url and source_url in str(note.content or ""):
        return True
    if _get_custom_field_value(note.custom_fields, RUANYF_WEEKLY_SOURCE_URL_FIELD) == source_url:
        return True
    return False
