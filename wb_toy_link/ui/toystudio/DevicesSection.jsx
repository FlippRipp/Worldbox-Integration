import React, { useState } from 'react';
import { Card, Field, TextInput, Toggle, Button, Dot } from './ui';

export default function DevicesSection({ config, saveConfig, status, api, refresh }) {
  const [busy, setBusy] = useState('');
  const [testStrength, setTestStrength] = useState(40);
  const lov = config.lovense || {};
  const bp = config.buttplug || {};
  const backends = Object.fromEntries((status?.backends || []).map((b) => [b.name, b]));

  const connect = async (name) => {
    setBusy(name);
    try { await fetch(`${api}/backends/${name}/connect`, { method: 'POST' }); }
    catch (e) { /* status poll shows the error */ }
    setBusy('');
    refresh();
  };

  const deviceLine = (b) => {
    if (!b) return '';
    if (b.connected) {
      const names = (b.devices || []).map((d) => d.name || d.id || 'device');
      return names.length ? names.join(', ') : 'connected, no devices yet';
    }
    return b.last_error || (b.enabled ? 'not connected' : 'disabled');
  };

  return (
    <Card title="Devices"
      subtitle="Both backends can run at once; every connected device follows the same signal.">
      <div className="grid md:grid-cols-2 gap-5">
        <div className="space-y-3 rounded-lg border border-gray-700/60 p-4">
          <div className="flex items-center gap-2">
            <Dot ok={backends.lovense?.connected} warn={lov.enabled && !backends.lovense?.connected} />
            <h3 className="font-medium text-gray-200">Lovense Remote (Game Mode)</h3>
          </div>
          <Toggle label="Enabled" checked={lov.enabled}
            onChange={(v) => saveConfig({ lovense: { enabled: v } })} />
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <Field label="Phone IP (shown in Game Mode)">
                <TextInput value={lov.host || ''} placeholder="192.168.1.x"
                  onChange={(e) => saveConfig({ lovense: { host: e.target.value } })} />
              </Field>
            </div>
            <Field label="Port">
              <TextInput type="number" value={lov.port ?? 20010}
                onChange={(e) => saveConfig({ lovense: { port: Number(e.target.value) } })} />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Button kind="primary" disabled={!lov.enabled || !lov.host || busy === 'lovense'}
              onClick={() => connect('lovense')}>
              {busy === 'lovense' ? 'Connecting…' : 'Connect / refresh toys'}
            </Button>
            <span className="text-xs text-gray-500 truncate">{deviceLine(backends.lovense)}</span>
          </div>
        </div>

        <div className="space-y-3 rounded-lg border border-gray-700/60 p-4">
          <div className="flex items-center gap-2">
            <Dot ok={backends.buttplug?.connected} warn={bp.enabled && !backends.buttplug?.connected} />
            <h3 className="font-medium text-gray-200">Intiface Central (buttplug.io)</h3>
          </div>
          <Toggle label="Enabled" checked={bp.enabled}
            onChange={(v) => saveConfig({ buttplug: { enabled: v } })} />
          <Field label="Websocket URL">
            <TextInput value={bp.url || ''} placeholder="ws://127.0.0.1:12345"
              onChange={(e) => saveConfig({ buttplug: { url: e.target.value } })} />
          </Field>
          <div className="flex items-center gap-3">
            <Button kind="primary" disabled={!bp.enabled || busy === 'buttplug'}
              onClick={() => connect('buttplug')}>
              {busy === 'buttplug' ? 'Connecting…' : 'Connect'}
            </Button>
            <span className="text-xs text-gray-500 truncate">{deviceLine(backends.buttplug)}</span>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <Button kind="primary" onClick={() =>
          fetch(`${api}/test`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ strength: testStrength }) })}>
          ⚡ Test buzz
        </Button>
        <input type="range" min="5" max="100" value={testStrength}
          onChange={(e) => setTestStrength(Number(e.target.value))}
          className="flex-1 accent-purple-500" />
        <span className="text-sm font-mono text-gray-400 w-10">{testStrength}%</span>
        <Button kind="danger" onClick={() => fetch(`${api}/stop`, { method: 'POST' })}>
          ■ STOP
        </Button>
      </div>
    </Card>
  );
}
