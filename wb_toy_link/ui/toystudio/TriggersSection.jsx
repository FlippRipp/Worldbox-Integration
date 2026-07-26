import React, { useState } from 'react';
import { Card, Field, TextInput, Toggle, Button, Select, PATTERN_OPTIONS } from './ui';

const MODES = [
  { id: 'hybrid', label: 'Hybrid',
    hint: 'Keywords react instantly; the AI verdict corrects them a second later. Recommended.' },
  { id: 'keywords', label: 'Keywords only',
    hint: 'Exact word matches only — fast, offline, but literal.' },
  { id: 'semantic', label: 'AI only',
    hint: 'Only the classifier drives intensity (reacts about a sentence behind).' },
];

function CategoryRow({ name, cat, onChange, onRemove }) {
  const set = (patch) => onChange({ ...cat, ...patch });
  return (
    <div className="grid grid-cols-12 gap-2 items-center text-sm">
      <span className="col-span-3 font-mono text-gray-300 truncate">{name}</span>
      <div className="col-span-2 flex items-center gap-1">
        <TextInput type="number" min="0" max="100" value={cat.strength ?? 0}
          onChange={(e) => set({ strength: Number(e.target.value) })} />
      </div>
      <div className="col-span-3">
        <Select value={cat.pattern || 'constant'} options={PATTERN_OPTIONS}
          onChange={(v) => set({ pattern: v })} />
      </div>
      <div className="col-span-3 text-xs text-gray-500">
        {cat.pattern === 'pulse' && (
          <span className="flex gap-1 items-center">
            <TextInput type="number" value={cat.pulse_on_ms ?? 400}
              onChange={(e) => set({ pulse_on_ms: Number(e.target.value) })} />
            /
            <TextInput type="number" value={cat.pulse_off_ms ?? 250}
              onChange={(e) => set({ pulse_off_ms: Number(e.target.value) })} />
            ms
          </span>
        )}
        {cat.pattern === 'wave' && (
          <span className="flex gap-1 items-center">
            <TextInput type="number" value={cat.wave_low_pct ?? 30}
              onChange={(e) => set({ wave_low_pct: Number(e.target.value) })} />
            % /
            <TextInput type="number" value={cat.wave_period_ms ?? 2500}
              onChange={(e) => set({ wave_period_ms: Number(e.target.value) })} />
            ms
          </span>
        )}
        {(!cat.pattern || cat.pattern === 'constant') && <span>steady</span>}
      </div>
      <button onClick={onRemove} className="col-span-1 text-gray-600 hover:text-red-400">✕</button>
    </div>
  );
}

function RuleRow({ rule, categories, onChange, onRemove }) {
  const set = (patch) => onChange({ ...rule, ...patch });
  return (
    <div className="grid grid-cols-12 gap-2 items-center text-sm">
      <div className="col-span-4">
        <TextInput value={(rule.keywords || []).join(', ')}
          placeholder="keyword, phrase, …"
          onChange={(e) => set({
            keywords: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} />
      </div>
      <div className="col-span-2">
        <Select value={rule.category} options={Object.keys(categories)}
          onChange={(v) => set({ category: v })} />
      </div>
      <div className="col-span-2">
        <TextInput type="number" min="0" max="100" placeholder="inherit"
          value={rule.strength ?? ''}
          onChange={(e) => set({
            strength: e.target.value === '' ? null : Number(e.target.value) })} />
      </div>
      <div className="col-span-2">
        <Select value={rule.pattern} options={PATTERN_OPTIONS} allowEmpty
          onChange={(v) => set({ pattern: v })} />
      </div>
      <div className="col-span-1 flex justify-center">
        <input type="checkbox" checked={rule.enabled !== false}
          onChange={(e) => set({ enabled: e.target.checked })}
          className="w-4 h-4 accent-purple-500" />
      </div>
      <button onClick={onRemove} className="col-span-1 text-gray-600 hover:text-red-400">✕</button>
    </div>
  );
}

function Tester({ api }) {
  const [text, setText] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async (withAI) => {
    setBusy(true);
    try {
      const res = await fetch(`${api}/rules/test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, semantic: withAI }),
      });
      setResult(await res.json());
    } catch (e) {
      setResult({ error: String(e) });
    }
    setBusy(false);
  };

  return (
    <div className="space-y-2 rounded-lg border border-gray-700/60 p-4">
      <h3 className="font-medium text-gray-200">Try it on a paragraph</h3>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
        placeholder="Paste story prose here to see what would trigger — nothing is sent to a device."
        className="w-full px-3 py-2 rounded-md bg-gray-900 border border-gray-700 text-sm
          text-gray-200 focus:border-purple-500 focus:outline-none resize-y" />
      <div className="flex gap-2">
        <Button onClick={() => run(false)} disabled={!text.trim() || busy}>Test keywords</Button>
        <Button kind="primary" onClick={() => run(true)} disabled={!text.trim() || busy}>
          {busy ? 'Testing…' : 'Test keywords + AI'}
        </Button>
      </div>
      {result && (
        <div className="text-xs space-y-1 pt-1">
          <div className="flex flex-wrap gap-1.5">
            {(result.keyword_effects || []).length === 0 &&
              <span className="text-gray-500">No keyword matches.</span>}
            {(result.keyword_effects || []).map((e, i) => (
              <span key={i} className="px-2 py-0.5 rounded-full bg-purple-900/50 border border-purple-700/50 text-purple-200">
                {e.rule_id} → {e.category} {Math.round(e.strength)}% {e.pattern}
              </span>
            ))}
          </div>
          {result.semantic_verdict && (
            <p className="text-gray-300">
              AI verdict: <span className="font-mono">{JSON.stringify(result.semantic_verdict)}</span>
            </p>
          )}
          {result.semantic_error && (
            <p className="text-yellow-500/90">AI test failed: {result.semantic_error}</p>
          )}
          <p className="text-gray-600">Last match in the text wins the latch.</p>
        </div>
      )}
    </div>
  );
}

export default function TriggersSection({ config, saveConfig, rules, setRules,
                                          saveRules, dirty, api }) {
  const categories = rules?.categories || {};
  const [newCat, setNewCat] = useState('');

  const updateRule = (i, rule) => {
    const next = [...rules.rules];
    next[i] = rule;
    setRules({ ...rules, rules: next });
  };

  return (
    <Card title="Triggers"
      subtitle="Define when vibration fires. Categories set the defaults; rules map words and phrases onto them. A category with strength 0 is a stop signal.">
      <div className="flex flex-wrap gap-2">
        {MODES.map((m) => (
          <button key={m.id} onClick={() => saveConfig({ trigger_mode: m.id })}
            title={m.hint}
            className={`px-4 py-2 rounded-lg border text-sm transition-colors ${
              (config.trigger_mode || 'hybrid') === m.id
                ? 'border-purple-500 bg-purple-600/20 text-purple-200'
                : 'border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-500'}`}>
            {m.label}
          </button>
        ))}
      </div>
      <p className="text-xs text-gray-500 -mt-2">
        {MODES.find((m) => m.id === (config.trigger_mode || 'hybrid'))?.hint}
      </p>

      <div className="space-y-2">
        <div className="grid grid-cols-12 gap-2 text-[11px] uppercase tracking-wide text-gray-600">
          <span className="col-span-3">Category</span><span className="col-span-2">Strength</span>
          <span className="col-span-3">Pattern</span><span className="col-span-3">Timing</span>
        </div>
        {Object.entries(categories).map(([name, cat]) => (
          <CategoryRow key={name} name={name} cat={cat}
            onChange={(c) => setRules({ ...rules, categories: { ...categories, [name]: c } })}
            onRemove={() => {
              const next = { ...categories };
              delete next[name];
              setRules({ ...rules, categories: next });
            }} />
        ))}
        <div className="flex gap-2">
          <TextInput value={newCat} placeholder="new category name"
            onChange={(e) => setNewCat(e.target.value)} className="max-w-[12rem]" />
          <Button onClick={() => {
            const name = newCat.trim();
            if (!name || categories[name]) return;
            setRules({ ...rules, categories: { ...categories,
              [name]: { strength: 50, pattern: 'constant' } } });
            setNewCat('');
          }}>+ Category</Button>
        </div>
      </div>

      <div className="space-y-2">
        <div className="grid grid-cols-12 gap-2 text-[11px] uppercase tracking-wide text-gray-600">
          <span className="col-span-4">Keywords / phrases</span><span className="col-span-2">Category</span>
          <span className="col-span-2">Strength</span><span className="col-span-2">Pattern</span>
          <span className="col-span-1 text-center">On</span>
        </div>
        {(rules?.rules || []).map((rule, i) => (
          <RuleRow key={rule.id || i} rule={rule} categories={categories}
            onChange={(r) => updateRule(i, r)}
            onRemove={() => setRules({ ...rules,
              rules: rules.rules.filter((_, j) => j !== i) })} />
        ))}
        <Button onClick={() => setRules({ ...rules, rules: [...(rules?.rules || []), {
          id: `r_${Date.now().toString(36)}`, keywords: [],
          category: Object.keys(categories)[0] || '', enabled: true,
          strength: null, pattern: null }] })}>+ Rule</Button>
      </div>

      <div className="flex items-center gap-3">
        <Button kind="primary" onClick={saveRules} disabled={!dirty}>
          {dirty ? 'Save trigger rules' : 'Rules saved'}
        </Button>
        {dirty && <span className="text-xs text-yellow-500/80">Unsaved changes</span>}
      </div>

      <Tester api={api} />
    </Card>
  );
}
