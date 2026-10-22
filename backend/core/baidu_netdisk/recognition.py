"""Pure scene / shape recognition of a netdisk client frame against an AssetStore.

No GUI, no I/O besides reading the store. Inputs: PNG/any PIL-readable frame
bytes and optionally an OCR spatial document ``{lines, tokens}`` whose boxes
are window-client pixels of that same frame. Results carry only ids, boxes,
scores and reason codes -- never image data, OCR text or store paths.

Scene identity:
- only ``role == "identity"`` shapes prove a scene; action shapes never do;
- every ``required`` identity shape (default True) must match (AND); if the
  scene has non-required identity shapes, at least one of them must match (OR);
- a shape counts only with exactly one hit inside its bounded search region;
- ``profile`` must equal the scene profile and the frame size must equal the
  reference image size, otherwise the scene is rejected;
- 0 candidate scenes -> ``unknown``, >1 -> ``ambiguous`` (never best-score).

Scene specificity: a scene may declare ``parent_scene_ids`` (e.g. a menu
overlay whose parent is the root view that stays visible beneath it). The
declared graph -- including stored parents outside ``scene_ids`` -- is
validated before any selection: string ids, no self/duplicate/unknown parent,
no cycle, bounded size; otherwise :class:`RecognitionError`. Among candidates
an ancestor is suppressed only when one of its explicit descendants is also a
candidate; exactly one remaining candidate is selected, several unrelated ones
stay ``ambiguous``. Parents are never inferred from scores, geometry or names.

Shape matching (``match``: ocr | image | both | either; default ocr when the
shape has ``ocr`` text, else image):
- OCR: lines and contiguous token runs of one line are compared after
  whitespace folding: ``exact`` equality, ``contains`` target inside a text at
  most ``CONTAINS_SLACK`` chars longer (a long line is never taken as the
  button; its token run is), ``regex`` fullmatch. Hit centres must lie in the
  padded bbox and line ``score`` must reach ``ocr_threshold`` (default 0.6).
- image: template ``image_id`` (or the reference bbox crop) matched with
  normalised cross-correlation on the padded bbox (``search_padding``, default
  8; larger only with ``floating``, capped). Flat templates are refused, flat
  windows score 0, neighbouring peaks merge (NMS), separated peaks are
  ``ambiguous``. The score is NCC scaled by colour fidelity.
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .assets import AssetError, AssetStore
from .client import Screen

DEFAULT_SEARCH_PADDING = 8
MAX_SEARCH_PADDING = 32
MAX_FLOATING_PADDING = 400
DEFAULT_OCR_THRESHOLD = 0.6
DEFAULT_IMAGE_THRESHOLD = 0.9
CONTAINS_SLACK = 2
MAX_RUN_TOKENS = 40
FLAT_STD = 2.0
MAX_PEAKS = 8
_MATCH_MODES = ("ocr", "image", "both", "either")
_OCR_MODES = ("exact", "contains", "regex")
MAX_SCENE_GRAPH = 64


class RecognitionError(ValueError):
    """Bad call input (unreadable frame, malformed shape options)."""


def _load_frame(image_bytes: bytes) -> np.ndarray:
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise RecognitionError("image_bytes must be non-empty bytes")
    try:
        with Image.open(io.BytesIO(bytes(image_bytes))) as img:
            return np.asarray(img.convert("RGB"))
    except Exception as exc:
        raise RecognitionError(f"not a readable image: {exc}") from None


def _box_dict(x: float, y: float, w: float, h: float) -> list[int]:
    return [int(round(x)), int(round(y)), int(round(w)), int(round(h))]


def _center_in(box: list[float], region: tuple[int, int, int, int]) -> bool:
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    rx, ry, rw, rh = region
    return rx <= cx <= rx + rw and ry <= cy <= ry + rh


def _overlap(a: list[float], b: list[float]) -> bool:
    """True when boxes overlap by >= 30% of the smaller one (same control)."""
    ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    iy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return False
    return ix * iy >= 0.3 * min(a[2] * a[3], b[2] * b[3])


def _region(shape: dict, width: int, height: int) -> tuple[int, int, int, int]:
    """Padded bbox clipped to the frame; padding is bounded."""
    pad = shape.get("search_padding", DEFAULT_SEARCH_PADDING)
    cap = MAX_FLOATING_PADDING if shape.get("floating") else MAX_SEARCH_PADDING
    if isinstance(pad, bool) or not isinstance(pad, int) or not 0 <= pad <= cap:
        raise RecognitionError(f"shape {shape['shape_id']} search_padding must be an int in [0, {cap}]")
    x, y, w, h = shape["bbox"]
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(width, x + w + pad), min(height, y + h + pad)
    return x1, y1, x2 - x1, y2 - y1


def _fold(text: object) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _xywh(item: dict) -> list[float]:
    return [float(item["x"]), float(item["y"]), float(item["w"]), float(item["h"])]


def _line_hits(screen: Screen, line: dict, mode: str, target: str) -> list[list[float]]:
    """Tight boxes for ``target`` in one OCR line (whole line or a token run)."""
    text = _fold(line.get("text"))
    if mode == "regex":
        pattern = re.compile(target)
        if pattern.fullmatch(text):
            return [_xywh(line)]
        parts = sorted((t for t in screen.tokens if t.get("parent_line_id") == line.get("line_id")),
                       key=lambda t: t.get("order", 0))
        hits = []
        for start in range(len(parts)):
            joined = ""
            for end in range(start, min(len(parts), start + MAX_RUN_TOKENS)):
                joined += _fold(parts[end].get("text"))
                if pattern.fullmatch(joined):
                    run = parts[start:end + 1]
                    x1, y1 = min(t["x"] for t in run), min(t["y"] for t in run)
                    x2 = max(t["x"] + t["w"] for t in run)
                    y2 = max(t["y"] + t["h"] for t in run)
                    hits.append([x1, y1, x2 - x1, y2 - y1])
                    break
        return hits
    want = _fold(target)
    if not want or want not in text:
        return []
    if text == want or (mode == "contains" and len(text) <= len(want) + CONTAINS_SLACK):
        return [_xywh(line)]
    span = screen.span(line, want)  # exact token-boundary sub-box, never a width guess
    return [_xywh(span)] if span else []


def _match_ocr(shape: dict, ocr_document: dict | None, size: tuple[int, int]) -> dict:
    if not isinstance(shape.get("ocr"), str) or not shape["ocr"]:
        return {"status": "unmatched", "bbox": None, "score": 0.0, "reason": "no_ocr_target"}
    if not isinstance(ocr_document, dict):
        return {"status": "unmatched", "bbox": None, "score": 0.0, "reason": "ocr_unavailable"}
    mode = shape.get("ocr_mode", "exact")
    if mode not in _OCR_MODES:
        raise RecognitionError(f"shape {shape['shape_id']} ocr_mode must be one of {_OCR_MODES}")
    if mode == "regex":
        try:
            re.compile(shape["ocr"])
        except re.error as exc:
            raise RecognitionError(f"shape {shape['shape_id']} ocr regex invalid: {exc}") from None
    threshold = shape.get("ocr_threshold", DEFAULT_OCR_THRESHOLD)
    width, height = size
    region = _region(shape, width, height)
    screen = Screen(frame={"width": width, "height": height},
                    lines=list(ocr_document.get("lines") or []), tokens=list(ocr_document.get("tokens") or []))
    found: list[tuple[list[float], float]] = []
    low_score = False
    for line in screen.lines:
        score = float(line.get("score", 1.0) if line.get("score") is not None else 1.0)
        for box in _line_hits(screen, line, mode, shape["ocr"]):
            if not _center_in(box, region):
                continue
            if score < threshold:
                low_score = True
                continue
            found.append((box, score))
    found.sort(key=lambda hit: hit[0][2] * hit[0][3])  # tightest box represents its control
    kept: list[tuple[list[float], float]] = []
    for box, score in found:
        if not any(_overlap(box, other) for other, _ in kept):
            kept.append((box, score))
    if len(kept) > 1:
        return {"status": "ambiguous", "bbox": None, "score": max(s for _, s in kept),
                "reason": f"ocr_hits:{len(kept)}"}
    if not kept:
        return {"status": "unmatched", "bbox": None, "score": 0.0,
                "reason": "ocr_low_score" if low_score else "ocr_not_found"}
    box, score = kept[0]
    return {"status": "matched", "bbox": _box_dict(*box), "score": round(score, 4), "reason": "ocr"}


def _template(store: AssetStore, shape: dict, reference: np.ndarray) -> np.ndarray:
    if shape.get("image_id"):
        return _load_frame(store.read_image(shape["image_id"]))
    x, y, w, h = shape["bbox"]
    return reference[y:y + h, x:x + w]


def _match_image(store: AssetStore, shape: dict, frame: np.ndarray, reference: np.ndarray) -> dict:
    threshold = shape.get("threshold", DEFAULT_IMAGE_THRESHOLD)
    templ = _template(store, shape, reference)
    th, tw = templ.shape[:2]
    rx, ry, rw, rh = _region(shape, frame.shape[1], frame.shape[0])
    if tw > rw or th > rh:
        return {"status": "unmatched", "bbox": None, "score": 0.0, "reason": "template_exceeds_region"}
    templ_gray = cv2.cvtColor(templ, cv2.COLOR_RGB2GRAY).astype(np.float32)
    if float(templ_gray.std()) < FLAT_STD:
        return {"status": "unmatched", "bbox": None, "score": 0.0, "reason": "flat_template"}
    window = frame[ry:ry + rh, rx:rx + rw]
    gray = cv2.cvtColor(window, cv2.COLOR_RGB2GRAY).astype(np.float32)
    ncc = cv2.matchTemplate(gray, templ_gray, cv2.TM_CCOEFF_NORMED)
    # Flat frame windows have no correlation: cv2 may report spurious 1.0 there.
    mean = cv2.boxFilter(gray, -1, (tw, th), normalize=True, borderType=cv2.BORDER_ISOLATED)
    sq = cv2.boxFilter(gray * gray, -1, (tw, th), normalize=True, borderType=cv2.BORDER_ISOLATED)
    ox, oy = tw // 2, th // 2
    var = (sq - mean * mean)[oy:oy + ncc.shape[0], ox:ox + ncc.shape[1]]
    ncc = np.where(np.sqrt(np.maximum(var, 0)) < FLAT_STD, 0.0, np.nan_to_num(ncc, nan=0.0))
    peaks: list[tuple[float, int, int]] = []
    work = ncc.copy()
    templ_rgb = templ.astype(np.float32)
    for _ in range(MAX_PEAKS):
        _, best, _, (px, py) = cv2.minMaxLoc(work)
        if best < threshold:
            break
        # Neighbouring pixels of one peak are the same control: suppress them.
        work[max(0, py - th // 2):py + th // 2 + 1, max(0, px - tw // 2):px + tw // 2 + 1] = -1.0
        patch = window[py:py + th, px:px + tw].astype(np.float32)
        fidelity = 1.0 - float(np.abs(patch - templ_rgb).mean()) / 255.0
        score = float(best) * fidelity
        if score >= threshold:
            peaks.append((score, px, py))
    if len(peaks) > 1:
        return {"status": "ambiguous", "bbox": None, "score": round(max(p[0] for p in peaks), 4),
                "reason": f"image_hits:{len(peaks)}"}
    if not peaks:
        best = float(ncc.max()) if ncc.size else 0.0
        return {"status": "unmatched", "bbox": None, "score": round(max(best, 0.0), 4),
                "reason": "image_below_threshold"}
    score, px, py = peaks[0]
    return {"status": "matched", "bbox": [rx + px, ry + py, tw, th], "score": round(score, 4),
            "reason": "image"}


def _match_one(store: AssetStore, shape: dict, frame: np.ndarray, reference: Any, ocr: dict | None) -> dict:
    mode = shape.get("match") or ("ocr" if isinstance(shape.get("ocr"), str) and shape["ocr"] else "image")
    if mode not in _MATCH_MODES:
        raise RecognitionError(f"shape {shape['shape_id']} match must be one of {_MATCH_MODES}")
    size = (frame.shape[1], frame.shape[0])
    if mode == "ocr":
        return _match_ocr(shape, ocr, size)
    if mode == "image":
        return _match_image(store, shape, frame, reference())
    by_ocr = _match_ocr(shape, ocr, size)
    by_image = _match_image(store, shape, frame, reference())
    both = (by_ocr, by_image)
    reason = f"{mode}:{by_ocr['reason']}/{by_image['reason']}"
    if any(r["status"] == "ambiguous" for r in both):
        return {"status": "ambiguous", "bbox": None, "score": 0.0, "reason": reason}
    hits = [r for r in both if r["status"] == "matched"]
    if len(hits) == 2 and not _overlap(by_ocr["bbox"], by_image["bbox"]):
        return {"status": "ambiguous", "bbox": None, "score": 0.0, "reason": f"{mode}:disagree"}
    if (mode == "both" and len(hits) == 2) or (mode == "either" and hits):
        best = by_image if by_image["status"] == "matched" else by_ocr
        return {"status": "matched", "bbox": best["bbox"],
                "score": min(r["score"] for r in hits), "reason": reason}
    return {"status": "unmatched", "bbox": None, "score": 0.0, "reason": reason}


def _declared_parents(scene_id: str, body: dict) -> list[str]:
    parents = body.get("parent_scene_ids", [])
    if not isinstance(parents, list) or not all(isinstance(p, str) and p for p in parents):
        raise RecognitionError(f"scene {scene_id} parent_scene_ids must be a list of scene ids")
    if scene_id in parents:
        raise RecognitionError(f"scene {scene_id} lists itself as parent")
    if len(set(parents)) != len(parents):
        raise RecognitionError(f"scene {scene_id} has duplicate parent_scene_ids")
    return parents


def _scene_graph(store: AssetStore, scene_ids: list[str]) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """Load ``scene_ids`` plus every declared ancestor; validate the parent graph.

    Returns (records of the requested scenes, transitive ancestor ids per
    requested scene). Unknown parents, cycles or an oversized graph raise.
    """
    parents: dict[str, list[str]] = {}
    records: dict[str, dict] = {}
    pending = list(scene_ids)
    while pending:
        sid = pending.pop()
        if sid in parents:
            continue
        if len(parents) >= MAX_SCENE_GRAPH:
            raise RecognitionError(f"scene parent graph exceeds {MAX_SCENE_GRAPH} scenes")
        try:
            record = store.get_scene(sid)
        except AssetError as exc:
            if sid in scene_ids:
                raise
            raise RecognitionError(f"unknown parent scene {sid!r}: {exc}") from None
        if sid in scene_ids:
            records[sid] = record
        parents[sid] = _declared_parents(sid, record["scene"])
        pending.extend(parents[sid])
    state: dict[str, int] = {}  # 1 = on the DFS stack, 2 = done
    for root in parents:
        if root in state:
            continue
        stack = [(root, iter(parents[root]))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                raise RecognitionError(f"scene parent cycle through {nxt!r}")
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(parents[nxt])))
    ancestors: dict[str, set[str]] = {}
    for sid in scene_ids:
        seen: set[str] = set()
        todo = list(parents[sid])
        while todo:
            p = todo.pop()
            if p not in seen:
                seen.add(p)
                todo.extend(parents[p])
        ancestors[sid] = seen
    return records, ancestors


def _scene(store: AssetStore, scene_id: str, frame: np.ndarray, profile: str, ocr: dict | None,
           record: dict | None = None) -> dict:
    """Evaluate identity shapes; returns status candidate|not_matched|rejected."""
    record = record or store.get_scene(scene_id)
    body = record["scene"]
    out = {"scene_id": scene_id, "revision": record["revision"], "shapes": {}, "body": body}
    if body.get("profile") != profile:
        return {**out, "status": "rejected", "reason": "profile_mismatch"}
    ref = store.get_image(body["reference_image_id"])
    if (ref["width"], ref["height"]) != (frame.shape[1], frame.shape[0]):
        return {**out, "status": "rejected", "reason": "size_mismatch"}
    identity = [s for s in body["shapes"] if s.get("role") == "identity"]
    if not identity:
        return {**out, "status": "rejected", "reason": "no_identity_shape"}
    cache: dict[str, np.ndarray] = {}

    def reference() -> np.ndarray:
        if "ref" not in cache:
            cache["ref"] = _load_frame(store.read_image(body["reference_image_id"]))
        return cache["ref"]

    out["reference"] = reference
    for shape in identity:
        out["shapes"][shape["shape_id"]] = _match_one(store, shape, frame, reference, ocr)
    ok = {sid for sid, r in out["shapes"].items() if r["status"] == "matched"}
    required = [s["shape_id"] for s in identity if s.get("required", True)]
    optional = [s["shape_id"] for s in identity if not s.get("required", True)]
    missing = [sid for sid in required if sid not in ok]
    if missing:
        return {**out, "status": "not_matched", "reason": f"identity_failed:{missing[0]}"}
    if optional and not ok.intersection(optional):
        return {**out, "status": "not_matched", "reason": "identity_any_failed"}
    return {**out, "status": "candidate", "reason": "identity_matched"}


def _public(scene: dict) -> dict:
    return {k: scene[k] for k in ("scene_id", "revision", "status", "reason", "shapes")}


def _prepare(image_bytes: bytes, profile: str) -> tuple[np.ndarray, str]:
    if not isinstance(profile, str) or not profile:
        raise RecognitionError("profile must be a non-empty string")
    return _load_frame(image_bytes), hashlib.sha256(bytes(image_bytes)).hexdigest()


def recognize(store: AssetStore, image_bytes: bytes, *, scene_ids: list[str], profile: str,
              ocr_document: dict | None = None) -> dict:
    """Identify which one of ``scene_ids`` the frame shows.

    ``status``: matched (exactly one candidate left after suppressing
    ancestors of other candidates; its action shapes are located too), unknown
    (none), ambiguous (several unrelated; no best-score pick) or rejected
    (every scene refused the profile/frame size or has no identity shape).
    ``candidates`` lists every matched scene, ``suppressed`` the ancestors
    dropped because an explicit descendant matched as well.
    """
    if not isinstance(scene_ids, (list, tuple)) or not scene_ids:
        raise RecognitionError("scene_ids must be a non-empty list")
    frame, digest = _prepare(image_bytes, profile)
    ids = list(dict.fromkeys(scene_ids))
    records, ancestors = _scene_graph(store, ids)
    scenes = [_scene(store, sid, frame, profile, ocr_document, records[sid]) for sid in ids]
    matched = [s for s in scenes if s["status"] == "candidate"]
    matched_ids = {s["scene_id"] for s in matched}
    suppressed = sorted(sid for sid in matched_ids
                        if any(sid in ancestors[other] for other in matched_ids if other != sid))
    candidates = [s for s in matched if s["scene_id"] not in suppressed]
    result = {"status": "unknown", "scene_id": None, "revision": None, "profile": profile,
              "frame_sha256": digest, "candidates": [s["scene_id"] for s in matched],
              "suppressed": suppressed, "shapes": {},
              "scenes": {s["scene_id"]: _public(s) for s in scenes}}
    if len(candidates) > 1:
        return {**result, "status": "ambiguous"}
    if not candidates:
        if all(s["status"] == "rejected" for s in scenes):
            result["status"] = "rejected"
        return result
    scene = candidates[0]
    shapes = dict(scene["shapes"])
    for shape in scene["body"]["shapes"]:
        if shape["shape_id"] not in shapes:
            shapes[shape["shape_id"]] = _match_one(store, shape, frame, scene["reference"], ocr_document)
    return {**result, "status": "matched", "scene_id": scene["scene_id"],
            "revision": scene["revision"], "shapes": shapes}


def match_shape(store: AssetStore, image_bytes: bytes, *, scene_id: str, shape_id: str, profile: str,
                ocr_document: dict | None = None) -> dict:
    """Prove ``scene_id`` from its identity shapes, then locate ``shape_id`` uniquely.

    ``status``: matched (``bbox``/``score`` set), not_found, ambiguous, or the
    scene outcome unknown / rejected when the scene itself is not proven.
    """
    frame, digest = _prepare(image_bytes, profile)
    records, _ = _scene_graph(store, [scene_id])
    scene = _scene(store, scene_id, frame, profile, ocr_document, records[scene_id])
    target = next((s for s in scene["body"]["shapes"] if s["shape_id"] == shape_id), None)
    if target is None:
        raise RecognitionError(f"scene {scene_id} has no shape {shape_id!r}")
    result = {"status": "unknown", "scene_id": scene_id, "revision": scene["revision"],
              "profile": profile, "frame_sha256": digest, "candidates": [], "shape_id": shape_id,
              "bbox": None, "score": 0.0, "reason": scene["reason"], "shapes": dict(scene["shapes"])}
    if scene["status"] != "candidate":
        return {**result, "status": "rejected" if scene["status"] == "rejected" else "unknown"}
    hit = scene["shapes"].get(shape_id) or _match_one(store, target, frame, scene["reference"], ocr_document)
    result["shapes"][shape_id] = hit
    status = {"matched": "matched", "ambiguous": "ambiguous"}.get(hit["status"], "not_found")
    return {**result, "status": status, "candidates": [scene_id], "bbox": hit["bbox"],
            "score": hit["score"], "reason": hit["reason"]}
