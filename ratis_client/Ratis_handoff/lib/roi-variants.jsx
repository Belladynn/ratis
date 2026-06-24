// ROI Hero Card — visual variants for exploration
// Each variant is a self-contained card meant to occupy the dashboard "ROI" slot (~220x230).

const RV_DATA = {
  total: '142,80',     // total saved €
  month: '24,30',      // this month €
  rings: 6,            // rings broken (subscription cycles paid for)
  ringsTotal: 10,      // rings in current prestige cycle
  fill: 0.62,          // current ring fill (0-1)
  pending: 1,          // pending rings to claim
  subPrice: '6,99',    // subscription price
};

// ─────────────────────────────────────────────────────────────
// V1 — Fossil Rings (current production version, simplified)
// ─────────────────────────────────────────────────────────────
function RoiV1_Fossil() {
  const cx = 80, cy = 80, R = 36, SW = 9;
  const FOSSIL_GAP = 3, FOSSIL_SW = 1.8, FOSSIL_SPACING = 3.5;
  const fossilBaseR = R + FOSSIL_GAP + SW / 2 + FOSSIL_SW / 2;
  const C = 2 * Math.PI * R;
  const visibleFossils = RV_DATA.rings;
  const ringColors = ['#67E8F9', '#A78BFA', '#FF6B9D', '#FFB800', '#4DD4B3', '#F87171', '#FB923C', '#A78BFA', '#67E8F9', '#4DD4B3'];
  return (
    <div style={rvCard('#0F3D3A', 'rgba(77,212,179,0.6)', 'rgba(15,80,72,0.85)')}>
      {/* halo */}
      <div style={rvHalo('rgba(77,212,179,0.55)')}/>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <svg width={160} height={160} viewBox={`0 0 160 160`}>
          {Array.from({ length: visibleFossils }).map((_, i) => {
            const r = fossilBaseR + i * FOSSIL_SPACING;
            return (
              <circle key={i} cx={cx} cy={cy} r={r} fill="none"
                stroke={ringColors[(visibleFossils - 1 - i) % 10]}
                strokeWidth={FOSSIL_SW}
                opacity={0.3 + (i / visibleFossils) * 0.5}
              />
            );
          })}
          <circle cx={cx} cy={cy} r={R} fill="none" stroke="rgba(255,255,255,0.10)" strokeWidth={SW}/>
          <circle cx={cx} cy={cy} r={R} fill="none" stroke={ringColors[visibleFossils % 10]}
            strokeWidth={SW} strokeLinecap="round"
            strokeDasharray={`${C * RV_DATA.fill} ${C}`}
            transform={`rotate(-90 ${cx} ${cy})`}/>
          <text x={cx} y={cy + 2} textAnchor="middle" fontSize="20" fontWeight="900" fill="#fff" letterSpacing="-0.5">
            {(RV_DATA.rings + RV_DATA.fill).toFixed(1).replace('.', ',')}
          </text>
          <text x={cx} y={cy + 18} textAnchor="middle" fontSize="8" fontWeight="700" fill="rgba(255,255,255,0.55)" letterSpacing="0.5">
            ABOS PAYÉS
          </text>
        </svg>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.65)', textAlign: 'center', fontWeight: 600 }}>
          {RV_DATA.month} € économisés ce mois
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// V2 — Coin Stack (3D stacked cabécoins growing upward)
// ─────────────────────────────────────────────────────────────
function RoiV2_CoinStack() {
  const stacks = 3; // number of stacks
  const heights = [4, 7, 5]; // coins per stack
  return (
    <div style={rvCard('#1A1F3A', 'rgba(255,184,0,0.55)', 'rgba(40,28,8,0.85)')}>
      <div style={rvHalo('rgba(255,184,0,0.40)')}/>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', height: '100%', justifyContent: 'space-between', padding: '6px 0' }}>
        <div style={{ textAlign: 'center', zIndex: 1 }}>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.8px', color: 'rgba(255,184,0,0.85)', textTransform: 'uppercase' }}>Total économisé</div>
          <div style={{ fontSize: 28, fontWeight: 900, color: '#fff', letterSpacing: '-0.8px', lineHeight: 1, marginTop: 2 }}>
            {RV_DATA.total}€
          </div>
        </div>
        <svg width="160" height="92" viewBox="0 0 160 92" style={{ marginTop: -2 }}>
          <defs>
            <linearGradient id="coinFace" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0%" stopColor="#FFE176"/>
              <stop offset="50%" stopColor="#FFB800"/>
              <stop offset="100%" stopColor="#C8860A"/>
            </linearGradient>
          </defs>
          {Array.from({ length: stacks }).map((_, sIdx) => {
            const h = heights[sIdx];
            const x = 28 + sIdx * 50;
            return Array.from({ length: h }).map((_, c) => {
              const y = 80 - c * 7;
              return (
                <g key={`${sIdx}-${c}`}>
                  <ellipse cx={x} cy={y + 2} rx="18" ry="4" fill="rgba(0,0,0,0.4)"/>
                  <ellipse cx={x} cy={y} rx="18" ry="5.5" fill="url(#coinFace)" stroke="#8F5E00" strokeWidth="0.6"/>
                  {c === h - 1 && <text x={x} y={y + 2} textAnchor="middle" fontSize="7" fontWeight="900" fill="#5C3D00">¢</text>}
                </g>
              );
            });
          })}
        </svg>
        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.55)', fontWeight: 600, zIndex: 1 }}>
          +{RV_DATA.month}€ ce mois
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// V3 — Treasure Chest with overflowing coins
// ─────────────────────────────────────────────────────────────
function RoiV3_Chest() {
  return (
    <div style={rvCard('#2A1F3A', 'rgba(168,85,247,0.55)', 'rgba(40,15,80,0.85)')}>
      <div style={rvHalo('rgba(168,85,247,0.40)')}/>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', height: '100%', justifyContent: 'space-between', padding: '4px 0' }}>
        <div style={{ textAlign: 'center', zIndex: 1 }}>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.8px', color: 'rgba(196,181,253,0.85)', textTransform: 'uppercase' }}>Trésor</div>
          <div style={{ fontSize: 26, fontWeight: 900, color: '#fff', letterSpacing: '-0.8px', lineHeight: 1, marginTop: 2 }}>
            {RV_DATA.total}€
          </div>
        </div>
        <svg width="140" height="100" viewBox="0 0 140 100">
          <defs>
            <linearGradient id="chestWood" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#7C4A1E"/>
              <stop offset="100%" stopColor="#3E2410"/>
            </linearGradient>
            <linearGradient id="chestCoin" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0%" stopColor="#FFE176"/>
              <stop offset="100%" stopColor="#C8860A"/>
            </linearGradient>
          </defs>
          {/* coin spill behind chest */}
          {[[20, 80], [115, 78], [30, 88], [110, 88]].map(([x, y], i) =>
            <ellipse key={`b-${i}`} cx={x} cy={y} rx="6" ry="5" fill="url(#chestCoin)" stroke="#8F5E00" strokeWidth="0.6"/>
          )}
          {/* chest body */}
          <rect x="35" y="50" width="70" height="42" rx="5" fill="url(#chestWood)" stroke="#1F1408" strokeWidth="1.5"/>
          <rect x="35" y="50" width="70" height="42" rx="5" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="0.8"/>
          {/* chest lid (open) */}
          <path d="M30 50 Q30 25 70 22 Q110 25 110 50 L110 56 Q110 50 70 47 Q30 50 30 56 Z" fill="url(#chestWood)" stroke="#1F1408" strokeWidth="1.5"/>
          {/* iron straps */}
          <rect x="35" y="62" width="70" height="3" fill="#1F1408"/>
          <rect x="65" y="50" width="10" height="42" fill="#1F1408"/>
          {/* lock */}
          <rect x="65" y="68" width="10" height="9" fill="#FFB800" stroke="#1F1408" strokeWidth="1"/>
          {/* coins overflowing top */}
          {[[50, 44], [62, 38], [72, 36], [85, 40], [56, 48], [78, 48], [90, 44]].map(([x, y], i) =>
            <ellipse key={i} cx={x} cy={y} rx="6" ry="5" fill="url(#chestCoin)" stroke="#8F5E00" strokeWidth="0.6"/>
          )}
          {/* sparkle */}
          <text x="100" y="34" fontSize="10" fill="#fff">✨</text>
          <text x="32" y="40" fontSize="8" fill="#fff">✨</text>
        </svg>
        <div style={{ fontSize: 10, color: '#C4B5FD', fontWeight: 700, zIndex: 1 }}>
          +{RV_DATA.month}€ ce mois
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// V4 — Bar chart (last 6 months)
// ─────────────────────────────────────────────────────────────
function RoiV4_BarChart() {
  const months = [
    { m: 'Déc', v: 0.45 },
    { m: 'Jan', v: 0.62 },
    { m: 'Fév', v: 0.38 },
    { m: 'Mar', v: 0.78 },
    { m: 'Avr', v: 0.55 },
    { m: 'Mai', v: 0.92, current: true },
  ];
  return (
    <div style={rvCard('#0F2A3D', 'rgba(103,232,249,0.55)', 'rgba(8,40,56,0.85)')}>
      <div style={rvHalo('rgba(103,232,249,0.35)')}/>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', padding: '4px 6px', zIndex: 1 }}>
        <div>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.8px', color: 'rgba(103,232,249,0.85)', textTransform: 'uppercase' }}>Économies · 6 mois</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 2 }}>
            <span style={{ fontSize: 26, fontWeight: 900, color: '#fff', letterSpacing: '-0.8px', lineHeight: 1 }}>{RV_DATA.month}€</span>
            <span style={{ fontSize: 10, fontWeight: 700, color: '#67E8F9' }}>↑ +12%</span>
          </div>
        </div>
        <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 4, marginTop: 8, paddingBottom: 14, position: 'relative' }}>
          {months.map((mo, i) => (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, height: '100%', justifyContent: 'flex-end' }}>
              <div style={{
                width: '100%',
                height: `${mo.v * 100}%`,
                borderRadius: '4px 4px 0 0',
                background: mo.current
                  ? 'linear-gradient(180deg, #67E8F9, #22D3EE)'
                  : 'linear-gradient(180deg, rgba(103,232,249,0.45), rgba(103,232,249,0.18))',
                border: mo.current ? '1px solid #A5F3FC' : '1px solid rgba(103,232,249,0.25)',
                boxShadow: mo.current ? '0 0 12px rgba(103,232,249,0.6)' : 'none',
              }}/>
              <div style={{ fontSize: 8, color: mo.current ? '#67E8F9' : 'rgba(255,255,255,0.45)', fontWeight: mo.current ? 900 : 700, marginTop: 4, letterSpacing: '0.4px' }}>
                {mo.m}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// V5 — Tirelire évolutive : 5 paliers selon économies cumulées
// Tier 0  : 0–25€    bocal vide en verre
// Tier 1  : 25–100€  bocal qui se remplit
// Tier 2  : 100–300€ cochon rose classique
// Tier 3  : 300–800€ cochon doré
// Tier 4  : 800€+    cochon couronné (king pig)
// ─────────────────────────────────────────────────────────────
function getJarTier(totalEur) {
  if (totalEur < 25)  return { id: 0, label: 'Bocal',           next: 25,  threshold: 0 };
  if (totalEur < 100) return { id: 1, label: 'Bocal rempli',    next: 100, threshold: 25 };
  if (totalEur < 300) return { id: 2, label: 'Cochon rose',     next: 300, threshold: 100 };
  if (totalEur < 800) return { id: 3, label: 'Cochon doré',     next: 800, threshold: 300 };
  return                        { id: 4, label: 'Cochon roi',     next: null, threshold: 800 };
}

function JarShape({ tier, fillPct = 62 }) {
  // common defs for fill gradients
  const defs = (
    <defs>
      <linearGradient id="jarGold" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stopColor="#FFE176"/>
        <stop offset="50%" stopColor="#FFB800"/>
        <stop offset="100%" stopColor="#C8860A"/>
      </linearGradient>
      <radialGradient id="pigPink" cx="50%" cy="40%" r="80%">
        <stop offset="0%" stopColor="#FFC4D8"/>
        <stop offset="100%" stopColor="#FF6B9D"/>
      </radialGradient>
      <radialGradient id="pigGold" cx="50%" cy="40%" r="80%">
        <stop offset="0%" stopColor="#FFE176"/>
        <stop offset="100%" stopColor="#C8860A"/>
      </radialGradient>
    </defs>
  );

  if (tier === 0 || tier === 1) {
    // Glass jar
    const fy = 25 + (75 * (100 - fillPct) / 100);
    return (
      <svg width="110" height="120" viewBox="0 0 100 120">
        {defs}
        <clipPath id="jarClipEv"><path d="M20 25 L20 90 Q20 100 30 100 L70 100 Q80 100 80 90 L80 25 Z"/></clipPath>
        {/* jar glass */}
        <path d="M20 25 L20 90 Q20 100 30 100 L70 100 Q80 100 80 90 L80 25 Z"
          fill="rgba(255,255,255,0.05)" stroke="rgba(255,255,255,0.45)" strokeWidth="1.5"/>
        {tier === 1 && (
          <g clipPath="url(#jarClipEv)">
            <rect x="20" y={fy} width="60" height="80" fill="url(#jarGold)"/>
            <ellipse cx="50" cy={fy} rx="30" ry="3" fill="#FFE176" opacity="0.8"/>
            <circle cx="32" cy="78" r="2" fill="rgba(255,255,255,0.5)"/>
            <circle cx="60" cy="84" r="1.5" fill="rgba(255,255,255,0.5)"/>
            <circle cx="48" cy="92" r="2.5" fill="rgba(255,255,255,0.5)"/>
          </g>
        )}
        {/* lid */}
        <rect x="22" y="18" width="56" height="10" rx="2" fill="#FF6B9D" stroke="rgba(0,0,0,0.4)" strokeWidth="1"/>
        <rect x="40" y="20" width="20" height="3" rx="1" fill="rgba(0,0,0,0.6)"/>
        {/* shine */}
        <path d="M28 35 L28 80" stroke="rgba(255,255,255,0.35)" strokeWidth="2" strokeLinecap="round"/>
        {tier === 1 && <text x="50" y={Math.max(48, fy + 14)} textAnchor="middle" fontSize="13" fontWeight="900" fill="#5C3D00">{fillPct}%</text>}
        {tier === 0 && <text x="50" y="62" textAnchor="middle" fontSize="9" fontWeight="800" fill="rgba(255,255,255,0.45)" letterSpacing="0.4">VIDE</text>}
      </svg>
    );
  }

  // Pig variants — share body silhouette
  const isGold = tier === 3;
  const isKing = tier === 4;
  const bodyFill = isGold || isKing ? 'url(#pigGold)' : 'url(#pigPink)';
  const accent  = isGold || isKing ? '#8F5E00' : '#C04A78';
  const ear     = isGold || isKing ? '#FFB800' : '#FF8FB3';
  const hoof    = isGold || isKing ? '#5C3D00' : '#8F3D62';

  return (
    <svg width="120" height="120" viewBox="0 0 130 120">
      {defs}
      {/* legs */}
      <rect x="32" y="84" width="9" height="14" rx="2" fill={hoof}/>
      <rect x="56" y="86" width="9" height="14" rx="2" fill={hoof}/>
      <rect x="80" y="86" width="9" height="14" rx="2" fill={hoof}/>
      <rect x="100" y="84" width="9" height="14" rx="2" fill={hoof}/>
      {/* body */}
      <ellipse cx="68" cy="64" rx="48" ry="32" fill={bodyFill} stroke={accent} strokeWidth="1.5"/>
      {/* belly highlight */}
      <ellipse cx="60" cy="50" rx="22" ry="9" fill="rgba(255,255,255,0.25)"/>
      {/* head (slight bump on right) */}
      <circle cx="106" cy="58" r="20" fill={bodyFill} stroke={accent} strokeWidth="1.5"/>
      {/* snout */}
      <ellipse cx="118" cy="62" rx="9" ry="7" fill={isGold || isKing ? '#FFB800' : '#FFA8C4'} stroke={accent} strokeWidth="1"/>
      <circle cx="115" cy="62" r="1.4" fill={accent}/>
      <circle cx="121" cy="62" r="1.4" fill={accent}/>
      {/* eye */}
      <circle cx="103" cy="52" r="2.2" fill="#1F1408"/>
      <circle cx="103.7" cy="51.3" r="0.7" fill="#fff"/>
      {/* ear */}
      <path d="M98 38 L102 30 L108 42 Z" fill={ear} stroke={accent} strokeWidth="1"/>
      {/* curly tail */}
      <path d="M22 58 q-6 -4 -3 -10 q3 -6 9 -3" fill="none" stroke={accent} strokeWidth="2.5" strokeLinecap="round"/>
      {/* coin slot on top */}
      <rect x="60" y="32" width="22" height="3.5" rx="1.5" fill="rgba(0,0,0,0.6)"/>
      {/* king crown */}
      {isKing && (
        <g>
          <path d="M50 22 L56 12 L64 18 L72 10 L80 18 L88 12 L94 22 Z" fill="#FFD60A" stroke="#5C3D00" strokeWidth="1.2" strokeLinejoin="round"/>
          <rect x="50" y="22" width="44" height="4" fill="#FFB800" stroke="#5C3D00" strokeWidth="1"/>
          <circle cx="56" cy="12" r="2" fill="#FF4757"/>
          <circle cx="72" cy="10" r="2" fill="#22D3EE"/>
          <circle cx="88" cy="12" r="2" fill="#A78BFA"/>
        </g>
      )}
      {/* sparkle for gold/king */}
      {(isGold || isKing) && (
        <g opacity="0.9">
          <text x="20" y="80" fontSize="10">✨</text>
          <text x="115" y="40" fontSize="9">✨</text>
        </g>
      )}
    </svg>
  );
}

function RoiV5_Jar({ totalEur = 142.80, fillPct = 62 }) {
  const tier = getJarTier(totalEur);
  const totalStr = totalEur.toFixed(2).replace('.', ',');
  const nextDelta = tier.next ? Math.max(0, tier.next - totalEur) : 0;
  // "Pleine" = palier max OU à <10% du palier suivant
  const span = tier.next ? tier.next - tier.threshold : 1;
  const progress = tier.next ? (totalEur - tier.threshold) / span : 1;
  const isFull = !tier.next || progress >= 0.9;

  return (
    <div style={rvCard('#3D1F2A', 'rgba(255,107,157,0.55)', 'rgba(80,20,40,0.85)')}>
      {/* Subtle base halo — always on */}
      <div style={{
        position: 'absolute', left: '50%', top: '52%',
        transform: 'translate(-50%, -50%)',
        width: 170, height: 170, borderRadius: '50%',
        background: 'radial-gradient(closest-side, rgba(255,107,157,0.40), transparent 72%)',
        pointerEvents: 'none',
        opacity: 0.7,
      }}/>

      {/* FULL EFFECTS — only when tirelire is full */}
      {isFull && <>
        <div style={{
          position: 'absolute', left: '50%', top: '52%',
          transform: 'translate(-50%, -50%)',
          width: 260, height: 260, borderRadius: '50%',
          background: 'radial-gradient(closest-side, rgba(255,184,0,0.32), transparent 70%)',
          pointerEvents: 'none',
          animation: 'jarHaloPulse 2.4s ease-in-out infinite',
        }}/>
        <div style={{
          position: 'absolute', left: '50%', top: '52%',
          transform: 'translate(-50%, -50%)',
          width: 220, height: 220,
          background: 'conic-gradient(from 0deg, transparent 0deg, rgba(255,184,0,0.18) 12deg, transparent 24deg, transparent 90deg, rgba(255,184,0,0.14) 100deg, transparent 112deg, transparent 180deg, rgba(255,184,0,0.18) 192deg, transparent 204deg, transparent 270deg, rgba(255,184,0,0.14) 280deg, transparent 292deg)',
          borderRadius: '50%',
          pointerEvents: 'none',
          animation: 'jarRayspin 14s linear infinite',
          opacity: 0.7,
          mixBlendMode: 'screen',
        }}/>
        <span style={{ position: 'absolute', left: 14, top: 30, fontSize: 12, opacity: 0.85, animation: 'jarSparkle 2.2s ease-in-out infinite', animationDelay: '0s' }}>✨</span>
        <span style={{ position: 'absolute', right: 18, top: 50, fontSize: 10, opacity: 0.85, animation: 'jarSparkle 2.6s ease-in-out infinite', animationDelay: '0.7s' }}>✨</span>
        <span style={{ position: 'absolute', left: 22, bottom: 60, fontSize: 9, opacity: 0.85, animation: 'jarSparkle 2.8s ease-in-out infinite', animationDelay: '1.3s' }}>✨</span>
        <span style={{ position: 'absolute', right: 14, bottom: 80, fontSize: 11, opacity: 0.85, animation: 'jarSparkle 2.4s ease-in-out infinite', animationDelay: '0.4s' }}>✨</span>
      </>}

      {/* Falling coins — always animated, regardless of jar fullness */}
      <span style={{ position: 'absolute', left: '38%', top: -4, fontSize: 12, animation: 'jarCoinFall 3.2s ease-in infinite', animationDelay: '0s', zIndex: 2 }}>🪙</span>
      <span style={{ position: 'absolute', left: '58%', top: -4, fontSize: 11, animation: 'jarCoinFall 3.8s ease-in infinite', animationDelay: '1.4s', zIndex: 2 }}>🪙</span>
      <span style={{ position: 'absolute', left: '48%', top: -4, fontSize: 10, animation: 'jarCoinFall 4.1s ease-in infinite', animationDelay: '2.6s', zIndex: 2 }}>🪙</span>

      {/* DIMMED EFFECTS — when not full: 5 sparkles blinking out of sync */}
      {!isFull && <>
        <span style={{ position: 'absolute', left: 14, top: 30,    fontSize: 11, opacity: 0.6, animation: 'jarSparkle 5.2s ease-in-out infinite', animationDelay: '0s'   }}>✨</span>
        <span style={{ position: 'absolute', right: 18, top: 56,   fontSize: 9,  opacity: 0.6, animation: 'jarSparkle 6.4s ease-in-out infinite', animationDelay: '1.5s' }}>✨</span>
        <span style={{ position: 'absolute', left: 24, bottom: 70, fontSize: 10, opacity: 0.6, animation: 'jarSparkle 5.8s ease-in-out infinite', animationDelay: '2.8s' }}>✨</span>
        <span style={{ position: 'absolute', right: 14, bottom: 92, fontSize: 12, opacity: 0.6, animation: 'jarSparkle 7.0s ease-in-out infinite', animationDelay: '4.1s' }}>✨</span>
        <span style={{ position: 'absolute', left: '50%', top: 14,  fontSize: 8,  opacity: 0.5, animation: 'jarSparkle 5.5s ease-in-out infinite', animationDelay: '3.6s' }}>✨</span>
      </>}

      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', height: '100%', justifyContent: 'space-between', padding: '4px 0', zIndex: 1 }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.8px', color: 'rgba(255,107,157,0.85)', textTransform: 'uppercase' }}>Tirelire</div>
          <div style={{ fontSize: 26, fontWeight: 900, color: '#fff', letterSpacing: '-0.8px', lineHeight: 1, marginTop: 2,
            textShadow: isFull ? '0 0 20px rgba(255,184,0,0.4), 0 2px 0 rgba(0,0,0,0.5)' : '0 2px 0 rgba(0,0,0,0.5)' }}>
            {totalStr}€
          </div>
        </div>
        <div style={{ filter: isFull ? 'drop-shadow(0 0 14px rgba(255,184,0,0.45))' : 'drop-shadow(0 4px 8px rgba(0,0,0,0.4))' }}>
          <JarShape tier={tier.id} fillPct={fillPct}/>
        </div>
        <div style={{ fontSize: 10, color: '#FF6B9D', fontWeight: 700, textAlign: 'center', lineHeight: 1.3 }}>
          {tier.next
            ? <>Plus que <b style={{ color: '#fff' }}>{nextDelta.toFixed(0)}€</b> → palier suivant</>
            : <>Palier max atteint 👑</>
          }
        </div>
      </div>
    </div>
  );
}

// expose helpers
window.JarShape = JarShape;
window.getJarTier = getJarTier;

// ─────────────────────────────────────────────────────────────
// V6 — Big Number, minimal (gold leaf accents only)
// ─────────────────────────────────────────────────────────────
function RoiV6_BigNumber() {
  return (
    <div style={rvCard('#0F1A1F', 'rgba(255,184,0,0.45)', 'rgba(40,28,8,0.85)')}>
      <div style={rvHalo('rgba(255,184,0,0.30)')}/>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', height: '100%', justifyContent: 'center', gap: 6, zIndex: 1, padding: '8px 12px' }}>
        <div style={{ fontSize: 9, fontWeight: 800, letterSpacing: '1.4px', color: 'rgba(255,184,0,0.85)', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span>◆</span> Économisé <span>◆</span>
        </div>
        <div style={{
          fontSize: 44, fontWeight: 900, color: '#FFB800',
          letterSpacing: '-1.6px', lineHeight: 1,
          textShadow: '0 2px 0 rgba(0,0,0,0.6), 0 0 24px rgba(255,184,0,0.35)',
          fontFamily: 'Inter, system-ui',
        }}>
          {RV_DATA.total}€
        </div>
        <div style={{ height: 1, width: '60%', background: 'linear-gradient(90deg, transparent, rgba(255,184,0,0.6), transparent)' }}/>
        <div style={{ display: 'flex', gap: 14, marginTop: 2 }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 14, fontWeight: 900, color: '#fff' }}>+{RV_DATA.month}€</div>
            <div style={{ fontSize: 8, fontWeight: 700, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.4px', textTransform: 'uppercase' }}>Ce mois</div>
          </div>
          <div style={{ width: 1, background: 'rgba(255,255,255,0.15)' }}/>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 14, fontWeight: 900, color: '#fff' }}>{RV_DATA.rings}<span style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)' }}>/{RV_DATA.ringsTotal}</span></div>
            <div style={{ fontSize: 8, fontWeight: 700, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.4px', textTransform: 'uppercase' }}>Anneaux</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// V7 — Orbital coins (constellation of cabécoins around a core)
// ─────────────────────────────────────────────────────────────
function RoiV7_Orbital() {
  const orbitR = [30, 48, 64];
  const counts = [3, 5, 6];
  return (
    <div style={rvCard('#1F1538', 'rgba(167,139,250,0.55)', 'rgba(40,15,80,0.85)')}>
      <div style={rvHalo('rgba(167,139,250,0.40)')}/>
      <div style={{ position: 'relative', width: '100%', height: '100%', zIndex: 1 }}>
        <svg width="100%" height="100%" viewBox="0 0 200 220" preserveAspectRatio="xMidYMid meet" style={{ position: 'absolute', inset: 0 }}>
          <defs>
            <radialGradient id="orbCore" cx="50%" cy="50%">
              <stop offset="0%" stopColor="#FFE176"/>
              <stop offset="60%" stopColor="#FFB800"/>
              <stop offset="100%" stopColor="#8F5E00"/>
            </radialGradient>
            <linearGradient id="orbCoin" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0%" stopColor="#FFE176"/>
              <stop offset="100%" stopColor="#C8860A"/>
            </linearGradient>
          </defs>
          {/* orbits */}
          {orbitR.map((r, i) => (
            <circle key={`o-${i}`} cx="100" cy="110" r={r} fill="none"
              stroke="rgba(167,139,250,0.20)" strokeWidth="0.8" strokeDasharray="2 3"/>
          ))}
          {/* coins on each orbit */}
          {orbitR.map((r, oi) => {
            const n = counts[oi];
            return Array.from({ length: n }).map((_, i) => {
              const ang = (i / n) * 2 * Math.PI + (oi * 0.4);
              const x = 100 + Math.cos(ang) * r;
              const y = 110 + Math.sin(ang) * r;
              return (
                <g key={`c-${oi}-${i}`}>
                  <circle cx={x} cy={y + 1.5} r="5.5" fill="rgba(0,0,0,0.5)"/>
                  <circle cx={x} cy={y} r="5.5" fill="url(#orbCoin)" stroke="#8F5E00" strokeWidth="0.5"/>
                </g>
              );
            });
          })}
          {/* core */}
          <circle cx="100" cy="110" r="22" fill="url(#orbCore)" stroke="#5C3D00" strokeWidth="1.5"/>
          <text x="100" y="106" textAnchor="middle" fontSize="9" fontWeight="800" fill="#5C3D00" letterSpacing="0.5">¢</text>
          <text x="100" y="118" textAnchor="middle" fontSize="11" fontWeight="900" fill="#3D2510">{RV_DATA.total.split(',')[0]}</text>
        </svg>
        <div style={{ position: 'absolute', top: 6, left: 0, right: 0, textAlign: 'center', zIndex: 2 }}>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.8px', color: 'rgba(196,181,253,0.85)', textTransform: 'uppercase' }}>Univers ¢</div>
        </div>
        <div style={{ position: 'absolute', bottom: 8, left: 0, right: 0, textAlign: 'center', zIndex: 2 }}>
          <div style={{ fontSize: 14, fontWeight: 900, color: '#fff', letterSpacing: '-0.5px' }}>{RV_DATA.total}€</div>
          <div style={{ fontSize: 9, fontWeight: 700, color: '#C4B5FD' }}>+{RV_DATA.month}€ ce mois</div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// V8 — Receipt scroll (paper unfurling with running total)
// ─────────────────────────────────────────────────────────────
function RoiV8_Receipt() {
  const lines = [
    ['Lidl Charonne', '-2,40'],
    ['Carrefour', '-1,10'],
    ['Auchan', '-0,80'],
    ['Monoprix', '-1,50'],
  ];
  return (
    <div style={rvCard('#1F2A1F', 'rgba(77,212,179,0.55)', 'rgba(15,60,40,0.85)')}>
      <div style={rvHalo('rgba(77,212,179,0.35)')}/>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', padding: '6px 8px', zIndex: 1 }}>
        <div style={{ textAlign: 'center', marginBottom: 6 }}>
          <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.8px', color: 'rgba(134,239,205,0.85)', textTransform: 'uppercase' }}>Ticket des économies</div>
        </div>
        {/* Receipt paper */}
        <div style={{
          flex: 1,
          background: 'linear-gradient(180deg, #F5EFD8 0%, #E8DFC3 100%)',
          borderRadius: '4px 4px 0 0',
          padding: '10px 10px 6px',
          fontFamily: 'ui-monospace, monospace',
          fontSize: 9,
          color: '#3D2510',
          display: 'flex', flexDirection: 'column', gap: 3,
          boxShadow: 'inset 0 -8px 8px rgba(0,0,0,0.06)',
          position: 'relative',
        }}>
          <div style={{ textAlign: 'center', fontWeight: 800, letterSpacing: '0.4px', borderBottom: '1px dashed #8B7649', paddingBottom: 3 }}>
            *** ÉCONOMIES MAI ***
          </div>
          {lines.map(([name, amt], i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>{name}</span>
              <span style={{ fontWeight: 700 }}>{amt}€</span>
            </div>
          ))}
          <div style={{ borderTop: '1px dashed #8B7649', paddingTop: 3, marginTop: 'auto', display: 'flex', justifyContent: 'space-between', fontWeight: 900, fontSize: 11 }}>
            <span>TOTAL</span>
            <span style={{ color: '#1FA08A' }}>-{RV_DATA.month}€</span>
          </div>
        </div>
        {/* zigzag tear */}
        <svg width="100%" height="6" viewBox="0 0 200 6" preserveAspectRatio="none">
          <polygon points="0,0 10,6 20,0 30,6 40,0 50,6 60,0 70,6 80,0 90,6 100,0 110,6 120,0 130,6 140,0 150,6 160,0 170,6 180,0 190,6 200,0 200,0 0,0" fill="#E8DFC3"/>
        </svg>
        <div style={{ fontSize: 9, fontWeight: 700, color: 'rgba(134,239,205,0.9)', textAlign: 'center', marginTop: 4 }}>
          Total cumulé · {RV_DATA.total}€
        </div>
      </div>
    </div>
  );
}

// Shared card chrome
function rvCard(bg, border, shadow3d) {
  return {
    width: '100%',
    height: '100%',
    borderRadius: 20,
    background: bg,
    border: `1.5px solid ${border}`,
    boxShadow: `0 5px 0 ${shadow3d}, 0 16px 32px rgba(0,0,0,0.4), inset 0 2px 0 rgba(255,255,255,0.18)`,
    padding: 14,
    position: 'relative',
    overflow: 'hidden',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  };
}
function rvHalo(color) {
  return {
    position: 'absolute',
    left: '50%', top: '50%',
    transform: 'translate(-50%, -50%)',
    width: 200, height: 200,
    borderRadius: '50%',
    background: `radial-gradient(closest-side, ${color}, transparent 72%)`,
    pointerEvents: 'none',
    opacity: 0.65,
  };
}

window.RoiVariants = {
  RoiV1_Fossil, RoiV2_CoinStack, RoiV3_Chest, RoiV4_BarChart,
  RoiV5_Jar, RoiV6_BigNumber, RoiV7_Orbital, RoiV8_Receipt,
};
