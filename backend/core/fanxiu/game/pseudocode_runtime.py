from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from backend.core.ai.chat import OllamaClientError, chat_with_provider
from backend.core.devices.device import build_background_popen_kwargs
from backend.core.fanxiu.runtime.mumu_control import get_fanxiu_mainwin_root


PSEUDOCODE_DIRNAME = "伪代码"
COMPILED_SCRIPT_FILENAME = "compiled_runtime.py"
COMPILE_MANIFEST_FILENAME = "compile_manifest.json"
LAST_RESULT_FILENAME = "last_result.json"
FANXIU_PSEUDOCODE_PROVIDER_ID = "deepseek"
FANXIU_PSEUDOCODE_DEFAULT_MODEL = "deepseek-v4-pro"


def _runtime_dir() -> Path:
    path = get_fanxiu_mainwin_root() / PSEUDOCODE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_dir() -> Path:
    path = _runtime_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _compiled_script_path() -> Path:
    return _runtime_dir() / COMPILED_SCRIPT_FILENAME


def _manifest_path() -> Path:
    return _runtime_dir() / COMPILE_MANIFEST_FILENAME


def _last_result_path() -> Path:
    return _runtime_dir() / LAST_RESULT_FILENAME


def _json_dumps(payload: Any, *, indent: int | None = None) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=indent)


def _sanitize_cache_stem(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    normalized = normalized.strip("._-")
    return normalized or hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _function_name(card: dict[str, Any], index: int) -> str:
    prefix = "script"
    stem = _sanitize_cache_stem(str(card.get("id") or index)).replace(".", "_").replace("-", "_")
    if not stem or stem[0].isdigit():
        stem = f"card_{stem}"
    return f"{prefix}_{stem}"


def _card_hash(card: dict[str, Any]) -> str:
    payload = {
        "scope": card.get("scope") or "action",
        "title": card.get("title") or "",
        "body": card.get("body") or "",
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(payload, indent=2), encoding="utf-8")


def _build_compile_prompt(card: dict[str, Any], function_name: str) -> str:
    title = str(card.get("title") or "").strip()
    body = str(card.get("body") or "").strip()
    context = card.get("annotation_context") or {}
    return textwrap.dedent(
        f"""
        你是凡修手游自动化的伪代码编译器。你的任务是把一个用户用自然语言写的伪代码卡片，翻译成可执行 Python 函数。

        重要约定：
        - 不要把用户文本当成固定 DSL 解析；用户会用自然语言、省略、伪代码和局部约定表达意图，你需要按语义理解。
        - 只输出 Python 代码，不要输出 Markdown、解释文字或代码块围栏。
        - 必须生成函数：def {function_name}(ctx):
        - 函数内部只能通过 ctx 表达业务动作，不要直接操作文件、网络、数据库、系统进程或 GUI。
        - 如果某个自然语言步骤目前无法可靠执行，用 ctx.todo("...") 记录，不要臆造危险操作。

        可用 ctx API：
        - ctx.log(message): 写日志。
        - ctx.ref(name): 读取截图引用或标注框，例如 ctx.ref("2#")、ctx.ref("2#日常")。
        - ctx.wait(description, target=None): 等待某个画面、文本或状态。
        - ctx.click(description, target=None): 点击目标。target 可以传 ctx.ref(...) 的结果。
        - ctx.scroll_find_text(text): 滚动查找文本。
        - ctx.read_relative_fields(anchor, fields): 基于锚点读取相邻字段。
        - ctx.todo(description): 记录后续需要补充真实执行器的步骤。
        - ctx.result(key, value): 写结构化结果。
        - ctx.yield_tick(reason=""): 当前脚本完成一次处理后让出本轮 tick。

        截图引用规则：
        - “2#”表示截图 0002.jpg 及其所有预标注框。
        - “2#日常”表示截图 0002.jpg 中名为“日常”的预标注框。
        - 下面的 annotation_context 已经把本卡片文本涉及的 # 引用解析为上下文；请优先使用这些上下文。

        卡片：
        - 类型：脚本
        - 标题：{title or "未命名"}

        用户伪代码：
        {body or "（空）"}

        annotation_context:
        {_json_dumps(context, indent=2)}

        输出要求：
        - 只输出 Python 源码。
        - 不要加 import，除非只用 Python 标准库且确实必要。
        - 不要定义 {function_name} 以外的顶层函数或类。
        """
    ).strip()


def _extract_python_code(content: str) -> str:
    text = content.strip()
    fence = re.search(r"```(?:python|py)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _validate_python_function(code: str, function_name: str) -> None:
    try:
        module = ast.parse(code)
    except SyntaxError as exc:
        raise RuntimeError(f"DeepSeek 生成的 Python 语法无效：{exc}") from exc
    names = {node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if function_name not in names:
        raise RuntimeError(f"DeepSeek 未生成指定函数：{function_name}")


def _call_deepseek_for_card(card: dict[str, Any], function_name: str, *, model: str, timeout: int) -> tuple[str, str]:
    prompt = _build_compile_prompt(card, function_name)
    resolved_model = (
        model.strip()
        or os.getenv("CODEYUN_FANXIU_PSEUDOCODE_MODEL", "").strip()
        or FANXIU_PSEUDOCODE_DEFAULT_MODEL
    )
    try:
        response = chat_with_provider(
            provider_id=FANXIU_PSEUDOCODE_PROVIDER_ID,
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是凡修手游自动化的伪代码编译器，只输出 Python 源码。",
            temperature=0.1,
            timeout_seconds=timeout,
        )
    except OllamaClientError as exc:
        raise RuntimeError(f"DeepSeek 编译失败：{exc}") from exc

    raw_output = str(response.get("content") or "").strip()
    if not raw_output:
        raise RuntimeError("DeepSeek 未返回代码")

    code = _extract_python_code(raw_output)
    _validate_python_function(code, function_name)
    return code, f"DeepSeek 编译完成：model={response.get('model') or resolved_model}"


def _compile_card(card: dict[str, Any], index: int, *, model: str, timeout: int) -> dict[str, Any]:
    function_name = _function_name(card, index)
    card_hash = _card_hash(card)
    cache_path = _cache_dir() / f"{_sanitize_cache_stem(str(card.get('id') or index))}.json"
    cached = _read_json(cache_path)
    if cached.get("hash") == card_hash and isinstance(cached.get("code"), str) and cached.get("code", "").strip():
        code = str(cached["code"])
        _validate_python_function(code, function_name)
        return {
            "id": card.get("id"),
            "title": card.get("title") or "",
            "scope": card.get("scope") or "action",
            "function_name": function_name,
            "hash": card_hash,
            "code": code,
            "cache_hit": True,
            "log": f"缓存命中：{card.get('title') or card.get('id')}",
        }

    code, log = _call_deepseek_for_card(card, function_name, model=model, timeout=timeout)
    payload = {
        "id": card.get("id"),
        "title": card.get("title") or "",
        "scope": card.get("scope") or "action",
        "function_name": function_name,
        "hash": card_hash,
        "code": code,
        "compiled_at": time.time(),
    }
    _write_json(cache_path, payload)
    return {
        **payload,
        "cache_hit": False,
        "log": f"重新编译：{card.get('title') or card.get('id')}\n{log}",
    }


def _build_annotation_index(cards: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for card in cards:
        context = card.get("annotation_context") or {}
        images = context.get("images") if isinstance(context, dict) else None
        if isinstance(images, list):
            for image in images:
                if not isinstance(image, dict):
                    continue
                image_no = image.get("image_no")
                if image_no is not None:
                    index[f"{int(image_no)}#"] = image
                for box in image.get("boxes") or []:
                    if not isinstance(box, dict):
                        continue
                    name = str(box.get("name") or "").strip()
                    if image_no is not None and name:
                        index[f"{int(image_no)}#{name}"] = {"image": image, "box": box}

        refs = context.get("refs") if isinstance(context, dict) else None
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                name = str(ref.get("ref") or "").strip()
                if name:
                    index[name] = ref
    return index


def _assemble_script(cards: list[dict[str, Any]], compiled_cards: list[dict[str, Any]]) -> str:
    manifest_cards = [
        {
            "id": card.get("id"),
            "scope": card.get("scope") or "action",
            "title": card.get("title") or "",
            "function_name": card.get("function_name") or "",
            "hash": card.get("hash") or "",
        }
        for card in compiled_cards
    ]
    context = {
        "compiled_at": time.time(),
        "annotation_index": _build_annotation_index(cards),
        "cards": manifest_cards,
    }
    snippets = "\n\n\n".join(str(card["code"]).strip() for card in compiled_cards)
    script_names = [card["function_name"] for card in compiled_cards]
    return (
        "# Auto-generated by CodeYun Fanxiu pseudocode compiler.\n"
        "# Do not edit this file directly; use the pseudo-code cards in game-window2.\n\n"
        "from __future__ import annotations\n\n"
        "import asyncio\n"
        "import inspect\n"
        "import json\n"
        "import time\n"
        "from pathlib import Path\n"
        "from typing import Any\n\n"
        f"CONTEXT = {_json_dumps(context, indent=2)}\n\n"
        f"CARD_MANIFEST = {_json_dumps(manifest_cards, indent=2)}\n\n"
        + textwrap.dedent(
            """
            class YieldTick(Exception):
                def __init__(self, reason: str = "") -> None:
                    super().__init__(reason)
                    self.reason = reason


            class FanxiuPseudoRuntime:
                def __init__(self, context: dict[str, Any]) -> None:
                    self.context = context
                    self.events: list[dict[str, Any]] = []
                    self.results: dict[str, Any] = {}

                def log(self, message: Any) -> None:
                    self.events.append({"type": "log", "message": str(message), "time": time.time()})

                def ref(self, name: str) -> Any:
                    value = self.context.get("annotation_index", {}).get(str(name))
                    self.events.append({"type": "ref", "name": str(name), "hit": value is not None, "time": time.time()})
                    return value

                def wait(self, description: str, target: Any = None) -> None:
                    self.events.append({"type": "wait", "description": description, "target": target, "time": time.time()})

                def click(self, description: str, target: Any = None) -> None:
                    self.events.append({"type": "click", "description": description, "target": target, "time": time.time()})

                def scroll_find_text(self, text: str) -> None:
                    self.events.append({"type": "scroll_find_text", "text": text, "time": time.time()})

                def read_relative_fields(self, anchor: Any, fields: list[str] | tuple[str, ...]) -> None:
                    self.events.append({"type": "read_relative_fields", "anchor": anchor, "fields": list(fields), "time": time.time()})

                def todo(self, description: str) -> None:
                    self.events.append({"type": "todo", "description": description, "time": time.time()})

                def result(self, key: str, value: Any) -> None:
                    self.results[str(key)] = value
                    self.events.append({"type": "result", "key": str(key), "value": value, "time": time.time()})

                def yield_tick(self, reason: str = "") -> None:
                    self.events.append({"type": "yield_tick", "reason": reason, "time": time.time()})
                    raise YieldTick(reason)


            def _run_card(ctx: FanxiuPseudoRuntime, fn: Any, card: dict[str, Any]) -> None:
                title = card.get("title") or card.get("function_name") or "未命名"
                ctx.log(f"开始：{title}")
                try:
                    value = fn(ctx)
                    if inspect.isawaitable(value):
                        value = asyncio.run(value)
                    if value is not None:
                        ctx.result(str(card.get("function_name") or title), value)
                except YieldTick as exc:
                    ctx.log(f"让出 tick：{title} {exc.reason}".strip())
                except Exception as exc:
                    ctx.events.append({
                        "type": "error",
                        "title": title,
                        "message": str(exc),
                        "time": time.time(),
                    })
                    raise
                finally:
                    ctx.log(f"结束：{title}")

            """
        )
        + "\n\n"
        + snippets
        + "\n\n"
        + f"SCRIPT_FUNCTIONS = {[name for name in script_names]!r}\n\n"
        + textwrap.dedent(
            """
            def main() -> None:
                ctx = FanxiuPseudoRuntime(CONTEXT)
                functions = globals()
                for card in CARD_MANIFEST:
                    _run_card(ctx, functions[card["function_name"]], card)
                payload = {
                    "ok": True,
                    "ran_at": time.time(),
                    "cards": CARD_MANIFEST,
                    "events": ctx.events,
                    "results": ctx.results,
                }
                output_path = Path(__file__).resolve().parent / "last_result.json"
                output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(payload, ensure_ascii=False))


            if __name__ == "__main__":
                main()
            """
        ).strip()
        + "\n"
    )


def compile_fanxiu_pseudocode(cards: list[dict[str, Any]], *, model: str = "", timeout: int = 300) -> dict[str, Any]:
    enabled_cards = [
        card
        for card in cards
        if bool(card.get("enabled", True)) and (str(card.get("title") or "").strip() or str(card.get("body") or "").strip())
    ]
    compiled_cards: list[dict[str, Any]] = []
    logs: list[str] = []
    for index, card in enumerate(enabled_cards, start=1):
        compiled = _compile_card(card, index, model=model, timeout=timeout)
        compiled_cards.append(compiled)
        logs.append(str(compiled.get("log") or ""))

    script = _assemble_script(enabled_cards, compiled_cards)
    script_path = _compiled_script_path()
    script_path.write_text(script, encoding="utf-8")
    manifest = {
        "compiled_at": time.time(),
        "script_path": os.fspath(script_path),
        "cards": [
            {
                "id": card.get("id"),
                "scope": card.get("scope"),
                "title": card.get("title"),
                "function_name": card.get("function_name"),
                "hash": card.get("hash"),
                "cache_hit": bool(card.get("cache_hit")),
            }
            for card in compiled_cards
        ],
    }
    _write_json(_manifest_path(), manifest)
    cache_hits = sum(1 for card in compiled_cards if card.get("cache_hit"))
    cache_misses = max(0, len(compiled_cards) - cache_hits)
    result = {
        "script_path": os.fspath(script_path),
        "compiled_cards": len(compiled_cards),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
    }
    return {
        "ok": True,
        "status": "compiled",
        "script_path": os.fspath(script_path),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "compiled_cards": len(compiled_cards),
        "log": "\n\n".join(line for line in logs if line) or "没有可编译的伪代码卡片，已生成空脚本。",
        "result": _json_dumps(result, indent=2),
        "updated_at": time.time(),
    }


def start_fanxiu_pseudocode_script(*, timeout: int = 120) -> dict[str, Any]:
    script_path = _compiled_script_path()
    if not script_path.is_file():
        raise RuntimeError("请先编译伪代码")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, os.fspath(script_path)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.fspath(_runtime_dir()),
            env=env,
            timeout=timeout,
            check=False,
            **build_background_popen_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("伪代码脚本运行超时") from exc
    except OSError as exc:
        raise RuntimeError(f"启动伪代码脚本失败：{exc}") from exc

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    result_payload = _read_json(_last_result_path())
    result_text = _json_dumps(result_payload, indent=2) if result_payload else stdout
    log = "\n".join(
        part for part in [
            f"$ {' '.join(command)}",
            f"returncode: {completed.returncode}",
            stderr,
            stdout,
        ]
        if part
    )
    if completed.returncode != 0:
        raise RuntimeError(f"伪代码脚本运行失败：{log[-4000:]}")
    return {
        "ok": True,
        "status": "started",
        "script_path": os.fspath(script_path),
        "cache_hits": 0,
        "cache_misses": 0,
        "compiled_cards": 0,
        "log": log or "伪代码脚本已运行",
        "result": result_text,
        "updated_at": time.time(),
    }

