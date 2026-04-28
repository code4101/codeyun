import math
import re
import time
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.core.fanxiu_inventory import load_region_character_list
from backend.models import FanxiuRegionArea, FanxiuRegionCharacterRecord, FanxiuRegionServer


CROSS_SIZE = 64
ANCHOR_REGION_NAME = "天澜圣殿"
ANCHOR_REGION_START_DATE = "2025-02-27"
LATE_REGION_FIRST_NAME = "古云大陆"
OBSERVED_LATE_REGION_NAME = "神驹降世"
OBSERVED_LATE_REGION_SERVER_ORDER = 7
OBSERVED_LATE_REGION_SERVER_DATE = "2026-04-27"
ATTACK_UNIT_EXPONENT = {
    "万": 4,
    "亿": 8,
    "兆": 12,
    "京": 16,
    "垓": 20,
    "秭": 24,
    "穰": 28,
    "沟": 32,
    "涧": 36,
    "正": 40,
    "载": 44,
    "极": 48,
}

REGION_NAMES = [
    "七玄风云",
    "黄枫轶事",
    "禁地试炼",
    "太岳灵眼",
    "越国皇宫",
    "天南大陆",
    "星海风光",
    "妙音秘坊",
    "风兽巢穴",
    "古修遗址",
    "无名荒岛",
    "天一石城",
    "阴冥之地",
    "无尽之海",
    "绿踪沼泽",
    "慕兰草原",
    "天澜圣殿",
    "极西之地",
    "古云大陆",
    "冥寒大陆",
    "魔金山脉",
    "大话西游",
    "一马当先",
    "神驹降世",
]

KNOWN_REGION_SERVER_NAMES: dict[str, list[str]] = {
    "妙音秘坊": [
        "千锤百炼",
        "悬壶济世",
        "远见卓识",
        "候鸟南飞",
        "意气风发",
        "逆境求生",
        "走马观花",
        "矢志不移",
        "寸草不生",
        "春风得意",
        "闻风而动",
        "一身正气",
        "梦枕新歌",
        "专注致志",
        "反求诸己",
        "视若无睹",
        "从容应对",
        "持之以恒",
        "莫测高深",
        "凭借内力",
        "修身养性",
        "甜水巷里",
        "以和为贵",
        "红尘仙境",
        "沂水弦歌",
        "知易行难",
        "鸳鸯翩跹",
        "逆流而上",
        "朱砂红藻",
        "瑰丽山河",
        "沐清海平",
        "一枕槐安",
        "瑶池仙境",
        "雁过留声",
        "碧水长流",
        "青山不老",
        "竹影婆娑",
        "银汉流照",
        "独步青云",
        "风雨如磐",
        "鹤骨松姿",
        "雪中送炭",
        "烟雨梦幻",
        "一笑莲海",
        "知音难觅",
        "纵情天地",
        "松林幽径",
        "玄黄共存",
        "翩翩起舞",
        "天穹之巅",
        "雾笼山谷",
        "雨落梧桐",
        "律转鸿钧",
        "火树银花",
        "炼玉流金",
        "稳如泰山",
        "仙鹤翩翩",
        "霜降寒露",
        "梦中仙境",
        "暮色苍茫",
        "风卷残云",
        "云深雾绕",
        "心有灵犀",
        "心念如玉",
    ],
    "天澜圣殿": [
        "云霞绚烂",
        "银装素裹",
        "九州风云",
        "稳操胜养",
        "莺啼燕语",
        "虎踞龙盘",
        "福泽天下",
        "瑞气盈门",
        "大夜弥天",
        "空谷足音",
        "无边映月",
        "雁阵高飞",
        "夜以继日",
        "星星点点",
        "春色茫茫",
        "心向往之",
        "巍巍青山",
        "小桥流水",
        "志在千里",
        "镇山静海",
        "斗转星移",
        "霞光万道",
        "枫桥夜泊",
        "帘外骤雨",
        "万事如意",
        "潮起东方",
        "神采飞扬",
        "碧血丹心",
        "光明正大",
        "时节如流",
        "鸾凤和鸣",
        "忠肝义胆",
        "彩云追月",
        "落霞孤骛",
        "夜幕深深",
        "茫茫烟波",
        "天冷气清",
        "丹心如火",
        "漫步长阳",
        "落宝八方",
        "前途无量",
        "才华出众",
        "光芒四射",
        "自由飞翔",
        "丝丝入扣",
        "听风起雨",
        "纵横捭阖",
        "花容月貌",
        "快步流星",
        "月白风清",
        "洞若观火",
        "海浪无声",
        "岁序更替",
        "大智若愚",
        "金相玉质",
        "春华秋实",
        "才子佳人",
        "风云人物",
        "烈日灼心",
        "金相玉振",
        "情深义重",
        "古往今来",
        "脱颖而出",
        "喜笑颜开",
    ],
    "极西之地": [
        "耳濡目染",
        "隔却山海",
        "万里晴空",
        "漫漫长路",
        "瑶草奇花",
        "长歌倚楼",
        "琼壶歌月",
        "任重道远",
        "杨柳依依",
    ],
}

SERVER_MARKS = {
    ("妙音秘坊", "梦枕新歌"): {
        "mark_type": "past",
        "mark_label": "曾玩",
        "mark_title": "以前短暂玩过的区服",
    },
    ("天澜圣殿", "岁序更替"): {
        "mark_type": "current",
        "mark_label": "当前",
        "mark_title": "当前在玩的区服",
    },
}


def normalize_region_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_attack_text(value: Any) -> str:
    translation = str.maketrans({
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "．": ".",
        "萬": "万",
        "億": "亿",
    })
    return re.sub(r"[\s,，]+", "", normalize_region_text(value)).translate(translation)


def parse_attack_power_score(value: Any) -> float:
    text = normalize_attack_text(value)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([万亿兆京垓秭穰沟涧正载极]*)", text)
    if not match:
        return float("-inf")

    coefficient = float(match.group(1))
    if not math.isfinite(coefficient) or coefficient <= 0:
        return float("-inf")

    exponent = sum(ATTACK_UNIT_EXPONENT.get(unit, 0) for unit in match.group(2))
    return math.log10(coefficient) + exponent


def is_attack_power_higher(left: Any, right: Any) -> bool:
    return parse_attack_power_score(left) > parse_attack_power_score(right)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _add_days(value: str, days: int) -> str:
    return (_parse_date(value) + timedelta(days=days)).isoformat()


def _region_start_date(region_index: int) -> str:
    anchor_index = REGION_NAMES.index(ANCHOR_REGION_NAME)
    late_first_index = REGION_NAMES.index(LATE_REGION_FIRST_NAME)
    observed_late_index = REGION_NAMES.index(OBSERVED_LATE_REGION_NAME)
    observed_late_start_date = _add_days(
        OBSERVED_LATE_REGION_SERVER_DATE,
        1 - OBSERVED_LATE_REGION_SERVER_ORDER,
    )
    if region_index >= late_first_index:
        return _add_days(observed_late_start_date, (region_index - observed_late_index) * CROSS_SIZE)
    return _add_days(ANCHOR_REGION_START_DATE, (region_index - anchor_index) * CROSS_SIZE)


def _region_seed_items() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, region_name in enumerate(REGION_NAMES):
        start_date = _region_start_date(index)
        end_date = _add_days(start_date, CROSS_SIZE - 1)
        result.append(
            {
                "number": index + 1,
                "name": region_name,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
    return result


def _server_seed_items(region: FanxiuRegionArea) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, server_name in enumerate(KNOWN_REGION_SERVER_NAMES.get(region.name, [])):
        order = index + 1
        mark = SERVER_MARKS.get((region.name, server_name), {})
        result.append(
            {
                "region_id": region.id,
                "region_name": region.name,
                "server_order": order,
                "name": server_name,
                "open_date": _add_days(region.start_date, index),
                "mark_type": mark.get("mark_type", ""),
                "mark_label": mark.get("mark_label", ""),
                "mark_title": mark.get("mark_title", ""),
            }
        )
    return result


def ensure_fanxiu_region_data_seeded(session: Session) -> None:
    mutated = False
    now = time.time()
    region_by_name = {
        region.name: region
        for region in session.exec(select(FanxiuRegionArea)).all()
    }

    for seed in _region_seed_items():
        region = region_by_name.get(seed["name"])
        if region is None:
            region = FanxiuRegionArea(**seed, created_at=now, updated_at=now)
            session.add(region)
            session.flush()
            region_by_name[region.name] = region
            mutated = True
            continue

        changed = False
        for key in ("number", "start_date", "end_date"):
            if getattr(region, key) != seed[key]:
                setattr(region, key, seed[key])
                changed = True
        if changed:
            region.updated_at = now
            session.add(region)
            mutated = True

    server_by_key = {
        (server.region_name, server.name): server
        for server in session.exec(select(FanxiuRegionServer)).all()
    }
    for region_name in REGION_NAMES:
        region = region_by_name.get(region_name)
        if region is None:
            continue
        for seed in _server_seed_items(region):
            key = (seed["region_name"], seed["name"])
            server = server_by_key.get(key)
            if server is None:
                server = FanxiuRegionServer(**seed, created_at=now, updated_at=now)
                session.add(server)
                server_by_key[key] = server
                mutated = True
                continue

            changed = False
            for field_name in (
                "region_id",
                "server_order",
                "open_date",
                "mark_type",
                "mark_label",
                "mark_title",
            ):
                if getattr(server, field_name) != seed[field_name]:
                    setattr(server, field_name, seed[field_name])
                    changed = True
            if changed:
                server.updated_at = now
                session.add(server)
                mutated = True

    if mutated:
        session.commit()


def migrate_legacy_region_characters_if_needed(session: Session) -> None:
    if session.exec(select(FanxiuRegionCharacterRecord).limit(1)).first() is not None:
        return

    try:
        legacy_payload = load_region_character_list()
    except Exception:
        return

    raw_characters = legacy_payload.get("characters")
    if not isinstance(raw_characters, list):
        return

    now = time.time()
    mutated = False
    for raw_item in raw_characters:
        if not isinstance(raw_item, dict):
            continue
        region_name = normalize_region_text(raw_item.get("region_name"))
        server_name = normalize_region_text(raw_item.get("server_name"))
        role_name = normalize_region_text(raw_item.get("role_name"))
        attack = normalize_region_text(raw_item.get("attack"))
        if not region_name or not server_name or not role_name or not attack:
            continue
        record = FanxiuRegionCharacterRecord(
            id=normalize_region_text(raw_item.get("id")) or uuid.uuid4().hex,
            region_name=region_name,
            server_name=server_name,
            guild_name=normalize_region_text(raw_item.get("guild_name")),
            role_name=role_name,
            attack=attack,
            cultivation_level=normalize_region_text(raw_item.get("cultivation_level")),
            recorded_date=normalize_region_text(raw_item.get("recorded_date")),
            disabled=bool(raw_item.get("disabled", False)),
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        mutated = True

    if mutated:
        session.commit()


def serialize_region_server(server: FanxiuRegionServer) -> dict[str, Any]:
    return {
        "id": server.id,
        "region_name": server.region_name,
        "order": int(server.server_order or 0),
        "name": server.name,
        "open_date": server.open_date or "",
        "mark_type": server.mark_type or "",
        "mark_label": server.mark_label or "",
        "mark_title": server.mark_title or "",
    }


def serialize_region_area(region: FanxiuRegionArea, servers: list[FanxiuRegionServer]) -> dict[str, Any]:
    ordered_servers = sorted(servers, key=lambda item: (int(item.server_order or 0), item.name))
    return {
        "id": region.id,
        "number": int(region.number or 0),
        "name": region.name,
        "start_date": region.start_date or "",
        "end_date": region.end_date or "",
        "known_count": len(ordered_servers),
        "servers": [serialize_region_server(server) for server in ordered_servers],
    }


def build_region_data_snapshot(session: Session) -> dict[str, Any]:
    ensure_fanxiu_region_data_seeded(session)
    regions = session.exec(select(FanxiuRegionArea).order_by(FanxiuRegionArea.number)).all()
    servers = session.exec(select(FanxiuRegionServer).order_by(FanxiuRegionServer.server_order)).all()
    servers_by_region: dict[str, list[FanxiuRegionServer]] = {}
    for server in servers:
        servers_by_region.setdefault(server.region_name, []).append(server)

    return {
        "regions": [
            serialize_region_area(region, servers_by_region.get(region.name, []))
            for region in regions
        ]
    }


def serialize_region_character_record(record: FanxiuRegionCharacterRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "region_name": record.region_name,
        "server_name": record.server_name,
        "guild_name": record.guild_name or "",
        "role_name": record.role_name or "",
        "attack": record.attack or "",
        "cultivation_level": record.cultivation_level or "",
        "recorded_date": record.recorded_date or "",
        "disabled": bool(record.disabled),
        "created_at": record.created_at or 0,
        "updated_at": record.updated_at or 0,
        "disabled_at": record.disabled_at,
    }


def _character_identity_key(record: FanxiuRegionCharacterRecord) -> tuple[str, str, str, str]:
    return (
        normalize_region_text(record.region_name),
        normalize_region_text(record.server_name),
        normalize_region_text(record.guild_name),
        normalize_region_text(record.role_name),
    )


def _character_identity_key_from_item(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        normalize_region_text(item.get("region_name")),
        normalize_region_text(item.get("server_name")),
        normalize_region_text(item.get("guild_name")),
        normalize_region_text(item.get("role_name")),
    )


def _record_current_sort_key(record: FanxiuRegionCharacterRecord) -> tuple[float, str, float, float]:
    return (
        parse_attack_power_score(record.attack),
        record.recorded_date or "",
        float(record.updated_at or 0),
        float(record.created_at or 0),
    )


def list_current_region_character_records(session: Session) -> list[FanxiuRegionCharacterRecord]:
    migrate_legacy_region_characters_if_needed(session)
    records = session.exec(
        select(FanxiuRegionCharacterRecord).where(FanxiuRegionCharacterRecord.disabled == False)
    ).all()
    records.sort(key=_record_current_sort_key, reverse=True)

    current_by_key: dict[tuple[str, str, str, str], FanxiuRegionCharacterRecord] = {}
    for record in records:
        key = _character_identity_key(record)
        if not key[0] or not key[1] or not key[3]:
            continue
        current_by_key.setdefault(key, record)

    return list(current_by_key.values())


def find_current_region_character_record(
    session: Session,
    item: dict[str, Any],
) -> FanxiuRegionCharacterRecord | None:
    region_name, server_name, guild_name, role_name = _character_identity_key_from_item(item)
    if not region_name or not server_name or not role_name:
        return None

    records = session.exec(
        select(FanxiuRegionCharacterRecord)
        .where(FanxiuRegionCharacterRecord.disabled == False)
        .where(FanxiuRegionCharacterRecord.region_name == region_name)
        .where(FanxiuRegionCharacterRecord.server_name == server_name)
        .where(FanxiuRegionCharacterRecord.guild_name == guild_name)
        .where(FanxiuRegionCharacterRecord.role_name == role_name)
    ).all()
    if not records:
        return None
    return max(records, key=_record_current_sort_key)


def build_region_character_snapshot(session: Session) -> dict[str, Any]:
    return {
        "characters": [
            serialize_region_character_record(record)
            for record in list_current_region_character_records(session)
        ]
    }


def create_region_character_record(session: Session, item: dict[str, Any]) -> FanxiuRegionCharacterRecord:
    now = time.time()
    record = FanxiuRegionCharacterRecord(
        id=normalize_region_text(item.get("id")) or uuid.uuid4().hex,
        region_name=normalize_region_text(item.get("region_name")),
        server_name=normalize_region_text(item.get("server_name")),
        guild_name=normalize_region_text(item.get("guild_name")),
        role_name=normalize_region_text(item.get("role_name")),
        attack=normalize_region_text(item.get("attack")),
        cultivation_level=normalize_region_text(item.get("cultivation_level")),
        recorded_date=normalize_region_text(item.get("recorded_date")),
        disabled=False,
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def create_region_character_record_if_stronger(
    session: Session,
    item: dict[str, Any],
) -> tuple[FanxiuRegionCharacterRecord, bool]:
    migrate_legacy_region_characters_if_needed(session)
    current_record = find_current_region_character_record(session, item)
    if current_record is not None and not is_attack_power_higher(item.get("attack"), current_record.attack):
        return current_record, False
    return create_region_character_record(session, item), True


def get_region_character_record_or_404(session: Session, character_id: str) -> FanxiuRegionCharacterRecord:
    record = session.get(FanxiuRegionCharacterRecord, character_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到区服人物记录")
    return record


def update_region_character_record(
    session: Session,
    character_id: str,
    payload: dict[str, Any],
) -> FanxiuRegionCharacterRecord:
    record = get_region_character_record_or_404(session, character_id)
    changed = False
    for field_name in ("guild_name", "role_name", "attack", "cultivation_level", "recorded_date"):
        if field_name not in payload:
            continue
        next_value = normalize_region_text(payload.get(field_name))
        if getattr(record, field_name) != next_value:
            setattr(record, field_name, next_value)
            changed = True
    if "disabled" in payload:
        next_disabled = bool(payload.get("disabled"))
        if bool(record.disabled) != next_disabled:
            record.disabled = next_disabled
            record.disabled_at = time.time() if next_disabled else None
            changed = True

    if changed:
        record.updated_at = time.time()
        session.add(record)
        session.commit()
        session.refresh(record)
    return record


def disable_region_character_record(session: Session, character_id: str) -> FanxiuRegionCharacterRecord:
    return update_region_character_record(session, character_id, {"disabled": True})


def build_region_character_history_snapshot(
    session: Session,
    *,
    region_name: str = "",
    server_name: str = "",
    guild_name: str = "",
    role_name: str = "",
    include_disabled: bool = True,
) -> dict[str, Any]:
    migrate_legacy_region_characters_if_needed(session)
    statement = select(FanxiuRegionCharacterRecord)
    if not include_disabled:
        statement = statement.where(FanxiuRegionCharacterRecord.disabled == False)
    if region_name:
        statement = statement.where(FanxiuRegionCharacterRecord.region_name == region_name)
    if server_name:
        statement = statement.where(FanxiuRegionCharacterRecord.server_name == server_name)
    if guild_name:
        statement = statement.where(FanxiuRegionCharacterRecord.guild_name == guild_name)
    if role_name:
        statement = statement.where(FanxiuRegionCharacterRecord.role_name == role_name)

    records = session.exec(statement).all()
    records.sort(
        key=lambda item: (
            item.recorded_date or "",
            float(item.created_at or 0),
            float(item.updated_at or 0),
        )
    )
    return {"characters": [serialize_region_character_record(record) for record in records]}
