// Ratis — Liste tab. Shopping list + multi-store optimized route (v2).
const { useState: useStateL, useMemo: useMemoL } = React;
const { GameButton: GBL, PageTitle: PTL, SegmentedTabs: STL, EmptyCard: ECL, screenCardBase: cardL, useRatisTweaks: useRatisTweaksL } = window.RatisShared;
const { ROUTE_STORES_V2, AnimatedNumber, LIST_CATEGORIES: CATSL } = window.RatisListeData;
const { ItemRowLU, AddBar, TemplatesSheet, SuggestionsSheet, VoiceSheet } = window.RatisListeUI;

const INITIAL_LIST_ITEMS = [
{ id: 'i1', name: 'Lait demi-écrémé 1L', brand: 'Lactel', qty: 2, checked: false, est: 1.05, cat: 'dairy' },
{ id: 'i2', name: 'Pâtes penne 500g', brand: 'Barilla', qty: 1, checked: true, est: 1.85, cat: 'pantry' },
{ id: 'i3', name: 'Bananes (kg)', brand: 'Bio', qty: 1, checked: false, est: 1.49, cat: 'produce' },
{ id: 'i4', name: 'Yaourts grec x4', brand: 'Mamie Nova', qty: 1, checked: false, est: 2.95, cat: 'dairy' },
{ id: 'i5', name: 'Café Nespresso x10', brand: 'Nespresso', qty: 1, checked: false, est: 4.20, cat: 'drinks' },
{ id: 'i6', name: 'Tomates cerises 250g', brand: '', qty: 2, checked: false, est: 1.79, cat: 'produce' }];


const iconHeaderStyleL = {
  width: 32, height: 32, borderRadius: 10,
  background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.1)',
  color: '#fff', fontSize: 13,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  cursor: 'pointer', padding: 0, fontFamily: 'inherit'
};

// ─── ListeScreen ───────────────────────────────────────────────────
function ListeScreen({ showToast }) {
  const [tab, setTab] = useStateL('products');
  const [items, setItems] = useStateL(INITIAL_LIST_ITEMS);
  const [routeReady, setRouteReady] = useStateL(false);
  const [optimizing, setOptimizing] = useStateL(false);
  const [templatesOpen, setTemplatesOpen] = useStateL(false);
  const [suggestionsOpen, setSuggestionsOpen] = useStateL(false);
  const [voiceOpen, setVoiceOpen] = useStateL(false);
  const tweaks = useRatisTweaksL();
  const optimizeColor = tweaks.optimizeColor || 'coral';

  // Ordre d'affichage des catégories
  const CAT_ORDER = ['produce', 'dairy', 'meat', 'bakery', 'pantry', 'frozen', 'drinks', 'snacks', 'hygiene', 'other'];
  const sortedItems = useMemoL(() => {
    const unchecked = items.filter((i) => !i.checked).sort((a, b) => {
      const ai = CAT_ORDER.indexOf(a.cat);const bi = CAT_ORDER.indexOf(b.cat);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
    const checked = items.filter((i) => i.checked);
    return [...unchecked, ...checked];
  }, [items]);

  const total = useMemoL(() => items.reduce((s, i) => s + i.est * i.qty, 0), [items]);
  const totalChecked = useMemoL(() => items.filter((i) => i.checked).reduce((s, i) => s + i.est * i.qty, 0), [items]);
  const checkedCount = items.filter((i) => i.checked).length;
  const savings = ROUTE_STORES_V2.reduce((s, x) => s + x.savings, 0);
  const totalDistance = ROUTE_STORES_V2.reduce((s, x) => s + x.distance, 0);
  const totalTime = ROUTE_STORES_V2.reduce((s, x) => s + x.time, 0);

  // Single-store comparison: if you bought everything at the most expensive of the 3
  // (Auchan would be 0.85€ savings → so doing all-at-Auchan loses the 2.40+1.10 = 3.50€)
  const singleStoreLoss = savings - ROUTE_STORES_V2[0].savings; // 4.35 - 2.40 = 1.95€

  const toggle = (id) => setItems(items.map((i) => i.id === id ? { ...i, checked: !i.checked } : i));
  const setQty = (id, qty) => setItems(items.map((i) => i.id === id ? { ...i, qty } : i));
  const removeItem = (id) => setItems(items.filter((i) => i.id !== id));
  const addItem = (item) => {
    const id = 'i_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
    setItems([{ id, qty: 1, checked: false, est: 1.5, cat: 'other', brand: '', ...item }, ...items]);
  };
  const addByName = (name) => {
    addItem({ name, est: 1 + Math.random() * 4 });
    showToast('Ajouté · ' + name);
  };
  const applyTemplate = (t) => {
    const additions = t.items.map((it, i) => ({
      id: 'i_t' + Date.now() + '_' + i,
      qty: 1, checked: false, ...it
    }));
    setItems([...additions, ...items]);
    showToast(t.label + ' · +' + t.items.length + ' articles');
  };
  const addSuggestion = (s) => {
    addItem({ name: s.name, brand: s.brand, est: s.est, cat: s.cat });
    showToast('Ajouté · ' + s.name);
  };

  const optimize = () => {
    setOptimizing(true);
    setTimeout(() => {
      setOptimizing(false);
      setRouteReady(true);
      setTab('route');
      showToast('Itinéraire prêt · -' + savings.toFixed(2).replace('.', ',') + '€');
    }, 900);
  };

  return (
    <>
      <PTL
        title="Ma liste"
        rightIcons={[
        <button key="map" style={iconHeaderStyleL} onClick={() => showToast('Carte bientôt')}>🗺</button>,
        <button key="more" style={iconHeaderStyleL} onClick={() => showToast('Plus d\'options')}>⋯</button>]
        } />
      
      <div className="liste-scroll-root" style={{ flex: 1, overflowY: 'auto', padding: '4px 14px 24px', display: 'flex', flexDirection: 'column', gap: 12, fontFamily: "Inter" }}>
        <style>{`
          .liste-scroll-root > * { flex-shrink: 0; }
          @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
          @keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        `}</style>

        <STL
          tabs={[
          { id: 'products', label: 'Liste · ' + items.length },
          { id: 'route', label: 'Itinéraire' + (routeReady ? ' ✓' : '') }]
          }
          active={tab}
          onChange={setTab}
          accent="teal" />
        

        {tab === 'products' &&
        <>
            {/* Add bar */}
            <AddBar
            onAdd={addByName}
            onVoice={() => setVoiceOpen(true)}
            onTemplates={() => setTemplatesOpen(true)}
            onSuggestions={() => setSuggestionsOpen(true)} />
          

            {/* Optimiser + Scan row */}
            <div style={{ display: 'flex', gap: 8 }}>
              <GBL
              color={optimizeColor}
              size="md"
              disabled={optimizing}
              icon={optimizing ? '⏳' : routeReady ? '↻' : '🗺'}
              style={{ flex: 2 }}
              onClick={optimize}>
              
                {optimizing ? 'Calcul…' : routeReady ? 'Recalculer' : "Optimiser l'itinéraire"}
              </GBL>
              <GBL
              color="terracotta-outline"
              size="md"
              icon="📷"
              style={{ flex: 'none', width: 44, padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              onClick={() => showToast('Scan rapide')}>
            </GBL>
            </div>

            {/* Total card with animated counters */}
            <div style={{
            ...cardL,
            padding: '12px 14px',
            display: 'flex', alignItems: 'center', gap: 12,
            background: 'linear-gradient(160deg, #2A1A1A 0%, #1F1212 100%)',
            border: '1.5px solid rgba(255,107,157,0.3)',
            boxShadow: '0 5px 0 rgba(80,20,40,0.7), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08)',
            position: 'relative', overflow: 'hidden'
          }}>
              {/* corner glow bocal */}
              <div style={{ position: 'absolute', top: -30, right: -30, width: 120, height: 120, borderRadius: '50%', background: 'radial-gradient(closest-side, rgba(255,107,157,0.18), transparent 70%)', pointerEvents: 'none' }} />
              <div style={{ flex: 1, position: 'relative' }}>
                <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Total estimé</div>
                <div style={{ fontSize: 22, fontWeight: 900, color: '#fff', letterSpacing: '-0.6px', marginTop: 2 }}>
                  <AnimatedNumber value={total} format={(v) => v.toFixed(2).replace('.', ',') + '€'} />
                </div>
                {checkedCount > 0 &&
              <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>
                    {checkedCount} coché{checkedCount > 1 ? 's' : ''} · <AnimatedNumber value={totalChecked} format={(v) => v.toFixed(2).replace('.', ',') + '€'} />
                  </div>
              }
              </div>
              <div style={{ width: 1, height: 36, background: 'rgba(255,255,255,0.1)' }} />
              <div style={{ flex: 1, position: 'relative' }}>
                <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,107,157,0.9)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Économies</div>
                <div style={{ fontSize: 22, fontWeight: 900, color: '#FF6B9D', letterSpacing: '-0.6px', marginTop: 2 }}>
                  -{savings.toFixed(2).replace('.', ',')}€
                </div>
                {!routeReady &&
              <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.4)', marginTop: 2 }}>
                    après optimisation
                  </div>
              }
              </div>
            </div>

            {/* Items list */}
            <div style={{ ...cardL, padding: 0, overflow: 'hidden', position: 'relative' }}>
              {/* Market background — subtle like battlepass */}
              <img src="lib/market.svg" alt="" aria-hidden="true" style={{
                position: 'absolute', bottom: 0, left: 0, right: 0,
                width: '100%', height: 180,
                objectFit: 'cover', objectPosition: 'center bottom',
                opacity: 0.15,
                mixBlendMode: 'luminosity',
                pointerEvents: 'none',
                zIndex: 0,
              }}/>
              {sortedItems.map((it, idx) =>
            <ItemRowLU
              key={it.id}
              item={it}
              onToggle={() => toggle(it.id)}
              onQty={(q) => setQty(it.id, q)}
              onRemove={() => removeItem(it.id)}
              last={idx === sortedItems.length - 1} />
            )}
            </div>

            {/* Empty hint at bottom for adding more */}
            {items.length < 3 &&
          <div style={{
            padding: 14,
            textAlign: 'center',
            background: 'rgba(255,255,255,0.02)',
            border: '1.5px dashed rgba(255,255,255,0.1)',
            borderRadius: 14
          }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.55)', marginBottom: 8 }}>
                  Besoin d'inspiration ? Essaie un template ou tes suggestions.
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                  <button onClick={() => setTemplatesOpen(true)} style={hintBtnStyle}>✨ Templates</button>
                  <button onClick={() => setSuggestionsOpen(true)} style={hintBtnStyle}>💡 Suggestions</button>
                </div>
              </div>
          }
          </>
        }

        {tab === 'route' &&
        <>
            {!routeReady ?
          <ECL
            icon="🗺"
            title="Pas encore d'itinéraire"
            subtitle="Optimise depuis l'onglet Liste pour calculer le meilleur trajet."
            action={<GBL color="teal" size="md" onClick={() => setTab('products')}>Aller à la liste</GBL>} /> :


          <>
                {/* Hero summary card */}
                <div style={{
              ...cardL,
              background: 'linear-gradient(160deg, #2A1A1A 0%, #1F1212 100%)',
              border: '1.5px solid rgba(255,107,157,0.35)',
              boxShadow: '0 5px 0 rgba(80,20,40,0.7), 0 12px 22px rgba(0,0,0,0.4), inset 0 2px 0 rgba(255,255,255,0.10)',
              padding: '14px 16px',
              display: 'flex', alignItems: 'center', gap: 14,
              position: 'relative', overflow: 'hidden'
            }}>
                  <div style={{ position: 'absolute', top: -30, right: -30, width: 120, height: 120, borderRadius: '50%', background: 'radial-gradient(closest-side, rgba(255,107,157,0.15), transparent 70%)', pointerEvents: 'none' }} />
                  <div style={{ flex: 1, position: 'relative' }}>
                    <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Total</div>
                    <div style={{ fontSize: 22, fontWeight: 900, color: '#fff', letterSpacing: '-0.6px' }}>{total.toFixed(2).replace('.', ',')}€</div>
                  </div>
                  <div style={{ width: 1, height: 36, background: 'rgba(255,255,255,0.12)' }} />
                  <div style={{ flex: 1, position: 'relative' }}>
                    <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,107,157,0.9)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Économisé</div>
                    <div style={{ fontSize: 22, fontWeight: 900, color: '#FF6B9D', letterSpacing: '-0.6px' }}>{savings.toFixed(2).replace('.', ',')}€</div>
                  </div>
                  <div style={{ width: 1, height: 36, background: 'rgba(255,255,255,0.12)' }} />
                  <div style={{ position: 'relative' }}>
                    <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.8px', textTransform: 'uppercase' }}>Trajet</div>
                    <div style={{ fontSize: 14, fontWeight: 900, color: '#fff', letterSpacing: '-0.3px', marginTop: 4 }}>{totalDistance.toFixed(1)} km · {totalTime}min</div>
                  </div>
                </div>

                {/* Comparison banner */}
                <div style={{
              padding: '10px 14px',
              background: 'linear-gradient(135deg, rgba(255,184,0,0.12), rgba(255,184,0,0.04))',
              border: '1.5px solid rgba(255,184,0,0.35)',
              borderRadius: 14,
              display: 'flex', alignItems: 'center', gap: 10
            }}>
                  <div style={{
                width: 32, height: 32, borderRadius: 10,
                background: 'rgba(255,184,0,0.2)',
                border: '1px solid rgba(255,184,0,0.5)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 16, flexShrink: 0
              }}>📊</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 10, fontWeight: 800, color: 'rgba(255,184,0,0.85)', letterSpacing: '0.5px', textTransform: 'uppercase' }}>Comparé à 1 magasin</div>
                    <div style={{ fontSize: 12, fontWeight: 800, color: '#fff', letterSpacing: '-0.2px', marginTop: 1, textWrap: 'pretty' }}>
                      Tu gagnes <span style={{ color: '#FFB800' }}>+{singleStoreLoss.toFixed(2).replace('.', ',')}€</span> en multi-magasin
                    </div>
                  </div>
                </div>

                {/* Stops grouped by store */}
                <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 0 }}>
                  {ROUTE_STORES_V2.map((s, i) =>
              <RouteStopCard
                key={s.id}
                store={s}
                idx={i}
                last={i === ROUTE_STORES_V2.length - 1}
                itemsList={s.items_list} />

              )}
                </div>

                <GBL color="teal" size="lg" fullWidth icon="🧭" onClick={() => showToast('Démarrage de l\'itinéraire')}>Démarrer l'itinéraire</GBL>
              </>
          }
          </>
        }
      </div>

      <TemplatesSheet open={templatesOpen} onClose={() => setTemplatesOpen(false)} onApply={applyTemplate} />
      <SuggestionsSheet open={suggestionsOpen} onClose={() => setSuggestionsOpen(false)} onAdd={addSuggestion} />
      <VoiceSheet open={voiceOpen} onClose={() => setVoiceOpen(false)} onTranscript={addItem} />
    </>);

}

const hintBtnStyle = {
  padding: '8px 12px',
  background: 'rgba(218,119,86,0.10)',
  border: '1px solid rgba(218,119,86,0.35)',
  borderRadius: 10,
  color: '#E8896A', fontSize: 11, fontWeight: 800, letterSpacing: '0.2px',
  cursor: 'pointer', fontFamily: 'inherit'
};

// ─── RouteStopCard with item breakdown ─────────────────────────────
function RouteStopCard({ store, idx, last, itemsList }) {
  return (
    <div style={{ position: 'relative', display: 'flex', gap: 12 }}>
      <div style={{ width: 32, position: 'relative', flexShrink: 0 }}>
        <div style={{
          width: 32, height: 32, borderRadius: 16,
          background: 'linear-gradient(180deg, ' + store.color + ', ' + store.color + 'cc)',
          border: '2px solid rgba(0,0,0,0.4)',
          boxShadow: '0 2px 0 rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontSize: 13, fontWeight: 900,
          textShadow: '0 1px 1px rgba(0,0,0,0.4)'
        }}>{idx + 1}</div>
        {!last &&
        <div style={{
          position: 'absolute', top: 34, bottom: -10, left: 15,
          width: 2,
          background: 'repeating-linear-gradient(180deg, rgba(255,255,255,0.25) 0 4px, transparent 4px 8px)'
        }} />
        }
      </div>
      <div style={{
        flex: 1,
        background: 'rgba(255,255,255,0.04)',
        border: '1.5px solid ' + store.color + '55',
        borderRadius: 14,
        padding: '12px 14px',
        boxShadow: '0 3px 0 rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.06)',
        marginBottom: 12
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: itemsList?.length ? 10 : 0 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 900, color: '#fff', letterSpacing: '-0.3px' }}>{store.name}</div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>
              {store.distance.toFixed(1)} km · {store.time} min · {store.items} article{store.items > 1 ? 's' : ''}
            </div>
          </div>
          <div style={{
            padding: '4px 10px',
            background: 'rgba(255,184,0,0.18)',
            border: '1px solid rgba(255,184,0,0.5)',
            borderRadius: 10
          }}>
            <span style={{ fontSize: 11, fontWeight: 900, color: '#FFB800', letterSpacing: '-0.1px' }}>
              -{store.savings.toFixed(2).replace('.', ',')}€
            </span>
          </div>
        </div>
        {itemsList && itemsList.length > 0 &&
        <div style={{
          paddingTop: 8,
          borderTop: '1px solid rgba(255,255,255,0.06)',
          display: 'flex', flexDirection: 'column', gap: 4
        }}>
            {itemsList.map((it, i) =>
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.7)' }}>
                <span style={{ color: store.color, fontSize: 10 }}>●</span>
                <span style={{ flex: 1, textOverflow: 'ellipsis', whiteSpace: 'nowrap', overflow: 'hidden' }}>{it}</span>
              </div>
          )}
          </div>
        }
      </div>
    </div>);

}

window.ListeScreen = ListeScreen;