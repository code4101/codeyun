from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TIANJIGE_PROFILE_URL = "https://forum.odchqpto.com/pages/profile/user?tgid=5636&userId=51"
_QUIZ_TITLE_PATTERN = re.compile(r"有奖竞答.*?DAY\s*([123])", re.IGNORECASE)
_ANSWER_PATTERN = re.compile(
    r"(?:^|\s)1\s*[.、:：,，)）]\s*(.+?)"
    r"\s*2\s*[.、:：,，)）]\s*(.+?)"
    r"\s*3\s*[.、:：,，)）]\s*(.+?)\s*$",
    re.DOTALL,
)
_INVISIBLE_PATTERN = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
TIANJIGE_QUIZ_ROLE_WEIGHTS = {
    "普通": 1,
    "活跃版主": 10,
    "互动大神": 100,
}
TIANJIGE_QUIZ_COMMENT_LOAD_TIMEOUT_SECONDS = 120


class TianjigeForumQuizError(RuntimeError):
    """天机阁论坛页面不满足安全自动化前置条件。"""


@dataclass(frozen=True)
class TianjigeQuizAnswer:
    """一个规范化后的三题回复候选。"""

    lines: tuple[str, str, str]
    score: int
    votes: int
    line_scores: tuple[int, int, int] = (0, 0, 0)
    line_votes: tuple[int, int, int] = (0, 0, 0)

    @property
    def text(self) -> str:
        return "\n".join(f"{index}、{line}" for index, line in enumerate(self.lines, start=1))


@dataclass(frozen=True)
class TianjigeQuizProbe:
    """一次短轮询得到的论坛事实。"""

    status: str
    thread_key: str = ""
    thread_url: str = ""
    title: str = ""
    comment_count: int = 0
    answer: TianjigeQuizAnswer | None = None
    profile_thread_count: int = 0
    elapsed_seconds: float = 0.0


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _INVISIBLE_PATTERN.sub("", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _canonical_answer_line(value: str) -> str:
    text = _normalize_text(value).casefold()
    return re.sub(r"[\s,，.。;；:：、!！?？'\"“”‘’()（）\[\]【】]+", "", text)


def parse_tianjige_quiz_answer(text: str) -> tuple[str, str, str] | None:
    """从宽松的中英文标点与空白中提取三行答案。"""

    normalized = _normalize_text(text).replace("\n", " ")
    match = _ANSWER_PATTERN.search(normalized)
    if match is None:
        return None
    lines = tuple(_normalize_text(item) for item in match.groups())
    if len(lines) != 3 or any(not line or len(line) > 120 for line in lines):
        return None
    return lines  # type: ignore[return-value]


def rank_tianjige_quiz_answers(
    comments: Sequence[tuple[str, str]],
) -> list[TianjigeQuizAnswer]:
    """分别汇总三道题，再把每题最高置信答案组合成最终回复。

    不能把三行答案当成不可拆分的整票，否则同一题的等价答案会因为另外
    两题不同而被分散。身份权重也逐题生效：普通 1、活跃版主 10、互动大神
    100。最终 ``score/votes`` 取三题中的最弱项，确保发送阈值约束每一道题。
    """

    scores = [Counter(), Counter(), Counter()]
    votes = [Counter(), Counter(), Counter()]
    display: dict[tuple[int, str], str] = {}
    display_weight: dict[tuple[int, str], int] = {}
    for content, context in comments:
        lines = parse_tianjige_quiz_answer(content)
        if lines is None:
            continue
        role = "互动大神" if "互动大神" in context else "活跃版主" if "活跃版主" in context else "普通"
        weight = TIANJIGE_QUIZ_ROLE_WEIGHTS[role]
        for index, line in enumerate(lines):
            key = _canonical_answer_line(line)
            if not key:
                continue
            scores[index][key] += weight
            votes[index][key] += 1
            display_key = (index, key)
            if weight > display_weight.get(display_key, -1):
                display[display_key] = line
                display_weight[display_key] = weight
    if any(not counter for counter in scores):
        return []

    winners = [
        max(
            counter,
            key=lambda key: (
                counter[key],
                votes[index][key],
                display[(index, key)],
            ),
        )
        for index, counter in enumerate(scores)
    ]
    line_scores = tuple(scores[index][key] for index, key in enumerate(winners))
    line_votes = tuple(votes[index][key] for index, key in enumerate(winners))
    lines = tuple(display[(index, key)] for index, key in enumerate(winners))
    return [
        TianjigeQuizAnswer(
            lines=lines,  # type: ignore[arg-type]
            score=min(line_scores),
            votes=min(line_votes),
            line_scores=line_scores,  # type: ignore[arg-type]
            line_votes=line_votes,  # type: ignore[arg-type]
        )
    ]


def sanitize_tianjige_thread_url(url: str) -> str:
    """仅保留打开帖子所需的非敏感查询参数。"""

    parts = urlsplit(str(url or ""))
    allowed = [(key, value) for key, value in parse_qsl(parts.query) if key in {"tgid", "id"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(allowed), ""))


def _is_login_wall(tab: Any) -> bool:
    url = str(getattr(tab, "url", "") or "")
    html = str(getattr(tab, "html", "") or "")
    return "/vip/login/" in url or ("短信登录" in html and "获取验证码" in html)


def _close_work_tab(browser: Any, tab: Any) -> None:
    try:
        if tab is not None and int(getattr(browser, "tabs_count", 0) or 0) > 1:
            tab.close()
    except Exception:
        pass


def _expected_quiz_day(activity_date: str) -> int:
    """Map Tuesday/Wednesday/Thursday to DAY1/DAY2/DAY3."""

    return date.fromisoformat(activity_date).isoweekday() - 1


def _thread_title_and_time(item: Any) -> tuple[str, str]:
    title_ele = item.ele("t:uni-view@@class:sq-thread-title", timeout=0.2)
    footer_ele = item.ele("t:uni-view@@class:sq-thread-footer", timeout=0.2)
    title = _normalize_text(title_ele.text if title_ele else getattr(item, "text", ""))
    footer = _normalize_text(footer_ele.text if footer_ele else "")
    posted_at = footer.split("\n", 1)[0] if footer else _normalize_text(getattr(item, "text", ""))
    return title, posted_at


def _is_current_quiz_thread(item: Any, activity_date: str) -> tuple[bool, str]:
    """Match the live card format, whose new rows say `刚刚/N分钟前` instead of a date."""

    title, posted_at = _thread_title_and_time(item)
    match = _QUIZ_TITLE_PATTERN.search(title)
    expected_day = _expected_quiz_day(activity_date)
    if "有奖竞答" not in title:
        return False, title
    # Tuesday's first post is currently the theme announcement/question post
    # and often has no literal DAY1 suffix.  DAY2/DAY3 do carry the suffix.
    if expected_day == 1:
        if match is not None and int(match.group(1)) != 1:
            return False, title
    elif match is None or int(match.group(1)) != expected_day:
        return False, title
    is_today = activity_date in posted_at
    is_recent = bool(re.search(r"(?:刚刚|\d+\s*(?:秒|分钟)前)", posted_at))
    return is_today or is_recent, title


def _cancelable_sleep(
    seconds: float,
    *,
    check_cancel: Callable[[], None] | None = None,
) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        if check_cancel:
            check_cancel()
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def _wait_profile_quiz_thread(
    tab: Any,
    activity_date: str,
    timeout_seconds: float,
    *,
    check_cancel: Callable[[], None] | None = None,
) -> tuple[Any | None, str, int]:
    """Wait for the target thread, or spend the full window proving absence.

    A forum profile is a JS-rendered list.  Seeing any old ``sq-thread`` only
    proves that the shell has started rendering; it does not prove that the
    newest rows have arrived.  Positive target evidence may return early, but
    a negative result must keep refreshing the same work tab until the page
    timeout expires.
    """

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    latest_threads: list[Any] = []
    while time.monotonic() < deadline:
        if check_cancel:
            check_cancel()
        if _is_login_wall(tab):
            raise TianjigeForumQuizError("天机阁论坛需要重新登录")
        container = tab.ele("t:uni-view@@class=profile-tabs__content flex fd-c", timeout=1)
        threads = container.eles("t:uni-view@@class=sq-thread", timeout=1) if container else []
        if threads:
            latest_threads = list(threads)
            for item in latest_threads:
                matched, title = _is_current_quiz_thread(item, activity_date)
                if matched:
                    return item, title, len(latest_threads)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.2, remaining))
    if latest_threads:
        return None, "", len(latest_threads)
    raise TianjigeForumQuizError("天机阁动态列表未在限定时间内加载")


def _load_latest_comments(
    tab: Any,
    *,
    max_expand_rounds: int | None = None,
    load_timeout_seconds: float = TIANJIGE_QUIZ_COMMENT_LOAD_TIMEOUT_SECONDS,
) -> list[tuple[str, str]]:
    """Load comments back to the earliest available row before analysis.

    The production default has no sampling limit: it stops only when the
    ``加载更多`` control disappears.  A wall-clock watchdog and two consecutive
    no-growth clicks fail closed instead of silently analysing a partial list.
    A finite ``max_expand_rounds`` is reserved for lightweight submission
    verification and diagnostics.
    """

    latest = tab.ele("t:uni-view@@alt=最新", timeout=1)
    if latest:
        latest.click()
    container = tab.ele("t:uni-view@@class:thread-comments__container", timeout=8)
    if not container:
        return []
    deadline = time.monotonic() + max(1.0, float(load_timeout_seconds))
    expand_rounds = 0
    stalled_rounds = 0
    while max_expand_rounds is None or expand_rounds < max(0, int(max_expand_rounds)):
        current_comments = container.eles("t:uni-view@@class=sq-comment", timeout=1)
        if current_comments:
            try:
                current_comments[-1].scroll.to_see()
            except Exception:
                pass
        more = container.ele("t:span@@text()=加载更多", timeout=2)
        if not more:
            break
        if time.monotonic() >= deadline:
            if max_expand_rounds is None:
                raise TianjigeForumQuizError("评论加载超过 120 秒且尚未到达最早回复")
            break
        before = len(current_comments)
        try:
            more.wait.clickable(timeout=2, raise_err=False)
        except Exception:
            pass
        more.click()
        deadline = time.monotonic() + 3
        grew = False
        while time.monotonic() < deadline:
            current = len(container.eles("t:uni-view@@class=sq-comment", timeout=0.5))
            if current > before:
                grew = True
                break
            time.sleep(0.2)
        if not grew:
            stalled_rounds += 1
            if stalled_rounds >= 2:
                if max_expand_rounds is None:
                    raise TianjigeForumQuizError("连续两次加载更多后评论数量未增长")
                break
            continue
        stalled_rounds = 0
        expand_rounds += 1
    comments: list[tuple[str, str]] = []
    for item in container.eles("t:uni-view@@class=sq-comment", timeout=1):
        content = item.ele("t:uni-view@@class=sq-comment-content", timeout=0.5)
        text = _normalize_text(content.text if content else "")
        if text:
            comments.append((text, _normalize_text(item.text)))
    return comments


def _current_forum_nickname(tab: Any) -> str:
    """Read only the current forum nickname; never expose token-bearing storage."""

    try:
        raw = tab.run_js("return localStorage.getItem('5636_userInfo') || ''")
        outer = json.loads(str(raw or ""))
        data = outer.get("data") if isinstance(outer, dict) else None
        return _normalize_text(data.get("nickname")) if isinstance(data, dict) else ""
    except Exception:
        return ""


def _own_answer_match_count(
    comments: Sequence[tuple[str, str]],
    expected: tuple[str, str, str] | None,
    nickname: str,
) -> int:
    if expected is None or not nickname:
        return 0
    return sum(
        1
        for text, context in comments
        if parse_tianjige_quiz_answer(text) == expected
        and _normalize_text(context).split("\n", 1)[0] == nickname
    )


def probe_tianjige_forum_quiz(
    activity_date: str,
    *,
    browser_factory: Callable[[], Any] | None = None,
    timeout_seconds: float = 15,
    overall_timeout_seconds: float | None = None,
    poll_seconds: float = 10,
    minimum_answer_score: int = 1,
    progress_callback: Callable[[TianjigeQuizProbe], None] | None = None,
    check_cancel: Callable[[], None] | None = None,
) -> TianjigeQuizProbe:
    """只读等待当天竞答与评论候选，全程复用一个工作标签。

    ``overall_timeout_seconds`` 未提供时只检查一轮，便于探针和测试。正式
    Job 会传入活动窗口剩余时间；找不到帖子或答案时只刷新当前 tab，不会
    由 Scheduler 反复创建、关闭 tab。
    """

    if browser_factory is None:
        from pyxllib.ext.drissionlib import Chromium

        browser_factory = Chromium
    browser = browser_factory()
    tab = browser.new_tab()
    started_at = time.monotonic()
    overall_deadline = (
        started_at + max(1.0, float(overall_timeout_seconds))
        if overall_timeout_seconds is not None
        else None
    )
    try:
        tab.get(TIANJIGE_PROFILE_URL)
        target = None
        title = ""
        profile_thread_count = 0
        while target is None:
            target, title, profile_thread_count = _wait_profile_quiz_thread(
                tab,
                activity_date,
                timeout_seconds,
                check_cancel=check_cancel,
            )
            if target is not None:
                break
            waiting = TianjigeQuizProbe(
                status="waiting_thread",
                profile_thread_count=profile_thread_count,
                elapsed_seconds=max(0.0, time.monotonic() - started_at),
            )
            if overall_deadline is None or time.monotonic() >= overall_deadline:
                return waiting
            if progress_callback:
                progress_callback(waiting)
            _cancelable_sleep(
                min(float(poll_seconds), max(0.0, overall_deadline - time.monotonic())),
                check_cancel=check_cancel,
            )
            tab.refresh()

        target.click()
        deadline = time.monotonic() + max(2.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            if check_cancel:
                check_cancel()
            if _is_login_wall(tab):
                raise TianjigeForumQuizError("天机阁论坛需要重新登录")
            if "/pages/thread/index" in str(getattr(tab, "url", "") or ""):
                break
            time.sleep(0.05)
        thread_url = sanitize_tianjige_thread_url(str(getattr(tab, "url", "") or ""))
        thread_key = f"{activity_date}:{thread_url or title}"
        while True:
            comments = _load_latest_comments(tab)
            ranked = rank_tianjige_quiz_answers(comments)
            probe = TianjigeQuizProbe(
                status=(
                    "ready"
                    if ranked and ranked[0].score >= max(1, int(minimum_answer_score))
                    else "waiting_answers"
                ),
                thread_key=thread_key,
                thread_url=thread_url,
                title=title,
                comment_count=len(comments),
                answer=ranked[0] if ranked else None,
                profile_thread_count=profile_thread_count,
                elapsed_seconds=max(0.0, time.monotonic() - started_at),
            )
            if probe.status == "ready":
                return probe
            if overall_deadline is None or time.monotonic() >= overall_deadline:
                return probe
            if progress_callback:
                progress_callback(probe)
            _cancelable_sleep(
                min(float(poll_seconds), max(0.0, overall_deadline - time.monotonic())),
                check_cancel=check_cancel,
            )
            tab.refresh()
    finally:
        _close_work_tab(browser, tab)


def submit_tianjige_forum_quiz_answer(
    thread_url: str,
    answer_text: str,
    *,
    browser_factory: Callable[[], Any] | None = None,
    timeout_seconds: float = 15,
    check_cancel: Callable[[], None] | None = None,
) -> bool:
    """提交一次论坛回复，并以页面重新出现完全相同的三行答案为完成判据。"""

    if not thread_url:
        raise TianjigeForumQuizError("竞答帖子缺少可复用链接")
    if browser_factory is None:
        from pyxllib.ext.drissionlib import Chromium

        browser_factory = Chromium
    browser = browser_factory()
    tab = browser.new_tab()
    try:
        tab.get(thread_url)
        if _is_login_wall(tab):
            raise TianjigeForumQuizError("天机阁论坛需要重新登录")
        expected = parse_tianjige_quiz_answer(answer_text)
        nickname = _current_forum_nickname(tab)
        if not nickname:
            raise TianjigeForumQuizError("无法确认当前天机阁登录昵称，拒绝发送")
        existing_comments = _load_latest_comments(tab, max_expand_rounds=0)
        existing_match_count = _own_answer_match_count(existing_comments, expected, nickname)
        entry = tab.ele("t:uni-view@@class:input-btn", timeout=timeout_seconds)
        if not entry:
            raise TianjigeForumQuizError("没有找到天机阁回复入口")
        entry.click()
        editor = None
        for selector in ("css:.ql-editor", 'css:[contenteditable="true"]', "css:textarea"):
            editor = tab.ele(selector, timeout=2)
            if editor:
                break
        if not editor:
            raise TianjigeForumQuizError("没有找到天机阁回复编辑框")
        editor.click()
        editor.input(answer_text)
        send = next(
            (button for button in tab.eles("t:uni-button", timeout=2) if _normalize_text(button.text) == "发送"),
            None,
        )
        if not send:
            raise TianjigeForumQuizError("没有找到文字完全匹配的发送按钮")
        if check_cancel:
            check_cancel()
        send.click()
        deadline = time.monotonic() + max(3.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            comments = _load_latest_comments(tab, max_expand_rounds=0)
            match_count = _own_answer_match_count(comments, expected, nickname)
            if match_count > existing_match_count:
                return True
            time.sleep(0.25)
        return False
    finally:
        _close_work_tab(browser, tab)


__all__ = [
    "TIANJIGE_PROFILE_URL",
    "TIANJIGE_QUIZ_COMMENT_LOAD_TIMEOUT_SECONDS",
    "TianjigeForumQuizError",
    "TianjigeQuizAnswer",
    "TianjigeQuizProbe",
    "parse_tianjige_quiz_answer",
    "probe_tianjige_forum_quiz",
    "rank_tianjige_quiz_answers",
    "sanitize_tianjige_thread_url",
    "submit_tianjige_forum_quiz_answer",
]
