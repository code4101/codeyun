"""Subscribe a verified engineering group to one Codex thread's final results.

record reads native Stop JSON from stdin and only enqueues it. Sending always
belongs to the existing WeChat engineering service, including its receipts.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    subscribe = commands.add_parser('subscribe')
    subscribe.add_argument('thread_id')
    subscribe.add_argument('--account', required=True)
    subscribe.add_argument('--chat', required=True)
    subscribe.add_argument('--include-latest', action='store_true')
    subscribe.add_argument('--allow-images', action='store_true', help='Allow verified question screenshots in TEMP/codeyun/fanxiu-question-evidence')
    subscribe.add_argument('--disable', action='store_true')
    commands.add_parser('record')
    commands.add_parser('status')
    args = parser.parse_args()
    import xlproject.loadenv
    from backend.core.messaging.codex_results import subscribe_codex_results, record_codex_turn_result, result_store
    if args.command == 'subscribe':
        result = subscribe_codex_results(args.thread_id, args.account, args.chat,
                                         enabled=not args.disable, include_latest=args.include_latest,
                                         allow_images=args.allow_images)
    elif args.command == 'record':
        result = {'queued': record_codex_turn_result(json.load(sys.stdin))}
    else:
        store = result_store()
        result = store.codex_notifications_status()
        for notification in result['notifications']:
            notification['verification'] = store.get_meta(f"codex_result_verification:{notification['id']}")
        result['worker'] = store.get_meta('codex_results_worker')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
