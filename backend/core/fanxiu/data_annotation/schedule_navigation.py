from __future__ import annotations

import base64
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Pattern

import cv2
import numpy as np

from backend.core.fanxiu.runtime_gui import (
    GuiCandidate,
    RuntimeEntity,
    ocr_name_similarity,
    score_runtime_gui_pair,
)


SCHEDULE_SCENE_ID = 66
HEADER_SHAPE = "表头"
CALENDAR_SHAPE = "日历"
ACTIVITY_CARD_SHAPE = "活动卡片"
ACTIVITY_CARD_FORWARD_SHAPE = "活动卡片/前往"


def _activity_card_indicator_points(
    runtime: Any,
    frame_data_url: str,
) -> tuple[tuple[float, float], ...]:
    """Locate the real dot pager rendered at the bottom of #66's promo card."""

    if not isinstance(frame_data_url, str) or "," not in frame_data_url:
        return ()
    try:
        encoded = frame_data_url.split(",", 1)[1]
        image = cv2.imdecode(
            np.frombuffer(base64.b64decode(encoded), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        card_shape = runtime.shape(SCHEDULE_SCENE_ID, ACTIVITY_CARD_SHAPE)
        view = card_shape.parent_view
        box = runtime.runner._box(card_shape.raw, view.raw)
    except Exception:
        return ()
    if image is None:
        return ()

    height, width = image.shape[:2]
    left = max(0, min(width - 1, int(round(float(box.get("x") or 0)))))
    top = max(0, min(height - 1, int(round(float(box.get("y") or 0)))))
    right = max(left + 1, min(width, int(round(left + float(box.get("w") or 0)))))
    bottom = max(top + 1, min(height, int(round(top + float(box.get("h") or 0)))))
    pager_top = top + int((bottom - top) * 0.84)
    roi = image[pager_top:bottom, left:right]
    if roi.size == 0:
        return ()

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    dark = hsv[:, :, 2] < 145
    orange = (
        (hsv[:, :, 0] < 30)
        & (hsv[:, :, 1] > 120)
        & (hsv[:, :, 2] > 120)
    )
    mask = ((dark | orange) * 255).astype(np.uint8)
    _count, _labels, stats, centers = cv2.connectedComponentsWithStats(mask)
    candidates: list[tuple[float, float]] = []
    for stat, center in zip(stats[1:], centers[1:]):
        _x, _y, component_width, component_height, area = map(int, stat)
        if not 30 <= area <= 400:
            continue
        if not 6 <= component_width <= 25 or not 6 <= component_height <= 25:
            continue
        aspect = component_width / max(1, component_height)
        if not 0.5 <= aspect <= 1.6:
            continue
        candidates.append((left + float(center[0]), pager_top + float(center[1])))

    rows: list[list[tuple[float, float]]] = []
    for point in sorted(candidates, key=lambda item: item[1]):
        row = next(
            (item for item in rows if abs(statistics.median(p[1] for p in item) - point[1]) <= 4.0),
            None,
        )
        if row is None:
            rows.append([point])
        else:
            row.append(point)
    if not rows:
        return ()
    pager_row = max(rows, key=len)
    pager_row.sort(key=lambda item: item[0])
    if len(pager_row) < 2:
        return ()
    gaps = [pager_row[index + 1][0] - pager_row[index][0] for index in range(len(pager_row) - 1)]
    median_gap = statistics.median(gaps)
    if not 12.0 <= median_gap <= 60.0:
        return ()
    if any(abs(gap - median_gap) > max(4.0, median_gap * 0.35) for gap in gaps):
        return ()
    return tuple(pager_row)


@dataclass(frozen=True)
class ScheduleHeader:
    column_centers: tuple[float, ...]
    today_index: int

    def x_for_day_offset(self, day_offset: int) -> float:
        index = self.today_index + int(day_offset)
        if not 0 <= index < len(self.column_centers):
            left = -self.today_index
            right = len(self.column_centers) - self.today_index - 1
            raise ValueError(
                f"#66 当前只展示相对今天 {left}..{right} 日，"
                f"目标偏移 {int(day_offset)} 不在可见表头内"
            )
        return self.column_centers[index]


@dataclass(frozen=True)
class ScheduleActivityTarget:
    day_offset: int
    x: float
    y: float
    matched_text: str
    runtime_key: str = ""
    alignment_score: float = 0.0


@dataclass(frozen=True)
class ScheduleCardProjection:
    kind: str
    text: str
    exact_match: bool
    runtime_key: str = ""
    name_score: float = 0.0
    covers_moment: bool = False
    rejection_reason: str = ""


class ScheduleActivityNotFoundError(RuntimeError):
    def __init__(self, message: str, *, exhaustive: bool) -> None:
        super().__init__(message)
        self.exhaustive = bool(exhaustive)


def _center(item: dict[str, Any]) -> tuple[float, float]:
    return (
        float(item.get("x") or 0) + float(item.get("w") or 0) / 2,
        float(item.get("y") or 0) + float(item.get("h") or 0) / 2,
    )


def parse_schedule_header(
    lines: Iterable[dict[str, Any]],
    *,
    anchor_date: date | None = None,
) -> ScheduleHeader:
    """Build #66's relative-time axis from the visible 今天 header."""

    rows = [dict(item) for item in lines]
    today = [
        item
        for item in rows
        if re.search(r"今\s*天", str(item.get("text") or ""))
    ]
    if len(today) == 1:
        today_x, _ = _center(today[0])
    else:
        anchor = anchor_date or date.today()
        dated = []
        for item in rows:
            match = re.fullmatch(
                r"\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*",
                str(item.get("text") or ""),
            )
            if match and (int(match.group(1)), int(match.group(2))) == (
                anchor.month,
                anchor.day,
            ):
                dated.append(item)
        if len(dated) != 1:
            raise RuntimeError(
                f"#66 表头『今天』命中数为 {len(today)}，"
                f"日期 {anchor:%m月%d日} 命中数为 {len(dated)}，无法建立时间坐标系"
            )
        today_x, _ = _center(dated[0])

    era_centers = sorted(
        _center(item)[0]
        for item in rows
        if re.fullmatch(r"凡\s*人\s*历", str(item.get("text") or "").strip())
    )
    if len(era_centers) < 3:
        era_centers = sorted(
            _center(item)[0]
            for item in rows
            if item in today
            or re.fullmatch(
                r"\d{1,2}\s*月\s*\d{1,2}\s*日",
                str(item.get("text") or "").strip(),
            )
        )
    if len(era_centers) < 3:
        raise RuntimeError("#66 表头可见列不足，无法解析日程横轴")

    gaps = [
        right - left
        for left, right in zip(era_centers, era_centers[1:])
        if right - left > 20
    ]
    if not gaps:
        raise RuntimeError("#66 表头列中心重叠，无法解析日程横轴")
    typical_gap = statistics.median(gaps)
    centers: list[float] = []
    for value in era_centers:
        if not centers or value - centers[-1] > typical_gap * 0.45:
            centers.append(value)
    today_index = min(
        range(len(centers)), key=lambda index: abs(centers[index] - today_x)
    )
    if abs(centers[today_index] - today_x) > typical_gap * 0.35:
        raise RuntimeError("#66『今天』未对齐任何日历列，拒绝猜测坐标")
    return ScheduleHeader(tuple(centers), today_index)


def resolve_schedule_activity_target(
    *,
    header_lines: Iterable[dict[str, Any]],
    calendar_lines: Iterable[dict[str, Any]],
    activity_pattern: str | Pattern[str],
    day_offset: int = 0,
    anchor_date: date | None = None,
) -> ScheduleActivityTarget:
    """Resolve one unambiguous calendar row (legacy convenience wrapper)."""

    targets = resolve_schedule_activity_targets(
        header_lines=header_lines,
        calendar_lines=calendar_lines,
        activity_pattern=activity_pattern,
        day_offset=day_offset,
        anchor_date=anchor_date,
    )
    if len(targets) != 1:
        raise RuntimeError(
            f"#66 日历活动命中 {len(targets)} 个候选行，单目标接口拒绝猜测"
        )
    return targets[0]


def resolve_schedule_activity_targets(
    *,
    header_lines: Iterable[dict[str, Any]],
    calendar_lines: Iterable[dict[str, Any]],
    activity_pattern: str | Pattern[str],
    day_offset: int = 0,
    anchor_date: date | None = None,
) -> tuple[ScheduleActivityTarget, ...]:
    """Resolve all candidate rows while keeping 今天 as the column anchor."""

    header = parse_schedule_header(header_lines, anchor_date=anchor_date)
    pattern = (
        re.compile(activity_pattern)
        if isinstance(activity_pattern, str)
        else activity_pattern
    )
    matches = [
        dict(item)
        for item in calendar_lines
        if pattern.search(re.sub(r"\s+", "", str(item.get("text") or "")))
    ]
    if not matches:
        raise RuntimeError(f"#66 日历未找到活动 {pattern.pattern!r}")
    candidates: list[ScheduleActivityTarget] = []
    for item in sorted(matches, key=lambda value: _center(value)[1]):
        _label_x, label_y = _center(item)
        if any(abs(candidate.y - label_y) < 12 for candidate in candidates):
            continue
        candidates.append(
            ScheduleActivityTarget(
                day_offset=int(day_offset),
                x=header.x_for_day_offset(day_offset),
                y=label_y,
                matched_text=str(item.get("text") or ""),
            )
        )
    return tuple(candidates)


def _packet_time_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None
    if number is not None:
        if abs(number) >= 100_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number).date()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], pattern).date()
        except ValueError:
            continue
    return None


def runtime_activity_entities_for_date(
    schedule: Mapping[str, Any],
    activity_pattern: str | Pattern[str],
    *,
    target_date: date,
) -> tuple[RuntimeEntity, ...]:
    """Project exact game Runtime activity identities for one calendar date."""

    if not bool(schedule.get("available")):
        return ()
    pattern = re.compile(activity_pattern) if isinstance(activity_pattern, str) else activity_pattern
    entities: list[RuntimeEntity] = []
    for index, raw in enumerate(schedule.get("items") or ()):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        name = str(item.get("name") or "").strip()
        if not name or not pattern.search(re.sub(r"\s+", "", name)):
            continue
        start = _packet_time_date(item.get("startTime") or item.get("startTimeText"))
        end = _packet_time_date(item.get("endTime") or item.get("endTimeText"))
        # A cached historical name without its concrete interval is not enough
        # to authorize a click in a rotating #66 calendar.
        if start is None or end is None or not start <= target_date <= end:
            continue
        key = "|".join(
            str(item.get(field) or "")
            for field in ("activityId", "id", "scheduleId")
        ).strip("|") or f"activity-{index}"
        qualifier = str(
            item.get("littleName")
            or item.get("little_name")
            or item.get("subtitle")
            or ""
        ).strip()
        runtime_name = " ".join(value for value in (name, qualifier) if value)
        entities.append(RuntimeEntity(key=key, name=runtime_name, payload=item))
    return tuple(entities)


def resolve_schedule_runtime_activity_targets(
    *,
    header_lines: Iterable[dict[str, Any]],
    calendar_lines: Iterable[dict[str, Any]],
    runtime_entities: Iterable[RuntimeEntity],
    day_offset: int = 0,
    minimum_pair_score: float = 0.55,
    anchor_date: date | None = None,
) -> tuple[ScheduleActivityTarget, ...]:
    """Use Runtime names to locate noisy OCR rows while preserving all instances."""

    header = parse_schedule_header(header_lines, anchor_date=anchor_date)
    entities = tuple(runtime_entities)
    scored: list[ScheduleActivityTarget] = []
    rows = [dict(raw) for raw in calendar_lines]
    for index, item in enumerate(rows):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        _anchor_x, anchor_y = _center(item)
        nearby = [
            row
            for row in rows
            if str(row.get("text") or "").strip()
            and abs(_center(row)[1] - anchor_y) <= 52
        ]
        nearby.sort(key=lambda row: (_center(row)[1], _center(row)[0]))
        combined_text = " ".join(
            str(row.get("text") or "").strip() for row in nearby
        )

        # Duplicate activity titles commonly render a second line such as
        # ``(预赛)`` or ``跨服[8]``.  The click row still belongs to the main
        # title, so derive Y from the nearby line that best matches the
        # Runtime entity's unqualified activity name instead of the subtitle.
        def main_name_score(row: dict[str, Any]) -> float:
            row_text = str(row.get("text") or "")
            return max(
                (
                    ocr_name_similarity(
                        str(entity.payload.get("name") or entity.name),
                        row_text,
                    )
                    for entity in entities
                ),
                default=0.0,
            )

        main_line = max(nearby, key=main_name_score, default=item)
        _label_x, label_y = _center(main_line)
        candidate = GuiCandidate(
            key=f"calendar-line-{index}",
            text=combined_text or text,
            point=(header.x_for_day_offset(day_offset), label_y),
            payload={"anchor": item, "row_lines": nearby},
        )
        eligible_entities: list[RuntimeEntity] = []
        for entity in entities:
            qualifier = str(
                entity.payload.get("littleName")
                or entity.payload.get("little_name")
                or entity.payload.get("subtitle")
                or ""
            ).strip()
            qualifier_threshold = max(float(minimum_pair_score), 0.90)
            if qualifier and max(
                (
                    ocr_name_similarity(qualifier, str(row.get("text") or ""))
                    for row in nearby
                ),
                default=0.0,
            ) < qualifier_threshold:
                # When Runtime provides an instance qualifier, it is identity
                # evidence rather than optional decoration. Similar strings
                # such as ``跨服[8]`` and ``跨服[16]`` must not share the
                # ordinary fuzzy title threshold.
                continue
            eligible_entities.append(entity)
        best = max(
            (
                score_runtime_gui_pair(entity, candidate)
                for entity in eligible_entities
            ),
            key=lambda evidence: evidence.score,
            default=None,
        )
        if best is None or best.score < float(minimum_pair_score):
            continue
        scored.append(
            ScheduleActivityTarget(
                day_offset=int(day_offset),
                x=header.x_for_day_offset(day_offset),
                y=label_y,
                matched_text=combined_text or text,
                runtime_key=best.runtime_key,
                alignment_score=best.score,
            )
        )
    deduplicated: list[ScheduleActivityTarget] = []
    for candidate in sorted(scored, key=lambda item: (-item.alignment_score, item.y)):
        if any(abs(existing.y - candidate.y) < 12 for existing in deduplicated):
            continue
        deduplicated.append(candidate)
    if not deduplicated:
        names = "、".join(entity.name for entity in entities)
        raise RuntimeError(f"#66 未将 Runtime 活动 {names or 'unknown'} 对齐到任何日历行")
    return tuple(sorted(deduplicated, key=lambda item: item.y))


def activity_card_covers_date(card_text: str, target: date) -> bool:
    match = re.search(
        r"活动时间\s*[:：]?\s*(\d{1,2})月(\d{1,2})日\s*[-－—~至]+\s*"
        r"(\d{1,2})月(\d{1,2})日",
        card_text,
    )
    if match is None:
        return False
    start_month, start_day, end_month, end_day = map(int, match.groups())
    start = date(target.year, start_month, start_day)
    end = date(target.year, end_month, end_day)
    if end < start:
        if target < start:
            start = date(target.year - 1, start_month, start_day)
        else:
            end = date(target.year + 1, end_month, end_day)
    return start <= target <= end


def activity_card_covers_moment(card_text: str, target: datetime) -> bool:
    """Verify either a dated activity period or a same-day clock window.

    Short daily activities such as 魔祖 render ``12:30:00-13:00:00`` on the
    card instead of repeating a calendar date.  That card is authoritative
    while the target moment is inside its window; it must not depend on a
    matching label also being present in the calendar grid.  Overnight
    windows are treated as crossing midnight.
    """

    if activity_card_covers_date(card_text, target.date()):
        return True
    match = re.search(
        r"活动时间\s*[:：]?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*"
        r"[-－—~至]+\s*(\d{1,2}):(\d{2})(?::(\d{2}))?",
        card_text,
    )
    if match is None:
        return False
    start_hour, start_minute, start_second, end_hour, end_minute, end_second = (
        int(value or 0) for value in match.groups()
    )
    if not (
        0 <= start_hour <= 23
        and 0 <= end_hour <= 23
        and 0 <= start_minute <= 59
        and 0 <= end_minute <= 59
        and 0 <= start_second <= 59
        and 0 <= end_second <= 59
    ):
        return False
    start_value = start_hour * 3600 + start_minute * 60 + start_second
    end_value = end_hour * 3600 + end_minute * 60 + end_second
    target_value = target.hour * 3600 + target.minute * 60 + target.second
    if start_value <= end_value:
        return start_value <= target_value <= end_value
    return target_value >= start_value or target_value <= end_value


def activity_card_matches(
    card_lines: Iterable[dict[str, Any]],
    activity_pattern: str | Pattern[str],
    *,
    target_date: date,
    target_moment: datetime | None = None,
) -> tuple[bool, str]:
    card_text = " ".join(str(item.get("text") or "") for item in card_lines)
    pattern = (
        re.compile(activity_pattern)
        if isinstance(activity_pattern, str)
        else activity_pattern
    )
    name_ok = bool(pattern.search(re.sub(r"\s+", "", card_text)))
    moment = target_moment or datetime.combine(target_date, datetime.now().time())
    return name_ok and activity_card_covers_moment(card_text, moment), card_text


def classify_activity_card(
    card_lines: Iterable[dict[str, Any]],
    activity_pattern: str | Pattern[str],
    *,
    target_date: date,
    target_moment: datetime | None = None,
    runtime_entities: Iterable[RuntimeEntity] = (),
) -> ScheduleCardProjection:
    """Classify #66's promo card and its active/status-card projection."""

    rows = [dict(item) for item in card_lines]
    entities = tuple(runtime_entities)
    moment = target_moment or datetime.combine(target_date, datetime.now().time())
    exact, text = activity_card_matches(
        rows,
        activity_pattern,
        target_date=target_date,
        target_moment=moment,
    )
    runtime_key = ""
    name_score = 0.0
    covers_moment = activity_card_covers_moment(text, moment)
    name_ok = exact
    if entities:
        card_candidate = GuiCandidate(key="activity-card", text=text)
        best = max(
            (score_runtime_gui_pair(entity, card_candidate) for entity in entities),
            key=lambda evidence: evidence.score,
            default=None,
        )
        name_score = best.score if best else 0.0
        runtime_key = best.runtime_key if best else ""
        name_ok = name_score >= 0.55
        exact = name_ok and covers_moment
    if exact:
        return ScheduleCardProjection(
            "promo",
            text,
            True,
            runtime_key,
            name_score,
            covers_moment,
            "exact",
        )
    rejection_reason = (
        "runtime_gui_score_below_threshold"
        if entities and not name_ok
        else "activity_name_mismatch"
        if not name_ok
        else "date_or_time_mismatch"
    )
    compact = re.sub(r"\s+", "", text)
    # Clicking an active calendar cell can project a status card without
    # repeating the activity title/date. This is deliberately not sufficient
    # by itself: the caller must also have calendar-selection evidence.
    if re.search(r"进入活动|活动倒计时|当前积分|我的团队|领取", compact):
        return ScheduleCardProjection(
            "active",
            text,
            False,
            runtime_key,
            name_score,
            covers_moment,
            "active_projection_requires_selection_evidence",
        )
    return ScheduleCardProjection(
        "unknown",
        text,
        False,
        runtime_key,
        name_score,
        covers_moment,
        rejection_reason,
    )


def _schedule_card_diagnostics_summary(
    diagnostics: Iterable[ScheduleCardProjection],
) -> str:
    items = []
    for index, projection in enumerate(diagnostics, start=1):
        compact = re.sub(r"\s+", "", projection.text)[:80] or "<empty>"
        items.append(
            f"p{index}[{projection.kind}] score={projection.name_score:.2f} "
            f"time={'yes' if projection.covers_moment else 'no'} "
            f"reason={projection.rejection_reason or 'unknown'} text={compact}"
        )
    return "；".join(items)


def _log_schedule_card_diagnostics(
    runtime: Any,
    diagnostics: Iterable[ScheduleCardProjection],
) -> None:
    summary = _schedule_card_diagnostics_summary(diagnostics)
    logger = getattr(getattr(runtime, "runner", None), "_log", None)
    if summary and callable(logger):
        logger("detail", f"#66 活动卡片逐页 Runtime-GUI 对齐：{summary}")


def select_schedule_activity(
    runtime: Any,
    activity_pattern: str | Pattern[str],
    *,
    day_offset: int = 0,
    enter: bool = False,
    settle_seconds: float = 0.8,
    runtime_schedule: Mapping[str, Any] | None = None,
    require_runtime_alignment: bool = False,
    now: datetime | None = None,
) :
    """Select and verify a #66 activity without encoding rotating content."""

    frame = runtime.cur_frame(update=True)
    header_lines = runtime.ocr_fragments_in_shapes(
        SCHEDULE_SCENE_ID, [HEADER_SHAPE], frame_data_url=frame
    )
    calendar_lines = runtime.ocr_fragments_in_shapes(
        SCHEDULE_SCENE_ID, [CALENDAR_SHAPE], frame_data_url=frame
    )
    current_moment = now or datetime.now()
    target_moment = current_moment + timedelta(days=int(day_offset))
    target_date = target_moment.date()
    if runtime_schedule is None:
        try:
            from backend.core.fanxiu.activity.runtime_schedule import (
                get_cached_fanxiu_activity_runtime_schedule,
            )

            runtime_schedule = get_cached_fanxiu_activity_runtime_schedule()
        except Exception:
            runtime_schedule = {}
    runtime_entities = runtime_activity_entities_for_date(
        runtime_schedule or {}, activity_pattern, target_date=target_date
    )
    current_page = runtime.paged_content_snapshot(
        SCHEDULE_SCENE_ID, ACTIVITY_CARD_SHAPE, frame_data_url=frame
    )
    current_projection = classify_activity_card(
        current_page.get("lines") or (),
        activity_pattern,
        target_date=target_date,
        target_moment=target_moment,
        runtime_entities=runtime_entities,
    )
    selected_page = current_page
    selected_projection = current_projection
    card_diagnostics = [current_projection]

    if require_runtime_alignment and not runtime_entities:
        raise RuntimeError(
            f"#66 缺少覆盖 {target_date.isoformat()} 的权威 Runtime 活动实体，拒绝只按 OCR 操作"
        )

    # An exact Runtime-aligned calendar cell is itself the activity entry.
    # When the currently projected bottom card belongs to another activity,
    # enter=True must consume that cell instead of paging unrelated cards.
    # Keep enter=False observational: calendar cells are navigation actions.
    if enter and runtime_entities and not current_projection.exact_match:
        calendar_targets = resolve_schedule_runtime_activity_targets(
            header_lines=header_lines,
            calendar_lines=calendar_lines,
            runtime_entities=runtime_entities,
            day_offset=day_offset,
            anchor_date=current_moment.date(),
        )
        if len(calendar_targets) == 1:
            selected_target = calendar_targets[0]
            runtime.click_frame_point(
                SCHEDULE_SCENE_ID,
                selected_target.x,
                selected_target.y,
            )
            yield from runtime.wait_action_settle(settle_seconds)
            return selected_target
        if require_runtime_alignment:
            raise RuntimeError(
                f"#66 Runtime 对齐的日历活动命中 {len(calendar_targets)} 个，拒绝猜测入口"
            )

    # The game's intelligent default often already exposes the current major
    # activity. Preserve that fast path, but verify name and date before 前往.
    if not current_projection.exact_match:
        # Calendar cells are navigation actions, not card selectors.  A real
        # 2026-08-12 observation proved that clicking the Beast Abyss cell
        # enters the activity main page immediately.  Search only through the
        # card's own paged window; otherwise ``enter=False`` would still leave
        # #66 and a failed lookup could produce an unintended business action.
        def card_matches(page: Mapping[str, Any]) -> bool:
            projection = classify_activity_card(
                page.get("lines") or (),
                activity_pattern,
                target_date=target_date,
                target_moment=target_moment,
                runtime_entities=runtime_entities,
            )
            previous = card_diagnostics[-1]
            if (
                projection.text,
                projection.runtime_key,
                projection.rejection_reason,
            ) != (
                previous.text,
                previous.runtime_key,
                previous.rejection_reason,
            ):
                card_diagnostics.append(projection)
            return projection.exact_match

        found = None
        indicator_points = _activity_card_indicator_points(runtime, frame)
        for x, y in indicator_points:
            runtime.click_frame_point(SCHEDULE_SCENE_ID, x, y)
            yield from runtime.wait_action_settle(settle_seconds)
            candidate = runtime.paged_content_snapshot(
                SCHEDULE_SCENE_ID,
                ACTIVITY_CARD_SHAPE,
            )
            if card_matches(candidate):
                found = candidate
                break
        if found is None and not indicator_points:
            found = yield from runtime.find_paged_content(
                SCHEDULE_SCENE_ID,
                card_matches,
                ACTIVITY_CARD_SHAPE,
            )
        _log_schedule_card_diagnostics(runtime, card_diagnostics)
        if found is None:
            diagnostic_summary = _schedule_card_diagnostics_summary(card_diagnostics)
            raise ScheduleActivityNotFoundError(
                f"#66 活动卡片未确认目标或日期 {target_date.isoformat()}："
                f"{diagnostic_summary[:1200]}",
                exhaustive=bool(indicator_points),
            )
        selected_page = found
        selected_projection = classify_activity_card(
            found.get("lines") or (),
            activity_pattern,
            target_date=target_date,
            target_moment=target_moment,
            runtime_entities=runtime_entities,
        )

    # Calendar rows describe the date grid; they are useful corroborating
    # evidence but are not a prerequisite for an already verified card.  A
    # real 2026-08-12 魔祖 run showed the current exact card while the calendar
    # grid intentionally contained no 魔祖 label.  Keep the evidence when it
    # is unique, otherwise return a card-backed target without guessing a row.
    try:
        if runtime_entities:
            targets = resolve_schedule_runtime_activity_targets(
                header_lines=header_lines,
                calendar_lines=calendar_lines,
                runtime_entities=runtime_entities,
                day_offset=day_offset,
                anchor_date=date.today(),
            )
        else:
            targets = resolve_schedule_activity_targets(
                header_lines=header_lines,
                calendar_lines=calendar_lines,
                activity_pattern=activity_pattern,
                day_offset=day_offset,
                anchor_date=date.today(),
            )
    except RuntimeError:
        targets = ()
    if len(targets) == 1:
        selected_target = targets[0]
    else:
        header = parse_schedule_header(header_lines)
        selected_target = ScheduleActivityTarget(
            day_offset=int(day_offset),
            x=header.x_for_day_offset(day_offset),
            y=0.0,
            matched_text=selected_projection.text,
            runtime_key=selected_projection.runtime_key,
            alignment_score=selected_projection.name_score,
        )
    if enter:
        runtime.click_shape(
            SCHEDULE_SCENE_ID,
            ACTIVITY_CARD_FORWARD_SHAPE,
            frame_data_url=selected_page.get("frame"),
        )
    return selected_target
