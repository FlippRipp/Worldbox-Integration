import React from 'react';
import { Card, Field, TextInput, Toggle } from './ui';

export default function GeneralSection({ config, saveConfig }) {
  return (
    <Card title="General">
      <div className="grid md:grid-cols-3 gap-4">
        <Field label={`Global intensity cap — ${config.master_cap_global ?? 100}%`}
          hint="Hard ceiling for every story and every device. The per-story cap stacks on top.">
          <input type="range" min="0" max="100" value={config.master_cap_global ?? 100}
            onChange={(e) => saveConfig({ master_cap_global: Number(e.target.value) })}
            className="w-full accent-purple-500" />
        </Field>
        <Field label="Intensity ramp (ms per full swing)"
          hint="How fast levels change: lower = snappier, higher = smoother. Manual STOP is always instant.">
          <TextInput type="number" min="0" max="10000" step="100"
            value={config.ramp_ms ?? 500}
            onChange={(e) => saveConfig({ ramp_ms: Number(e.target.value) })} />
        </Field>
        <div className="pt-1">
          <Toggle label="Stop when a turn completes" checked={!!config.stop_on_turn_end}
            onChange={(v) => saveConfig({ stop_on_turn_end: v })} />
          <p className="text-[11px] text-gray-600 leading-snug">
            Off (default): the story’s mood keeps playing while you read and type.
          </p>
        </div>
      </div>
    </Card>
  );
}
