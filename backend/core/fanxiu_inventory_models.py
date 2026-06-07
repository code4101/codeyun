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


class FanxiuWardrobeHallSnapshot(BaseModel):
    shizhuang: List[FanxiuWardrobeItem] = Field(default_factory=list)
    wuqi: List[FanxiuWardrobeItem] = Field(default_factory=list)
    huanshen: List[FanxiuWardrobeItem] = Field(default_factory=list)
    beishi: List[FanxiuWardrobeItem] = Field(default_factory=list)
    yuqi: List[FanxiuWardrobeItem] = Field(default_factory=list)


class FanxiuSpiritBeastHallSnapshot(BaseModel):
    lingshou: List[FanxiuWardrobeItem] = Field(default_factory=list)
    shengshou: List[FanxiuWardrobeItem] = Field(default_factory=list)


class FanxiuMagicTreasureHallSnapshot(BaseModel):
    fabao: List[FanxiuWardrobeItem] = Field(default_factory=list)
    xiantiangubao: List[FanxiuWardrobeItem] = Field(default_factory=list)
    houtiangubao: List[FanxiuWardrobeItem] = Field(default_factory=list)


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


class FanxiuActivityItem(BaseModel):
    id: str
    name: str = ""
    cross_count: int = 0
    start_date: date
    end_date: date
    note_id: Optional[str] = None


class FanxiuActivityListSnapshot(BaseModel):
    items: List[FanxiuActivityItem] = Field(default_factory=list)


class FanxiuModaoInvasionExchangeItem(BaseModel):
    id: str
    name: str = ""
    magic_crystal_cost: int = 0
    purchase_limit: int = 0
    checked: bool = False


class FanxiuModaoInvasionPersonalRankingItem(BaseModel):
    id: str
    rank: int = 0
    name: str = ""
    plane: str = ""
    merit: int = 0


class FanxiuModaoInvasionRecord(BaseModel):
    id: str
    activity_id: str = ""
    label: str = ""
    personal_rankings: List[FanxiuModaoInvasionPersonalRankingItem] = Field(default_factory=list)
    items: List[FanxiuModaoInvasionExchangeItem] = Field(default_factory=list)


class FanxiuModaoInvasionSnapshot(BaseModel):
    records: List[FanxiuModaoInvasionRecord] = Field(default_factory=list)


class FanxiuShouyuanExplorationExchangeItem(BaseModel):
    id: str
    name: str = ""
    magic_crystal_cost: int = 0
    purchase_limit: int = 0
    checked: bool = False


class FanxiuShouyuanExplorationPersonalRankingItem(BaseModel):
    id: str
    rank: int = 0
    name: str = ""
    plane: str = ""
    merit: int = 0


class FanxiuShouyuanExplorationIncomeSpeedItem(BaseModel):
    id: str
    captured_date: str = ""
    search_count: int = 0
    beast_crystal: int = 0
    score: int = 0
    merit: int = 0
    remark: str = ""


class FanxiuShouyuanExplorationConsumptionEvaluationItem(BaseModel):
    id: str
    label: str = ""
    current: float = 0
    target: float = 0
    speed: float = 0


class FanxiuShouyuanExplorationRecord(BaseModel):
    id: str
    activity_id: str = ""
    label: str = ""
    personal_rankings: List[FanxiuShouyuanExplorationPersonalRankingItem] = Field(default_factory=list)
    income_speeds: List[FanxiuShouyuanExplorationIncomeSpeedItem] = Field(default_factory=list)
    consumption_evaluations: List[FanxiuShouyuanExplorationConsumptionEvaluationItem] = Field(default_factory=list)
    items: List[FanxiuShouyuanExplorationExchangeItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationSnapshot(BaseModel):
    records: List[FanxiuShouyuanExplorationRecord] = Field(default_factory=list)


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


class FanxiuModaoInvasionOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuModaoInvasionExchangeItem] = Field(default_factory=list)


class FanxiuModaoInvasionPersonalRankingOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuModaoInvasionPersonalRankingItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuShouyuanExplorationExchangeItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationPersonalRankingOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    items: List[FanxiuShouyuanExplorationPersonalRankingItem] = Field(default_factory=list)


class FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse(BaseModel):
    lines: List[str] = Field(default_factory=list)
    item: FanxiuShouyuanExplorationIncomeSpeedItem


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
