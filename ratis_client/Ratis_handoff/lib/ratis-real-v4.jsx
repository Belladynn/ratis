// Ratis Dashboard — ported from Belladynn/ratis@main
// Source: ratis_client/app/(tabs)/index.tsx + components/dashboard/* + components/ui/*
// React Native styles → inline CSS. Pixel-faithful where possible.

const { useState, useEffect, useRef, useMemo } = React;

// ─────────────────────────────────────────────────────────────────────
// theme.ts → DarkTheme
// ─────────────────────────────────────────────────────────────────────
const DT = {
  bg: { base: '#1a2428', card: 'rgba(255,255,255,0.03)', cardBorder: 'rgba(255,255,255,0.08)' },
  text: { primary: '#fff', secondary: 'rgba(255,255,255,0.45)', muted: 'rgba(255,255,255,0.3)' },
  divider: 'rgba(255,255,255,0.08)',
  ring: { 1: '#22D3EE', 2: '#2DD4BF', 3: '#34D399', 4: '#A3E635', 5: '#FACC15', 6: '#FBBF24', 7: '#F97316', 8: '#EF4444', 9: '#EC4899', 10: '#A855F7' },
  orange: '#FF6B35',
  gold: '#FFB800'
};

// ─────────────────────────────────────────────────────────────────────
// ScreenBackground — image + tint + fog + glows
// (port of components/ui/screen-background.tsx)
// We don't have bg-ratis.jpg, so we compose a similar "industrial dark
// teal" base with subtle texture using SVG.
// ─────────────────────────────────────────────────────────────────────
function ScreenBackground() {
  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
      background: '#1c2730' }} />);

}

// ─────────────────────────────────────────────────────────────────────
// Cabecoin — gold coin SVG (port of components/ui/cabecoin.tsx)
// ─────────────────────────────────────────────────────────────────────
function Cabecoin({ size = 'md' }) {
  const SIZES = { sm: { px: 14, fs: 8 }, md: { px: 18, fs: 10 }, lg: { px: 26, fs: 14 } };
  const { px, fs } = SIZES[size];
  return (
    <div style={{ width: px, height: px, position: 'relative', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
      <svg width={px} height={px} style={{ position: 'absolute', inset: 0 }}>
        <defs>
          <radialGradient id={`coinGrad-${size}`} cx="35%" cy="30%" r="75%">
            <stop offset="0" stopColor="#FFF3D0" />
            <stop offset="0.2" stopColor="#FFE6A3" />
            <stop offset="0.6" stopColor="#D4A947" />
            <stop offset="1" stopColor="#8B6B24" />
          </radialGradient>
        </defs>
        <circle cx={px / 2} cy={px / 2} r={(px - 2) / 2} fill={`url(#coinGrad-${size})`} stroke="#6b5020" strokeWidth="1" />
      </svg>
      <span style={{ fontSize: fs, fontWeight: 900, color: 'rgba(74,52,14,0.75)', position: 'relative', lineHeight: 1 }}>€</span>
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// AppHeader (sticky)
// ─────────────────────────────────────────────────────────────────────
function formatBalance(n) {return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '\u00A0');}

function AppHeader({ seasonLabel, seasonProgress, cabBalance, missionsBadge, onPressShop, onPressMissions, onPressAchievements }) {
  const pct = Math.max(0, Math.min(1, seasonProgress));
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '72px 14px 10px', // 62px status bar + 10px breathing room
      background: '#162028',
      borderBottom: '1px solid rgba(255,255,255,0.06)'
    }}>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 5 }}>
        <div style={{ fontSize: 9, fontWeight: 600, color: 'rgba(255,255,255,0.45)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
          {seasonLabel}
        </div>
        <div style={{ height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct * 100}%`, background: 'linear-gradient(90deg, #FBBF24, #F59E0B)', borderRadius: 2, transition: 'width .3s' }} />
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '0 4px' }}>
        <Cabecoin size="sm" />
        <span style={{ fontSize: 13, fontWeight: 800, color: '#fff', letterSpacing: '-0.26px' }}>{formatBalance(cabBalance)}</span>
      </div>
      <button onClick={onPressShop} style={iconBtnStyle}>🎁</button>
      {window.RatisAchievementsUI && <window.RatisAchievementsUI.TrophyButton onClick={onPressAchievements} />}
      <button onClick={onPressMissions} style={{ ...iconBtnStyle, position: 'relative' }}>
        📅
        {missionsBadge > 0 &&
        <div style={{
          position: 'absolute', top: -3, right: -3, width: 14, height: 14, borderRadius: 7,
          background: '#F59E0B', display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: '2px solid #0B0B10'
        }}>
            <span style={{ fontSize: 9, fontWeight: 900, color: '#0a0a0a', lineHeight: 1 }}>{missionsBadge}</span>
          </div>
        }
      </button>
    </div>);

}

const iconBtnStyle = {
  width: 34, height: 34, borderRadius: 10,
  background: 'linear-gradient(180deg, rgba(255,255,255,0.1), rgba(255,255,255,0.03))',
  border: '1px solid rgba(255,255,255,0.12)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 15, cursor: 'pointer', padding: 0, color: 'inherit',
  boxShadow: '0 2px 0 rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.08)',
  fontFamily: 'inherit'
};

// ─────────────────────────────────────────────────────────────────────
// RoiRings — SVG fossil rings (port of roi-rings.tsx + utils/roi-rings)
// ─────────────────────────────────────────────────────────────────────
const RING_R = 36,RING_SW = 9,FOSSIL_SW = 1.8,FOSSIL_SPACING = 3.5,FOSSIL_GAP = 3;
const CIRCUMFERENCE = 2 * Math.PI * RING_R;
const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];
const DEFAULT_SUB_PRICE = 799;

function getRingColor(i) {return DT.ring[i % 10 + 1] || DT.ring[1];}
function getFossilOpacity(idx, total) {return 0.25 + idx / Math.max(1, total - 1) * 0.5;}
function computeRings(totalCents, subCents = DEFAULT_SUB_PRICE) {
  const totalAbonnements = totalCents / subCents;
  const completedRings = Math.floor(totalAbonnements);
  const currentFill = totalAbonnements - completedRings;
  const prestigeLevel = Math.floor(completedRings / 10);
  const displayFossils = Math.min(completedRings, 9);
  return { totalAbonnements, completedRings, currentFill, prestigeLevel, displayFossils };
}

function RoiRings({ totalSavingsCents, monthSavingsCents, ringsConsumed = 0, pendingRings = 0, subscriptionPriceCents = DEFAULT_SUB_PRICE, streakMultiplier = 0, onClaim }) {
  const [pulse, setPulse] = useState(1);
  useEffect(() => {
    if (pendingRings <= 0) {setPulse(1);return;}
    let t = 0;
    const id = setInterval(() => {t = (t + 1) % 2;setPulse(t === 0 ? 1 : 0.7);}, 700);
    return () => clearInterval(id);
  }, [pendingRings]);

  const { totalAbonnements, completedRings, currentFill, prestigeLevel, displayFossils } = computeRings(totalSavingsCents, subscriptionPriceCents);
  const visibleFossils = Math.min(ringsConsumed, displayFossils);

  const maxFossilR = RING_R + FOSSIL_GAP + RING_SW / 2 + FOSSIL_SW / 2 + (
  visibleFossils > 0 ? (visibleFossils - 1) * FOSSIL_SPACING + FOSSIL_SW : 0);
  const svgSize = Math.max(88, (maxFossilR + 6) * 2);
  const cx = svgSize / 2,cy = svgSize / 2;
  const fossilBaseR = RING_R + FOSSIL_GAP + RING_SW / 2 + FOSSIL_SW / 2;

  const countStr = totalAbonnements.toFixed(1).replace('.', ',');
  const prestigeLabel = prestigeLevel > 0 ? prestigeLevel <= 10 ? `★${ROMAN[prestigeLevel - 1]}` : `★${prestigeLevel}` : null;
  const monthStr = (monthSavingsCents / 100).toFixed(2).replace('.', ',');

  const ringEl =
  <div style={{
    position: 'relative', width: svgSize, height: svgSize,
    opacity: pendingRings > 0 ? pulse : 1,
    transition: 'opacity .7s linear',
    cursor: pendingRings > 0 ? 'pointer' : 'default'
  }} onClick={pendingRings > 0 ? onClaim : undefined}>
      <svg width={svgSize} height={svgSize} style={{ overflow: 'visible' }}>
        <defs>
          {/* rotating light gradient on active ring */}
          <linearGradient id="roiLight" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="rgba(255,255,255,0)" />
            <stop offset="45%" stopColor="rgba(255,255,255,0)" />
            <stop offset="60%" stopColor="rgba(255,255,255,0.85)" />
            <stop offset="75%" stopColor="rgba(255,255,255,0)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0)" />
          </linearGradient>
        </defs>
        {Array.from({ length: visibleFossils }).map((_, i) => {
        const r = fossilBaseR + i * FOSSIL_SPACING;
        const ringIndex = completedRings - 1 - i;
        const opacity = getFossilOpacity(visibleFossils - 1 - i, visibleFossils);
        // cascade blink: each fossil gets its own staggered animation
        const animName = `roiFossilBlink_${i}`;
        const delay = (i * 0.18).toFixed(2);
        return (
          <circle key={`f-${ringIndex}`} cx={cx} cy={cy} r={r}
          fill="none" stroke={getRingColor(ringIndex)} strokeWidth={FOSSIL_SW}
          style={{
            opacity,
            animation: `roiFossilBlink ${(visibleFossils * 0.4).toFixed(2)}s ease-in-out ${delay}s infinite`,
            transformOrigin: `${cx}px ${cy}px`
          }} />);

      })}
        {/* Active ring track */}
        <circle cx={cx} cy={cy} r={RING_R} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={RING_SW} />
        {/* Active ring fill */}
        <circle cx={cx} cy={cy} r={RING_R} fill="none"
      stroke={prestigeLevel > 0 ? '#FF6B35' : getRingColor(completedRings)}
      strokeWidth={RING_SW}
      strokeDasharray={`${CIRCUMFERENCE * currentFill} ${CIRCUMFERENCE * (1 - currentFill)}`}
      strokeLinecap="round"
      transform={`rotate(-90 ${cx} ${cy})`} />
        {/* Rotating light overlay on active ring */}
        <g style={{ animation: 'roiLightSpin 3.2s linear infinite', transformOrigin: `${cx}px ${cy}px` }}>
          <circle cx={cx} cy={cy} r={RING_R} fill="none"
        stroke="url(#roiLight)" strokeWidth={RING_SW}
        strokeDasharray={`${CIRCUMFERENCE * 0.32} ${CIRCUMFERENCE * 0.68}`}
        strokeLinecap="round" />
        </g>
      </svg>
      <div style={{
      position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center'
    }}>
        <span style={{ fontSize: 28, fontWeight: 900, color: '#fff', letterSpacing: '-1.2px', lineHeight: 1 }}>{countStr}</span>
        <span style={{ fontSize: 9, fontWeight: 500, color: DT.text.secondary, textTransform: 'uppercase', letterSpacing: '0.5px', marginTop: 2 }}>abonnements</span>
        {prestigeLabel && <span style={{ fontSize: 11, fontWeight: 700, color: DT.gold, marginTop: 2 }}>{prestigeLabel}</span>}
      </div>
    </div>;


  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, position: 'relative', zIndex: 1 }}>
      {ringEl}
      <div style={{ fontSize: 11, color: DT.text.secondary, textAlign: 'center' }}>
        {monthStr} € économisés ce mois
      </div>
      {/* x1.4 multiplier removed per user request */}
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// MysteryProductCard
// ─────────────────────────────────────────────────────────────────────
function MysteryProductCard() {
  return (
    <div style={{
      flex: 1, width: '100%',
      background: '#3D2E5A',
      borderRadius: 18, padding: '10px 12px',
      display: 'flex', alignItems: 'center', gap: 10,
      border: '1.5px solid rgba(168,85,247,0.4)',
      boxShadow: '0 5px 0 #251638, 0 8px 18px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.1)',
      position: 'relative', overflow: 'hidden',
      cursor: 'pointer'
    }}>
      <div style={{
        position: 'absolute', top: -30, right: -30, width: 110, height: 110,
        borderRadius: 55, pointerEvents: 'none',
        background: 'radial-gradient(closest-side, rgba(168,85,247,0.3), rgba(168,85,247,0) 70%)'
      }} />
      <div style={{
        flexShrink: 0,
        width: 50, height: 50, borderRadius: 13,
        background: 'rgba(168,85,247,0.22)',
        border: '1.5px solid rgba(168,85,247,0.55)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.18)',
        position: 'relative', zIndex: 1
      }}>
        <span style={{ fontSize: 26, fontWeight: 900, color: 'rgba(255,255,255,0.92)', lineHeight: 1 }}>?</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, position: 'relative', zIndex: 1 }}>
        <div style={{ fontSize: 8, fontWeight: 800, color: 'rgba(255,255,255,0.55)', letterSpacing: '0.8px', textTransform: 'uppercase', marginBottom: 2 }}>
          Mystère
        </div>
        <div style={{ fontSize: 12, fontWeight: 900, color: '#fff', letterSpacing: '-0.2px', lineHeight: 1.1 }}>
          Produit du jour
        </div>
        <div style={{
          display: 'inline-flex', alignSelf: 'flex-start',
          marginTop: 4, padding: '2px 8px',
          background: 'linear-gradient(180deg, #FFE066, #FFB800)',
          border: '1px solid #B47800',
          borderRadius: 10,
          boxShadow: '0 2px 0 #8F5E00, inset 0 1px 0 rgba(255,255,255,0.4)'
        }}>
          <span style={{ fontSize: 9, fontWeight: 900, color: '#3A2200', letterSpacing: '-0.1px' }}>+50 cab</span>
        </div>
      </div>
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// MissionsCard + MissionsBlock
// ─────────────────────────────────────────────────────────────────────
const MISSION_VARIANTS = {
  daily: { titleColor: '#FF6B35', icon: '📅', checkBg: '#FB923C', ptsColor: '#FED7AA', activeText: '#fff', doneText: 'rgba(255,255,255,0.55)', emptyBorder: 'rgba(255,255,255,0.12)' },
  weekly: { titleColor: '#A78BFA', icon: '★', checkBg: '#8B5CF6', ptsColor: '#C4B5FD', activeText: '#fff', doneText: 'rgba(255,255,255,0.55)', emptyBorder: 'rgba(255,255,255,0.12)' }
};

function MissionsCard({ missions, onClaim, variant = 'daily', title }) {
  const v = MISSION_VARIANTS[variant];
  const completed = missions.filter((m) => m.status !== 'active').length;
  const displayed = missions.slice(0, 4);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 13 }}>{v.icon}</span>
        <span style={{ flex: 1, fontSize: 11, fontWeight: 700, color: v.titleColor, textTransform: 'uppercase', letterSpacing: '0.8px' }}>{title}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: v.titleColor }}>{completed}/{missions.length}</span>
      </div>
      {displayed.map((m) => {
        const done = m.status !== 'active';
        const GB = window.RatisShared && window.RatisShared.GameButton;
        return (
          <div key={m.id}
          style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 16, height: 16, borderRadius: 8, flexShrink: 0,
              border: '2px solid ' + (done ? v.checkBg : v.emptyBorder),
              background: done ? v.checkBg : 'transparent',
              display: 'flex', alignItems: 'center', justifyContent: 'center'
            }}>
              {done && <span style={{ color: '#fff', fontSize: 9, fontWeight: 700, lineHeight: 1 }}>✓</span>}
            </div>
            <span style={{
              flex: 1, fontSize: 12,
              color: done ? v.doneText : v.activeText,
              textDecoration: done ? 'line-through' : 'none',
              opacity: done ? 0.85 : 1,
              lineHeight: '16px'
            }}>{m.label}</span>
            {GB ?
            <GB
              color={done ? 'slate' : 'gold'}
              size="sm"
              disabled={done}
              onClick={() => !done && onClaim(m.id)}
              style={{ width: 44, padding: '3px 0', fontSize: 11, borderRadius: 9, position: 'relative', zIndex: 5, flexShrink: 0 }}>
              
                +{m.xp_reward}
              </GB> :

            <span style={{ fontSize: 10, fontWeight: 700, color: v.ptsColor, position: 'relative', zIndex: 5 }}>+{m.xp_reward}</span>
            }
          </div>);

      })}
    </div>);

}

function JackStreakButton({ streak, onFeed }) {
  const bonusPct = Math.round(streak.multiplier * 100);
  const fed = streak.already_fed_today;
  const GB = window.RatisShared && window.RatisShared.GameButton;

  return (
    <div
      style={{
        flex: 1,
        width: '100%',
        borderRadius: 18,
        padding: '10px 12px',
        display: 'flex', alignItems: 'center', gap: 10,
        position: 'relative', overflow: 'hidden',
        background: fed ?
        'linear-gradient(160deg, #0F4D45 0%, #0A3A34 100%)' :
        'linear-gradient(160deg, #4A1F1B 0%, #2E1410 100%)',
        border: '2px solid ' + (fed ? 'rgba(77,212,179,0.6)' : 'rgba(255,107,53,0.55)'),
        boxShadow: fed ?
        '0 5px 0 rgba(10,58,52,0.9), 0 12px 22px rgba(0,0,0,0.45), inset 0 2px 0 rgba(255,255,255,0.12), 0 0 22px rgba(77,212,179,0.18)' :
        '0 5px 0 rgba(60,12,8,0.95), 0 12px 22px rgba(0,0,0,0.5), inset 0 2px 0 rgba(255,255,255,0.14), 0 0 26px rgba(255,107,53,0.32)',
        userSelect: 'none'
      }}>
      
      {/* corner glow */}
      <div style={{
        position: 'absolute', top: -30, right: -30, width: 110, height: 110,
        borderRadius: 60, pointerEvents: 'none',
        background: fed ?
        'radial-gradient(closest-side, rgba(77,212,179,0.35), rgba(77,212,179,0) 72%)' :
        'radial-gradient(closest-side, rgba(255,107,53,0.5), rgba(255,107,53,0) 72%)'
      }} />

      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, position: 'relative', zIndex: 1, gap: 4 }}>
        <div style={{ fontSize: 8, fontWeight: 800, color: 'rgba(255,255,255,0.55)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>
          Streak Jack
        </div>
        <div style={{ fontSize: 12, fontWeight: 900, color: fed ? '#4DD4B3' : '#fff', letterSpacing: '-0.2px', lineHeight: 1.1 }}>
          {fed ? 'Rassasié' : 'Nourrir Jack'}
        </div>
        {bonusPct > 0 && !fed &&
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 6px', borderRadius: 6, background: 'rgba(255,184,0,0.20)', border: '1px solid rgba(255,184,0,0.5)', alignSelf: 'flex-start' }}>
            <span style={{ fontSize: 9, fontWeight: 900, color: '#FFB800', letterSpacing: '-0.1px' }}>
              +{bonusPct}%
            </span>
          </div>
        }
        {fed &&
        <div style={{ fontSize: 9, fontWeight: 700, color: 'rgba(77,212,179,0.7)' }}>
            Reviens demain
          </div>
        }
      </div>

      {/* Big streak BUTTON (red coral, like gold +XP buttons but red) */}
      {GB ?
      <GB
        color={fed ? 'slate' : 'coral'}
        size="md"
        disabled={fed}
        onClick={fed ? undefined : onFeed}
        style={{
          flexShrink: 0,
          width: 56, minHeight: 50, padding: 0,
          display: 'flex', flexDirection: 'column', gap: 0,
          borderRadius: 13,
          position: 'relative', zIndex: 1
        }}>
          <span style={{ fontWeight: 900, lineHeight: 1, letterSpacing: '-0.8px', fontSize: 22 }}>{streak.streak_days}</span>
          <span style={{ fontWeight: 800, letterSpacing: '0.8px', textTransform: 'uppercase', marginTop: 2, opacity: 0.92, fontSize: 7 }}>jours</span>
        </GB> :

      <div onClick={fed ? undefined : onFeed} style={{
        flexShrink: 0, width: 56, height: 50, borderRadius: 13,
        background: fed ? 'rgba(77,212,179,0.18)' : '#EF4444',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        color: '#fff', cursor: fed ? 'default' : 'pointer'
      }}>
          <span style={{ fontSize: 22, fontWeight: 900, lineHeight: 1 }}>{streak.streak_days}</span>
          <span style={{ fontSize: 7, fontWeight: 800, marginTop: 2 }}>JOURS</span>
        </div>
      }
    </div>);

}

// Reusable: floating sparkles around a chest/scene. Uses jarSparkle keyframe (defined in the host stylesheet).
function ChestSparkles({ tone = 'gold' }) {
  const color = tone === 'violet' ? '#C4B5FD' : tone === 'orange' ? '#FFD8B5' : '#FFE176';
  const items = [
  { pos: { right: 18, top: 18 }, size: 11, dur: 5.2, delay: 0.0 },
  { pos: { right: 60, top: 38 }, size: 9, dur: 6.4, delay: 1.5 },
  { pos: { right: 28, top: 70 }, size: 10, dur: 5.8, delay: 2.8 },
  { pos: { right: 80, bottom: 26 }, size: 12, dur: 7.0, delay: 4.1 },
  { pos: { right: 14, bottom: 52 }, size: 8, dur: 5.5, delay: 3.6 }];

  return (
    <div aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 1, overflow: 'hidden' }}>
      {items.map((it, i) =>
      <span key={i} style={{
        position: 'absolute',
        ...it.pos,
        fontSize: it.size,
        color, opacity: 0.6,
        animation: `jarSparkle ${it.dur}s ease-in-out infinite`,
        animationDelay: `${it.delay}s`,
        textShadow: `0 0 6px ${color}`
      }}>✨</span>
      )}
    </div>);

}

function MissionsBlock({ weekly, daily, onClaim }) {
  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', width: "374px" }}>
      {/* Weekly first — violet */}
      <div style={{
        borderRadius: 20, position: 'relative', overflow: 'hidden',
        background: '#27293A',
        border: '1.5px solid rgba(139,92,246,0.35)',
        boxShadow: '0 5px 0 rgba(60,30,120,0.5), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.10)',
        padding: 16
      }}>
        <div style={{ position: 'relative', zIndex: 1 }}>
          <MissionsCard missions={weekly} onClaim={onClaim} variant="weekly" title="Missions de la semaine" />
        </div>
      </div>

      {/* Gap separator: opaque strip at body color. Sits ABOVE the chest image so it cleanly hides the chest portion that crosses between the two cards. */}
      <div style={{ height: 10, background: '#1a242c', position: 'relative', zIndex: 3 }} />

      {/* Daily — orange/coral accents */}
      <div style={{
        borderRadius: 20, position: 'relative', overflow: 'hidden',
        background: '#27293A',
        border: '1.5px solid rgba(251,146,60,0.35)',
        boxShadow: '0 5px 0 rgba(60,30,10,0.55), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.10)',
        padding: 16
      }}>
        <div style={{ position: 'relative', zIndex: 1 }}>
          <MissionsCard missions={daily} onClaim={onClaim} variant="daily" title="Missions du jour" />
        </div>
      </div>

      {/* SINGLE chest image overlaying both cards. Anchored to wrapper edges so it's robust to any card-height variation. The opaque gap separator above clips it cleanly in the middle. */}
      <img src="lib/cabecoin-chest.svg" alt="" aria-hidden="true" style={{
        position: 'absolute',
        right: '18%', top: 0, bottom: 0,
        height: '100%', width: 'auto',
        transform: 'scaleX(-1)',
        opacity: 0.32,
        pointerEvents: 'none',
        zIndex: 2,
        filter: 'drop-shadow(0 6px 14px rgba(0,0,0,0.5))'
      }} />
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// BattlepassCard
// ─────────────────────────────────────────────────────────────────────
const TIER_ICONS = ['🎀', '💎', '⭐', '🎁', '👑', '🏆', '🔑', '🎖️'];

function BattlepassCard({ bp, onPress }) {
  const pct = bp.xp_next_level > 0 ? Math.max(0, Math.min(100, bp.xp_current / bp.xp_next_level * 100)) : 100;
  const xpRemaining = Math.max(0, bp.xp_next_level - bp.xp_current);
  const startLevel = bp.current_level - 1;
  return (
    <div onClick={onPress} style={{
      position: 'relative', overflow: 'hidden', flexShrink: 0,
      background: 'linear-gradient(180deg, #0E7490 0%, #0E5366 65%, #082C3A 100%)',
      border: '1.5px solid rgba(103,232,249,0.55)',
      borderRadius: 20, cursor: 'pointer',
      boxShadow: '0 5px 0 rgba(8,60,80,0.95), 0 12px 24px rgba(0,0,0,0.4), inset 0 2px 0 rgba(255,255,255,0.18)', padding: 14
    }}>
      {/* Spring scene background — soft, dreamy ambiance behind all content */}
      <img src="lib/ratis-spring-scene.png" alt="" aria-hidden="true" style={{
        position: 'absolute', inset: 0, width: '100%', height: '100%',
        objectFit: 'cover', objectPosition: 'center',
        opacity: 0.22,
        mixBlendMode: 'luminosity',
        pointerEvents: 'none',
        zIndex: 0
      }} />
      {/* shimmer */}
      <div style={{
        position: 'absolute', top: -40, right: -30, width: 180, height: 180,
        borderRadius: 90, pointerEvents: 'none',
        background: 'radial-gradient(closest-side, rgba(103,232,249,0.30), rgba(103,232,249,0) 70%)'
      }} />
      <div style={{ position: 'relative', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 22, height: 22, borderRadius: 7, background: 'rgba(34,211,238,0.35)', border: '1px solid rgba(103,232,249,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontSize: 11 }}>🎫</span>
          </div>
          <span style={{ fontSize: 11, fontWeight: 800, color: '#67E8F9', letterSpacing: '0.5px', textTransform: 'uppercase' }}>Pass {bp.season_name}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '3px 8px', borderRadius: 999,
            background: 'rgba(0,0,0,0.35)', border: '1px solid rgba(103,232,249,0.25)'
          }}>
            <span style={{ fontSize: 10 }}>⏱</span>
            <span style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.85)', letterSpacing: '0.3px' }}>23j restants</span>
          </div>
          <span style={{ color: '#67E8F9', fontSize: 18, fontWeight: 600 }}>→</span>
        </div>
      </div>

      <div style={{ position: 'relative', display: 'flex', alignItems: 'baseline', marginTop: 12, marginBottom: 8 }}>
        <span style={{ fontSize: 24, fontWeight: 900, color: '#fff', letterSpacing: '-0.66px' }}>Niv. {bp.current_level}</span>
        <span style={{ fontSize: 16, color: 'rgba(255,255,255,0.45)', fontWeight: 500, marginLeft: 6 }}>/ 50</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.55)', fontWeight: 600, letterSpacing: '0.4px', textTransform: 'uppercase' }}>Saison 04</span>
      </div>

      <div style={{ position: 'relative', height: 12, background: 'rgba(0,0,0,0.4)', borderRadius: 6, overflow: 'hidden', border: '1px solid rgba(0,0,0,0.5)', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.5)' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: 'linear-gradient(180deg, #67E8F9 0%, #22D3EE 50%, #0891B2 100%)', borderRadius: 5, transition: 'width .3s', boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.55)' }} />
      </div>

      <div style={{ position: 'relative', display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'rgba(255,255,255,0.65)', fontWeight: 500, marginTop: 6 }}>
        <span><span style={{ color: '#fff', fontWeight: 800 }}>{bp.xp_current}</span>{' / '}{bp.xp_next_level} XP</span>
        <span>encore <span style={{ color: '#fff', fontWeight: 800 }}>{xpRemaining}</span> pour Niv. {bp.current_level + 1}</span>
      </div>

      {/* Next reward banner */}
      <div style={{
        position: 'relative', marginTop: 12,
        padding: '10px 12px', borderRadius: 14,
        background: 'rgba(0,0,0,0.32)',
        border: '1px solid rgba(255,184,0,0.35)',
        display: 'flex', alignItems: 'center', gap: 10
      }}>
        <div style={{
          width: 36, height: 36, borderRadius: 10, flexShrink: 0,
          background: 'linear-gradient(180deg, #FFD860, #FFB800)',
          border: '1.5px solid #FFE47A',
          boxShadow: '0 2px 0 #8F5E00, inset 0 1px 0 rgba(255,255,255,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 18
        }}>🎁</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,184,0,0.85)', letterSpacing: '0.5px', textTransform: 'uppercase' }}>Prochaine récompense</div>
          <div style={{ fontSize: 13, fontWeight: 800, color: '#fff', marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{bp.next_reward_label}</div>
        </div>
        <div style={{
          padding: '4px 10px', borderRadius: 999,
          background: 'rgba(103,232,249,0.18)', border: '1px solid rgba(103,232,249,0.4)',
          fontSize: 10, fontWeight: 800, color: '#67E8F9', letterSpacing: '0.3px'
        }}>Niv. {bp.current_level + 1}</div>
      </div>

      <div style={{ position: 'relative', display: 'flex', gap: 6, marginTop: 14 }}>
        {[0, 1, 2, 3, 4].map((off) => {
          const level = startLevel + off;
          const isCurrent = level === bp.current_level;
          const isDone = level < bp.current_level;
          const isLocked = level > bp.current_level + 1;
          let bg = 'rgba(0,0,0,0.25)',bd = 'rgba(255,255,255,0.08)',shadow = '0 2px 0 rgba(0,0,0,0.4)',op = 1;
          if (isDone) {bg = 'linear-gradient(180deg, rgba(34,211,238,0.25), rgba(8,145,178,0.25))';bd = 'rgba(34,211,238,0.45)';shadow = '0 2px 0 rgba(8,80,100,0.6)';op = 0.85;}
          if (isCurrent) {bg = 'linear-gradient(180deg, #22D3EE, #0891B2)';bd = '#67E8F9';shadow = '0 4px 0 #0E5566, 0 8px 14px rgba(34,211,238,0.4)';op = 1;}
          if (isLocked) {op = 0.4;}
          return (
            <div key={level} style={{
              flex: 1, padding: 8, borderRadius: 10, position: 'relative',
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              background: bg, border: `1px solid ${bd}`, opacity: op, boxShadow: shadow
            }}>
              {isDone && <span style={{ position: 'absolute', top: 3, right: 4, fontSize: 9, color: 'rgba(255,255,255,0.7)' }}>✓</span>}
              {isLocked && <span style={{ position: 'absolute', top: 3, right: 4, fontSize: 9 }}>🔒</span>}
              <span style={{ fontSize: 20, marginBottom: 2, lineHeight: 1, filter: isCurrent ? 'drop-shadow(0 1px 2px rgba(0,0,0,0.3))' : 'none' }}>{TIER_ICONS[level % TIER_ICONS.length]}</span>
              <span style={{ fontSize: 10, fontWeight: 900, color: isCurrent ? '#fff' : 'rgba(255,255,255,0.5)' }}>{level}</span>
            </div>);

        })}
      </div>
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// JackCard — the rat companion
// ─────────────────────────────────────────────────────────────────────
function JackCard({ streak, onPress }) {
  const bonusPct = Math.min(100, streak.streak_days * 5);
  return (
    <div onClick={onPress} style={{
      position: 'relative', overflow: 'hidden',
      padding: 14, borderRadius: 20,
      border: '1.5px solid rgba(239,68,68,0.5)',
      background: '#4A2A26',
      boxShadow: '0 5px 0 rgba(80,18,18,0.7), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.14)',
      display: 'flex', flexDirection: 'column', gap: 8, cursor: 'pointer'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{
          width: 44, height: 44, borderRadius: 22,
          background: 'linear-gradient(135deg, #6EE7B7, #10B981)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 3px 0 #047857, inset 0 1px 0 rgba(255,255,255,0.4)'
        }}>
          <span style={{ fontSize: 24 }}>🐀</span>
        </div>
        {streak.already_fed_today ?
        <div style={{ width: 26, height: 26, borderRadius: 13, background: 'rgba(77,212,179,0.15)', border: '1px solid rgba(77,212,179,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ color: '#4DD4B3', fontSize: 13, fontWeight: 800 }}>✓</span>
          </div> :

        <div style={{ width: 26, height: 26, borderRadius: 13, background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ color: '#F87171', fontSize: 13, fontWeight: 900 }}>!</span>
          </div>
        }
      </div>
      <div style={{ fontSize: 13, fontWeight: 800, color: '#fff', letterSpacing: '-0.26px', marginTop: 4 }}>Jack</div>
      <div style={{
        alignSelf: 'flex-start', padding: '3px 8px', borderRadius: 10,
        background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)'
      }}>
        <span style={{ color: '#F87171', fontSize: 10.5, fontWeight: 800, letterSpacing: '-0.21px' }}>+{bonusPct}% bonus</span>
      </div>
      <div style={{ height: 1, marginTop: 2, borderTop: '1px dashed rgba(255,255,255,0.08)' }} />
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ fontSize: 32, fontWeight: 900, color: '#EF4444', letterSpacing: '-1.28px', lineHeight: 1 }}>{streak.streak_days}</span>
        <span style={{ fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.5)' }}>jours de série</span>
      </div>
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// EnrichissementCard
// ─────────────────────────────────────────────────────────────────────
function EnrichissementCard({ task, onPress }) {
  if (!task) return null;
  const rewardEuros = (task.cab_reward / 100).toFixed(2).replace('.', ',');
  return (
    <div style={{
      padding: 14, borderRadius: 20,
      background: '#3D2E0F',
      border: '1.5px solid rgba(255,184,0,0.45)',
      boxShadow: '0 5px 0 rgba(120,80,0,0.5), 0 12px 22px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.16)',
      display: 'flex', flexDirection: 'column', gap: 6
    }}>
      <div style={{
        width: 42, height: 42, borderRadius: 12,
        background: 'linear-gradient(135deg, #FFD860, #FFB800)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: '0 3px 0 #8F5E00, inset 0 1px 0 rgba(255,255,255,0.4)'
      }}>
        <span style={{ fontSize: 22 }}>💡</span>
      </div>
      <div style={{ fontSize: 13, fontWeight: 800, color: '#fff', marginTop: 4, letterSpacing: '-0.26px' }}>Compléter</div>
      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)', fontWeight: 500, lineHeight: '14px',
        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden'
      }}>
        {task.product_name} — {task.missing_field}
      </div>
      <button onClick={() => onPress(task.product_ean)} style={{
        marginTop: 4, padding: '9px 10px',
        background: 'linear-gradient(180deg, #FFE066 0%, #FFB800 60%, #E69500 100%)',
        borderRadius: 12, border: '1.5px solid #B47800',
        cursor: 'pointer',
        fontSize: 12, fontWeight: 900, color: '#3A2200', letterSpacing: '-0.24px',
        boxShadow: '0 4px 0 #8F5E00, inset 0 1px 0 rgba(255,255,255,0.5)',
        textShadow: '0 1px 0 rgba(255,255,255,0.3)',
        fontFamily: 'inherit'
      }}>+{rewardEuros} €</button>
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// RatisTabBar
// ─────────────────────────────────────────────────────────────────────
const TAB_ICONS = {
  index: { svg: 'M3 11.5L12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z', label: 'Accueil' },
  liste: { svg: 'M3 6h18M3 12h18M3 18h18', label: 'Liste', stroke: true },
  produit: { svg: 'M3 7h18l-1 13H4zM8 7V5a4 4 0 0 1 8 0v2', label: 'Produit', stroke: true },
  profil: { svg: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0', label: 'Profil', stroke: true }
};

function TabIcon({ name, color }) {
  const meta = TAB_ICONS[name];
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill={meta.stroke ? 'none' : color} stroke={meta.stroke ? color : 'none'} strokeWidth={meta.stroke ? 2 : 0} strokeLinecap="round" strokeLinejoin="round">
      <path d={meta.svg} />
    </svg>);

}

function RatisTabBar({ active, onSelect }) {
  const order = ['index', 'liste', 'scan', 'produit', 'profil'];
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', height: 84,
      background: 'rgba(22,32,40,0.95)',
      borderTop: '1px solid rgba(255,255,255,0.06)',
      paddingTop: 10, paddingBottom: 32, paddingLeft: 4, paddingRight: 4,
      backdropFilter: 'blur(12px)',
      position: 'relative', zIndex: 20, overflow: 'visible'
    }}>
      {order.map((name) => {
        if (name === 'scan') {
          return (
            <div key="scan" style={{ width: 78, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div onClick={() => onSelect('scan')} style={{
                width: 60, height: 60, borderRadius: 30, marginTop: -20,
                background: 'rgba(22,32,40,0.98)',
                border: '2.5px solid #DA7756',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                boxShadow: '0 5px 0 rgba(100,40,20,0.6), 0 10px 20px rgba(218,119,86,0.3), inset 0 1px 0 rgba(255,255,255,0.08)',
                cursor: 'pointer',
                position: 'relative', zIndex: 10
              }}>
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#DA7756" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" />
                  <rect x="7" y="7" width="10" height="10" />
                </svg>
              </div>
              <span style={{ fontSize: 10, fontWeight: 700, color: '#DA7756', marginTop: 4 }}>Scan</span>
            </div>);

        }
        const isActive = active === name;
        const color = isActive ? '#DA7756' : 'rgba(255,255,255,0.45)';
        return (
          <div key={name} onClick={() => onSelect(name)} style={{
            flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
            gap: 4, paddingTop: 6, position: 'relative', cursor: 'pointer'
          }}>
            {isActive && <div style={{ position: 'absolute', top: 0, width: 4, height: 4, borderRadius: 2, background: '#DA7756' }} />}
            <TabIcon name={name} color={color} />
            <span style={{ fontSize: 10, fontWeight: 700, color, letterSpacing: '-0.1px' }}>{TAB_ICONS[name].label}</span>
          </div>);

      })}
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// Mock data (matches hook shapes)
// ─────────────────────────────────────────────────────────────────────
const INITIAL_DATA = () => ({
  streak: { streak_days: 7, multiplier: 0.35, food_reserves: 3, already_fed_today: false, needs_repair: false, last_fed_at: null },
  weekly: [
  { id: 'w1', label: 'Scanner 5 tickets de caisse', status: 'completed', xp_reward: 50, cab_reward: 200 },
  { id: 'w2', label: 'Ajouter 10 produits à ta liste', status: 'completed', xp_reward: 30, cab_reward: 100 },
  { id: 'w3', label: 'Inviter un ami à Ratis', status: 'active', xp_reward: 100, cab_reward: 500 },
  { id: 'w4', label: 'Compléter 3 fiches produit', status: 'active', xp_reward: 60, cab_reward: 250 }],

  daily: [
  { id: 'd1', label: 'Scanner un code-barres', status: 'completed', xp_reward: 10, cab_reward: 30 },
  { id: 'd2', label: 'Scanner une étiquette en magasin', status: 'active', xp_reward: 15, cab_reward: 50 },
  { id: 'd3', label: 'Scanner un ticket de caisse', status: 'active', xp_reward: 20, cab_reward: 60 },
  { id: 'd4', label: 'Compléter une fiche produit', status: 'active', xp_reward: 25, cab_reward: 80 }],

  battlepass: { season_name: 'Printemps 26', current_level: 12, xp_current: 340, xp_next_level: 500, next_reward_label: 'Skin doré', next_reward_type: 'skin' },
  stats: { total_savings_cents: 4795, today_savings_cents: 1240, rings: { rings_consumed: 3, pending_rings: 1, subscription_price_cents: 799 } },
  enrichissement: { product_ean: '3456789012345', product_name: 'Yaourt grec Andros', missing_field: 'la marque', cab_reward: 25 },
  cabBalance: 12480
});

// ─────────────────────────────────────────────────────────────────────
// Missions Modal — popup view of weekly + daily
// ─────────────────────────────────────────────────────────────────────
function MissionsModal({ open, onClose, weekly, daily, onClaim }) {
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'absolute', inset: 0, zIndex: 200,
        background: 'rgba(0,0,0,0.65)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
        animation: 'fadeIn .2s ease-out'
      }}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%',
          maxHeight: '82%',
          background: 'linear-gradient(180deg, #1c2730 0%, #15191c 100%)',
          borderTopLeftRadius: 24, borderTopRightRadius: 24,
          border: '1px solid rgba(255,255,255,0.08)',
          borderBottom: 'none',
          boxShadow: '0 -10px 40px rgba(0,0,0,0.6)',
          padding: 16, paddingBottom: 28,
          overflowY: 'auto',
          display: 'flex', flexDirection: 'column', gap: 12,
          animation: 'slideUp .26s cubic-bezier(.2,.9,.3,1.2)'
        }}>
        {/* drag handle */}
        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <div style={{ width: 40, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.18)' }} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 4px 8px' }}>
          <div>
            <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.45)', letterSpacing: '0.6px', textTransform: 'uppercase' }}>Tes missions</div>
            <h2 style={{ margin: 0, fontSize: 22, fontWeight: 900, color: '#fff', letterSpacing: '-0.6px', marginTop: 2 }}>Missions actives</h2>
          </div>
          <button
            onClick={onClose}
            style={{
              width: 36, height: 36, borderRadius: 10,
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.12)',
              color: '#fff', fontSize: 18, fontWeight: 700,
              cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
              padding: 0
            }}>×</button>
        </div>

        <div style={{
          background: '#27293A',
          border: '1px solid rgba(167,139,250,0.25)',
          borderRadius: 18,
          padding: 14,
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.06)'
        }}>
          <MissionsCard missions={weekly} onClaim={onClaim} variant="weekly" title="Missions de la semaine" />
        </div>

        <div style={{
          background: '#27293A',
          border: '1px solid rgba(255,107,53,0.25)',
          borderRadius: 18,
          padding: 14,
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.06)'
        }}>
          <MissionsCard missions={daily} onClaim={onClaim} variant="daily" title="Missions du jour" />
        </div>
      </div>
    </div>);

}

// ─────────────────────────────────────────────────────────────────────
// Tweaks Panel
// ─────────────────────────────────────────────────────────────────────
function TweaksPanel({ open, onClose, onClaimRing, onCompleteMission, onFeedJack, onReset, onUnlockAchievement, hasPendingRing, fedToday, allDone, btnStyle, tabStyle, onBtnStyle, onTabStyle, optimizeColor, onOptimizeColor }) {
  if (!open) return null;

  const actionBtn = (label, fn, danger) =>
  <button onClick={fn} disabled={!fn} style={{
    width: '100%', padding: '8px 10px', marginBottom: 6,
    background: fn ? danger ? 'linear-gradient(180deg, #EF4444, #B91C1C)' : 'linear-gradient(180deg, #4DD4B3, #0F8F7B)' : 'rgba(255,255,255,0.05)',
    color: fn ? '#0B0B10' : 'rgba(255,255,255,0.3)',
    border: '1px solid ' + (fn ? 'rgba(0,0,0,0.3)' : 'rgba(255,255,255,0.06)'),
    borderRadius: 8,
    fontSize: 11, fontWeight: 800, letterSpacing: '0.5px', textTransform: 'uppercase',
    cursor: fn ? 'pointer' : 'not-allowed', fontFamily: 'inherit'
  }}>{label}</button>;

  const segLabel = { fontSize: 10, fontWeight: 800, color: 'rgba(255,255,255,0.45)', letterSpacing: '0.6px', textTransform: 'uppercase', marginBottom: 5 };
  const segWrap = { display: 'flex', gap: 4, marginBottom: 12 };
  const segBtn = (label, active, onClick) =>
  <button onClick={onClick} style={{
    flex: 1, padding: '6px 4px',
    fontSize: 10, fontWeight: 800,
    background: active ? 'rgba(218,119,86,0.22)' : 'rgba(255,255,255,0.04)',
    border: active ? '1.5px solid rgba(218,119,86,0.6)' : '1.5px solid rgba(255,255,255,0.1)',
    borderRadius: 7, color: active ? '#fff' : 'rgba(255,255,255,0.45)',
    cursor: 'pointer', fontFamily: 'inherit'
  }}>{label}</button>;


  return (
    <div style={{
      position: 'fixed', right: 20, bottom: 20, width: 256, padding: 14, zIndex: 9999,
      background: 'linear-gradient(180deg, #1a2428, #0d1518)',
      border: '1px solid rgba(218,119,86,0.3)',
      borderRadius: 14,
      boxShadow: '0 12px 32px rgba(0,0,0,0.6)',
      color: '#fff', fontFamily: 'Inter, system-ui, sans-serif'
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.2em', textTransform: 'uppercase', color: '#4DD4B3' }}>Tweaks</span>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.5)', cursor: 'pointer', fontSize: 16, padding: 0 }}>×</button>
      </div>

      {/* ── Design ── */}
      <div style={{ marginBottom: 14, paddingBottom: 12, borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
        <div style={{ fontSize: 9, fontWeight: 900, color: 'rgba(218,119,86,0.8)', letterSpacing: '1px', textTransform: 'uppercase', marginBottom: 8 }}>Design</div>
        <div style={segLabel}>Boutons</div>
        <div style={segWrap}>
          {segBtn('3D', btnStyle === '3d', () => onBtnStyle('3d'))}
          {segBtn('Glow', btnStyle === 'glow', () => onBtnStyle('glow'))}
          {segBtn('Flat', btnStyle === 'flat', () => onBtnStyle('flat'))}
          {segBtn('Outline', btnStyle === 'outline', () => onBtnStyle('outline'))}
        </div>
        <div style={segLabel}>Onglets</div>
        <div style={segWrap}>
          {segBtn('Filled', tabStyle === 'filled', () => onTabStyle('filled'))}
          {segBtn('Underline', tabStyle === 'underline', () => onTabStyle('underline'))}
          {segBtn('Ghost', tabStyle === 'ghost', () => onTabStyle('ghost'))}
        </div>

        <div style={segLabel}>Bouton optimiser</div>
        <div style={segWrap}>
          {segBtn('🔴 Coral', optimizeColor === 'coral', () => onOptimizeColor('coral'))}
          {segBtn('🟠 Saumon', optimizeColor === 'teal', () => onOptimizeColor('teal'))}
          {segBtn('🟢 Teal', optimizeColor === 'teal', () => onOptimizeColor('teal'))}
          {segBtn('🔵 Violet', optimizeColor === 'violet', () => onOptimizeColor('violet'))}
        </div>
      </div>

      {/* ── Actions ── */}
      <div style={{ fontSize: 9, fontWeight: 900, color: 'rgba(218,119,86,0.8)', letterSpacing: '1px', textTransform: 'uppercase', marginBottom: 8 }}>Actions</div>
      {actionBtn(hasPendingRing ? '◉ Briser l\'anneau' : '◉ Anneau (rien à briser)', hasPendingRing ? onClaimRing : null)}
      {actionBtn(allDone ? '✓ Toutes faites' : '✓ Compléter une mission', allDone ? null : onCompleteMission)}
      {actionBtn(fedToday ? '🐀 Jack rassasié' : '🐀 Nourrir Jack', fedToday ? null : onFeedJack)}
      {actionBtn('🏆 Tester un succès débloqué', onUnlockAchievement)}
      {actionBtn('↻ Réinitialiser', onReset, true)}
    </div>);

}


// ─────────────────────────────────────────────────────────────────────
// Main app
// ─────────────────────────────────────────────────────────────────────
function getContextualMessage({ hour, streak, missions }) {
  if (hour < 6) return 'Tu veilles tard ce soir 🦉';
  if (hour < 12) return 'Belle matinée pour économiser';
  if (hour < 18) return streak >= 7 ? `Belle série de ${streak} jours !` : 'Continue, tu es sur la bonne voie';
  return 'Bilan de la journée';
}

function RatisRealApp() {
  const [data, setData] = useState(INITIAL_DATA);
  const [activeTab, setActiveTab] = useState('index');
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [missionsOpen, setMissionsOpen] = useState(false);
  const [achievementsOpen, setAchievementsOpen] = useState(false);
  const [unlockToast, setUnlockToast] = useState(null);
  const [toast, setToast] = useState(null);
  const [btnStyle, setBtnStyle] = useState(() => window.RatisTweaks && window.RatisTweaks.btnStyle || '3d');
  const [tabStyle, setTabStyle] = useState(() => window.RatisTweaks && window.RatisTweaks.tabStyle || 'underline');
  const [optimizeColor, setOptimizeColor] = useState(() => window.RatisTweaks && window.RatisTweaks.optimizeColor || 'coral');
  const scrollRef = useRef(null);
  const missionsRef = useRef(null);

  const dispatchTweaks = (bs, ts, oc) => {
    window.RatisTweaks = { btnStyle: bs, tabStyle: ts, optimizeColor: oc };
    window.dispatchEvent(new CustomEvent('ratis-tweaks-change', { detail: { btnStyle: bs, tabStyle: ts, optimizeColor: oc } }));
  };
  const handleBtnStyle = (v) => {setBtnStyle(v);dispatchTweaks(v, tabStyle, optimizeColor);};
  const handleTabStyle = (v) => {setTabStyle(v);dispatchTweaks(btnStyle, v, optimizeColor);};
  const handleOptimizeColor = (v) => {setOptimizeColor(v);dispatchTweaks(btnStyle, tabStyle, v);};

  // Dispatch initial tweaks so all components are in sync on first render
  React.useEffect(() => {
    dispatchTweaks(btnStyle, tabStyle, optimizeColor);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // tweaks protocol
  useEffect(() => {
    const onMsg = (e) => {
      if (!e.data || typeof e.data !== 'object') return;
      if (e.data.type === '__activate_edit_mode') setTweaksOpen(true);
      if (e.data.type === '__deactivate_edit_mode') setTweaksOpen(false);
    };
    window.addEventListener('message', onMsg);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', onMsg);
  }, []);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 1800);
  };

  const claimMission = (id) => {
    setData((d) => {
      const tag = d.daily.find((m) => m.id === id) ? 'daily' : 'weekly';
      const list = d[tag];
      const m = list.find((x) => x.id === id);
      if (!m || m.status !== 'active') return d;
      showToast(`+${m.xp_reward} XP · +${m.cab_reward} cab`);
      return {
        ...d,
        [tag]: list.map((x) => x.id === id ? { ...x, status: 'completed' } : x),
        cabBalance: d.cabBalance + m.cab_reward
      };
    });
  };

  const claimRing = () => {
    setData((d) => {
      if (d.stats.rings.pending_rings <= 0) return d;
      showToast('Anneau brisé · +1 fossile');
      return {
        ...d,
        stats: { ...d.stats, rings: {
            ...d.stats.rings,
            rings_consumed: d.stats.rings.rings_consumed + 1,
            pending_rings: d.stats.rings.pending_rings - 1
          } }
      };
    });
  };

  const feedJack = () => {
    setData((d) => d.streak.already_fed_today ? d : {
      ...d,
      streak: { ...d.streak, already_fed_today: true, streak_days: d.streak.streak_days + 1 }
    });
    showToast('Jack rassasié 🐀 · série +1');
  };

  const completeNextMission = () => {
    const dailyActive = data.daily.find((m) => m.status === 'active');
    const weeklyActive = data.weekly.find((m) => m.status === 'active');
    const next = dailyActive || weeklyActive;
    if (next) claimMission(next.id);
  };

  const reset = () => setData(INITIAL_DATA());

  const scrollToMissions = () => {
    if (missionsRef.current && scrollRef.current) {
      scrollRef.current.scrollTo({ top: missionsRef.current.offsetTop - 8, behavior: 'smooth' });
    }
  };

  const activeMissionsCount = data.daily.filter((m) => m.status === 'active').length +
  data.weekly.filter((m) => m.status === 'active').length;
  const allDone = activeMissionsCount === 0;

  const message = getContextualMessage({ hour: 10, streak: data.streak.streak_days, missions: data.daily });
  const seasonLabel = `Saison · Niv. ${data.battlepass.current_level}`;
  const seasonProgress = data.battlepass.xp_current / data.battlepass.xp_next_level;

  return (
    <div style={{ position: 'relative', height: '100%', background: '#1c2730', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <ScreenBackground />

      {/* Sticky header — hidden on Scan (full-bleed camera) */}
      {activeTab !== 'scan' &&
      <div style={{ position: 'relative', zIndex: 5 }}>
          <AppHeader
          seasonLabel={seasonLabel}
          seasonProgress={seasonProgress}
          cabBalance={data.cabBalance}
          missionsBadge={activeMissionsCount}
          onPressShop={() => showToast('Boutique bientôt 🎁')}
          onPressMissions={() => setMissionsOpen(true)}
          onPressAchievements={() => setAchievementsOpen(true)} />
        
        </div>
      }

      {/* Scan tab — fullscreen camera replaces normal content area */}
      {activeTab === 'scan' &&
      <div style={{ position: 'relative', flex: 1, display: 'flex', flexDirection: 'column' }}>
          <window.ScanScreen showToast={showToast} />
        </div>
      }

      {/* Liste tab */}
      {activeTab === 'liste' &&
      <div style={{ position: 'relative', zIndex: 1, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <window.ListeScreen showToast={showToast} />
      </div>
      }

      {/* Produit tab */}
      {activeTab === 'produit' &&
      <div style={{ position: 'relative', zIndex: 1, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <window.ProduitScreen showToast={showToast} />
      </div>
      }

      {/* Profil tab */}
      {activeTab === 'profil' &&
      <div style={{ position: 'relative', zIndex: 1, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <window.ProfilScreen data={data} showToast={showToast} />
      </div>
      }

      {/* Index (Dashboard) — original scrollable content */}
      {activeTab === 'index' &&
      <div ref={scrollRef} className="dash-scroll-root" style={{
        position: 'relative', flex: 1, overflowY: 'auto',
        padding: '0 14px 24px',
        display: 'flex', flexDirection: 'column', gap: 12
      }}>
        <style>{`.dash-scroll-root > * { flex-shrink: 0; }`}</style>
        {/* Greeting */}
        <div style={{ paddingLeft: 4, paddingRight: 4, marginTop: 12 }}>
          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)', fontWeight: 600, letterSpacing: '-0.1px' }}>Bonjour,</div>
          <div style={{ fontSize: 17, color: '#fff', fontWeight: 800, letterSpacing: '-0.34px', marginTop: 2 }}>{message}</div>
        </div>

        {/* Hero row — ROI left, Mystery + Jack stacked right */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'stretch' }}>
          <div style={{ flex: 1.4, display: 'flex', minHeight: 220 }}>
            <window.RoiVariants.RoiV5_Jar totalEur={data.stats.total_savings_cents / 100} />
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ flex: 1, display: 'flex' }}>
              <MysteryProductCard />
            </div>
            <div style={{ flex: 1, display: 'flex' }}>
              <JackStreakButton streak={data.streak} onFeed={feedJack} />
            </div>
          </div>
        </div>

        {/* Battle Pass */}
        <BattlepassCard bp={data.battlepass} onPress={() => showToast('Pass bientôt')} />

        {/* Next achievement preview */}
        {window.RatisAchievementsUI &&
        <window.RatisAchievementsUI.NextAchievementCard onPress={() => setAchievementsOpen(true)} />
        }

        {/* Missions */}
        <div ref={missionsRef}>
          <MissionsBlock
            weekly={data.weekly}
            daily={data.daily}
            onClaim={claimMission} />
          
        </div>

        {/* Enrichissement — full width */}
        <EnrichissementCard task={data.enrichissement} onPress={() => showToast('Ouverture fiche produit')} />
      </div>
      }

      {/* Tab bar */}
      <RatisTabBar active={activeTab} onSelect={(n) => {
        setActiveTab(n);
      }} />

      {/* Toast */}
      {toast &&
      <div style={{
        position: 'absolute', left: '50%', bottom: 110, transform: 'translateX(-50%)',
        padding: '10px 16px', background: 'rgba(15,30,35,0.95)',
        border: '1px solid rgba(218,119,86,0.4)', borderRadius: 12,
        color: '#fff', fontSize: 12, fontWeight: 700, letterSpacing: '-0.1px',
        boxShadow: '0 8px 22px rgba(0,0,0,0.4)', zIndex: 50,
        animation: 'toastIn .25s ease-out'
      }}>{toast}</div>
      }

      {/* Missions modal */}
      <MissionsModal
        open={missionsOpen}
        onClose={() => setMissionsOpen(false)}
        weekly={data.weekly}
        daily={data.daily}
        onClaim={claimMission} />
      

      {/* Achievements modal */}
      {window.RatisAchievementsUI &&
      <window.RatisAchievementsUI.AchievementsModal
        open={achievementsOpen}
        onClose={() => setAchievementsOpen(false)} />

      }

      {/* Achievement unlock toast */}
      {window.RatisAchievementsUI &&
      <window.RatisAchievementsUI.AchievementUnlockToast
        ach={unlockToast}
        onDismiss={() => setUnlockToast(null)} />

      }

      {/* Tweaks panel */}
      <TweaksPanel
        open={tweaksOpen}
        onClose={() => {setTweaksOpen(false);window.parent.postMessage({ type: '__edit_mode_dismissed' }, '*');}}
        onClaimRing={claimRing}
        onCompleteMission={completeNextMission}
        onFeedJack={feedJack}
        btnStyle={btnStyle}
        tabStyle={tabStyle}
        onBtnStyle={handleBtnStyle}
        onTabStyle={handleTabStyle}
        optimizeColor={optimizeColor}
        onOptimizeColor={handleOptimizeColor}
        onUnlockAchievement={() => {
          const all = window.RatisAchievements?.ACHIEVEMENTS || [];
          // pick a random non-secret one
          const pool = all.filter((a) => a.category !== 'secret');
          const random = pool[Math.floor(Math.random() * pool.length)];
          if (random) setUnlockToast({ ...random, status: 'unlocked' });
        }}
        onReset={reset}
        hasPendingRing={data.stats.rings.pending_rings > 0}
        fedToday={data.streak.already_fed_today}
        allDone={allDone} />
      
    </div>);

}

window.RatisRealApp = RatisRealApp;