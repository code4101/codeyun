"""Asset-backed native netdisk client: every frame is stored and recognised.

:class:`AssetNetdiskClient` keeps the formal :class:`NetdiskClient` business
methods (``upload_file``, ``download_file`` ... with their ledger
at-most-once rules) and changes only what sits under them:

* ``observe`` stores the frame's PNG verbatim in the :class:`AssetStore`
  (immutable capture), reuses that one frame's OCR and runs
  :func:`recognition.recognize` against the configured scenes.  Unknown or
  dynamic screens are recorded as such; nothing is guessed.
* profile facts are read from the live window, never from the caller: the
  executable version of the window's process (cached per window identity,
  which carries the process generation), the client frame size and
  ``GetDpiForWindow`` (both re-read on every observation).  A scene takes
  part only when its declared ``client_version``/``width``/``height``/``dpi``
  equal those facts.
* ``prepare_action`` / ``execute_action`` / ``action_shape`` give one
  generic, capability-bound click on a shape declared ``effect`` navigation or
  inspection with ``action`` click/double_click.  Uploads, downloads, saves
  and other submits are not reachable this way.  A capability is bound to
  window identity, size, DPI, version, profile, scene revision and capture
  hash, valid ``capability_ttl`` seconds from its observation, and consumed
  once.  Coordinates come only from the matched bbox.  A navigation is
  ``verified`` only when a fresh frame recognises an expected scene; with
  ``discovery=True`` the result is at most ``acted_unverified``.
* the driver is wrapped once in a per-client :class:`GuardedDriver`, so every
  inherited input (legacy ``act``, typed keys, dialog clicks, pointer parking)
  is lease-checked, profile-gated before and after ``account.pace()`` and
  delegated exactly once; nothing here paces on its own.  The gate requires
  the input frame's window to be the unique window of a configured scene's
  role (a main input also refuses interfering windows) and the live facts to
  equal at least one configured scene of that role (no scene match: dynamic
  legacy screens stay usable); a facts mismatch carries a fresh raw capture as
  evidence.  While a capability executes, the gate also refuses changed
  facts, an expired capability or any input since its observation
  (``generation``).
* every scene declares the window it lives in (:mod:`window_roles`):
  ``window_role`` ``main`` (default, title exactly 百度网盘), ``settings``,
  ``file-dialog`` or a custom slug with its exact title; a non-main scene
  must spell out its profile facts.  Windows are selected among client-app
  windows by exact title plus live client facts (``driver.window_geometry``
  client size, DPI, cached version; never the outer list-row size; unreadable
  facts never match) and must be unique.  A main-role action is refused while
  any other visible non-minimized client window is open (declared role,
  same-title undeclared popup or unreadable facts; labels only).
  After an action, each expected scene's role window is located afresh and
  captured; a closed window is never captured again.
* ``prepare_window`` runs before ``prepare_action``, ``dismiss_auxiliary`` and
  the inherited ``open_remote_dir`` (every legacy upload/download): a window
  already inside its work area is left alone; one outside is moved in by one
  gated, paced no-resize ``fit_window`` after its original frame is stored.
  Gate refusals for an undeclared or changed input window carry a fresh raw
  capture as evidence (ids only).
* ``driver.native_controls`` reads exact UIA values of Edit/Text controls inside
  client-pixel bounds (lease-checked, no input); nothing reads app files.
* ``hover`` (``effect`` inspection only, expected scene required) is one
  pointer-only ``move`` to the bbox centre followed by a same-role readback.
* ``dismiss_auxiliary`` closes a freshly recognised non-main scene (two
  matched identity shapes) with one gated native close carrying the exact
  title; the main window is never closed and nothing falls back to Escape,
  blank clicks or a retried close.
* a *file* upload source is selected by its full native list row: on a fresh recognised ``file-dialog``
  frame with a unique confirm button, the one editable filename Edit control (bounded UIA read of that row)
  is clicked, select-all, the parent directory typed and read back exactly, then one Enter navigates (a
  closed dialog is the base's ``dialog_closed_after_directory_enter``).  On a fresh frame an explicit
  bounded ``ListItemControl`` read above the filename row must hold exactly one row whose full native name
  is the source name (ellipsized pixels/OCR never decide); it is clicked once, and a fresh frame plus the
  bounded Edit read must show exactly that name (or the full path) before the base ``upload_file`` records
  ``submitted`` and clicks confirm.  Directory sources keep the base flow.
* ``observe`` persists the frame's PNG (with UTC ``captured_at``) right after
  conversion, before OCR; an OCR/recognition failure raises
  ``observation_failed`` carrying the capture/observation ids and the error
  class only, and never re-captures.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable

from .assets import AssetStore
from .client import (DIALOG_CONFIRM, ROOT_CRUMB, SIDE_NAV_HOME, NetdiskClient, ProjectionDriver,
                     Screen, StepFailed, _brief, _norm, breadcrumb, frame_image, own_file_view, top_tab_home)
from .file_selection import (FILE_NAME_CONTROL_TYPES, LIST_ROW_CONTROL_TYPES, directory_text, filename_band,
                             filename_control, list_bounds, list_row, path_value)
from .input_guard import GuardedDriver
from .ledger import TransferLedger
from .ownership import NativeAccountHandle
from .recognition import RecognitionError, match_shape, recognize
from .window_roles import (MAIN_TITLE, PROFILE_FACTS, SETTINGS_TITLE, WINDOW_ROLES, interfering_windows, role_rows,
                           scene_window, window_specs)

ALLOWED_EFFECTS = ("navigation", "inspection")
ALLOWED_ACTIONS = ("click", "double_click", "hover")  # hover: inspection only
DEFAULT_CAPABILITY_TTL = 5.0
KEPT_OBSERVATIONS = 64


class AssetProjectionDriver(ProjectionDriver):
    """Projection driver plus read-only window profile facts (pointer parking lives in the guard)."""

    @staticmethod
    def _resolve(window_id: str) -> tuple[int, int]:
        """(hwnd, pid) only while ``window_id``'s hwnd:pid:process-creation identity still holds."""
        import psutil
        import win32process
        hwnd = int(str(window_id).split(":", 1)[0])
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if f"{hwnd}:{pid}:{psutil.Process(pid).create_time()}" != window_id:
            raise StepFailed("window_generation_changed")
        return hwnd, pid

    def client_version(self, window_id: str) -> str:
        """File version of the executable running the window's process."""
        import psutil
        import win32api
        _, pid = self._resolve(window_id)
        info = win32api.GetFileVersionInfo(psutil.Process(pid).exe(), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"

    def window_dpi(self, window_id: str) -> int:
        import ctypes
        hwnd, _ = self._resolve(window_id)
        dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
        if dpi <= 0:
            raise StepFailed("window_dpi_unavailable")
        return dpi

    def window_geometry(self, window_id: str) -> dict:
        """Read-only identity-fenced client size, visibility and screen rects (see ``projection_window_geometry``)."""
        from backend.core.devices.window_projection import projection_window_geometry
        return projection_window_geometry(window_id)

    def fit_window(self, window_id: str) -> dict:
        """Move the window wholly into its work area, never resize/focus/click (see ``fit_projection_window``)."""
        from backend.core.devices.window_projection import fit_projection_window
        return fit_projection_window(window_id)

    def native_controls(self, window_id: str, *, bounds_xywh: Iterable[float],
                        control_types: Iterable[str] = ("EditControl", "TextControl")) -> dict:
        """Read-only UIA values of controls inside client-pixel bounds (see ``projection_window_controls``)."""
        from backend.core.devices.window_projection import projection_window_controls
        return projection_window_controls(window_id, bounds_xywh=tuple(bounds_xywh),
                                          control_types=tuple(control_types))

    def close_window(self, window_id: str, expected_title: str) -> bool:
        self._resolve(window_id)  # generation fence before the inner exact-title fence
        return super().close_window(window_id, expected_title)


def _png(frame: dict) -> bytes:
    buf = BytesIO()
    frame_image(frame).save(buf, format="PNG")
    return buf.getvalue()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jump_targets(shape: dict) -> list[str]:
    target = shape.get("sceneJumpTarget")
    return [target] if isinstance(target, str) else list(target or [])


class AssetNetdiskClient(NetdiskClient):
    """:class:`NetdiskClient` whose observations are asset captures; see the module docstring."""

    def __init__(self, *, ledger: TransferLedger, store: AssetStore, profile: str, scene_ids: Iterable[str],
                 account: NativeAccountHandle, driver: ProjectionDriver | None = None,
                 capability_ttl: float = DEFAULT_CAPABILITY_TTL, clock: Callable[[], float] = time.monotonic,
                 **options: object):
        if not 0 < capability_ttl <= DEFAULT_CAPABILITY_TTL:  # also refuses NaN and infinity
            raise ValueError(f"capability_ttl must be in (0, {DEFAULT_CAPABILITY_TTL}]")
        if not isinstance(profile, str) or not profile:
            raise ValueError("profile must be a non-empty string")
        guard = GuardedDriver(driver or AssetProjectionDriver(), account=account, gate=self._input_gate)
        super().__init__(ledger=ledger, lease_check=account.check, driver=guard, **options)
        self.driver: GuardedDriver = guard
        self.store, self.profile, self.account = store, profile, account
        self.scene_ids = list(dict.fromkeys(scene_ids))
        if not self.scene_ids:
            raise ValueError("scene_ids must not be empty")
        for scene_id in self.scene_ids:  # unknown scenes or window roles fail at construction, not mid-transfer
            try:
                scene_window(store.get_scene(scene_id)["scene"])
            except ValueError as exc:
                raise ValueError(f"scene {scene_id}: {exc}") from None
        window_specs((store.get_scene(s)["scene"] for s in self.scene_ids), profile)  # consistent role titles
        self.capability_ttl = capability_ttl
        self.clock = clock
        self._versions: dict[str, str] = {}  # window identity (with process generation) -> exe version
        self._observations: OrderedDict[str, dict] = OrderedDict()
        self._capabilities: dict[str, dict] = {}
        self._active: dict | None = None  # capability whose single input is in flight

    # -- windows by declared role ------------------------------------------------

    def _window_of(self, scene_id: str) -> tuple[str, str]:
        try:
            return scene_window(self.store.get_scene(scene_id)["scene"])
        except ValueError:
            raise StepFailed("scene_window_invalid", scene_id=scene_id) from None

    def _role_of(self, scene_id: str) -> str | None:
        try:
            return scene_window(self.store.get_scene(scene_id)["scene"])[0]
        except ValueError:
            return None

    def _specs(self, extra: Iterable[str] = ()) -> list[dict]:
        """Normalized window specs of the configured (plus ``extra``) scenes of this profile."""
        scene_ids = dict.fromkeys([*self.scene_ids, *extra])
        try:
            return window_specs((self.store.get_scene(s)["scene"] for s in scene_ids), self.profile)
        except ValueError:
            raise StepFailed("scene_window_invalid") from None

    def _version(self, window_id: str) -> str:
        if window_id not in self._versions:  # the identity carries the process generation
            self._versions[window_id] = str(self.driver.client_version(window_id))
        return self._versions[window_id]

    def _window_facts(self, window_id: str) -> dict:
        """Live facts of a listed window: public client geometry (never the outer row), DPI, cached version."""
        geometry = self.driver.window_geometry(window_id)
        return {"client_version": self._version(window_id), "width": geometry["width"],
                "height": geometry["height"], "dpi": int(self.driver.window_dpi(window_id)),
                "visible": geometry.get("visible"), "minimized": geometry.get("minimized")}

    def _role_rows(self, role: str, extra: Iterable[str] = ()) -> list[dict]:
        specs = self._specs(extra)
        if role not in {s["role"] for s in specs}:
            raise StepFailed("window_role_unknown", window_role=role)
        return role_rows(self.client_windows(), specs, role, self._window_facts)

    def role_window(self, role: str = "main") -> str:
        """The unique client-app window with the role's exact title and live client facts of a declared scene."""
        rows = self._role_rows(role)
        if len(rows) != 1:
            raise StepFailed("client_main_window_not_unique" if role == "main" else "role_window_not_unique",
                             window_role=role, count=len(rows))
        return rows[0]["id"]

    def main_window(self) -> str:
        return self.role_window("main")

    def _refuse_interference(self, role: str, window_id: str, **evidence: object) -> None:
        """Main-role input only while no other visible non-minimized client window (declared, same-title
        undeclared or with unreadable facts) is open; labels only, never titles.  Other roles ignore main."""
        if role != "main":
            return
        found = interfering_windows(self.client_windows(), self._specs(), window_id, self._window_facts)
        if found:
            raise StepFailed("interfering_window_open", windows=found, **evidence)

    def prepare_window(self, role: str = "main") -> dict:
        """Bring the unique ``role`` window wholly inside its work area before navigation; never resizes.

        Already inside: ``ready`` without pacing, capture or input.  Minimized: one paced ``activate``
        (no retry), then the facts are re-read.  Outside: the original frame is stored as evidence and one
        gated :meth:`GuardedDriver.fit_window` moves it (the public core verifies identity and size)."""
        restored = False
        try:
            window = self.role_window(role)
        except StepFailed:
            # Windows reports a minimized client's collapsed geometry (observed
            # 160x28), not its restored profile. Only restore an exact, unique
            # title/version/DPI candidate; never use that relaxed identity for
            # input. Full profile matching is mandatory immediately afterwards.
            specs = self._specs()
            own = [s for s in specs if s["role"] == role and s["facts"]]
            titles = {s["title"] for s in own}
            rows = [w for w in self.client_windows() if w.get("title") in titles]
            if len(rows) != 1:
                raise
            candidate = rows[0]["id"]
            facts = self._window_facts(candidate)
            if facts.get("minimized") is not True or not any(
                all(facts.get(k) == s["facts"][k] for k in ("client_version", "dpi")) for s in own
            ):
                raise
            self._refuse_interference(role, candidate, window_role=role)
            self.account.pace()
            try:
                self.driver.activate(candidate)
            finally:
                self.account.record_activity()
            window = self.role_window(role)
            if window != candidate:
                raise StepFailed("window_changed", window_role=role)
            restored = True
        self._refuse_interference(role, window, window_role=role)
        geometry = self.driver.window_geometry(window)
        if geometry.get("minimized") is True:
            self.account.check()
            self.account.pace()
            try:
                self.driver.activate(window)
            finally:
                self.account.record_activity()
            restored = True
            if self.role_window(role) != window:
                raise StepFailed("window_changed", window_role=role)
            geometry = self.driver.window_geometry(window)
        receipt = {"window_id": window, "window_role": role, "restored": restored}
        if geometry.get("visible") is not True or geometry.get("minimized") is not False:
            raise StepFailed("window_not_visible", **receipt)
        inside = geometry.get("outer_in_workarea")
        if inside is True:
            return {"status": "ready", "moved": False, **receipt}
        if inside is not False:
            raise StepFailed("window_geometry_unknown", **receipt)
        frame, _, _, capture = self._store_frame(window)
        fit = self.driver.fit_window(window, frame=frame)
        return {"status": "moved" if fit.get("moved") else "ready", **receipt,
                **{k: fit.get(k) for k in ("moved", "old_rect", "new_rect", "workarea_rect")},
                "evidence_capture_id": capture["image_id"], "evidence_capture_sha256": capture["sha256"],
                "generation": self.driver.generation}

    # -- observation -------------------------------------------------------------

    def _facts(self, frame: dict) -> dict | None:
        """Live profile facts of the captured window, or None when any cannot be read."""
        window_id = frame.get("window_id")
        try:
            version = self._version(window_id)
            dpi = int(self.driver.window_dpi(window_id))
        except Exception:  # no driver support, window gone, generation changed: no profile
            return None
        return {"window_id": window_id, "client_version": version,
                "width": frame["width"], "height": frame["height"], "dpi": dpi}

    @staticmethod
    def _declared(body: dict, facts: dict | None) -> bool:
        return facts is not None and all(body.get(k) == facts[k] for k in PROFILE_FACTS)

    def _recognize(self, png: bytes, doc: dict, facts: dict | None, extra: Iterable[str],
                   role: str | None = None) -> dict:
        scene_ids = list(dict.fromkeys([*self.scene_ids, *extra]))
        if role is not None:  # a role window is judged against that role's scenes only
            scene_ids = [s for s in scene_ids if self._role_of(s) == role]
        eligible = [s for s in scene_ids if self._declared(self.store.get_scene(s)["scene"], facts)]
        excluded = [s for s in scene_ids if s not in eligible]
        if not eligible:
            return {"status": "rejected", "reason": "profile_facts_mismatch", "scene_id": None, "revision": None,
                    "candidates": [], "shapes": {}, "excluded": excluded}
        try:
            result = recognize(self.store, png, scene_ids=eligible, profile=self.profile, ocr_document=doc)
        except RecognitionError as exc:  # malformed asset options: recorded, never a match
            return {"status": "error", "reason": str(exc), "scene_id": None, "revision": None,
                    "candidates": [], "shapes": {}, "excluded": excluded}
        return {**result, "excluded": excluded}

    def _store_frame(self, window_id: str | None) -> tuple[dict, dict | None, bytes, dict]:
        """One guarded frame, its live facts, and its PNG persisted at once (before any OCR)."""
        frame = self.driver.frame(window_id)
        captured_at = _utc_now()
        facts = self._facts(frame)
        png = _png(frame)
        capture = self.store.capture(png, {"profile": self.profile, "facts": facts, "captured_at": captured_at,
                                           "coordinate_space": "window_client_pixels"})
        return frame, facts, png, capture

    def _capture(self, window_id: str, extra: Iterable[str] = (),
                 role: str | None = None) -> tuple[Screen, dict, bytes, dict]:
        """One frame -> stored capture -> same-frame OCR + recognition; nothing re-captured."""
        observed_at, monotonic = self.clock(), time.monotonic()
        frame, facts, png, capture = self._store_frame(window_id)
        ids = {"observation_id": uuid.uuid4().hex, "capture_id": capture["image_id"],
               "capture_sha256": capture["sha256"]}
        try:
            doc = self.driver.ocr(frame)
            recognition = self._recognize(png, doc, facts, extra, role)
        except Exception as exc:  # the message may carry OCR text or paths: class only; the frame stays stored
            raise StepFailed("observation_failed", error=type(exc).__name__, **ids) from None
        obs = {**ids, "observed_at": observed_at, "facts": facts,
               "scene_id": recognition["scene_id"], "revision": recognition["revision"],
               "recognition": recognition}
        self._observations[obs["observation_id"]] = obs
        while len(self._observations) > KEPT_OBSERVATIONS:
            self._observations.popitem(last=False)
        screen = Screen(frame, list(doc.get("lines") or []), list(doc.get("tokens") or []), monotonic)
        return screen, obs, png, doc

    def observe(self, window_id: str) -> Screen:
        return self._capture(window_id)[0]

    def observe_assets(self, window_id: str | None = None, *, role: str | None = None) -> dict:
        """Observation only (no input): ids, facts and recognition; no image bytes or OCR text.

        Without ``window_id`` the window of ``role`` (default main) is located and judged against that
        role's scenes only."""
        if window_id is None:
            role = role or "main"
            window_id = self.role_window(role)
        return dict(self._capture(window_id, role=role)[1])

    def observation(self, observation_id: str) -> dict | None:
        """A recent observation receipt by id (in-memory, bounded)."""
        obs = self._observations.get(observation_id)
        return dict(obs) if obs else None

    # -- input gate (the guard calls it before and after pacing) --------------

    def _raw_evidence(self, window_id: str | None) -> dict:
        """A fresh frame stored verbatim (no OCR) as refusal evidence; its own failure by class only."""
        try:
            capture = self._store_frame(window_id)[3]
        except Exception as exc:
            return {"evidence_error": type(exc).__name__}
        return {"evidence_capture_id": capture["image_id"], "evidence_capture_sha256": capture["sha256"]}

    @staticmethod
    def _cap_evidence(cap: dict) -> dict:
        private = ("frame", "facts", "observed_at", "profile", "generation", "window_id")
        return {k: v for k, v in cap.items() if k not in private}

    def _input_gate(self, frame: dict, action: dict) -> None:
        """Live facts must equal a configured scene; an executing capability must also still hold."""
        facts = self._facts(frame)
        cap = self._active
        closing = action.get("action") == "close"
        if cap is None and closing:
            raise StepFailed("close_window_not_declared")  # only dismiss_auxiliary closes
        if cap is not None:
            evidence = self._cap_evidence(cap)
            if self.driver.generation != cap["generation"]:
                raise StepFailed("action_superseded", **evidence)
            if closing != (cap["action"] == "close") or frame.get("window_id") != cap["window_id"] \
                    or closing and action.get("title") != cap["window_title"]:
                raise StepFailed("action_mismatch", **evidence)
            if self.store.get_scene(cap["scene_id"])["revision"] != cap["revision"]:
                raise StepFailed("asset_revision_changed", **evidence)
            if [w.get("id") for w in self._role_rows(cap["window_role"])] != [cap["window_id"]]:
                raise StepFailed("window_changed", **evidence, **self._raw_evidence(frame.get("window_id")))
            self._refuse_interference(cap["window_role"], cap["window_id"], **evidence)
            if facts != cap["facts"]:  # same identity/size; DPI and version re-read
                raise StepFailed("profile_facts_changed", **evidence, **self._raw_evidence(frame.get("window_id")))
            if self.clock() - cap["observed_at"] > self.capability_ttl:
                raise StepFailed("action_expired", **evidence)
            return
        # Inherited (legacy) input: the frame's window must be the unique window of a declared role, a main
        # input needs no interfering window, and the live facts must equal a scene of that role (facts only:
        # dynamic legacy screens need no scene match).
        window_id = frame.get("window_id")
        roles = dict.fromkeys(self._role_of(s) for s in self.scene_ids)  # all valid since construction
        role = next((r for r in roles if [w.get("id") for w in self._role_rows(r)] == [window_id]), None)
        if role is None:
            raise StepFailed("input_window_not_declared", action=action.get("action"),
                             **self._raw_evidence(window_id))
        self._refuse_interference(role, window_id, action=action.get("action"), window_role=role)
        if not any(self._role_of(s) == role and self._declared(self.store.get_scene(s)["scene"], facts)
                   for s in self.scene_ids):
            raise StepFailed("profile_facts_mismatch", action=action.get("action"), window_role=role,
                             **self._raw_evidence(window_id))

    # -- capability-bound generic actions --------------------------------------

    def prepare_action(self, scene_id: str, shape_id: str, *, expected_scene_ids: Iterable[str] | None = None,
                       discovery: bool = False) -> dict:
        """Paced fresh frame -> unique scene + shape match -> opaque one-shot ``action_id``."""
        record = self.store.get_scene(scene_id)
        shape = next((s for s in record["scene"]["shapes"] if s["shape_id"] == shape_id), None)
        if shape is None:
            raise StepFailed("shape_unknown", scene_id=scene_id, shape_id=shape_id)
        hover = shape.get("action") == "hover"
        if shape.get("effect") not in ALLOWED_EFFECTS or shape.get("action") not in ALLOWED_ACTIONS \
                or hover and shape.get("effect") != "inspection":
            raise StepFailed("action_not_declared", scene_id=scene_id, shape_id=shape_id,
                             effect=shape.get("effect"), action=shape.get("action"))
        expected = list(dict.fromkeys(expected_scene_ids or _jump_targets(shape)))
        if not expected and (hover or not discovery):
            raise StepFailed("expected_scene_required", scene_id=scene_id, shape_id=shape_id)
        role, title = self._window_of(scene_id)
        targets = {target: self._window_of(target)[0] for target in expected}
        if hover and set(targets.values()) != {role}:
            raise StepFailed("hover_readback_role_mismatch", scene_id=scene_id, shape_id=shape_id)
        window = self.prepare_window(role)["window_id"]
        self._refuse_interference(role, window, scene_id=scene_id, shape_id=shape_id)
        self.account.wait_ready()  # after any fit and before the frame: no pace ages the capability
        self.driver.activate(window)
        generation = self.driver.generation
        screen, obs, png, doc = self._capture(window, [scene_id], role=role)
        evidence = {"observation_id": obs["observation_id"], "capture_id": obs["capture_id"],
                    "capture_sha256": obs["capture_sha256"], "scene_id": scene_id, "shape_id": shape_id,
                    "window_role": role, "window_title": title}
        if not self._declared(record["scene"], obs["facts"]) or obs["facts"]["window_id"] != window:
            raise StepFailed("profile_facts_mismatch", **evidence)
        recognition = obs["recognition"]
        if recognition["status"] != "matched" or recognition["scene_id"] != scene_id:
            raise StepFailed("scene_not_recognized", status=recognition["status"], **evidence)
        hit = match_shape(self.store, png, scene_id=scene_id, shape_id=shape_id, profile=self.profile,
                          ocr_document=doc)
        if hit["status"] != "matched" or hit["revision"] != record["revision"] \
                or hit["frame_sha256"] != obs["capture_sha256"]:
            raise StepFailed("shape_not_matched", status=hit["status"], reason=hit["reason"], **evidence)
        action_id = secrets.token_hex(16)
        evidence.update(revision=record["revision"], bbox=hit["bbox"], score=hit["score"],
                        effect=shape["effect"], action=shape["action"], expected_scene_ids=expected,
                        expected_window_roles=targets, discovery=bool(discovery), ttl=self.capability_ttl)
        self._capabilities[action_id] = {**evidence, "window_id": window, "frame": screen.frame,
                                         "facts": obs["facts"], "observed_at": obs["observed_at"],
                                         "profile": self.profile, "generation": generation}
        return {"action_id": action_id, "evidence": evidence}

    def execute_action(self, action_id: str) -> dict:
        """Consume the capability once; click (or hover) the matched bbox centre; read the result back."""
        cap = self._capabilities.pop(action_id, None)
        if cap is None:
            raise StepFailed("action_unknown_or_consumed")
        evidence = self._cap_evidence(cap)
        if self.store.get_scene(cap["scene_id"])["revision"] != cap["revision"]:
            raise StepFailed("asset_revision_changed", **evidence)
        if self.role_window(cap["window_role"]) != cap["window_id"] or self.profile != cap["profile"]:
            raise StepFailed("window_changed", **evidence)
        self._refuse_interference(cap["window_role"], cap["window_id"], **evidence)
        x, y, w, h = cap["bbox"]
        width, height = cap["frame"]["width"], cap["frame"]["height"]
        point = {"x": (x + w / 2) / width, "y": (y + h / 2) / height}
        self._active = cap  # the gate re-checks generation, revision, window, facts and expiry around pacing
        try:
            if cap["action"] == "hover":  # pointer only: no button, wheel or key
                self.driver.input(cap["frame"], action="move", **point)
            else:
                self.driver.input(cap["frame"], action=cap["action"], **point, button="left")
        finally:
            self._active = None
        return {"action_id": action_id, "before": evidence, **self._readback(cap)}

    def _readback(self, cap: dict, closed: str | None = None) -> dict:
        """Observation only: each expected scene's role window located afresh per attempt, never by old id.

        ``closed``: a dismissed window id that must no longer be listed for ``verified``."""
        targets = cap["expected_window_roles"]
        roles = list(dict.fromkeys(targets.values())) or [cap["window_role"]]
        after: list[dict] = []
        rec = None
        attempts = 1 if cap["discovery"] else max(1, self.poll_attempts)
        for _ in range(attempts):
            self.sleep(self.settle)
            gone = closed is None or all(w.get("id") != closed for w in self.client_windows())
            for role in roles:
                rows = self._role_rows(role, targets)
                if len(rows) != 1:  # absent or not unique: nothing captured
                    after.append({"window_role": role, "windows": len(rows)})
                    continue
                obs = self._capture(rows[0]["id"], [s for s, r in targets.items() if r == role], role=role)[1]
                after.append({"window_role": role, **{k: obs[k] for k in (
                    "observation_id", "capture_id", "capture_sha256", "scene_id", "revision")}})
                rec = obs["recognition"]
                if not cap["discovery"] and gone and rec["status"] == "matched" and rec["scene_id"] in targets:
                    return {"status": "verified", "after": after, "recognition": rec}
        status = "acted_unverified" if cap["discovery"] else "post_scene_not_verified"
        return {"status": status, "after": after, "recognition": rec}

    def action_shape(self, scene_id: str, shape_id: str, expected_scene_ids: Iterable[str] | None = None,
                     discovery: bool = False) -> dict:
        prepared = self.prepare_action(scene_id, shape_id, expected_scene_ids=expected_scene_ids,
                                       discovery=discovery)
        return self.execute_action(prepared["action_id"])

    def dismiss_auxiliary(self, scene_id: str, expected_scene_ids: Iterable[str]) -> dict:
        """Close one freshly recognised non-main scene's window by one gated native close.

        Requires the scene's declared role/title, live facts and at least two matched identity shapes on a
        fresh frame; the close carries the exact title and is sent at most once.  ``verified`` only when the
        window is gone and a fresh frame of an expected scene's role window recognises that scene; an
        unknown close outcome is reported, never replayed."""
        role, title = self._window_of(scene_id)
        if role == "main":
            raise StepFailed("main_dismiss_refused", scene_id=scene_id)
        expected = list(dict.fromkeys(expected_scene_ids or ()))
        if not expected:
            raise StepFailed("expected_scene_required", scene_id=scene_id)
        targets = {target: self._window_of(target)[0] for target in expected}
        record = self.store.get_scene(scene_id)
        identities = [s["shape_id"] for s in record["scene"]["shapes"] if s.get("role") == "identity"]
        if len(identities) < 2:
            raise StepFailed("dismiss_identity_not_declared", scene_id=scene_id, identities=len(identities))
        window = self.prepare_window(role)["window_id"]
        self.account.wait_ready()
        self.driver.activate(window)
        generation = self.driver.generation
        screen, obs, _, _ = self._capture(window, [scene_id], role=role)
        evidence = {"observation_id": obs["observation_id"], "capture_id": obs["capture_id"],
                    "capture_sha256": obs["capture_sha256"], "scene_id": scene_id,
                    "window_role": role, "window_title": title}
        if not self._declared(record["scene"], obs["facts"]) or obs["facts"]["window_id"] != window:
            raise StepFailed("profile_facts_mismatch", **evidence)
        rec = obs["recognition"]
        if rec["status"] != "matched" or rec["scene_id"] != scene_id or rec["revision"] != record["revision"]:
            raise StepFailed("scene_not_recognized", status=rec["status"], **evidence)
        matched = [s for s in identities if (rec["shapes"].get(s) or {}).get("status") == "matched"]
        if len(matched) < 2:
            raise StepFailed("dismiss_identity_not_matched", matched=len(matched), **evidence)
        evidence.update(revision=record["revision"], action="close", expected_scene_ids=expected,
                        expected_window_roles=targets, discovery=False, ttl=self.capability_ttl)
        cap = {**evidence, "window_id": window, "frame": screen.frame, "facts": obs["facts"],
               "observed_at": obs["observed_at"], "profile": self.profile, "generation": generation}
        self._active = cap  # same gate as an input: lease, revision, window, facts, expiry, generation
        try:
            sent = self.driver.close_window(window, title, frame=screen.frame)
        except Exception as exc:
            if self.driver.generation == generation:  # refused before delegation: nothing sent
                raise
            return {"status": "close_outcome_unknown", "error": type(exc).__name__, "before": evidence,
                    **{k: v for k, v in self._readback(cap, closed=window).items() if k != "status"}}
        finally:
            self._active = None
        if not sent:
            raise StepFailed("close_title_mismatch", **evidence)  # inner fence: nothing sent
        return {"before": evidence, **self._readback(cap, closed=window)}

    # -- provider-internal navigation fixes (validated on the real client) -----

    def open_remote_dir(self, remote_dir: str, *, create_missing: bool = False) -> tuple[str, Screen]:
        """Every legacy native upload/download enters here: prepare the main window, then the base flow."""
        self.prepare_window("main")
        return super().open_remote_dir(remote_dir, create_missing=create_missing)

    def _file_dialog_screen(self, dialog: str) -> tuple[Screen, dict, list[float]]:
        """Fresh frame of the unique declared ``file-dialog`` window, recognised as a file-dialog scene, with a
        unique confirm button; returns it, its safe evidence ids and the filename band of the confirm row.  The
        OCR filename label is not required (frames may drop or merge it): the native control name decides."""
        if self.role_window("file-dialog") != dialog:
            raise StepFailed("file_dialog_window_mismatch")
        screen, obs, _, _ = self._capture(dialog, role="file-dialog")
        ids = {k: obs[k] for k in ("observation_id", "capture_id", "capture_sha256")}
        rec = obs["recognition"]
        if (obs["facts"] or {}).get("window_id") != dialog or rec["status"] != "matched" \
                or self._role_of(rec["scene_id"]) != "file-dialog":
            raise StepFailed("file_dialog_not_recognized", status=rec["status"], **ids)
        confirms = screen.find(DIALOG_CONFIRM)
        if len(confirms) != 1:  # counts only: OCR text may carry local names
            raise StepFailed("file_dialog_anchors_not_unique", confirms=len(confirms), **ids)
        try:
            band = filename_band(confirms[0], screen.lines, *screen.size)
        except StepFailed as exc:
            raise StepFailed(exc.code, **exc.facts, **ids) from None
        return screen, ids, band

    def _filename_control(self, screen: Screen, dialog: str, band: list[float], ids: dict) -> dict:
        """The one editable filename Edit control of a bounded UIA read taken after ``screen``'s frame."""
        try:
            read = self.driver.native_controls(dialog, bounds_xywh=band, control_types=FILE_NAME_CONTROL_TYPES)
        except Exception as exc:  # the message may name local paths: class only
            raise StepFailed("file_name_control_unreadable", error=type(exc).__name__, **ids) from None
        width, height = screen.size
        try:
            return filename_control(read, window_id=dialog, width=width, height=height,
                                    not_before=screen.observed_at, band=band)
        except StepFailed as exc:
            raise StepFailed(exc.code, **exc.facts, **ids) from None

    def _list_row(self, screen: Screen, dialog: str, band: list[float], name: str, ids: dict) -> dict:
        """The one list row above ``band`` whose full native name is ``name``, from a bounded ListItem read
        taken after ``screen``'s frame (ellipsized pixels and OCR are never consulted)."""
        width, height = screen.size
        try:
            bounds = list_bounds(band, width)
            read = self.driver.native_controls(dialog, bounds_xywh=bounds, control_types=LIST_ROW_CONTROL_TYPES)
        except StepFailed as exc:
            raise StepFailed(exc.code, **exc.facts, **ids) from None
        except Exception as exc:  # the message may name local paths: class only
            raise StepFailed("source_row_unreadable", error=type(exc).__name__, **ids) from None
        try:
            return list_row(read, window_id=dialog, width=width, height=height, not_before=screen.observed_at,
                            bounds=bounds, name=name)
        except StepFailed as exc:
            raise StepFailed(exc.code, **exc.facts, **ids) from None

    def _fresh_filename(self, dialog: str, size: tuple[int, int], control: dict, *expected: str) -> Screen:
        """Fresh dialog frame of the same size whose filename control (same bbox) holds exactly ``expected``."""
        screen, ids, band = self._file_dialog_screen(dialog)
        if screen.size != size:
            raise StepFailed("file_dialog_size_changed", **ids)
        after = self._filename_control(screen, dialog, band, ids)
        if list(after["bbox"]) != list(control["bbox"]):
            raise StepFailed("file_name_control_changed", **ids)
        try:
            path_value(after["value"], *expected)
        except StepFailed as exc:
            raise StepFailed(exc.code, **exc.facts, **ids) from None
        return screen

    def _select_in_dialog(self, dialog: str, source: Path) -> Screen:
        """File source: navigate to its parent through the filename control, click its exact native list row.

        The parent directory text is typed into the verified filename Edit control and read back exactly
        before the one Enter (legacy directory navigation); a dialog closed by that Enter raises the base's
        ``dialog_closed_after_directory_enter`` (the base marks the key unknown).  On a fresh recognised
        dialog frame the bounded ListItem read must hold exactly one row named ``source.name`` (full native
        name; OCR may show it ellipsized), clicked once.  A fresh frame plus the same bounded Edit read must
        then show exactly ``source.name`` or the full resolved path; that genuine frame is returned for the
        base ``upload_file`` ledger-then-confirm click.  Directory sources keep the base flow unchanged."""
        if source.is_dir():
            return super()._select_in_dialog(dialog, source)
        if source.is_symlink():
            raise StepFailed("source_is_link")
        if not source.is_file():
            raise StepFailed("source_not_file")
        resolved = source.resolve()
        if resolved.name != source.name:
            raise StepFailed("source_is_link")
        expected = str(resolved)
        directory = directory_text(expected)
        self.lease_check()
        self.driver.activate(dialog)
        screen, ids, band = self._file_dialog_screen(dialog)
        control = self._filename_control(screen, dialog, band, ids)
        size = screen.size
        x, y, w, h = control["bbox"]
        screen = self.act(screen, dialog, action="click", x=(x + w / 2) / size[0], y=(y + h / 2) / size[1],
                          button="left")
        screen = self.act(screen, dialog, action="key", key="select_all")
        self.act(screen, dialog, action="type", text=directory)
        screen = self._fresh_filename(dialog, size, control, directory)  # Enter only on the exact directory
        self.lease_check()
        self.driver.input(screen.frame, action="key", key="enter")
        self.sleep(self.settle)
        if self.dialog_window() != dialog:
            raise StepFailed("dialog_closed_after_directory_enter")  # the base blocks a resubmit
        screen, ids, band = self._file_dialog_screen(dialog)
        if screen.size != size:
            raise StepFailed("file_dialog_size_changed", **ids)
        row = self._list_row(screen, dialog, band, source.name, ids)
        x, y, w, h = row["bbox"]
        self.act(screen, dialog, action="click", x=(x + w / 2) / size[0], y=(y + h / 2) / size[1], button="left")
        return self._fresh_filename(dialog, size, control, source.name, expected)

    def _to_file_view(self, main: str, screen: Screen) -> Screen:
        """Side-nav 首页, or the unique top-tab 首页 when a tab has no side nav; one click.

        Then observation only until the own list header and a proven path show (the
        transfer page also has a 文件/大小 header, so a header alone is no proof).
        """
        if own_file_view(screen):
            return screen
        width, height = screen.size
        if screen.find(SIDE_NAV_HOME, left_of=width * .06, below=height * .12):
            home = screen.unique(SIDE_NAV_HOME, "side_nav_home_not_found", left_of=width * .06, below=height * .12)
        else:
            home = top_tab_home(screen)
        screen = self.click_box(screen, main, home)
        if own_file_view(screen):
            return screen
        polled = self._poll(main, own_file_view)
        if polled is None:
            raise StepFailed("own_file_view_not_verified")  # no texts: the left tab may show share content
        return polled

    def _to_root(self, main: str, screen: Screen) -> Screen:
        path = self.current_path(screen)
        if path == []:
            return screen
        if path is None:  # no provable crumb and no root-home evidence: fail closed
            raise StepFailed("current_path_unknown", texts=screen.texts()[:60])
        root_line, _ = breadcrumb(screen, self._header(screen))
        box = screen.span(root_line, ROOT_CRUMB)
        if box is None:  # a separate 全部文件 line is clickable as a whole
            if _norm(root_line.get("text")) != ROOT_CRUMB:
                raise StepFailed("root_crumb_span_unknown", line=_brief(root_line))
            box = root_line
        screen = self.click_box(screen, main, box)
        if self.current_path(screen) == []:
            return screen
        # The pointer rests on the recent-files strip whose hover card can cover the list
        # header: park it (pointer move only) and judge a fresh frame strictly.
        self.driver.park_pointer(screen.frame)
        self.sleep(self.settle)
        screen = self.observe(main)
        if self.current_path(screen) == []:
            return screen
        polled = self._poll(main, lambda s: self.current_path(s) == [])
        if polled is None:
            raise StepFailed("root_not_verified", texts=screen.texts()[:60])
        return polled


__all__ = ["ALLOWED_ACTIONS", "ALLOWED_EFFECTS", "AssetNetdiskClient", "AssetProjectionDriver",
           "DEFAULT_CAPABILITY_TTL", "MAIN_TITLE", "PROFILE_FACTS", "SETTINGS_TITLE", "WINDOW_ROLES", "scene_window"]
