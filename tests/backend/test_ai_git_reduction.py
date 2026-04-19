import json
import subprocess

from backend.core.ai_git_reduction import _build_reduce_system_prompt, generate_ai_git_commit_draft_hierarchical
from backend.core.git_tools import collect_git_reduction_source_units


def _run_git(repo_path, *args):
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _init_git_repo(repo_path):
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "CodeYun Test")
    _run_git(repo_path, "config", "user.email", "codeyun-test@example.com")
    (repo_path / "README.md").write_text("# demo\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    _run_git(repo_path, "commit", "-m", "init")


def test_collect_git_reduction_source_units_builds_file_level_units(tmp_path):
    repo_path = tmp_path / "git-reduction-source-units"
    _init_git_repo(repo_path)
    (repo_path / "README.md").write_text("# demo\n\nmore changes\n", encoding="utf-8")
    nested = repo_path / "src" / "中文 计划.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("print('hello')\n", encoding="utf-8")

    payload = collect_git_reduction_source_units(str(repo_path))

    assert payload["clean"] is False
    assert payload["source_unit_count"] == 2
    unit_map = {item["unit_id"]: item for item in payload["source_units"]}
    assert "README.md" in unit_map
    assert "src/中文 计划.py" in unit_map
    assert "文件路径: README.md" in unit_map["README.md"]["content"]
    assert "状态: 未跟踪" in unit_map["src/中文 计划.py"]["content"]
    assert "所属分组: src" in unit_map["src/中文 计划.py"]["content"]


def test_build_reduce_system_prompt_contains_reduce_specific_guidance():
    prompt = _build_reduce_system_prompt(style="summary", include_body=True)

    assert "Git 归并摘要助手" in prompt
    assert "summary 应该是归并后的整体概括" in prompt
    assert "candidate_body 必须是 2 到 4 条中文短句数组" in prompt
    assert "自动格式化成 1、2、3 编号正文" in prompt


def test_generate_ai_git_commit_draft_hierarchical_returns_final_draft_and_reduction_meta(tmp_path, monkeypatch):
    repo_path = tmp_path / "git-reduction-draft"
    _init_git_repo(repo_path)
    for index in range(10):
        if index % 2 == 0:
            relative = repo_path / "frontend" / f"view_{index}.ts"
        else:
            relative = repo_path / "backend" / f"worker_{index}.py"
        relative.parent.mkdir(parents=True, exist_ok=True)
        relative.write_text(f"line {index}\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.core.ai_git_reduction.chat_with_provider",
        lambda **_: {
            "model": "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": "Git 分层归并",
                    "summary": "把多组文件摘要归并成最终提交草稿",
                    "key_points": ["先按文件生成叶子摘要", "再逐层归并出最终标题"],
                    "risk_points": [],
                    "candidate_subject": "整理 Git 分层压缩提交流程",
                    "candidate_body": ["补齐文件级输入单元收集", "新增通用分层归并执行链路"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        },
    )

    payload = generate_ai_git_commit_draft_hierarchical(
        cwd=str(repo_path),
        provider_id="ollama",
        base_url=None,
        api_key=None,
        model="qwen3.5:4b-instruct",
        style="summary",
        include_body=True,
        branch_factor=3,
    )

    assert payload["inspect"]["repo_root"] == str(repo_path.resolve())
    assert payload["subject"] == "整理 Git 分层压缩提交流程"
    assert payload["body"] == ["补齐文件级输入单元收集", "新增通用分层归并执行链路"]
    assert payload["model"] == "qwen3.5:4b-instruct"
    assert payload["needs_split"] is False
    assert payload["reduction"]["level_count"] == 3
    assert payload["reduction"]["source_unit_count"] == 10
    assert payload["reduction"]["leaf_chunk_count"] == 4
    assert payload["reduction"]["node_count"] == 7
    assert payload["reduction"]["levels"][0]["preview_nodes"]
    assert payload["reduction"]["levels"][-1]["preview_nodes"][0]["candidate_subject"] == "整理 Git 分层压缩提交流程"


def test_generate_ai_git_commit_draft_hierarchical_repairs_invalid_json_response(tmp_path, monkeypatch):
    repo_path = tmp_path / "git-reduction-repair"
    _init_git_repo(repo_path)
    changed = repo_path / "src" / "feature.py"
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("print('repair me')\n", encoding="utf-8")

    calls = []

    def fake_chat_with_provider(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "model": "qwen3.5:4b-instruct",
                "content": '{"topic":"半截", "summary":"坏 JSON"',
            }
        return {
            "model": "qwen3.5:4b-instruct",
            "content": json.dumps(
                {
                    "topic": "修复后的摘要",
                    "summary": "修复成合法 JSON 后继续完成最终草稿",
                    "key_points": ["保留原始主题"],
                    "risk_points": [],
                    "candidate_subject": "修复 Git 分层压缩的 JSON 漂移",
                    "candidate_body": ["为非稳定模型输出增加修复步骤"],
                    "should_split": False,
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr("backend.core.ai_git_reduction.chat_with_provider", fake_chat_with_provider)

    payload = generate_ai_git_commit_draft_hierarchical(
        cwd=str(repo_path),
        provider_id="ollama",
        base_url=None,
        api_key=None,
        model="qwen3.5:4b-instruct",
        style="summary",
        include_body=True,
        branch_factor=10,
    )

    assert payload["subject"] == "修复 Git 分层压缩的 JSON 漂移"
    assert payload["body"] == ["为非稳定模型输出增加修复步骤"]
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "object"
    assert calls[1]["response_format"] == "json"
