import React, { useState } from 'react';
import { Card, Field, TextInput, Button } from './ui';

export default function ModelSection({ config, saveConfig, status }) {
  const override = config.semantic?.model_override || '';
  const [draft, setDraft] = useState(override);
  const sem = status?.semantic || {};
  const appDefault = sem.app_default_model || 'app default';
  const usingCustom = !!override;

  return (
    <Card title="Classifier model"
      subtitle="Which LLM judges scene intensity in hybrid/AI mode. It must be comfortable with explicit prose — a model that refuses simply leaves keywords in charge.">
      <div className="space-y-2">
        <label className="flex items-start gap-3 cursor-pointer">
          <input type="radio" checked={!usingCustom}
            onChange={() => { setDraft(''); saveConfig({ semantic: { model_override: '' } }); }}
            className="mt-1 accent-purple-500" />
          <span>
            <span className="text-sm text-gray-200">App default</span>
            <span className="block text-xs text-gray-500 font-mono">{appDefault}</span>
            <span className="block text-[11px] text-gray-600">
              Follows the “module fast model” from the app’s provider settings.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-3 cursor-pointer">
          <input type="radio" checked={usingCustom}
            onChange={() => { if (draft) saveConfig({ semantic: { model_override: draft } }); }}
            className="mt-1 accent-purple-500" />
          <span className="flex-1">
            <span className="text-sm text-gray-200">Custom model for this module only</span>
            <span className="flex gap-2 mt-1">
              <TextInput value={draft} placeholder="e.g. provider/model-id"
                onChange={(e) => setDraft(e.target.value)} />
              <Button kind="primary" disabled={!draft.trim() || draft.trim() === override}
                onClick={() => saveConfig({ semantic: { model_override: draft.trim() } })}>
                Use
              </Button>
            </span>
          </span>
        </label>
      </div>

      <div className="text-xs text-gray-500 space-y-1 border-t border-gray-700/60 pt-3">
        <p>
          Classifier calls: <span className="text-gray-400">{sem.calls ?? 0}</span>
          {sem.in_backoff && <span className="text-yellow-500"> · backing off after repeated failures</span>}
        </p>
        {sem.last_verdict && (
          <p>Last verdict: <span className="font-mono text-gray-400">{JSON.stringify(sem.last_verdict)}</span></p>
        )}
        {sem.last_error && <p className="text-yellow-500/90">Last error: {sem.last_error}</p>}
      </div>
    </Card>
  );
}
