"""Built-in seeds for the shared Fanxiu choice knowledge model."""

from __future__ import annotations


LILIAN_EVENT_RECOMMENDED_ANSWERS: tuple[tuple[str, str], ...] = (
    ("灵果换药", "购买灵药"),
    ("生死赌斗", "刚正"),
    ("奇异法石", "全力"),
    ("全女之宗", "不分男女"),
    ("枯骨美人", "坦而言之"),
    ("情为何物", "长生"),
    ('跪拜"真仙"', "显露"),
    ("英雄救美", "踹他一脚"),
    ("雪山斗法", "偷袭"),
    ("雪山残尸", "安葬"),
    ("冰雪鸳鸯", "仗义相助"),
    ("妙法玉简", "询问"),
    ("逃婚少女", "助她"),
    ("星光淬体", "蒲团"),
    ("魔祖投诚", "灭杀"),
    ("商会委托", "直接答应"),
    ("血脉引动", "另寻他法"),
    ("斗法试炼", "团队"),
    ("大罗之命", "原地不动"),
    ("无求剑意", "直言无趣"),
    ("草原倩影", "拒绝"),
    ("鬼市赌局", "愿赌服输"),
    ("鲛鱼戏水", "提醒"),
    ("忘忧甘露", "品尝"),
    ("女修王国", "答应"),
    ("双修之缘", "同意"),
    ("石父捧骨", "欺骗"),
)


# Only options proven by a real current screen belong here. Missing events stay
# intentionally incomplete until their business layer observes them.
LILIAN_EVENT_OBSERVED_OPTIONS: dict[str, tuple[str, ...]] = {
    "妙法玉简": ("捡漏买下", "询问出处"),
}


# First real #430-#432 capture.  Positions are stable for each question in
# 活动_答题, so the ordered option list is reusable without re-learning it.
ACTIVITY_QUIZ_OBSERVED_QUESTIONS: tuple[
    tuple[str, tuple[str, ...], str], ...
] = (
    ("韩立拼死从呼老魔手里救出的人是？", ("黛儿", "张铁", "紫灵"), "紫灵"),
    ("南宫婉的体质特殊，被称为？", ("纯阴之体", "纯阳之体", "凡人体质"), "纯阴之体"),
    ("剑修功法适合什么性格的人修炼？", ("坚毅", "刚正", "自在"), "刚正"),
    ("困住银月万年的宝贝是什么？", ("古朴黑戒", "狼首玉如意", "掌天瓶"), "狼首玉如意"),
    ("用四个字评价韩立的样貌，这四个字是？", ("平平无奇", "惊为天人", "奇丑无比"), "平平无奇"),
)
