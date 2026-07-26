import React from 'react';

// Small shared building blocks, styled to match the app (dark cards, purple
// accents, touch-friendly targets).

export function Card({ title, subtitle, children, accent }) {
  return (
    <section className="rounded-xl border border-gray-700 bg-gray-800/70 p-5 space-y-4">
      <div>
        <h2 className={`font-semibold text-lg ${accent || 'text-gray-100'}`}>{title}</h2>
        {subtitle && <p className="text-xs text-gray-500 mt-0.5 leading-snug">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

export function Field({ label, children, hint }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-gray-400">{label}</span>
      {children}
      {hint && <p className="text-[11px] text-gray-600 leading-snug">{hint}</p>}
    </label>
  );
}

export function TextInput(props) {
  return (
    <input
      {...props}
      className={`w-full px-3 py-2 rounded-md bg-gray-900 border border-gray-700
        text-sm text-gray-200 focus:border-purple-500 focus:outline-none
        ${props.className || ''}`}
    />
  );
}

export function Toggle({ checked, onChange, label }) {
  return (
    <label className="flex items-center justify-between gap-3 py-1 cursor-pointer">
      <span className="text-sm text-gray-300">{label}</span>
      <input type="checkbox" checked={!!checked}
        onChange={(e) => onChange(e.target.checked)}
        className="w-5 h-5 accent-purple-500" />
    </label>
  );
}

export function Button({ children, onClick, kind, disabled, title }) {
  const styles = {
    primary: 'bg-purple-600 hover:bg-purple-500 text-white',
    danger: 'bg-red-600/90 hover:bg-red-500 text-white',
    ghost: 'bg-gray-700 hover:bg-gray-600 text-gray-200',
  }[kind || 'ghost'];
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      className={`px-4 py-2 rounded-md text-sm font-medium transition-colors
        disabled:opacity-40 disabled:cursor-not-allowed ${styles}`}>
      {children}
    </button>
  );
}

export function Dot({ ok, warn }) {
  const color = ok ? 'bg-green-500' : warn ? 'bg-yellow-500' : 'bg-gray-600';
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${color}`} />;
}

export const PATTERN_OPTIONS = ['constant', 'pulse', 'wave'];

export function Select({ value, onChange, options, allowEmpty }) {
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}
      className="px-2 py-2 rounded-md bg-gray-900 border border-gray-700 text-sm
        text-gray-200 focus:border-purple-500 focus:outline-none">
      {allowEmpty && <option value="">inherit</option>}
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}
