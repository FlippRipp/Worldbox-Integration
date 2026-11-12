import React, { useState, useEffect } from 'react';
import { Card, Field, TextInput, Toggle, Segmented, Button, Dot } from './ui';

export default function LovenseCard({ config, saveConfig, status, api, refresh }) {
  const lovCfg = config.lovense || {};
  const lovStatus = status?.lovense || {};
  const toys = lovStatus.devices || [];

  const [hostDraft, setHostDraft] = useState(lovCfg.host || '');
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [testStrength, setTestStrength] = useState(55);

  useEffect(() => {
    setHostDraft(lovCfg.host || '');
  }, [lovCfg.host]);

  const connect = async (overrideHost) => {
    const targetHost = overrideHost !== undefined ? overrideHost : hostDraft.trim();
    setBusy(true);
    try {
      await fetch(`${api}/lovense/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: targetHost, enabled: true }),
      });
    } catch (e) {
      /* status poll shows error */
    }
    setBusy(false);
    refresh();
  };

  const badge = (
    <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-gray-900 border border-gray-700 text-xs">
      <Dot ok={lovStatus.connected && toys.length > 0} warn={lovStatus.connected && toys.length === 0} />
      <span className={lovStatus.connected ? 'text-emerald-300' : 'text-gray-400'}>
        {lovStatus.connected
          ? toys.length > 0
            ? `${toys.length} toy${toys.length === 1 ? '' : 's'} ready`
            : 'App connected · pair a toy'
          : 'Not connected'}
      </span>
    </div>
  );

  return (
    <Card
      title="Lovense Connection"
      subtitle="Open Lovense Remote on your phone (Discover → Game Mode) and enter the Local IP, or use 127.0.0.1 for Lovense Connect on this PC. Ports and HTTP/HTTPS are detected automatically."
      badge={badge}
    >
      <div className="flex flex-col sm:flex-row gap-2.5">
        <div className="flex-1 flex gap-2">
          <TextInput
            value={hostDraft}
            onChange={(e) => setHostDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') connect();
            }}
            placeholder="e.g. 192.168.1.42  (or leave blank to check this PC)"
          />
          <Button
            kind="subtle"
            title="Use Lovense Connect running on this PC"
            onClick={() => {
              setHostDraft('127.0.0.1');
              connect('127.0.0.1');
            }}
          >
            This PC
          </Button>
        </div>
        <Button kind="primary" disabled={busy} onClick={() => connect()}>
          {busy ? 'Searching…' : lovStatus.connected ? 'Refresh Toys' : 'Connect'}
        </Button>
      </div>

      {/* Connected toy pills or clear error */}
      {lovStatus.connected && toys.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {toys.map((t, idx) => (
            <div
              key={t.id || idx}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-700/40 text-xs text-emerald-200"
            >
              <span className="font-medium">{t.display_name || t.name || 'Lovense Toy'}</span>
              {t.battery != null && (
                <span className="font-mono text-emerald-300/80">🔋 {t.battery}%</span>
              )}
            </div>
          ))}
        </div>
      )}

      {lovStatus.last_error && !lovStatus.connected && (
        <p className="text-xs text-amber-400/90 bg-amber-950/30 border border-amber-800/40 rounded-lg px-3 py-2">
          Could not reach Lovense ({lovStatus.last_error}). Make sure Game Mode is enabled and your phone is on the same Wi-Fi.
        </p>
      )}

      {/* Quick Test Bar */}
      <div className="flex items-center gap-3 pt-1 border-t border-gray-700/60">
        <Button
          kind="subtle"
          onClick={() =>
            fetch(`${api}/test`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ strength: testStrength, pattern: 'crest' }),
            }).then(refresh)
          }
        >
          ⚡ Quick Test
        </Button>
        <input
          type="range"
          min="10"
          max="100"
          value={testStrength}
          onChange={(e) => setTestStrength(Number(e.target.value))}
          className="flex-1 accent-purple-500"
        />
        <span className="text-xs font-mono text-gray-300 w-10 text-right">{testStrength}%</span>
        <Button
          kind="danger"
          onClick={() => fetch(`${api}/stop`, { method: 'POST' }).then(refresh)}
        >
          ■ Stop
        </Button>
      </div>

      {/* Progressive disclosure for rare port/motor overrides */}
      <div>
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          {showAdvanced ? '▾ Hide advanced connection options' : '▸ Advanced connection options'}
        </button>

        {showAdvanced && (
          <div className="mt-3 pt-3 border-t border-gray-700/50 grid sm:grid-cols-3 gap-4 text-xs">
            <Field label="Port" hint="Default 20010 (HTTP) or 30010 (HTTPS)">
              <TextInput
                type="number"
                value={lovCfg.port ?? 20010}
                onChange={(e) => saveConfig({ lovense: { port: Number(e.target.value) } })}
              />
            </Field>
            <Field label="Motor mode" hint="Drive all motors (Rotate/Thrust/Vibrate) or Vibrate only">
              <Segmented
                value={lovCfg.action_mode || 'All'}
                onChange={(v) => saveConfig({ lovense: { action_mode: v } })}
                options={[
                  { id: 'All', label: 'All Motors' },
                  { id: 'Vibrate', label: 'Vibrate Only' },
                ]}
              />
            </Field>
            <div className="space-y-1 pt-1">
              <Toggle
                label="Use HTTPS"
                checked={!!lovCfg.https}
                onChange={(v) => saveConfig({ lovense: { https: v } })}
              />
              <Toggle
                label="2s safety auto-stop"
                checked={lovCfg.use_time_sec_safety !== false}
                onChange={(v) => saveConfig({ lovense: { use_time_sec_safety: v } })}
              />
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
