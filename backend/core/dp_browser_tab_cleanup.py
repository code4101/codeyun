from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from backend.core.settings import get_settings


DP_BROWSER_TAB_CLEANUP_TASK_KEY = "dp_browser_tab_cleanup"
DP_BROWSER_TAB_CLEANUP_DEFAULT_PORT = 9222
DP_BROWSER_TAB_CLEANUP_STATE_VERSION = 1
DP_BROWSER_TAB_CLEANUP_DEFAULT_ALLOWED_HOSTS = (
    "pay.weixin.qq.com",
    "admin.xiaoe-tech.com",
    "shop.xiaoe-tech.com",
    "*.xiaoe-tech.com",
)
DP_BROWSER_TAB_CLEANUP_PROTECTED_KEYWORDS = (
    "登录",
    "登陆",
    "扫码",
    "二维码",
    "验证码",
    "验证",
    "安全验证",
    "授权",
    "确认",
    "login",
    "signin",
    "captcha",
    "verify",
    "oauth",
    "authorize",
    "qr",
)


@dataclass(frozen=True)
class DpBrowserTabCleanupConfig:
    host: str = "127.0.0.1"
    port: int = DP_BROWSER_TAB_CLEANUP_DEFAULT_PORT
    allowed_hosts: tuple[str, ...] = DP_BROWSER_TAB_CLEANUP_DEFAULT_ALLOWED_HOSTS
    protected_keywords: tuple[str, ...] = DP_BROWSER_TAB_CLEANUP_PROTECTED_KEYWORDS
    min_candidate_age_seconds: float = 60 * 60
    min_seen_count: int = 2
    max_tabs_per_url: int = 1
    max_close_per_run: int = 10
    request_timeout_seconds: float = 3.0
    dry_run: bool = False


@dataclass
class DpBrowserTabCleanupDecision:
    tabs_before: int
    tracked_tabs: int
    eligible_tabs: int
    candidate_tabs: int
    close_ids: list[str]
    close_reasons: dict[str, str] = field(default_factory=dict)
    kept_domain_tabs: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    limited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tabs_before": self.tabs_before,
            "tracked_tabs": self.tracked_tabs,
            "eligible_tabs": self.eligible_tabs,
            "candidate_tabs": self.candidate_tabs,
            "close_ids": list(self.close_ids),
            "close_reasons": dict(self.close_reasons),
            "kept_domain_tabs": dict(self.kept_domain_tabs),
            "skipped": dict(self.skipped),
            "limited": self.limited,
        }


def get_dp_browser_tab_cleanup_state_path() -> Path:
    return get_settings().data_dir / "scheduler" / "dp_browser_tab_cleanup_state.json"


def load_dp_browser_tab_cleanup_config() -> DpBrowserTabCleanupConfig:
    hosts = _split_csv(os.getenv("CODEYUN_DP_TAB_CLEANUP_ALLOWED_HOSTS"))
    protected_keywords = _split_csv(os.getenv("CODEYUN_DP_TAB_CLEANUP_PROTECTED_KEYWORDS"))
    return DpBrowserTabCleanupConfig(
        host=(os.getenv("CODEYUN_DP_BROWSER_DEBUG_HOST") or "127.0.0.1").strip() or "127.0.0.1",
        port=_env_int("CODEYUN_DP_BROWSER_DEBUG_PORT", DP_BROWSER_TAB_CLEANUP_DEFAULT_PORT),
        allowed_hosts=hosts or DP_BROWSER_TAB_CLEANUP_DEFAULT_ALLOWED_HOSTS,
        protected_keywords=protected_keywords or DP_BROWSER_TAB_CLEANUP_PROTECTED_KEYWORDS,
        min_candidate_age_seconds=max(0, _env_float("CODEYUN_DP_TAB_CLEANUP_MIN_AGE_SECONDS", 60 * 60)),
        min_seen_count=max(1, _env_int("CODEYUN_DP_TAB_CLEANUP_MIN_SEEN_COUNT", 2)),
        max_tabs_per_url=max(1, _env_int("CODEYUN_DP_TAB_CLEANUP_MAX_TABS_PER_URL", 1)),
        max_close_per_run=max(0, _env_int("CODEYUN_DP_TAB_CLEANUP_MAX_CLOSE_PER_RUN", 10)),
        request_timeout_seconds=max(0.5, _env_float("CODEYUN_DP_TAB_CLEANUP_REQUEST_TIMEOUT_SECONDS", 3.0)),
        dry_run=_env_flag("CODEYUN_DP_TAB_CLEANUP_DRY_RUN", False),
    )


def run_dp_browser_tab_cleanup(
    *,
    config: DpBrowserTabCleanupConfig | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    resolved_config = config or load_dp_browser_tab_cleanup_config()
    resolved_state_path = state_path or get_dp_browser_tab_cleanup_state_path()
    state = _read_state(resolved_state_path)

    try:
        tabs = fetch_chrome_debug_tabs(resolved_config)
    except (OSError, URLError, TimeoutError) as exc:
        result = {
            "status": "skipped",
            "reason": "debug_port_unavailable",
            "detail": str(exc),
            "host": resolved_config.host,
            "port": resolved_config.port,
        }
        print(f"DP browser tab cleanup skipped: {result}")
        return result

    decision = plan_dp_browser_tab_cleanup(tabs, state, now=time.time(), config=resolved_config)
    closed_ids: list[str] = []
    close_errors: dict[str, str] = {}
    if not resolved_config.dry_run:
        for tab_id in decision.close_ids:
            try:
                close_chrome_debug_tab(tab_id, resolved_config)
                closed_ids.append(tab_id)
            except Exception as exc:  # pragma: no cover - depends on the live browser.
                close_errors[tab_id] = str(exc)
    tabs_after: int | None = None
    if closed_ids and not resolved_config.dry_run:
        try:
            tabs_after = len(fetch_chrome_debug_tabs(resolved_config))
        except Exception:
            tabs_after = None

    decision.state["last_closed_ids"] = closed_ids
    decision.state["last_close_errors"] = close_errors
    _write_state(resolved_state_path, decision.state)

    result = decision.to_dict()
    result.update(
        {
            "status": "completed",
            "dry_run": resolved_config.dry_run,
            "closed_ids": closed_ids,
            "close_errors": close_errors,
            "closed_count": len(closed_ids),
            "tabs_after": tabs_after,
            "host": resolved_config.host,
            "port": resolved_config.port,
            "allowed_hosts": list(resolved_config.allowed_hosts),
            "state_path": str(resolved_state_path),
        }
    )
    print(
        "DP browser tab cleanup finished: "
        f"tabs={result['tabs_before']} eligible={result['eligible_tabs']} "
        f"candidates={result['candidate_tabs']} closed={len(closed_ids)} "
        f"after={tabs_after if tabs_after is not None else '-'} "
        f"dry_run={result['dry_run']}"
    )
    return result


def fetch_chrome_debug_tabs(config: DpBrowserTabCleanupConfig) -> list[dict[str, Any]]:
    url = f"http://{config.host}:{config.port}/json/list"
    with urlopen(url, timeout=config.request_timeout_seconds) as response:
        payload = response.read().decode("utf-8", errors="replace")
    data = json.loads(payload)
    return [dict(item) for item in data] if isinstance(data, list) else []


def close_chrome_debug_tab(tab_id: str, config: DpBrowserTabCleanupConfig) -> None:
    encoded_id = quote(str(tab_id), safe="")
    url = f"http://{config.host}:{config.port}/json/close/{encoded_id}"
    with urlopen(url, timeout=config.request_timeout_seconds) as response:
        response.read()


def plan_dp_browser_tab_cleanup(
    tabs: list[dict[str, Any]],
    state: dict[str, Any] | None,
    *,
    now: float,
    config: DpBrowserTabCleanupConfig | None = None,
) -> DpBrowserTabCleanupDecision:
    resolved_config = config or DpBrowserTabCleanupConfig()
    previous_state = state if isinstance(state, dict) else {}
    previous_tabs = previous_state.get("tabs") if isinstance(previous_state.get("tabs"), dict) else {}
    records: list[dict[str, Any]] = []
    next_tabs: dict[str, dict[str, Any]] = {}
    skipped: dict[str, int] = {}

    for index, raw_tab in enumerate(tabs):
        tab = _normalize_tab(raw_tab)
        tab["observed_index"] = index
        if not tab["id"]:
            _increment(skipped, "missing_id")
            continue
        previous = previous_tabs.get(tab["id"]) if isinstance(previous_tabs.get(tab["id"]), dict) else {}
        record = _merge_tab_state(tab, previous, now)
        next_tabs[tab["id"]] = record
        records.append(record)

    eligible_records: list[dict[str, Any]] = []
    for record in records:
        reason = _skip_reason(record, resolved_config)
        if reason:
            record["candidate_since"] = None
            _increment(skipped, reason)
            continue
        eligible_records.append(record)

    keep_by_domain = _select_domain_keepers(eligible_records)
    close_ids: list[str] = []
    close_reasons: dict[str, str] = {}
    current_candidate_ids: set[str] = set()

    for group_key, group_records in _group_by_url(eligible_records).items():
        if len(group_records) <= resolved_config.max_tabs_per_url:
            continue

        keep_ids = {
            item["id"]
            for item in sorted(group_records, key=_tab_recency_key, reverse=True)[: resolved_config.max_tabs_per_url]
        }
        for domain, keep_id in keep_by_domain.items():
            if group_records and group_records[0]["domain"] == domain:
                keep_ids.add(keep_id)

        for record in sorted(group_records, key=_tab_recency_key):
            tab_id = record["id"]
            if tab_id in keep_ids:
                continue
            current_candidate_ids.add(tab_id)
            candidate_since = _as_float(record.get("candidate_since"))
            if candidate_since is None:
                record["candidate_since"] = now
                continue
            candidate_age = now - candidate_since
            if record["seen_count"] < resolved_config.min_seen_count:
                continue
            if candidate_age < resolved_config.min_candidate_age_seconds:
                continue
            if len(close_ids) >= resolved_config.max_close_per_run:
                continue
            close_ids.append(tab_id)
            close_reasons[tab_id] = f"duplicate url retained for {candidate_age:.0f}s: {group_key}"

    limited = len(current_candidate_ids) > len(close_ids) and len(close_ids) >= resolved_config.max_close_per_run
    for record in records:
        if record["id"] not in current_candidate_ids:
            record["candidate_since"] = None

    next_state = {
        "version": DP_BROWSER_TAB_CLEANUP_STATE_VERSION,
        "last_run_at": now,
        "tabs": next_tabs,
        "last_decision": {
            "close_ids": close_ids,
            "candidate_ids": sorted(current_candidate_ids),
            "kept_domain_tabs": keep_by_domain,
            "skipped": skipped,
            "limited": limited,
        },
    }
    return DpBrowserTabCleanupDecision(
        tabs_before=len(tabs),
        tracked_tabs=len(records),
        eligible_tabs=len(eligible_records),
        candidate_tabs=len(current_candidate_ids),
        close_ids=close_ids,
        close_reasons=close_reasons,
        kept_domain_tabs=keep_by_domain,
        skipped=skipped,
        state=next_state,
        limited=limited,
    )


def _normalize_tab(raw_tab: dict[str, Any]) -> dict[str, Any]:
    url = str(raw_tab.get("url") or "").strip()
    parsed = urlparse(url)
    domain = (parsed.hostname or "").lower()
    return {
        "id": str(raw_tab.get("id") or "").strip(),
        "url": url,
        "normalized_url": _normalize_url(url),
        "title": str(raw_tab.get("title") or "").strip(),
        "type": str(raw_tab.get("type") or "").strip().lower(),
        "attached": bool(raw_tab.get("attached")),
        "domain": domain,
    }


def _merge_tab_state(tab: dict[str, Any], previous: dict[str, Any], now: float) -> dict[str, Any]:
    previous_url = str(previous.get("url") or "")
    previous_title = str(previous.get("title") or "")
    changed = previous_url != tab["url"] or previous_title != tab["title"]
    first_seen_at = _as_float(previous.get("first_seen_at"))
    last_changed_at = _as_float(previous.get("last_changed_at"))
    return {
        **tab,
        "first_seen_at": first_seen_at if first_seen_at is not None else now,
        "last_seen_at": now,
        "last_changed_at": now if changed or last_changed_at is None else last_changed_at,
        "last_observed_index": int(tab.get("observed_index") or 0),
        "seen_count": int(previous.get("seen_count") or 0) + 1,
        "candidate_since": None if changed else previous.get("candidate_since"),
    }


def _skip_reason(record: dict[str, Any], config: DpBrowserTabCleanupConfig) -> str:
    if record.get("type") != "page":
        return "non_page"
    if record.get("attached"):
        return "attached"
    if not str(record.get("normalized_url") or ""):
        return "unsupported_url"
    if not _host_allowed(str(record.get("domain") or ""), config.allowed_hosts):
        return "host_not_allowed"
    text = f"{record.get('title') or ''}\n{record.get('url') or ''}".lower()
    if any(keyword.lower() in text for keyword in config.protected_keywords):
        return "protected_keyword"
    return ""


def _select_domain_keepers(records: list[dict[str, Any]]) -> dict[str, str]:
    keepers: dict[str, str] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(str(record.get("domain") or ""), []).append(record)
    for domain, items in groups.items():
        if not domain:
            continue
        keepers[domain] = sorted(items, key=_tab_recency_key, reverse=True)[0]["id"]
    return keepers


def _group_by_url(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = f"{record.get('domain') or ''}\n{record.get('normalized_url') or ''}"
        groups.setdefault(key, []).append(record)
    return groups


def _tab_recency_key(record: dict[str, Any]) -> tuple[float, float, float, int, str]:
    return (
        _as_float(record.get("last_changed_at")) or 0.0,
        _as_float(record.get("first_seen_at")) or 0.0,
        _as_float(record.get("last_seen_at")) or 0.0,
        int(record.get("last_observed_index") or 0),
        str(record.get("id") or ""),
    )


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    netloc = parsed.hostname.lower()
    if parsed.port and not ((parsed.scheme == "http" and parsed.port == 80) or (parsed.scheme == "https" and parsed.port == 443)):
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=netloc).geturl()


def _host_allowed(host: str, patterns: tuple[str, ...]) -> bool:
    normalized = host.lower().strip()
    for pattern in patterns:
        item = pattern.lower().strip()
        if not item:
            continue
        if item.startswith("*.") and normalized.endswith(item[1:]):
            return True
        if normalized == item:
            return True
    return False


def _read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = int(counter.get(key) or 0) + 1
