import React from 'react';

// Per-story quick settings (module_configs). Everything global — devices,
// rules, models — lives in the Toy Studio main-menu screen.
export default function ToyLinkSettings({ config, onSaveConfig }) {
  const enabled = config?.enabled !== false;
  const cap = config?.master_cap ?? 70;

  return (
    <div className="space-y-4 text-sm">
      <label className="flex items-center justify-between gap-3">
        <span className="text-gray-300">Enabled for this story</span>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onSaveConfig({ ...config, enabled: e.target.checked })}
          className="w-4 h-4 accent-purple-500"
        />
      </label>
      <label className="block">
        <div className="flex justify-between text-gray-300 mb-1">
          <span>Intensity cap (this story)</span>
          <span className="font-mono text-gray-400">{cap}%</span>
        </div>
        <input
          type="range" min="0" max="100" value={cap}
          onChange={(e) => onSaveConfig({ ...config, master_cap: Number(e.target.value) })}
          className="w-full accent-purple-500"
        />
      </label>
      <p className="text-xs text-gray-500">
        Devices, trigger rules, and the classifier model are configured in
        <span className="text-gray-400"> Toy Studio</span> (main menu).
      </p>
    </div>
  );
}
