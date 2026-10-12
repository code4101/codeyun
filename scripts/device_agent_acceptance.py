"""本机真实 HTTP/Claude Code 验收：提问、临时配置调整、文件下载和原生追问。

仅修改 codeyun_temp_root 下的样例配置；不改变系统全局选项。
凭据由本机所有者公共入口获取，不打印或写入验收报告。
"""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="本机验收使用的已有超管账号")
    args = parser.parse_args()
    from backend.core.agents.cli import bootstrap
    from backend.core.agents.policy import load_config
    bootstrap(load_config())  # import xlproject.loadenv
    from backend.core.access.auth import create_local_owner_session
    from backend.core.devices.device import get_device_id
    from backend.core.temp_paths import codeyun_temp_root
    import requests

    base = "http://127.0.0.1:8000"
    http = requests.Session()
    http.trust_env = False
    owner = create_local_owner_session(username=args.username)
    user_headers = {"Authorization": "Bearer " + owner["access_token"]}
    def call(method, path, **kwargs):
        response = http.request(method, base + path, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()
    entries = call("GET", "/api/devices/", headers=user_headers)
    entry = next(e for e in entries if e["device_id"] == get_device_id() and e["mode"] == "local")
    token = call("GET", f"/api/devices/{entry['id']}/token", headers=user_headers)["token"]
    headers = {"X-Device-Token": token}
    api = "/api/device-agent"
    config = call("GET", api + "/config", headers=headers)
    if config["default_provider"] != "cc":
        call("PUT", api + "/config", headers=headers, json={"default_provider": "cc", "default_model": ""})
    root = codeyun_temp_root("device-agent-acceptance") / uuid.uuid4().hex
    root.mkdir(parents=True)
    marker = uuid.uuid4().hex
    target = root / "sample-config.json"
    target.write_text(json.dumps({"poll_interval_ms": 1000, "marker": marker}), encoding="utf-8")
    requester = {"kind": "device", "id": "loopback-acceptance", "display_name": "模拟对端"}
    def payload(instruction):
        return {"requester": requester, "request_type": "ask", "instruction": instruction}
    def wait(turn_id):
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            try:
                turn = call("GET", api + "/turns/" + turn_id, headers=headers)
            except requests.ConnectionError:
                time.sleep(2)  # 只重试幂等查询；POST 不重发。
                continue
            if turn["status"] in {"completed", "failed"}:
                if turn["status"] != "completed":
                    raise RuntimeError(turn["error_message"])
                return turn
            time.sleep(2)
        raise TimeoutError("验收轮次未完成：" + turn_id)
    def verify_file(turn, expected):
        attachment = turn["result_report"]["attachments"][0]
        response = http.get(base + attachment["download_path"], headers=headers, timeout=30)
        response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest() == attachment["sha256"]
        data = response.json()
        assert data == {"poll_interval_ms": expected, "marker": marker}, data
    created = call("POST", api + "/sessions", headers=headers, json=payload(
        f"本机接口验收，只读检查主机名和样例文件 {target}，将文件作为附件交付。不要修改文件；报告 findings 写出 poll_interval_ms 和 marker。"))
    print("session", created["id"], flush=True)
    first = wait(created["last_turn_id"])
    verify_file(first, 1000)
    print("提问与附件下载通过", flush=True)
    second_request = call("POST", api + f"/sessions/{created['id']}/turns", headers=headers, json=payload(
        "调整刚才的样例配置，把 poll_interval_ms 改成 250，保持 marker 不变。读回核验并交付更新后的文件；仅修改这一个临时样例。"))
    second = wait(second_request["id"])
    verify_file(second, 250)
    assert json.loads(target.read_text(encoding="utf-8"))["poll_interval_ms"] == 250
    verify_file(first, 1000)  # 旧下载必须仍是原来的快照。
    print("调整、回读与快照通过", flush=True)
    third_request = call("POST", api + f"/sessions/{created['id']}/turns", headers=headers, json=payload(
        "刚才调整的参数原值和现值分别是多少？marker 是什么？只回答并交付当前文件，不再修改。"))
    third = wait(third_request["id"])
    verify_file(third, 250)
    native_ids = [t["runtime"]["native_session_id"] for t in (first, second, third)]
    assert len(set(native_ids)) == 1
    assert marker in json.dumps(third["result_report"], ensure_ascii=False)
    report = {"session_id": created["id"], "turns": [first, second, third],
              "passed": ["question", "adjustment", "attachment_download", "immutable_snapshot", "native_followup"]}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("全部通过", root / "report.json", flush=True)


if __name__ == "__main__":
    main()
