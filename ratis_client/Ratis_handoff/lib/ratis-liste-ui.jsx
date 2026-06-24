// Ratis — Liste tab UI components: AddBar, ItemRow, CheckBurst, sheets

const { useState: useStateLU, useRef: useRefLU, useEffect: useEffectLU, useMemo: useMemoLU } = React;
const { LIST_CATEGORIES: CATSLU, SUGGESTIONS: SUGGSLU, LIST_TEMPLATES: TEMPSLU, AUTOCOMPLETE_POOL: POOLLU } = window.RatisListeData;

// ─── CheckBurst — small particles burst when an item is checked ────
function CheckBurst({ trigger, color = '#5EE5C2' }) {
  if (!trigger) return null;
  const particles = Array.from({ length: 8 }, (_, i) => ({
    id: i + '_' + trigger,
    angle: (i / 8) * Math.PI * 2,
    delay: i * 12,
  }));
  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'visible' }}>
      {particles.map((p) => (
        <div key={p.id} style={{
          position: 'absolute',
          left: 12, top: '50%',
          width: 4, height: 4, borderRadius: 2,
          background: color,
          boxShadow: `0 0 8px ${color}`,
          animation: `listBurst 0.55s cubic-bezier(0.2,0.7,0.4,1) forwards`,
          animationDelay: p.delay + 'ms',
          // @ts-ignore
          '--bx': Math.cos(p.angle) * 22 + 'px',
          '--by': Math.sin(p.angle) * 22 + 'px',
          transform: 'translate(-50%, -50%)',
        }} />
      ))}
      <style>{`
        @keyframes listBurst {
          0%   { transform: translate(-50%,-50%) scale(1); opacity: 1; }
          100% { transform: translate(calc(-50% + var(--bx)), calc(-50% + var(--by))) scale(0); opacity: 0; }
        }
      `}</style>
    </div>
  );
}

// ─── ItemRow ─────────────────────────────────────────────────────────
function ItemRowLU({ item, onToggle, onQty, onRemove, last }) {
  const [burstKey, setBurstKey] = useStateLU(0);
  const cat = CATSLU[item.cat] || CATSLU.other;

  const hexToRgb = (hex) => {
    const r = parseInt(hex.slice(1,3),16);
    const g = parseInt(hex.slice(3,5),16);
    const b = parseInt(hex.slice(5,7),16);
    return `${r},${g},${b}`;
  };
  const catRgb = hexToRgb(cat.color);

  const handleToggle = () => {
    if (!item.checked) setBurstKey(k => k + 1);
    onToggle();
  };

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '11px 14px',
      background: item.checked
        ? `rgba(${catRgb},0.06)`
        : `rgba(${catRgb},0.14)`,
      borderBottom: last ? 'none' : '1px solid rgba(255,255,255,0.05)',
      borderLeft: `3px solid rgba(${catRgb},${item.checked ? '0.2' : '0.7'})`,
      transition: 'background 0.15s',
      position: 'relative', zIndex: 1,
    }}>

      {/* Checkbox */}
      <button onClick={handleToggle} style={{
        flexShrink: 0, position: 'relative',
        width: 24, height: 24, borderRadius: 8,
        background: item.checked
          ? `linear-gradient(180deg, ${cat.color}, rgba(${catRgb},0.6))`
          : 'rgba(0,0,0,0.2)',
        border: '1.5px solid ' + (item.checked ? `rgba(${catRgb},0.5)` : 'rgba(255,255,255,0.15)'),
        boxShadow: item.checked ? `0 1px 0 rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.3)` : 'none',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer', padding: 0,
      }}>
        {item.checked && (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="rgba(0,0,0,0.65)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12l5 5L20 7"/>
          </svg>
        )}
        <CheckBurst trigger={burstKey} color={cat.color} />
      </button>

      {/* Cat icon */}
      <div style={{
        flexShrink: 0, width: 26, height: 26, borderRadius: 7,
        background: `rgba(${catRgb},0.22)`,
        border: `1px solid rgba(${catRgb},0.45)`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 13, opacity: item.checked ? 0.35 : 1,
      }}>{cat.icon}</div>

      {/* Body */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {item.brand && (
          <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.6px', textTransform: 'uppercase', marginBottom: 1 }}>
            {item.brand}
          </div>
        )}
        <div style={{
          fontSize: 13, fontWeight: 800,
          color: item.checked ? 'rgba(255,255,255,0.4)' : '#fff',
          textDecoration: item.checked ? 'line-through' : 'none',
          letterSpacing: '-0.2px',
          textOverflow: 'ellipsis', whiteSpace: 'nowrap', overflow: 'hidden',
        }}>{item.name}</div>
      </div>

      {/* Qty stepper */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 0,
        background: 'rgba(0,0,0,0.18)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: 7, padding: 2,
        opacity: item.checked ? 0.4 : 1,
      }}>
        <button onClick={() => onQty(Math.max(1, item.qty - 1))} style={qtyBtnStyle}>−</button>
        <div style={{ minWidth: 16, textAlign: 'center', fontSize: 11, fontWeight: 900, color: '#fff' }}>{item.qty}</div>
        <button onClick={() => onQty(item.qty + 1)} style={qtyBtnStyle}>＋</button>
      </div>

      {/* Price */}
      <div style={{ minWidth: 46, textAlign: 'right' }}>
        <div style={{ fontSize: 13, fontWeight: 900, color: item.checked ? 'rgba(255,255,255,0.35)' : '#FFB800', letterSpacing: '-0.3px' }}>
          {(item.est * item.qty).toFixed(2).replace('.',',')}€
        </div>
        {item.qty > 1 && !item.checked && (
          <div style={{ fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.4)', marginTop: 1 }}>
            ×{item.qty}
          </div>
        )}
      </div>
    </div>
  );
}

const qtyBtnStyle = {
  width: 22, height: 22, borderRadius: 6,
  border: 'none', background: 'transparent',
  color: '#fff', fontSize: 12, fontWeight: 900,
  cursor: 'pointer', padding: 0, fontFamily: 'inherit',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

// ─── AddBar with autocomplete ────────────────────────────────────────
function AddBar({ onAdd, onVoice, onTemplates, onSuggestions }) {
  const [query, setQuery] = useStateLU('');
  const [focused, setFocused] = useStateLU(false);
  const inputRef = useRefLU(null);

  const matches = useMemoLU(() => {
    if (!query.trim()) return [];
    const q = query.toLowerCase();
    return POOLLU.filter(n => n.toLowerCase().includes(q)).slice(0, 5);
  }, [query]);

  const submit = (name) => {
    const n = (name || query).trim();
    if (!n) return;
    onAdd(n);
    setQuery('');
    inputRef.current?.blur();
  };

  return (
    <div style={{ position: 'relative' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: 6,
        background: 'rgba(255,255,255,0.04)',
        border: '1.5px solid ' + (focused ? 'rgba(218,119,86,0.55)' : 'rgba(255,255,255,0.08)'),
        borderRadius: 14,
        boxShadow: focused
          ? '0 0 0 3px rgba(218,119,86,0.12), inset 0 1px 0 rgba(255,255,255,0.05)'
          : 'inset 0 1px 0 rgba(255,255,255,0.05)',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
          placeholder="Ajouter un produit…"
          style={{
            flex: 1, minWidth: 0,
            background: 'transparent', border: 'none', outline: 'none',
            color: '#fff', fontSize: 13, fontWeight: 700,
            padding: '6px 8px',
            fontFamily: 'inherit',
          }}
        />
        <button onClick={onSuggestions} title="Suggestions" style={addBarIconBtnStyle}>💡</button>
        <button onClick={onTemplates} title="Templates" style={addBarIconBtnStyle}>✨</button>
        <button onClick={onVoice} title="Voix" style={addBarIconBtnStyle}>🎤</button>
        <button onClick={() => submit()} disabled={!query.trim()} style={{
          ...addBarIconBtnStyle,
          background: query.trim() ? 'linear-gradient(180deg, #E8896A, #DA7756)' : 'rgba(255,255,255,0.04)',
          borderColor: query.trim() ? '#A8562E' : 'rgba(255,255,255,0.08)',
          color: query.trim() ? '#fff' : 'rgba(255,255,255,0.3)',
          fontWeight: 900,
          opacity: query.trim() ? 1 : 0.5,
        }}>＋</button>
      </div>

      {/* Autocomplete dropdown */}
      {focused && matches.length > 0 && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0,
          marginTop: 4, zIndex: 30,
          background: '#1A1B26',
          border: '1.5px solid rgba(218,119,86,0.4)',
          borderRadius: 12,
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
          overflow: 'hidden',
        }}>
          {matches.map((m, i) => (
            <button key={i} onMouseDown={(e) => { e.preventDefault(); submit(m); }} style={{
              display: 'block', width: '100%', textAlign: 'left',
              padding: '10px 14px',
              border: 'none',
              background: 'transparent',
              color: '#fff', fontSize: 12, fontWeight: 700,
              cursor: 'pointer',
              borderBottom: i < matches.length - 1 ? '1px solid rgba(255,255,255,0.06)' : 'none',
              fontFamily: 'inherit',
            }}>
              <span style={{ color: '#DA7756', fontSize: 11, marginRight: 6 }}>↵</span>
              {m}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const addBarIconBtnStyle = {
  width: 36, height: 36, borderRadius: 10,
  border: '1px solid rgba(255,255,255,0.08)',
  background: 'rgba(255,255,255,0.04)',
  color: '#fff', fontSize: 13,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  cursor: 'pointer', padding: 0, fontFamily: 'inherit',
  flexShrink: 0,
};

// ─── BottomSheet ──────────────────────────────────────────────────────
function BottomSheet({ open, onClose, title, subtitle, children }) {
  if (!open) return null;
  return (
    <div onClick={onClose} style={{
      position: 'absolute', inset: 0, zIndex: 40,
      background: 'rgba(0,0,0,0.5)',
      animation: 'fadeIn 0.15s ease',
      display: 'flex', alignItems: 'flex-end',
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: '100%',
        maxHeight: '75%',
        background: 'linear-gradient(180deg, #1F2030 0%, #15161E 100%)',
        borderRadius: '20px 20px 0 0',
        border: '1.5px solid rgba(255,255,255,0.08)',
        borderBottom: 'none',
        animation: 'slideUp 0.25s cubic-bezier(0.2,0.7,0.4,1)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Drag handle */}
        <div style={{ display: 'flex', justifyContent: 'center', padding: '10px 0 4px' }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.2)' }} />
        </div>
        {/* Header */}
        <div style={{ padding: '8px 16px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            {subtitle && <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(167,139,250,0.85)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>{subtitle}</div>}
            <h3 style={{ margin: 0, fontSize: 18, fontWeight: 900, color: '#fff', letterSpacing: '-0.5px', marginTop: subtitle ? 2 : 0 }}>{title}</h3>
          </div>
          <button onClick={onClose} style={{
            width: 28, height: 28, borderRadius: 14,
            border: '1px solid rgba(255,255,255,0.15)',
            background: 'rgba(255,255,255,0.06)', color: '#fff',
            fontSize: 12, cursor: 'pointer',
          }}>✕</button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px 20px' }}>
          {children}
        </div>
      </div>
    </div>
  );
}

// ─── TemplatesSheet ───────────────────────────────────────────────────
function TemplatesSheet({ open, onClose, onApply }) {
  return (
    <BottomSheet open={open} onClose={onClose} title="Listes type" subtitle="Démarre vite">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {TEMPSLU.map((t) => {
          const total = t.items.reduce((s, i) => s + i.est, 0);
          return (
            <button key={t.id} onClick={() => { onApply(t); onClose(); }} style={{
              textAlign: 'left',
              padding: 12,
              background: 'rgba(255,255,255,0.03)',
              border: '1.5px solid rgba(255,255,255,0.08)',
              borderRadius: 14,
              cursor: 'pointer',
              fontFamily: 'inherit',
              display: 'flex', alignItems: 'center', gap: 12,
            }}>
              <div style={{
                width: 44, height: 44, borderRadius: 12,
                background: `linear-gradient(180deg, ${t.color}, ${t.color}aa)`,
                border: '2px solid rgba(0,0,0,0.4)',
                boxShadow: '0 2px 0 rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.4)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 22, flexShrink: 0,
              }}>{t.icon}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 900, color: '#fff', letterSpacing: '-0.3px' }}>{t.label}</div>
                <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>
                  {t.items.length} articles · ~{total.toFixed(2).replace('.',',')}€
                </div>
              </div>
              <div style={{
                width: 28, height: 28, borderRadius: 8,
                background: 'rgba(167,139,250,0.18)',
                border: '1px solid rgba(167,139,250,0.5)',
                color: '#C4B5FD', fontSize: 14, fontWeight: 900,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>＋</div>
            </button>
          );
        })}
      </div>
    </BottomSheet>
  );
}

// ─── SuggestionsSheet ─────────────────────────────────────────────────
function SuggestionsSheet({ open, onClose, onAdd }) {
  return (
    <BottomSheet open={open} onClose={onClose} title="Tu rachètes souvent…" subtitle="Suggestions">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {SUGGSLU.map((s, i) => {
          const cat = CATSLU[s.cat] || CATSLU.other;
          return (
            <button key={i} onClick={() => { onAdd(s); }} style={{
              textAlign: 'left',
              padding: '10px 12px',
              background: 'rgba(255,255,255,0.03)',
              border: '1.5px solid rgba(255,255,255,0.08)',
              borderRadius: 12,
              cursor: 'pointer',
              fontFamily: 'inherit',
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <div style={{
                width: 32, height: 32, borderRadius: 9,
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 16, flexShrink: 0,
              }}>{cat.icon}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                {s.brand && <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.6px', textTransform: 'uppercase' }}>{s.brand}</div>}
                <div style={{ fontSize: 13, fontWeight: 800, color: '#fff', letterSpacing: '-0.2px' }}>{s.name}</div>
                <div style={{ fontSize: 10, fontWeight: 700, color: '#A78BFA', marginTop: 2 }}>
                  {s.freq} · {s.lastBuy}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 12, fontWeight: 900, color: '#FFB800' }}>{s.est.toFixed(2).replace('.',',')}€</div>
                <div style={{
                  marginTop: 4,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 24, height: 24, borderRadius: 7,
                  background: 'rgba(167,139,250,0.18)',
                  border: '1px solid rgba(167,139,250,0.5)',
                  color: '#C4B5FD', fontSize: 13, fontWeight: 900,
                }}>＋</div>
              </div>
            </button>
          );
        })}
      </div>
    </BottomSheet>
  );
}

// ─── VoiceSheet (mock) ────────────────────────────────────────────────
function VoiceSheet({ open, onClose, onTranscript }) {
  const [listening, setListening] = useStateLU(false);
  const [phrase, setPhrase] = useStateLU('');

  useEffectLU(() => {
    if (!open) return;
    setListening(true);
    setPhrase('');
    const phrases = ['Du lait', 'Du lait, des œufs', 'Du lait, des œufs, du pain', 'Du lait, des œufs, du pain et des bananes'];
    const timeouts = phrases.map((p, i) => setTimeout(() => setPhrase(p), 600 + i * 700));
    const stopT = setTimeout(() => { setListening(false); }, 600 + phrases.length * 700);
    return () => { timeouts.forEach(clearTimeout); clearTimeout(stopT); };
  }, [open]);

  return (
    <BottomSheet open={open} onClose={onClose} title="Dicte ta liste" subtitle="Mode vocal">
      <div style={{ padding: '20px 0', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
        <div style={{
          width: 100, height: 100, borderRadius: 50,
          background: listening
            ? 'radial-gradient(circle, #C4B5FD 0%, #8B6BE9 70%, #5B40A5 100%)'
            : 'linear-gradient(180deg, rgba(167,139,250,0.3), rgba(91,64,165,0.3))',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 44,
          boxShadow: listening ? '0 0 0 8px rgba(167,139,250,0.2), 0 0 0 16px rgba(167,139,250,0.1)' : 'none',
          animation: listening ? 'voicePulse 1.2s ease-in-out infinite' : 'none',
        }}>🎤</div>
        <div style={{ minHeight: 30, textAlign: 'center' }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: '#fff', letterSpacing: '-0.2px' }}>
            {phrase || (listening ? 'J\'écoute…' : 'Démo terminée')}
          </div>
        </div>
        <button onClick={() => {
          if (phrase) {
            const items = phrase.split(/,| et /).map(s => s.trim()).filter(Boolean).map(s => s.replace(/^(du |de la |des |le |la |les |un |une )/i, ''));
            items.forEach(name => onTranscript({ name: name.charAt(0).toUpperCase() + name.slice(1), brand: '', est: 1 + Math.random() * 3, cat: 'other' }));
          }
          onClose();
        }} style={{
          padding: '10px 24px',
          borderRadius: 12,
          border: '1.5px solid #5B40A5',
          background: 'linear-gradient(180deg, #B49DFC, #8B6BE9)',
          color: '#fff', fontSize: 12, fontWeight: 900, letterSpacing: '0.3px',
          textTransform: 'uppercase', cursor: 'pointer', fontFamily: 'inherit',
          opacity: phrase ? 1 : 0.5,
        }}>Ajouter à la liste</button>
      </div>
      <style>{`
        @keyframes voicePulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.06); }
        }
      `}</style>
    </BottomSheet>
  );
}

window.RatisListeUI = { ItemRowLU, AddBar, TemplatesSheet, SuggestionsSheet, VoiceSheet };
