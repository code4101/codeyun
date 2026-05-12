from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.core import evomind


def test_scan_codex_cases_detects_explicit_learning_marker(monkeypatch) -> None:
    def fake_overview(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "root_dir": "C:/Users/test/.codex",
            "total_threads": 1,
            "groups": [
                {
                    "threads": [
                        {
                            "id": "thread-1",
                            "title": "表格列宽真实案例",
                            "archived": False,
                            "preview": "设计 EvoMind",
                            "project_label": "codeyun",
                        }
                    ]
                }
            ],
        }

    def fake_detail(root_dir: str, thread_id: str, session: Any = None) -> dict[str, Any]:
        return {
            "root_dir": root_dir,
            "thread": {
                "id": thread_id,
                "title": "表格列宽真实案例",
                "project_label": "codeyun",
                "workspace_root": "D:/home/codeyun",
            },
            "messages": [
                {"seq": 1, "role": "user", "text": "调整表格页面列宽"},
                {"seq": 2, "role": "assistant", "text": "我做一个很多卡片的复杂页面。"},
                {"seq": 3, "role": "user", "text": "这里这个表格列宽可以作为样例学习，不要这么冗余，列宽要根据内容自适应。<image>"},
            ],
        }

    monkeypatch.setattr(evomind, "build_codex_overview", fake_overview)
    monkeypatch.setattr(evomind, "build_codex_thread_detail", fake_detail)

    result = evomind.scan_evomind_cases_from_codex(session=None)  # type: ignore[arg-type]

    assert result["scanned_threads"] == 1
    assert result["items"][0]["signal_type"] == "explicit_learning_marker"
    assert result["items"][0]["evidence_strength"] == "p0"
    assert "忽略用户明确要求沉淀的样例" in result["items"][0]["anti_patterns"]


def test_scan_codex_cases_keeps_multi_turn_evidence_window(monkeypatch) -> None:
    monkeypatch.setattr(
        evomind,
        "build_codex_overview",
        lambda *args, **kwargs: {
            "root_dir": "C:/Users/test/.codex",
            "total_threads": 1,
            "groups": [
                {
                    "threads": [
                        {
                            "id": "thread-multi",
                            "title": "AI 提交右栏密度多轮修正",
                            "archived": False,
                            "preview": "调整 AI 提交页面",
                            "project_label": "codeyun",
                        }
                    ]
                }
            ],
        },
    )
    monkeypatch.setattr(
        evomind,
        "build_codex_thread_detail",
        lambda root_dir, thread_id, session=None: {
            "root_dir": root_dir,
            "thread": {"id": thread_id, "title": "AI 提交右栏密度多轮修正", "project_label": "codeyun"},
            "messages": [
                {"seq": 1, "role": "user", "text": "AI 提交页面右栏敏感信息可以更紧凑。"},
                {"seq": 2, "role": "assistant", "text": "我先改标题字号。"},
                {"seq": 3, "role": "user", "text": "不是只改标题，右栏整体密度要像新增文件区域。"},
                {"seq": 4, "role": "assistant", "text": "我把每项拆成了卡片。"},
                {"seq": 5, "role": "user", "text": "还是大，而且分块更多了，上下文被切碎。"},
                {"seq": 6, "role": "assistant", "text": "我继续调小局部 padding。"},
                {
                    "seq": 7,
                    "role": "user",
                    "text": "你反复没理解：要按新增文件右栏作为参照，连续命中内容合并展示，别只微调局部样式。",
                },
                {"seq": 8, "role": "assistant", "text": "最终改成按参照区域统一字号、行高、间距，并合并连续命中。"},
            ],
        },
    )

    result = evomind.scan_evomind_cases_from_codex(session=None, use_codex_cli=False)  # type: ignore[arg-type]

    turns = result["items"][0]["evidence_turns"]
    assert len(turns) > 4
    assert [turn["seq"] for turn in turns] == list(range(1, 9))
    assert [turn["seq"] for turn in turns if turn["is_signal"]] == [7]
    assert [turn["label"] for turn in turns] == ["任务", "反面", "纠正", "反面", "纠正", "反面", "关键", "正向"]
    assert [turn["kind"] for turn in turns] == [
        "task",
        "anti",
        "correction",
        "anti",
        "correction",
        "anti",
        "key",
        "positive",
    ]


def test_scan_codex_cases_prefers_concrete_operation_over_abstract_meta(monkeypatch) -> None:
    monkeypatch.setattr(
        evomind,
        "build_codex_overview",
        lambda *args, **kwargs: {
            "root_dir": "C:/Users/test/.codex",
            "total_threads": 2,
            "groups": [
                {
                    "threads": [
                        {
                            "id": "abstract-thread",
                            "title": "EvoMind 方案讨论",
                            "archived": False,
                            "preview": "讨论样例如何捕捉",
                            "project_label": "codeyun",
                        },
                        {
                            "id": "concrete-thread",
                            "title": "sheet 右键菜单修复",
                            "archived": False,
                            "preview": "修复 sheet tab 右键",
                            "project_label": "codeyun",
                        },
                    ]
                }
            ],
        },
    )

    def fake_detail(root_dir: str, thread_id: str, session: Any = None) -> dict[str, Any]:
        if thread_id == "abstract-thread":
            return {
                "root_dir": root_dir,
                "thread": {
                    "id": thread_id,
                    "title": "EvoMind 方案讨论",
                    "project_label": "codeyun",
                },
                "messages": [
                    {"seq": 1, "role": "user", "text": "怎么设计 EvoMind 案例捕捉机制？"},
                    {"seq": 2, "role": "assistant", "text": "可以做一套抽象的案例评分。"},
                    {"seq": 3, "role": "user", "text": "这里 EvoMind 可以作为样例学习，怎么捕捉有效样例？"},
                ],
            }
        return {
            "root_dir": root_dir,
            "thread": {
                "id": thread_id,
                "title": "sheet 右键菜单修复",
                "project_label": "codeyun",
            },
            "messages": [
                {"seq": 1, "role": "user", "text": "修复 sheet tab 右键菜单"},
                {"seq": 2, "role": "assistant", "text": "我只验证了单元格右键。"},
                {
                    "seq": 3,
                    "role": "user",
                    "text": "sheet右键的功能我始终试不出来！你是不是没理解到要处理的是tab的右键？",
                },
            ],
        }

    monkeypatch.setattr(evomind, "build_codex_thread_detail", fake_detail)

    result = evomind.scan_evomind_cases_from_codex(session=None, max_cases=1)  # type: ignore[arg-type]

    assert result["items"][0]["source"]["thread_id"] == "concrete-thread"
    assert "sheet右键" in result["items"][0]["user_corrections"]


def test_scan_codex_cases_detects_friction_marker(monkeypatch) -> None:
    monkeypatch.setattr(
        evomind,
        "build_codex_overview",
        lambda *args, **kwargs: {
            "root_dir": "C:/Users/test/.codex",
            "total_threads": 1,
            "groups": [
                {
                    "threads": [
                        {
                            "id": "thread-2",
                            "title": "UI 简洁纠错",
                            "archived": False,
                            "preview": "做一个工具页",
                            "project_label": "codeyun",
                        }
                    ]
                }
            ],
        },
    )
    monkeypatch.setattr(
        evomind,
        "build_codex_thread_detail",
        lambda root_dir, thread_id, session=None: {
            "root_dir": root_dir,
            "thread": {
                "id": thread_id,
                "title": "UI 简洁纠错",
                "project_label": "codeyun",
            },
            "messages": [
                {"seq": 1, "role": "user", "text": "做一个配置页面"},
                {"seq": 2, "role": "assistant", "text": "这里加很多摘要卡片和常驻说明。"},
                {"seq": 3, "role": "user", "text": "我说过不要这么复杂，怎么还反复重复同一个事实？"},
            ],
        },
    )

    result = evomind.scan_evomind_cases_from_codex(session=None)  # type: ignore[arg-type]

    item = result["items"][0]
    assert item["signal_type"] == "friction"
    assert item["friction_level"] == "high"
    assert any("决策依据" in pattern for pattern in item["positive_patterns"])
    assert item["inferred_rule"].startswith("当用户纠正界面或方案过度复杂")
    assert "先识别用户真正参考什么做判断" not in item["inferred_rule"]
    assert "再删掉不服务这个判断" in item["inferred_rule"]


def test_scan_codex_cases_can_refine_candidates_with_codex_cli(monkeypatch) -> None:
    monkeypatch.setattr(
        evomind,
        "build_codex_overview",
        lambda *args, **kwargs: {
            "root_dir": "C:/Users/test/.codex",
            "total_threads": 1,
            "groups": [
                {
                    "threads": [
                        {
                            "id": "thread-cli",
                            "title": "AI 提交文件清单压缩",
                            "archived": False,
                            "preview": "调整 AI 提交页面",
                            "project_label": "codeyun",
                        }
                    ]
                }
            ],
        },
    )
    monkeypatch.setattr(
        evomind,
        "build_codex_thread_detail",
        lambda root_dir, thread_id, session=None: {
            "root_dir": root_dir,
            "thread": {
                "id": thread_id,
                "title": "AI 提交文件清单压缩",
                "project_label": "codeyun",
            },
            "messages": [
                {"seq": 1, "role": "user", "text": "AI提交，文件清单这里可以更紧凑一些"},
                {"seq": 2, "role": "assistant", "text": "我把每个问题都做成大卡片展示。"},
                {"seq": 3, "role": "user", "text": "我说过不要这么冗余，好像还是大啊？你就设置得跟新增文件右栏一样，敏感信息不能这样分块展示。"},
            ],
        },
    )

    called: dict[str, Any] = {}

    def fake_refine(
        candidates: list[dict[str, Any]],
        *,
        max_cases: int,
        candidate_limit: int,
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        called["candidate_limit"] = candidate_limit
        called["count"] = len(candidates)
        refined = dict(candidates[0])
        refined["title"] = "Codex判断：文件清单密度对齐"
        refined["inferred_rule"] = "列表密度应对齐同屏参照区域，连续上下文合并展示。"
        return [refined], {
            "codex_cli_invoked": True,
            "cache_hit_count": 0,
            "cache_miss_count": 1,
            "rule_hash": "test-rule",
            "cache_rule_mismatch": False,
            "cache_reset": False,
        }

    monkeypatch.setattr(evomind, "_refine_candidates_with_codex_cli", fake_refine)

    result = evomind.scan_evomind_cases_from_codex(  # type: ignore[arg-type]
        session=None,
        use_codex_cli=True,
        codex_cli_limit=3,
    )

    assert result["codex_cli_used"] is True
    assert result["analysis_mode"] == "codex_cli"
    assert called == {"candidate_limit": 3, "count": 1}
    assert result["items"][0]["title"] == "Codex判断：文件清单密度对齐"


def test_codex_cli_scan_cache_reuses_rule_matched_candidates(monkeypatch, tmp_path) -> None:
    cache_path = tmp_path / "scan-cache.json"
    monkeypatch.setattr(evomind, "_evomind_cache_path", lambda: cache_path)
    monkeypatch.setattr(evomind, "_codex_cli_executable", lambda: "codex")

    candidates = [
        {
            "id": "candidate-1",
            "title": "候选1",
            "domain": "frontend_ui",
            "signal_type": "friction",
            "evidence_strength": "p1",
            "friction_level": "high",
            "original_task": "压缩文件清单",
            "bad_attempt": "分成很多卡片",
            "user_corrections": "不要分块，按连续上下文展示",
            "final_pattern": "合并上下文展示",
            "inferred_rule": "旧规则",
            "anti_patterns": [],
            "positive_patterns": [],
            "source": {"thread_id": "t1", "message_seq": 3, "timestamp": "1", "score": 100},
        },
        {
            "id": "candidate-2",
            "title": "候选2",
            "domain": "meta_learning",
            "signal_type": "explicit_learning_marker",
            "evidence_strength": "p0",
            "friction_level": "medium",
            "original_task": "讨论机制",
            "bad_attempt": "抽象方案",
            "user_corrections": "可以作为样例",
            "final_pattern": "继续讨论",
            "inferred_rule": "旧规则",
            "anti_patterns": [],
            "positive_patterns": [],
            "source": {"thread_id": "t2", "message_seq": 4, "timestamp": "2", "score": 90},
        },
    ]
    run_count = {"value": 0}

    def fake_run(args: list[str], **kwargs: Any) -> SimpleNamespace:
        run_count["value"] += 1
        output_path = args[args.index("-o") + 1]
        payload = {
            "items": [
                {
                    "id": "candidate-1",
                    "keep": True,
                    "quality_score": 91,
                    "title": "连续上下文展示",
                    "domain": "frontend_ui",
                    "signal_type": "friction",
                    "evidence_strength": "p0",
                    "friction_level": "high",
                    "original_task": "压缩文件清单",
                    "bad_attempt": "把问题拆成卡片",
                    "user_corrections": "不要分块，按连续上下文展示",
                    "final_pattern": "合并上下文展示",
                    "inferred_rule": "连续问题应合并为可扫描上下文。",
                    "anti_patterns": ["分块卡片化"],
                    "positive_patterns": ["合并上下文"],
                },
                {"id": "candidate-2", "keep": False, "reject_reason": "纯机制讨论"},
            ]
        }
        tmp_path.joinpath("stdout.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, ensure_ascii=False)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(evomind.subprocess, "run", fake_run)

    first_items, first_stats = evomind._refine_candidates_with_codex_cli(
        candidates,
        max_cases=2,
        candidate_limit=2,
        reset_cache=False,
    )
    second_items, second_stats = evomind._refine_candidates_with_codex_cli(
        candidates,
        max_cases=2,
        candidate_limit=2,
        reset_cache=False,
    )

    assert run_count["value"] == 1
    assert first_stats["codex_cli_invoked"] is True
    assert first_stats["cache_miss_count"] == 2
    assert second_stats["codex_cli_invoked"] is False
    assert second_stats["cache_hit_count"] == 2
    assert second_items[0]["title"] == first_items[0]["title"] == "连续上下文展示"


def test_scan_rule_text_changes_cache_rule_hash() -> None:
    assert evomind._scanner_rule_hash("规则 A") != evomind._scanner_rule_hash("规则 B")


def test_case_card_prompt_requires_codex_cli_to_rewrite_fragments() -> None:
    prompt = evomind._build_codex_cli_case_card_prompt(
        case={
            "id": "case-ui",
            "title": "UI 密度纠正",
            "user_corrections": "好像还是大啊",
            "bad_attempt": "对，你感觉没错",
        }
    )

    assert "聊天填充语和情绪原话" in prompt
    assert "规则精髓标题" in prompt
    assert "不是素材标题" in prompt
    assert "好像还是大啊" in prompt
    assert "先定参照，再统一密度" in prompt
    assert "把用户要求的整体密度优化误解为只调局部字号" in prompt


def test_scan_prompt_requires_rule_essence_title() -> None:
    prompt = evomind._build_codex_cli_scan_prompt(
        [
            {
                "id": "case-ui",
                "title": "敏感信息右栏对齐新增文件密度",
                "domain": "frontend_ui",
                "signal_type": "friction",
                "evidence_strength": "p0",
                "friction_level": "high",
                "original_task": "用户要求对齐右栏密度。",
                "bad_attempt": "只调局部标题字号。",
                "user_corrections": "要跟新增文件右栏一样。",
                "final_pattern": "对齐同屏参照。",
                "inferred_rule": "先定参照，再统一密度。",
            }
        ],
        max_cases=1,
    )

    assert "title 必须是这条案例学到的规则精髓" in prompt
    assert '"title": "规则精髓标题，不是素材标题"' in prompt


def test_derive_case_card_uses_codex_cli(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(evomind, "_codex_cli_executable", lambda: "codex")

    def fake_run(args: list[str], **kwargs: Any) -> SimpleNamespace:
        output_path = args[args.index("-o") + 1]
        payload = {
            "id": "case-ui",
            "title": "先定参照，再统一密度",
            "domain": "frontend_ui",
            "signal_type": "friction",
            "evidence_strength": "p0",
            "friction_level": "high",
            "original_task": "优化案例卡 UI",
            "bad_attempt": "只调小局部字体，没有处理整体密度。",
            "user_corrections": "用户指出仍然太大，要求参考已有区域。",
            "final_pattern": "对齐参照区域的字号、间距和分组密度。",
            "inferred_rule": "当用户指出界面仍然显得大或松散时，不要只调局部字号；先找同屏参照，再统一对齐字号、间距和分组密度。",
            "anti_patterns": [
                "把整体密度问题误解为只需要调小某个标题字号。",
                "把用户纠正原话直接塞进案例卡，导致反面模式不可复用。",
            ],
            "positive_patterns": [
                "先找到用户提到的参照区域，再对齐目标区域的字号、间距和分组密度。",
                "把用户原话改写为可执行模式，避免保留聊天填充语。",
            ],
        }
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, ensure_ascii=False)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(evomind.subprocess, "run", fake_run)

    result = evomind.derive_evomind_case_card(
        case={
            "id": "case-ui",
            "title": "UI 密度纠正",
            "domain": "frontend_ui",
            "bad_attempt": "对，你感觉没错",
            "user_corrections": "好像还是大啊",
            "final_pattern": "参考已有区域调整。",
        },
    )

    assert result["generation_mode"] == "codex_cli"
    assert result["title"] == "先定参照，再统一密度"
    assert result["evidence_strength"] == "p0"
    assert "好像还是大啊" not in "\n".join(result["anti_patterns"] + result["positive_patterns"])
    assert "同屏参照" in result["inferred_rule"]


def test_generate_rule_proposal_selects_existing_frontend_skill(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "前端UI规范"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'name: "前端UI规范"',
                'description: "为 codeyun 前端 UI 沉淀简洁、低冗余的界面规则。"',
                "---",
                "# 前端 UI 规范",
                "对素材、日志、会话、证据链，优先采用结构列表 + 选中详情。",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(evomind, "_skills_root_path", lambda: tmp_path)

    proposal = evomind.generate_evomind_rule_proposal(
        case={
            "id": "case-ui",
            "title": "素材详情展示过度平铺",
            "domain": "frontend_ui",
            "signal_type": "friction",
            "evidence_strength": "p0",
            "friction_level": "high",
            "original_task": "展示 EvoMind 素材详情",
            "bad_attempt": "把原始请求、AI回复、用户纠正全部平铺成多个 textarea。",
            "user_corrections": "参考 codex 那种一条条结构列表，点中某一条看详细信息。",
            "final_pattern": "左侧结构列表，右侧选中详情。",
            "inferred_rule": "展示高信息密度素材时使用 inspector 结构。",
            "anti_patterns": ["长文本全部平铺"],
            "positive_patterns": ["结构列表 + 选中详情"],
        },
        target="skill",
        use_codex_cli=False,
    )

    assert proposal["target_type"] == "skill"
    assert proposal["target"] == "前端UI规范"
    assert proposal["target_status"] == "existing"
    assert proposal["generation_mode"] == "heuristic"
    assert proposal["target_path"].endswith("前端UI规范\\SKILL.md") or proposal["target_path"].endswith("前端UI规范/SKILL.md")
    assert "展示高信息密度素材时使用 inspector 结构" in proposal["content"]


def test_generate_rule_proposal_falls_back_when_codex_cli_fails(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "设计品味"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: "设计品味"\ndescription: "降低偶然复杂度。"\n---\n# 设计品味\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(evomind, "_skills_root_path", lambda: tmp_path)

    def fail_cli(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mock cli failed")

    monkeypatch.setattr(evomind, "_generate_proposal_with_codex_cli", fail_cli)

    proposal = evomind.generate_evomind_rule_proposal(
        case={
            "id": "case-style",
            "title": "复杂度过高",
            "domain": "frontend_ui",
            "user_corrections": "不要为了高级感增加卡片套卡片。",
            "final_pattern": "单层结构就够。",
        },
        target="skill",
        use_codex_cli=True,
    )

    assert proposal["generation_mode"] == "heuristic_fallback"
    assert "mock cli failed" in proposal["warning"]
    assert proposal["lifecycle"] == "candidate"
