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
    runtime_is_break: bool = False
    runtime_effects: List[dict[str, Any]] = Field(default_factory=list)

    @computed_field
    @property
    def stage(self) -> str:
        """按洗灵规则由高到低判定阶段；缺少词缀事实时不推断错升。"""
        if self.rank <= 0:
            return "待识别"
        if self.rank < 6:
            return "普通"
        effects = self.runtime_effects
        if not effects or any("affix" not in effect for effect in effects):
            return "待识别"
        if sum(effect["affix"] in ("满", "巅", "颠") for effect in effects) < 4:
            return "错升"
        names = {effect.get("name") or effect.get("official_name") for effect in effects}
        if "灵器无双" not in names:
            return "升阶"
        if "混沌道威" not in names:
            return "无双"
        return "巅峰" if any(effect["affix"] in ("巅", "颠") for effect in effects) else "道威"

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
