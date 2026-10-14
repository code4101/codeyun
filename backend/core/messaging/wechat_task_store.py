"""Provider-owned durable task lanes sharing the inbox/outbox transaction.

One input owns one lane. Multi-task directives require clarification rather
than partially executing an ambiguous split. Only messages assigned to the
same task can invalidate its pending answer; other tasks run independently.
"""
from __future__ import annotations
import json
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
            ''')

    def conversation_tasks(self, key: str) -> list[dict]:
        with self.transaction(write=False) as conn:
            return [json.loads(r[0]) for r in conn.execute(
                'SELECT data FROM wechat_tasks WHERE hook_key=? ORDER BY rowid', (key,))]

    def update_conversation_task(self, key: str, task: dict):
        """Update execution progress; caller is the single hook worker."""
        with self.transaction() as conn:
            if not conn.execute('SELECT 1 FROM wechat_tasks WHERE hook_key=? AND id=?',
                                (key, task['id'])).fetchone():
                raise ValueError('Foreign task')
            conn.execute('UPDATE wechat_tasks SET data=? WHERE hook_key=? AND id=?',
                         (json.dumps(task, ensure_ascii=False), key, task['id']))

    def unassigned_task_inputs(self, key: str, *, limit=20) -> list[dict]:
        pending = self.pending_messages(key, self.hook(key)['consumed_seq'], all_lanes=True)
        with self.transaction(write=False) as conn:
            assigned = {r[0] for r in conn.execute(
                'SELECT seq FROM wechat_task_inputs WHERE hook_key=?', (key,))}
        return [m for m in pending if m['seq'] not in assigned][:limit]

    def assign_task_input(self, key: str, message: dict, decision: dict) -> str | None:
        """Atomically publish a model decision and its lane; retries are idempotent."""
        action, task_id = decision['action'], decision.get('task_id')
        if action not in {'ignore', 'new_task', 'continue', 'cancel', 'clarify'}:
            raise ValueError('Invalid task action')
        with self.transaction() as conn:
            previous = conn.execute('SELECT task_id FROM wechat_task_inputs WHERE hook_key=? AND seq=?',
                                    (key, message['seq'])).fetchone()
            if previous:
                return previous[0]
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            source = conn.execute('SELECT data FROM inbox WHERE seq=? AND account_id=? AND chat_id=? AND sender_id<>?',
                                  (message['seq'], hook['account_id'], hook['chat_id'], hook['account_id'])).fetchone()
            if not source or conn.execute('SELECT 1 FROM resolved_questions WHERE hook_key=? AND seq=?',
                                          (key, message['seq'])).fetchone():
                raise ValueError('Unavailable task source')
            if action in {'new_task', 'clarify'}:
                task_id = uuid.uuid4().hex
                original = json.loads(source[0])
                task = {'id': task_id, 'title': str(original.get('text') or '')[:160],
                        'owner_id': original['sender_id'], 'status': 'queued',
                        'kind': action, 'created_at': time.time(), 'request': None, 'session_id': None}
                conn.execute('INSERT INTO wechat_tasks VALUES(?,?,?)',
                             (task_id, key, json.dumps(task, ensure_ascii=False)))
            elif action in {'continue', 'cancel'}:
                row = conn.execute('SELECT data FROM wechat_tasks WHERE hook_key=? AND id=?', (key, task_id)).fetchone()
                if not row or json.loads(row[0])['kind'] == 'clarify':
                    raise ValueError('Unknown task reference')
            elif task_id is not None:
                raise ValueError('Ignored input cannot reference a task')
            conn.execute('INSERT INTO wechat_task_inputs VALUES(?,?,?,?)',
                         (key, message['seq'], task_id, json.dumps(decision, ensure_ascii=False)))
            conn.execute("INSERT OR IGNORE INTO message_routes VALUES(?,?,'main',NULL,?)", (key, message['seq'], time.time()))
            if action == 'ignore':
                conn.execute('INSERT OR IGNORE INTO resolved_questions VALUES(?,?,NULL)', (key, message['seq']))
                self._advance_resolved_cursor(conn, key)
            return task_id

    def task_inputs(self, key: str, task_id: str) -> list[dict]:
        with self.transaction(write=False) as conn:
            return [{**json.loads(r['data']), 'seq': r['seq'], 'intake': json.loads(r['decision'])}
                    for r in conn.execute('SELECT i.seq,i.data,t.decision FROM inbox i '
                        'JOIN wechat_task_inputs t ON t.seq=i.seq AND t.hook_key=? '
                        'WHERE t.task_id=? AND NOT EXISTS(SELECT 1 FROM resolved_questions r '
                        'WHERE r.hook_key=t.hook_key AND r.seq=t.seq) ORDER BY i.seq', (key, task_id))]

    def reserve_task_reply(self, key: str, task_id: str, seqs: list[int], text: str) -> int | None:
        """No stale task correction or unclassified input may race a visible reply."""
        if not seqs or len(set(seqs)) != len(seqs):
            raise ValueError('Invalid task reply sources')
        with self.transaction() as conn:
            hook = conn.execute('SELECT * FROM hooks WHERE key=?', (key,)).fetchone()
            if conn.execute('SELECT 1 FROM inbox i WHERE i.account_id=? AND i.chat_id=? AND i.sender_id<>? '
                'AND i.seq>? AND NOT EXISTS(SELECT 1 FROM wechat_task_inputs t WHERE t.hook_key=? AND t.seq=i.seq) '
                'AND NOT EXISTS(SELECT 1 FROM resolved_questions r WHERE r.hook_key=? AND r.seq=i.seq)',
                (hook['account_id'], hook['chat_id'], hook['account_id'], hook['consumed_seq'], key, key)).fetchone():
                return None
            pending = {r[0] for r in conn.execute('SELECT t.seq FROM wechat_task_inputs t WHERE hook_key=? AND task_id=? '
                'AND NOT EXISTS(SELECT 1 FROM resolved_questions r WHERE r.hook_key=t.hook_key AND r.seq=t.seq)', (key, task_id))}
            if pending != set(seqs) or conn.execute("SELECT 1 FROM outbox WHERE hook_key=? AND status IN ('sending','uncertain')", (key,)).fetchone():
                return None
            cursor = conn.execute("INSERT OR IGNORE INTO outbox(hook_key,through_seq,text,status,created_at) VALUES(?,?,?,'sending',?)",
                                  (key, max(seqs), text, time.time()))
            return cursor.lastrowid if cursor.rowcount else None
