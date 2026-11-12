import React, { useState } from 'react';
import { Card, Segmented, WaveformPreview } from './ui';

export default function PatternGalleryCard({
  patterns,
  config,
  saveConfig,
  status,
  api,
  refresh,
}) {
  const [previewBand, setPreviewBand] = useState('high'); // 'moderate' | 'high'
  const enabledList = config.enabled_patterns || patterns.map((p) => p.id);
  const activePreview = status?.preview?.pattern || null;

  const toggleAllowed = (patId) => {
    const set = new Set(enabledList);
    if (set.has(patId)) {
      if (set.size <= 1) return; // Always keep at least one pattern enabled
      set.delete(patId);
    } else {
      set.add(patId);
    }
    saveConfig({ enabled_patterns: Array.from(set) });
  };

  const triggerFeel = async (patId) => {
    if (activePreview === patId) {
      await fetch(`${api}/stop`, { method: 'POST' });
    } else {
      const strength = previewBand === 'high' ? 88 : 56;
      await fetch(`${api}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pattern: patId, strength, seconds: 4.5 }),
      });
    }
    refresh();
  };

  const badge = (
    <div className="w-56">
      <Segmented
        value={previewBand}
        onChange={setPreviewBand}
        options={[
          { id: 'moderate', label: 'Moderate (58%)' },
          { id: 'high', label: 'High (92%)' },
        ]}
      />
    </div>
  );

  return (
    <Card
      title="Dynamic Pattern Gallery"
      subtitle="Switch between Moderate and High above to see how waveforms lift their floor and cycle rapidly between High & Medium near peak intensity to prevent numbing. Click Feel to audition any pattern on your Lovense."
      badge={badge}
    >
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3.5">
        {patterns.map((p) => {
          const allowed = enabledList.includes(p.id);
          const isPlaying = activePreview === p.id;
          const samples = previewBand === 'high' ? p.curve_high : p.curve_moderate;

          return (
            <div
              key={p.id}
              className={`rounded-xl border p-3.5 flex flex-col justify-between gap-3 transition-colors ${
                isPlaying
                  ? 'border-pink-500/80 bg-pink-950/15'
                  : allowed
                    ? 'border-gray-700/80 bg-gray-900/60 hover:border-gray-600'
                    : 'border-gray-800 bg-gray-900/25 opacity-60'
              }`}
            >
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-semibold text-sm text-gray-100">{p.label}</h3>
                  <label
                    className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer select-none"
                    title="Allow JEV to choose this pattern during stories"
                  >
                    <input
                      type="checkbox"
                      checked={allowed}
                      onChange={() => toggleAllowed(p.id)}
                      className="w-3.5 h-3.5 accent-purple-500"
                    />
                    <span>JEV</span>
                  </label>
                </div>

                <WaveformPreview samples={samples} active={isPlaying} idSuffix={p.id} />

                <p className="text-xs text-gray-400 leading-snug">{p.tagline}</p>
              </div>

              <button
                type="button"
                onClick={() => triggerFeel(p.id)}
                className={`w-full py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  isPlaying
                    ? 'bg-pink-600 hover:bg-pink-500 text-white'
                    : 'bg-gray-800 hover:bg-gray-700 text-purple-200 border border-gray-700'
                }`}
              >
                {isPlaying ? '■ Stop Preview' : `▶ Feel (${previewBand === 'high' ? '88%' : '56%'})`}
              </button>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
