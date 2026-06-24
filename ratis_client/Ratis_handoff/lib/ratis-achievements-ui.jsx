// ─────────────────────────────────────────────────────────────────────
// Achievements UI — full-page modal, achievement card, unlock toast
// ─────────────────────────────────────────────────────────────────────
const { useState: useAchState, useMemo: useAchMemo, useEffect: useAchEffect } = React;

// Single achievement card — trading-card style with rarity-based metallic frame
function AchievementCard({ ach, onClick }) {
  const { RARITIES, CATEGORIES } = window.RatisAchievements;
  const r = RARITIES[ach.rarity];
  const cat = CATEGORIES[ach.category];
  const isLocked = ach.status === 'locked';
  const isInProgress = ach.status === 'in_progress';
  const isUnlocked = ach.status === 'unlocked';
  const isSecret = ach.category === 'secret' && isLocked;
  // Show progress bar on the card itself for the easier tiers (so users see the next milestone clearly)
  const lowTier = ['terracotta', 'bronze', 'copper'].includes(ach.rarity);
  const showProgress = isInProgress && lowTier;
  const pct = ach.target > 0 ? Math.min(100, ach.progress / ach.target * 100) : 0;

  return (
    <div onClick={() => onClick(ach)} style={{
      position: 'relative',
      aspectRatio: '3/4',
      borderRadius: 12,
      cursor: 'pointer',
      overflow: 'hidden',
      // Outer metallic frame (from rarity)
      background: isUnlocked ? r.metal : 'linear-gradient(135deg, #1F2937, #111827, #1F2937)',
      padding: 2,
      boxShadow: isUnlocked
        ? `0 0 12px ${r.glow}, 0 4px 10px rgba(0,0,0,0.5)`
        : '0 4px 8px rgba(0,0,0,0.4)',
      transition: 'transform 0.18s ease',
    }}
      onMouseEnter={(e) => { e.currentTarget.style.transform = 'translateY(-2px)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.transform = 'translateY(0)'; }}
    >
      {/* Inner card body */}
      <div style={{
        width: '100%', height: '100%',
        borderRadius: 10,
        background: isUnlocked
          ? `radial-gradient(ellipse at top, ${r.glow}, #1A1B26 60%)`
          : '#1A1B26',
        position: 'relative',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}>
        {/* Holographic shine sweep — only on unlocked + paliers >= émeraude */}
        {isUnlocked && r.holo && (
          <div style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.18) 45%, rgba(255,255,255,0.35) 50%, rgba(255,255,255,0.18) 55%, transparent 70%)',
            backgroundSize: '300% 100%',
            animation: 'achHoloShine 4.5s ease-in-out infinite',
            pointerEvents: 'none',
            mixBlendMode: 'screen',
          }} />
        )}

        {/* Scanlines for arcade vibe */}
        <div style={{
          position: 'absolute', inset: 0,
          background: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.18) 2px, rgba(0,0,0,0.18) 3px)',
          pointerEvents: 'none', opacity: 0.4,
        }} />

        {/* Rarity ribbon at top */}
        <div style={{
          padding: '4px 6px',
          background: isUnlocked ? `linear-gradient(90deg, ${r.color}, transparent)` : 'rgba(255,255,255,0.05)',
          borderBottom: `1px solid ${isUnlocked ? r.color : 'rgba(255,255,255,0.08)'}`,
          fontSize: 7,
          fontWeight: 900,
          color: isUnlocked ? '#fff' : 'rgba(255,255,255,0.4)',
          letterSpacing: '0.6px',
          textTransform: 'uppercase',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          textShadow: isUnlocked ? '0 1px 2px rgba(0,0,0,0.5)' : 'none',
        }}>
          <span>{r.label}</span>
          <span style={{ opacity: 0.7, fontSize: 8 }}>{cat.icon}</span>
        </div>

        {/* Icon area */}
        <div style={{
          flex: 1,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          position: 'relative',
          padding: '8px 4px',
        }}>
          <div style={{
            fontSize: 36,
            filter: isLocked ? 'grayscale(1) brightness(0.4)' : 'none',
            opacity: isLocked ? 0.45 : 1,
            textShadow: isUnlocked ? `0 0 14px ${r.glow}` : 'none',
            position: 'relative',
            zIndex: 1,
          }}>
            {isSecret ? '🔒' : ach.icon}
          </div>
        </div>

        {/* Title */}
        <div style={{
          padding: '4px 6px 6px',
          textAlign: 'center',
          background: isUnlocked ? 'rgba(0,0,0,0.45)' : 'rgba(0,0,0,0.3)',
          borderTop: `1px solid ${isUnlocked ? `${r.color}50` : 'rgba(255,255,255,0.06)'}`,
        }}>
          <div style={{
            fontSize: 9,
            fontWeight: 900,
            color: isUnlocked ? '#fff' : isLocked ? 'rgba(255,255,255,0.45)' : 'rgba(255,255,255,0.85)',
            letterSpacing: '0.2px',
            lineHeight: 1.15,
            textShadow: isUnlocked ? `0 0 6px ${r.glow}` : 'none',
            minHeight: 22,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {isSecret ? '???' : ach.label}
          </div>
          {showProgress && (
            <div style={{ marginTop: 4 }}>
              <div style={{ height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.1)', overflow: 'hidden' }}>
                <div style={{ width: `${pct}%`, height: '100%', background: r.color, borderRadius: 2, boxShadow: `0 0 4px ${r.glow}` }} />
              </div>
              <div style={{ fontSize: 7, color: r.color, fontWeight: 800, marginTop: 2, letterSpacing: '0.3px' }}>
                {Math.floor(ach.progress)}/{ach.target}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Detail modal when you tap a card
function AchievementDetailModal({ ach, onClose }) {
  if (!ach) return null;
  const { RARITIES, CATEGORIES } = window.RatisAchievements;
  const r = RARITIES[ach.rarity];
  const cat = CATEGORIES[ach.category];
  const isUnlocked = ach.status === 'unlocked';
  const isSecret = ach.category === 'secret' && ach.status === 'locked';
  const pct = ach.target > 0 ? Math.min(100, ach.progress / ach.target * 100) : 0;

  return (
    <div onClick={onClose} style={{
      position: 'absolute', inset: 0, zIndex: 30,
      background: 'rgba(5, 8, 14, 0.85)', backdropFilter: 'blur(8px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 20, animation: 'fadeIn 0.2s ease',
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 280,
        background: isUnlocked ? r.metal : 'linear-gradient(135deg, #1F2937, #111827, #1F2937)',
        padding: 3, borderRadius: 16,
        boxShadow: isUnlocked ? `0 0 30px ${r.glow}` : '0 12px 30px rgba(0,0,0,0.6)',
      }}>
        <div style={{
          background: isUnlocked
            ? `radial-gradient(ellipse at top, ${r.glow}, #1A1B26 70%)`
            : '#1A1B26',
          borderRadius: 14, padding: 20,
          textAlign: 'center', position: 'relative', overflow: 'hidden',
        }}>
          {isUnlocked && r.holo && (
            <div style={{
              position: 'absolute', inset: 0,
              background: 'linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.15) 50%, transparent 70%)',
              backgroundSize: '300% 100%',
              animation: 'achHoloShine 3.5s ease-in-out infinite',
              pointerEvents: 'none', mixBlendMode: 'screen',
            }} />
          )}
          <div style={{
            position: 'relative', zIndex: 1,
          }}>
            <div style={{
              fontSize: 11, fontWeight: 900,
              color: isUnlocked ? r.color : 'rgba(255,255,255,0.5)',
              letterSpacing: '1px', textTransform: 'uppercase',
              marginBottom: 6,
            }}>{r.label} · {cat.label}</div>
            <div style={{
              fontSize: 64, marginBottom: 6,
              filter: ach.status === 'locked' ? 'grayscale(1) brightness(0.5)' : 'none',
              textShadow: isUnlocked ? `0 0 24px ${r.glow}` : 'none',
            }}>{isSecret ? '🔒' : ach.icon}</div>
            <div style={{ fontSize: 18, fontWeight: 900, color: '#fff', marginBottom: 8, letterSpacing: '-0.3px' }}>
              {isSecret ? '???' : ach.label}
            </div>
            <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.7)', lineHeight: 1.4, marginBottom: 16 }}>
              {isSecret ? 'Continue à explorer pour le découvrir…' : ach.description}
            </div>
            {ach.status === 'in_progress' && (
              <div>
                <div style={{ height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.1)', overflow: 'hidden' }}>
                  <div style={{ width: `${pct}%`, height: '100%', background: r.color, boxShadow: `0 0 8px ${r.glow}` }} />
                </div>
                <div style={{ fontSize: 11, fontWeight: 800, color: r.color, marginTop: 6 }}>
                  {Math.floor(ach.progress)} / {ach.target}
                </div>
              </div>
            )}
            {isUnlocked && (
              <div style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '6px 12px', borderRadius: 8,
                background: 'rgba(0,0,0,0.4)', border: `1px solid ${r.color}`,
                fontSize: 10, fontWeight: 900, color: r.color, letterSpacing: '0.5px', textTransform: 'uppercase',
              }}>
                ✓ Débloqué
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Full-page Achievements modal
function AchievementsModal({ open, onClose }) {
  const [filter, setFilter] = useAchState('all'); // all / unlocked / in_progress / locked
  const [category, setCategory] = useAchState('all');
  const [selected, setSelected] = useAchState(null);

  const { ACHIEVEMENTS, CATEGORIES, RARITIES } = window.RatisAchievements;

  const filtered = useAchMemo(() => {
    return ACHIEVEMENTS.filter((a) => {
      if (filter === 'unlocked' && a.status !== 'unlocked') return false;
      if (filter === 'in_progress' && a.status !== 'in_progress') return false;
      if (filter === 'locked' && a.status !== 'locked') return false;
      if (category !== 'all' && a.category !== category) return false;
      return true;
    });
  }, [filter, category]);

  const stats = useAchMemo(() => {
    const total = ACHIEVEMENTS.length;
    const unlocked = ACHIEVEMENTS.filter((a) => a.status === 'unlocked').length;
    const inProgress = ACHIEVEMENTS.filter((a) => a.status === 'in_progress').length;
    return { total, unlocked, inProgress };
  }, []);

  if (!open) return null;

  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 20,
      background: 'linear-gradient(180deg, #0a0d14 0%, #1a242c 100%)',
      display: 'flex', flexDirection: 'column',
      animation: 'slideUp 0.3s ease',
    }}>
      {/* Header */}
      <div style={{
        padding: '14px 16px 12px',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        background: 'linear-gradient(180deg, rgba(192,132,252,0.10), transparent)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(192,132,252,0.85)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Collection</div>
            <h2 style={{ margin: 0, fontSize: 22, fontWeight: 900, color: '#fff', letterSpacing: '-0.5px', marginTop: 2 }}>Succès</h2>
          </div>
          <button onClick={onClose} style={{
            width: 30, height: 30, borderRadius: 15,
            border: '1px solid rgba(255,255,255,0.15)',
            background: 'rgba(255,255,255,0.06)', color: '#fff',
            fontSize: 14, cursor: 'pointer',
          }}>✕</button>
        </div>

        {/* Stats bar */}
        <div style={{
          display: 'flex', gap: 6, marginBottom: 12,
        }}>
          <StatPill label="Débloqués" value={`${stats.unlocked}/${stats.total}`} color="#34D399" />
          <StatPill label="En cours" value={stats.inProgress} color="#60A5FA" />
          <StatPill label="Score" value={Math.round(stats.unlocked / stats.total * 100) + '%'} color="#FBBF24" />
        </div>

        {/* Status filters */}
        <div style={{ display: 'flex', gap: 0, marginBottom: 8, borderBottom: '2px solid rgba(255,255,255,0.08)' }}>
          {[['all', 'Tous'], ['unlocked', 'Débloqués'], ['in_progress', 'En cours'], ['locked', 'À faire']].map(([k, lbl]) => (
            <button key={k} onClick={() => setFilter(k)} style={{
              flex: 1, padding: '6px 4px 8px',
              borderRadius: 0, border: 'none',
              borderBottom: filter === k ? '2px solid #DA7756' : '2px solid transparent',
              background: 'transparent',
              color: filter === k ? '#fff' : 'rgba(255,255,255,0.45)',
              fontSize: 10, fontWeight: 900, letterSpacing: '0.3px',
              textTransform: 'uppercase', cursor: 'pointer',
              fontFamily: 'inherit', marginBottom: -2,
              transition: 'all 0.12s',
            }}>{lbl}</button>
          ))}
        </div>

        {/* Category filters */}
        <div style={{ display: 'flex', gap: 5, overflowX: 'auto', paddingBottom: 2 }}>
          <CategoryChip active={category === 'all'} icon="✨" label="Toutes" color="#fff" onClick={() => setCategory('all')} />
          {Object.entries(CATEGORIES).map(([k, c]) => (
            <CategoryChip key={k} active={category === k} icon={c.icon} label={c.label} color={c.color} onClick={() => setCategory(k)} />
          ))}
        </div>
      </div>

      {/* Grid */}
      <div style={{
        flex: 1, overflowY: 'auto', padding: 12,
      }}>
        {filtered.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: 'rgba(255,255,255,0.4)', fontSize: 13 }}>
            Aucun succès dans cette section pour l'instant.
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 8,
          }}>
            {filtered.map((a) => <AchievementCard key={a.id} ach={a} onClick={setSelected} />)}
          </div>
        )}
      </div>

      <AchievementDetailModal ach={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function StatPill({ label, value, color }) {
  return (
    <div style={{
      flex: 1, padding: '8px 10px',
      borderRadius: 10,
      background: 'rgba(255,255,255,0.04)',
      border: '1px solid rgba(255,255,255,0.06)',
    }}>
      <div style={{ fontSize: 8, fontWeight: 800, color: 'rgba(255,255,255,0.45)', letterSpacing: '0.5px', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 900, color, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function CategoryChip({ active, icon, label, color, onClick }) {
  return (
    <button onClick={onClick} style={{
      flex: '0 0 auto',
      display: 'flex', alignItems: 'center', gap: 4,
      padding: '5px 10px',
      borderRadius: 999,
      border: `1px solid ${active ? color : 'rgba(255,255,255,0.08)'}`,
      background: active ? `${color}25` : 'rgba(255,255,255,0.04)',
      color: active ? color : 'rgba(255,255,255,0.6)',
      fontSize: 10, fontWeight: 800, letterSpacing: '0.3px',
      cursor: 'pointer', whiteSpace: 'nowrap',
    }}>
      <span>{icon}</span>
      <span>{label}</span>
    </button>
  );
}

// Compact "next achievement" preview card for the home dashboard
function NextAchievementCard({ onPress }) {
  const { ACHIEVEMENTS, RARITIES } = window.RatisAchievements;
  // pick the in_progress achievement closest to completion
  const next = useAchMemo(() => {
    const candidates = ACHIEVEMENTS.filter((a) => a.status === 'in_progress');
    return candidates.sort((a, b) => (b.progress / b.target) - (a.progress / a.target))[0];
  }, []);
  if (!next) return null;
  const r = RARITIES[next.rarity];
  const pct = Math.min(100, next.progress / next.target * 100);

  return (
    <div onClick={onPress} style={{
      position: 'relative',
      borderRadius: 16,
      padding: 14,
      cursor: 'pointer',
      overflow: 'hidden',
      background: `linear-gradient(135deg, ${r.glow}, rgba(26,27,38,0.8))`,
      border: `1.5px solid ${r.color}80`,
      boxShadow: `0 4px 14px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08)`,
    }}>
      {/* Holo sweep — paliers >= émeraude uniquement */}
      {r.holo && (
        <div style={{
          position: 'absolute', inset: 0,
          background: 'linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.15) 50%, transparent 70%)',
          backgroundSize: '300% 100%',
          animation: 'achHoloShine 5s ease-in-out infinite',
          pointerEvents: 'none', mixBlendMode: 'screen',
        }} />
      )}      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{
          width: 56, height: 56, borderRadius: 12,
          background: r.metal, padding: 2,
          flexShrink: 0,
        }}>
          <div style={{
            width: '100%', height: '100%', borderRadius: 10,
            background: '#1A1B26',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 28,
            textShadow: `0 0 12px ${r.glow}`,
          }}>{next.icon}</div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 9, fontWeight: 800, color: r.color, letterSpacing: '0.6px', textTransform: 'uppercase', marginBottom: 2 }}>
            Prochain succès · {r.label}
          </div>
          <div style={{ fontSize: 14, fontWeight: 900, color: '#fff', letterSpacing: '-0.2px', marginBottom: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {next.label}
          </div>
          <div style={{ height: 5, borderRadius: 3, background: 'rgba(0,0,0,0.4)', overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: r.color, boxShadow: `0 0 6px ${r.glow}` }} />
          </div>
          <div style={{ fontSize: 10, fontWeight: 800, color: r.color, marginTop: 4 }}>
            {Math.floor(next.progress)} / {next.target}
          </div>
        </div>
      </div>
    </div>
  );
}

// Trophy button for the header
function TrophyButton({ onClick }) {
  const { ACHIEVEMENTS } = window.RatisAchievements;
  const unlocked = ACHIEVEMENTS.filter((a) => a.status === 'unlocked').length;
  return (
    <button onClick={onClick} style={{
      position: 'relative',
      width: 36, height: 36, borderRadius: 10,
      border: '1px solid rgba(192,132,252,0.4)',
      background: 'rgba(192,132,252,0.12)',
      cursor: 'pointer',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 16,
    }}>
      🏆
      {unlocked > 0 && (
        <div style={{
          position: 'absolute', top: -4, right: -4,
          minWidth: 16, height: 16, padding: '0 4px',
          borderRadius: 8,
          background: '#C084FC',
          border: '2px solid #1A1B26',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 9, fontWeight: 900, color: '#1A1B26',
        }}>{unlocked}</div>
      )}
    </button>
  );
}

// Full-screen unlock animation (PS/Xbox trophy style)
function AchievementUnlockToast({ ach, onDismiss }) {
  useAchEffect(() => {
    if (!ach) return;
    const t = setTimeout(onDismiss, 4500);
    return () => clearTimeout(t);
  }, [ach]);
  if (!ach) return null;
  const { RARITIES } = window.RatisAchievements;
  const r = RARITIES[ach.rarity];

  return (
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0,
      zIndex: 50,
      pointerEvents: 'none',
      animation: 'achUnlockSlideIn 0.5s cubic-bezier(0.34, 1.4, 0.64, 1) forwards, achUnlockSlideOut 0.4s ease 4s forwards',
    }}>
      <div style={{
        margin: '12px 16px',
        background: r.metal, padding: 2, borderRadius: 14,
        boxShadow: `0 0 40px ${r.glow}, 0 8px 20px rgba(0,0,0,0.6)`,
      }}>
        <div style={{
          background: `radial-gradient(ellipse at top, ${r.glow}, #1A1B26 80%)`,
          borderRadius: 12, padding: 12,
          display: 'flex', alignItems: 'center', gap: 12,
          position: 'relative', overflow: 'hidden',
        }}>
          {/* Holo sweep */}
          <div style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.3) 50%, transparent 70%)',
            backgroundSize: '300% 100%',
            animation: 'achHoloShine 1.5s ease-in-out 0.4s 2',
            pointerEvents: 'none', mixBlendMode: 'screen',
          }} />
          {/* Burst rays */}
          <div style={{
            position: 'absolute', left: 24, top: '50%',
            transform: 'translateY(-50%)',
            width: 80, height: 80,
            background: `conic-gradient(from 0deg, transparent 0deg, ${r.color}30 10deg, transparent 20deg, transparent 80deg, ${r.color}30 90deg, transparent 100deg, transparent 170deg, ${r.color}30 180deg, transparent 190deg, transparent 260deg, ${r.color}30 270deg, transparent 280deg)`,
            borderRadius: '50%',
            animation: 'achBurstSpin 8s linear infinite',
            opacity: 0.7,
            pointerEvents: 'none',
          }} />
          <div style={{
            position: 'relative',
            width: 56, height: 56, flexShrink: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <div style={{
              fontSize: 40,
              textShadow: `0 0 20px ${r.glow}`,
              animation: 'achIconPop 0.6s cubic-bezier(0.34, 1.6, 0.64, 1) 0.2s both',
            }}>{ach.icon}</div>
          </div>
          <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
            <div style={{ fontSize: 9, fontWeight: 900, color: r.color, letterSpacing: '1.2px', textTransform: 'uppercase', marginBottom: 2 }}>
              ★ Succès débloqué · {r.label}
            </div>
            <div style={{ fontSize: 16, fontWeight: 900, color: '#fff', letterSpacing: '-0.3px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {ach.label}
            </div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.7)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {ach.description}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.RatisAchievementsUI = {
  AchievementsModal, NextAchievementCard, TrophyButton, AchievementUnlockToast, AchievementCard,
};
