import React, { useState, useEffect } from 'react';

// In-story quick settings modal (loaded by ModuleSettingsModal, which only
// exposes `react` in `require` — keep self-contained with no relative imports).
const API = '/api/modules/wb_toy_link';

export default function ToyLinkSettings({ config, onSaveConfig }) {
  const enabled = config?.enabled !== false;
  const cap = config?.master_cap ?? 70;

  const [globalCfg, setGlobalCfg] = useState(null);
  const [status, setStatus] = useState(null);
  const [hostDraft, setHostDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const [sRes, cRes] = await Promise.all([fetch(`${API}/status`), fetch(`${API}/config`)]);
      if (sRes.ok) setStatus(await sRes.json());
      if (cRes.ok) {
        const c = await cRes.json();
        setGlobalCfg(c);
        setHostDraft((prev) => prev || c?.lovense?.host || '');
      }
    } catch (e) {
      /* ignore */
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const updateStory = (patch) => {
    const next = { ...config, ...patch };
    onSaveConfig(next);
    fetch(`${API}/story-sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: next.enabled !== false,
        master_cap: next.master_cap ?? 70,
      }),
    }).catch(() => {});
  };

  const updateGlobal = async (patch) => {
    try {
      const res = await fetch(`${API}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (res.ok) setGlobalCfg(await res.json());
    } catch (e) {
      /* ignore */
    }
  };

  const connectLovense = async () => {
    setBusy(true);
    try {
      await fetch(`${API}/lovense/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: hostDraft.trim() }),
      });
    } catch (e) {
      /* ignore */
    }
    setBusy(false);
    refresh();
  };

  const lov = status?.lovense || {};
  const toys = lov.devices || [];

  return (
    <div className="p-5 space-y-5 text-sm text-gray-200">
      {/* Story controls */}
      <div className="space-y-3">
        <label className="flex items-center justify-between gap-3 cursor-pointer">
          <div>
            <div className="font-medium text-gray-100">Enabled in this story</div>
            <div className="text-xs text-gray-400">Disabling stops vibration immediately.</div>
          </div>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => updateStory({ enabled: e.target.checked })}
            className="w-5 h-5 accent-purple-500"
          />
        </label>

        <div className="pt-1">
          <div className="flex justify-between text-gray-300 mb-1">
            <span>Story intensity ceiling</span>
            <span className="font-mono text-purple-300">{cap}%</span>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={cap}
            onChange={(e) => updateStory({ master_cap: Number(e.target.value) })}
            className="w-full accent-purple-500"
          />
        </div>
      </div>

      {/* Quick Lovense reconnect without leaving the story */}
      <div className="rounded-lg border border-gray-700 bg-gray-900/60 p-3.5 space-y-2.5">
        <div className="flex items-center justify-between">
          <span className="font-medium text-gray-200">Lovense Connection</span>
          <span className={`text-xs ${lov.connected ? 'text-emerald-400' : 'text-gray-400'}`}>
            {lov.connected
              ? toys.length
                ? toys.map((t) => `${t.display_name || t.name} (${t.battery ?? '?'}%)`).join(', ')
                : 'Connected · waiting for toy'
              : 'Not connected'}
          </span>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={hostDraft}
            onChange={(e) => setHostDraft(e.target.value)}
            placeholder="Phone IP (e.g. 192.168.1.42) or 127.0.0.1"
            className="flex-1 px-3 py-1.5 rounded bg-gray-800 border border-gray-700 text-sm text-gray-200 focus:border-purple-500 focus:outline-none"
          />
          <button
            type="button"
            disabled={busy}
            onClick={connectLovense}
            className="px-3.5 py-1.5 rounded bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white font-medium"
          >
            {busy ? 'Connecting…' : 'Connect'}
          </button>
        </div>
        {lov.last_error && !lov.connected && (
          <p className="text-xs text-amber-400/90">{lov.last_error}</p>
        )}
      </div>

      {/* Quick feel presets */}
      {globalCfg && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <span className="block text-xs text-gray-400 mb-1">High-intensity contrast</span>
            <div className="flex rounded-md overflow-hidden border border-gray-700">
              {[
                ['subtle', 'Subtle'],
                ['balanced', 'Balanced'],
                ['high', 'High'],
              ].map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => updateGlobal({ contrast_mode: id })}
                  className={`flex-1 py-1.5 text-xs font-medium ${
                    globalCfg.contrast_mode === id
                      ? 'bg-purple-600 text-white'
                      : 'bg-gray-900 text-gray-400 hover:text-gray-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <span className="block text-xs text-gray-400 mb-1">Transition feel</span>
            <div className="flex rounded-md overflow-hidden border border-gray-700">
              {[
                ['silky', 'Silky'],
                ['natural', 'Natural'],
                ['snappy', 'Snappy'],
              ].map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => updateGlobal({ response_feel: id })}
                  className={`flex-1 py-1.5 text-xs font-medium ${
                    globalCfg.response_feel === id
                      ? 'bg-purple-600 text-white'
                      : 'bg-gray-900 text-gray-400 hover:text-gray-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {globalCfg && (
        <div className="space-y-3 pt-1">
          <div>
            <span className="block text-xs text-gray-400 mb-1">Between storyteller blocks</span>
            <div className="flex rounded-md overflow-hidden border border-gray-700">
              {[
                ['continue', 'Continue Last'],
                ['low', 'Low (~20%)'],
                ['stop', 'Stop'],
              ].map(([id, label]) => {
                const cur = globalCfg.between_blocks || (globalCfg.stop_on_turn_end ? 'stop' : 'continue');
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => updateGlobal({ between_blocks: id, stop_on_turn_end: id === 'stop' })}
                    className={`flex-1 py-1.5 text-xs font-medium ${
                      cur === id
                        ? 'bg-purple-600 text-white'
                        : 'bg-gray-900 text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          <label className="flex items-center justify-between gap-3 cursor-pointer pt-1">
            <div>
              <div className="text-xs font-medium text-gray-200">Sentence sync in Paced Stage mode</div>
              <div className="text-[11px] text-gray-500">Pre-score sentences ahead and sync haptics to reading speed.</div>
            </div>
            <input
              type="checkbox"
              checked={globalCfg.sync_stage_reader !== false}
              onChange={(e) => updateGlobal({ sync_stage_reader: e.target.checked })}
              className="w-4 h-4 accent-purple-500"
            />
          </label>
        </div>
      )}

      <p className="text-xs text-gray-500">
        Open <span className="text-gray-300">Toy Studio</span> from the main menu to preview and customize the pattern gallery.
      </p>
    </div>
  );
}
