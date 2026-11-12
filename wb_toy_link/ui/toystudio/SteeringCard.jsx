import React, { useState } from 'react';
import { Card, Field, Toggle, Segmented, Button, Dot } from './ui';

export default function SteeringCard({ config, saveConfig, status, api, refresh }) {
  const jev = status?.jev || {};
  const sensitivityPct = Math.round(((config.jev?.sensitivity ?? 1.0) || 1.0) * 100);

  const [showTester, setShowTester] = useState(false);
  const [sampleText, setSampleText] = useState('');
  const [testRes, setTestRes] = useState(null);
  const [testing, setTesting] = useState(false);

  const enableJev = async () => {
    try {
      await fetch(`${api}/jev/enable`, { method: 'POST' });
    } catch (e) {
      /* ignore */
    }
    refresh();
  };

  const runProseTest = async (feel) => {
    setTesting(true);
    setTestRes(null);
    try {
      const res = await fetch(`${api}/jev/test-prose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sampleText, feel }),
      });
      setTestRes(await res.json());
    } catch (e) {
      setTestRes({ ok: false, error: String(e) });
    }
    setTesting(false);
    refresh();
  };

  const badge = (
    <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-gray-900 border border-gray-700 text-xs">
      <Dot ok={jev.ready} warn={jev.available && !jev.ready} />
      <span className={jev.ready ? 'text-purple-200' : 'text-amber-300'}>
        {jev.ready
          ? 'JEV Active'
          : !jev.available
            ? 'JEV Unavailable'
            : !jev.enabled
              ? 'JEV Disabled'
              : 'Needs API Key'}
      </span>
    </div>
  );

  return (
    <Card
      title="JEV Steering & Feel"
      subtitle="JEV reads story momentum in ~200 ms, filtering out negations and memories while steering analog intensity, tempo, and pattern choice."
      badge={badge}
    >
      {/* Actionable banner if JEV is turned off or missing credentials */}
      {jev.available && !jev.enabled && (
        <div className="flex items-center justify-between gap-3 rounded-lg bg-purple-950/40 border border-purple-700/50 px-4 py-3 text-xs text-purple-200">
          <span>JEV decisions are currently switched off in Worldbox Model Settings.</span>
          <Button kind="primary" onClick={enableJev}>
            Enable JEV Now
          </Button>
        </div>
      )}
      {jev.available && jev.enabled && !jev.has_credentials && (
        <div className="rounded-lg bg-amber-950/30 border border-amber-700/50 px-4 py-3 text-xs text-amber-200">
          JEV is enabled, but no OpenRouter, NanoGPT, or TypeSafe API key is configured yet. Add one in Worldbox’s Model Settings.
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-5">
        <Field
          label="Global Intensity Ceiling"
          rightLabel={`${config.master_cap_global ?? 100}%`}
          hint="Hard maximum across all stories. Each story also has its own ceiling in sidebar settings."
        >
          <input
            type="range"
            min="0"
            max="100"
            value={config.master_cap_global ?? 100}
            onChange={(e) => saveConfig({ master_cap_global: Number(e.target.value) })}
            className="w-full accent-purple-500"
          />
        </Field>

        <Field
          label="JEV Intensity Scale"
          rightLabel={`${sensitivityPct}%`}
          hint="Boost or soften JEV’s judged intensity before caps are applied."
        >
          <input
            type="range"
            min="50"
            max="150"
            step="5"
            value={sensitivityPct}
            onChange={(e) => saveConfig({ jev: { sensitivity: Number(e.target.value) / 100 } })}
            className="w-full accent-purple-500"
          />
        </Field>

        <Field
          label="High-Intensity Anti-Numbing Contrast"
          hint="Rapidly cycles between High and Medium as intensity rises so sensation stays sharp instead of numbing."
        >
          <Segmented
            value={config.contrast_mode || 'balanced'}
            onChange={(v) => saveConfig({ contrast_mode: v })}
            options={[
              { id: 'subtle', label: 'Subtle', hint: 'Gentler high-intensity modulation' },
              { id: 'balanced', label: 'Balanced', hint: 'Crisp High↔Medium contrast above ~60%' },
              { id: 'high', label: 'High Contrast', hint: 'Strong punchy High↔Medium contrast from 50%+' },
            ]}
          />
        </Field>

        <Field
          label="Transition Smoothness"
          hint="How quickly overall intensity glides when the scene shifts."
        >
          <Segmented
            value={config.response_feel || 'natural'}
            onChange={(v) => saveConfig({ response_feel: v })}
            options={[
              { id: 'silky', label: 'Silky', hint: 'Slow ~900ms swells between intensity changes' },
              { id: 'natural', label: 'Natural', hint: 'Balanced ~420ms glide' },
              { id: 'snappy', label: 'Snappy', hint: 'Fast ~140ms response' },
            ]}
          />
        </Field>
      </div>

      <div className="pt-2 border-t border-gray-700/50 space-y-4">
        <Field
          label="Between Storyteller Blocks"
          hint="What your toy should do once a storyteller block finishes (or when Paced Stage finishes revealing the block)."
        >
          <Segmented
            value={config.between_blocks || (config.stop_on_turn_end ? 'stop' : 'continue')}
            onChange={(v) => saveConfig({ between_blocks: v, stop_on_turn_end: v === 'stop' })}
            options={[
              { id: 'continue', label: 'Continue Last', hint: 'Keep the closing pattern & intensity playing while you reply' },
              { id: 'low', label: 'Low Intensity', hint: 'Ease active scenes down to a gentle ~20% Velvet simmer between blocks' },
              { id: 'stop', label: 'Stop', hint: 'Stop automatically between storyteller blocks' },
            ]}
          />
        </Field>

        <Toggle
          label="Sync haptics sentence-by-sentence in Paced Stage mode"
          hint="When Worldbox is set to Paced Stage mode, JEV pre-scores sentences ahead of your reading cursor so toy transitions fire right as each sentence appears."
          checked={config.sync_stage_reader !== false}
          onChange={(v) => saveConfig({ sync_stage_reader: v })}
        />
      </div>

      {/* Optional prose tester tucked away cleanly */}
      <div>
        <button
          type="button"
          onClick={() => setShowTester((v) => !v)}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          {showTester ? '▾ Hide JEV prose tester' : '▸ Test JEV on a sample sentence'}
        </button>

        {showTester && (
          <div className="mt-3 p-3.5 rounded-lg bg-gray-900/70 border border-gray-700/70 space-y-2.5">
            <textarea
              rows={2}
              value={sampleText}
              onChange={(e) => setSampleText(e.target.value)}
              placeholder="Type or paste a sentence of story prose to see how JEV steers intensity, pattern, and tempo…"
              className="w-full px-3 py-2 rounded-lg bg-gray-950 border border-gray-700 text-sm text-gray-200 focus:border-purple-500 focus:outline-none"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                kind="subtle"
                disabled={!sampleText.trim() || testing}
                onClick={() => runProseTest(false)}
              >
                {testing ? 'Judging…' : 'Judge Only'}
              </Button>
              <Button
                kind="primary"
                disabled={!sampleText.trim() || testing}
                onClick={() => runProseTest(true)}
              >
                ⚡ Judge & Feel (3.5s)
              </Button>
              {jev.last_verdict && !testRes && (
                <span className="text-xs text-gray-500 ml-auto">
                  Last live call: {jev.last_verdict.pattern || 'hold'} ({jev.last_verdict.strength ?? 0}%)
                </span>
              )}
            </div>
            {testRes && (
              <div className="text-xs pt-1">
                {!testRes.ok ? (
                  <span className="text-amber-400">JEV error: {testRes.error}</span>
                ) : testRes.verdict.action === 'hold' ? (
                  <span className="text-gray-400">
                    Verdict: Hold current state ({testRes.verdict.reason || 'no change'})
                  </span>
                ) : (
                  <div className="flex flex-wrap gap-2 items-center text-purple-200">
                    <span className="px-2.5 py-1 rounded-md bg-purple-900/50 border border-purple-600/40 font-medium">
                      Pattern: {testRes.verdict.pattern}
                    </span>
                    <span className="px-2.5 py-1 rounded-md bg-pink-900/40 border border-pink-600/40 font-mono">
                      Strength: {testRes.verdict.strength}%
                    </span>
                    <span className="px-2.5 py-1 rounded-md bg-gray-800 border border-gray-700 font-mono">
                      Pace: {Math.round((testRes.verdict.pace || 0) * 100)}%
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
