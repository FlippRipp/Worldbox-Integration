import React from 'react';

export function Card({ title, subtitle, badge, children }) {
  return (
    <section className="rounded-xl border border-gray-700/80 bg-gray-800/60 p-5 space-y-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-lg text-gray-100">{title}</h2>
          {subtitle && <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{subtitle}</p>}
        </div>
        {badge}
      </div>
      {children}
    </section>
  );
}

export function Field({ label, rightLabel, children, hint }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-300 font-medium">{label}</span>
        {rightLabel && <span className="font-mono text-purple-300">{rightLabel}</span>}
      </div>
      {children}
      {hint && <p className="text-[11px] text-gray-500 leading-snug">{hint}</p>}
    </div>
  );
}

export function TextInput(props) {
  return (
    <input
      {...props}
      className={`w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-700
        text-sm text-gray-200 focus:border-purple-500 focus:outline-none
        ${props.className || ''}`}
    />
  );
}

export function Toggle({ checked, onChange, label, hint }) {
  return (
    <label className="flex items-center justify-between gap-3 py-1 cursor-pointer">
      <div>
        <span className="text-sm text-gray-200">{label}</span>
        {hint && <p className="text-[11px] text-gray-500 leading-snug">{hint}</p>}
      </div>
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange(e.target.checked)}
        className="w-5 h-5 accent-purple-500 shrink-0"
      />
    </label>
  );
}

export function Segmented({ value, onChange, options }) {
  return (
    <div className="grid grid-flow-col auto-cols-fr rounded-lg bg-gray-900/90 p-1 border border-gray-700/80 gap-1">
      {options.map((opt) => {
        const active = value === opt.id;
        return (
          <button
            key={opt.id}
            type="button"
            title={opt.hint}
            onClick={() => onChange(opt.id)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              active
                ? 'bg-purple-600 text-white shadow-sm'
                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/60'
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

export function Button({ children, onClick, kind, disabled, title, className }) {
  const styles = {
    primary: 'bg-purple-600 hover:bg-purple-500 text-white',
    danger: 'bg-red-600/90 hover:bg-red-500 text-white',
    ghost: 'bg-gray-700/80 hover:bg-gray-600 text-gray-200',
    subtle: 'bg-gray-900 hover:bg-gray-800 text-gray-300 border border-gray-700',
  }[kind || 'ghost'];
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`px-3.5 py-2 rounded-lg text-sm font-medium transition-colors
        disabled:opacity-40 disabled:cursor-not-allowed ${styles} ${className || ''}`}
    >
      {children}
    </button>
  );
}

export function Dot({ ok, warn }) {
  const color = ok ? 'bg-emerald-400' : warn ? 'bg-amber-400' : 'bg-gray-600';
  return <span className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${color}`} />;
}

export function WaveformPreview({ samples = [], active = false, idSuffix = 'wf' }) {
  if (!samples || samples.length < 2) {
    return <div className="h-14 rounded bg-gray-900/80" />;
  }
  const w = 240;
  const h = 52;
  const padY = 4;
  const usableH = h - padY * 2;
  const pts = samples.map((v, i) => {
    const x = (i / (samples.length - 1)) * w;
    const y = padY + usableH * (1 - Math.min(1, Math.max(0, v)));
    return [x.toFixed(1), y.toFixed(1)];
  });
  const linePath = `M ${pts.map((p) => p.join(',')).join(' L ')}`;
  const areaPath = `${linePath} L ${w},${h} L 0,${h} Z`;
  const gradId = `wf-grad-${idSuffix}`;

  // Midline at 50% helps visually highlight High↔Medium anti-numbing contrast
  const midY = (padY + usableH * 0.5).toFixed(1);

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className={`w-full h-14 rounded-lg border ${
        active ? 'border-pink-500/60 bg-gray-950' : 'border-gray-800 bg-gray-950/80'
      }`}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={active ? '#f472b6' : '#a855f7'} stopOpacity="0.45" />
          <stop offset="100%" stopColor={active ? '#ec4899' : '#7e22ce'} stopOpacity="0.03" />
        </linearGradient>
      </defs>
      <line x1="0" y1={midY} x2={w} y2={midY} stroke="#374151" strokeDasharray="3 3" strokeWidth="0.8" />
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={active ? '#f472b6' : '#c084fc'}
        strokeWidth="1.8"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
