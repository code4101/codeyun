"""Public incident CLI: dispatch a JSON request, inspect, or explicitly resume."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.agents.cli import bootstrap
from backend.core.agents.policy import load_config


def main():
    bootstrap(load_config())
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    launch = sub.add_parser('dispatch')
    launch.add_argument('request_file')
    status = sub.add_parser('status')
    status.add_argument('dispatch_id')
    verify = sub.add_parser('verify', help='Recheck persisted CC business evidence without a model call')
    verify.add_argument('dispatch_id')
    resume = sub.add_parser('resume')
    resume.add_argument('dispatch_id')
    resume.add_argument('--prompt-file', required=True)
    resume.add_argument('--mode', default=None,
                        help='Explicitly change the next generation mode (repair/read_only_research/maintenance/smoke); default keeps it')
    args = parser.parse_args()
    from backend.core.agents.contracts import AgentTaskRequest
    from backend.core.agents.dispatch import dispatch_agent_task, inspect_agent_dispatch, resume_agent_dispatch
    if args.command == 'dispatch':
        result = dispatch_agent_task(AgentTaskRequest(**json.loads(Path(args.request_file).read_text(encoding='utf-8-sig'))))
    elif args.command == 'resume':
        result = resume_agent_dispatch(args.dispatch_id, Path(args.prompt_file).read_text(encoding='utf-8-sig'),
                                       mode=args.mode)
    elif args.command == 'verify':
        from backend.core.agents.background import verify_claude_acceptance
        result = verify_claude_acceptance(args.dispatch_id)
    else:
        result = inspect_agent_dispatch(args.dispatch_id)
    print(json.dumps(result.model_dump(), ensure_ascii=True))


if __name__ == '__main__':
    main()
