"""Owner @ requests admit new groups; ordinary conversation never calls a model.

The creation message is durable input for the normal group worker. Config keeps
its identity so a crash between saving config and making the hook cannot lose
that request or replay older group history. No separate reply sender is used.
"""
import re


def registration_scope(event: dict, account_id: str) -> str | None:
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT, ATTENDANCE_MENTION_ALIASES, is_hard
    if (event.get('sender_id') != OWNER_ACCOUNT or not re.fullmatch(r'\d+@chatroom', event.get('chat_id', ''))
            or event.get('message_type') not in (1, '1')):
        return None
    hook = {'account_id':account_id, 'mention_aliases':ATTENDANCE_MENTION_ALIASES}
    if not is_hard(event, hook):
        return None
    text = str(event.get('text') or '')
    if text.startswith(OWNER_ACCOUNT + ':\n'):
        text = text[len(OWNER_ACCOUNT)+2:]
    text = re.sub(r'^@[^\s，,:：]+[\s，,:：]*', '', text).strip()
    match = re.match(r'^(?:请|帮我|麻烦)?\s*(?:创建|建立|接入|绑定|开通|启用)\s*(?:这个群|本群|此群|该群|群聊|群)'
                     r'.{0,20}?(?:机器人|自动处理)(?:功能|能力)?[\s，,。:：；;]*(.*)$', text, re.S)
    if not match or len(match[1].strip()) < 4:
        return None  # Scope must come from the owner's text, never an attachment.
    return match[1].strip()[:2000]


def register_group(event: dict, account_id: str, source, store) -> dict | None:
    """Validate real sender/group, then idempotently persist one scoped hook."""
    from backend.core.messaging.wechat_agent import (load_config, save_config, OWNER_ACCOUNT,
        ATTENDANCE_ACCOUNT, ATTENDANCE_MENTION_ALIASES)
    scope = registration_scope(event, account_id)
    if account_id != ATTENDANCE_ACCOUNT or scope is None:
        return None
    config = load_config()
    existing = next((h for h in config.get('hooks', []) if h['account_id']==account_id and h['chat_id']==event['chat_id']), None)
    if existing:
        if existing.get('registration_message_id'):
            store.ensure_hook(existing['key'], account_id, existing['chat_id'],
                              start_message_id=existing['registration_message_id'])
        return {'created':False, 'hook':existing}
    name = None
    for offset in range(0, 100000, 500):
        rows = source.list_chats(limit=500, offset=offset)
        found = next((r for r in rows if r.get('username')==event['chat_id']), None)
        if found:
            name = found.get('name') or event['chat_id']
            break
        if len(rows)<500:
            break
    if name is None:
        raise ValueError('Creation request group is not visible to the robot')
    hook = {'key':'group-'+event['chat_id'].split('@')[0], 'account_id':account_id,
        'chat_id':event['chat_id'], 'name':name, 'domain':'general',
        'description':'本群业务范围（由核验的项目所有者指定）：'+scope+
            '。普通成员可提出本群问题；工程操作及扩大范围仍需项目所有者交办。',
        'priority_sender_ids':[OWNER_ACCOUNT], 'mention_ids':[account_id],
        'mention_aliases':ATTENDANCE_MENTION_ALIASES, 'followup_seconds':0,
        'message_routing':True,
        'registration_message_id':event['message_id']}
    save_config({**config, 'hooks':[*config.get('hooks', []), hook]}, expected_config=config)
    store.ensure_hook(hook['key'], account_id, hook['chat_id'], start_message_id=event['message_id'])
    return {'created':True, 'hook':hook}


def process_group_registrations(service, account_id: str) -> list[str]:
    """Scan durable new owner messages; publication failures do not advance admission."""
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    name = 'group_registration_cursor:' + account_id
    through = service.store.latest_sequence(account_id)
    after = service.store.get_meta(name, through)
    created = []
    while True:
        rows = service.store.owner_messages(account_id, OWNER_ACCOUNT, after)
        for event in rows:
            result = register_group(event, account_id, service.source(account_id), service.store)
            if result and result['hook']['key'] not in {h['key'] for h in service.config.get('hooks', [])}:
                created.append(result['hook']['key'])
                service.store.set_meta('group_registration_reload_required', True)
            after = event['seq']
            service.store.set_meta(name, after)
        if len(rows)<100:
            service.store.set_meta(name, through)
            break
    if service.store.get_meta('group_registration_reload_required') and service.workers:
        from backend.core.messaging.wechat_agent import request_wechat_agent_reload
        request_wechat_agent_reload()
        service.store.set_meta('group_registration_reload_required', False)
    return created
