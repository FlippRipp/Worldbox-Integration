import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactDOM from 'react-dom';
import { storage } from 'storage';

// Sidebar widget + floating story-view toggle.
// Note: Worldbox mounts sidebar slots twice on responsive layouts (desktop aside
// + mobile drawer), so polling, story-leave tracking, and the floating portal
// are coordinated at module scope.
const API = '/api/modules/wb_toy_link';
const POLL_MS = 1000;
const FLOAT_POS_KEY = 'wb_toy_link_float_pos';

const PATTERN_LIST = [
  { id: 'auto', label: 'Auto (JEV)' },
  { id: 'velvet', label: 'Velvet' },
  { id: 'swell', label: 'Ocean Swell' },
  { id: 'heartbeat', label: 'Heartbeat' },
  { id: 'tease', label: 'Tease & Edge' },
  { id: 'rhythm', label: 'Rhythm' },
  { id: 'crest', label: 'Crest Surge' },
  { id: 'afterglow', label: 'Afterglow' },
];

const store = { status: null, listeners: new Set(), timer: null };
let widgetMountCount = 0;
let leaveTimer = null;
let readerBridgeCleanup = null;

function currentHostReaderMode() {
  if (typeof window !== 'undefined' && window.__wbReaderMode) {
    return window.__wbReaderMode === 'stage' ? 'stage' : 'feed';
  }
  try {
    return storage.getItem('wb_display_mode') === 'stage' ? 'stage' : 'feed';
  } catch (e) {
    return 'feed';
  }
}

function attachReaderBridge() {
  if (readerBridgeCleanup || typeof window === 'undefined') return;
  const pushMode = (mode) => {
    fetch(`${API}/reader-sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phase: 'mode', mode }),
    }).then(() => poll()).catch(() => {});
  };

  pushMode(currentHostReaderMode());

  const onMode = (e) => {
    const mode = e?.detail?.mode === 'stage' ? 'stage' : 'feed';
    pushMode(mode);
  };

  const onSync = (e) => {
    const d = e?.detail || {};
    if (!d.phase) return;
    fetch(`${API}/reader-sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        phase: d.phase,
        turn_id: d.turnId,
        sentence_index: d.sentenceIndex ?? 0,
        char_start: d.charStart ?? 0,
        char_end: d.charEnd ?? 0,
        duration_ms: d.durationMs ?? 0,
        text: d.text || '',
        reason: d.reason || '',
      }),
    }).then(() => poll()).catch(() => {});
  };

  window.addEventListener('wb:reader-mode', onMode);
  window.addEventListener('wb:reader-sync', onSync);
  readerBridgeCleanup = () => {
    window.removeEventListener('wb:reader-mode', onMode);
    window.removeEventListener('wb:reader-sync', onSync);
    readerBridgeCleanup = null;
  };
}

function notify() {
  store.listeners.forEach((fn) => fn());
}

async function poll() {
  try {
    const res = await fetch(`${API}/status`);
    if (!res.ok) return;
    store.status = await res.json();
    notify();
  } catch (e) {
    /* next poll retries */
  }
}

function subscribe(fn) {
  store.listeners.add(fn);
  if (!store.timer) {
    store.timer = setInterval(poll, POLL_MS);
    poll();
  }
  return () => {
    store.listeners.delete(fn);
    if (store.listeners.size === 0 && store.timer) {
      clearInterval(store.timer);
      store.timer = null;
    }
  };
}

async function post(path, body) {
  try {
    await fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  } catch (e) {
    /* surfaced via status poll */
  }
  poll();
}

function useToyStatus() {
  const [, force] = useState(0);
  useEffect(() => subscribe(() => force((n) => n + 1)), []);
  return store.status;
}

// ---------------------------------------------------------------- floating orb

let nextFloatCandidateId = 1;
const floatCandidates = new Map();
let activeFloatOwnerId = null;

function registerFloatCandidate(onOwnerChange) {
  const id = nextFloatCandidateId;
  nextFloatCandidateId += 1;
  floatCandidates.set(id, onOwnerChange);
  if (activeFloatOwnerId === null) {
    activeFloatOwnerId = id;
    onOwnerChange(true);
  } else {
    onOwnerChange(false);
  }
  return () => {
    floatCandidates.delete(id);
    if (activeFloatOwnerId === id) {
      activeFloatOwnerId = null;
      const nextEntry = floatCandidates.entries().next().value;
      if (nextEntry) {
        activeFloatOwnerId = nextEntry[0];
        nextEntry[1](true);
      }
    }
  };
}

function clampPos(p) {
  const maxX = Math.max(8, (window.innerWidth || 400) - 58);
  const maxY = Math.max(8, (window.innerHeight || 600) - 58);
  return {
    x: Math.min(Math.max(Math.round(p.x), 6), maxX),
    y: Math.min(Math.max(Math.round(p.y), 6), maxY),
  };
}

function loadFloatPos() {
  try {
    const raw = storage.getItem(FLOAT_POS_KEY);
    if (raw) return clampPos(JSON.parse(raw));
  } catch (e) {
    /* fall through */
  }
  return clampPos({ x: 16, y: 0.62 * (window.innerHeight || 600) });
}

function FloatingToggle({ status }) {
  const [owner, setOwner] = useState(false);
  useEffect(() => registerFloatCandidate(setOwner), []);

  const posRef = useRef(null);
  if (posRef.current === null && typeof window !== 'undefined') {
    posRef.current = loadFloatPos();
  }
  const [, bump] = useState(0);
  const btnRef = useRef(null);
  const dragRef = useRef(null);

  const applyTransform = useCallback((p) => {
    if (btnRef.current) {
      btnRef.current.style.transform = `translate3d(${p.x}px, ${p.y}px, 0)`;
    }
  }, []);

  useEffect(() => {
    if (!owner) return undefined;
    const onResize = () => {
      if (!posRef.current) return;
      posRef.current = clampPos(posRef.current);
      applyTransform(posRef.current);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [owner, applyTransform]);

  const onPointerDown = (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const cur = posRef.current || { x: 16, y: 300 };
    dragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      origX: cur.x,
      origY: cur.y,
      moved: false,
    };
  };

  const onPointerMove = (e) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < 5) return;
    d.moved = true;
    // Direct GPU-composited transform write: zero React re-renders during drag,
    // no CSS transition on transform, so 0ms lag while held & no snap on release.
    const next = clampPos({ x: d.origX + dx, y: d.origY + dy });
    posRef.current = next;
    applyTransform(next);
  };

  const finishPointer = (e, cancelled = false) => {
    const d = dragRef.current;
    if (!d || (e && d.pointerId !== undefined && e.pointerId !== d.pointerId)) return;
    dragRef.current = null;
    if (d.moved) {
      try {
        storage.setItem(FLOAT_POS_KEY, JSON.stringify(posRef.current));
      } catch (err) {
        /* ignore quota errors */
      }
      bump((n) => n + 1);
    } else if (!cancelled) {
      post('/toggle');
    }
  };

  if (!owner || !status) return null;
  const on = status.vibe_on;
  const level = Math.round((status.level || 0) * 100);
  const connected = !!status.lovense?.connected;
  const p = posRef.current || { x: 16, y: 300 };

  return ReactDOM.createPortal(
    <button
      ref={btnRef}
      type="button"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={(e) => finishPointer(e, false)}
      onPointerCancel={(e) => finishPointer(e, true)}
      title={
        on
          ? `Toy Link (${status.vibe?.pattern_label || 'Velvet'} · ${level}%) — tap to mute · drag to move`
          : 'Toy Link muted — tap to resume · drag to move'
      }
      style={{
        position: 'fixed',
        left: 0,
        top: 0,
        transform: `translate3d(${p.x}px, ${p.y}px, 0)`,
        willChange: 'transform',
        zIndex: 9999,
        width: 52,
        height: 52,
        touchAction: 'none',
      }}
      className={`rounded-full shadow-lg border flex flex-col items-center justify-center
        select-none cursor-grab active:cursor-grabbing transition-colors transition-shadow duration-150 ${
          on && level > 0
            ? 'bg-gradient-to-br from-purple-600 to-pink-600 border-pink-400/70 text-white shadow-pink-500/25'
            : on
              ? 'bg-gray-900/95 border-purple-500/60 text-purple-200 hover:border-purple-400'
              : 'bg-gray-900/90 border-gray-700 text-gray-500 hover:text-gray-300'
        }`}
    >
      <span className="text-base leading-none pointer-events-none">{on ? (level > 0 ? '✦' : '▶') : '⏸'}</span>
      <span className="text-[9px] font-mono leading-tight pointer-events-none">
        {connected ? `${level}%` : 'off'}
      </span>
    </button>,
    document.body
  );
}

// ----------------------------------------------------------------- sidebar

function Dot({ ok, warn }) {
  const color = ok ? 'bg-emerald-400' : warn ? 'bg-amber-400' : 'bg-gray-600';
  return <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${color}`} />;
}

export default function ToyLinkWidget({ state, config, slotName }) {
  const status = useToyStatus();
  const [busyConnect, setBusyConnect] = useState(false);

  const storyEnabled = config?.enabled !== false;
  const storyCap = config?.master_cap ?? 70;
  const saveId = state?.active_save_id || null;

  // Keep backend immediately synced with per-story settings and save switches
  useEffect(() => {
    post('/story-sync', { enabled: storyEnabled, master_cap: storyCap, save_id: saveId });
  }, [storyEnabled, storyCap, saveId]);

  // Stop toys cleanly when the last story slot unmounts (leaving story to Main Menu)
  useEffect(() => {
    widgetMountCount += 1;
    attachReaderBridge();
    if (leaveTimer) {
      clearTimeout(leaveTimer);
      leaveTimer = null;
    }
    return () => {
      widgetMountCount = Math.max(0, widgetMountCount - 1);
      if (widgetMountCount === 0) {
        if (readerBridgeCleanup) readerBridgeCleanup();
        leaveTimer = setTimeout(() => {
          if (widgetMountCount === 0) {
            fetch(`${API}/story-leave`, { method: 'POST' }).catch(() => {});
          }
        }, 200);
      }
    };
  }, []);

  // In slot_header, act purely as an always-mounted session & portal anchor so
  // the floating orb and Stage sync stay active even when the sidebar is closed.
  if (slotName === 'slot_header') {
    return <FloatingToggle status={status} />;
  }

  if (!status) {
    return (
      <div className="p-3 bg-gray-900/70 rounded-md border border-gray-700 text-xs text-gray-500">
        🔗 Toy Link — connecting…
      </div>
    );
  }

  const level = Math.round((status.level || 0) * 100);
  const lov = status.lovense || {};
  const jev = status.jev || {};
  const toys = lov.devices || [];
  const patternOverride = jev.pattern_override || 'auto';
  const patternOptions = Array.isArray(status.available_patterns) && status.available_patterns.length > 0
    ? [{ id: 'auto', label: 'Auto (JEV)' }, ...status.available_patterns]
    : PATTERN_LIST;
  const stageActive = !!jev.reader_mode_active;
  const stageSynced = !!jev.reader_synced;

  const toggleHostStage = () => {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(
      new CustomEvent('wb:set-display-mode', {
        detail: { mode: stageActive ? 'feed' : 'stage' },
      })
    );
  };

  const reconnectLovense = async () => {
    setBusyConnect(true);
    try {
      await fetch(`${API}/lovense/connect`, { method: 'POST' });
    } catch (e) {
      /* surfaced on status */
    }
    setBusyConnect(false);
    poll();
  };

  return (
    <div className="p-3 bg-gray-900/70 rounded-lg border border-gray-700/80 text-xs space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-gray-200 flex items-center gap-1.5">
          <span>🔗 Toy Link</span>
        </span>
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
            !storyEnabled
              ? 'bg-gray-800 text-gray-500'
              : status.vibe_on
                ? level > 0
                  ? 'bg-pink-500/20 text-pink-300 border border-pink-500/30'
                  : 'bg-purple-500/15 text-purple-300'
                : 'bg-gray-800 text-gray-400'
          }`}
        >
          {!storyEnabled ? 'off' : status.vibe_on ? (level > 0 ? 'playing' : 'ready') : 'muted'}
        </span>
      </div>

      {!status.hooks_available && (
        <p className="text-amber-400/90 leading-snug">
          Stream hooks missing on this WorldboxAI build.
        </p>
      )}

      {/* Lovense + JEV compact status */}
      <div className="space-y-1.5 text-[11px]">
        <div className="flex items-center justify-between gap-2 text-gray-300">
          <div className="flex items-center gap-1.5 min-w-0">
            <Dot ok={lov.connected && toys.length > 0} warn={lov.connected && toys.length === 0} />
            <span className="truncate">
              {lov.connected
                ? toys.length > 0
                  ? toys
                      .map((t) => `${t.display_name || t.name || 'Toy'}${t.battery != null ? ` ${t.battery}%` : ''}`)
                      .join(', ')
                  : 'Lovense open (no toy)'
                : lov.configured_host
                  ? 'Lovense disconnected'
                  : 'No Lovense IP set'}
            </span>
          </div>
          {!lov.connected && lov.configured_host && (
            <button
              type="button"
              disabled={busyConnect}
              onClick={reconnectLovense}
              className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-purple-300 border border-gray-700 shrink-0"
            >
              {busyConnect ? '…' : 'Connect'}
            </button>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 text-gray-400">
          <div className="flex items-center gap-1.5 min-w-0">
            <Dot ok={jev.ready} warn={jev.available && !jev.ready} />
            <span className="truncate">
              {jev.ready
                ? 'JEV steering active'
                : !jev.enabled
                  ? 'JEV disabled'
                  : 'JEV needs API key'}
            </span>
          </div>
          {jev.available && !jev.enabled && (
            <button
              type="button"
              onClick={() => post('/jev/enable')}
              className="px-2 py-0.5 rounded bg-purple-600/30 hover:bg-purple-600/50 text-purple-200 border border-purple-500/40 shrink-0"
            >
              Enable
            </button>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 text-gray-400">
          <div className="flex items-center gap-1.5 min-w-0">
            <Dot ok={stageSynced} warn={stageActive && !stageSynced} />
            <span className="truncate">
              {stageSynced
                ? 'Synced Stage active'
                : stageActive
                  ? 'Stage on (sync off)'
                  : 'Classic log view'}
            </span>
          </div>
          <button
            type="button"
            onClick={toggleHostStage}
            className={`px-2 py-0.5 rounded border shrink-0 transition-colors ${
              stageActive
                ? 'bg-purple-600/25 hover:bg-purple-600/40 text-purple-200 border-purple-500/40'
                : 'bg-gray-800 hover:bg-gray-700 text-gray-300 border-gray-700'
            }`}
            title="Switch between classic Story Log and Paced Stage synced reader"
          >
            {stageActive ? 'Use Log' : 'Stage Mode'}
          </button>
        </div>
      </div>

      {/* Live level bar & active pattern */}
      <div className="space-y-1">
        <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-purple-500 via-fuchsia-500 to-pink-500 transition-[width] duration-150"
            style={{ width: `${level}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-[11px] text-gray-400">
          <span className="font-mono text-gray-300">{level}%</span>
          <span className="truncate text-purple-300/90">
            {status.preview
              ? `Preview: ${status.preview.pattern}`
              : level > 0 || status.vibe?.strength > 0
                ? status.vibe?.pattern_label || 'Velvet'
                : 'idle'}
          </span>
        </div>
      </div>

      {/* Quick pattern selector */}
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] text-gray-500 shrink-0">Pattern</span>
        <select
          value={patternOverride}
          onChange={(e) => post('/pattern-override', { pattern: e.target.value })}
          className="flex-1 min-w-0 px-2 py-1 rounded bg-gray-800 border border-gray-700 text-[11px] text-gray-200 focus:border-purple-500 focus:outline-none"
        >
          {patternOptions.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      {/* Primary controls */}
      <div className="flex gap-1.5 pt-0.5">
        <button
          type="button"
          onClick={() => post('/stop')}
          className="flex-1 py-1.5 rounded bg-red-600/90 hover:bg-red-500 text-white font-semibold tracking-wide transition-colors"
        >
          ■ Stop
        </button>
        <button
          type="button"
          onClick={() => post('/toggle')}
          className={`flex-1 py-1.5 rounded font-medium transition-colors ${
            status.vibe_on
              ? 'bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700'
              : 'bg-purple-600 hover:bg-purple-500 text-white'
          }`}
        >
          {status.vibe_on ? '⏸ Mute' : '▶ Resume'}
        </button>
        <button
          type="button"
          onClick={() => post('/test', { strength: 55 })}
          className="px-2.5 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 transition-colors"
          title="2s test surge"
        >
          ⚡
        </button>
      </div>

      <FloatingToggle status={status} />
    </div>
  );
}
