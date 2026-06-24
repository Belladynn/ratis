// Ratis — Scan, Produit, Profil tabs.
const { useState: useStateS } = React;
const { GameButton: GBS, PageTitle: PTS, SegmentedTabs: STS, EmptyCard: ECS, screenCardBase: cardS } = window.RatisShared;

// ─────────────────────────────────────────────────────────────────────
// SCAN — camera-style fullscreen with mode switch + capture button
// ─────────────────────────────────────────────────────────────────────
const SCAN_HISTORY_DEMO = [
  { id: 'h1', store: 'Lidl Charonne',     amount: 23.45, status: 'done',     when: 'Il y a 2h',  cab: 35 },
  { id: 'h2', store: 'Carrefour Voltaire', amount: 41.20, status: 'processing', when: 'Hier',     cab: null },
  { id: 'h3', store: 'Magasin inconnu',    amount: 0,     status: 'unknown',  when: 'Hier',      cab: null },
];

function ScanModeSwitch({ mode, onChange }) {
  const modes = [
    { id: 'receipt',  icon: '🧾', label: 'Ticket' },
    { id: 'label',    icon: '🏷', label: 'Étiquette' },
    { id: 'barcode',  icon: '⫴', label: 'Code-barre' },
  ];
  return (
    <div style={{
      display: 'flex',
      background: 'rgba(0,0,0,0.55)',
      backdropFilter: 'blur(12px)',
      border: '1.5px solid rgba(255,255,255,0.15)',
      borderRadius: 16,
      padding: 4,
      gap: 2,
    }}>
      {modes.map(m => {
        const on = mode === m.id;
        return (
          <button key={m.id} onClick={() => onChange(m.id)} style={{
            flex: 1, padding: '8px 12px',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            fontSize: 11, fontWeight: 800, letterSpacing: '-0.1px',
            color: on ? '#0A2200' : 'rgba(255,255,255,0.7)',
            background: on ? 'linear-gradient(180deg, #FFE066, #FFB800)' : 'transparent',
            border: on ? '1.5px solid #B47800' : '1.5px solid transparent',
            boxShadow: on ? '0 2px 0 #8F5E00, inset 0 1px 0 rgba(255,255,255,0.4)' : 'none',
            borderRadius: 12,
            cursor: 'pointer',
            fontFamily: 'inherit',
            textShadow: on ? '0 1px 0 rgba(255,255,255,0.3)' : 'none',
          }}>
            <span style={{ fontSize: 13 }}>{m.icon}</span>
            <span>{m.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function ScanCaptureButton({ onPress, mode }) {
  const [pressed, setPressed] = useStateS(false);
  return (
    <button
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      onClick={onPress}
      style={{
        width: 78, height: 78, borderRadius: 39,
        background: 'linear-gradient(180deg, #FFEB85 0%, #FFB800 55%, #D48F00 100%)',
        border: '4px solid rgba(0,0,0,0.85)',
        boxShadow: pressed
          ? '0 1px 0 #8F5E00, 0 4px 8px rgba(255,184,0,0.5), inset 0 2px 0 rgba(255,255,255,0.5)'
          : '0 6px 0 #8F5E00, 0 14px 28px rgba(255,184,0,0.55), inset 0 2px 0 rgba(255,255,255,0.55)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer',
        transform: pressed ? 'translateY(5px)' : 'translateY(0)',
        transition: 'transform 0.08s, box-shadow 0.08s',
        padding: 0,
      }}>
      <div style={{
        width: 60, height: 60, borderRadius: 30,
        border: '3px solid rgba(0,0,0,0.85)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <span style={{ fontSize: 26 }}>📷</span>
      </div>
    </button>
  );
}

function ScanHistoryStrip({ items, onMore }) {
  const colorFor = (st) => st === 'done' ? '#4DD4B3' : st === 'processing' ? '#FFB800' : st === 'unknown' ? '#FB7185' : '#fff';
  return (
    <div style={{
      background: 'rgba(0,0,0,0.5)',
      backdropFilter: 'blur(12px)',
      border: '1.5px solid rgba(255,255,255,0.12)',
      borderRadius: 16,
      padding: 8,
      flex: 1,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, paddingLeft: 4 }}>
        <span style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.65)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Récents</span>
        <button onClick={onMore} style={{
          marginLeft: 'auto',
          background: 'transparent', border: 'none',
          fontSize: 10, fontWeight: 800, color: '#FFB800',
          cursor: 'pointer', padding: 0, fontFamily: 'inherit',
        }}>Voir tout →</button>
      </div>
      {items.slice(0, 3).map(it => (
        <div key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 4px' }}>
          <div style={{
            width: 6, height: 6, borderRadius: 3,
            background: colorFor(it.status),
            boxShadow: '0 0 6px ' + colorFor(it.status),
            flexShrink: 0,
          }}/>
          <div style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
            <div style={{ fontSize: 11, fontWeight: 800, color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', letterSpacing: '-0.2px' }}>{it.store}</div>
          </div>
          {it.status === 'done' && it.cab != null && (
            <span style={{ fontSize: 10, fontWeight: 900, color: '#FFB800' }}>+{it.cab}</span>
          )}
          {it.status === 'processing' && (
            <span style={{ fontSize: 9, fontWeight: 700, color: '#FFB800' }}>OCR…</span>
          )}
          {it.status === 'unknown' && (
            <span style={{ fontSize: 9, fontWeight: 700, color: '#FB7185' }}>?</span>
          )}
        </div>
      ))}
    </div>
  );
}

function ScanScreen({ showToast }) {
  const [mode, setMode] = useStateS('receipt');
  const [previewing, setPreviewing] = useStateS(false);
  const [batchCount, setBatchCount] = useStateS(0);

  const capture = () => {
    if (mode === 'receipt') setPreviewing(true);
    else if (mode === 'label') {
      setBatchCount(c => c + 1);
      showToast('Étiquette ajoutée · ' + (batchCount + 1) + ' photo' + (batchCount + 1 > 1 ? 's' : ''));
    } else {
      showToast('Code-barre détecté · 3 prix trouvés');
    }
  };

  const sendBatch = () => {
    showToast(batchCount + ' étiquettes envoyées · +' + (batchCount * 5) + ' cab');
    setBatchCount(0);
  };

  const confirmReceipt = () => {
    setPreviewing(false);
    showToast('Ticket envoyé · OCR en cours');
  };

  const HINT_TEXT = {
    receipt: 'Aligne le ticket dans le cadre',
    label:   'Cadre l\'étiquette de prix',
    barcode: 'Vise le code-barre',
  };

  return (
    <div style={{
      flex: 1, position: 'relative', overflow: 'hidden',
      background: 'radial-gradient(ellipse at center, #1a1410 0%, #050505 100%)',
    }}>
      {/* simulated camera preview */}
      <div style={{
        position: 'absolute', inset: 0,
        background:
          'radial-gradient(circle at 30% 20%, rgba(255,255,255,0.07), transparent 40%),' +
          'radial-gradient(circle at 70% 80%, rgba(255,255,255,0.05), transparent 50%),' +
          'linear-gradient(135deg, #1a1410 0%, #0a0a0a 50%, #100a08 100%)',
      }}>
        {/* faint grid */}
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage:
            'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),' +
            'linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)',
          backgroundSize: '40px 40px',
        }}/>
      </div>

      {/* viewfinder corners */}
      <div style={{
        position: 'absolute', inset: '20% 16% 28% 16%',
        pointerEvents: 'none',
      }}>
        {[
          { top: 0, left: 0, br: '0 0 0 0', borders: 'top left' },
          { top: 0, right: 0, br: '0 0 0 0', borders: 'top right' },
          { bottom: 0, left: 0, br: '0 0 0 0', borders: 'bottom left' },
          { bottom: 0, right: 0, br: '0 0 0 0', borders: 'bottom right' },
        ].map((c, i) => {
          const isTop = c.top === 0;
          const isLeft = c.left === 0;
          return (
            <div key={i} style={{
              position: 'absolute',
              top: isTop ? 0 : 'auto',
              bottom: isTop ? 'auto' : 0,
              left: isLeft ? 0 : 'auto',
              right: isLeft ? 'auto' : 0,
              width: 28, height: 28,
              borderTop: isTop ? '3px solid #FFB800' : 'none',
              borderBottom: isTop ? 'none' : '3px solid #FFB800',
              borderLeft: isLeft ? '3px solid #FFB800' : 'none',
              borderRight: isLeft ? 'none' : '3px solid #FFB800',
              borderRadius:
                (isTop && isLeft ? '8px 0 0 0' :
                 isTop && !isLeft ? '0 8px 0 0' :
                 !isTop && isLeft ? '0 0 0 8px' : '0 0 8px 0'),
              filter: 'drop-shadow(0 0 8px rgba(255,184,0,0.8))',
            }}/>
          );
        })}
        {/* hint */}
        <div style={{
          position: 'absolute', bottom: -36, left: '50%', transform: 'translateX(-50%)',
          padding: '6px 12px',
          background: 'rgba(0,0,0,0.7)',
          backdropFilter: 'blur(6px)',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 10,
          fontSize: 11, fontWeight: 700, color: '#fff',
          whiteSpace: 'nowrap',
        }}>{HINT_TEXT[mode]}</div>
      </div>

      {/* top overlay */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0,
        padding: '54px 14px 14px',
        display: 'flex', gap: 10,
        background: 'linear-gradient(180deg, rgba(11,11,16,0.85), rgba(11,11,16,0))',
      }}>
        <ScanHistoryStrip items={SCAN_HISTORY_DEMO} onMore={() => showToast('Historique scans')}/>
        {mode === 'label' && batchCount > 0 && (
          <GBS color="gold" size="md" onClick={sendBatch}>Envoyer {batchCount} →</GBS>
        )}
      </div>

      {/* bottom overlay: mode switch + capture */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        padding: '14px 14px 20px',
        display: 'flex', flexDirection: 'column', gap: 14, alignItems: 'center',
        background: 'linear-gradient(0deg, rgba(11,11,16,0.85), rgba(11,11,16,0))',
      }}>
        <div style={{ width: '100%' }}>
          <ScanModeSwitch mode={mode} onChange={setMode}/>
        </div>
        <ScanCaptureButton onPress={capture} mode={mode}/>
        <div style={{ fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.5px', textTransform: 'uppercase' }}>
          {mode === 'receipt' ? '+50 cab par ticket' : mode === 'label' ? '+5 cab par étiquette' : 'Comparaison instantanée'}
        </div>
      </div>

      {/* receipt preview overlay */}
      {previewing && (
        <div style={{
          position: 'absolute', inset: 0,
          background: 'rgba(0,0,0,0.85)',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          padding: 20, gap: 16, zIndex: 10,
        }}>
          <div style={{
            width: 200, height: 280,
            background: 'linear-gradient(180deg, #fff 0%, #f5f0e6 100%)',
            borderRadius: 12,
            border: '2px solid #FFB800',
            boxShadow: '0 12px 40px rgba(0,0,0,0.6), 0 0 0 4px rgba(255,184,0,0.2)',
            padding: 16, fontSize: 9, color: '#1a1410',
            fontFamily: 'monospace',
            lineHeight: 1.5,
          }}>
            <div style={{ textAlign: 'center', fontWeight: 900, marginBottom: 8 }}>LIDL CHARONNE</div>
            <div style={{ borderBottom: '1px dashed #1a1410', marginBottom: 6 }}/>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Pâtes 500g</span><span>1,85</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Lait 1L x2</span><span>2,10</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Bananes</span><span>1,49</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Tomates 250g</span><span>1,79</span></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Yaourts x4</span><span>2,95</span></div>
            <div style={{ borderBottom: '1px dashed #1a1410', margin: '6px 0' }}/>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 900 }}><span>TOTAL</span><span>10,18€</span></div>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <GBS color="slate" size="md" onClick={() => setPreviewing(false)}>↺ Reprendre</GBS>
            <GBS color="gold" size="md" onClick={confirmReceipt} icon="✓">Envoyer</GBS>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// PRODUIT — product detail with consensus price + nearby store list
// ─────────────────────────────────────────────────────────────────────
const PRODUCT_DEMO = {
  brand: 'NESPRESSO',
  name: 'Capsules Café Vivalto Lungo x10',
  ean: '7640110350683',
  emoji: '☕',
  bestPrice: 4.20,
  storesCount: 7,
  prices: [
    { id: 's1', name: 'Auchan Nation',       distance: 2.8, price: 4.20, best: true },
    { id: 's2', name: 'Carrefour Voltaire',  distance: 1.2, price: 4.50 },
    { id: 's3', name: 'Monoprix Bastille',   distance: 0.6, price: 4.95 },
    { id: 's4', name: 'Casino République',   distance: 1.5, price: 5.10 },
    { id: 's5', name: 'Franprix Charonne',   distance: 0.4, price: 5.25 },
    { id: 's6', name: 'G20 Faidherbe',       distance: 2.1, price: 5.40 },
    { id: 's7', name: 'Carrefour Express',   distance: 0.3, price: 5.60 },
  ],
};

function PriceRow({ s, last }) {
  const pct = ((s.price - PRODUCT_DEMO.bestPrice) / PRODUCT_DEMO.bestPrice) * 100;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '12px 14px',
      borderBottom: last ? 'none' : '1px solid rgba(255,255,255,0.06)',
      background: s.best ? 'rgba(255,184,0,0.08)' : 'transparent',
    }}>
      {s.best && (
        <div style={{
          width: 24, height: 24, borderRadius: 12, flexShrink: 0,
          background: 'linear-gradient(180deg, #FFE066, #FFB800)',
          border: '1.5px solid #B47800',
          boxShadow: '0 2px 0 #8F5E00, inset 0 1px 0 rgba(255,255,255,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 12,
        }}>👑</div>
      )}
      {!s.best && <div style={{ width: 24, height: 24, flexShrink: 0 }}/>}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: s.best ? '#FFB800' : '#fff', letterSpacing: '-0.2px' }}>{s.name}</div>
        <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>{s.distance.toFixed(1)} km</div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div style={{ fontSize: 14, fontWeight: 900, color: s.best ? '#FFB800' : '#fff', letterSpacing: '-0.3px' }}>
          {s.price.toFixed(2).replace('.',',')}€
        </div>
        {!s.best && pct > 0 && (
          <div style={{ fontSize: 9, fontWeight: 700, color: '#FB7185', marginTop: 1 }}>+{pct.toFixed(0)}%</div>
        )}
        {s.best && (
          <div style={{ fontSize: 9, fontWeight: 800, color: '#FFB800', marginTop: 1, letterSpacing: '0.4px', textTransform: 'uppercase' }}>Meilleur</div>
        )}
      </div>
    </div>
  );
}

function ProduitScreen({ showToast }) {
  const [tab, setTab] = useStateS('prices');
  const [fav, setFav] = useStateS(false);
  const p = PRODUCT_DEMO;

  return (
    <>
      <PTS
        title="Fiche produit"
        leftIcon={<button style={iconHeaderStyleS} onClick={() => showToast('Retour')}>←</button>}
        rightIcons={[
          <button key="fav" onClick={() => setFav(!fav)} style={{ ...iconHeaderStyleS, color: fav ? '#FB7185' : '#fff', fontSize: 16 }}>
            {fav ? '♥' : '♡'}
          </button>,
          <button key="share" style={iconHeaderStyleS}>↗</button>,
        ]}
      />
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 14px 24px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Hero — product image + title */}
        <div style={{
          ...cardS,
          padding: 16,
          display: 'flex', alignItems: 'center', gap: 14,
          background: 'linear-gradient(160deg, #2D2438 0%, #1F1A2E 100%)',
          border: '1.5px solid rgba(168,85,247,0.3)',
          boxShadow: '0 5px 0 rgba(60,30,100,0.55), 0 12px 22px rgba(0,0,0,0.4), inset 0 2px 0 rgba(255,255,255,0.1)',
        }}>
          <div style={{
            width: 80, height: 80, borderRadius: 18,
            background: 'linear-gradient(180deg, #fff 0%, #f0e8d8 100%)',
            border: '2px solid #B47800',
            boxShadow: '0 4px 0 #8F5E00, inset 0 2px 0 rgba(255,255,255,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 42, flexShrink: 0,
          }}>{p.emoji}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 9, fontWeight: 900, color: '#A78BFA', letterSpacing: '1.2px' }}>{p.brand}</div>
            <div style={{ fontSize: 14, fontWeight: 900, color: '#fff', letterSpacing: '-0.3px', marginTop: 3, lineHeight: 1.2 }}>{p.name}</div>
            <div style={{ fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.4)', marginTop: 4, fontFamily: 'monospace' }}>{p.ean}</div>
          </div>
        </div>

        {/* Consensus price card — bocal rose */}
        <div style={{
          ...cardS,
          padding: '16px 18px',
          background: 'linear-gradient(160deg, #2A1A1A 0%, #1F1212 100%)',
          border: '1.5px solid rgba(255,107,157,0.45)',
          boxShadow: '0 6px 0 rgba(80,20,40,0.85), 0 14px 28px rgba(0,0,0,0.45), inset 0 2px 0 rgba(255,107,157,0.12), 0 0 22px rgba(255,107,157,0.12)',
          display: 'flex', alignItems: 'center', gap: 14,
          position: 'relative', overflow: 'hidden',
        }}>
          <div style={{ position: 'absolute', top: -30, right: -30, width: 120, height: 120, borderRadius: '50%', background: 'radial-gradient(closest-side, rgba(255,107,157,0.18), transparent 70%)', pointerEvents: 'none' }}/>
          <div style={{
            width: 56, height: 56, borderRadius: 14,
            background: 'linear-gradient(180deg, #FF8FB3, #FF6B9D)',
            border: '2px solid rgba(180,40,80,0.8)',
            boxShadow: '0 3px 0 rgba(120,20,50,0.8), inset 0 1px 0 rgba(255,255,255,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0, position: 'relative',
          }}>
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2L4 7v10l8 5 8-5V7l-8-5z"/>
              <path d="M12 22V12M4 7l8 5 8-5"/>
            </svg>
          </div>
          <div style={{ flex: 1, position: 'relative' }}>
            <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,107,157,0.9)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Meilleur prix</div>
            <div style={{ fontSize: 28, fontWeight: 900, color: '#FF6B9D', letterSpacing: '-0.8px', lineHeight: 1, marginTop: 2, textShadow: '0 0 18px rgba(255,107,157,0.35)' }}>
              {p.bestPrice.toFixed(2).replace('.',',')}€
            </div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.65)', marginTop: 4 }}>
              {p.storesCount} magasins · 4 km autour
            </div>
          </div>
        </div>

        {/* Tabs */}
        <STS
          tabs={[{ id: 'prices', label: 'Prix · ' + p.prices.length }, { id: 'info', label: 'Infos' }]}
          active={tab}
          onChange={setTab}
          accent="violet"
        />

        {tab === 'prices' && (
          <div style={{
            background: '#27293A',
            border: '1.5px solid rgba(255,255,255,0.08)',
            borderRadius: 18,
            boxShadow: '0 5px 0 rgba(0,0,0,0.35), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08)',
            overflow: 'hidden',
          }}>
            {p.prices.map((s, i) => <PriceRow key={s.id} s={s} last={i === p.prices.length - 1}/>)}
          </div>
        )}

        {tab === 'info' && (
          <div style={{ ...cardS, padding: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 800, color: 'rgba(255,255,255,0.55)', letterSpacing: '0.8px', textTransform: 'uppercase', marginBottom: 8 }}>Caractéristiques</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[
                ['Quantité', '10 capsules'],
                ['Marque', 'Nespresso'],
                ['Origine', 'Suisse'],
                ['Poids net', '57 g'],
                ['Conservation', 'Sec, ambiant'],
              ].map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.55)' }}>{k}</span>
                  <span style={{ fontSize: 12, fontWeight: 800, color: '#fff' }}>{v}</span>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 12 }}>
              <GBS color="coral" size="md" fullWidth icon="✏" onClick={() => showToast('Compléter +30 cab')}>Compléter la fiche · +30 cab</GBS>
            </div>
          </div>
        )}

        <GBS color="coral" size="lg" fullWidth icon="＋" onClick={() => showToast('Ajouté à la liste')}>Ajouter à ma liste</GBS>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────
// PROFIL — user profile with stats grid + grouped menu
// ─────────────────────────────────────────────────────────────────────
function StatTile({ value, label, color, icon }) {
  return (
    <div style={{
      flex: 1,
      ...cardS,
      padding: '12px 10px',
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4,
      background: 'rgba(255,255,255,0.03)',
      border: '1.5px solid ' + color + '33',
      boxShadow: '0 4px 0 rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.05)',
    }}>
      <div style={{ fontSize: 18, marginBottom: 2 }}>{icon}</div>
      <div style={{ fontSize: 18, fontWeight: 900, color: color, letterSpacing: '-0.4px', lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.55)', letterSpacing: '0.6px', textTransform: 'uppercase' }}>{label}</div>
    </div>
  );
}

function MenuRow({ icon, iconBg, title, subtitle, onPress, last, danger, disabled }) {
  const [pressed, setPressed] = useStateS(false);
  return (
    <div
      onPointerDown={() => !disabled && setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      onClick={disabled ? undefined : onPress}
      style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '12px 14px',
        borderBottom: last ? 'none' : '1px solid rgba(255,255,255,0.05)',
        cursor: disabled ? 'default' : 'pointer',
        userSelect: 'none',
        background: pressed ? 'rgba(255,255,255,0.04)' : 'transparent',
        opacity: disabled ? 0.45 : 1,
        transition: 'background 0.1s',
      }}>
      <div style={{
        width: 36, height: 36, borderRadius: 11,
        background: iconBg + '22',
        border: '1.5px solid ' + iconBg + '50',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        flexShrink: 0,
        fontSize: 16,
      }}>{icon}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: danger ? '#FB7185' : '#fff', letterSpacing: '-0.2px' }}>{title}</div>
        {subtitle && (
          <div style={{ fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>{subtitle}</div>
        )}
      </div>
      {!danger && <span style={{ color: 'rgba(255,255,255,0.35)', fontSize: 16, fontWeight: 700 }}>›</span>}
    </div>
  );
}

function MenuGroup({ label, color, children }) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 800, color, letterSpacing: '1px', textTransform: 'uppercase', paddingLeft: 14, marginBottom: 8 }}>{label}</div>
      <div style={{
        background: '#27293A',
        border: '1.5px solid ' + color + '30',
        borderRadius: 18,
        boxShadow: '0 5px 0 rgba(0,0,0,0.35), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.06)',
        overflow: 'hidden',
      }}>
        {children}
      </div>
    </div>
  );
}

function ProfilScreen({ data, showToast }) {
  return (
    <>
      <PTS
        title="Profil"
        rightIcons={[<button key="set" style={iconHeaderStyleS}>⚙</button>]}
      />
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 14px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Avatar block */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, marginTop: 4 }}>
          <div style={{
            width: 84, height: 84, borderRadius: 42,
            background: 'linear-gradient(180deg, #C4895C 0%, #8B5A2B 100%)',
            border: '4px solid rgba(255,184,0,0.4)',
            boxShadow: '0 5px 0 rgba(60,30,10,0.7), 0 12px 22px rgba(0,0,0,0.45), inset 0 2px 0 rgba(255,255,255,0.25), 0 0 0 6px rgba(255,184,0,0.08)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 44,
          }}>🐀</div>
          <div style={{ fontSize: 18, fontWeight: 900, color: '#fff', letterSpacing: '-0.4px' }}>Marie L.</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.55)' }}>@marie.l</span>
            <span style={{
              padding: '2px 8px',
              background: 'linear-gradient(180deg, #FFE066, #FFB800)',
              border: '1.5px solid #B47800',
              borderRadius: 8,
              boxShadow: '0 2px 0 #8F5E00, inset 0 1px 0 rgba(255,255,255,0.4)',
            }}>
              <span style={{ fontSize: 10, fontWeight: 900, color: '#3A2200', letterSpacing: '-0.1px' }}>★ Niv. {data.battlepass.current_level}</span>
            </span>
          </div>
        </div>

        {/* Stats grid */}
        <div style={{ display: 'flex', gap: 8 }}>
          <StatTile value={data.cabBalance.toLocaleString('fr-FR')} label="Cab" color="#FFB800" icon="🪙"/>
          <StatTile value={data.stats.total_scans || 47} label="Scans" color="#A78BFA" icon="📷"/>
          <StatTile value={Math.round(data.stats.total_savings_cents / 100) + '€'} label="Économies" color="#4DD4B3" icon="💚"/>
        </div>

        {/* Rewards group */}
        <MenuGroup label="Récompenses" color="#FFB800">
          <MenuRow icon="🎁" iconBg="#FFB800" title="Boutique" subtitle="Cartes cadeaux · bonus" onPress={() => showToast('Boutique bientôt')}/>
          <MenuRow icon="🏆" iconBg="#A78BFA" title="Succès" subtitle="7 / 24 débloqués" onPress={() => showToast('Succès')}/>
          <MenuRow icon="👥" iconBg="#FB7185" title="Parrainage" subtitle="Invite un ami · +500 cab" onPress={() => showToast('Parrainage')} last/>
        </MenuGroup>

        {/* Account group */}
        <MenuGroup label="Compte" color="#A78BFA">
          <MenuRow icon="📝" iconBg="#FB7185" title="Mes informations" onPress={() => showToast('Mes infos')}/>
          <MenuRow icon="🔔" iconBg="#FFB800" title="Notifications" disabled onPress={() => {}}/>
          <MenuRow icon="🌐" iconBg="#4DD4B3" title="Langue · Français" onPress={() => showToast('Langue')}/>
          <MenuRow icon="🔒" iconBg="#A78BFA" title="Confidentialité" onPress={() => showToast('Confidentialité')} last/>
        </MenuGroup>

        {/* Logout */}
        <MenuGroup label="Session" color="#FB7185">
          <MenuRow icon="🚪" iconBg="#FB7185" title="Se déconnecter" danger onPress={() => showToast('Déconnexion')} last/>
        </MenuGroup>

        <div style={{ textAlign: 'center', fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.3)', marginTop: 8 }}>
          Ratis v1.0.0 · Made with 🧀
        </div>
      </div>
    </>
  );
}

const iconHeaderStyleS = {
  width: 32, height: 32, borderRadius: 10,
  background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.1)',
  color: '#fff', fontSize: 14,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  cursor: 'pointer', padding: 0, fontFamily: 'inherit',
};

window.ScanScreen = ScanScreen;
window.ProduitScreen = ProduitScreen;
window.ProfilScreen = ProfilScreen;
