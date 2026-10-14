"""Provider-owned durable task lanes sharing the inbox/outbox transaction.

One input may own several scoped lanes. Its global resolution waits for every
lane, while each lane has its own delivery identity and correction barrier.
"""
from __future__ import annotations
import json
import hashlib
import re
import time
import uuid


class WechatTaskStore:
    def initialize_task_store(self):
        with self.transaction() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS wechat_tasks(
                    id TEXT PRIMARY KEY, hook_key TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wechat_task_inputs(
                    hook_key TEXT NOT NULL, seq INTEGER NOT NULL, task_id TEXT,
                    decision TEXT NOT NULL, PRIMARY KEY(hook_key,seq));
                CREATE INDEX IF NOT EXISTS wechat_tasks_hook ON wechat_tasks(hook_key);
                CREATE INDEX IF NOT EXISTS wechat_task_inputs_task ON wechat_task_inputs(hook_key,task_id,seq);
                CREATE TABLE IF NOT EXISTS wechat_task_links(
                    hook_key TEXT NOT NULL, seq INTEGER NOT NULL, slot INTEGER NOT NULL,
                    task_id TEXT NOT NULL, decision TEXT NOT NULL, scoped_text TEXT NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0, reply_id INTEGER,
                    PRIMARY KEY(hook_key,seq,slot));
                CREATE INDEX IF NOT EXISTS wechat_task_links_task ON wechat_task_links(hook_key,task_id,resolved,seq);
                CREATE TABLE IF NOT EXISTS wechat_task_outbox(
                    reply_id INTEGER PRIMARY KEY, hook_key TEXT NOT NULL, task_id TEXT NOT NULL,
                    source_seqs TEXT NOT NULL);
            ''')
        with self.transaction() as conn:
            # Preserve legacy IDs/receipts atomically. Ordinary writers retain
            # their old uniqueness; task writers add an explicit scoped key.
            if 'delivery_key' not in {r['name'] for r in conn.execute('PRAGMA table_info(outbox)')}:
                conn.execute('CREATE TABLE outbox_scoped(id INTEGER PRIMARY KEY AUTOINCREMENT, '
                    'hook_key TEXT NOT NULL, through_seq INTEGER NOT NULL, text TEXT NOT NULL, '
                    'status TEXT NOT NULL, created_at REAL NOT NULL, result TEXT, delivery_key TEXT)')
                conn.execute('INSERT INTO outbox_scoped(id,hook_key,through_seq,text,status,created_at,result) '
                             'SELECT id,hook_key,through_seq,text,status,created_at,result FROM outbox')
                conn.execute('DROP TABLE outbox')
                conn.execute('ALTER TABLE outbox_scoped RENAME TO outbox')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS outbox_ordinary_identity ON outbox(hook_key,through_seq) WHERE delivery_key IS NULL')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS outbox_task_identity ON outbox(hook_key,delivery_key) WHERE delivery_key IS NOT NULL')
            conn.execute('INSERT OR IGNORE INTO wechat_task_links(hook_key,seq,slot,task_id,decision,scoped_text,resolved,reply_id) '
                "SELECT t.hook_key,t.seq,0,t.task_id,t.decision,COALESCE(json_extract(i.data,'$.text'),''),"
                'CASE WHEN r.seq IS NULL THEN 0 ELSE 1 END,r.reply_id FROM wechat_task_inputs t '
                'JOIN inbox i ON i.seq=t.seq LEFT JOIN resolved_questions r ON r.hook_key=t.hook_key AND r.seq=t.seq '
                'WHERE t.task_id IS NOT NULL')

    def conversation_tasks(self, key: str, *, recent_limit: int = 32) -> list[dict]:
        """Retain every unfinished lane plus a bounded recent history.

        Old completed tasks stay durable. A caller inspecting history can raise
        recent_limit; worker cost no longer grows with every completed task.
        """
        if type(recent_limit) is not int or not 1 <= recent_limit <= 10000:
            raise ValueError('Invalid task history limit')
        with self.transaction(write=False) as conn:
            return [json.loads(r[0]) for r in conn.execute(
                'SELECT t.data FROM wechat_tasks t WHERE t.hook_key=? AND ('
                "json_extract(t.data,'$.request') IS NOT NULL OR "
                'EXISTS(SELECT 1 FROM wechat_task_links i WHERE i.hook_key=t.hook_key AND i.task_id=t.id AND i.resolved=0 '
                'AND NOT EXISTS(SELECT 1 FROM resolved_questions r WHERE r.hook_key=i.hook_key AND r.seq=i.seq)) OR '
                't.id IN (SELECT id FROM wechat_tasks WHERE hook_key=? ORDER BY rowid DESC LIMIT ?)) ORDER BY t.rowid',
                (key, key, recent_limit))]

    def conversation_task_reference(self, key: str, reference: str) -> dict | None:
        """Resolve a visible short reference within this group, including archives.

        A prefix collision is ambiguous, never permission to choose a task.
        """
        if not re.fullmatch(r'[0-9a-f]{8,32}', reference):
            raise ValueError('Invalid task reference')
        with self.transaction(write=False) as conn:
            rows = conn.execute('SELECT data FROM wechat_tasks WHERE hook_key=? AND id LIKE ? LIMIT 2',
                                (key, reference + '%')).fetchall()
            return json.loads(rows[0][0]) if len(rows) == 1 else None

    def update_conversation_task(self, key: str, task: dict):
        """Update execution progress; caller is the single hook worker."""
        with self.transaction() as conn:
            if not conn.execute('SELECT 1 FROM wechat_tasks WHERE hook_key=? AND id=?',
                                (key, task['id'])).fetchone():
                raise ValueError('Foreign task')
            conn.execute('UPDATE wechat_tasks SET data=? WHERE hook_key=? AND id=?',
                         (json.dumps(task, ensure_ascii=False), key, task['id']))

    def unassigned_task_inputs(self, key: str, *, limit=20) -> list[dict]:
        """Filter ownership before pagination, so a busy backlog cannot starve intake."""
        hook = self.hook(key)
        age_filter, age_args = self._message_age_filter(key)
        with self.transaction(write=False) as conn:
            rows = conn.execute('SELECT i.seq,i.data FROM inbox i WHERE i.account_id=? AND i.chat_id=? '
                'AND i.sender_id<>? AND i.seq>? AND NOT EXISTS('
                'SELECT 1 FROM wechat_task_inputs t WHERE t.hook_key=? AND t.seq=i.seq) AND NOT EXISTS('
                'SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq)' + age_filter +
                ' ORDER BY i.seq LIMIT ?',
                (hook['account_id'], hook['chat_id'], hook['account_id'], hook['consumed_seq'], key, key, *age_args, limit)).fetchall()
            return [{**json.loads(r['data']), 'seq':r['seq']} for r in rows]

    def assign_task_input(self, key: str, message: dict, decision: dict) -> str | None:
        """Compatibility entry point for a single decision."""
        ids = self.assign_task_plan(key, message, [decision])
        return ids[0] if ids else None

    def assign_task_plan(self, key: str, message: dict, decisions: list[dict]) -> list[str]:
        """Atomically assign every literal clause, or none; retries return original IDs."""
        if not decisions or len(decisions) > 8:
            raise ValueError('Invalid task plan')
        actions = [d.get('action') for d in decisions]
        if any(a not in {'ignore','new_task','continue','cancel','clarify'} for a in actions):
            raise ValueError('Invalid task action')
        if len(decisions) > 1 and any(a in {'ignore','clarify'} for a in actions):
            raise ValueError('Ambiguous plan cannot partly execute')
        references = [d.get('task_id') for d in decisions if d['action'] in {'continue','cancel'}]
        if len(set(references)) != len(references):
            raise ValueError('Duplicate task directive')
        new_scopes = [d.get('text',message.get('text')) for d in decisions if d['action']=='new_task']
        if len(new_scopes) != len(set(new_scopes)):
            raise ValueError('Duplicate new task scope')
        with self.transaction() as conn:
            previous = conn.execute('SELECT task_id FROM wechat_task_inputs WHERE hook_key=? AND seq=?',
                                    (key, message['seq'])).fetchone()
            if previous:
                return [r[0] for r in conn.execute('SELECT task_id FROM wechat_task_links WHERE hook_key=? AND seq=? ORDER BY slot',
                                                  (key,message['seq']))]
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            source = conn.execute('SELECT data FROM inbox WHERE seq=? AND account_id=? AND chat_id=? AND sender_id<>?',
                                  (message['seq'], hook['account_id'], hook['chat_id'], hook['account_id'])).fetchone()
            if not source or conn.execute('SELECT 1 FROM resolved_questions WHERE hook_key=? AND seq=?',
                                          (key, message['seq'])).fetchone():
                raise ValueError('Unavailable task source')
            original = json.loads(source[0])
            from backend.core.messaging.wechat_agent import model_message
            visible = model_message(original)['text']
            ids = []
            for slot, decision in enumerate(decisions):
                action, task_id = decision['action'], decision.get('task_id')
                scoped = decision.get('text', visible)
                if (not isinstance(scoped,str) or scoped not in visible
                        or (not scoped.strip() and action not in {'ignore','clarify'})):
                    raise ValueError('Task scope must be literal source text')
                if action in {'new_task','clarify'}:
                    if task_id is not None:
                        raise ValueError('New task cannot reference an existing task')
                    task_id = uuid.uuid4().hex
                    task = {'id':task_id,'title':scoped[:160], 'owner_id':original['sender_id'],
                            'owner_name':original.get('sender_name'),'status':'queued','kind':action,
                            'created_at':time.time(),'request':None,'session_id':None}
                    conn.execute('INSERT INTO wechat_tasks VALUES(?,?,?)',(task_id,key,json.dumps(task,ensure_ascii=False)))
                elif action in {'continue','cancel'}:
                    row = conn.execute('SELECT data FROM wechat_tasks WHERE hook_key=? AND id=?',(key,task_id)).fetchone()
                    if not row or json.loads(row[0])['kind'] == 'clarify':
                        raise ValueError('Unknown task reference')
                    task = json.loads(row[0])
                    if task['status'] == 'completed':
                        task['status'] = 'queued'
                        conn.execute('UPDATE wechat_tasks SET data=? WHERE hook_key=? AND id=?',
                                     (json.dumps(task,ensure_ascii=False),key,task_id))
                elif task_id is not None:
                    raise ValueError('Ignored input cannot reference a task')
                if action != 'ignore':
                    ids.append(task_id)
                    conn.execute('INSERT INTO wechat_task_links(hook_key,seq,slot,task_id,decision,scoped_text) VALUES(?,?,?,?,?,?)',
                                 (key,message['seq'],slot,task_id,json.dumps(decision,ensure_ascii=False),scoped))
            parent = decisions[0] if len(decisions)==1 else {'action':'batch','actions':decisions}
            conn.execute('INSERT INTO wechat_task_inputs VALUES(?,?,?,?)',
                         (key, message['seq'], ids[0] if len(ids)==1 else None, json.dumps(parent, ensure_ascii=False)))
            conn.execute("INSERT OR IGNORE INTO message_routes VALUES(?,?,'main',NULL,?)", (key, message['seq'], time.time()))
            if actions == ['ignore']:
                conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,NULL)', (key, message['seq']))
                self._advance_resolved_cursor(conn, key)
            return ids

    def task_inputs(self, key: str, task_id: str) -> list[dict]:
        with self.transaction(write=False) as conn:
            return [{**json.loads(r['data']), 'seq': r['seq'], 'intake': json.loads(r['decision']), 'assigned_text':r['scoped_text']}
                    for r in conn.execute('SELECT i.seq,i.data,t.decision,t.scoped_text FROM inbox i '
                        'JOIN wechat_task_links t ON t.seq=i.seq AND t.hook_key=? '
                        'WHERE t.task_id=? AND t.resolved=0 AND NOT EXISTS(SELECT 1 FROM resolved_questions r '
                        'WHERE r.hook_key=t.hook_key AND r.seq=t.seq) ORDER BY i.seq', (key, task_id))]

    def reserve_task_reply(self, key: str, task_id: str, seqs: list[int], text: str,
                           *, reviewed_seqs: list[int] | None = None) -> int | None:
        """No stale task correction or unclassified input may race a visible reply."""
        if not seqs or len(set(seqs)) != len(seqs):
            raise ValueError('Invalid task reply sources')
        age_filter,age_args=self._message_age_filter(key)
        with self.transaction() as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            if conn.execute('SELECT 1 FROM inbox i WHERE i.account_id=? AND i.chat_id=? AND i.sender_id<>? '
                'AND i.seq>? AND NOT EXISTS(SELECT 1 FROM wechat_task_inputs t WHERE t.hook_key=? AND t.seq=i.seq) '
                'AND NOT EXISTS(SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq)' + age_filter,
                (hook['account_id'], hook['chat_id'], hook['account_id'], hook['consumed_seq'], key, key,*age_args)).fetchone():
                return None
            pending = {r[0] for r in conn.execute('SELECT t.seq FROM wechat_task_links t WHERE hook_key=? AND task_id=? AND t.resolved=0 '
                'AND NOT EXISTS(SELECT 1 FROM resolved_questions r WHERE r.hook_key=t.hook_key AND r.seq=t.seq)', (key, task_id))}
            reviewed = set(seqs if reviewed_seqs is None else reviewed_seqs)
            if pending != reviewed or not set(seqs) <= reviewed or conn.execute("SELECT 1 FROM outbox WHERE hook_key=? AND status IN ('sending','uncertain')", (key,)).fetchone():
                return None
            identity = 'task:' + task_id + ':' + hashlib.sha256(json.dumps(sorted(seqs)).encode()).hexdigest()
            # Legacy unscoped receipt still owns this old source after migration.
            if conn.execute('SELECT 1 FROM outbox WHERE hook_key=? AND through_seq=? AND delivery_key IS NULL',
                            (key,max(seqs))).fetchone():
                return None
            cursor = conn.execute("INSERT OR IGNORE INTO outbox(hook_key,through_seq,text,status,created_at,delivery_key) VALUES(?,?,?,'sending',?,?)",
                                  (key, max(seqs), text, time.time(), identity))
            if not cursor.rowcount:
                return None
            conn.execute('INSERT INTO wechat_task_outbox VALUES(?,?,?,?)',
                         (cursor.lastrowid,key,task_id,json.dumps(seqs)))
            return cursor.lastrowid

    def resolve_task_inputs(self, key: str, task_id: str, seqs: list[int]):
        """Resolve this lane's no-reply decision without consuming sibling lanes."""
        with self.transaction() as conn:
            self._resolve_task_links(conn,key,task_id,seqs,None)

    def _resolve_task_links(self, conn, key, task_id, seqs, reply_id):
        for seq in seqs:
            if not conn.execute('SELECT 1 FROM wechat_task_links WHERE hook_key=? AND task_id=? AND seq=?',
                                (key,task_id,seq)).fetchone():
                raise ValueError('Foreign task resolution')
            conn.execute('UPDATE wechat_task_links SET resolved=1,reply_id=COALESCE(reply_id,?) '
                         'WHERE hook_key=? AND task_id=? AND seq=?',(reply_id,key,task_id,seq))
            if not conn.execute('SELECT 1 FROM wechat_task_links WHERE hook_key=? AND seq=? AND resolved=0',(key,seq)).fetchone():
                receipt = conn.execute('SELECT reply_id FROM wechat_task_links WHERE hook_key=? AND seq=? AND reply_id IS NOT NULL LIMIT 1',
                                       (key,seq)).fetchone()
                conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,?)',(key,seq,receipt[0] if receipt else None))
        self._advance_resolved_cursor(conn,key)

    def _resolve_task_delivery(self, conn, row, groups=None):
        receipt = conn.execute('SELECT * FROM wechat_task_outbox WHERE reply_id=?',(row['id'],)).fetchone()
        if not receipt:
            return False
        seqs = json.loads(receipt['source_seqs'])
        if groups is not None:
            covered = {s for g in groups for s in g.get('question_seqs',[])}
            seqs = [s for s in seqs if s in covered]
        self._resolve_task_links(conn,row['hook_key'],receipt['task_id'],seqs,row['id'])
        return True
