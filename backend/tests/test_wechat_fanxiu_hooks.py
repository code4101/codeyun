"""Group domain and context survive daily WeChat agent routing."""
import json

import pytest

from backend.tests.test_wechat_agent import HOOK, CandidateClient, ingest, message, service, store


def test_account_rename_keeps_native_mentions_and_exact_text_aliases():
    from backend.core.messaging.wechat_agent import ATTENDANCE_MENTION_ALIASES, is_hard
    hook = {**HOOK, "mention_aliases": ATTENDANCE_MENTION_ALIASES}
    assert is_hard(message(1, "@代号4102\u2005请查"), hook)
    assert is_hard(message(2, "@考勤返款 请查"), hook)
    assert is_hard(message(3, "@群昵称 请查", mentions=[HOOK["account_id"]]), hook)
    assert not is_hard(message(4, "@代号4102其他人 请查"), hook)
    assert not is_hard(message(5, "他说 @代号4102 请查"), hook)
    assert not is_hard(message(6, "@代号4102 请查", sender=HOOK["account_id"]), hook)


def test_fanxiu_group_identity_reaches_agent(store):
    hook = {**HOOK, "domain": "fanxiu", "name": "三清道宗", "description": "凡修联盟群"}
    ingest(store, message(1, "@考勤返款 联盟活动什么时候开始？"))

    class Client(CandidateClient):
        def open_thread(self, thread_id, title):
            assert title.startswith("凡修群 三清道宗 ")
            return super().open_thread(thread_id, title)

    client = Client()
    assert service(store, sender=lambda *a, **k: {}).process_hook(hook, client)
    assert client.prompts[0]["group_context"] == {
        "name": "三清道宗", "domain": "fanxiu", "description": "凡修联盟群"}


def test_domain_configuration_preserves_other_hooks(tmp_path, monkeypatch):
    from backend.core.messaging import wechat_agent as module
    monkeypatch.setattr(module, "agent_root", lambda: tmp_path)
    fanxiu = {**HOOK, "key": "fanxiu", "domain": "fanxiu", "chat_id": "456@chatroom"}
    config = {"accounts": [HOOK["account_id"]], "hooks": [HOOK, fanxiu]}
    module.save_config(config)
    assert module.load_config()["hooks"] == [HOOK, fanxiu]
    fanxiu["domain"] = "unknown"
    with pytest.raises(ValueError, match="domain"):
        module.save_config(config)
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["hooks"][1]["domain"] == "fanxiu"
