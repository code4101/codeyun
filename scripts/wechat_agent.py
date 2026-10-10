"""Operate the account listener through its public business interface."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure", help="Set explicit account and hook; enabled service takes effect on next start")
    configure.add_argument("--chat", choices=["test", "production"], required=True)
    configure.add_argument("--enabled", action="store_true")
    configure.add_argument("--text-mentions", action="store_true", help="Also accept exact leading @考勤机器人 text (including old nicknames)")
    sub.add_parser("run", help="Run one durable service; Ctrl+C stops its workers")
    sub.add_parser("status")
    sub.add_parser('reload-when-idle', help='Drain current replies and reload without interrupting active work')
    switch = sub.add_parser('switch-processor', help='Move one group to another processor after a delivered receipt gate')
    switch.add_argument('--hook', required=True)
    switch.add_argument('--processor', required=True, choices=['claude', 'codex'])
    switch.add_argument('--after-seq', type=int, required=True, help='Switch only after a sent receipt covers this inbox seq')
    switch.add_argument('--expected', default=None)
    switch.add_argument('--wait', type=float, default=0, help='Seconds to keep re-checking the gate (0 = check once)')
    switch.add_argument('--no-reload', action='store_true')
    request = sub.add_parser('request-switch', help='Persist a group-only processor switch and start the hidden gate/reload worker')
    request.add_argument('--hook', required=True)
    request.add_argument('--processor', required=True, choices=['claude', 'codex'])
    request.add_argument('--after-seq', type=int, required=True)
    worker = sub.add_parser('switch-worker', help='Maintenance worker for a persisted switch request')
    worker.add_argument('--hook', required=True)
    status = sub.add_parser('switch-status', help='Read back a switch request and whether it is effective')
    status.add_argument('--hook', required=True)
    poll = sub.add_parser("poll", help="Refresh without running agents; baseline does not replay history")
    poll.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    ensure_attendance_engine_importable()
    import xlproject.loadenv  # All live account/environment access uses the project loader.
    from backend.core.messaging.wechat_agent import (
        ATTENDANCE_ACCOUNT, ATTENDANCE_MENTION_ALIASES, TEST_CHAT, PRODUCTION_CHAT, WechatAgentService,
        load_config, save_config, wechat_agent_status, reload_wechat_agent_service_when_idle,
        switch_wechat_hook_processor, request_wechat_hook_processor_switch, run_wechat_hook_processor_switch,
        wechat_hook_processor_switch_status,
    )
    if args.command == "configure":
        from pyxllib.autogui.wechat_accounts import check_account
        name = "考勤后台" if args.chat == "test" else "考勤中台"
        expected = TEST_CHAT if args.chat == "test" else PRODUCTION_CHAT
        check = check_account(ATTENDANCE_ACCOUNT, [name])
        if check["recipients"][name] != expected:
            raise ValueError("Group identity changed; refusing to configure")
        config = save_config({"enabled": args.enabled, "poll_seconds": 5, "accounts": [ATTENDANCE_ACCOUNT],
                              "hooks": [{"key": "attendance-" + args.chat, "name": name, "account_id": ATTENDANCE_ACCOUNT,
                                         "chat_id": expected, "followup_seconds": 0,
                                         "mention_ids": [ATTENDANCE_ACCOUNT],
                                         "mention_aliases": ATTENDANCE_MENTION_ALIASES if args.text_mentions else []}]})
        print(json.dumps(config, ensure_ascii=False))
    elif args.command == "status":
        print(json.dumps(wechat_agent_status(), ensure_ascii=False))
    elif args.command == 'reload-when-idle':
        result = reload_wechat_agent_service_when_idle()
        print(json.dumps({'reloaded':True, 'running':result['running']}, ensure_ascii=False))
    elif args.command == 'request-switch':
        print(json.dumps(request_wechat_hook_processor_switch(args.hook, args.processor, after_seq=args.after_seq),
                         ensure_ascii=False))
    elif args.command == 'switch-worker':
        print(json.dumps(run_wechat_hook_processor_switch(args.hook), ensure_ascii=False))
    elif args.command == 'switch-status':
        print(json.dumps(wechat_hook_processor_switch_status(args.hook), ensure_ascii=False))
    elif args.command == 'switch-processor':
        deadline = time.monotonic() + args.wait
        while True:
            result = switch_wechat_hook_processor(args.hook, args.processor, after_seq=args.after_seq,
                                                  expected_processor=args.expected, reload=not args.no_reload)
            if result['switched'] or time.monotonic() >= deadline:
                break
            time.sleep(5)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "poll":
        service = WechatAgentService(load_config())
        for _ in range(args.samples):
            print(json.dumps(service.poll_once(), ensure_ascii=False), flush=True)
        print(json.dumps(service.status(), ensure_ascii=False), flush=True)
    else:
        config = load_config()
        if not config.get("enabled"):
            raise ValueError("Service disabled; configure --enabled first")
        service = WechatAgentService(config)
        started_at = time.time()
        try:
            service.start()
            print(json.dumps({"started": True}, ensure_ascii=False), flush=True)
            while not service.stop_event.wait(5):
                if service.store.get_meta('stop_requested', 0) >= started_at:
                    break
                # Observable heartbeat; no model call or group message for idle polls.
                service.publish_heartbeat()
        except KeyboardInterrupt:
            pass
        finally:
            service.stop()


if __name__ == "__main__":
    main()
