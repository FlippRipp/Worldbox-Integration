import React, { useState } from 'react';
import { Card, Segmented, WaveformPreview } from './ui';
import PatternEditorModal from './PatternEditorModal';

export default function PatternGalleryCard({
  patterns,
  config,
  saveConfig,
  status,
  api,
  refresh,
  onPatternsUpdated,
}) {
  const [previewBand, setPreviewBand] = useState('high'); // 'moderate' | 'high'
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingPattern, setEditingPattern] = useState(null);

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

  const triggerFeel = async (patId, bandOverride = null) => {
    if (!bandOverride && activePreview === patId) {
      await fetch(`${api}/stop`, { method: 'POST' });
    } else {
      const band = bandOverride || previewBand;
      const strength = band === 'high' ? 88 : 56;
      await fetch(`${api}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pattern: patId, strength, seconds: 0 }),
      });
    }
    refresh();
  };

  const handleBandChange = (nextBand) => {
    setPreviewBand(nextBand);
    if (activePreview) {
      triggerFeel(activePreview, nextBand);
    }
  };

  const openNewPattern = async () => {
    if (activePreview) {
      await fetch(`${api}/stop`, { method: 'POST' }).catch(() => {});
      refresh();
    }
    setEditingPattern(null);
    setEditorOpen(true);
  };

  const openEditPattern = async (pat) => {
    if (activePreview) {
      await fetch(`${api}/stop`, { method: 'POST' }).catch(() => {});
      refresh();
    }
    setEditingPattern(pat);
    setEditorOpen(true);
  };

  const handleDeleteOrReset = async (pat) => {
    const res = await fetch(`${api}/patterns/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: pat.id }),
    });
    if (res.ok) {
      const data = await res.json();
      if (onPatternsUpdated) onPatternsUpdated(data);
      refresh();
    }
  };

  const handleSaved = (data) => {
    setEditorOpen(false);
    setEditingPattern(null);
    if (onPatternsUpdated) onPatternsUpdated(data);
    refresh();
  };

  const badge = (
    <div className="flex items-center gap-2 flex-wrap justify-end">
      <button
        type="button"
        onClick={openNewPattern}
        className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-purple-600 hover:bg-purple-500 text-white shadow-sm transition-colors shrink-0"
      >
        + New Pattern
      </button>
      <div className="w-52 sm:w-56">
        <Segmented
          value={previewBand}
          onChange={handleBandChange}
          options={[
            { id: 'moderate', label: 'Moderate (58%)' },
            { id: 'high', label: 'High (92%)' },
          ]}
        />
      </div>
    </div>
  );

  return (
    <>
      <Card
        title="Dynamic Pattern Gallery"
        subtitle="Tap + New Pattern to sculpt your own waveform with drag handles and block repeats, or tap Edit on any built-in pattern to customize it. Switch between Moderate and High to preview intensity scaling."
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
                    <div className="flex items-center gap-1.5 min-w-0">
                      <h3 className="font-semibold text-sm text-gray-100 truncate">{p.label}</h3>
                      {!p.is_builtin && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-500/20 text-purple-200 border border-purple-500/30 shrink-0">
                          Custom
                        </span>
                      )}
                      {p.is_builtin && p.is_customized && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-300 border border-amber-500/30 shrink-0">
                          Edited
                        </span>
                      )}
                    </div>
                    <label
                      className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer select-none shrink-0"
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

                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => triggerFeel(p.id)}
                    className={`flex-1 py-1.5 px-2.5 rounded-lg text-xs font-medium transition-colors ${
                      isPlaying
                        ? 'bg-pink-600 hover:bg-pink-500 text-white'
                        : 'bg-gray-800 hover:bg-gray-700 text-purple-200 border border-gray-700'
                    }`}
                  >
                    {isPlaying ? '■ Stop' : `▶ Feel (${previewBand === 'high' ? '88%' : '56%'})`}
                  </button>
                  <button
                    type="button"
                    onClick={() => openEditPattern(p)}
                    title={`Edit ${p.label} in the visual curve editor`}
                    className="py-1.5 px-2.5 rounded-lg text-xs font-medium bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 transition-colors shrink-0"
                  >
                    ✎ Edit
                  </button>
                  {p.is_customized && (
                    <button
                      type="button"
                      onClick={() => handleDeleteOrReset(p)}
                      title={
                        p.is_builtin
                          ? `Reset ${p.label} back to factory default`
                          : `Delete custom pattern ${p.label}`
                      }
                      className="py-1.5 px-2 rounded-lg text-xs font-medium bg-gray-900 hover:bg-red-950/60 text-gray-400 hover:text-red-200 border border-gray-700/80 transition-colors shrink-0"
                    >
                      {p.is_builtin ? '↺' : '✕'}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <PatternEditorModal
        open={editorOpen}
        initialPattern={editingPattern}
        api={api}
        onClose={() => setEditorOpen(false)}
        onSaved={handleSaved}
      />
    </>
  );
}
