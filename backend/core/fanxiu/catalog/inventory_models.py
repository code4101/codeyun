from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from pydantic import BaseModel, Field, computed_field, model_validator


class FanxiuWardrobeItem(BaseModel):
    id: str
    name: str = ""
    rank: int = 0
    shenlian: int = 0
    type: str = ""
    quality: Optional[int] = None
    main_use: str = ""
    acquisition: str = ""
    date: date
    note_id: Optional[str] = None
    fashion_id: int = 0
    item_id: int = 0
    owned: bool = True
    category: str = ""
    type_id: int = 0
    max_level: int = 0
    show_max_level: int = 0
    is_max_level: bool = False
    is_forever: bool = False
    dress: bool = False
    condition: str = ""
    knowledge_source: str = "runtime_memory"
    catalog_icon: str = ""
    catalog_description: str = ""
    catalog_effect_description: str = ""
    catalog_quality_name: str = ""
    catalog_quality_color: str = ""


class FanxiuWardrobeHallSnapshot(BaseModel):
    shizhuang: List[FanxiuWardrobeItem] = Field(default_factory=list)
    wuqi: List[FanxiuWardrobeItem] = Field(default_factory=list)
    huanshen: List[FanxiuWardrobeItem] = Field(default_factory=list)
    beishi: List[FanxiuWardrobeItem] = Field(default_factory=list)
    yuqi: List[FanxiuWardrobeItem] = Field(default_factory=list)
    runtime_source: str = ""
    runtime_complete: bool = False
    runtime_error: str = ""
    runtime_updated_at: float = 0
    runtime_item_count: int = 0
    runtime_owned_count: int = 0
    runtime_debug: dict[str, Any] = Field(default_factory=dict)


class FanxiuSpiritBeastHallSnapshot(BaseModel):
    lingshou: List[FanxiuWardrobeItem] = Field(default_factory=list)
    shengshou: List[FanxiuWardrobeItem] = Field(default_factory=list)


class FanxiuGameRichTextSegment(BaseModel):
    text: str = ""
    color: str = ""
    role: str = ""


class FanxiuMagicTreasureGradient(BaseModel):
    pin: int = 0
    level: int = 0
    pin_label: str = ""
    unlock_label: str = ""
    skill_name: str = ""
    summary_description: str = ""
    summary_segments: List[FanxiuGameRichTextSegment] = Field(default_factory=list)
    effect_description: str = ""
    effect_segments: List[FanxiuGameRichTextSegment] = Field(default_factory=list)
    schedule_description: str = ""
    schedule_segments: List[FanxiuGameRichTextSegment] = Field(default_factory=list)
    active: bool = False
    current: bool = False


class FanxiuMagicTreasureUpgradeEffect(BaseModel):
    stage: int = 0
    description: str = ""
    segments: List[FanxiuGameRichTextSegment] = Field(default_factory=list)
    unlocked: bool = False
    current: bool = False


class FanxiuMagicTreasureItem(FanxiuWardrobeItem):
    talisman_id: int = 0
    owned: bool = True
    category: str = "法宝"
    wujing_level: int = 0
    mix_level: int = 0
    bind_id: int = 0
    num: int = 0
    knowledge_source: str = "runtime_memory"
    catalog_item_id: Optional[int] = None
    catalog_name: str = ""
    catalog_icon: str = ""
    catalog_description: str = ""
    catalog_effect_description: str = ""
    catalog_quality: Optional[int] = None
    catalog_quality_name: str = ""
    catalog_quality_color: str = ""
    catalog_refine_item_id: Optional[int] = None
    catalog_refine_name: str = ""
    original_effect: str = ""
    upgrade_effects: List[FanxiuMagicTreasureUpgradeEffect] = Field(default_factory=list)
    shenlian_effect: str = ""
    shenlian_effect_segments: List[FanxiuGameRichTextSegment] = Field(default_factory=list)
    shenlian_schedule: str = ""
    shenlian_schedule_segments: List[FanxiuGameRichTextSegment] = Field(default_factory=list)
    shenlian_pin: int = 0
    shenlian_pin_label: str = ""
    shenlian_progress_nodes: int = 0
    shenlian_remaining_nodes: int = 0
    shenlian_next_pin: int = 0
    shenlian_next_level: int = 0
    shenlian_next_label: str = ""
    shenlian_next_skill_name: str = ""
    shenlian_max_pin: int = 0
    shenlian_gradients: List[FanxiuMagicTreasureGradient] = Field(default_factory=list)


class FanxiuMagicTreasureHallSnapshot(BaseModel):
    fabao: List[FanxiuMagicTreasureItem] = Field(default_factory=list)
    xiantiangubao: List[FanxiuMagicTreasureItem] = Field(default_factory=list)
    houtiangubao: List[FanxiuMagicTreasureItem] = Field(default_factory=list)
    runtime_source: str = ""
    runtime_complete: bool = False
    runtime_error: str = ""
    runtime_updated_at: float = 0
    runtime_item_count: int = 0
    runtime_debug: dict[str, Any] = Field(default_factory=dict)


def spirit_artifact_basic_scores(effects: list[dict[str, Any]], part: int) -> dict[str, float]:
    """普通属性基础分：当前词条 max 对应本部位的 100/150 分，不借用突破后常量。

    type=3 特殊属性不套倍率；配置缺失和重复同名不猜分母，省略该项以保留未知。
    不截断巅峰加成后的实际分数，也不把颜色品质当作分数。
    """
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    if part not in range(1, 7):
        return {}
    result: dict[str, float] = {}
    seen = set()
    for effect in effects:
        name = effect.get("name") or effect.get("official_name")
        if not name:
            continue
        if name in seen:
            result.pop(name, None)
            continue
        seen.add(name)
        if effect.get("type") not in (1, 2):
            continue
        try:
            maximum = Decimal(str(effect.get("normal_max")))
            value = Decimal(str(effect.get("value")))
        except InvalidOperation:
            continue
        if not maximum.is_finite() or maximum <= 0 or not value.is_finite() or value < 0:
            continue
        score = value / maximum * (150 if part >= 5 else 100)
        result[name] = float(score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return result


def classify_spirit_artifact_stage(*, rank: int, base_id: int, is_break: bool | None,
                                    effects: list[dict[str, Any]], a_codes: list[str]) -> str:
    """纯分类：未知事实不等于失败；已突破优先检查 A 集合，与阶数无关。"""
    if is_break is True:
        # 六个槽位必须齐全，缺 code 无法确认缺的是 A 还是普通属性。
        if not a_codes or len(effects) != 6 or any(
            (not effect.get("code") and effect.get("type") != 3) or "affix" not in effect for effect in effects
        ):
            return "待识别"
        full_codes = {effect.get("code") for effect in effects if effect["affix"] in ("满", "巅", "颠")}
        if not set(a_codes).issubset(full_codes):
            return "错升"
        names = {effect.get("name") or effect.get("official_name") for effect in effects}
        if "灵器无双" not in names:
            return "突破"
        if "混沌道威" not in names:
            return "无双"
        return "巅峰" if any(effect["affix"] in ("巅", "颠") for effect in effects) else "道威"
    quality = base_id % 100
    if not base_id or quality not in range(1, 7) or rank < 0:
        return "待识别"
    if quality < 6:
        return "初始"
    if is_break is None:
        return "待识别"
    return "初始" if rank < 6 else "预备"


class FanxiuSpiritArtifactPartRow(BaseModel):
    order: int = 0
    part_name: str = ""
    rank: int = 0
    realm: int = 0
    artifact_peerless_1: int = 0
    artifact_peerless_2: int = 0
    chaos_power: str = ""
    attack: str = ""
    stat_raw_values: dict[str, str] = Field(default_factory=dict)
    exclusive_stats: dict[str, str] = Field(default_factory=dict)
    exclusive_stat_raw_values: dict[str, str] = Field(default_factory=dict)
    spirit_power: str = ""
    health: str = ""
    defense: str = ""
    runtime_base_id: int = 0
    runtime_item_id: str = ""
    runtime_ware_id: int = 0
    runtime_part: int = 0
    runtime_refine_num: int = 0
    runtime_is_break: bool | None = None
    runtime_effects: List[dict[str, Any]] = Field(default_factory=list)

    runtime_empty_slot: bool | None = None
    runtime_observation: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def basic_scores(self) -> dict[str, float]:
        """按官方属性名称投影，旧百分比字段仅用于缺 Runtime 配置的兼容显示。"""
        return spirit_artifact_basic_scores(self.runtime_effects, self.runtime_part)

    @computed_field
    @property
    def a_codes(self) -> list[str]:
        """只读静态配置投影；缺配置保持未知，不接受前端传来的策略集合。"""
        from .spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
        try:
            rules = load_spirit_artifact_wash_rules()
            item = rules["items_by_base_id"].get(self.runtime_base_id)
            if not item:
                return []
            ware = item["type"]
            if self.runtime_ware_id and self.runtime_ware_id != ware:
                return []
            return list(rules["wares"][ware]["a_codes"])
        except (OSError, ValueError):
            return []

    @computed_field
    @property
    def stage(self) -> str:
        """培养完成按本灵器全部 A 满值；客户端四仙品准入不用于倒推错升。"""
        if self.runtime_empty_slot is True:
            # 只有服务器明确空槽才归 0 阶；旧数据字段缺失仍是待识别。
            return "初始" if (not self.runtime_item_id and not self.runtime_base_id
                             and self.rank == 0 and not self.runtime_effects) else "待识别"
        return classify_spirit_artifact_stage(
            rank=self.rank, base_id=self.runtime_base_id,
            is_break=self.runtime_is_break, effects=self.runtime_effects,
            a_codes=self.a_codes if self.runtime_is_break is True else [],
        )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_peerless(cls, data: Any) -> Any:
        if isinstance(data, dict) and "artifact_peerless_1" not in data:
            legacy_value = data.get("aura_peerless", data.get("auraPeerless"))
            if legacy_value is not None:
                return {**data, "artifact_peerless_1": legacy_value}
        return data


class FanxiuSpiritArtifactItem(BaseModel):
    order: int = 0
    name: str = ""
    rows: List[FanxiuSpiritArtifactPartRow] = Field(default_factory=list)


class FanxiuSpiritArtifactMarketItem(BaseModel):
    order: int = 0
    artifact_name: str = ""
    part_name: str = ""
    cost: int = 80


class FanxiuSpiritArtifactStorageBagChoice(BaseModel):
    order: int = 0
    raw_name: str = ""
    artifact_name: str = ""
    part_name: str = ""


class FanxiuSpiritArtifactStorageBagItem(BaseModel):
    order: int = 0
    title: str = ""
    quantity: int = 0
    choices: List[FanxiuSpiritArtifactStorageBagChoice] = Field(default_factory=list)


class FanxiuSpiritArtifactHallSnapshot(BaseModel):
    artifacts: List[FanxiuSpiritArtifactItem] = Field(default_factory=list)
    market_currency_count: int = 0
    market_items: List[FanxiuSpiritArtifactMarketItem] = Field(default_factory=list)
    storage_bag_items: List[FanxiuSpiritArtifactStorageBagItem] = Field(default_factory=list)
    runtime_observation_scope: str = "full"
    runtime_partial_updated_at: float = 0
    runtime_source: str = ""
    runtime_complete: bool = False
    runtime_error: str = ""
    runtime_updated_at: float = 0
    runtime_item_count: int = 0
    runtime_equipped_count: int = 0
    runtime_debug: dict[str, Any] = Field(default_factory=dict)


class FanxiuActivityItem(BaseModel):
    id: str
    name: str = ""
    cross_count: int = 0
    start_date: date
    end_date: date
    note_id: Optional[str] = None


class FanxiuActivityListSnapshot(BaseModel):
    items: List[FanxiuActivityItem] = Field(default_factory=list)
