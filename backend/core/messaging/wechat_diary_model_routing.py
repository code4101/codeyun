"""Conservative, opt-in model selection for the diary writer, not a new agent.

Only short owner experience statements enter the cheaper lane. The same daily
thread, business interfaces and engineering receipts remain authoritative.
Anything outside that envelope keeps the live Codex default; uncertainty can
upgrade a completed lightweight turn before its candidate is sent.
"""
import re

OWNER_ID = "wxid_m1cd4f5aahut22"
COMPLEX = re.compile(
    r"gpt|codex|模型|分流|研究|修[正复]|工程|测试|接口|代码|编程|识别|读取|转发|机器人|"
    r"gui|api|报错|错误|失败|不对|权限|登录|退款|删除|覆盖|批量|取消|停止|医疗|用药|投资|"
    r"怎么|如何|为什么|什么|哪里|哪个|能不能|要不要|能否|是不是|帮[我忙]|"
    r"没保存|没记录|丢失|打不开|不一致|撤回|纠正|检查|[吗么呢?？]", re.I)
EXPERIENCE = re.compile(
    r"记录[：:]|[今昨前]天|[今昨]晚|早上|中午|午餐|晚餐|吃|到了|来[过晚]|去.*馆|"
    r"氛围|感觉|挺多|座位|位置|拍[照了]|散步|逛|电影|收拾|物资|睡|心情|天桥|图书馆|博物馆|写字")


def diary_model_profile(hook: dict, payload: dict) -> dict | None:
    """Return a per-turn override only for an unambiguous fresh diary batch."""
    model = hook.get("routine_model")
    if (not model or hook.get("domain") != "general" or hook.get("kind", "group") != "group"
            or "日记" not in hook.get("description", "") or payload.get("previous_reply_unsent")
            or payload.get("pending_question_seqs") or payload.get("hard_event_seqs")):
        return None
    messages = payload.get("new_messages") or []
    if not messages or len(messages) > 8:
        return None
    text_seen, unexplained_image = False, False
    for message in messages:
        if message.get("sender_id") != OWNER_ID:
            return None
        kind = message.get("message_type")
        if kind in (3, "3"):
            unexplained_image = True
            continue
        if kind not in (1, "1") or message.get("quote"):
            return None
        text = str(message.get("text") or "").strip()
        if not text or len(text) > 300 or COMPLEX.search(text) or not EXPERIENCE.search(text):
            return None
        text_seen = True
        unexplained_image = False
    if not text_seen or unexplained_image:
        return None
    return {"model": model, "effort": hook.get("routine_effort", "low")}


ROUTINE_INSTRUCTION = """工程已将本轮明确的短日记记录交给轻量模型。
仍须使用正式日记接口执行、查重、版本校验和回读；照片仅在后续文字解释后读取。
日记默认 HTML（format_type=html、note_form=document），标题不重复日期。每件事前写 HH:MM，只有时段时明确约（中午约12:00），补发时间不当作事件时间。照片通过上传接口存入 CodeYun，以 img 嵌入自然段，并核验公网实际图片。不创建自定义属性或逐消息小标题。同一天同一次出行更新同一篇，不同日期或独立主题才分篇；分类唯一。消息来源放不展示的 HTML 注释。现有文本格式转换用 /api/notes/{id}/convert-format，版本校验并回读。
仅处理保存事实、感受、照片及其相关补充；不得执行工程修复、GUI、植物识别或其他复杂业务。
原始身份及群范围不变。若有歧义、复杂新补充或工具故障，停止新增操作，
返回requires_default_model=true、action=defer、text为空，summary写已执行事实和需升级原因。
普通日记处理成功时requires_default_model=false。工程会在同一会话升级默认模型并核验已有操作。
群配置 diary_silent=true 时，正常保存与回读成功后 action=ignore、text为空；失败、纠正请求或明确询问仍须处理，不因静默而漏报。
"""
