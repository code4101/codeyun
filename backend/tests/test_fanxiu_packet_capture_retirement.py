from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MUSEUM_PACKAGE = REPO_ROOT / "backend/core/fanxiu/history_museum/packet_capture"
PRODUCTION_ROOTS = (
    REPO_ROOT / "backend/app.py",
    REPO_ROOT / "backend/api",
    REPO_ROOT / "backend/core/runtime",
    REPO_ROOT / "backend/core/fanxiu",
    REPO_ROOT / "scripts",
)
FORBIDDEN_IMPORTS = (
    "backend.core.fanxiu.history_museum.packet_capture",
    "backend.core.fanxiu.runtime.capture_runtime",
    "backend.core.fanxiu.runtime.android_proxy",
    "from backend.core.fanxiu.packet.service_runtime import",
    "from backend.core.fanxiu.packet.insight_worker import",
    "from backend.core.fanxiu.packet.activity import",
    "from backend.core.fanxiu.packet.proxy import",
    "from backend.core.fanxiu.packet.capture import",
    "backend.services.fanxiu_packet_daemon",
    "backend.core.fanxiu.packet.tcp_flow",
    "backend.core.fanxiu.packet.decoded_store",
    "backend.core.fanxiu.packet.activity_sync",
    "backend.core.fanxiu.packet.insights",
    "backend.core.fanxiu.packet.current_facts",
    "backend.core.fanxiu.packet.red_packet_state",
    "backend.core.fanxiu.packet.business_store",
    "backend.core.fanxiu.packet.player_profile_store",
    "backend.core.fanxiu.mail.packet_sync",
)
FORBIDDEN_AUTOSTART_MARKERS = (
    "FX_PACKET_SERVICE_AUTOSTART",
    "FX_CAPTURE_RUNTIME_SERVICE_ENABLED",
    "FX_CAPTURE_RUNTIME_WATCHDOG_INTERVAL_SECONDS",
    "fanxiu-capture-runtime",
)
FORBIDDEN_PRODUCTION_CAPTURE_MARKERS = (
    "FanxiuPacketDecodedRecord",
    "list_tcp_streams_with_tshark",
    "extract_tcp_stream_payloads_with_tshark",
    "_capture_decoded_jsons",
    ' / "tcp_captures"',
    '"/fanxiu/packet-capture',
    '"/resources/digitdoor/readyfight-runtime-sample-probe"',
    '"/resources/digitdoor/startgame-runtime-sample-probe"',
    '"/resources/digitdoor/gameplayer-runtime-sample-probe"',
)


def _python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return [path for path in root.rglob("*.py") if "history_museum" not in path.parts]


def test_packet_capture_code_lives_only_in_history_museum() -> None:
    assert (MUSEUM_PACKAGE / "__main__.py").is_file()
    assert (MUSEUM_PACKAGE / "README.md").is_file()
    for retired_path in (
        REPO_ROOT / "backend/services/fanxiu_packet_daemon.py",
        REPO_ROOT / "backend/core/fanxiu/runtime/capture_runtime.py",
        REPO_ROOT / "backend/core/fanxiu/runtime/android_proxy.py",
        REPO_ROOT / "backend/core/fanxiu/packet/service_runtime.py",
        REPO_ROOT / "backend/core/fanxiu/packet/insight_worker.py",
        REPO_ROOT / "backend/core/fanxiu/packet/activity.py",
        REPO_ROOT / "backend/core/fanxiu/packet/proxy.py",
        REPO_ROOT / "backend/core/fanxiu/packet/capture.py",
        REPO_ROOT / "backend/core/fanxiu/packet/tcp_flow.py",
        REPO_ROOT / "backend/core/fanxiu/packet/decoded_store.py",
        REPO_ROOT / "backend/core/fanxiu/packet/activity_sync.py",
        REPO_ROOT / "backend/core/fanxiu/packet/insights.py",
        REPO_ROOT / "backend/core/fanxiu/packet/current_facts.py",
        REPO_ROOT / "backend/core/fanxiu/packet/red_packet_state.py",
        REPO_ROOT / "backend/core/fanxiu/packet/business_store.py",
        REPO_ROOT / "backend/core/fanxiu/packet/player_profile_store.py",
        REPO_ROOT / "backend/core/fanxiu/mail/packet_sync.py",
    ):
        assert not retired_path.exists(), retired_path


def test_production_cannot_import_or_autostart_packet_capture() -> None:
    violations: list[str] = []
    for root in PRODUCTION_ROOTS:
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            for marker in (
                *FORBIDDEN_IMPORTS,
                *FORBIDDEN_AUTOSTART_MARKERS,
                *FORBIDDEN_PRODUCTION_CAPTURE_MARKERS,
            ):
                if marker in text:
                    violations.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    assert violations == []


def test_frontend_has_no_packet_capture_entry_or_client() -> None:
    frontend_root = REPO_ROOT / "frontend/src"
    violations: list[str] = []
    for path in frontend_root.rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".vue", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in (
            "/fanxiu/packet-capture",
            "listFanxiuTcpBusinessEntries",
            "packetProtocolVisibility",
            "activeTab.value === 'packet'",
        ):
            if marker in text:
                violations.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    assert violations == []


def test_manual_museum_entry_requires_explicit_acknowledgement() -> None:
    source = (MUSEUM_PACKAGE / "__main__.py").read_text(encoding="utf-8")
    assert "--i-understand-this-is-retired" in source
    assert "run_fanxiu_packet_service_loop" in source


def test_storage_bag_no_longer_uses_packet_capture_or_persisted_packet_domains() -> None:
    sources = {
        "active business data": REPO_ROOT / "backend/core/fanxiu/business_data.py",
        "legacy packet business store": MUSEUM_PACKAGE / "business_store_legacy.py",
        "packet insights": MUSEUM_PACKAGE / "insights.py",
    }
    violations: list[str] = []
    for label, path in sources.items():
        text = path.read_text(encoding="utf-8")
        for marker in (
            "storage_bag_state",
            "storage_bag_current_item",
            '"storage_bag_item"',
            "apply_fanxiu_storage_bag_events",
            "get_fanxiu_storage_bag_current_state",
        ):
            if marker in text:
                violations.append(f"{label}: {marker}")
    packet_insights = sources["packet insights"].read_text(encoding="utf-8")
    for marker in ("SM_AllBagSyncInfo", "SM_BagSyncInfo", "_extract_bag"):
        if marker in packet_insights:
            violations.append(f"packet insights: {marker}")
    assert violations == []


def test_active_fanxiu_routers_expose_no_packet_capture_routes() -> None:
    from backend.api.fanxiu import router as fanxiu_router
    from backend.api.fanxiu_resources import router as resources_router

    paths = [
        str(getattr(route, "path", "")).lower()
        for router in (fanxiu_router, resources_router)
        for route in router.routes
    ]
    assert [
        path
        for path in paths
        if any(marker in path for marker in ("packet", "pcap", "capture"))
    ] == []
    assert "/business-data/player-profiles" in paths
    assert "/activity-runtime-schedule/latest" in paths
