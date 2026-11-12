"""Procedural haptic pattern engine, seamless Lovense phrase synthesizer, and VibeState.

Design principles:
1. Gapless hardware loops on Lovense (``timeSec: 0``, ``stopPrevious: 0``):
   ``build_lovense_phrase()`` synthesizes 50-step multi-beat phrases (~6–9 s)
   where carrier cycles, High↔Medium contrast cycles, and dual-harmonic phrase
   envelopes complete exact integer multiples over the 50 steps. Lovense loops
   ``step[49] -> step[0]`` natively in hardware with zero seam and zero periodic
   HTTP refreshes — completely eliminating ~4 s refresh dropouts.
2. Single-packet story ``afterglow``: when winding down after an intimate scene,
   all 9.5 seconds (50 × 190 ms) decay smoothly to trailing zeros in one packet.
3. BLE-aligned High↔Medium contrast (~1.0–1.4 Hz, 6–8 steps per cycle): prevents
   numbing at high intensity while giving physical Lovense motors full spin-up
   and spin-down contrast without BLE aliasing.
"""
from __future__ import annotations

import copy
import math
import re
import time

TAU = 2.0 * math.pi
PHI = 1.618033988749895
XFADE_S = 0.35
AFTERGLOW_DECAY_S = 9.5

PATTERN_SPECS: dict[str, dict] = {
    "velvet": {
        "id": "velvet",
        "label": "Velvet",
        "tagline": "Warm continuous caress that breathes softly; pulses High↔Medium when intense.",
        "default_pace": 0.35,
    },
    "swell": {
        "id": "swell",
        "label": "Ocean Swell",
        "tagline": "Slow, deep 13s tide building gradually from 0% all the way to full crest and back down.",
        "default_pace": 0.40,
    },
    "heartbeat": {
        "id": "heartbeat",
        "label": "Heartbeat",
        "tagline": "Pronounced lub-dub throb punching out of pure silence with a crisp pause between beats.",
        "default_pace": 0.45,
    },
    "tease": {
        "id": "tease",
        "label": "Tease & Edge",
        "tagline": "Climbing swells that hover at the brink, hush softly, then build again.",
        "default_pace": 0.50,
    },
    "rhythm": {
        "id": "rhythm",
        "label": "Rhythm",
        "tagline": "Bold 0%↔100% full-depth strokes with 4-beat accents and crisp zero-floor contrast.",
        "default_pace": 0.60,
    },
    "crest": {
        "id": "crest",
        "label": "Crest Surge",
        "tagline": "Brisk High↔Medium contrast surges riding a deep wave — peak intensity, zero numbing.",
        "default_pace": 0.78,
    },
    "afterglow": {
        "id": "afterglow",
        "label": "Afterglow",
        "tagline": "Slow, silky cosine waves that gently wind down to silence.",
        "default_pace": 0.25,
    },
}

PATTERNS: tuple[str, ...] = tuple(PATTERN_SPECS.keys())

LEGACY_ALIASES: dict[str, str] = {
    "constant": "velvet",
    "wave": "swell",
    "pulse": "rhythm",
}

CONTRAST_MODES: dict[str, float] = {
    "subtle": 0.55,
    "balanced": 1.00,
    "high": 1.30,
}

SENTENCE_ARCS: tuple[str, ...] = ("steady", "rise", "surge", "ease")

BLOCK_TYPES: tuple[str, ...] = ("wave", "saw_up", "saw_down", "line", "hold", "ripple")

VIBE_TAG_CRITERIA: dict[str, str] = {
    "gentle": "Gentle, tender, warm, affectionate, or slow lingering closeness.",
    "sensual": "Unhurried sensual touch, deep kissing, caressing, undressing, or smooth rolling motion.",
    "teasing": "Playful, edging, suspenseful anticipation, stop-and-go teasing, or hovering near the brink.",
    "rhythmic": "Steady repeated strokes, thrusting, rocking, grinding, or a clear physical beat.",
    "building": "Gradually escalating tension, rising pressure, or long slow buildup toward a peak.",
    "intense": "Fast, urgent, breathless passion, driving energy, or overwhelming climax.",
    "afterglow": "Post-climax release, catching breath, resting, or softly winding down.",
}

BUILTIN_EDITOR_TEMPLATES: dict[str, dict] = {
    "velvet": {
        "id": "velvet",
        "label": "Velvet",
        "tagline": "Warm continuous caress that breathes softly; pulses High↔Medium when intense.",
        "vibe_tags": ["gentle", "sensual"],
        "loop_seconds": 6.8,
        "default_pace": 0.35,
        "lift_high_floor": True,
        "blocks": [
            {"type": "wave", "width": 1.0, "repeat": 3, "floor": 0.44, "peak": 0.96, "skew": 0.50, "bend": 0.25},
        ],
    },
    "swell": {
        "id": "swell",
        "label": "Ocean Swell",
        "tagline": "Slow, deep 13s tide building gradually from 0% all the way to full crest and back down.",
        "vibe_tags": ["sensual", "building"],
        "loop_seconds": 13.6,
        "default_pace": 0.40,
        "lift_high_floor": False,
        "blocks": [
            {"type": "wave", "width": 1.0, "repeat": 1, "floor": 0.0, "peak": 1.0, "skew": 0.66, "bend": 0.12},
        ],
    },
    "heartbeat": {
        "id": "heartbeat",
        "label": "Heartbeat",
        "tagline": "Pronounced lub-dub throb punching out of pure silence with a crisp pause between beats.",
        "vibe_tags": ["teasing", "building"],
        "loop_seconds": 1.9,
        "default_pace": 0.45,
        "lift_high_floor": False,
        "blocks": [
            {"type": "wave", "width": 0.25, "repeat": 1, "floor": 0.0, "peak": 0.70, "skew": 0.45, "bend": -0.28},
            {"type": "hold", "width": 0.15, "repeat": 1, "floor": 0.0, "peak": 0.0, "skew": 0.50, "bend": 0.0},
            {"type": "wave", "width": 0.27, "repeat": 1, "floor": 0.0, "peak": 1.00, "skew": 0.45, "bend": -0.22},
            {"type": "hold", "width": 0.33, "repeat": 1, "floor": 0.0, "peak": 0.0, "skew": 0.50, "bend": 0.0},
        ],
    },
    "tease": {
        "id": "tease",
        "label": "Tease & Edge",
        "tagline": "Climbing swells that hover at the brink, hush softly, then build again.",
        "vibe_tags": ["teasing", "building"],
        "loop_seconds": 7.6,
        "default_pace": 0.50,
        "lift_high_floor": False,
        "blocks": [
            {"type": "saw_up", "width": 0.54, "repeat": 1, "floor": 0.08, "peak": 0.96, "skew": 0.50, "bend": -0.35},
            {"type": "ripple", "width": 0.24, "repeat": 3, "floor": 0.74, "peak": 1.00, "skew": 0.50, "bend": 0.10},
            {"type": "hold", "width": 0.22, "repeat": 1, "floor": 0.0, "peak": 0.05, "skew": 0.50, "bend": 0.0},
        ],
    },
    "rhythm": {
        "id": "rhythm",
        "label": "Rhythm",
        "tagline": "Bold 0%↔100% full-depth strokes with 4-beat accents and crisp zero-floor contrast.",
        "vibe_tags": ["rhythmic"],
        "loop_seconds": 3.4,
        "default_pace": 0.60,
        "lift_high_floor": False,
        "blocks": [
            {"type": "wave", "width": 0.75, "repeat": 3, "floor": 0.0, "peak": 0.88, "skew": 0.50, "bend": -0.22},
            {"type": "wave", "width": 0.25, "repeat": 1, "floor": 0.0, "peak": 1.00, "skew": 0.50, "bend": -0.20},
        ],
    },
    "crest": {
        "id": "crest",
        "label": "Crest Surge",
        "tagline": "Brisk High↔Medium contrast surges riding a deep wave — peak intensity, zero numbing.",
        "vibe_tags": ["intense", "rhythmic"],
        "loop_seconds": 5.2,
        "default_pace": 0.78,
        "lift_high_floor": True,
        "blocks": [
            {"type": "ripple", "width": 1.0, "repeat": 6, "floor": 0.52, "peak": 1.00, "skew": 0.50, "bend": 0.15},
        ],
    },
    "afterglow": {
        "id": "afterglow",
        "label": "Afterglow",
        "tagline": "Slow, silky cosine waves that gently wind down to silence.",
        "vibe_tags": ["afterglow", "gentle"],
        "loop_seconds": 8.0,
        "default_pace": 0.25,
        "lift_high_floor": False,
        "blocks": [
            {"type": "wave", "width": 0.55, "repeat": 2, "floor": 0.12, "peak": 0.78, "skew": 0.45, "bend": 0.20},
            {"type": "line", "width": 0.45, "repeat": 1, "floor": 0.12, "peak": 0.0, "skew": 0.50, "bend": 0.30},
        ],
    },
}

_user_patterns_provider = None


def set_user_patterns_provider(fn) -> None:
    global _user_patterns_provider
    _user_patterns_provider = fn


def _active_user_patterns() -> dict[str, dict]:
    if _user_patterns_provider is None:
        return {}
    try:
        raw = _user_patterns_provider()
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def slugify_pattern_id(raw: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "_", str(raw or "").strip().lower()).strip("_-")
    if not s:
        s = "custom_pattern"
    if s == "auto":
        s = "custom_auto"
    return s[:36]


def sanitize_block_pattern(raw_spec: dict, fallback_id: str = "") -> dict:
    """Validate and normalize a Shape-Block pattern definition."""
    d = raw_spec if isinstance(raw_spec, dict) else {}
    pat_id = slugify_pattern_id(d.get("id") or fallback_id or d.get("label") or "custom_pattern")
    base_builtin = PATTERN_SPECS.get(pat_id)

    label = str(d.get("label") or (base_builtin["label"] if base_builtin else pat_id.replace("_", " ").title())).strip()[:48]
    if not label:
        label = "Custom Pattern"
    tagline = str(
        d.get("tagline")
        or (base_builtin["tagline"] if base_builtin else "Custom sculpted waveform.")
    ).strip()[:160]

    raw_tags = d.get("vibe_tags")
    if isinstance(raw_tags, list):
        vibe_tags = [str(t).strip().lower() for t in raw_tags if str(t).strip().lower() in VIBE_TAG_CRITERIA]
    else:
        vibe_tags = list(BUILTIN_EDITOR_TEMPLATES.get(pat_id, {}).get("vibe_tags", ["sensual", "rhythmic"]))
    if not vibe_tags:
        vibe_tags = ["sensual"]

    try:
        loop_s = _clamp(float(d.get("loop_seconds", 5.6)), 1.0, 16.0)
    except (TypeError, ValueError):
        loop_s = 5.6

    try:
        def_pace = _clamp(float(d.get("default_pace", base_builtin["default_pace"] if base_builtin else 0.5)), 0.0, 1.0)
    except (TypeError, ValueError):
        def_pace = 0.5

    lift_floor = bool(d.get("lift_high_floor", False))

    raw_blocks = d.get("blocks")
    blocks: list[dict] = []
    if isinstance(raw_blocks, list):
        for b in raw_blocks[:12]:
            if not isinstance(b, dict):
                continue
            b_type = str(b.get("type") or "wave").strip().lower()
            if b_type not in BLOCK_TYPES:
                b_type = "wave"
            try:
                width = _clamp(float(b.get("width", 1.0)), 0.08, 12.0)
            except (TypeError, ValueError):
                width = 1.0
            try:
                repeat = max(1, min(8, int(round(float(b.get("repeat", 1))))))
            except (TypeError, ValueError):
                repeat = 1
            try:
                floor_v = _clamp(float(b.get("floor", 0.0)), 0.0, 1.0)
            except (TypeError, ValueError):
                floor_v = 0.0
            try:
                peak_v = _clamp(float(b.get("peak", 1.0)), 0.0, 1.0)
            except (TypeError, ValueError):
                peak_v = 1.0
            if b_type in ("wave", "saw_up", "saw_down", "ripple") and floor_v > peak_v:
                floor_v, peak_v = peak_v, floor_v
            try:
                skew_v = _clamp(float(b.get("skew", 0.5)), 0.14, 0.86)
            except (TypeError, ValueError):
                skew_v = 0.5
            try:
                bend_v = _clamp(float(b.get("bend", 0.0)), -0.85, 0.85)
            except (TypeError, ValueError):
                bend_v = 0.0
            blocks.append({
                "type": b_type,
                "width": round(width, 4),
                "repeat": repeat,
                "floor": round(floor_v, 4),
                "peak": round(peak_v, 4),
                "skew": round(skew_v, 4),
                "bend": round(bend_v, 4),
            })

    if not blocks:
        blocks = [{"type": "wave", "width": 1.0, "repeat": 2, "floor": 0.0, "peak": 1.0, "skew": 0.5, "bend": 0.0}]

    return {
        "id": pat_id,
        "label": label,
        "tagline": tagline,
        "vibe_tags": vibe_tags,
        "loop_seconds": round(loop_s, 2),
        "default_pace": round(def_pace, 2),
        "lift_high_floor": lift_floor,
        "blocks": blocks,
    }


def _curve_pow(t: float, bend: float) -> float:
    tc = _clamp(t, 0.0, 1.0)
    if tc <= 0.0 or tc >= 1.0 or abs(bend) < 1e-4:
        return tc
    exp = 2.0 ** (-_clamp(bend, -0.85, 0.85) * 1.45)
    return tc ** exp


def _block_natural_end(block: dict) -> float:
    b_type = block["type"]
    if b_type in ("hold", "line"):
        return float(block["peak"])
    return float(block["floor"])


def _resolve_block_starts(blocks: list[dict]) -> list[float]:
    if not blocks:
        return []
    ends = [_block_natural_end(b) for b in blocks]
    starts: list[float] = []
    for idx, b in enumerate(blocks):
        if b["type"] == "line":
            if len(blocks) == 1:
                starts.append(float(b["floor"]))
            else:
                prev_idx = (idx - 1) % len(blocks)
                starts.append(ends[prev_idx])
        elif b["type"] == "hold":
            starts.append(float(b["peak"]))
        elif b["type"] == "saw_down":
            starts.append(float(b["peak"]))
        else:
            starts.append(float(b["floor"]))
    return starts


def _eval_block_subcycle(block: dict, start_h: float, t: float) -> float:
    """Evaluate a single repeat sub-cycle of ``block`` for ``t`` in ``[0, 1]``."""
    b_type = block["type"]
    floor_v = float(block["floor"])
    peak_v = float(block["peak"])
    skew_v = _clamp(float(block.get("skew", 0.5)), 0.14, 0.86)
    bend_v = float(block.get("bend", 0.0))
    tc = _clamp(t, 0.0, 1.0)

    if b_type == "hold":
        return _clamp(peak_v)

    if b_type == "line":
        return _clamp(start_h + (peak_v - start_h) * _curve_pow(tc, bend_v))

    if b_type == "saw_up":
        return _clamp(floor_v + (peak_v - floor_v) * _curve_pow(tc, bend_v))

    if b_type == "saw_down":
        return _clamp(floor_v + (peak_v - floor_v) * (1.0 - _curve_pow(tc, -bend_v)))

    if b_type == "ripple":
        osc = 0.5 - 0.5 * math.cos(TAU * tc)
        shaped = _curve_pow(osc, bend_v)
        return _clamp(floor_v + (peak_v - floor_v) * shaped)

    # Default: "wave" (smooth floor -> peak at skew -> floor)
    if tc <= skew_v:
        u1 = _curve_pow(tc / max(1e-4, skew_v), bend_v)
        h = 0.5 - 0.5 * math.cos(math.pi * u1)
    else:
        u2 = _curve_pow((1.0 - tc) / max(1e-4, 1.0 - skew_v), bend_v)
        h = 0.5 - 0.5 * math.cos(math.pi * u2)
    return _clamp(floor_v + (peak_v - floor_v) * h)


def eval_block_pattern(
    spec: dict,
    u: float,
    *,
    intensity_norm: float = 1.0,
    contrast_mode: str = "balanced",
) -> float:
    """Evaluate normalized `[0, 1]` carrier wave of a Shape-Block pattern at loop phase `u`."""
    blocks = spec.get("blocks") or []
    if not blocks:
        return 0.0
    starts = _resolve_block_starts(blocks)
    total_w = sum(max(0.05, float(b.get("width", 1.0))) for b in blocks)
    pos = (float(u) % 1.0) * total_w

    acc = 0.0
    chosen_idx = len(blocks) - 1
    local_frac = 0.0
    for idx, b in enumerate(blocks):
        w = max(0.05, float(b.get("width", 1.0)))
        if pos <= acc + w or idx == len(blocks) - 1:
            chosen_idx = idx
            local_frac = _clamp((pos - acc) / w, 0.0, 0.999999)
            break
        acc += w

    b = blocks[chosen_idx]
    rep = max(1, int(b.get("repeat", 1)))
    scaled = local_frac * rep
    sub_idx = min(rep - 1, int(scaled))
    sub_t = scaled - sub_idx
    start_h = starts[chosen_idx] if sub_idx == 0 else _block_natural_end(b)
    val = _eval_block_subcycle(b, start_h, sub_t)

    if spec.get("lift_high_floor"):
        c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
        thresh = 0.52 if c_gain > 1.1 else 0.62
        hi_blend = _clamp(_clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh)) * c_gain)
        if hi_blend > 0.0:
            floor_lift = 0.42 * hi_blend
            val = floor_lift + (1.0 - floor_lift) * val
    return _clamp(val)


def _choose_harmonic_steps(
    blocks: list[dict],
    requested_steps: int | None = None,
    *,
    loop_dur_s: float = 6.0,
) -> int:
    if requested_steps is not None:
        return max(12, min(50, int(requested_steps)))
    total_beats = max(1, sum(max(1, int(b.get("repeat", 1))) for b in blocks))
    max_by_rate = max(12, min(50, int(round((max(1.2, loop_dur_s) * 1000.0) / 100.0))))
    ideal_n = max(16, min(max_by_rate, int(round(max(1.2, loop_dur_s) / 0.125))))
    lo_cand = max(12, min(max_by_rate, ideal_n - 10))
    hi_cand = max(lo_cand, max_by_rate)

    best_n = ideal_n
    best_score = (999, 999, 0)
    for cand in range(lo_cand, hi_cand + 1):
        rem = cand % total_beats
        score = (rem, abs(cand - ideal_n), -cand)
        if score < best_score:
            best_score = score
            best_n = cand
    return best_n


def _sample_block_phrase_steps(
    spec: dict,
    n: int,
    *,
    intensity_norm: float,
    contrast_mode: str,
) -> list[float]:
    """Sample ``n`` discrete hardware steps, pinning sawtooth cliffs while stitching smooth joints."""
    blocks = spec.get("blocks") or []
    if not blocks:
        return [0.0] * n
    starts = _resolve_block_starts(blocks)
    weights = [max(0.05, float(b.get("width", 1.0))) for b in blocks]
    total_w = sum(weights)

    # Expand sub-cycles with exact proportional widths so every repeat gets crisp step endpoints
    subcycles: list[tuple[dict, float, float, float]] = []
    for idx, b in enumerate(blocks):
        rep = max(1, int(b.get("repeat", 1)))
        sub_w = (weights[idx] / total_w) / rep
        end_h = _block_natural_end(b)
        for r in range(rep):
            s_h = starts[idx] if r == 0 else end_h
            subcycles.append((b, s_h, end_h, sub_w))

    # Allocate integer step counts summing to n via largest-remainder rounding
    raw_counts = [sc[3] * n for sc in subcycles]
    int_counts = [max(1, int(math.floor(rc))) for rc in raw_counts]
    while sum(int_counts) > n:
        widest = max(range(len(int_counts)), key=lambda idx: int_counts[idx])
        if int_counts[widest] <= 1:
            break
        int_counts[widest] -= 1
    remainders = sorted(
        range(len(subcycles)),
        key=lambda idx: (raw_counts[idx] - int_counts[idx]),
        reverse=True,
    )
    rem_idx = 0
    while sum(int_counts) < n:
        int_counts[remainders[rem_idx % len(remainders)]] += 1
        rem_idx += 1

    c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
    thresh = 0.52 if c_gain > 1.1 else 0.62
    hi_blend = (
        _clamp(_clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh)) * c_gain)
        if spec.get("lift_high_floor")
        else 0.0
    )

    out: list[float] = []
    num_sc = len(subcycles)
    for sc_idx, ((b, s_h, e_h, _), m) in enumerate(zip(subcycles, int_counts)):
        next_start_h = subcycles[(sc_idx + 1) % num_sc][1]
        has_cliff = abs(e_h - next_start_h) >= 0.03
        b_type = b["type"]
        pin_both_ends = b_type in ("saw_up", "saw_down") or (b_type == "line" and has_cliff)
        for j in range(m):
            if pin_both_ends:
                t = 1.0 if m == 1 else (j / (m - 1))
            else:
                t = j / m
            w = _eval_block_subcycle(b, s_h, t)
            if hi_blend > 0.0:
                lift = 0.42 * hi_blend
                w = lift + (1.0 - lift) * w
            out.append(_clamp(w))
    return out[:n]


def all_pattern_specs(user_patterns: dict | None = None) -> dict[str, dict]:
    """Return merged map of built-in + custom/overridden pattern metadata."""
    up = user_patterns if user_patterns is not None else _active_user_patterns()
    merged: dict[str, dict] = {}
    for pat_id, base in PATTERN_SPECS.items():
        if pat_id in up and isinstance(up[pat_id], dict):
            clean = sanitize_block_pattern(up[pat_id], fallback_id=pat_id)
            merged[pat_id] = {
                **clean,
                "is_builtin": True,
                "is_customized": True,
            }
        else:
            merged[pat_id] = {
                **base,
                "vibe_tags": list(BUILTIN_EDITOR_TEMPLATES[pat_id]["vibe_tags"]),
                "loop_seconds": BUILTIN_EDITOR_TEMPLATES[pat_id]["loop_seconds"],
                "lift_high_floor": BUILTIN_EDITOR_TEMPLATES[pat_id]["lift_high_floor"],
                "blocks": copy.deepcopy(BUILTIN_EDITOR_TEMPLATES[pat_id]["blocks"]),
                "is_builtin": True,
                "is_customized": False,
            }
    for pat_id, custom in (up or {}).items():
        if pat_id in PATTERN_SPECS or not isinstance(custom, dict):
            continue
        clean = sanitize_block_pattern(custom, fallback_id=pat_id)
        merged[clean["id"]] = {
            **clean,
            "is_builtin": False,
            "is_customized": True,
        }
    return merged


def get_custom_or_override_spec(pattern: str, user_patterns: dict | None = None) -> dict | None:
    up = user_patterns if user_patterns is not None else _active_user_patterns()
    if not up or pattern not in up or not isinstance(up[pattern], dict):
        return None
    return sanitize_block_pattern(up[pattern], fallback_id=pattern)


def normalize_arc(arc: str | None) -> str:
    if not arc:
        return "steady"
    clean = str(arc).strip().lower()
    return clean if clean in SENTENCE_ARCS else "steady"


def sentence_arc_gain(arc: str | None, progress: float) -> float:
    """Within-sentence amplitude envelope multiplier for ``progress`` in ``[0, 1]``."""
    a = normalize_arc(arc)
    p = _clamp(float(progress), 0.0, 1.0)
    if a == "rise":
        # Gradual buildup across the sentence (0.74x -> 1.08x)
        return 0.74 + 0.34 * (0.5 - 0.5 * math.cos(math.pi * p))
    if a == "surge":
        # Cresting swell peaking around mid-sentence (0.80x -> 1.12x -> 0.88x)
        crest = math.sin(math.pi * (p ** 0.82))
        tail = 0.08 * (0.5 - 0.5 * math.cos(math.pi * p))
        return 0.80 + 0.32 * crest + tail * (1.0 - crest)
    if a == "ease":
        # Gentle breath / softening across the sentence (1.00x -> 0.76x)
        return 1.00 - 0.24 * (0.5 - 0.5 * math.cos(math.pi * p))
    return 1.0


def normalize_pattern(name: str | None, user_patterns: dict | None = None) -> str:
    if not name:
        return "velvet"
    clean = str(name).strip().lower()
    clean = LEGACY_ALIASES.get(clean, clean)
    if clean in PATTERN_SPECS:
        return clean
    up = user_patterns if user_patterns is not None else _active_user_patterns()
    if up:
        if clean in up:
            return clean
        slug = slugify_pattern_id(clean)
        if slug in up:
            return slug
    return "velvet"


def _custom_loop_seconds(spec: dict, pace: float | None = None) -> float:
    base_s = max(1.2, min(16.0, float(spec.get("loop_seconds", 5.0))))
    def_pace = _clamp(float(spec.get("default_pace", 0.50)))
    use_pace = def_pace if pace is None else _clamp(float(pace))
    scale = _clamp(1.0 - 0.50 * (use_pace - def_pace), 0.65, 1.45)
    return max(1.2, min(16.0, base_s * scale))


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if val < lo else (hi if val > hi else val)


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 <= edge0:
        return 1.0 if x >= edge1 else 0.0
    t = _clamp((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _carrier_hz(pattern: str, pace: float, m1: float, hi_blend: float) -> float:
    """Instantaneous carrier frequency in Hz, tuned below BLE's Nyquist limit."""
    rubato = 1.0 + 0.10 * m1
    if pattern == "velvet":
        return (0.30 + 0.38 * pace) * rubato
    if pattern == "swell":
        # Long 11.5–15s majestic tide wave (0% -> 100% -> 0%)
        return (0.066 + 0.026 * pace) * (1.0 + 0.06 * m1)
    if pattern == "heartbeat":
        return (0.46 + 0.30 * pace + 0.08 * hi_blend) * (1.0 + 0.06 * m1)
    if pattern == "tease":
        return (0.55 + 0.55 * pace) * rubato
    if pattern == "rhythm":
        return (0.58 + 0.56 * pace + 0.10 * hi_blend) * rubato
    if pattern == "crest":
        return (1.02 + 0.40 * pace) * rubato
    if pattern == "afterglow":
        return (0.18 + 0.14 * pace) * (1.0 + 0.06 * m1)
    return 0.45


def _contrast_hz(pace: float, m1: float) -> float:
    """BLE-friendly High↔Medium contrast rate (~0.95–1.38 Hz)."""
    return (0.95 + 0.42 * pace) * (1.0 + 0.10 * m1)


def _contrast_osc(contrast_phase: float, m2: float) -> float:
    """Smooth High↔Medium anti-numbing wave in [mid_floor, 1.0]."""
    mid_floor = 0.48 + 0.06 * m2
    raw = 0.5 - 0.5 * math.cos(TAU * contrast_phase)
    shaped = raw ** 0.85
    return mid_floor + (1.0 - mid_floor) * shaped


def _eval_single(
    pattern: str,
    phase: float,
    phrase_phase: float,
    contrast_phase: float,
    intensity_norm: float,
    pace: float,
    contrast_gain: float,
    *,
    periodic_lfo: tuple[float, float] | None = None,
) -> float:
    """Pure instantaneous waveform value in [0.0, 1.0]."""
    if periodic_lfo is not None:
        m1, m2 = periodic_lfo
    else:
        m1 = math.sin(TAU * phrase_phase)
        m2 = math.sin(TAU * (phrase_phase / PHI))
    breath = _clamp(0.5 + 0.30 * m1 + 0.20 * m2)

    thresh = 0.52 if contrast_gain > 1.1 else 0.62
    hi_raw = _clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh))
    hi_blend = _clamp(hi_raw * contrast_gain)
    c_wave = _contrast_osc(contrast_phase, m2)

    u = phase % 1.0

    if pattern == "velvet":
        warm = 0.76 + 0.16 * breath + 0.08 * math.sin(TAU * u)
        val = warm * (1.0 - 0.70 * hi_blend) + c_wave * (0.70 * hi_blend)
        return _clamp(val)

    if pattern == "swell":
        # Long gradual tide: brief 0% rest -> slow ~64% buildup -> full crest -> smooth ~30% glide to 0%
        if u < 0.03 or u >= 0.97:
            return 0.0
        crest_u = 0.67 if periodic_lfo is not None else (0.65 + 0.04 * m1)
        if u < crest_u:
            r = (u - 0.03) / (crest_u - 0.03)
            s = (0.5 - 0.5 * math.cos(math.pi * r)) ** 1.18
        else:
            d = (u - crest_u) / (0.97 - crest_u)
            s = (0.5 + 0.5 * math.cos(math.pi * d)) ** 1.10
        peak = 1.0 if periodic_lfo is not None else (0.94 + 0.06 * breath)
        return _clamp(peak * s)

    if pattern == "heartbeat":
        # Crisp paired LUB–DUB punching out of pure 0% silence with a clean brake pause between beats
        if 0.035 <= u < 0.235:
            w_lub = (u - 0.035) / 0.200
            lub = min(1.0, 1.12 * ((0.5 - 0.5 * math.cos(TAU * w_lub)) ** 0.72)) * (0.68 + 0.08 * breath)
            return _clamp(lub)
        if 0.420 <= u < 0.665:
            w_dub = (u - 0.420) / 0.245
            dub = min(1.0, 1.12 * ((0.5 - 0.5 * math.cos(TAU * w_dub)) ** 0.68)) * (0.97 + 0.03 * breath)
            return _clamp(dub)
        return 0.0

    if pattern == "tease":
        bar = (phase % 8.0) / 8.0
        prog = _clamp(bar / 0.68)
        step_wave = 0.5 - 0.5 * math.cos(TAU * u)
        ceil = 0.48 + 0.52 * (prog ** 0.9)
        floor = 0.18 + 0.28 * prog
        climb_val = _clamp(floor + (ceil - floor) * (step_wave ** 0.80))
        brink_val = _clamp(c_wave * (0.94 + 0.06 * m1))
        hush_val = _clamp(0.18 + 0.10 * hi_blend + 0.04 * breath)

        to_brink = _smoothstep(0.64, 0.71, bar)
        to_hush = _smoothstep(0.83, 0.89, bar)
        re_enter = _smoothstep(0.94, 1.00, bar)

        mid = climb_val * (1.0 - to_brink) + brink_val * to_brink
        hushed = mid * (1.0 - to_hush) + hush_val * to_hush
        return _clamp(hushed * (1.0 - re_enter) + 0.18 * re_enter)

    if pattern == "rhythm":
        # Full 0% <-> 100% high-contrast stroke with accented 4th beat and clean zero valleys
        bar_beat = int(math.floor(phase)) % 4
        accent = 1.0 if bar_beat == 3 else (0.90 + 0.06 * breath)
        active_frac = 0.65
        if u >= active_frac:
            return 0.0
        w_u = u / active_frac
        stroke = min(1.0, 1.24 * ((0.5 - 0.5 * math.cos(TAU * w_u)) ** 0.60))
        return _clamp(accent * stroke)

    if pattern == "crest":
        if periodic_lfo is not None:
            # Periodic 2-wave macro envelope across the 50-step phrase
            macro = 0.84 + 0.16 * (0.5 + 0.5 * m2)
        else:
            macro = 0.84 + 0.16 * (0.5 - 0.5 * math.cos(TAU * (phrase_phase * 1.5)))
        carrier_mod = 0.5 - 0.5 * math.cos(TAU * u)
        mid_floor = 0.46 + 0.10 * breath
        fast = mid_floor + (1.0 - mid_floor) * (carrier_mod ** 0.82)
        return _clamp(fast * macro)

    if pattern == "afterglow":
        gentle_swell = 0.5 - 0.5 * math.cos(TAU * u)
        return _clamp(0.20 + 0.78 * gentle_swell * (0.85 + 0.15 * breath))

    return 1.0


def build_lovense_phrase(
    pattern: str,
    *,
    intensity_norm: float,
    pace: float | None = None,
    contrast_mode: str = "balanced",
    loop: bool = True,
    steps: int | None = None,
    entry_norm: float | None = None,
    arc: str = "steady",
    duration_s: float | None = None,
    pattern_def: dict | None = None,
) -> tuple[list[float], int, bool]:
    """Synthesize a Lovense ``Pattern`` phrase: ``(levels, interval_ms, is_loop)``.

    - Supports both procedural built-ins and custom/edited Shape-Block patterns.
    - When ``loop=True`` and ``entry_norm is None`` and ``arc == "steady"``,
      carrier cycles complete exact integer multiples across ``steps`` so the end
      wraps to ``steps[0]`` seamlessly inside Lovense with ``timeSec: 0`` and ZERO
      refresh packets.
    - When ``entry_norm`` or a shaped ``arc`` (``rise`` | ``surge`` | ``ease``) is
      supplied for a Stage sentence cue, the opening ~0.75 s smoothly blends from
      ``entry_norm`` into the sentence's arc contour timed to ``duration_s``.
    - When ``loop=False`` and ``pattern == "afterglow"`` (story wind-down),
      synthesizes a single 9.5 s decaying cosine phrase ending on ``0;0;0;0``.
    """
    custom_spec = (
        sanitize_block_pattern(pattern_def)
        if isinstance(pattern_def, dict)
        else None
    )
    pat = custom_spec["id"] if custom_spec is not None else normalize_pattern(pattern)
    if custom_spec is None:
        custom_spec = get_custom_or_override_spec(pat)

    norm_i = _clamp(float(intensity_norm), 0.0, 1.0)
    c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
    use_arc = normalize_arc(arc)

    if custom_spec is not None:
        use_pace = _clamp(custom_spec["default_pace"] if pace is None else float(pace))
        if pat == "afterglow" and not loop:
            n_decay = max(16, min(50, 50 if steps is None else int(steps)))
            interval_ms = 190
            levels_decay: list[float] = []
            active_n = max(8, n_decay - 4)
            for k in range(n_decay):
                if k >= active_n:
                    levels_decay.append(0.0)
                    continue
                env = 0.5 + 0.5 * math.cos(math.pi * (k / active_n))
                u = (2.0 * (k / active_n)) % 1.0
                w = eval_block_pattern(
                    custom_spec,
                    u,
                    intensity_norm=norm_i * env,
                    contrast_mode=contrast_mode,
                )
                levels_decay.append(round(_clamp(norm_i * env * w), 4))
            return levels_decay, interval_ms, False

        dur_s = _custom_loop_seconds(custom_spec, use_pace)
        n = _choose_harmonic_steps(custom_spec["blocks"], steps, loop_dur_s=dur_s)
        interval_ms = max(100, min(360, int(round((dur_s * 1000.0) / n))))
        unit_steps = _sample_block_phrase_steps(
            custom_spec,
            n,
            intensity_norm=norm_i,
            contrast_mode=contrast_mode,
        )
        shaped_sentence = (entry_norm is not None) or (use_arc != "steady")
        phrase_dur_s = (n * interval_ms) / 1000.0
        win_s = max(1.3, min(phrase_dur_s * 0.85, float(duration_s or 3.4)))
        ramp_s = 0.72
        clamped_entry = None if entry_norm is None else _clamp(float(entry_norm), 0.0, 1.0)

        levels_custom: list[float] = []
        for k, w in enumerate(unit_steps):
            if shaped_sentence:
                t_k = ((k + 0.5) * interval_ms) / 1000.0
                arc_prog = min(1.0, t_k / win_s)
                local_i = _clamp(norm_i * sentence_arc_gain(use_arc, arc_prog), 0.0, 1.0)
                if clamped_entry is not None and t_k < ramp_s:
                    blend = 0.5 - 0.5 * math.cos(math.pi * (t_k / ramp_s))
                    local_i = _clamp(clamped_entry * (1.0 - blend) + local_i * blend, 0.0, 1.0)
            else:
                local_i = norm_i
            levels_custom.append(round(_clamp(w * local_i), 4))
        return levels_custom, interval_ms, True

    spec = PATTERN_SPECS[pat]
    use_pace = _clamp(spec["default_pace"] if pace is None else float(pace))
    default_n = 48 if (loop and pat in ("heartbeat", "tease", "rhythm")) else 50
    n = max(16, min(50, default_n if steps is None else int(steps)))

    if pat == "afterglow" and not loop:
        interval_ms = 190  # 50 * 190 ms = 9.50 s full decay in one packet
        levels: list[float] = []
        active_n = max(8, n - 4)
        for k in range(n):
            if k >= active_n:
                levels.append(0.0)
                continue
            env = 0.5 + 0.5 * math.cos(math.pi * (k / active_n))  # smooth cosine fade 1 -> 0
            u = (2.5 * (k / active_n)) % 1.0
            swell = 0.22 + 0.78 * (0.5 - 0.5 * math.cos(TAU * u))
            levels.append(round(_clamp(norm_i * env * swell), 4))
        return levels, interval_ms, False

    # Loop-seamless phrase grid (integer cycles across n steps)
    if pat == "velvet":
        interval_ms = int(round(160 - 30 * use_pace))
        m_cycles = 3
        k_contrast = 7 + int(round(2 * use_pace))
    elif pat == "swell":
        # 50 steps * 230..300 ms = 11.5..15.0 s single gradual 0% -> 100% -> 0% tide wave
        interval_ms = int(round(300 - 70 * use_pace))
        m_cycles = 1
        k_contrast = 6
    elif pat == "heartbeat":
        # 4 paired LUB–DUB beats across 48 steps (~12 steps / ~1.7–2.0 s per heartbeat)
        interval_ms = int(round(165 - 35 * use_pace))
        m_cycles = 4
        k_contrast = 8
    elif pat == "tease":
        interval_ms = int(round(170 - 35 * use_pace))
        m_cycles = 8  # 1 complete 8-beat climb -> brink -> hush tease arc
        k_contrast = 8
    elif pat == "rhythm":
        interval_ms = int(round(165 - 45 * use_pace))
        m_cycles = 8  # 2 full 4-beat accented bars
        k_contrast = 8
    elif pat == "crest":
        interval_ms = int(round(135 - 20 * use_pace))
        m_cycles = 7 + int(round(2 * use_pace))
        k_contrast = m_cycles
    else:  # looping afterglow preview
        interval_ms = int(round(185 - 30 * use_pace))
        m_cycles = 2
        k_contrast = 6

    interval_ms = max(110, interval_ms)
    shaped_sentence = (entry_norm is not None) or (use_arc != "steady")
    phrase_dur_s = (n * interval_ms) / 1000.0
    win_s = max(1.3, min(phrase_dur_s * 0.85, float(duration_s or 3.4)))
    ramp_s = 0.72
    clamped_entry = None if entry_norm is None else _clamp(float(entry_norm), 0.0, 1.0)

    levels = []
    sub_offsets = (0.125, 0.375, 0.625, 0.875)

    for k in range(n):
        if shaped_sentence:
            t_k = ((k + 0.5) * interval_ms) / 1000.0
            arc_prog = min(1.0, t_k / win_s)
            local_i = _clamp(norm_i * sentence_arc_gain(use_arc, arc_prog), 0.0, 1.0)
            if clamped_entry is not None and t_k < ramp_s:
                blend = 0.5 - 0.5 * math.cos(math.pi * (t_k / ramp_s))
                local_i = _clamp(clamped_entry * (1.0 - blend) + local_i * blend, 0.0, 1.0)
        else:
            local_i = norm_i

        acc = 0.0
        for off in sub_offsets:
            frac_u = (k + off) / n
            theta = TAU * frac_u
            m1 = math.sin(theta)
            m2 = math.sin(2.0 * theta + 0.7)
            rubato = 0.0 if pat in ("swell", "heartbeat", "tease", "rhythm") else (0.04 * m1)
            phase = m_cycles * frac_u + rubato
            c_phase = k_contrast * frac_u + 0.05 * m1
            w = _eval_single(
                pat,
                phase,
                frac_u,
                c_phase,
                local_i,
                use_pace,
                c_gain,
                periodic_lfo=(m1, m2),
            )
            acc += w * local_i
        levels.append(round(_clamp(acc / len(sub_offsets)), 4))

    return levels, interval_ms, True


def sample_pattern_curve(
    pattern: str,
    *,
    duration_s: float = 8.0,
    steps: int = 120,
    intensity: float = 80.0,
    pace: float | None = None,
    contrast_mode: str = "balanced",
    pattern_def: dict | None = None,
) -> list[float]:
    """Deterministic waveform preview samples in [0.0, 1.0] for UI & tests."""
    custom_spec = (
        sanitize_block_pattern(pattern_def)
        if isinstance(pattern_def, dict)
        else get_custom_or_override_spec(normalize_pattern(pattern))
    )
    steps = max(8, int(steps))
    if custom_spec is not None:
        norm_i = _clamp(float(intensity) / 100.0, 0.0, 1.0)
        loop_s = _custom_loop_seconds(custom_spec, pace)
        win_s = max(0.5, float(duration_s))
        samples_custom: list[float] = []
        for idx in range(steps):
            t = (idx / max(1, steps - 1)) * win_s
            u = (t / loop_s) % 1.0
            if custom_spec["id"] == "afterglow":
                env = max(0.0, 1.0 - (t / AFTERGLOW_DECAY_S))
            else:
                env = 1.0
            w = eval_block_pattern(
                custom_spec,
                u,
                intensity_norm=norm_i * env,
                contrast_mode=contrast_mode,
            )
            samples_custom.append(round(_clamp(w * norm_i * env), 4))
        return samples_custom

    pat = normalize_pattern(pattern)
    spec = PATTERN_SPECS[pat]
    use_pace = _clamp(spec["default_pace"] if pace is None else float(pace))
    dt = max(0.01, float(duration_s) / steps)

    vs = VibeState()
    vs.set_target(intensity, pat, pace=use_pace, source="sample")
    vs.phrase_phase = 0.12
    samples: list[float] = []
    for _ in range(steps):
        norm_i = vs.strength / 100.0
        w = vs.advance_and_sample(dt, norm_i, contrast_mode=contrast_mode)
        samples.append(round(_clamp(w * (vs.strength / 100.0)), 4))
    return samples


def pattern_multiplier(
    pattern: str,
    t: float,
    params: dict | None = None,
    *,
    intensity_norm: float = 0.75,
) -> float:
    """Stateless helper for quick evaluation at ``t`` seconds."""
    pat = normalize_pattern(pattern)
    p = params or {}
    contrast_mode = str(p.get("contrast_mode", "balanced"))
    custom_spec = get_custom_or_override_spec(pat)
    if custom_spec is not None:
        pace = _clamp(float(p.get("pace", custom_spec["default_pace"])))
        loop_s = _custom_loop_seconds(custom_spec, pace)
        u = (max(0.0, t) / loop_s) % 1.0
        return eval_block_pattern(custom_spec, u, intensity_norm=intensity_norm, contrast_mode=contrast_mode)

    pace = _clamp(float(p.get("pace", PATTERN_SPECS[pat]["default_pace"])))
    c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
    phrase_phase = max(0.0, t) * (0.105 + 0.045 * pace)
    m1 = math.sin(TAU * phrase_phase)
    thresh = 0.52 if c_gain > 1.1 else 0.62
    hi_blend = _clamp(_clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh)) * c_gain)
    phase = max(0.0, t) * _carrier_hz(pat, pace, m1, hi_blend)
    contrast_phase = max(0.0, t) * _contrast_hz(pace, m1)
    return _eval_single(pat, phase, phrase_phase, contrast_phase, intensity_norm, pace, c_gain)


class VibeState:
    """Latched target and continuous oscillator state."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.strength: float = 0.0
        self.pattern: str = "velvet"
        self.pace: float = 0.45
        self.params: dict = {}
        self.vibe_on: bool = True
        self.latched_at: float = clock()
        self.source: str = ""
        self.version: int = 0

        self.phase: float = 0.0
        self.phrase_phase: float = 0.0
        self.contrast_phase: float = 0.0
        self._prev_pattern: str | None = None
        self._xfade: float = 1.0
        self._afterglow_start_strength: float = 0.0
        self._pattern_def: dict | None = None

    def clone(self) -> VibeState:
        dup = copy.copy(self)
        dup.params = dict(self.params)
        if self._pattern_def is not None:
            dup._pattern_def = copy.deepcopy(self._pattern_def)
        return dup

    def set_target(
        self,
        strength: float,
        pattern: str | None = None,
        params: dict | None = None,
        source: str = "",
        *,
        pace: float | None = None,
        pattern_def: dict | None = None,
    ) -> None:
        new_strength = _clamp(float(strength), 0.0, 100.0)
        clean_def = sanitize_block_pattern(pattern_def) if isinstance(pattern_def, dict) else None
        new_pat = clean_def["id"] if clean_def is not None else normalize_pattern(
            pattern if pattern is not None else self.pattern
        )
        p = dict(params or {})
        spec = clean_def or all_pattern_specs().get(new_pat) or PATTERN_SPECS["velvet"]
        if pace is None:
            pace = p.get("pace", self.pace if pattern is None else spec["default_pace"])
        new_pace = _clamp(float(pace), 0.0, 1.0)

        was_silent = self.strength <= 0.5
        if was_silent and new_strength > 0.5:
            self.phase = 0.0
            self.phrase_phase = 0.0
            self.contrast_phase = 0.0
            self._prev_pattern = None
            self._xfade = 1.0
        elif new_pat != self.pattern and not was_silent:
            self._prev_pattern = self.pattern
            self._xfade = 0.0

        self.strength = new_strength
        self.pattern = new_pat
        self.pace = new_pace
        self.params = p
        self._pattern_def = clean_def
        self.latched_at = self._clock()
        self.source = source
        if new_pat == "afterglow":
            self._afterglow_start_strength = new_strength
        self.version += 1

    def apply_effect(self, effect: dict, source_prefix: str = "") -> None:
        src = effect.get("rule_id") or effect.get("pattern") or effect.get("category") or "jev"
        self.set_target(
            effect.get("strength", 0.0),
            effect.get("pattern") or "velvet",
            effect.get("params"),
            source=f"{source_prefix}{src}",
            pace=effect.get("pace"),
        )

    def clear(self, source: str = "stop") -> None:
        self.strength = 0.0
        self._prev_pattern = None
        self._xfade = 1.0
        self._pattern_def = None
        self.latched_at = self._clock()
        self.source = source
        self.version += 1

    def set_gate(self, on: bool) -> None:
        if self.vibe_on != bool(on):
            self.vibe_on = bool(on)
            self.version += 1

    def _eval_pat_instant(self, pat: str, intensity_norm: float, c_gain: float, contrast_mode: str) -> float:
        custom_spec = self._pattern_def if (self._pattern_def and pat == self.pattern) else get_custom_or_override_spec(pat)
        if custom_spec is not None:
            return eval_block_pattern(
                custom_spec,
                self.phase % 1.0,
                intensity_norm=intensity_norm,
                contrast_mode=contrast_mode,
            )
        return _eval_single(
            pat,
            self.phase,
            self.phrase_phase,
            self.contrast_phase,
            intensity_norm,
            self.pace,
            c_gain,
        )

    def _step_once(
        self,
        dt: float,
        intensity_norm: float,
        c_gain: float,
        contrast_mode: str = "balanced",
        auto_decay: bool = True,
    ) -> float:
        if auto_decay and self.pattern == "afterglow" and self.strength > 0.0 and dt > 0.0:
            rate = max(6.0, self._afterglow_start_strength or 50.0) / AFTERGLOW_DECAY_S
            self.strength = max(0.0, self.strength - rate * dt)
            if self.strength == 0.0:
                self.source = "afterglow:complete"

        m1 = math.sin(TAU * self.phrase_phase)
        thresh = 0.52 if c_gain > 1.1 else 0.62
        hi_blend = _clamp(_clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh)) * c_gain)

        cur = self._eval_pat_instant(self.pattern, intensity_norm, c_gain, contrast_mode)

        if self._prev_pattern is not None and self._xfade < 1.0:
            prev = self._eval_pat_instant(self._prev_pattern, intensity_norm, c_gain, contrast_mode)
            w = prev * (1.0 - self._xfade) + cur * self._xfade
            if dt > 0.0:
                self._xfade = min(1.0, self._xfade + dt / XFADE_S)
                if self._xfade >= 1.0:
                    self._prev_pattern = None
        else:
            w = cur

        if dt > 0.0:
            custom_spec = self._pattern_def or get_custom_or_override_spec(self.pattern)
            if custom_spec is not None:
                hz = 1.0 / _custom_loop_seconds(custom_spec, self.pace)
            else:
                hz = _carrier_hz(self.pattern, self.pace, m1, hi_blend)
            self.phase += dt * hz
            self.phrase_phase += dt * (0.105 + 0.045 * self.pace)
            self.contrast_phase += dt * _contrast_hz(self.pace, m1)
            if self.phase > 1e6:
                self.phase %= 8.0
            if self.contrast_phase > 1e6:
                self.contrast_phase %= 1.0

        return w

    def advance_and_sample(
        self,
        dt: float,
        intensity_norm: float,
        contrast_mode: str = "balanced",
        *,
        auto_decay: bool = True,
    ) -> float:
        """Advance oscillators by ``dt`` seconds with anti-aliased sub-stepping."""
        dt = max(0.0, float(dt))
        c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
        if dt <= 0.04:
            return self._step_once(dt, intensity_norm, c_gain, contrast_mode=contrast_mode, auto_decay=auto_decay)

        sub_n = min(8, max(2, math.ceil(dt / 0.03)))
        sub_dt = dt / sub_n
        acc = 0.0
        for _ in range(sub_n):
            acc += self._step_once(sub_dt, intensity_norm, c_gain, contrast_mode=contrast_mode, auto_decay=auto_decay)
        return acc / sub_n

    def snapshot(self) -> dict:
        spec = self._pattern_def or all_pattern_specs().get(self.pattern) or PATTERN_SPECS["velvet"]
        return {
            "strength": round(self.strength, 1),
            "pattern": self.pattern,
            "pattern_label": spec["label"],
            "pace": round(self.pace, 2),
            "params": dict(self.params),
            "vibe_on": self.vibe_on,
            "source": self.source,
        }
