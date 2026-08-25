from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from sqlmodel import Session


RankBindingSource = Literal[
    "activity_follow",
    "activity_self_or_follow",
    "cross_count_formula",
    "fixed",
]
RankScopeRole = Literal["primary", "comparative"]
RankSubject = Literal["role", "server", "team"]
RankRowMode = Literal["key_points", "full_observed"]


@dataclass(frozen=True)
class RankActivityIdBinding:
    """Declarative rule for resolving one game ranking activity ID."""

    source: RankBindingSource
    follow_index: int | None = None
    base_id: int | None = None
    cross_count_multiplier: int = 100
    suffix: int | None = None
    fixed_id: int | None = None

    def __post_init__(self) -> None:
        if self.source in {"activity_follow", "activity_self_or_follow"}:
            if self.follow_index is None or self.follow_index < 0:
                raise ValueError(f"{self.source} 榜单绑定必须声明非负 follow_index")
            if self.base_id is not None or self.suffix is not None or self.fixed_id is not None:
                raise ValueError("activity_follow 榜单绑定不能声明公式字段")
            return
        if self.source == "cross_count_formula":
            if self.base_id is None or self.suffix is None:
                raise ValueError("cross_count_formula 榜单绑定必须声明 base_id 和 suffix")
            if self.cross_count_multiplier <= 0:
                raise ValueError("榜单跨服系数必须为正数")
            if self.follow_index is not None or self.fixed_id is not None:
                raise ValueError("cross_count_formula 榜单绑定不能声明 follow_index")
            return
        if self.source == "fixed":
            if self.fixed_id is None or int(self.fixed_id) <= 0:
                raise ValueError("fixed 榜单绑定必须声明正数 fixed_id")
            if any(value is not None for value in (self.follow_index, self.base_id, self.suffix)):
                raise ValueError("fixed 榜单绑定不能声明其它绑定字段")
            return
        raise ValueError(f"未知榜单 ID 绑定来源：{self.source}")

    def resolve(
        self,
        *,
        activity_follow: tuple[int, ...] = (),
        activity_id: int | None = None,
        cross_count: int | None = None,
    ) -> int:
        if self.source in {"activity_follow", "activity_self_or_follow"}:
            index = int(self.follow_index or 0)
            if index >= len(activity_follow):
                if self.source == "activity_self_or_follow" and int(activity_id or 0) > 0:
                    return int(activity_id or 0)
                raise ValueError(f"活动 follow 缺少索引 {index} 对应的榜单 ID")
            return int(activity_follow[index])
        if self.source == "fixed":
            return int(self.fixed_id or 0)
        if cross_count is None or int(cross_count) <= 0:
            raise ValueError("公式榜单 ID 绑定需要正数 cross_count")
        return (
            int(self.base_id or 0)
            + int(cross_count) * int(self.cross_count_multiplier)
            + int(self.suffix or 0)
        )


@dataclass(frozen=True)
class RankScopeSpec:
    scope: str
    required: bool
    accepted_vo_types: tuple[str, ...]
    activity_id: RankActivityIdBinding
    label: str = ""
    role: RankScopeRole | None = None
    subject: RankSubject = "role"
    row_mode: RankRowMode = "key_points"
    reward_tiers_enabled: bool = True

    @property
    def effective_label(self) -> str:
        return self.label.strip() or self.scope.strip()

    @property
    def effective_role(self) -> RankScopeRole:
        # Compatibility for the original four-field constructor: the required
        # scope was the primary board and optional scopes were companions.
        return self.role or ("primary" if self.required else "comparative")

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("榜单 scope 不能为空")
        if not self.accepted_vo_types or any(
            not value.strip() for value in self.accepted_vo_types
        ):
            raise ValueError(f"榜单 {self.scope} 必须声明可接受的 VO 类型")
        if len(set(self.accepted_vo_types)) != len(self.accepted_vo_types):
            raise ValueError(f"榜单 {self.scope} 的 VO 类型不能重复")
        if not self.effective_label:
            raise ValueError(f"榜单 {self.scope} 的展示名称不能为空")


@dataclass(frozen=True)
class ShopSpec:
    base_id: int
    currency_type: int
    required_on_explicit_collect: bool = True

    def __post_init__(self) -> None:
        if self.base_id <= 0:
            raise ValueError("活动商店 base_id 必须为正数")
        if self.currency_type <= 0:
            raise ValueError("活动商店 currency_type 必须为正数")


@dataclass(frozen=True)
class PageContract:
    page_kind: str
    ranking_scopes: tuple[str, ...]
    has_shop: bool
    allow_collect: bool = True
    allow_priority: bool = True
    allow_lock: bool = True
    materialize_on_get: bool = True

    def __post_init__(self) -> None:
        if not self.page_kind.strip():
            raise ValueError("页面类型不能为空")
        if len(set(self.ranking_scopes)) != len(self.ranking_scopes):
            raise ValueError("页面榜单 scope 不能重复")
        if not self.has_shop and (self.allow_priority or self.allow_lock):
            raise ValueError("无商店页面不能开放兑换优先级或锁定能力")


@runtime_checkable
class ExchangeActivityAdapter(Protocol):
    """Narrow bridge to an existing activity-specific collection function."""

    def collect_activity(
        self,
        session: Session,
        *,
        activity_id: str,
    ) -> Any: ...


@runtime_checkable
class ExchangeActivityMaterializer(Protocol):
    """Optional DB projection used before a page has an activity row to select."""

    def materialize_activity(self, session: Session) -> str: ...


@runtime_checkable
class ExchangeOccurrenceShopAdapter(Protocol):
    """Optional occurrence-specific shop identity.

    Most activities use the shop declared on :class:`ExchangeActivitySpec`.
    An activity with distinct server-internal/cross-server shops can override
    that identity without leaking activity-specific branching into the shared
    lifecycle core.
    """

    def resolve_occurrence_shop(self, *, cross_count: int) -> ShopSpec: ...


@runtime_checkable
class ResourceRankingTaskAdapter(Protocol):
    """Optional family capability for server-declared task milestones."""

    def load_tasks(
        self,
        session: Session,
        *,
        activity_id: str,
    ) -> Any: ...


@runtime_checkable
class ResourceRankingResourceAdapter(Protocol):
    """Optional family capability for resource inventory snapshots."""

    def load_resources(self, session: Session, *, activity_id: str) -> Any: ...

    def collect_resources(self, session: Session, *, activity_id: str) -> Any: ...


@dataclass(frozen=True)
class ExchangeActivitySpec:
    activity_type: str
    label: str
    worldline_vo_types: tuple[str, ...]
    currency_type: int
    currency_name: str
    rank_scopes: tuple[RankScopeSpec, ...]
    shop: ShopSpec | None
    page: PageContract
    adapter: ExchangeActivityAdapter

    def __post_init__(self) -> None:
        if not self.activity_type.strip():
            raise ValueError("activity_type 不能为空")
        if not self.label.strip():
            raise ValueError(f"活动 {self.activity_type} 的 label 不能为空")
        if not self.worldline_vo_types or any(
            not value.strip() for value in self.worldline_vo_types
        ):
            raise ValueError(f"活动 {self.activity_type} 必须声明 worldline VO")
        if not self.currency_name.strip():
            raise ValueError(f"活动 {self.activity_type} 的资源名称不能为空")
        if self.shop is not None and self.currency_type <= 0:
            raise ValueError(f"活动 {self.activity_type} 的商店币种声明无效")
        if self.shop is None and self.currency_type < 0:
            raise ValueError(f"活动 {self.activity_type} 的资源类型不能为负数")
        scopes = tuple(item.scope for item in self.rank_scopes)
        if not scopes or len(set(scopes)) != len(scopes):
            raise ValueError(f"活动 {self.activity_type} 的榜单 scope 必须唯一且非空")
        if set(self.page.ranking_scopes) != set(scopes):
            raise ValueError(
                f"活动 {self.activity_type} 的页面榜单 scope 与活动声明不一致"
            )
        primary_scopes = [
            item for item in self.rank_scopes if item.effective_role == "primary"
        ]
        if len(primary_scopes) != 1 or not primary_scopes[0].required:
            raise ValueError(
                f"活动 {self.activity_type} 必须声明且仅声明一个必需 primary 榜单"
            )
        if self.page.has_shop != (self.shop is not None):
            raise ValueError(f"活动 {self.activity_type} 的页面商店能力与商店声明不一致")
        if self.shop is not None and self.shop.currency_type != self.currency_type:
            raise ValueError(f"活动 {self.activity_type} 的商店币种与活动币种不一致")
        if not isinstance(self.adapter, ExchangeActivityAdapter):
            raise ValueError(f"活动 {self.activity_type} 的 adapter 不符合协议")
