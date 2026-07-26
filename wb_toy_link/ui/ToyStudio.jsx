import React, { useState, useEffect, useRef, useCallback } from 'react';
import DevicesSection from './toystudio/DevicesSection';
import TriggersSection from './toystudio/TriggersSection';
import ModelSection from './toystudio/ModelSection';
import GeneralSection from './toystudio/GeneralSection';
import { Button } from './toystudio/ui';

// Toy Studio — the module's settings home, reached from its main-menu card.
// Config changes save immediately (each PUT sends only the touched keys);
// trigger rules are drafted locally and saved explicitly.
const API = '/api/modules/wb_toy_link';
const STATUS_POLL_MS = 2000;

export default function ToyStudio({ onBack }) {
  const [config, setConfig] = useState(null);
  const [rules, setRulesState] = useState(null);
  const [status, setStatus] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const savedRules = useRef(null);

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API}/status`);
      if (res.ok) setStatus(await res.json());
    } catch (e) { /* poll retries */ }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [cfgRes, rulesRes] = await Promise.all([
          fetch(`${API}/config`), fetch(`${API}/rules`)]);
        setConfig(await cfgRes.json());
        const r = await rulesRes.json();
        savedRules.current = JSON.stringify(r);
        setRulesState(r);
      } catch (e) {
        setError('Could not reach the Toy Link backend. Is the module loaded?');
      }
    })();
    refreshStatus();
    const timer = setInterval(refreshStatus, STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshStatus]);

  const saveConfig = useCallback(async (patch) => {
    try {
      const res = await fetch(`${API}/config`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (res.ok) setConfig(await res.json());
    } catch (e) { setError('Saving settings failed — backend unreachable.'); }
  }, []);

  const setRules = useCallback((next) => {
    setRulesState(next);
    setDirty(JSON.stringify(next) !== savedRules.current);
  }, []);

  const saveRules = useCallback(async () => {
    try {
      const res = await fetch(`${API}/rules`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rules),
      });
      if (res.ok) {
        const saved = await res.json();
        savedRules.current = JSON.stringify(saved);
        setRulesState(saved);
        setDirty(false);
      }
    } catch (e) { setError('Saving rules failed — backend unreachable.'); }
  }, [rules]);

  if (!config || !rules) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-950 via-gray-900 to-gray-950 flex items-center justify-center text-gray-500">
        {error || 'Loading Toy Studio…'}
      </div>
    );
  }

  const level = Math.round((status?.level || 0) * 100);

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-950 via-gray-900 to-gray-950 text-gray-100">
      <div className="max-w-5xl mx-auto p-4 md:p-6 space-y-5">
        <header className="flex items-center gap-4 flex-wrap">
          <Button onClick={onBack}>← Back</Button>
          <div className="flex-1 min-w-[10rem]">
            <h1 className="text-2xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
              🎛️ Toy Studio
            </h1>
            <p className="text-xs text-gray-500">
              Devices, triggers, and haptics settings — app-wide.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="w-28 h-2 bg-gray-800 rounded overflow-hidden">
              <div className="h-full bg-gradient-to-r from-purple-500 to-pink-500 transition-all duration-300"
                style={{ width: `${level}%` }} />
            </div>
            <span className="text-sm font-mono text-gray-400 w-10">{level}%</span>
            <Button kind="danger" onClick={() => fetch(`${API}/stop`, { method: 'POST' })}>
              ■ STOP
            </Button>
          </div>
        </header>

        {status && !status.hooks_available && (
          <div className="rounded-xl border border-yellow-700/50 bg-yellow-900/20 p-4 text-sm text-yellow-200">
            ⚠ This WorldboxAI build doesn’t expose the streaming module API —
            update WorldboxAI to a build with stream hooks. Devices and settings
            work, but story triggers stay inactive.
          </div>
        )}
        {error && (
          <div className="rounded-xl border border-red-700/50 bg-red-900/20 p-4 text-sm text-red-200">
            {error}
          </div>
        )}

        <DevicesSection config={config} saveConfig={saveConfig} status={status}
          api={API} refresh={refreshStatus} />
        <TriggersSection config={config} saveConfig={saveConfig} rules={rules}
          setRules={setRules} saveRules={saveRules} dirty={dirty} api={API} />
        <ModelSection config={config} saveConfig={saveConfig} status={status} />
        <GeneralSection config={config} saveConfig={saveConfig} />

        <p className="text-[11px] text-gray-600 leading-relaxed pb-6">
          Privacy: in hybrid/AI mode, recent story prose is sent to the
          configured LLM provider for classification — the same provider that
          already writes the story. Keywords-only mode makes no calls at all.
        </p>
      </div>
    </div>
  );
}
