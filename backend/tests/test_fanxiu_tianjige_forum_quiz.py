from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.fanxiu import tianjige_forum_quiz as crawler
from backend.core.fanxiu.data_annotation.behavior_tree_control import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import tianjige_forum_quiz as task
from backend.core.fanxiu.tianjige_forum_quiz import (
    TianjigeQuizAnswer,
    TianjigeQuizProbe,
    parse_tianjige_quiz_answer,
    rank_tianjige_quiz_answers,
    sanitize_tianjige_thread_url,
)


class _Runner:
    def __init__(self) -> None:
        self.next_times: list[tuple[str, str]] = []
        self.logs: list[tuple[str, str]] = []

    def _persist_scheduler_task_next_time(self, task_id, next_time) -> None:
        self.next_times.append((task_id, next_time))

    def _log(self, kind, message) -> None:
        self.logs.append((kind, message))

    @staticmethod
    def _raise_if_stopped(_stop_event) -> None:
        return None


def _ready_probe(*, score: int = 3, votes: int = 3) -> TianjigeQuizProbe:
    return TianjigeQuizProbe(
        status="ready",
        thread_key="2026-08-05:thread-1",
        thread_url="https://forum.odchqpto.com/pages/thread/index?tgid=5636&id=1",
        title="有奖竞答 DAY2 2026-08-05 18:00:00",
        comment_count=8,
        answer=TianjigeQuizAnswer(("协助道友", "200%", "6000"), score, votes),
    )


def test_parse_tianjige_quiz_answer_tolerates_spacing_and_punctuation() -> None:
    assert parse_tianjige_quiz_answer("\u200b1 . 协助道友\n2： 200％  3、6000") == (
        "协助道友",
        "200%",
        "6000",
    )
    assert parse_tianjige_quiz_answer("1、10%2、203、练体功法") == (
        "10%",
        "20",
        "练体功法",
    )
    assert parse_tianjige_quiz_answer("这里只有两个答案：1、甲 2、乙") is None


def test_rank_tianjige_quiz_answers_merges_equivalent_answers_and_weights_roles() -> None:
    ranked = rank_tianjige_quiz_answers(
        [
            ("1、协助道友 2、200% 3、6000", "普通玩家"),
            ("1. 协助道友 2：200％ 3，6000", "普通玩家"),
            ("1、其它 2、答案 3、组合", "活跃版主"),
            ("1、协助道友 2、200% 3、6000", "互动大神"),
        ]
    )

    assert ranked[0].lines == ("协助道友", "200%", "6000")
    assert ranked[0].score == 102
    assert ranked[0].votes == 3
    assert ranked[0].line_scores == (102, 102, 102)


def test_rank_tianjige_quiz_answers_votes_each_question_and_prioritizes_expert() -> None:
    comments = [
        *(('1、不可以 2、80 3、极品灵环', '普通玩家') for _ in range(20)),
        *(('1、不可以 2、100 3、一阶基础灵环', '普通玩家') for _ in range(15)),
        ('1、不可以 2、80 3、初灵', '专业小鸡 互动大神'),
    ]

    answer = rank_tianjige_quiz_answers(comments)[0]

    assert answer.lines == ("不可以", "80", "初灵")
    assert answer.line_scores == (135, 120, 100)
    assert answer.line_votes == (36, 21, 1)
    assert answer.score == 100
    assert answer.votes == 1


def test_rank_uses_experts_display_text_for_equivalent_answer() -> None:
    answer = rank_tianjige_quiz_answers(
        [
            ("1、不可以 2、80 3、初灵。", "普通玩家"),
            ("1、不可以 2、80 3、初灵", "专业小鸡 互动大神"),
        ]
    )[0]

    assert answer.lines[2] == "初灵"


def test_submission_verification_only_counts_current_users_answer() -> None:
    expected = ("不可以", "80", "极品灵环")
    comments = [
        ("1、不可以\n2、80\n3、极品灵环", "别人\n审核中\n1分钟前"),
        ("1、不可以\n2、80\n3、极品灵环", "止观驼来\n审核中\n刚刚"),
        ("1、可以\n2、80\n3、极品灵环", "止观驼来\n审核中\n刚刚"),
    ]

    assert crawler._own_answer_match_count(comments, expected, "止观驼来") == 1


def test_sanitize_tianjige_thread_url_removes_sensitive_and_unneeded_query() -> None:
    url = (
        "https://forum.odchqpto.com/pages/thread/index?tgid=5636&id=723245"
        "&token=secret&dev=device&backUrl=https%3A%2F%2Fexample.com"
    )
    assert sanitize_tianjige_thread_url(url) == (
        "https://forum.odchqpto.com/pages/thread/index?tgid=5636&id=723245"
    )


def test_probe_closes_its_work_tab_on_login_wall() -> None:
    class Tab:
        url = ""
        html = ""

        def __init__(self, browser) -> None:
            self.browser = browser

        def get(self, _url) -> None:
            self.url = "https://forum.odchqpto.com/vip/login/"

        def close(self) -> None:
            self.browser.tabs_count -= 1

    class Browser:
        def __init__(self) -> None:
            self.tabs_count = 1

        def new_tab(self):
            self.tabs_count += 1
            return Tab(self)

    browser = Browser()

    with pytest.raises(crawler.TianjigeForumQuizError, match="重新登录"):
        crawler.probe_tianjige_forum_quiz(
            "2026-08-05",
            browser_factory=lambda: browser,
        )

    assert browser.tabs_count == 1


def test_probe_waits_for_target_instead_of_returning_after_old_threads(monkeypatch) -> None:
    class Clock:
        value = 0.0

        @classmethod
        def monotonic(cls):
            return cls.value

        @classmethod
        def sleep(cls, seconds):
            cls.value += seconds

    class Thread:
        def __init__(self, title, footer="") -> None:
            self.title = title
            self.footer = footer
            self.text = f"{title}\n{footer}".strip()
            self.clicked = False

        def click(self) -> None:
            self.clicked = True
            tab.url = "https://forum.odchqpto.com/pages/thread/index?tgid=5636&id=1"

        def ele(self, selector, timeout=0.2):
            if "sq-thread-title" in selector:
                return type("Ele", (), {"text": self.title})()
            if "sq-thread-footer" in selector:
                return type("Ele", (), {"text": self.footer})()
            return None

    old_thread = Thread("旧帖子", "2026-08-04")
    target_thread = Thread("有奖竞答 DAY2", "34分钟前")

    class Container:
        calls = 0

        @classmethod
        def eles(cls, _selector, timeout=1):
            cls.calls += 1
            return [old_thread] if cls.calls < 3 else [target_thread, old_thread]

    class Tab:
        url = ""
        html = ""

        def get(self, url) -> None:
            self.url = url

        @staticmethod
        def ele(_selector, timeout=1):
            return Container()

        def close(self) -> None:
            browser.tabs_count -= 1

    class Browser:
        tabs_count = 1

        def new_tab(self):
            self.tabs_count += 1
            return tab

    tab = Tab()
    browser = Browser()
    monkeypatch.setattr(crawler.time, "monotonic", Clock.monotonic)
    monkeypatch.setattr(crawler.time, "sleep", Clock.sleep)
    monkeypatch.setattr(crawler, "_load_latest_comments", lambda *_args, **_kwargs: [])

    probe = crawler.probe_tianjige_forum_quiz(
        "2026-08-05",
        browser_factory=lambda: browser,
        timeout_seconds=5,
    )

    assert probe.status == "waiting_answers"
    assert probe.profile_thread_count == 2
    assert target_thread.clicked is True
    assert Container.calls == 3
    assert browser.tabs_count == 1


def test_probe_spends_full_timeout_before_reporting_missing_thread(monkeypatch) -> None:
    class Clock:
        value = 0.0

        @classmethod
        def monotonic(cls):
            return cls.value

        @classmethod
        def sleep(cls, seconds):
            cls.value += seconds

    class OldThread:
        text = "旧帖子 2026-08-04"

        @staticmethod
        def ele(_selector, timeout=0.2):
            return None

    old_thread = OldThread()

    class Container:
        @staticmethod
        def eles(_selector, timeout=1):
            return [old_thread]

    class Tab:
        url = ""
        html = ""

        def get(self, url) -> None:
            self.url = url

        @staticmethod
        def ele(_selector, timeout=1):
            return Container()

        def close(self) -> None:
            browser.tabs_count -= 1

    class Browser:
        tabs_count = 1

        def new_tab(self):
            self.tabs_count += 1
            return tab

    tab = Tab()
    browser = Browser()
    monkeypatch.setattr(crawler.time, "monotonic", Clock.monotonic)
    monkeypatch.setattr(crawler.time, "sleep", Clock.sleep)

    probe = crawler.probe_tianjige_forum_quiz(
        "2026-08-05",
        browser_factory=lambda: browser,
        timeout_seconds=5,
    )

    assert probe.status == "waiting_thread"
    assert probe.profile_thread_count == 1
    assert probe.elapsed_seconds == pytest.approx(5.0)
    assert browser.tabs_count == 1


def test_current_quiz_thread_accepts_relative_time_and_tuesday_title_without_day1() -> None:
    class Thread:
        @staticmethod
        def ele(selector, timeout=0.2):
            text = (
                "有奖竞答丨参与“仙市”主题有奖竞答，888灵石等你来战！"
                if "sq-thread-title" in selector
                else "2分钟前\n100\n20\n3"
            )
            return type("Ele", (), {"text": text})()

    matched, title = crawler._is_current_quiz_thread(Thread(), "2026-08-04")

    assert matched is True
    assert "有奖竞答" in title

@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (datetime(2026, 8, 4, 17, 0), datetime(2026, 8, 4, 17, 59, 50)),
        (datetime(2026, 8, 4, 18, 0), datetime(2026, 8, 5, 17, 59, 50)),
        (datetime(2026, 8, 6, 19, 0), datetime(2026, 8, 11, 17, 59, 50)),
    ],
)
def test_next_tianjige_forum_quiz_trigger_at(current, expected) -> None:
    assert task.next_tianjige_forum_quiz_trigger_at(current) == expected


def test_waiting_thread_short_cell_writes_poll_next_time(monkeypatch) -> None:
    moments = iter((datetime(2026, 8, 5, 18, 0, 0), datetime(2026, 8, 5, 18, 0, 15)))
    monkeypatch.setattr(task, "_now", lambda: next(moments))
    probe_kwargs = {}

    def fake_probe(*_args, **kwargs):
        probe_kwargs.update(kwargs)
        return TianjigeQuizProbe(
            status="waiting_thread",
            profile_thread_count=8,
            elapsed_seconds=15.0,
        )

    monkeypatch.setattr(
        task,
        "probe_tianjige_forum_quiz",
        fake_probe,
    )
    runner = _Runner()

    result = task.execute_tianjige_forum_quiz_task(runner, {}, {"poll_seconds": 10}, object())

    assert "job_status" not in result
    assert "next_time" not in result
    assert "等待页面 15.0 秒并读取 8 条动态" in result["message"]
    assert runner.next_times == [(task.TIANJIGE_FORUM_QUIZ_TASK_ID, "2026-08-05 18:00:25")]
    assert probe_kwargs["overall_timeout_seconds"] is None


def test_readonly_ready_result_does_not_write_ledger_or_submit(monkeypatch) -> None:
    monkeypatch.setattr(task, "_now", lambda: datetime(2026, 8, 5, 18, 0, 0))
    monkeypatch.setattr(task, "probe_tianjige_forum_quiz", lambda *_args, **_kwargs: _ready_probe())
    monkeypatch.setattr(task, "_read_submission_ledger", lambda: {})
    monkeypatch.setattr(task, "_write_submission_ledger", lambda _value: pytest.fail("must not write ledger"))
    monkeypatch.setattr(
        task,
        "submit_tianjige_forum_quiz_answer",
        lambda *_args, **_kwargs: pytest.fail("must not submit"),
    )
    runner = _Runner()

    result = task.execute_tianjige_forum_quiz_task(
        runner,
        {},
        {"submit_enabled": False},
        object(),
    )

    assert "job_status" not in result
    assert "next_time" not in result
    assert runner.next_times[-1][1] == "2026-08-05 18:00:10"


def test_successful_submission_records_intent_then_completion(monkeypatch) -> None:
    monkeypatch.setattr(task, "_now", lambda: datetime(2026, 8, 5, 18, 0, 0))
    monkeypatch.setattr(task, "probe_tianjige_forum_quiz", lambda *_args, **_kwargs: _ready_probe())
    monkeypatch.setattr(task, "_read_submission_ledger", lambda: {})
    written: list[dict] = []
    monkeypatch.setattr(task, "_write_submission_ledger", lambda value: written.append(dict(value)))
    monkeypatch.setattr(task, "submit_tianjige_forum_quiz_answer", lambda *_args, **_kwargs: True)
    runner = _Runner()

    result = task.execute_tianjige_forum_quiz_task(runner, {}, {}, object())

    assert "job_status" not in result
    assert [item["state"] for item in written] == ["submitting", "submitted"]
    assert "next_time" not in result
    assert runner.next_times[-1][1] == "2026-08-06 17:59:50"


def test_uncertain_submission_is_not_retried(monkeypatch) -> None:
    monkeypatch.setattr(task, "_now", lambda: datetime(2026, 8, 5, 18, 0, 0))
    monkeypatch.setattr(task, "probe_tianjige_forum_quiz", lambda *_args, **_kwargs: _ready_probe())
    monkeypatch.setattr(task, "_read_submission_ledger", lambda: {})
    written: list[dict] = []
    monkeypatch.setattr(task, "_write_submission_ledger", lambda value: written.append(dict(value)))
    monkeypatch.setattr(
        task,
        "submit_tianjige_forum_quiz_answer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network uncertain")),
    )
    runner = _Runner()

    result = task.execute_tianjige_forum_quiz_task(runner, {}, {}, object())

    assert "job_status" not in result
    assert [item["state"] for item in written] == ["submitting"]
    assert "next_time" not in result
    assert runner.next_times[-1][1] == "2026-08-06 17:59:50"


def test_tianjige_forum_quiz_is_one_standard_job() -> None:
    jobs = [
        item
        for item in default_data_annotation_scheduler_tasks(now=datetime(2026, 8, 5, 13, 0))
        if item["task_type"] == "tianjige_forum_quiz"
    ]

    assert len(jobs) == 1
    assert jobs[0]["id"] == "tianjige-forum-quiz"
    assert jobs[0]["next_time"] == "2026-08-05 17:59:50"
    assert jobs[0]["dispatch_level"] == 0
    assert jobs[0]["error_retry_delay_seconds"] == 60
    assert jobs[0]["payload"]["submit_enabled"] is True


def test_codeyun_tianjige_sources_have_no_wild_code_or_wechat_dependency() -> None:
    sources = [
        __import__("inspect").getsource(__import__("backend.core.fanxiu.tianjige_forum_quiz", fromlist=["*"])),
        __import__("inspect").getsource(task),
    ]
    combined = "\n".join(sources).casefold()

    assert "xlsln" not in combined
    assert "xlproject" not in combined
    assert "wechat" not in combined


def test_comment_loading_defaults_to_full_history_with_watchdog() -> None:
    import inspect

    parameters = inspect.signature(crawler._load_latest_comments).parameters

    assert parameters["max_expand_rounds"].default is None
    assert crawler.TIANJIGE_QUIZ_COMMENT_LOAD_TIMEOUT_SECONDS == 120
