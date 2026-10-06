"""Separate routine operational bookkeeping from the user's task answer.

The transport owns removal and durable recording, instead of relying on the
agent to remember an apology. Required approval/error disclosures remain in
ordinary reply text; they must never be classified as routine bookkeeping.
"""
import json

OPS_MARKER = 'CODECLAW_OPS_NOTE: '
OPERATIONAL_REPLY_INSTRUCTIONS = '''用户答复只包含当前交办的结果、必要限制及下一步；不重复历史运维通知。
与当前结果无关的临时产物、清理待办和其他非阻断运维事项记录到内部运维通道，不占用用户答复。
需要同时提供内部记录时，在正常答复后单独写 CODECLAW_OPS_NOTE: {"category":"routine_housekeeping","message":"内部记录正文"}，工程会移除该指令并持久化去重。
仅有内部运维事项时不要生成用户回复。用户主动询问该事项时正常回答，不使用内部指令隐藏答案。
影响当前目标的失败、风险、必须披露的权限或自动审查阻断仍直接说明，禁止归入 routine_housekeeping。'''


def extract_operational_notes(text: str) -> tuple[str, list[dict]]:
    """Extract explicit nonblocking housekeeping directives outside code fences.

    Arbitrary prose is never censored. Current failures affecting the task and
    required approval disclosures cannot use this internal-only channel.
    """
    lines, notes, fenced = [], [], False
    for line in text.splitlines():
        if line.lstrip().startswith('```'):
            fenced = not fenced
        if not fenced and line.startswith(OPS_MARKER):
            note = json.loads(line[len(OPS_MARKER):])
            if (not isinstance(note, dict) or set(note) != {'category', 'message'}
                    or note['category'] != 'routine_housekeeping'
                    or not isinstance(note['message'], str) or not note['message'].strip()
                    or len(note['message']) > 2000):
                raise ValueError('Only nonblocking routine housekeeping can be archived')
            notes.append(note)
        else:
            lines.append(line)
    return '\n'.join(lines).strip(), notes
