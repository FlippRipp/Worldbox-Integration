import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactDOM from 'react-dom';
import { storage } from 'storage';

// Sidebar widget + the floating vibe toggle. The sidebar content mounts twice
// on mobile (hidden desktop aside + drawer), so everything shared lives at
// module scope: one status poller, and a singleton claim for the floating
// button so it never renders twice.
const API = '/api/modules/wb_toy_link';
const POLL_MS = 1500;
const FLOAT_POS_KEY = 'wb_toy_link_float_pos';

const store = { status: null, listeners: new Set(), timer: null };

function notify() { store.listeners.forEach((fn) => fn()); }

async function poll() {
  try {
    const res = await fetch(`${API}/status`);
    if (!res.ok) return;
    store.status = await res.json();
    notify();
  } catch (e) { /* next poll retries */ }
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
  } catch (e) { /* surfaced via the next status poll */ }
  poll();
}

function useToyStatus() {
  const [, force] = useState(0);
  useEffect(() => subscribe(() => force((n) => n + 1)), []);
  return store.status;
}

// ---------------------------------------------------------------- floating

let floatClaimed = false;

function loadFloatPos() {
  try {
    const raw = storage.getItem(FLOAT_POS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* fall through */ }
  return { x: 16, y: 0.6 * (window.innerHeight || 600) };
}

// Manual drive: hold the button still ~400 ms, then drag — strength follows
// hand speed. Full speed ≈ DRIVE_VMAX px/s; stop moving and it decays to 0.
const DRIVE_HOLD_MS = 400;
const DRIVE_VMAX = 1200;        // px/s that maps to 100%
const DRIVE_TICK_MS = 120;
const DRIVE_EMA = 0.5;

function FloatingToggle({ status }) {
  // Singleton across the double-mounted sidebar: first instance wins.
  const [owner] = useState(() => {
    if (floatClaimed) return false;
    floatClaimed = true;
    return true;
  });
  useEffect(() => () => { if (owner) floatClaimed = false; }, [owner]);

  const [pos, setPos] = useState(loadFloatPos);
  const [driving, setDriving] = useState(false);
  const [driveStrength, setDriveStrength] = useState(0);
  const dragRef = useRef(null);
  const driveRef = useRef(null);

  const clamp = useCallback((p) => ({
    x: Math.min(Math.max(p.x, 4), (window.innerWidth || 400) - 60),
    y: Math.min(Math.max(p.y, 4), (window.innerHeight || 600) - 60),
  }), []);

  const sendManual = (strength) => {
    fetch(`${API}/manual`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strength }),
    }).catch(() => {});
  };

  const startDrive = () => {
    setDriving(true);
    const d = { acc: 0, ema: 0, lastSent: -1, lastT: performance.now(),
                prevX: null, prevY: null };
    d.timer = setInterval(() => {
      const now = performance.now();
      const dt = Math.max((now - d.lastT) / 1000, 0.05);
      d.lastT = now;
      const v = Math.min(d.acc / dt, DRIVE_VMAX);
      d.acc = 0;
      d.ema = d.ema * (1 - DRIVE_EMA) + v * DRIVE_EMA;
      const strength = Math.round((d.ema / DRIVE_VMAX) * 100);
      setDriveStrength(strength);
      if (Math.abs(strength - d.lastSent) >= 3
          || (strength === 0 && d.lastSent > 0)) {
        d.lastSent = strength;
        sendManual(strength);
      }
    }, DRIVE_TICK_MS);
    driveRef.current = d;
  };

  const stopDrive = () => {
    const d = driveRef.current;
    driveRef.current = null;
    if (d) clearInterval(d.timer);
    setDriving(false);
    setDriveStrength(0);
  };
  useEffect(() => stopDrive, []);   // never leak the interval on unmount

  const onPointerDown = (e) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { startX: e.clientX, startY: e.clientY,
                        origX: pos.x, origY: pos.y, moved: false,
                        holdTimer: setTimeout(startDrive, DRIVE_HOLD_MS) };
  };
  const onPointerMove = (e) => {
    const d = dragRef.current;
    if (!d) return;
    const drive = driveRef.current;
    if (drive) {
      // Drive mode: accumulate travelled distance, don't move the button.
      if (drive.prevX !== null) {
        drive.acc += Math.hypot(e.clientX - drive.prevX, e.clientY - drive.prevY);
      }
      drive.prevX = e.clientX;
      drive.prevY = e.clientY;
      return;
    }
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < 8) return;   // forgiving tap threshold
    if (!d.moved) clearTimeout(d.holdTimer);          // it's a reposition drag
    d.moved = true;
    setPos(clamp({ x: d.origX + dx, y: d.origY + dy }));
  };
  const onPointerUp = () => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d) return;
    clearTimeout(d.holdTimer);
    if (driveRef.current) {
      // Release keeps the latch where it landed (a still hand has already
      // decayed to 0; a fling-and-release holds the flung level).
      stopDrive();
      return;
    }
    if (d.moved) {
      setPos((p) => {
        storage.setItem(FLOAT_POS_KEY, JSON.stringify(p));
        return p;
      });
    } else {
      post('/toggle');
    }
  };

  if (!owner || !status) return null;
  const on = status.vibe_on;
  const level = driving ? driveStrength : Math.round((status.level || 0) * 100);
  const anyConnected = (status.backends || []).some((b) => b.connected);

  return ReactDOM.createPortal(
    <button
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      title={driving ? 'Manual drive — strength follows your hand speed'
        : on ? 'Vibration on — tap to mute · drag to move · hold, then drag to drive'
             : 'Vibration muted — tap to resume · drag to move · hold, then drag to drive'}
      style={{ position: 'fixed', left: pos.x, top: pos.y, zIndex: 9999,
               width: 52, height: 52, touchAction: 'none' }}
      className={`rounded-full shadow-lg border flex flex-col items-center justify-center
        select-none transition-all duration-150 ${driving
          ? 'bg-gradient-to-br from-pink-500 to-rose-500 border-pink-300 text-white scale-110 ring-2 ring-pink-400/60'
          : on
            ? 'bg-gradient-to-br from-purple-600 to-pink-600 border-purple-400/60 text-white'
            : 'bg-gray-800 border-gray-600 text-gray-400'}`}
    >
      <span className="text-lg leading-none">{driving ? '✦' : on ? '▶' : '⏸'}</span>
      <span className="text-[9px] font-mono leading-tight">
        {driving ? `${level}%` : anyConnected ? `${level}%` : '·'}
      </span>
    </button>,
    document.body
  );
}

// ----------------------------------------------------------------- sidebar

function Dot({ ok, warn }) {
  const color = ok ? 'bg-green-500' : warn ? 'bg-yellow-500' : 'bg-gray-600';
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}

export default function ToyLinkWidget({ state, config }) {
  const status = useToyStatus();

  if (!status) {
    return (
      <div className="p-3 bg-gray-900/70 rounded-md border border-gray-700 text-xs text-gray-500">
        🔗 Toy Link — connecting…
      </div>
    );
  }

  const level = Math.round((status.level || 0) * 100);
  const enabled = config?.enabled !== false;

  return (
    <div className="p-3 bg-gray-900/70 rounded-md border border-gray-700 text-xs space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-gray-200">🔗 Toy Link</span>
        <span className="text-gray-500">
          {status.hooks_available ? (status.vibe_on ? 'on' : 'muted') : 'inactive'}
        </span>
      </div>

      {!status.hooks_available && (
        <p className="text-yellow-500/90 leading-snug">
          Requires an updated WorldboxAI (stream hooks missing) — triggers are
          disabled.
        </p>
      )}
      {!enabled && (
        <p className="text-gray-500">Disabled for this story (see settings).</p>
      )}

      <div className="space-y-1">
        {(status.backends || []).map((b) => (
          <div key={b.name} className="flex items-center gap-2 text-gray-400">
            <Dot ok={b.connected} warn={b.enabled && !b.connected} />
            <span className="capitalize">{b.name}</span>
            <span className="text-gray-600 truncate">
              {b.connected
                ? `${(b.devices || []).length || 'no'} device${(b.devices || []).length === 1 ? '' : 's'}`
                : b.enabled ? (b.last_error ? 'error' : 'not connected') : 'off'}
            </span>
          </div>
        ))}
      </div>

      <div className="h-2 bg-gray-800 rounded overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-purple-500 to-pink-500 transition-all duration-300"
          style={{ width: `${level}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-gray-500">
        <span>{level}%</span>
        <span className="truncate max-w-[9rem]" title={status.vibe?.source}>
          {status.vibe?.source || 'idle'}
        </span>
      </div>

      <div className="flex gap-2 pt-1">
        <button
          onClick={() => post('/stop')}
          className="flex-1 py-2 rounded bg-red-600/90 hover:bg-red-500 text-white font-bold tracking-wide"
        >
          ■ STOP
        </button>
        <button
          onClick={() => post('/toggle')}
          className={`flex-1 py-2 rounded font-semibold ${status.vibe_on
            ? 'bg-gray-700 hover:bg-gray-600 text-gray-200'
            : 'bg-purple-600 hover:bg-purple-500 text-white'}`}
        >
          {status.vibe_on ? '⏸ Mute' : '▶ Resume'}
        </button>
        <button
          onClick={() => post('/test', { strength: 40 })}
          className="px-3 py-2 rounded bg-gray-700 hover:bg-gray-600 text-gray-300"
          title="Short test buzz"
        >
          ⚡
        </button>
      </div>

      <FloatingToggle status={status} />
    </div>
  );
}
