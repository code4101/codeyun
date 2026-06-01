"""Prompt variants for comparing WeChat daily note summary styles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeChatSummaryVariant:
    key: str
    name: str
    source_hint: str
    prompt: str


VARIANTS: list[WeChatSummaryVariant] = [
    WeChatSummaryVariant(
        "topic_digest",
        "话题精华",
        "参考微信群精华总结模板：话题概要、重要提醒、待跟进事项。",
        "摘要先用一句话说清事件；笔记按主要话题组织，每个话题写事实、结论、后续动作，避免时间流水账。",
    ),
    WeChatSummaryVariant(
        "trigger_diagnosis_action",
        "触发-诊断-动作",
        "适配个人故障排查聊天。",
        "摘要固定为触发、排查、判断、安排；笔记每条用“事实状态 -> 判断 -> 可复用动作”。",
    ),
    WeChatSummaryVariant(
        "incident_review",
        "事件复盘",
        "参考运维事故复盘格式。",
        "把聊天当作一次小事件复盘：影响、现象、排查、根因假设、临时绕过、后续验证。",
    ),
    WeChatSummaryVariant(
        "troubleshooting_runbook",
        "排障手册",
        "适合 Clash/网络/账号类问题。",
        "笔记优先生成可复用排障手册：适用场景、判断规则、步骤、回退方案、误区。",
    ),
    WeChatSummaryVariant(
        "fact_then_rule",
        "事实到规则",
        "针对用户反馈：事实与知识互补。",
        "每条笔记先写已发生事实，再抽象出规则；不得把已买、已测、已失败改成泛化建议。",
    ),
    WeChatSummaryVariant(
        "decision_log",
        "决策日志",
        "参考决策记录 ADR 思路。",
        "突出当天做出的选择：为什么不继续原方案、为什么换订阅/节点、保留哪些备选。",
    ),
    WeChatSummaryVariant(
        "knowledge_card",
        "知识卡片",
        "参考知识库卡片组织。",
        "笔记以可检索知识卡片为主，但每张卡片必须带一条当天事实锚点，避免空泛知识化。",
    ),
    WeChatSummaryVariant(
        "support_ticket",
        "客服工单",
        "参考支持工单摘要。",
        "把聊天整理成问题描述、已尝试操作、当前状态、解决方案、待客户验证。",
    ),
    WeChatSummaryVariant(
        "meeting_minutes",
        "会议纪要",
        "参考会议纪要：议题、结论、行动项。",
        "用会议纪要风格：议题、共识/判断、行动项；只保留关键事实，不记录闲聊过程。",
    ),
    WeChatSummaryVariant(
        "personal_diary",
        "个人日记",
        "适配星图笔记第一人称视角。",
        "摘要用第一人称记录我当天处理了什么；笔记保持客观，但允许保留我已完成的关键动作。",
    ),
    WeChatSummaryVariant(
        "resource_index",
        "资源索引",
        "参考微信群模板中的资源链接提取。",
        "重点提取链接、文件、安装包、套餐页、节点名；每个资源说明用途、状态和下一步。",
    ),
    WeChatSummaryVariant(
        "followup_checklist",
        "跟进清单",
        "参考待跟进事项格式。",
        "摘要后，笔记主要写可执行检查清单：已完成、待确认、下次复现需要记录什么。",
    ),
    WeChatSummaryVariant(
        "signal_vs_noise",
        "主线去噪",
        "参考群聊总结中的高频词与主题识别。",
        "先区分主线和噪声：核心问题、辅助工具、无关闲聊；笔记只保留对主线有用的事实。",
    ),
    WeChatSummaryVariant(
        "timeline_compressed",
        "压缩时间线",
        "保留时间结构但避免流水账。",
        "只保留 3-5 个阶段，不写逐条时间；每阶段写状态变化、关键判断、动作结果。",
    ),
    WeChatSummaryVariant(
        "memory_extraction",
        "记忆提取",
        "参考聊天机器人记忆提取项目。",
        "输出可进入长期记忆的稳定信息：偏好、常用工具、账号/订阅事实、反复出现的问题模式。",
    ),
    WeChatSummaryVariant(
        "agent_query",
        "Agent 查询",
        "参考 wechat-cli 给 AI Agent 查询历史的命令式用法。",
        "笔记像给未来 Agent 的查询答案：下次遇到同类问题，应先查哪些事实、运行哪些动作。",
    ),
    WeChatSummaryVariant(
        "qq_group_analysis",
        "群聊分析",
        "参考 QQ 群聊分析工具的统计/报告思路。",
        "强调参与者、消息量、主题热度和行动项；即使是私聊，也提取沟通强度和重点事项。",
    ),
    WeChatSummaryVariant(
        "sensitive_risk",
        "风险提醒",
        "参考微信群模板中敏感话题与注意事项。",
        "重点记录账号、付费、共享、封号、网络工具等风险；事实和建议分开写。",
    ),
    WeChatSummaryVariant(
        "workflow_recipe",
        "流程配方",
        "适合注册、安装、配置、订阅流程。",
        "把聊天中形成的方法整理成配方：材料/入口、步骤、验证标准、失败回退。",
    ),
    WeChatSummaryVariant(
        "minimal_actionable",
        "极简可执行",
        "压缩版。",
        "摘要 3 条以内；笔记只保留最能指导下次行动的 5 条，每条必须能直接执行或判断。",
    ),
]


def variant_by_key(key: str) -> WeChatSummaryVariant:
    for variant in VARIANTS:
        if variant.key == key:
            return variant
    raise KeyError(key)
