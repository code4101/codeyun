"""Connect the existing Your dot to the future-community group.

connect-current uses the account's selected dot and existing desktop login.
It installs no plugin and grants no new permission. status reports the binding.

The optional legacy webhook transport pairs in ChatGPT Plugins:

Pair in ChatGPT Plugins -> Add custom MCP server, OAuth, URL:
https://code4101.com/api/wechat-dots/mcp
Then tell the dot:
订阅 CodeYun 的 wechat.task.updated 事件，hook_key=future-community。
收到事件先 read_wechat_task；按最新任务处理，用 submit_wechat_reply 提交，
读取送达结果。平时静默，图片等后续人工文字说明，不把群内容写入私有日记。

configure enables pairing but does not pretend a cloud subscription exists.
Before pairing the existing Codex writer remains active. Pending Dots tasks
are durable, and expiry never grants a second writer those same tasks.
"""
from pathlib import Path
import sys
import argparse
import json

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command',choices=['configure','status','probe','connect-current'])
    args=parser.parse_args()
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv
    from backend.core.messaging.wechat_agent import load_config,save_config,request_wechat_agent_reload
    from backend.core.messaging.wechat_dots import bridge_store
    if args.command=='configure':
        config=load_config()
        hook=next(h for h in config['hooks'] if h['key']=='future-community')
        if hook['chat_id']!='44812733824@chatroom' or hook.get('router_mode')!='flash_mentions':
            raise ValueError('Future community identity/routing mismatch')
        hook['processor']='dots'
        save_config(config)
        print(json.dumps({'configured':True,'subscription_active':bool(bridge_store().subscriptions(hook['key'])),
                          'reload':request_wechat_agent_reload()},ensure_ascii=False))
    elif args.command=='connect-current':
        from backend.core.codex.dot import CurrentDotClient
        hook=next(h for h in load_config()['hooks'] if h['key']=='future-community')
        binding=bridge_store().bind_existing_dot(hook,CurrentDotClient())
        print(json.dumps({'connected':True,'thread_id':binding['thread_id'],
                          'reload':request_wechat_agent_reload()},ensure_ascii=False))
    elif args.command=='status':
        print(json.dumps(bridge_store().status(),ensure_ascii=False))
    else:
        import requests
        for base in ('http://127.0.0.1:8000','https://code4101.com'):
            response=requests.post(base+'/api/wechat-dots/mcp',json={'jsonrpc':'2.0','id':1,'method':'server/discover'},timeout=15)
            challenge=response.headers.get('WWW-Authenticate','')
            print(json.dumps({'base':base,'status':response.status_code,'auth_challenge':challenge},ensure_ascii=False))


if __name__=='__main__':
    main()
