from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


TRANSLATION_VERSION = "manual_zh_tcg_v1"

SET_ZH = {
    "Base Set": "基础系列",
    "Jungle": "丛林",
    "Fossil": "化石",
    "Team Rocket": "火箭队",
}

RARITY_ZH = {
    "Common": "普通",
    "Uncommon": "非普通",
    "Rare": "稀有",
    "Rare Holo": "稀有闪卡",
    "Rare Secret": "秘密稀有",
    "Promo": "宣传卡",
}

STAGE_ZH = {
    "Basic": "基础宝可梦",
    "Stage 1": "一阶进化",
    "Stage 2": "二阶进化",
    "Pokémon": "宝可梦",
}

ENERGY_ZH = {
    "C": "无色",
    "G": "草",
    "R": "火",
    "W": "水",
    "L": "雷",
    "P": "超能力",
    "F": "斗",
    "D": "恶",
}

SPECIES_ZH = {
    "Abra": "凯西",
    "Aerodactyl": "化石翼龙",
    "Alakazam": "胡地",
    "Arbok": "阿柏怪",
    "Arcanine": "风速狗",
    "Articuno": "急冻鸟",
    "Beedrill": "大针蜂",
    "Bellsprout": "喇叭芽",
    "Blastoise": "水箭龟",
    "Bulbasaur": "妙蛙种子",
    "Butterfree": "巴大蝶",
    "Caterpie": "绿毛虫",
    "Chansey": "吉利蛋",
    "Charizard": "喷火龙",
    "Charmander": "小火龙",
    "Charmeleon": "火恐龙",
    "Clefable": "皮可西",
    "Clefairy": "皮皮",
    "Cloyster": "刺甲贝",
    "Cubone": "卡拉卡拉",
    "Dewgong": "白海狮",
    "Diglett": "地鼠",
    "Ditto": "百变怪",
    "Dodrio": "嘟嘟利",
    "Doduo": "嘟嘟",
    "Dragonair": "哈克龙",
    "Dragonite": "快龙",
    "Dratini": "迷你龙",
    "Drowzee": "催眠貘",
    "Dugtrio": "三地鼠",
    "Eevee": "伊布",
    "Ekans": "阿柏蛇",
    "Electabuzz": "电击兽",
    "Electrode": "顽皮雷弹",
    "Exeggcute": "蛋蛋",
    "Exeggutor": "椰蛋树",
    "Farfetch'd": "大葱鸭",
    "Fearow": "大嘴雀",
    "Flareon": "火伊布",
    "Gastly": "鬼斯",
    "Gengar": "耿鬼",
    "Geodude": "小拳石",
    "Gloom": "臭臭花",
    "Golbat": "大嘴蝠",
    "Goldeen": "角金鱼",
    "Golduck": "哥达鸭",
    "Golem": "隆隆岩",
    "Graveler": "隆隆石",
    "Grimer": "臭泥",
    "Growlithe": "卡蒂狗",
    "Gyarados": "暴鲤龙",
    "Haunter": "鬼斯通",
    "Hitmonchan": "快拳郎",
    "Horsea": "墨海马",
    "Hypno": "引梦貘人",
    "Ivysaur": "妙蛙草",
    "Jigglypuff": "胖丁",
    "Jolteon": "雷伊布",
    "Jynx": "迷唇姐",
    "Kabuto": "化石盔",
    "Kabutops": "镰刀盔",
    "Kadabra": "勇基拉",
    "Kakuna": "铁壳蛹",
    "Kangaskhan": "袋兽",
    "Kingler": "巨钳蟹",
    "Koffing": "瓦斯弹",
    "Krabby": "大钳蟹",
    "Lapras": "拉普拉斯",
    "Lickitung": "大舌头",
    "Machamp": "怪力",
    "Machoke": "豪力",
    "Machop": "腕力",
    "Magikarp": "鲤鱼王",
    "Magmar": "鸭嘴火兽",
    "Magnemite": "小磁怪",
    "Magneton": "三合一磁怪",
    "Mankey": "猴怪",
    "Marowak": "嘎啦嘎啦",
    "Meowth": "喵喵",
    "Metapod": "铁甲蛹",
    "Mewtwo": "超梦",
    "Moltres": "火焰鸟",
    "Mr. Mime": "魔墙人偶",
    "Muk": "臭臭泥",
    "Nidoking": "尼多王",
    "Nidoqueen": "尼多后",
    "Nidoran F": "尼多兰",
    "Nidoran M": "尼多朗",
    "Nidorina": "尼多娜",
    "Nidorino": "尼多力诺",
    "Ninetales": "九尾",
    "Oddish": "走路草",
    "Omanyte": "菊石兽",
    "Omastar": "多刺菊石兽",
    "Onix": "大岩蛇",
    "Paras": "派拉斯",
    "Parasect": "派拉斯特",
    "Persian": "猫老大",
    "Pidgeot": "大比鸟",
    "Pidgeotto": "比比鸟",
    "Pidgey": "波波",
    "Pikachu": "皮卡丘",
    "Pinsir": "凯罗斯",
    "Poliwag": "蚊香蝌蚪",
    "Poliwhirl": "蚊香君",
    "Poliwrath": "蚊香泳士",
    "Ponyta": "小火马",
    "Porygon": "多边兽",
    "Primeape": "火暴猴",
    "Psyduck": "可达鸭",
    "Raichu": "雷丘",
    "Rapidash": "烈焰马",
    "Raticate": "拉达",
    "Rattata": "小拉达",
    "Rhydon": "钻角犀兽",
    "Rhyhorn": "独角犀牛",
    "Sandshrew": "穿山鼠",
    "Sandslash": "穿山王",
    "Scyther": "飞天螳螂",
    "Seadra": "海刺龙",
    "Seaking": "金鱼王",
    "Seel": "小海狮",
    "Shellder": "大舌贝",
    "Slowbro": "呆壳兽",
    "Slowpoke": "呆呆兽",
    "Snorlax": "卡比兽",
    "Spearow": "烈雀",
    "Squirtle": "杰尼龟",
    "Starmie": "宝石海星",
    "Staryu": "海星星",
    "Tangela": "蔓藤怪",
    "Tauros": "肯泰罗",
    "Tentacool": "玛瑙水母",
    "Tentacruel": "毒刺水母",
    "Vaporeon": "水伊布",
    "Venomoth": "摩鲁蛾",
    "Venonat": "毛球",
    "Venusaur": "妙蛙花",
    "Victreebel": "大食花",
    "Vileplume": "霸王花",
    "Voltorb": "霹雳电球",
    "Vulpix": "六尾",
    "Wartortle": "卡咪龟",
    "Weedle": "独角虫",
    "Weepinbell": "口呆花",
    "Weezing": "双弹瓦斯",
    "Wigglytuff": "胖可丁",
    "Zapdos": "闪电鸟",
    "Zubat": "超音蝠",
}

TRAINER_ZH = {
    "Bill": "正辉",
    "Computer Search": "电脑搜索",
    "Devolution Spray": "退化喷雾",
    "Energy Removal": "能量移除",
    "Gust of Wind": "一阵风",
    "Imposter Professor Oak": "冒牌大木博士",
    "Item Finder": "道具搜寻器",
    "Lass": "短裤女孩",
    "Pokémon Breeder": "宝可梦培育家",
    "Pokémon Trader": "宝可梦交换商",
    "Scoop Up": "收回",
    "Super Energy Removal": "超级能量移除",
    "Defender": "防御器",
    "Energy Retrieval": "能量回收",
    "Full Heal": "万灵药",
    "Maintenance": "维护",
    "PlusPower": "力量增强",
    "Pokémon Center": "宝可梦中心",
    "Pokédex": "宝可梦图鉴",
    "Professor Oak": "大木博士",
    "Revive": "复活",
    "Super Potion": "超级伤药",
    "Switch": "替换",
    "Double Colorless Energy": "双倍无色能量",
    "Grass Energy": "草能量",
    "Fire Energy": "火能量",
    "Water Energy": "水能量",
    "Lightning Energy": "雷能量",
    "Psychic Energy": "超能力能量",
    "Fighting Energy": "斗能量",
    "Dark Dragonite": "黑暗快龙",
    "Dark Charizard": "黑暗喷火龙",
    "Rocket's Sneak Attack": "火箭队的偷袭",
    "Here Comes Team Rocket!": "火箭队来了！",
    "The Boss's Way": "老大的做法",
}

ATTACK_NAME_ZH = {
    "Absorb": "吸取",
    "Acid": "溶解液",
    "Agility": "高速移动",
    "Amnesia": "瞬间失忆",
    "Aurora Beam": "极光束",
    "Avalanche": "雪崩",
    "Barrier": "屏障",
    "Bind": "绑紧",
    "Bite": "咬住",
    "Blizzard": "暴风雪",
    "Body Slam": "泰山压顶",
    "Bonemerang": "骨头回力镖",
    "Bubble": "泡沫",
    "Bubblebeam": "泡沫光线",
    "Call for Family": "呼唤同伴",
    "Confuse Ray": "混乱光线",
    "Crabhammer": "蟹钳锤",
    "Damage Swap": "伤害转移",
    "Dig": "挖洞",
    "Double Kick": "二连踢",
    "Double-edge": "舍身冲撞",
    "Doubleslap": "连环巴掌",
    "Dragon Rage": "龙之怒",
    "Dream Eater": "食梦",
    "Drill Peck": "啄钻",
    "Earthquake": "地震",
    "Ember": "火花",
    "Energy Burn": "能量燃烧",
    "Energy Trans": "能量转移",
    "Fire Blast": "大字爆炎",
    "Fire Punch": "火焰拳",
    "Fire Spin": "火焰旋涡",
    "Flamethrower": "喷射火焰",
    "Fury Attack": "乱击",
    "Fury Swipes": "疯狂乱抓",
    "Guillotine": "断头钳",
    "Harden": "变硬",
    "Headbutt": "头锤",
    "Horn Attack": "角撞",
    "Horn Drill": "角钻",
    "Hydro Pump": "水炮",
    "Hyper Beam": "破坏光线",
    "Hyper Fang": "必杀门牙",
    "Hypnosis": "催眠术",
    "Ice Beam": "冰冻光束",
    "Jab": "刺拳",
    "Karate Chop": "空手劈",
    "Leech Life": "吸血",
    "Leech Seed": "寄生种子",
    "Leer": "瞪眼",
    "Lick": "舌舔",
    "Low Kick": "下盘踢",
    "Lure": "引诱",
    "Mega Drain": "超级吸取",
    "Mega Punch": "百万吨重拳",
    "Metronome": "挥指",
    "Minimize": "变小",
    "Pay Day": "聚宝功",
    "Peck": "啄",
    "Petal Dance": "花瓣舞",
    "Pin Missile": "飞弹针",
    "Poison Gas": "毒瓦斯",
    "Poison Sting": "毒针",
    "Poisonpowder": "毒粉",
    "Pound": "拍击",
    "Psybeam": "幻象光线",
    "Psychic": "精神强念",
    "Quick Attack": "电光一闪",
    "Rage": "愤怒",
    "Rain Dance": "求雨",
    "Razor Leaf": "飞叶快刀",
    "Recover": "自我再生",
    "Rock Throw": "落石",
    "Scratch": "抓",
    "Scrunch": "缩紧",
    "Seismic Toss": "地球上投",
    "Selfdestruct": "自爆",
    "Sing": "唱歌",
    "Slam": "摔打",
    "Slash": "劈开",
    "Sleep Powder": "催眠粉",
    "Smog": "浊雾",
    "Smokescreen": "烟幕",
    "Solarbeam": "日光束",
    "Sonicboom": "音爆",
    "Special Punch": "特殊拳",
    "Spore": "蘑菇孢子",
    "Stomp": "踩踏",
    "String Shot": "吐丝",
    "Stun Spore": "麻痹粉",
    "Submission": "地狱翻滚",
    "Supersonic": "超音波",
    "Swords Dance": "剑舞",
    "Tackle": "撞击",
    "Tail Wag": "摇尾巴",
    "Take Down": "猛撞",
    "Teleport": "瞬间移动",
    "Thrash": "大闹一番",
    "Thunder": "打雷",
    "Thunder Wave": "电磁波",
    "Thunderbolt": "十万伏特",
    "Thunderpunch": "雷电拳",
    "Thundershock": "电击",
    "Toxic": "剧毒",
    "Transform": "变身",
    "Twineedle": "双针",
    "Vine Whip": "藤鞭",
    "Water Gun": "水枪",
    "Waterfall": "攀瀑",
    "Whirlwind": "吹飞",
    "Wing Attack": "翅膀攻击",
    "Withdraw": "缩入壳中",
    "Wrap": "缠绕",
}

PHRASE_REPLACEMENTS = [
    ("you may move 1 damage counter from 1 of your Pokémon to another as long as you don’t Knock Out that Pokémon.", "你可以把自己一只宝可梦身上的 1 个伤害指示物，移动到自己的另一只宝可梦身上，但不能因此让那只宝可梦气绝。"),
    ("you may move 1 damage counter from 1 of your Pokémon to another as long as you don't Knock Out that Pokémon.", "你可以把自己一只宝可梦身上的 1 个伤害指示物，移动到自己的另一只宝可梦身上，但不能因此让那只宝可梦气绝。"),
    ("if Alakazam is Asleep, Confused, or Paralyzed.", "如果胡地处于睡眠、混乱或麻痹状态。"),
    ("If heads, the Defending Pokémon is now Confused.", "若为正面，防守宝可梦陷入混乱。"),
    ("If heads, the Defending Pokémon is now Asleep.", "若为正面，防守宝可梦陷入睡眠。"),
    ("If heads, the Defending Pokémon is now Paralyzed.", "若为正面，防守宝可梦陷入麻痹。"),
    ("If heads, the Defending Pokémon is now Poisoned.", "若为正面，防守宝可梦陷入中毒。"),
    ("This power can’t be used if", "如果"),
    ("This power can't be used if", "如果"),
    ("is Asleep, Confused, or Paralyzed.", "处于睡眠、混乱或麻痹状态，则不能使用这个能力。"),
    ("Pokémon Power", "宝可梦特殊能力"),
    ("Defending Pokémon", "防守宝可梦"),
    ("Active Pokémon", "出战宝可梦"),
    ("Benched Pokémon", "备战宝可梦"),
    ("Energy card", "能量卡"),
    ("Energy cards", "能量卡"),
    ("damage counter", "伤害指示物"),
    ("damage counters", "伤害指示物"),
    ("during your opponent’s next turn", "在对手的下个回合"),
    ("during your opponent's next turn", "在对手的下个回合"),
    ("during your turn", "在你的回合中"),
    ("before your attack", "在你攻击前"),
    ("As often as you like", "你可以任意多次"),
    ("Flip a coin.", "投掷一枚硬币。"),
    ("Flip 2 coins.", "投掷两枚硬币。"),
    ("If heads,", "若为正面，"),
    ("if heads,", "若为正面，"),
    ("If tails,", "若为反面，"),
    ("if tails,", "若为反面，"),
    ("prevent all damage", "防止所有伤害"),
    ("prevent all effects of attacks, including damage", "防止攻击造成的所有效果，包括伤害"),
    ("does 10 damage", "造成 10 点伤害"),
    ("does 20 damage", "造成 20 点伤害"),
    ("does 30 damage", "造成 30 点伤害"),
    ("does 40 damage", "造成 40 点伤害"),
    ("does 50 damage", "造成 50 点伤害"),
    ("does 60 damage", "造成 60 点伤害"),
    ("does 70 damage", "造成 70 点伤害"),
    ("does 80 damage", "造成 80 点伤害"),
    ("does 100 damage", "造成 100 点伤害"),
    ("plus 10 more damage", "再增加 10 点伤害"),
    ("plus 20 more damage", "再增加 20 点伤害"),
    ("times the number of heads", "乘以正面次数"),
    ("is now Asleep", "陷入睡眠"),
    ("is now Confused", "陷入混乱"),
    ("is now Paralyzed", "陷入麻痹"),
    ("is now Poisoned", "陷入中毒"),
    ("This power can’t be used", "这个能力不能使用"),
    ("This power can't be used", "这个能力不能使用"),
    ("Discard", "丢弃"),
    ("attached to", "附着在"),
    ("choose 1", "选择 1 个"),
    ("move 1", "移动 1 个"),
    ("from 1 of", "从 1 只"),
    ("to another", "到另一只"),
    ("as long as", "只要"),
    ("you may", "你可以"),
    ("may", "可以"),
    ("your opponent", "你的对手"),
    ("opponent’s", "对手的"),
    ("opponent's", "对手的"),
    ("your", "你的"),
    ("this attack", "这个招式"),
    ("Knocked Out", "气绝"),
    ("Weakness and Resistance", "弱点与抵抗力"),
    ("Don’t apply", "不计算"),
    ("Don't apply", "不计算"),
    ("Pokémon", "宝可梦"),
]

FLAVOR_REPLACEMENTS = [
    ("Its brain can outperform a supercomputer. Its intelligence quotient is said to be 5000.", "它的大脑性能可以超过超级计算机。据说它的智商高达 5000。"),
    ("Spits fire that is hot enough to melt boulders. Known to unintentionally cause forest fires.", "会喷出足以熔化巨石的火焰。也因无意中引发森林火灾而闻名。"),
    ("A scientist created this Pokémon after years of horrific gene-splicing and DNA engineering experiments.", "一位科学家经过多年可怕的基因拼接和 DNA 工程实验，创造出了这只宝可梦。"),
    ("A legendary bird Pokémon said to appear from clouds while wielding enormous lightning bolts.", "传说中的鸟宝可梦，据说会从云层中现身，并挥洒巨大的雷电。"),
    ("Its boulder-like body is extremely hard. It can easily withstand dynamite blasts without damage.", "它像巨石一样的身体极其坚硬，即使用炸药爆破也能轻易承受而不受伤。"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def zh_name(name: str) -> str:
    if not name:
        return ""
    if name.startswith("Dark "):
        return f"黑暗{zh_name(name.removeprefix('Dark ').strip())}"
    return SPECIES_ZH.get(name) or TRAINER_ZH.get(name) or name


def translate_energy_symbols(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        code = match.group(1)
        return ENERGY_ZH.get(code, code)

    return re.sub(r"\{\s*([A-Z])\s*\}", repl, text)


def translate_stage(stage: str) -> str:
    return STAGE_ZH.get(stage, stage or "")


def translate_rarity(rarity: str) -> str:
    return RARITY_ZH.get(rarity, rarity or "")


def translate_set_name(set_name: str) -> str:
    return SET_ZH.get(set_name, set_name or "")


def translate_weakness(value: str) -> str:
    if not value or value == "n/a":
        return "无"
    return translate_energy_symbols(value)


def translate_resistance(value: str) -> str:
    if not value or value == "n/a":
        return "无"
    return translate_energy_symbols(value)


def translate_attack_names(text: str) -> str:
    result = text
    for name in sorted(ATTACK_NAME_ZH, key=len, reverse=True):
        result = re.sub(rf"(?<=→\s){re.escape(name)}(?=\s|:|$)", ATTACK_NAME_ZH[name], result)
        result = re.sub(rf"(?<=⇢\s){re.escape(name)}(?=\s|:|$)", ATTACK_NAME_ZH[name], result)
    return result


def translate_rules_text(text: str) -> str:
    if not text:
        return ""
    result = text.replace("→", "→").replace("⇢", "⇢")
    result = translate_attack_names(result)
    result = translate_energy_symbols(result)
    for english, chinese in sorted({**SPECIES_ZH, **TRAINER_ZH}.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"\b{re.escape(english)}\b", chinese, result)
    for source, target in PHRASE_REPLACEMENTS:
        result = result.replace(source, target)
    result = result.replace("⇢", "：").replace("→", "：")
    result = re.sub(r"\s+,", "，", result)
    result = re.sub(r"\s+\.", "。", result)
    result = result.replace(". ", "。").replace(" ,", "，").replace(", ", "，")
    result = result.replace("(", "（").replace(")", "）")
    result = re.sub(r"\s+", " ", result).strip()
    return result


def translate_flavor_text(text: str) -> str:
    if not text:
        return ""
    for source, target in FLAVOR_REPLACEMENTS:
        if text == source:
            return target
    result = text
    for english, chinese in sorted(SPECIES_ZH.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"\b{re.escape(english)}\b", chinese, result)
    phrase_pairs = [
        ("A legendary bird Pokémon", "传说中的鸟宝可梦"),
        ("A rare and elusive Pokémon", "稀有而难以捉摸的宝可梦"),
        ("Rarely seen in the wild", "在野外很少见"),
        ("Normally found", "通常出没于"),
        ("It is said to", "据说它会"),
        ("It can", "它能够"),
        ("It has", "它拥有"),
        ("Its", "它的"),
        ("When", "当"),
        ("while", "同时"),
        ("during", "在"),
        ("attacks", "攻击"),
        ("attack", "攻击"),
        ("powerful", "强力的"),
        ("high speed", "高速"),
        ("lightning", "雷电"),
        ("fire", "火焰"),
        ("water", "水"),
        ("poison", "毒"),
        ("body", "身体"),
        ("tail", "尾巴"),
        ("wings", "翅膀"),
        ("rare", "稀有"),
        ("wild", "野外"),
        ("energy", "能量"),
        ("opponent", "对手"),
        ("prey", "猎物"),
        ("city", "城市"),
        ("cities", "城市"),
    ]
    for source, target in phrase_pairs:
        result = re.sub(rf"\b{re.escape(source)}\b", target, result, flags=re.IGNORECASE)
    result = result.replace(". ", "。").replace(".", "。").replace(", ", "，")
    return result.strip()


def translate_card(card: dict[str, Any]) -> dict[str, Any]:
    official_name = str(card.get("official_name") or "")
    species = str(card.get("pokemon_species") or official_name)
    set_name = str(card.get("set_name") or "")
    official_id = str(card.get("official_id") or "")
    set_zh = translate_set_name(set_name)
    name_zh = zh_name(official_name)
    species_zh = zh_name(species)
    hp = str(card.get("hp") or "")
    color_zh = translate_energy_symbols(str(card.get("color") or ""))
    stage_zh = translate_stage(str(card.get("stage") or ""))
    attacks_zh = translate_rules_text(str(card.get("attacks_text") or ""))
    weakness_zh = translate_weakness(str(card.get("weakness_text") or ""))
    resistance_zh = translate_resistance(str(card.get("resistance_text") or ""))
    rarity_zh = translate_rarity(str(card.get("rarity") or ""))
    flavor_zh = translate_flavor_text(str(card.get("flavor_text") or ""))
    evolves_from_zh = zh_name(str(card.get("evolves_from") or ""))
    evolves_into_zh = zh_name(str(card.get("evolves_into") or ""))
    display_title_zh = f"{name_zh} · {set_zh}（{str(card.get('official_set_code') or '')}）#{str(card.get('official_number') or '')}"
    return {
        "translation_version": TRANSLATION_VERSION,
        "translated_at": now_iso(),
        "display_title": display_title_zh,
        "set_name": set_zh,
        "official_name": name_zh,
        "pokemon_species": species_zh,
        "hp_text": f"{hp} HP" if hp else "",
        "color": color_zh,
        "stage": stage_zh,
        "evolves_from": evolves_from_zh,
        "evolves_into": evolves_into_zh,
        "attacks_text": attacks_zh,
        "weakness_text": weakness_zh,
        "resistance_text": resistance_zh,
        "retreat_cost": card.get("retreat_cost"),
        "rarity": rarity_zh,
        "release_date_text": str(card.get("release_date_text") or ""),
        "illustrator_text": str(card.get("illustrator_text") or "").replace("illus.", "插画："),
        "flavor_text": flavor_zh,
        "source_label": "PkmnCards",
        "summary_lines": [
            f"{name_zh} · {hp} HP · {color_zh}".strip(" ·"),
            stage_zh,
            f"弱点：{weakness_zh} / 抵抗：{resistance_zh} / 撤退：{card.get('retreat_cost') if card.get('retreat_cost') is not None else '无'}",
            f"{rarity_zh} · {str(card.get('release_date_text') or '')}".strip(" ·"),
        ],
        "source_identity": {
            "set": set_zh,
            "set_original": set_name,
            "official_id": official_id,
            "number": str(card.get("official_number") or ""),
            "total": str(card.get("official_total") or ""),
        },
    }
