from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator


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


class FanxiuMagicTreasureOcrImportResponse(BaseModel):
    section_key: str
    lines: List[str] = Field(default_factory=list)
    item: FanxiuWardrobeItem


class FanxiuSpiritArtifactRankPart(BaseModel):
    part_name: str
    rank: int = 0
    realm: int = 0
    quality: str = ""
    background_color: str = ""


class FanxiuSpiritArtifactRankRecognitionResponse(BaseModel):
    matched: bool = False
    reason: str = ""
    artifact_name: str = ""
    title_text: str = ""
    lines: List[str] = Field(default_factory=list)
    parts: List[FanxiuSpiritArtifactRankPart] = Field(default_factory=list)


class FanxiuSpiritArtifactAttributeValue(BaseModel):
    label: str = ""
    percent: str = ""
    raw_value: str = ""
    source_text: str = ""


class FanxiuSpiritArtifactAttributeRecognitionResponse(BaseModel):
    matched: bool = False
    reason: str = ""
    artifact_name: str = ""
    part_name: str = ""
    title_text: str = ""
    lines: List[str] = Field(default_factory=list)
    artifact_peerless_1: int = 0
    artifact_peerless_2: int = 0
    common_stats: dict[str, str] = Field(default_factory=dict)
    exclusive_stats: dict[str, str] = Field(default_factory=dict)
    attributes: List[FanxiuSpiritArtifactAttributeValue] = Field(default_factory=list)


class FanxiuSpiritArtifactMarketRecognitionResponse(BaseModel):
    matched: bool = False
    reason: str = ""
    market_currency_count: int = 0
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuSpiritArtifactMarketItem] = Field(default_factory=list)


class FanxiuSpiritArtifactStorageBagRecognitionResponse(BaseModel):
    matched: bool = False
    reason: str = ""
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuSpiritArtifactStorageBagItem] = Field(default_factory=list)


class FanxiuFormationRequirementImportItem(BaseModel):
    text: str
    effect_text: str = ""


class FanxiuFormationEffectDetailImportItem(BaseModel):
    effect_name: str
    effect_detail: str = ""


class FanxiuFormationRequirementOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    requirements: List[FanxiuFormationRequirementImportItem] = Field(default_factory=list)
    effect_details: List[FanxiuFormationEffectDetailImportItem] = Field(default_factory=list)
