import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Button, Segmented, TextInput } from './ui';

const SHAPE_PALETTE = [
  { id: 'wave', icon: '⌒', label: 'Wave', hint: 'Smooth arch' },
  { id: 'saw_up', icon: '╱∣', label: 'Saw Up', hint: 'Climb & drop' },
  { id: 'saw_down', icon: '∣╲', label: 'Saw Down', hint: 'Strike & fade' },
  { id: 'line', icon: '╱', label: 'Line / Curve', hint: 'Stitches seamlessly' },
  { id: 'hold', icon: '─', label: 'Hold / Rest', hint: 'Plateau or 0% gap' },
  { id: 'ripple', icon: '∿', label: 'Ripple', hint: 'Fast flutter' },
];

const VIBE_TAGS = [
  { id: 'gentle', label: 'Gentle' },
  { id: 'sensual', label: 'Sensual' },
  { id: 'teasing', label: 'Teasing' },
  { id: 'rhythmic', label: 'Rhythmic' },
  { id: 'building', label: 'Building' },
  { id: 'intense', label: 'Intense' },
  { id: 'afterglow', label: 'Afterglow' },
];

const STARTER_PRESETS = [
  {
    id: 'wave_saw',
    label: 'Wave ×2 → Saw ×2',
    loop_seconds: 6.0,
    lift_high_floor: false,
    vibe_tags: ['rhythmic', 'building'],
    tagline: 'Two smooth waves flowing into two sharp sawtooth climbs.',
    blocks: [
      { type: 'wave', width: 1.0, repeat: 2, peak: 0.92, floor: 0.0, skew: 0.5, bend: 0.0, cycles: 3 },
      { type: 'saw_up', width: 1.0, repeat: 2, peak: 1.0, floor: 0.0, skew: 0.5, bend: -0.15, cycles: 3 },
    ],
  },
  {
    id: 'blank_wave',
    label: 'Single Wave',
    loop_seconds: 5.0,
    lift_high_floor: false,
    vibe_tags: ['sensual'],
    tagline: 'Smooth sculpted wave.',
    blocks: [
      { type: 'wave', width: 1.0, repeat: 1, peak: 1.0, floor: 0.0, skew: 0.55, bend: 0.0, cycles: 3 },
    ],
  },
  {
    id: 'stairs_drop',
    label: 'Rising Climb',
    loop_seconds: 6.5,
    lift_high_floor: false,
    vibe_tags: ['teasing', 'building'],
    tagline: 'Connected rising slopes into a quick release.',
    blocks: [
      { type: 'line', width: 1.0, repeat: 1, peak: 0.48, floor: 0.0, skew: 0.5, bend: 0.15, cycles: 3 },
      { type: 'line', width: 1.0, repeat: 1, peak: 1.0, floor: 0.0, skew: 0.5, bend: -0.2, cycles: 3 },
      { type: 'line', width: 0.7, repeat: 1, peak: 0.0, floor: 0.0, skew: 0.5, bend: 0.2, cycles: 3 },
    ],
  },
  {
    id: 'pulse_flutter',
    label: 'Pulse & Flutter',
    loop_seconds: 5.5,
    lift_high_floor: false,
    vibe_tags: ['teasing', 'intense'],
    tagline: 'Two sharp strikes into a rapid teasing flutter.',
    blocks: [
      { type: 'saw_down', width: 1.0, repeat: 2, peak: 0.95, floor: 0.0, skew: 0.5, bend: -0.25, cycles: 3 },
      { type: 'ripple', width: 1.0, repeat: 1, peak: 1.0, floor: 0.35, skew: 0.5, bend: 0.0, cycles: 4 },
    ],
  },
];

function clamp(v, lo = 0, hi = 1) {
  return v < lo ? lo : v > hi ? hi : v;
}

function applyBend(t, bend) {
  const c = clamp(t, 0, 1);
  if (Math.abs(bend) < 0.02) return c;
  const gamma = Math.pow(2.0, -2.2 * clamp(bend, -0.85, 0.85));
  return Math.pow(c, gamma);
}

function blockNaturalEnd(b) {
  if (b.type === 'wave' || b.type === 'saw_down' || b.type === 'ripple') {
    return clamp(b.floor ?? 0, 0, 1);
  }
  return clamp(b.peak ?? 1, 0, 1);
}

function resolveBlockStarts(blocks) {
  const n = blocks.length;
  if (!n) return [];
  let prevEnd = 0;
  let foundAnchor = false;
  for (let i = n - 1; i >= 0; i -= 1) {
    if (blocks[i].type !== 'line') {
      prevEnd = blockNaturalEnd(blocks[i]);
      foundAnchor = true;
      break;
    }
  }
  if (!foundAnchor) {
    prevEnd = clamp(blocks[n - 1].peak ?? 0, 0, 1);
  }
  const starts = [];
  for (let i = 0; i < n; i += 1) {
    const b = blocks[i];
    if (b.type === 'line') {
      starts.push(prevEnd);
      prevEnd = clamp(b.peak ?? 1, 0, 1);
    } else if (b.type === 'saw_down') {
      const s = clamp(b.peak ?? 1, 0, 1);
      starts.push(s);
      prevEnd = clamp(b.floor ?? 0, 0, 1);
    } else if (b.type === 'hold') {
      const s = clamp(b.peak ?? 0.5, 0, 1);
      starts.push(s);
      prevEnd = s;
    } else {
      const s = clamp(b.floor ?? 0, 0, 1);
      starts.push(s);
      prevEnd = blockNaturalEnd(b);
    }
  }
  return starts;
}

function evalBlockSubcycle(b, startH, subT) {
  const peak = clamp(b.peak ?? 1, 0, 1);
  const floor = Math.min(peak, clamp(b.floor ?? 0, 0, 1));
  const skew = clamp(b.skew ?? 0.5, 0.14, 0.86);
  const bend = clamp(b.bend ?? 0, -0.85, 0.85);
  const t = clamp(subT, 0, 1);

  if (b.type === 'hold') return peak;

  if (b.type === 'line') {
    const shaped = applyBend(t, bend);
    return clamp(startH + (peak - startH) * shaped, 0, 1);
  }

  if (b.type === 'saw_up') {
    const shaped = applyBend(t, bend);
    return clamp(floor + (peak - floor) * shaped, 0, 1);
  }

  if (b.type === 'saw_down') {
    const shaped = applyBend(1.0 - t, bend);
    return clamp(floor + (peak - floor) * shaped, 0, 1);
  }

  if (b.type === 'ripple') {
    const cyc = Math.max(2, Math.min(6, Math.round(b.cycles || 3)));
    let raw = 0.5 - 0.5 * Math.cos(Math.PI * 2 * cyc * t);
    raw = applyBend(raw, bend);
    return clamp(floor + (peak - floor) * raw, 0, 1);
  }

  // wave
  let s;
  if (t <= skew) {
    const r = skew <= 1e-6 ? 1 : t / skew;
    const rb = applyBend(r, bend);
    s = 0.5 - 0.5 * Math.cos(Math.PI * rb);
  } else {
    const d = skew >= 1 - 1e-6 ? 1 : (t - skew) / (1.0 - skew);
    const db = applyBend(1.0 - d, bend);
    s = 0.5 - 0.5 * Math.cos(Math.PI * db);
  }
  return clamp(floor + (peak - floor) * s, 0, 1);
}

function defaultBlockForType(type, prevEnd = 0) {
  if (type === 'hold') {
    return { type: 'hold', width: 0.7, repeat: 1, peak: 0.0, floor: 0.0, skew: 0.5, bend: 0.0, cycles: 3 };
  }
  if (type === 'line') {
    const targetPeak = prevEnd > 0.65 ? 0.15 : 0.95;
    return { type: 'line', width: 1.0, repeat: 1, peak: targetPeak, floor: 0.0, skew: 0.5, bend: 0.0, cycles: 3 };
  }
  if (type === 'saw_up') {
    return { type: 'saw_up', width: 1.0, repeat: 1, peak: 1.0, floor: 0.0, skew: 0.5, bend: 0.0, cycles: 3 };
  }
  if (type === 'saw_down') {
    return { type: 'saw_down', width: 1.0, repeat: 1, peak: 1.0, floor: 0.0, skew: 0.5, bend: -0.2, cycles: 3 };
  }
  if (type === 'ripple') {
    return { type: 'ripple', width: 1.0, repeat: 1, peak: 0.95, floor: 0.3, skew: 0.5, bend: 0.0, cycles: 3 };
  }
  return { type: 'wave', width: 1.0, repeat: 1, peak: 0.95, floor: 0.0, skew: 0.5, bend: 0.0, cycles: 3 };
}

function makeInitialDraft(sourcePattern) {
  if (sourcePattern && Array.isArray(sourcePattern.blocks) && sourcePattern.blocks.length > 0) {
    return {
      id: sourcePattern.id || '',
      label: sourcePattern.label || 'Custom Pattern',
      tagline: sourcePattern.tagline || '',
      loop_seconds: Number(sourcePattern.loop_seconds || 6.0),
      default_pace: Number(sourcePattern.default_pace ?? 0.5),
      lift_high_floor: Boolean(sourcePattern.lift_high_floor),
      vibe_tags: Array.isArray(sourcePattern.vibe_tags) && sourcePattern.vibe_tags.length > 0
        ? [...sourcePattern.vibe_tags]
        : ['sensual'],
      blocks: sourcePattern.blocks.map((b) => ({
        type: b.type || 'wave',
        width: Number(b.width ?? 1.0),
        repeat: Math.max(1, Math.min(8, Math.round(b.repeat || 1))),
        peak: clamp(Number(b.peak ?? 1.0), 0, 1),
        floor: clamp(Number(b.floor ?? 0.0), 0, 1),
        skew: clamp(Number(b.skew ?? 0.5), 0.14, 0.86),
        bend: clamp(Number(b.bend ?? 0.0), -0.85, 0.85),
        cycles: Math.max(2, Math.min(6, Math.round(b.cycles || 3))),
      })),
      is_builtin: Boolean(sourcePattern.is_builtin),
      is_customized: Boolean(sourcePattern.is_customized),
    };
  }
  const preset = STARTER_PRESETS[0];
  return {
    id: '',
    label: 'My Pattern',
    tagline: preset.tagline,
    loop_seconds: preset.loop_seconds,
    default_pace: 0.5,
    lift_high_floor: preset.lift_high_floor,
    vibe_tags: [...preset.vibe_tags],
    blocks: preset.blocks.map((b) => ({ ...b })),
    is_builtin: false,
    is_customized: false,
  };
}

function describeLoopFeel(sec) {
  if (sec <= 2.8) return `${sec.toFixed(1)}s · Quick pulse`;
  if (sec <= 5.5) return `${sec.toFixed(1)}s · Steady phrase`;
  if (sec <= 9.5) return `${sec.toFixed(1)}s · Long rolling arc`;
  return `${sec.toFixed(1)}s · Deep ocean tide`;
}

export default function PatternEditorModal({
  open,
  initialPattern,
  api,
  onClose,
  onSaved,
}) {
  const isEditingExisting = Boolean(initialPattern && initialPattern.id);
  const [draft, setDraft] = useState(() => makeInitialDraft(initialPattern));
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [feelLive, setFeelLive] = useState(false);
  const [feelStrength, setFeelStrength] = useState(72);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  const svgRef = useRef(null);
  const dragRef = useRef(null);
  const feelLiveRef = useRef(false);
  feelLiveRef.current = feelLive;
  const lastBendTapRef = useRef(0);

  useEffect(() => {
    if (open) {
      setDraft(makeInitialDraft(initialPattern));
      setSelectedIdx(0);
      setFeelLive(false);
      setSaveError('');
    }
  }, [open, initialPattern]);

  // Stop live hardware preview when closing editor
  useEffect(() => {
    if (!open) return undefined;
    return () => {
      if (feelLiveRef.current) {
        fetch(`${api}/stop`, { method: 'POST' }).catch(() => {});
      }
    };
  }, [open, api]);

  // Stream live draft updates to Lovense while Feel Live is active
  useEffect(() => {
    if (!open || !feelLive) return undefined;
    const timer = setTimeout(() => {
      fetch(`${api}/patterns/preview-draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pattern: {
            ...draft,
            id: draft.id || 'draft_preview',
          },
          strength: feelStrength,
          seconds: 0,
        }),
      }).catch(() => {});
    }, 170);
    return () => clearTimeout(timer);
  }, [open, feelLive, feelStrength, draft, api]);

  const toggleFeelLive = async () => {
    if (feelLive) {
      setFeelLive(false);
      await fetch(`${api}/stop`, { method: 'POST' }).catch(() => {});
    } else {
      setFeelLive(true);
    }
  };

  const handleClose = async () => {
    if (feelLive) {
      setFeelLive(false);
      await fetch(`${api}/stop`, { method: 'POST' }).catch(() => {});
    }
    onClose();
  };

  const blocks = draft.blocks;
  const activeIdx = Math.min(selectedIdx, Math.max(0, blocks.length - 1));
  const activeBlock = blocks[activeIdx] || blocks[0];

  // Geometry constants for SVG canvas
  const W = 600;
  const H = 224;
  const PAD_L = 20;
  const PAD_R = 20;
  const PAD_T = 22;
  const PAD_B = 26;
  const PLOT_W = W - PAD_L - PAD_R;
  const PLOT_H = H - PAD_T - PAD_B;

  const valToY = useCallback((v) => PAD_T + PLOT_H * (1 - clamp(v, 0, 1)), [PLOT_H]);
  const uToX = useCallback((u) => PAD_L + PLOT_W * clamp(u, 0, 1), [PLOT_W]);

  const layout = useMemo(() => {
    const starts = resolveBlockStarts(blocks);
    const weights = blocks.map((b) => Math.max(0.12, Number(b.width || 1)));
    const totalW = weights.reduce((a, b) => a + b, 0) || 1;
    let acc = 0;
    const spans = blocks.map((b, idx) => {
      const u0 = acc / totalW;
      acc += weights[idx];
      const u1 = idx === blocks.length - 1 ? 1.0 : acc / totalW;
      return {
        idx,
        block: b,
        startH: starts[idx],
        endH: blockNaturalEnd(b),
        u0,
        u1,
        uSpan: Math.max(1e-4, u1 - u0),
        x0: uToX(u0),
        x1: uToX(u1),
      };
    });

    // Sample high-resolution continuous path across 0..1
    const sampleCount = 240;
    const pts = [];
    for (let i = 0; i <= sampleCount; i += 1) {
      const u = i / sampleCount;
      let sp = spans[spans.length - 1];
      for (let k = 0; k < spans.length; k += 1) {
        if (u <= spans[k].u1 || k === spans.length - 1) {
          sp = spans[k];
          break;
        }
      }
      const localFrac = sp.uSpan <= 1e-6 ? 0 : clamp((u - sp.u0) / sp.uSpan, 0, 0.999999);
      const rep = Math.max(1, Math.round(sp.block.repeat || 1));
      const scaled = localFrac * rep;
      const subIdx = Math.min(rep - 1, Math.floor(scaled));
      const subT = u >= 0.999999 && sp.idx === spans.length - 1 ? 1.0 : scaled - subIdx;
      const subStartH = subIdx === 0 ? sp.startH : sp.endH;
      const val = evalBlockSubcycle(sp.block, subStartH, subT);
      pts.push([uToX(u).toFixed(1), valToY(val).toFixed(1)]);
    }
    const linePath = `M ${pts.map((p) => p.join(',')).join(' L ')}`;
    const areaPath = `${linePath} L ${uToX(1).toFixed(1)},${valToY(0).toFixed(1)} L ${uToX(0).toFixed(1)},${valToY(0).toFixed(1)} Z`;

    return { spans, totalW, linePath, areaPath };
  }, [blocks, uToX, valToY]);

  const activeSpan = layout.spans[activeIdx] || layout.spans[0];

  // Compute interactive handles for the selected block (anchored on sub-cycle 0)
  const handles = useMemo(() => {
    if (!activeSpan) return null;
    const b = activeSpan.block;
    const rep = Math.max(1, Math.round(b.repeat || 1));
    const subU0 = activeSpan.u0;
    const subUSpan = activeSpan.uSpan / rep;
    const subU1 = subU0 + subUSpan;
    const peak = clamp(b.peak ?? 1, 0, 1);
    const floor = Math.min(peak, clamp(b.floor ?? 0, 0, 1));
    const skew = clamp(b.skew ?? 0.5, 0.14, 0.86);

    let peakU = subU0 + 0.5 * subUSpan;
    if (b.type === 'wave') peakU = subU0 + skew * subUSpan;
    else if (b.type === 'saw_up' || b.type === 'line') peakU = subU1;
    else if (b.type === 'saw_down') peakU = subU0;
    else if (b.type === 'ripple') {
      const cyc = Math.max(2, Math.min(6, Math.round(b.cycles || 3)));
      peakU = subU0 + (0.5 / cyc) * subUSpan;
    }

    let floorHandle = null;
    if (b.type === 'wave' || b.type === 'saw_up') {
      floorHandle = { x: uToX(subU0), y: valToY(floor), val: floor };
    } else if (b.type === 'saw_down') {
      floorHandle = { x: uToX(subU1), y: valToY(floor), val: floor };
    } else if (b.type === 'ripple') {
      floorHandle = { x: uToX(subU0), y: valToY(floor), val: floor };
    }

    let bendHandle = null;
    if (b.type === 'wave' || b.type === 'saw_up' || b.type === 'saw_down' || b.type === 'line') {
      const subT = b.type === 'wave' ? 0.5 * skew : 0.5;
      const bendU = subU0 + subT * subUSpan;
      const bendVal = evalBlockSubcycle(b, activeSpan.startH, subT);
      bendHandle = { x: uToX(bendU), y: valToY(bendVal), bend: b.bend || 0 };
    }

    let stitchDot = null;
    if (b.type === 'line') {
      stitchDot = { x: uToX(subU0), y: valToY(activeSpan.startH) };
    }

    return {
      subU0,
      subUSpan,
      peakHandle: {
        x: uToX(peakU),
        y: valToY(peak),
        canMoveX: b.type === 'wave',
      },
      floorHandle,
      bendHandle,
      stitchDot,
    };
  }, [activeSpan, uToX, valToY]);

  const clientToNorm = (e) => {
    const svg = svgRef.current;
    if (!svg) return { u: 0.5, yNorm: 0.5 };
    const rect = svg.getBoundingClientRect();
    const vx = ((e.clientX - rect.left) / Math.max(1, rect.width)) * W;
    const vy = ((e.clientY - rect.top) / Math.max(1, rect.height)) * H;
    const u = clamp((vx - PAD_L) / PLOT_W, 0, 1);
    const yNorm = clamp(1 - (vy - PAD_T) / PLOT_H, 0, 1);
    return { u, yNorm };
  };

  const updateBlock = useCallback((idx, updater) => {
    setDraft((prev) => {
      const nextBlocks = prev.blocks.map((b, i) => (i === idx ? updater({ ...b }) : b));
      return { ...prev, blocks: nextBlocks };
    });
  }, []);

  const startHandleDrag = (kind, extra = {}) => (e) => {
    e.stopPropagation();
    if (e.button !== undefined && e.button !== 0) return;

    if (kind === 'bend') {
      const now = Date.now();
      if (now - lastBendTapRef.current < 320) {
        updateBlock(activeIdx, (b) => ({ ...b, bend: 0 }));
        lastBendTapRef.current = 0;
        return;
      }
      lastBendTapRef.current = now;
    }

    if (svgRef.current && e.pointerId !== undefined) {
      try {
        svgRef.current.setPointerCapture(e.pointerId);
      } catch (err) {
        /* ignore */
      }
    }
    dragRef.current = {
      pointerId: e.pointerId,
      kind,
      blockIdx: activeIdx,
      ...extra,
    };
  };

  const onCanvasPointerDown = (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    const { u } = clientToNorm(e);
    for (let i = 0; i < layout.spans.length; i += 1) {
      if (u <= layout.spans[i].u1 || i === layout.spans.length - 1) {
        setSelectedIdx(i);
        break;
      }
    }
  };

  const onCanvasPointerMove = (e) => {
    const d = dragRef.current;
    if (!d || (d.pointerId !== undefined && e.pointerId !== d.pointerId)) return;
    const { u, yNorm } = clientToNorm(e);

    if (d.kind === 'divider') {
      const leftIdx = d.leftIdx;
      const rightIdx = leftIdx + 1;
      setDraft((prev) => {
        if (!prev.blocks[leftIdx] || !prev.blocks[rightIdx]) return prev;
        const weights = prev.blocks.map((b) => Math.max(0.12, Number(b.width || 1)));
        const totalW = weights.reduce((a, b) => a + b, 0) || 1;
        let beforeU = 0;
        for (let i = 0; i < leftIdx; i += 1) beforeU += weights[i] / totalW;
        const pairSpanU = (weights[leftIdx] + weights[rightIdx]) / totalW;
        const pairWeightSum = weights[leftIdx] + weights[rightIdx];
        if (pairSpanU <= 0.02) return prev;

        let frac = clamp((u - beforeU) / pairSpanU, 0.14, 0.86);
        const snaps = [0.25, 1 / 3, 0.5, 2 / 3, 0.75];
        for (let s = 0; s < snaps.length; s += 1) {
          if (Math.abs(frac - snaps[s]) < 0.028) {
            frac = snaps[s];
            break;
          }
        }
        const nextBlocks = prev.blocks.map((b, idx) => {
          if (idx === leftIdx) return { ...b, width: Number((pairWeightSum * frac).toFixed(3)) };
          if (idx === rightIdx) return { ...b, width: Number((pairWeightSum * (1 - frac)).toFixed(3)) };
          return b;
        });
        return { ...prev, blocks: nextBlocks };
      });
      return;
    }

    if (d.kind === 'peak') {
      updateBlock(d.blockIdx, (b) => {
        const nextPeak = Number(clamp(yNorm, 0, 1).toFixed(3));
        const nextFloor = Math.min(nextPeak, b.floor ?? 0);
        let nextSkew = b.skew ?? 0.5;
        if (b.type === 'wave' && d.subUSpan > 1e-4) {
          let rawSkew = clamp((u - d.subU0) / d.subUSpan, 0.14, 0.86);
          if (Math.abs(rawSkew - 0.5) < 0.03) rawSkew = 0.5;
          nextSkew = Number(rawSkew.toFixed(3));
        }
        return { ...b, peak: nextPeak, floor: nextFloor, skew: nextSkew };
      });
      return;
    }

    if (d.kind === 'floor') {
      updateBlock(d.blockIdx, (b) => {
        const nextFloor = Number(clamp(yNorm, 0, 0.95).toFixed(3));
        const nextPeak = Math.max(b.peak ?? 1, nextFloor);
        return { ...b, floor: nextFloor, peak: nextPeak };
      });
      return;
    }

    if (d.kind === 'bend') {
      const startH = d.startH ?? 0;
      updateBlock(d.blockIdx, (b) => {
        const peak = clamp(b.peak ?? 1, 0, 1);
        const floor = Math.min(peak, clamp(b.floor ?? 0, 0, 1));
        let nextBend = 0;
        if (b.type === 'line') {
          const span = peak - startH;
          if (Math.abs(span) >= 0.06) {
            const frac = clamp((yNorm - startH) / span, 0.08, 0.92);
            const gamma = Math.log(frac) / Math.log(0.5);
            nextBend = clamp(-Math.log2(gamma) / 2.2, -0.85, 0.85);
          }
        } else if (b.type === 'wave') {
          const span = Math.max(0.08, peak - floor);
          const s = clamp((yNorm - floor) / span, 0.06, 0.94);
          const rb = clamp(Math.acos(1 - 2 * s) / Math.PI, 0.08, 0.92);
          const gamma = Math.log(rb) / Math.log(0.5);
          nextBend = clamp(-Math.log2(gamma) / 2.2, -0.85, 0.85);
        } else {
          const span = Math.max(0.08, peak - floor);
          const frac = clamp((yNorm - floor) / span, 0.08, 0.92);
          const gamma = Math.log(frac) / Math.log(0.5);
          nextBend = clamp(-Math.log2(gamma) / 2.2, -0.85, 0.85);
        }
        if (Math.abs(nextBend) < 0.04) nextBend = 0;
        return { ...b, bend: Number(nextBend.toFixed(3)) };
      });
    }
  };

  const endCanvasDrag = (e) => {
    const d = dragRef.current;
    if (!d || (e && d.pointerId !== undefined && e.pointerId !== d.pointerId)) return;
    dragRef.current = null;
  };

  // Block list mutations
  const setActiveShape = (newType) => {
    const prevEnd = activeIdx > 0
      ? layout.spans[activeIdx - 1]?.endH ?? 0
      : layout.spans[layout.spans.length - 1]?.endH ?? 0;
    updateBlock(activeIdx, (b) => {
      if (b.type === newType) return b;
      if (newType === 'hold') {
        return { ...b, type: 'hold', peak: b.peak > 0.15 ? b.peak : 0.0, bend: 0 };
      }
      if (newType === 'line') {
        const suggestedPeak = Math.abs((b.peak ?? 1) - prevEnd) < 0.08
          ? (prevEnd > 0.6 ? 0.1 : 0.95)
          : (b.peak ?? 0.95);
        return { ...b, type: 'line', peak: suggestedPeak, bend: 0 };
      }
      return {
        ...b,
        type: newType,
        peak: Math.max(0.25, b.peak ?? 0.95),
        bend: newType === 'saw_down' ? -0.15 : 0,
      };
    });
  };

  const addBlock = () => {
    if (blocks.length >= 12) return;
    const prevSpan = layout.spans[activeIdx] || layout.spans[layout.spans.length - 1];
    const nextB = defaultBlockForType('wave', prevSpan?.endH ?? 0);
    setDraft((prev) => {
      const copy = [...prev.blocks];
      copy.splice(activeIdx + 1, 0, nextB);
      return { ...prev, blocks: copy };
    });
    setSelectedIdx(activeIdx + 1);
  };

  const duplicateBlock = () => {
    if (blocks.length >= 12 || !activeBlock) return;
    setDraft((prev) => {
      const copy = [...prev.blocks];
      copy.splice(activeIdx + 1, 0, { ...activeBlock });
      return { ...prev, blocks: copy };
    });
    setSelectedIdx(activeIdx + 1);
  };

  const splitRepeats = () => {
    if (!activeBlock || (activeBlock.repeat || 1) < 2) return;
    const rep = Math.max(2, Math.round(activeBlock.repeat));
    if (blocks.length + rep - 1 > 12) return;
    const perWidth = Number(((activeBlock.width || 1) / rep).toFixed(3));
    const expanded = Array.from({ length: rep }, () => ({
      ...activeBlock,
      repeat: 1,
      width: Math.max(0.15, perWidth),
    }));
    setDraft((prev) => {
      const copy = [...prev.blocks];
      copy.splice(activeIdx, 1, ...expanded);
      return { ...prev, blocks: copy };
    });
  };

  const moveBlock = (dir) => {
    const target = activeIdx + dir;
    if (target < 0 || target >= blocks.length) return;
    setDraft((prev) => {
      const copy = [...prev.blocks];
      const [item] = copy.splice(activeIdx, 1);
      copy.splice(target, 0, item);
      return { ...prev, blocks: copy };
    });
    setSelectedIdx(target);
  };

  const removeBlock = () => {
    if (blocks.length <= 1) return;
    setDraft((prev) => {
      const copy = prev.blocks.filter((_, idx) => idx !== activeIdx);
      return { ...prev, blocks: copy };
    });
    setSelectedIdx(Math.max(0, activeIdx - 1));
  };

  const applyStarterPreset = (preset) => {
    setDraft((prev) => ({
      ...prev,
      tagline: preset.tagline,
      loop_seconds: preset.loop_seconds,
      lift_high_floor: preset.lift_high_floor,
      vibe_tags: [...preset.vibe_tags],
      blocks: preset.blocks.map((b) => ({ ...b })),
    }));
    setSelectedIdx(0);
  };

  const toggleVibeTag = (tagId) => {
    setDraft((prev) => {
      const cur = new Set(prev.vibe_tags || []);
      if (cur.has(tagId)) {
        if (cur.size <= 1) return prev;
        cur.delete(tagId);
      } else {
        cur.add(tagId);
      }
      return { ...prev, vibe_tags: Array.from(cur) };
    });
  };

  const submitSave = async (asCopy = false) => {
    const cleanLabel = (draft.label || '').trim();
    if (!cleanLabel) {
      setSaveError('Give your pattern a short name first.');
      return;
    }
    setSaving(true);
    setSaveError('');
    try {
      const payload = {
        create_new: asCopy || !isEditingExisting,
        previous_id: !asCopy && isEditingExisting ? initialPattern.id : undefined,
        pattern: {
          ...draft,
          id: asCopy ? '' : draft.id,
          label: asCopy && cleanLabel === initialPattern?.label ? `${cleanLabel} Copy` : cleanLabel,
        },
      };
      const res = await fetch(`${api}/patterns/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (feelLive) {
        setFeelLive(false);
        await fetch(`${api}/stop`, { method: 'POST' }).catch(() => {});
      }
      onSaved(data);
    } catch (e) {
      setSaveError('Could not save pattern — check backend connection.');
    } finally {
      setSaving(false);
    }
  };

  const handleResetBuiltin = async () => {
    if (!initialPattern?.id) return;
    setSaving(true);
    try {
      const res = await fetch(`${api}/patterns/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: initialPattern.id }),
      });
      if (res.ok) {
        const data = await res.json();
        if (feelLive) {
          setFeelLive(false);
          await fetch(`${api}/stop`, { method: 'POST' }).catch(() => {});
        }
        onSaved(data);
      }
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  const shapeMeta = SHAPE_PALETTE.find((s) => s.id === activeBlock?.type) || SHAPE_PALETTE[0];

  return (
    <div
      className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-end sm:items-center justify-center p-0 sm:p-4"
      onClick={handleClose}
    >
      <div
        className="w-full sm:max-w-2xl max-h-[94vh] flex flex-col rounded-t-2xl sm:rounded-2xl bg-gray-900 border border-gray-700 shadow-2xl overflow-hidden text-gray-100"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Sticky top header */}
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-800 bg-gray-950/90 shrink-0">
          <div className="min-w-0">
            <h2 className="font-semibold text-base text-gray-100 truncate">
              {isEditingExisting
                ? `Edit Pattern · ${initialPattern.label}`
                : 'New Custom Pattern'}
            </h2>
            <p className="text-[11px] text-gray-400">
              Grab handles right on the curve — shapes connect edge-to-edge with zero gaps.
            </p>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="w-8 h-8 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center justify-center shrink-0"
            title="Close editor"
          >
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Quick starter templates when creating a new pattern */}
          {!isEditingExisting && (
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
              <span className="text-[11px] text-gray-400 shrink-0 mr-1">Start from:</span>
              {STARTER_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => applyStarterPreset(preset)}
                  className="px-2.5 py-1 rounded-full text-[11px] font-medium bg-gray-800 hover:bg-gray-700 text-purple-200 border border-gray-700 shrink-0 transition-colors"
                >
                  {preset.label}
                </button>
              ))}
            </div>
          )}

          {/* Interactive Touch Curve Canvas */}
          <div className="rounded-xl border border-gray-700/90 bg-gray-950 p-2 space-y-1.5">
            <div className="flex items-center justify-between px-1 text-[11px] text-gray-400">
              <span>
                Block {activeIdx + 1}/{blocks.length}:{' '}
                <strong className="text-purple-300">
                  {shapeMeta.icon} {shapeMeta.label}
                  {activeBlock.repeat > 1 ? ` ×${activeBlock.repeat}` : ''}
                </strong>
              </span>
              <span className="font-mono text-gray-400">{describeLoopFeel(draft.loop_seconds)}</span>
            </div>

            <svg
              ref={svgRef}
              viewBox={`0 0 ${W} ${H}`}
              onPointerDown={onCanvasPointerDown}
              onPointerMove={onCanvasPointerMove}
              onPointerUp={endCanvasDrag}
              onPointerCancel={endCanvasDrag}
              style={{ touchAction: 'none' }}
              className="w-full h-52 sm:h-60 rounded-lg bg-gray-950 select-none cursor-crosshair"
            >
              <defs>
                <linearGradient id="pe-fill-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ec4899" stopOpacity="0.44" />
                  <stop offset="60%" stopColor="#a855f7" stopOpacity="0.18" />
                  <stop offset="100%" stopColor="#581c87" stopOpacity="0.03" />
                </linearGradient>
              </defs>

              {/* Horizontal guide lines: 100%, 50%, 0% */}
              <line x1={PAD_L} y1={valToY(1)} x2={W - PAD_R} y2={valToY(1)} stroke="#1f2937" strokeWidth="1" />
              <line
                x1={PAD_L}
                y1={valToY(0.5)}
                x2={W - PAD_R}
                y2={valToY(0.5)}
                stroke="#374151"
                strokeDasharray="4 4"
                strokeWidth="1"
              />
              <line x1={PAD_L} y1={valToY(0)} x2={W - PAD_R} y2={valToY(0)} stroke="#374151" strokeWidth="1.2" />

              {/* Selected block column highlight & sub-repeat guides */}
              {layout.spans.map((sp) => {
                const isSel = sp.idx === activeIdx;
                const rep = Math.max(1, Math.round(sp.block.repeat || 1));
                const subLines = [];
                for (let r = 1; r < rep; r += 1) {
                  const rx = uToX(sp.u0 + (sp.uSpan * r) / rep);
                  subLines.push(
                    <line
                      key={`rep-${sp.idx}-${r}`}
                      x1={rx}
                      y1={PAD_T}
                      x2={rx}
                      y2={H - PAD_B}
                      stroke={isSel ? '#a855f7' : '#4b5563'}
                      strokeOpacity={isSel ? '0.45' : '0.3'}
                      strokeDasharray="3 4"
                      strokeWidth="1"
                    />
                  );
                }
                return (
                  <g key={`col-${sp.idx}`}>
                    {isSel && (
                      <rect
                        x={sp.x0}
                        y={PAD_T - 6}
                        width={Math.max(2, sp.x1 - sp.x0)}
                        height={PLOT_H + 12}
                        rx="6"
                        fill="#a855f7"
                        fillOpacity="0.10"
                      />
                    )}
                    {subLines}
                    {/* Label at bottom of each block column */}
                    <text
                      x={(sp.x0 + sp.x1) / 2}
                      y={H - 7}
                      textAnchor="middle"
                      fontSize="11"
                      fill={isSel ? '#e9d5ff' : '#9ca3af'}
                      fontWeight={isSel ? '600' : '400'}
                    >
                      {sp.block.repeat > 1 ? `${sp.idx + 1} · ×${sp.block.repeat}` : `${sp.idx + 1}`}
                    </text>
                  </g>
                );
              })}

              {/* Filled waveform & crisp continuous curve */}
              <path d={layout.areaPath} fill="url(#pe-fill-grad)" />
              <path
                d={layout.linePath}
                fill="none"
                stroke="#f472b6"
                strokeWidth="3"
                strokeLinejoin="round"
                strokeLinecap="round"
              />

              {/* Draggable vertical block dividers */}
              {layout.spans.slice(0, -1).map((sp) => {
                const dx = sp.x1;
                const midY = PAD_T + PLOT_H * 0.5;
                return (
                  <g key={`div-${sp.idx}`} className="cursor-ew-resize">
                    <line
                      x1={dx}
                      y1={PAD_T - 4}
                      x2={dx}
                      y2={H - PAD_B + 4}
                      stroke="#9ca3af"
                      strokeOpacity="0.65"
                      strokeWidth="1.6"
                    />
                    <rect
                      x={dx - 7}
                      y={midY - 14}
                      width="14"
                      height="28"
                      rx="6"
                      fill="#1f2937"
                      stroke="#c084fc"
                      strokeWidth="1.4"
                    />
                    <line x1={dx - 2} y1={midY - 6} x2={dx - 2} y2={midY + 6} stroke="#e9d5ff" strokeWidth="1.2" />
                    <line x1={dx + 2} y1={midY - 6} x2={dx + 2} y2={midY + 6} stroke="#e9d5ff" strokeWidth="1.2" />
                    {/* Wide touch hit zone */}
                    <rect
                      x={dx - 20}
                      y={PAD_T - 8}
                      width="40"
                      height={PLOT_H + 16}
                      fill="transparent"
                      onPointerDown={startHandleDrag('divider', { leftIdx: sp.idx })}
                    />
                  </g>
                );
              })}

              {/* Stitched start indicator for Line blocks */}
              {handles?.stitchDot && (
                <circle
                  cx={handles.stitchDot.x}
                  cy={handles.stitchDot.y}
                  r="5"
                  fill="#111827"
                  stroke="#a855f7"
                  strokeWidth="2"
                />
              )}

              {/* Floor / Valley handle (hollow teal ring) */}
              {handles?.floorHandle && (
                <g className="cursor-ns-resize">
                  <circle
                    cx={handles.floorHandle.x}
                    cy={handles.floorHandle.y}
                    r="7.5"
                    fill="#0f172a"
                    stroke="#2dd4bf"
                    strokeWidth="2.6"
                  />
                  <circle
                    cx={handles.floorHandle.x}
                    cy={handles.floorHandle.y}
                    r="23"
                    fill="transparent"
                    onPointerDown={startHandleDrag('floor')}
                  />
                </g>
              )}

              {/* Curve Bend diamond handle */}
              {handles?.bendHandle && (
                <g className="cursor-ns-resize">
                  <polygon
                    points={`${handles.bendHandle.x},${handles.bendHandle.y - 8} ${handles.bendHandle.x + 8},${handles.bendHandle.y} ${handles.bendHandle.x},${handles.bendHandle.y + 8} ${handles.bendHandle.x - 8},${handles.bendHandle.y}`}
                    fill="#fbbf24"
                    stroke="#111827"
                    strokeWidth="2"
                  />
                  <circle
                    cx={handles.bendHandle.x}
                    cy={handles.bendHandle.y}
                    r="23"
                    fill="transparent"
                    onPointerDown={startHandleDrag('bend', { startH: activeSpan.startH })}
                  />
                </g>
              )}

              {/* Peak handle (primary pink/white knob) */}
              {handles?.peakHandle && (
                <g className={handles.peakHandle.canMoveX ? 'cursor-move' : 'cursor-ns-resize'}>
                  <circle
                    cx={handles.peakHandle.x}
                    cy={handles.peakHandle.y}
                    r="13"
                    fill="#ec4899"
                    fillOpacity="0.24"
                  />
                  <circle
                    cx={handles.peakHandle.x}
                    cy={handles.peakHandle.y}
                    r="8.5"
                    fill="#f472b6"
                    stroke="#ffffff"
                    strokeWidth="2.4"
                  />
                  <circle
                    cx={handles.peakHandle.x}
                    cy={handles.peakHandle.y}
                    r="25"
                    fill="transparent"
                    onPointerDown={startHandleDrag('peak', {
                      subU0: handles.subU0,
                      subUSpan: handles.subUSpan,
                    })}
                  />
                </g>
              )}
            </svg>

            {/* Compact legend + quick reset bend pill */}
            <div className="flex items-center justify-between flex-wrap gap-2 px-1 text-[11px] text-gray-400">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="flex items-center gap-1">
                  <span className="inline-block w-2.5 h-2.5 rounded-full bg-pink-400 border border-white" />
                  Peak{activeBlock?.type === 'wave' ? ' & Lean' : ''}
                </span>
                {handles?.bendHandle && (
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-2 rotate-45 bg-amber-400" />
                    Curve bend
                  </span>
                )}
                {handles?.floorHandle && (
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-teal-400" />
                    Valley floor
                  </span>
                )}
                {blocks.length > 1 && (
                  <span className="text-gray-500">┃ Drag bars for width</span>
                )}
              </div>

              {Math.abs(activeBlock?.bend || 0) >= 0.04 && (
                <button
                  type="button"
                  onClick={() => updateBlock(activeIdx, (b) => ({ ...b, bend: 0 }))}
                  className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-amber-300 border border-gray-700 text-[10px]"
                >
                  Straighten curve
                </button>
              )}
            </div>
          </div>

          {/* Sequence Strip: tap blocks or add more */}
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
              {blocks.map((b, idx) => {
                const sm = SHAPE_PALETTE.find((s) => s.id === b.type) || SHAPE_PALETTE[0];
                const isSel = idx === activeIdx;
                return (
                  <button
                    key={`chip-${idx}`}
                    type="button"
                    onClick={() => setSelectedIdx(idx)}
                    className={`px-3 py-2 rounded-xl text-xs font-medium flex items-center gap-1.5 shrink-0 border transition-colors ${
                      isSel
                        ? 'bg-purple-600 text-white border-purple-400 shadow-sm'
                        : 'bg-gray-800/90 hover:bg-gray-700 text-gray-300 border-gray-700'
                    }`}
                  >
                    <span className="opacity-70">{idx + 1}.</span>
                    <span>{sm.icon}</span>
                    <span>{sm.label}</span>
                    {b.repeat > 1 && (
                      <span className="px-1.5 py-0.5 rounded-full bg-black/30 text-[10px] font-mono">
                        ×{b.repeat}
                      </span>
                    )}
                  </button>
                );
              })}
              {blocks.length < 12 && (
                <button
                  type="button"
                  onClick={addBlock}
                  className="px-3 py-2 rounded-xl text-xs font-semibold bg-gray-900 hover:bg-gray-800 text-purple-300 border border-dashed border-purple-500/50 shrink-0"
                >
                  + Add Shape
                </button>
              )}
            </div>

            {/* Repeat stepper + block actions */}
            <div className="flex items-center justify-between gap-2 flex-wrap bg-gray-800/50 border border-gray-700/70 rounded-xl px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-300 font-medium">Repeat block</span>
                <div className="inline-flex items-center rounded-lg bg-gray-900 border border-gray-700 overflow-hidden">
                  <button
                    type="button"
                    disabled={(activeBlock?.repeat || 1) <= 1}
                    onClick={() =>
                      updateBlock(activeIdx, (b) => ({
                        ...b,
                        repeat: Math.max(1, (b.repeat || 1) - 1),
                      }))
                    }
                    className="w-9 h-8 text-sm font-bold text-gray-200 hover:bg-gray-800 disabled:opacity-35"
                  >
                    −
                  </button>
                  <span className="px-2.5 text-xs font-mono text-purple-300 select-none">
                    {activeBlock?.repeat || 1}×
                  </span>
                  <button
                    type="button"
                    disabled={(activeBlock?.repeat || 1) >= 8}
                    onClick={() =>
                      updateBlock(activeIdx, (b) => ({
                        ...b,
                        repeat: Math.min(8, (b.repeat || 1) + 1),
                      }))
                    }
                    className="w-9 h-8 text-sm font-bold text-gray-200 hover:bg-gray-800 disabled:opacity-35"
                  >
                    +
                  </button>
                </div>
                {(activeBlock?.repeat || 1) >= 2 && blocks.length + (activeBlock.repeat - 1) <= 12 && (
                  <button
                    type="button"
                    onClick={splitRepeats}
                    title="Expand repeats into separate blocks so you can sculpt each one individually"
                    className="px-2.5 py-1.5 rounded-lg text-xs bg-gray-900 hover:bg-gray-800 text-purple-200 border border-gray-700"
                  >
                    Split
                  </button>
                )}
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  disabled={activeIdx <= 0}
                  onClick={() => moveBlock(-1)}
                  title="Move block left"
                  className="px-2.5 py-1.5 rounded-lg text-xs bg-gray-900 hover:bg-gray-800 text-gray-300 border border-gray-700 disabled:opacity-35"
                >
                  ◀
                </button>
                <button
                  type="button"
                  disabled={activeIdx >= blocks.length - 1}
                  onClick={() => moveBlock(1)}
                  title="Move block right"
                  className="px-2.5 py-1.5 rounded-lg text-xs bg-gray-900 hover:bg-gray-800 text-gray-300 border border-gray-700 disabled:opacity-35"
                >
                  ▶
                </button>
                <button
                  type="button"
                  disabled={blocks.length >= 12}
                  onClick={duplicateBlock}
                  title="Duplicate block"
                  className="px-2.5 py-1.5 rounded-lg text-xs bg-gray-900 hover:bg-gray-800 text-gray-300 border border-gray-700 disabled:opacity-35"
                >
                  Copy
                </button>
                <button
                  type="button"
                  disabled={blocks.length <= 1}
                  onClick={removeBlock}
                  title="Remove block"
                  className="px-2.5 py-1.5 rounded-lg text-xs bg-red-950/60 hover:bg-red-900/70 text-red-200 border border-red-800/60 disabled:opacity-35"
                >
                  Delete
                </button>
              </div>
            </div>
          </div>

          {/* 6-Shape Tap Palette */}
          <div className="space-y-2">
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {SHAPE_PALETTE.map((sh) => {
                const active = activeBlock?.type === sh.id;
                return (
                  <button
                    key={sh.id}
                    type="button"
                    onClick={() => setActiveShape(sh.id)}
                    className={`p-2.5 rounded-xl border text-center flex flex-col items-center gap-0.5 transition-colors ${
                      active
                        ? 'bg-purple-600/25 border-purple-400 text-white'
                        : 'bg-gray-800/60 hover:bg-gray-800 border-gray-700/80 text-gray-300'
                    }`}
                  >
                    <span className="text-base font-mono leading-none text-purple-300">{sh.icon}</span>
                    <span className="text-[11px] font-medium leading-tight mt-0.5">{sh.label}</span>
                    <span className="text-[10px] text-gray-500 leading-tight hidden sm:block">{sh.hint}</span>
                  </button>
                );
              })}
            </div>

            {/* Extra flutter density pill when Ripple shape is selected */}
            {activeBlock?.type === 'ripple' && (
              <div className="flex items-center justify-between gap-2 bg-gray-800/40 border border-gray-700/60 rounded-xl px-3 py-2">
                <span className="text-xs text-gray-300">Flutter ripples per block</span>
                <div className="flex gap-1">
                  {[2, 3, 4, 5, 6].map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => updateBlock(activeIdx, (b) => ({ ...b, cycles: n }))}
                      className={`px-2.5 py-1 rounded-md text-xs font-mono ${
                        (activeBlock.cycles || 3) === n
                          ? 'bg-purple-600 text-white'
                          : 'bg-gray-900 text-gray-400 hover:text-gray-200 border border-gray-700'
                      }`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Loop Speed & Valley Mode */}
          <div className="grid sm:grid-cols-2 gap-3 bg-gray-800/40 border border-gray-700/70 rounded-xl p-3">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-300 font-medium">Loop Length</span>
                <span className="text-purple-300 font-mono text-[11px]">
                  {describeLoopFeel(draft.loop_seconds)}
                </span>
              </div>
              <input
                type="range"
                min="1.5"
                max="15"
                step="0.5"
                value={draft.loop_seconds}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, loop_seconds: Number(e.target.value) }))
                }
                className="w-full accent-purple-500"
              />
              <div className="flex justify-between text-[10px] text-gray-500">
                <span>Fast (1.5s)</span>
                <span>Slow tide (15s)</span>
              </div>
            </div>

            <div className="space-y-1.5">
              <span className="text-xs text-gray-300 font-medium block">Valleys at High Intensity</span>
              <Segmented
                value={draft.lift_high_floor ? 'lift' : 'exact'}
                onChange={(v) => setDraft((prev) => ({ ...prev, lift_high_floor: v === 'lift' }))}
                options={[
                  { id: 'exact', label: 'Crisp 0% Valleys' },
                  { id: 'lift', label: 'Lift Floor Near Peak' },
                ]}
              />
              <p className="text-[10px] text-gray-500 leading-snug">
                Crisp keeps full 0→100% drops on sawtooths &amp; beats; Lift raises valleys above 70% intensity.
              </p>
            </div>
          </div>

          {/* Pattern Name, Short Description & Story Vibe Tags */}
          <div className="space-y-3 bg-gray-800/40 border border-gray-700/70 rounded-xl p-3">
            <div className="grid sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs text-gray-300 font-medium">Pattern Name</label>
                <TextInput
                  value={draft.label}
                  maxLength={36}
                  placeholder="e.g. Double Wave & Saw"
                  onChange={(e) => setDraft((prev) => ({ ...prev, label: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-gray-300 font-medium">Short Note (optional)</label>
                <TextInput
                  value={draft.tagline}
                  maxLength={140}
                  placeholder="How this pattern feels…"
                  onChange={(e) => setDraft((prev) => ({ ...prev, tagline: e.target.value }))}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <span className="text-xs text-gray-300 font-medium block">
                Story Vibes (helps JEV pick this pattern automatically)
              </span>
              <div className="flex flex-wrap gap-1.5">
                {VIBE_TAGS.map((tag) => {
                  const on = (draft.vibe_tags || []).includes(tag.id);
                  return (
                    <button
                      key={tag.id}
                      type="button"
                      onClick={() => toggleVibeTag(tag.id)}
                      className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                        on
                          ? 'bg-purple-600/30 border-purple-400 text-purple-100'
                          : 'bg-gray-900 border-gray-700 text-gray-400 hover:text-gray-200'
                      }`}
                    >
                      {on ? `✓ ${tag.label}` : tag.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {saveError && (
            <div className="rounded-lg border border-red-700/60 bg-red-950/40 px-3 py-2 text-xs text-red-200">
              {saveError}
            </div>
          )}
        </div>

        {/* Sticky action footer */}
        <div className="border-t border-gray-800 bg-gray-950/95 px-4 py-3 flex items-center justify-between gap-2 flex-wrap shrink-0">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={toggleFeelLive}
              className={`px-3.5 py-2 rounded-lg text-xs sm:text-sm font-medium border transition-colors ${
                feelLive
                  ? 'bg-pink-600 hover:bg-pink-500 text-white border-pink-400'
                  : 'bg-gray-800 hover:bg-gray-700 text-purple-200 border-gray-700'
              }`}
            >
              {feelLive ? '■ Stop Live Feel' : '▶ Feel Live'}
            </button>
            {feelLive && (
              <input
                type="range"
                min="20"
                max="100"
                step="5"
                value={feelStrength}
                onChange={(e) => setFeelStrength(Number(e.target.value))}
                title={`Preview strength: ${feelStrength}%`}
                className="w-20 accent-pink-500"
              />
            )}
          </div>

          <div className="flex items-center gap-2 ml-auto">
            {draft.is_builtin && draft.is_customized && (
              <Button
                kind="subtle"
                disabled={saving}
                onClick={handleResetBuiltin}
                title="Restore factory waveform for this built-in pattern"
              >
                ↺ Reset Default
              </Button>
            )}
            {isEditingExisting && (
              <Button
                kind="subtle"
                disabled={saving}
                onClick={() => submitSave(true)}
                title="Save as a brand-new custom pattern without overwriting"
              >
                Save as Copy
              </Button>
            )}
            <Button kind="primary" disabled={saving} onClick={() => submitSave(false)}>
              {saving ? 'Saving…' : isEditingExisting ? 'Save Changes' : 'Add Pattern'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
