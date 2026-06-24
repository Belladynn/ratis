// Ratis — Liste / Scan / Produit / Profil screens
// Matches the v4 Lighter visual language: raised cards, 3D hard shadows,
// chunky 800-900 weight type, color-coded sections.

// ─────────────────────────────────────────────────────────────────────
// Shared helpers
// ─────────────────────────────────────────────────────────────────────
const Cabecoin = window.Cabecoin || (() => null);

// Tweaks context — created here so it exists before any component renders
window.RatisTweaksCtx = React.createContext({ btnStyle: '3d', tabStyle: 'underline', optimizeColor: 'coral' });

// Hook that works across Babel scopes: reads window.RatisTweaks + re-renders on change
function useRatisTweaks() {
  const [t, setT] = React.useState(() => window.RatisTweaks || { btnStyle: '3d', tabStyle: 'underline', optimizeColor: 'coral' });React.useEffect(() => {
    const handler = (e) => setT({ ...e.detail });
    window.addEventListener('ratis-tweaks-change', handler);
    return () => window.removeEventListener('ratis-tweaks-change', handler);
  }, []);
  return t;
}

function formatBalanceN(n) {return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '\u00A0');}

const screenCardBase = {
  borderRadius: 20,
  background: '#27293A',
  border: '1.5px solid rgba(255,255,255,0.08)',
  boxShadow: '0 5px 0 rgba(0,0,0,0.35), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08)',
  padding: 14,
  position: 'relative',
  overflow: 'hidden'
};

// pill section heading (used across screens)
function SectionHead({ icon, label, color, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, paddingLeft: 4, paddingRight: 4 }}>
      {icon != null && <span style={{ fontSize: 13 }}>{icon}</span>}
      <span style={{ flex: 1, fontSize: 11, fontWeight: 800, color, letterSpacing: '0.8px', textTransform: 'uppercase' }}>{label}</span>
      {right}
    </div>);

}

// 3D press button — primary action style, supports btnStyle tweak
function GameButton({ children, onClick, color = 'teal', disabled, icon, fullWidth, size = 'md', style: overrideStyle }) {
  const [pressed, setPressed] = React.useState(false);
  const tweaks = useRatisTweaks();
  const btnStyle = tweaks.btnStyle || '3d';

  const PALETTES = {
    teal: { top: '#E8896A', bot: '#DA7756', stroke: '#A8562E', dropS: '#6B3218', text: '#fff', solid: '#DA7756', glow: '218,119,86' },
    violet: { top: '#B49AFB', bot: '#7C3AED', stroke: '#5B21B6', dropS: '#3B1380', text: '#fff', solid: '#7C3AED', glow: '139,92,246' },
    gold: { top: '#FFE066', bot: '#FFB800', stroke: '#B47800', dropS: '#7E5300', text: '#3A2200', solid: '#FFB800', glow: '255,184,0' },
    coral: { top: '#E8896A', bot: '#DA7756', stroke: '#A8562E', dropS: '#6B3218', text: '#fff', solid: '#DA7756', glow: '218,119,86' },
    cyan: { top: '#67E8F9', bot: '#0EA5E9', stroke: '#0369A1', dropS: '#0C3F68', text: '#042940', solid: '#0EA5E9', glow: '14,165,233' },
    slate: { top: 'transparent', bot: 'transparent', stroke: 'rgba(255,255,255,0.22)', dropS: 'rgba(0,0,0,0)', text: 'rgba(255,255,255,0.75)', solid: '#fff', glow: '255,255,255' },
    'terracotta-outline': { top: 'transparent', bot: 'transparent', stroke: '#DA7756', dropS: 'rgba(0,0,0,0)', text: '#DA7756', solid: '#DA7756', glow: '218,119,86' }
  };
  const p = PALETTES[color] || PALETTES.teal;
  const pads = { sm: '8px 12px', md: '11px 16px', lg: '14px 20px' };
  const fonts = { sm: 11, md: 13, lg: 14 };

  const getStyle = () => {
    const base = {
      width: fullWidth ? '100%' : 'auto',
      padding: pads[size], fontSize: fonts[size], fontWeight: 900,
      letterSpacing: '-0.1px', cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
      fontFamily: 'inherit', transition: 'transform 0.08s ease, box-shadow 0.08s ease, opacity 0.08s',
      ...overrideStyle
    };

    if (btnStyle === 'flat') return {
      ...base,
      color: p.text === '#fff' ? p.solid : p.text,
      background: `rgba(${p.glow},0.16)`,
      border: `1.5px solid rgba(${p.glow},0.35)`,
      borderRadius: 12,
      boxShadow: 'none',
      transform: 'none'
    };

    if (btnStyle === 'outline') return {
      ...base,
      color: p.solid,
      background: 'transparent',
      border: `2px solid ${p.solid}`,
      borderRadius: 12,
      boxShadow: pressed ? 'none' : `0 0 0 0px rgba(${p.glow},0)`,
      transform: pressed ? 'scale(0.97)' : 'scale(1)'
    };

    if (btnStyle === 'glow') return {
      ...base,
      color: p.text,
      background: `linear-gradient(180deg, ${p.top} 0%, ${p.bot} 100%)`,
      border: `1.5px solid rgba(${p.glow},0.4)`,
      borderRadius: 12,
      boxShadow: pressed ?
      `0 0 8px rgba(${p.glow},0.3)` :
      `0 0 18px rgba(${p.glow},0.45), 0 0 6px rgba(${p.glow},0.3), inset 0 1px 0 rgba(255,255,255,0.3)`,
      transform: pressed ? 'translateY(1px)' : 'translateY(0)'
    };

    // default: 3d
    // slate = même forme que 3D mais fond transparent
    if (color === 'slate') return {
      ...base,
      color: p.text,
      background: 'transparent',
      border: `2px solid rgba(255,255,255,0.22)`,
      borderRadius: 14,
      boxShadow: pressed
        ? `0 1px 0 rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.08)`
        : `0 4px 0 rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.08)`,
      transform: pressed ? 'translateY(3px)' : 'translateY(0)',
    };

    // terracotta-outline = outline terracotta avec relief 3D
    if (color === 'terracotta-outline') return {
      ...base,
      color: '#DA7756',
      background: 'transparent',
      border: `2px solid #DA7756`,
      borderRadius: 14,
      boxShadow: pressed
        ? `0 1px 0 rgba(100,40,20,0.5), inset 0 1px 0 rgba(218,119,86,0.15)`
        : `0 4px 0 rgba(100,40,20,0.5), inset 0 1px 0 rgba(218,119,86,0.15)`,
      transform: pressed ? 'translateY(3px)' : 'translateY(0)',
    };

    return {
      ...base,
      color: p.text,
      background: `linear-gradient(180deg, ${p.top} 0%, ${p.bot} 100%)`,
      border: `2px solid ${p.stroke}`,
      borderRadius: 14,
      textShadow: color === 'gold' ? '0 1px 0 rgba(255,255,255,0.3)' : '0 1px 2px rgba(0,0,0,0.25)',
      boxShadow: pressed ?
      `0 1px 0 ${p.dropS}, inset 0 1px 0 rgba(255,255,255,0.35)` :
      `0 4px 0 ${p.dropS}, inset 0 1px 0 rgba(255,255,255,0.4)`,
      transform: pressed ? 'translateY(3px)' : 'translateY(0)'
    };
  };

  return (
    <button
      onClick={disabled ? undefined : onClick}
      onPointerDown={() => !disabled && setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      disabled={disabled}
      style={getStyle()}>
      {icon && <span>{icon}</span>}
      {children}
    </button>);

}

// PageTitle — sub-header below AppHeader
function PageTitle({ title, leftIcon, rightIcons, color = '#fff' }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '12px 14px 6px'
    }}>
      {leftIcon}
      <h1 style={{ flex: 1, margin: 0, fontSize: 22, fontWeight: 900, color, letterSpacing: '-0.6px', lineHeight: 1.1 }}>
        {title}
      </h1>
      {rightIcons && <div style={{ display: 'flex', gap: 8 }}>{rightIcons}</div>}
    </div>);

}

// Segmented switch (pill tabs) — supports tabStyle tweak
function SegmentedTabs({ tabs, active, onChange, accent = 'violet' }) {
  const tweaks = useRatisTweaks();
  const tabStyle = tweaks.tabStyle || 'filled';

  const ACCENTS = {
    violet: { bg: 'rgba(139,92,246,0.22)', border: 'rgba(139,92,246,0.55)', solid: '#7C3AED', text: '#C4B5FD', activeText: '#fff' },
    teal: { bg: 'rgba(218,119,86,0.20)', border: 'rgba(218,119,86,0.55)', solid: '#DA7756', text: '#E8896A', activeText: '#fff' },
    coral: { bg: 'rgba(255,107,53,0.22)', border: 'rgba(255,107,53,0.55)', solid: '#E53030', text: '#FFB89D', activeText: '#fff' }
  };
  const a = ACCENTS[accent] || ACCENTS.teal;

  // ── tab style variants ──────────────────────────────────────────────
  const getWrapStyle = () => {
    if (tabStyle === 'underline') return {
      display: 'flex', background: 'transparent',
      border: 'none', borderBottom: '2px solid rgba(255,255,255,0.08)',
      borderRadius: 0, padding: '0 0 0 0', gap: 0
    };
    if (tabStyle === 'ghost') return {
      display: 'flex', background: 'transparent',
      border: 'none', borderRadius: 14, padding: 0, gap: 4
    };
    // default: filled pill
    return {
      display: 'flex', background: 'rgba(0,0,0,0.25)',
      border: '1.5px solid rgba(255,255,255,0.06)',
      borderRadius: 14, padding: 4,
      boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.35)'
    };
  };

  const getBtnStyle = (on) => {
    if (tabStyle === 'underline') return {
      flex: 1, padding: '8px 10px 10px',
      fontSize: 12, fontWeight: 800, letterSpacing: '-0.2px',
      color: on ? '#fff' : 'rgba(255,255,255,0.45)',
      background: 'transparent', border: 'none',
      borderBottom: on ? `2px solid ${a.solid}` : '2px solid transparent',
      borderRadius: 0, cursor: 'pointer', fontFamily: 'inherit',
      transition: 'all 0.12s', marginBottom: -2
    };
    if (tabStyle === 'ghost') return {
      flex: 1, padding: '8px 10px',
      fontSize: 12, fontWeight: 800, letterSpacing: '-0.2px',
      color: on ? a.solid : 'rgba(255,255,255,0.45)',
      background: 'transparent', border: 'none',
      borderRadius: 11, cursor: 'pointer', fontFamily: 'inherit',
      transition: 'all 0.12s'
    };
    // filled
    return {
      flex: 1, padding: '8px 10px',
      fontSize: 12, fontWeight: 800, letterSpacing: '-0.2px',
      color: on ? a.activeText : 'rgba(255,255,255,0.55)',
      background: on ? a.bg : 'transparent',
      border: on ? `1.5px solid ${a.border}` : '1.5px solid transparent',
      borderRadius: 11, cursor: 'pointer', fontFamily: 'inherit',
      boxShadow: on ? 'inset 0 1px 0 rgba(255,255,255,0.12)' : 'none',
      transition: 'all 0.12s'
    };
  };

  return (
    <div style={getWrapStyle()}>
      {tabs.map((t) => {
        const on = t.id === active;
        return (
          <button key={t.id} onClick={() => onChange(t.id)} style={getBtnStyle(on)}>
            {t.label}
          </button>);

      })}
    </div>);

}

// Empty state card
function EmptyCard({ icon, title, subtitle, action }) {
  return (
    <div style={{
      ...screenCardBase,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      padding: '28px 20px',
      gap: 8
    }}>
      <div style={{
        width: 56, height: 56, borderRadius: 16,
        background: 'rgba(255,255,255,0.05)',
        border: '1.5px solid rgba(255,255,255,0.08)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        marginBottom: 4,
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08)'
      }}>
        <span style={{ fontSize: 26 }}>{icon}</span>
      </div>
      <div style={{ fontSize: 15, fontWeight: 900, color: '#fff', letterSpacing: '-0.3px', textAlign: 'center' }}>{title}</div>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.55)', textAlign: 'center', lineHeight: 1.4, maxWidth: 240 }}>{subtitle}</div>
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>);

}

window.RatisShared = { GameButton, PageTitle, SegmentedTabs, EmptyCard, SectionHead, screenCardBase, formatBalanceN, useRatisTweaks };