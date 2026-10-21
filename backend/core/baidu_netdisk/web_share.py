"""Phase-1 dry-run of saving a public share via the official Baidu Netdisk web UI.

``prepare_share`` validates a share request, checks the submission ledger,
attaches to CodeYun's own verified browser (``web.owned_context``: PID,
creation time, profile and port ownership plus the browser lock), optionally
opens the share link and reads back DOM facts for each stage.  It never
presses Save/copy, never types into the page and never writes the ledger.

Share-page DOM selectors have not been observed on the live page yet, so
``SHARE_SELECTORS`` is empty and every stage reports ``selector_unverified``;
the overall outcome is then ``needs_primary_observation`` (never a guessed
success).  Selectors enter this registry only after the primary runtime has
observed them on the own browser.

Request file (JSON)::

    {"share_url": "https://pan.baidu.com/s/<surl>?pwd=<code>",
     "pwd": "<code, optional if in url>", "target_dir": "/abs/dir",
     "items": ["optional root names"]}

Results carry only reason codes, hashes, counts and selector names: the link,
surl, extraction code, target directory and item names are never output.

CLI::

    uv run python -m backend.core.baidu_netdisk.web_share prepare --request FILE [--ledger PATH] [--no-navigate]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import web
from .client import parse_share_url
from .ledger import TransferLedger, transfer_key
from .local_files import validate_file_name, validate_remote_dir

# stage -> selectors observed on the live share page.  Empty until primary observation.
SINGLE_STAGES = ("extraction_input", "extraction_submit", "target_readback")
LIST_STAGES = ("share_rows",)
STAGES = SINGLE_STAGES + LIST_STAGES
SHARE_SELECTORS: dict[str, tuple[str, ...]] = {stage: () for stage in STAGES}

NEXT_EVIDENCE = ("primary runtime must open a synthetic-safe share on the own browser and record, per stage "
                 f"({', '.join(STAGES)}), the observed selector names with match counts only; "
                 "phase-2 Save stays disabled until these are in SHARE_SELECTORS")


@dataclass(frozen=True)
class ShareRequest:
    url: str  # canonical https://pan.baidu.com/s/<surl>, no code
    code: str
    target_dir: str
    items: tuple[str, ...] | None

    @property
    def identity(self) -> dict:
        """Same ledger identity as the native ``save_share`` so both providers dedupe together."""
        return {"link": _hash(self.url), "target_dir": self.target_dir,
                "items": list(self.items) if self.items else "*all*"}

    @property
    def key(self) -> str:
        return transfer_key("save_share", **self.identity)

    @property
    def secrets(self) -> tuple[str, ...]:
        surl = self.url.rsplit("/", 1)[-1]
        found = {self.url, surl, self.code, self.target_dir, *(self.items or ())}
        if surl.startswith("1") and len(surl) > 1:
            found.add(surl[1:])
        return tuple(sorted((s for s in found if s and s != "/"), key=len, reverse=True))

    def public(self) -> dict:
        return {"link": _hash(self.url), "target": _hash(self.target_dir),
                "selection": "explicit" if self.items else "all_root_items",
                "item_count": len(self.items) if self.items else None}


class RequestError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def parse_request(data: object) -> ShareRequest:
    """Validate a request dict; errors carry a fixed reason code, never the input values."""
    if not isinstance(data, dict):
        raise RequestError("request_not_object")
    if set(data) - {"share_url", "pwd", "target_dir", "items"}:
        raise RequestError("unknown_request_field")
    try:
        url, code = parse_share_url(str(data.get("share_url") or ""), data.get("pwd"))
    except ValueError:
        raise RequestError("invalid_share_url") from None
    try:
        target = validate_remote_dir(str(data.get("target_dir") or ""))
    except ValueError:
        raise RequestError("invalid_target_dir") from None
    if target == "/":
        raise RequestError("target_dir_is_root")
    items = data.get("items")
    if items is not None:
        if not isinstance(items, list) or not items or not all(isinstance(n, str) for n in items):
            raise RequestError("invalid_items")
        try:
            items = tuple(validate_file_name(n) or n for n in items)
        except ValueError:
            raise RequestError("invalid_items") from None
        if len({n.casefold() for n in items}) != len(items):
            raise RequestError("duplicate_items")
    return ShareRequest(url, code, target, items)


def load_request(path: str | Path) -> ShareRequest:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RequestError("request_file_unreadable") from None
    return parse_request(data)


# Fixed identifiers (hashes, reason codes, registry selector names) are never rewritten,
# so a 4-char code cannot corrupt them; everything else is scrubbed defensively.
IDENTIFIER_KEYS = {"key", "link", "target", "reason", "outcome", "state", "page_kind", "next", "next_evidence",
                   "selection", "selector", "matches", "evidence"}


def redact(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, dict):
        return {k: v if k in IDENTIFIER_KEYS else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        low = value.lower()
        for secret in secrets:
            while (i := low.find(secret.lower())) >= 0:
                value = value[:i] + "[redacted]" + value[i + len(secret):]
                low = value.lower()
        return value
    return value


# ----- page facts ----------------------------------------------------------------

def page_kind(url: str) -> str:
    """Coarse page class from host/path prefix only (paths carry the surl and are never output)."""
    host = web.official_host(url)
    if host == "passport.baidu.com":
        return "passport"
    if host != "pan.baidu.com":
        return "foreign"
    path = urlsplit(url).path
    if path.startswith("/s/") or path == "/share/init":
        return "share"
    if path.startswith("/disk/"):
        return "disk"
    return "other"


def _count(tab, selector: str) -> int:
    return len(tab.eles(selector, timeout=0) or ())


def inspect_stage(tab, stage: str, selectors: tuple[str, ...]) -> dict:
    """``selector_unverified`` without observed selectors; else ``absent``/``observed``/``ambiguous``."""
    if not selectors:
        return {"state": "selector_unverified"}
    hits = {s: n for s in selectors if (n := _count(tab, s))}
    if not hits:
        return {"state": "absent"}
    if len(hits) > 1 or (stage in SINGLE_STAGES and next(iter(hits.values())) > 1):
        return {"state": "ambiguous", "matches": hits}
    (selector, count), = hits.items()
    return {"state": "observed", "selector": selector, "count": count}


def inspect_share_page(tab, selectors: dict[str, tuple[str, ...]]) -> dict:
    url = str(tab.url or "")
    kind = page_kind(url)
    login = next((s for s in web.LOGIN_SELECTORS if tab.ele(s, timeout=0)), None)
    if login:
        return {"outcome": "login_required", "page_kind": kind, "evidence": login}
    if kind != "share":
        # Captcha, risk-control, expired/foreign redirect: never interpreted further.
        return {"outcome": "unknown_challenge", "page_kind": kind}
    stages = {stage: inspect_stage(tab, stage, tuple(selectors.get(stage) or ())) for stage in STAGES}
    states = {s["state"] for s in stages.values()}
    if "selector_unverified" in states:
        outcome = "needs_primary_observation"
    elif "ambiguous" in states:
        outcome = "ambiguous_selector"
    elif stages["share_rows"]["state"] != "observed" or stages["target_readback"]["state"] != "observed":
        outcome = "inventory_unavailable"
    else:
        outcome = "dry_run_ready"
    return {"outcome": outcome, "page_kind": kind, "stages": stages}


# ----- public entry ----------------------------------------------------------------

def prepare_share(request: ShareRequest | dict, *, dry_run: bool = True, ledger: TransferLedger | None = None,
                  context: Callable[[], object] | None = None,
                  selectors: dict[str, tuple[str, ...]] | None = None, navigate: bool = True) -> dict:
    """Dry-run a share save on the own browser; returns redacted facts and never submits.

    Outcomes: ``invalid_request``, ``unsupported`` (``dry_run=False``),
    ``already_recorded`` (ledger has an open record: do not resubmit),
    ``blocked`` (ownership/lock), ``login_required``, ``unknown_challenge``,
    ``needs_primary_observation``, ``ambiguous_selector``,
    ``inventory_unavailable``, ``dry_run_ready``, ``unknown`` (page fault)."""
    try:
        req = request if isinstance(request, ShareRequest) else parse_request(request)
    except RequestError as exc:
        return {"outcome": "invalid_request", "reason": exc.reason, "submitted": False}
    base = {"key": req.key, "request": req.public(), "dry_run": dry_run, "submitted": False}
    if not dry_run:
        return {**base, "outcome": "unsupported", "reason": "save_disabled_until_primary_dom_observation",
                "next_evidence": NEXT_EVIDENCE}
    if ledger is not None:
        prior = ledger.get(req.key)
        if prior and prior["state"] not in {"failed", "abandoned"}:
            return {**base, "outcome": "already_recorded", "state": prior["state"],
                    "next": "verify_save_share_do_not_resubmit"}
    context = context or web.owned_context
    selectors = SHARE_SELECTORS if selectors is None else selectors
    try:
        with context() as owned:
            result = {**base, "browser": {k: owned.facts.get(k) for k in ("pid", "port", "running")}}
            try:
                tab = owned.main_tab()
                if navigate:
                    tab.get(f"{req.url}?pwd={req.code}")
                result.update(inspect_share_page(tab, selectors))
            except Exception as exc:  # noqa: BLE001 - class name only, page content never leaves
                result.update(outcome="unknown", probe_error=type(exc).__name__)
    except web.OwnershipError as exc:
        return {**base, "outcome": "blocked", "reason": exc.reason}
    except web.WebError:
        return {**base, "outcome": "blocked", "reason": "browser_lock_busy"}
    if result["outcome"] in {"needs_primary_observation", "unknown_challenge", "ambiguous_selector"}:
        result["next_evidence"] = NEXT_EVIDENCE
    return redact(result, req.secrets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run official web share save (phase 1, no submission)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="validate request, check ledger, read back share page DOM facts")
    p.add_argument("--request", required=True, help="JSON request file (values are never printed)")
    p.add_argument("--ledger", help="默认 <data_dir>/baidu_netdisk/ledger.json")
    p.add_argument("--no-navigate", action="store_true", help="inspect the current tab without opening the link")
    args = parser.parse_args(argv)
    try:
        req = load_request(args.request)
    except RequestError as exc:
        print(json.dumps({"outcome": "invalid_request", "reason": exc.reason, "submitted": False}))
        return 2
    if args.ledger:
        ledger_path = Path(args.ledger)
    else:
        from backend.core.settings import get_settings
        ledger_path = get_settings().data_dir / "baidu_netdisk" / "ledger.json"
    result = prepare_share(req, ledger=TransferLedger(ledger_path), navigate=not args.no_navigate)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["outcome"] in {"invalid_request", "blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
