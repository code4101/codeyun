"""Preserve an unsent answer across unrelated steering within one native turn."""
import json


def merge_structured_final_text(previous: str, current: str) -> str:
    """Ignore without addressed questions cannot erase a prior reply candidate.

    Explicitly addressed ignore, defer and replacement reply stay authoritative.
    Invalid JSON remains the latest output so normal terminal validation fails;
    this projection neither sends nor acknowledges any source message.
    """
    if not current:
        return previous
    try:
        old, new = json.loads(previous), json.loads(current)
    except (ValueError, TypeError):
        return current
    if (isinstance(old, dict) and isinstance(new, dict)
            and old.get('action') == 'reply' and new.get('action') == 'ignore'
            and new.get('question_seqs') == []):
        return json.dumps({**old, 'summary': new.get('summary', old.get('summary', ''))},
                          ensure_ascii=False)
    return current
