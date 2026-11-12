import React, { useState, useEffect, useCallback, useRef } from 'react';
import LovenseCard from './toystudio/LovenseCard';
import SteeringCard from './toystudio/SteeringCard';
import PatternGalleryCard from './toystudio/PatternGalleryCard';
import { Button } from './toystudio/ui';

const API = '/api/modules/wb_toy_link';
const STATUS_POLL_MS = 1200;

export default function ToyStudio({ onBack }) {
  const [config, setConfig] = useState(null);
  const [patterns, setPatterns] = useState([]);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const previewActiveRef = useRef(false);
  previewActiveRef.current = Boolean(status?.preview);

  useEffect(() => {
    return () => {
      if (previewActiveRef.current) {
        fetch(`${API}/stop`, { method: 'POST' }).catch(() => {});
      }
    };
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API}/status`);
      if (res.ok) setStatus(await res.json());
    } catch (e) {
      /* next poll retries */
    }
  }, []);

  const refreshPatterns = useCallback(async () => {
    try {
      const res = await fetch(`${API}/patterns`);
      if (res.ok) {
        const data = await res.json();
        setPatterns(data.patterns || []);
      }
    } catch (e) {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const cfgRes = await fetch(`${API}/config`);
        if (!cfgRes.ok) throw new Error('HTTP ' + cfgRes.status);
        setConfig(await cfgRes.json());
        await refreshPatterns();
      } catch (e) {
        setError('Could not reach Toy Link backend. Make sure the module is enabled.');
      }
    })();
    refreshStatus();
    const timer = setInterval(refreshStatus, STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshStatus, refreshPatterns]);

  const saveConfig = useCallback(
    async (patch) => {
      try {
        const res = await fetch(`${API}/config`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        });
        if (res.ok) {
          const updated = await res.json();
          setConfig(updated);
          if ('contrast_mode' in patch || 'enabled_patterns' in patch) {
            refreshPatterns();
          }
        }
      } catch (e) {
        setError('Saving settings failed — backend unreachable.');
      }
    },
    [refreshPatterns]
  );

  if (!config) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-950 via-gray-900 to-gray-950 flex items-center justify-center text-gray-400">
        {error || 'Loading Toy Studio…'}
      </div>
    );
  }

  const level = Math.round((status?.level || 0) * 100);
  const activePatternLabel = status?.preview
    ? `Preview: ${status.preview.pattern}`
    : status?.vibe?.strength > 0
      ? status?.vibe?.pattern_label
      : 'Idle';

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-950 via-gray-900 to-gray-950 text-gray-100">
      <div className="max-w-5xl mx-auto p-4 md:p-6 space-y-5">
        <header className="flex items-center gap-4 flex-wrap pb-1">
          <Button kind="subtle" onClick={onBack}>
            ← Back
          </Button>
          <div className="flex-1 min-w-[12rem]">
            <h1 className="text-2xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
              🎛️ Toy Studio
            </h1>
            <p className="text-xs text-gray-400">
              Lovense setup, JEV steering, and anti-numbing dynamic patterns.
            </p>
          </div>
          <div className="flex items-center gap-3 bg-gray-900/80 border border-gray-800 rounded-xl px-3.5 py-2">
            <div className="text-right">
              <div className="text-[11px] text-purple-300 font-medium">{activePatternLabel}</div>
              <div className="w-24 h-1.5 bg-gray-800 rounded-full overflow-hidden mt-1">
                <div
                  className="h-full bg-gradient-to-r from-purple-500 to-pink-500 transition-[width] duration-150"
                  style={{ width: `${level}%` }}
                />
              </div>
            </div>
            <span className="text-sm font-mono text-gray-200 w-10 text-right">{level}%</span>
            <Button
              kind="danger"
              onClick={() => fetch(`${API}/stop`, { method: 'POST' }).then(refreshStatus)}
            >
              ■ Stop
            </Button>
          </div>
        </header>

        {status && !status.hooks_available && (
          <div className="rounded-xl border border-amber-700/50 bg-amber-950/30 p-4 text-sm text-amber-200">
            ⚠ This WorldboxAI build does not expose streaming hooks — update WorldboxAI so story turns can steer haptics live.
          </div>
        )}
        {error && (
          <div className="rounded-xl border border-red-700/50 bg-red-950/30 p-4 text-sm text-red-200">
            {error}
          </div>
        )}

        <LovenseCard
          config={config}
          saveConfig={saveConfig}
          status={status}
          api={API}
          refresh={refreshStatus}
        />

        <SteeringCard
          config={config}
          saveConfig={saveConfig}
          status={status}
          api={API}
          refresh={refreshStatus}
        />

        <PatternGalleryCard
          patterns={patterns}
          config={config}
          saveConfig={saveConfig}
          status={status}
          api={API}
          refresh={refreshStatus}
        />
      </div>
    </div>
  );
}
