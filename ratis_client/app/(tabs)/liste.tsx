// ratis_client/app/(tabs)/liste.tsx
//
// V5 Liste composition — port of `Ratis_handoff/lib/ratis-liste.jsx`. The
// visual layer reads from existing hooks ; no new business logic is
// introduced (R31).
//
// Layout :
//   1. ScreenBackground (V5 shared)
//   2. PageTitleBand "Ma liste" + 2 right icons (map / more)
//   3. SegmentedTabs : Liste · N | Itinéraire
//   4. Scrollable content :
//      - tab=products: AddBar → "Optimiser l'itinéraire" CTA + Scan icon →
//                      ListeTotalCard → ListItemRow × N
//      - tab=route   : empty state | RouteSummaryCard + RouteStopCard × N +
//                      "Démarrer l'itinéraire" CTA
//
// Hooks consumed (no signature reinvention) :
//   - `useActiveList()`         — first list (V1: one user = one list)
//   - `useShoppingListDetail()` — fetches items
//   - `useActiveRoute()`        — latest optimised route (or null)
//   - `useOptimizeRoute()`      — POSTs the optimization request
//   - `useToggleItem()`         — PATCH item.checked
//   - `useDeleteItem()`         — DELETE item
//
// V1 caveats :
//   - Wave 12 — backend now ships ``category`` per item (canonical key
//     among frais/boulangerie/epicerie/boissons/vrac/autres) ; ``brand``
//     still requires a local heuristic (``inferBrand``) until the LO
//     payload widens. ``ListItemRow`` keeps its own 10-key palette,
//     mapped from the backend 6-key set via ``ROW_PALETTE_FOR_LIST_CATEGORY``.
//   - `total` / `checkedTotal` for the products-tab card default to 0 until
//     prices are wired through ; the optimised route is the source of
//     `total` / `savings` once `status === 'ready'`.
//   - The "Démarrer l'itinéraire" CTA is a no-op pending the navigation
//     wiring (V2). Per ARCH § Liste it lives in this surface.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as Location from 'expo-location';
import { useTranslation } from 'react-i18next';

import { ScreenBackground } from '@/components/ui/screen-background';
import { PageTitleBand } from '@/components/ui/page-title-band';
import { SegmentedTabs } from '@/components/design-system';
import { AddBar } from '@/components/liste/add-bar';
import {
  ListItemRow,
  type CategoryKey,
} from '@/components/liste/list-item-row';
import { ListeTotalCard } from '@/components/liste/liste-total-card';
import { RouteMap } from '@/components/liste/route-map';
import { RouteSummaryCard } from '@/components/liste/route-summary-card';
import { RouteStopCard } from '@/components/liste/route-stop-card';
import { SuggestionsSheet } from '@/components/liste/suggestions-sheet';
import { TemplatesSheet } from '@/components/liste/templates-sheet';
import { VoiceSheet } from '@/components/liste/voice-sheet';
import { Colors } from '@/constants/theme';
import { useActiveList, useCreateList } from '@/hooks/use-shopping-lists';
import { useShoppingListDetail } from '@/hooks/use-shopping-list-detail';
import { useActiveRoute, type RouteFull } from '@/hooks/use-active-route';
import { useOptimizeRoute } from '@/hooks/use-optimize-route';
import {
  useAddItem,
  useDeleteItem,
  useToggleItem,
} from '@/hooks/use-list-items';
import type { ProductSearchHit } from '@/hooks/use-product-search';
import { useDefaultSuggestions } from '@/hooks/use-default-suggestions';
import { listClient } from '@/services/list-client';
import { useQueryClient } from '@tanstack/react-query';
import { AuthError } from '@/types/auth';
import type { ListCategoryKey, ShoppingListItem } from '@/types/shopping-list';
import {
  LIST_CATEGORY_FALLBACK,
  LIST_CATEGORY_ORDER,
} from '@/constants/list-categories';
import MarketSvg from '@/assets/images/market.svg';

type Tab = 'products' | 'route';

type ToastVariant = 'success' | 'error';
type ToastState = { variant: ToastVariant; message: string } | null;
const TOAST_VISIBLE_MS = 2800;

/**
 * Maps the AddItem mutation outcome to a localised toast message.
 *
 * Backend error contract (see
 * ``webservices/ratis_list_optimiser/routes/shopping_lists.py``
 * ``add_item``) :
 *   404 ``list_not_found``       — the active list was deleted under us
 *   404 ``product_not_found``    — search hit references an unknown EAN
 *   409 ``item_already_in_list`` — product already on the list
 *   422 ``list_full``            — capped at ``max_items_per_list``
 *   * / network                  — generic fallback
 *
 * Each branch lives in ``locales/fr.json`` § ``liste.add_item_toast``.
 */
function buildAddItemErrorMessage(
  err: unknown,
  productName: string,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (err instanceof AuthError) {
    if (err.code === 'item_already_in_list')
      return t('liste.add_item_toast.already_in_list', { name: productName });
    if (err.code === 'list_full') return t('liste.add_item_toast.list_full');
    if (err.code === 'list_not_found')
      return t('liste.add_item_toast.list_not_found');
    if (err.code === 'product_not_found')
      return t('liste.add_item_toast.product_not_found');
  }
  return t('liste.add_item_toast.network');
}

export default function ListeScreen() {
  const { t } = useTranslation();
  // Warm the default-suggestions cache so the AddBar renders the
  // dropdown instantly when the user focuses the empty search field
  // (no spinner). React Query dedupes by queryKey, so AddBar's later
  // call hits the same cache entry. See
  // ``docs/superpowers/specs/2026-05-14-default-search-3tier-design.md``.
  useDefaultSuggestions();
  const [activeTab, setActiveTab] = useState<Tab>('products');
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  // In-screen toast for AddItem mutation outcomes (R33 — no silent
  // failures). A FE-only banner is sufficient here ; if/when we get a
  // global toast utility the message map above can be reused.
  const [toast, setToast] = useState<ToastState>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((next: NonNullable<ToastState>) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast(next);
    toastTimerRef.current = setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, TOAST_VISIBLE_MS);
  }, []);
  // Clear pending toast timer on unmount — prevents the dismiss
  // callback from firing on an unmounted screen (which would log a
  // React state-update warning) and stops jest test leakage across
  // suites (the timer would otherwise survive the test boundary).
  useEffect(() => {
    return () => {
      if (toastTimerRef.current) {
        clearTimeout(toastTimerRef.current);
        toastTimerRef.current = null;
      }
    };
  }, []);

  const { data: activeList } = useActiveList();
  const listId = activeList?.id ?? null;
  const createList = useCreateList();

  const { data: detail, isLoading: loadingList } =
    useShoppingListDetail(listId);
  const { data: route } = useActiveRoute(listId);
  const optimize = useOptimizeRoute(listId);
  const toggle = useToggleItem(listId);
  const del = useDeleteItem(listId);
  const addItem = useAddItem(listId);

  const items = useMemo(() => detail?.items ?? [], [detail?.items]);
  const hasItems = items.length > 0;
  const isComputing = !!route && route.status === 'computing';
  const readyRoute: RouteFull | null =
    route && route.status === 'ready' ? route : null;

  // Derived metrics for the products-tab Total card. Backend doesn't yet
  // expose per-item est price — rely on the optimized route when ready.
  const checkedCount = items.filter((i) => i.checked).length;
  const totalCardSavings = readyRoute?.total_savings ?? 0;
  const totalCardTotal = readyRoute?.total_price ?? 0;
  const totalCardCheckedTotal = 0;

  const handleOptimize = useCallback(async () => {
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        return;
      }
      const loc = await Location.getCurrentPositionAsync({});
      optimize.mutate({
        lat: loc.coords.latitude,
        lng: loc.coords.longitude,
      });
    } catch {
      // Swallow — the user can retry. A toast/banner can land V2.
    }
  }, [optimize]);

  const handleAddByName = useCallback(
    (_name: string) => {
      // Wave 6 (PO ticket 2026-05-13 Issue 3) — the « + » button no
      // longer opens the SuggestionsSheet. PO complaint verbatim :
      // « le + n'a pas d'intérêt, en cliquant sur l'objet dans la
      // liste déroulante, ça devrait l'ajouter directement ». The
      // AddBar now picks the FIRST dropdown hit on « + » press (or
      // keyboard Enter) — this fallback only runs when no hits are
      // available yet (e.g. user pressed « + » before the debounced
      // search returned). We intentionally NO-OP rather than reopen
      // the legacy suggestions sheet : the 💡 IconBtn (separate path)
      // still surfaces the « Vous achetez souvent » sheet when the
      // user explicitly wants it.
    },
    [],
  );

  // Bug 3 (wave 4) — picking a search hit from the AddBar dropdown
  // posts AddItem with the resolved EAN. No-op when no list is active
  // (the empty-state already explains the user must create one).
  //
  // PO ticket 2026-05-12 follow-up — surface every mutation outcome
  // (R33). Both success and error were previously silent ; the user
  // had no way to tell whether their tap was registered. The success
  // path already invalidates the list query so the new row appears,
  // but a quick confirmation toast removes any ambiguity ; errors
  // map 1:1 to the backend ``detail`` codes (see
  // ``buildAddItemErrorMessage`` above).
  const qc = useQueryClient();
  const handleSelectHit = useCallback(
    async (hit: ProductSearchHit) => {
      // Wave 9 — if the user has no list yet, auto-create one before
      // adding the item. PR #434 (wave 8) fixed the touch event so the
      // tap reaches the handler, but the silent `if (!listId) return`
      // here was the next gate : zero-list users tapped a hit and
      // nothing happened, no feedback, no DB write. The cleanest UX is
      // to create a default list « Ma liste » in-line then enchain the
      // AddItem call. We use ``listClient.post`` directly for the
      // AddItem (instead of ``useAddItem(listId)`` which closed over a
      // stale null ``listId``) so the new list id is used immediately.
      let effectiveListId = listId;
      if (!effectiveListId) {
        try {
          const created = await createList.mutateAsync({ name: 'Ma liste' });
          effectiveListId = created.id;
        } catch (err) {
          showToast({
            variant: 'error',
            message: buildAddItemErrorMessage(err, hit.name, t),
          });
          return;
        }
      }
      try {
        await listClient.post<ShoppingListItem>(
          `/lists/${effectiveListId}/items`,
          { product_ean: hit.ean, quantity: 1 },
        );
        qc.invalidateQueries({ queryKey: ['list', effectiveListId] });
        qc.invalidateQueries({ queryKey: ['route', effectiveListId] });
        showToast({
          variant: 'success',
          message: t('liste.add_item_toast.success', { name: hit.name }),
        });
      } catch (err) {
        showToast({
          variant: 'error',
          message: buildAddItemErrorMessage(err, hit.name, t),
        });
      }
    },
    [listId, createList, qc, showToast, t],
  );

  return (
    <View style={styles.container} testID="liste-screen">
      <ScreenBackground />
      <SafeAreaView edges={['top']} style={{ flex: 1 }}>
        <PageTitleBand
          title={t('liste.title')}
          rightIcons={[
            <Pressable
              key="map"
              testID="liste-header-map"
              accessibilityRole="button"
              accessibilityLabel={t('liste.title')}
              hitSlop={8}
              onPress={() => {}}
              style={styles.hdrBtn}
            >
              <Text style={styles.hdrIcon}>🗺️</Text>
            </Pressable>,
            <Pressable
              key="more"
              testID="liste-header-more"
              accessibilityRole="button"
              accessibilityLabel={t('liste.title')}
              hitSlop={8}
              onPress={() => {}}
              style={styles.hdrBtn}
            >
              <Text style={styles.hdrIcon}>⋯</Text>
            </Pressable>,
          ]}
        />
        <ScrollView
          contentContainerStyle={styles.content}
          // ``keyboardShouldPersistTaps="handled"`` (RN default = ``"never"``)
          // permet aux taps sur les ``Pressable`` (= dropdown row de l'AddBar
          // search autocomplete) d'arriver au handler MÊME quand le clavier
          // est ouvert. Sans ça, la première tap dismiss le clavier et est
          // avalée par la ScrollView. Cf KP-82 — c'est la deuxième moitié
          // du fix touch-event-dropdown (la première moitié étant
          // ``onPressIn`` au lieu de ``onPress`` côté row).
          keyboardShouldPersistTaps="handled"
        >
          <SegmentedTabs
            testID="liste-tabs"
            tabs={[
              {
                id: 'products',
                label:
                  items.length > 0
                    ? t('liste.tabs_v5.products_with_count', {
                        count: items.length,
                      })
                    : t('liste.tabs_v5.products'),
              },
              { id: 'route', label: t('liste.tabs_v5.itinerary') },
            ]}
            activeId={activeTab}
            onChange={(id) => setActiveTab(id as Tab)}
          />

          {activeTab === 'products' ? (
            <>
              <AddBar
                onSubmit={handleAddByName}
                onSelectHit={handleSelectHit}
                onPressSuggestions={() => setSuggestionsOpen(true)}
                onPressTemplates={() => setTemplatesOpen(true)}
                onPressVoice={() => setVoiceOpen(true)}
              />
              {toast ? (
                <View
                  testID="liste-add-item-toast"
                  accessibilityLiveRegion="polite"
                  accessibilityRole="alert"
                  style={[
                    styles.toast,
                    toast.variant === 'success'
                      ? styles.toastSuccess
                      : styles.toastError,
                  ]}
                >
                  <Text
                    testID="liste-add-item-toast-text"
                    style={styles.toastTxt}
                  >
                    {toast.message}
                  </Text>
                </View>
              ) : null}

              {/* Optimise CTA + Scan icon row (JSX lines 130-148) */}
              <View style={styles.optimizeRow}>
                <Pressable
                  testID="liste-optimize-cta"
                  onPress={handleOptimize}
                  disabled={!hasItems || optimize.isPending}
                  accessibilityRole="button"
                  accessibilityState={{
                    disabled: !hasItems || optimize.isPending,
                  }}
                  style={[
                    styles.optimizeBtn,
                    (!hasItems || optimize.isPending) && styles.optimizeBtnDim,
                  ]}
                >
                  <Text style={styles.optimizeBtnIcon}>
                    {optimize.isPending ? '⏳' : readyRoute ? '↻' : '🗺'}
                  </Text>
                  <Text style={styles.optimizeBtnTxt}>
                    {optimize.isPending
                      ? t('liste.optimize_cta.computing')
                      : t('liste.optimize_cta.idle')}
                  </Text>
                </Pressable>
                <Pressable
                  testID="liste-scan-cta"
                  onPress={() => {}}
                  accessibilityRole="button"
                  accessibilityLabel={t('liste.scan_cta')}
                  style={styles.scanBtn}
                  hitSlop={6}
                >
                  <Text style={styles.scanBtnIcon}>📷</Text>
                </Pressable>
              </View>

              {hasItems ? (
                <ListeTotalCard
                  total={totalCardTotal}
                  savings={totalCardSavings}
                  checkedCount={checkedCount}
                  checkedTotal={totalCardCheckedTotal}
                  routeReady={!!readyRoute}
                />
              ) : null}

              {loadingList ? (
                <View style={styles.loader} testID="liste-loader">
                  <ActivityIndicator color={Colors.violet} />
                </View>
              ) : null}

              {!loadingList && !hasItems ? (
                <View style={styles.emptyCard} testID="liste-empty">
                  <Text style={styles.emptyTitle}>
                    {t('liste.empty.title')}
                  </Text>
                  <Text style={styles.emptyHint}>
                    {t('liste.empty.hint')}
                  </Text>
                </View>
              ) : null}

              {!loadingList && hasItems ? (
                <View
                  testID="liste-grouped-items"
                  style={[
                    styles.itemsGroupedWrap,
                    isComputing ? styles.dimmed : undefined,
                  ]}
                >
                  {/* Wave 12 — market.svg watermark behind the grouped
                      items. Low opacity, no touch capture. Matches the
                      handoff iso (lib/ratis-liste.jsx lines 188-198). */}
                  <View
                    pointerEvents="none"
                    style={styles.marketBgWrap}
                    testID="liste-market-bg"
                  >
                    <MarketSvg
                      width="100%"
                      height={180}
                      opacity={0.12}
                      preserveAspectRatio="xMidYMax slice"
                    />
                  </View>
                  {groupItemsByCategory(items).map((group) => (
                    <View
                      key={group.key}
                      testID={`liste-section-${group.key}`}
                      style={styles.section}
                    >
                      <Text
                        testID={`liste-section-${group.key}-header`}
                        style={styles.sectionHeader}
                      >
                        {t(`liste.category.${group.key}`)}
                      </Text>
                      <View style={styles.itemsBlock}>
                        {group.items.map((it, idx) => {
                          const brand = inferBrand(it.product_name);
                          const palette =
                            ROW_PALETTE_FOR_LIST_CATEGORY[
                              (it.category ??
                                LIST_CATEGORY_FALLBACK) as ListCategoryKey
                            ];
                          return (
                            <ListItemRow
                              key={it.id}
                              item={it}
                              brand={brand}
                              category={palette}
                              isFirst={idx === 0}
                              isLast={idx === group.items.length - 1}
                              onToggle={() =>
                                toggle.mutate({
                                  itemId: it.id,
                                  checked: !it.checked,
                                })
                              }
                              onDelete={() =>
                                del.mutate({ itemId: it.id })
                              }
                              onQuantityChange={(q) =>
                                toggle.mutate({ itemId: it.id, quantity: q })
                              }
                            />
                          );
                        })}
                      </View>
                    </View>
                  ))}
                </View>
              ) : null}
            </>
          ) : (
            <>
              {!hasItems ? (
                <View style={styles.emptyCard} testID="route-empty-no-items">
                  <Text style={styles.emptyTitle}>
                    {t('liste.itineraire.empty_list_title')}
                  </Text>
                  <Text style={styles.emptyHint}>
                    {t('liste.itineraire.empty_list_hint')}
                  </Text>
                </View>
              ) : null}

              {hasItems && isComputing ? (
                <View style={styles.computingCard} testID="route-computing">
                  <ActivityIndicator color={Colors.violet} />
                  <Text style={styles.computingTxt}>
                    {t('liste.computing')}
                  </Text>
                </View>
              ) : null}

              {hasItems && !isComputing && !readyRoute ? (
                <View style={styles.emptyCard} testID="route-empty-no-route">
                  <Text style={styles.emptyTitle}>
                    {t('liste.itineraire.no_route_title')}
                  </Text>
                  <Text style={styles.emptyHint}>
                    {t('liste.itineraire.no_route_hint')}
                  </Text>
                  <Pressable
                    onPress={handleOptimize}
                    disabled={optimize.isPending}
                    style={[
                      styles.startCta,
                      optimize.isPending && styles.optimizeBtnDim,
                    ]}
                    testID="route-empty-optimize-cta"
                    accessibilityRole="button"
                    accessibilityLabel={t('liste.itinerary.empty_cta')}
                  >
                    <Text style={styles.startCtaTxt}>
                      {t('liste.itinerary.empty_cta')}
                    </Text>
                  </Pressable>
                </View>
              ) : null}

              {hasItems && readyRoute ? (
                <>
                  <RouteSummaryCard
                    total={readyRoute.total_price}
                    savings={readyRoute.total_savings}
                    distanceKm={readyRoute.distance_km}
                  />
                  <RouteMap
                    stores={readyRoute.stores}
                    polylineEncoded={readyRoute.route_polyline}
                  />
                  {readyRoute.stores.map((s, i) => (
                    <RouteStopCard
                      key={s.store_id}
                      store={s}
                      index={i + 1}
                      last={i === readyRoute.stores.length - 1}
                    />
                  ))}
                  <Pressable
                    onPress={handleOptimize}
                    style={styles.startCta}
                    testID="reoptimize-btn"
                    accessibilityRole="button"
                    accessibilityLabel={t('liste.itineraire.reoptimize_a11y')}
                  >
                    <Text style={styles.startCtaTxt}>
                      {t('liste.itinerary.start_cta')}
                    </Text>
                  </Pressable>
                </>
              ) : null}
            </>
          )}
        </ScrollView>
      </SafeAreaView>

      <SuggestionsSheet
        open={suggestionsOpen}
        onClose={() => setSuggestionsOpen(false)}
      />
      <TemplatesSheet
        open={templatesOpen}
        onClose={() => setTemplatesOpen(false)}
      />
      <VoiceSheet open={voiceOpen} onClose={() => setVoiceOpen(false)} />
    </View>
  );
}

/**
 * V1 brand heuristic — backend ships ``category`` now (wave 12) but
 * not ``brand`` per shopping-list item. We still derive the brand from
 * the product name keywords so ``ListItemRow`` can render the V5 brand
 * eyebrow without lying about the data shape.
 *
 * TODO V1.x — extend the LO ``/lists/{id}`` items payload to ship
 * ``brand`` from the resolved product, then delete this heuristic.
 * Tracked in
 * ``webservices/ratis_list_optimiser/ARCH_LIST_OPTIMISER.md`` § backlog.
 */
const KNOWN_BRANDS = [
  'BIO',
  'LACTEL',
  'MAMIE NOVA',
  'NESPRESSO',
  'BARILLA',
  'PRESIDENT',
  'YOPLAIT',
  'DANONE',
  'EVIAN',
  'HARIBO',
];

function inferBrand(name: string): string | undefined {
  const n = name.toLowerCase();
  return KNOWN_BRANDS.find(
    (b) =>
      n.includes(b.toLowerCase().replace(' ', '')) ||
      n.includes(b.toLowerCase()),
  );
}

/**
 * Maps the backend-canonical ``ListCategoryKey`` (6 buckets) to the
 * ``CategoryKey`` palette already in use by ``ListItemRow`` (10 keys
 * mirroring the handoff JSX). Lets the row keep its existing colour /
 * icon palette without forcing the backend mapping to widen.
 *
 *   frais        → dairy   (cold violet — handles dairy/meat/yogurt/cheese)
 *   boulangerie  → bakery  (gold — bread, viennoiseries)
 *   epicerie     → pantry  (amber — shelf-stable)
 *   boissons     → drinks  (orange — beverages)
 *   vrac         → produce (green — loose-weighted, mostly fresh fruits/veg)
 *   autres       → other   (neutral grey — unknown)
 */
const ROW_PALETTE_FOR_LIST_CATEGORY: Record<
  ListCategoryKey,
  CategoryKey
> = {
  frais: 'dairy',
  boulangerie: 'bakery',
  epicerie: 'pantry',
  boissons: 'drinks',
  vrac: 'produce',
  autres: 'other',
};

/**
 * Splits the items array into the canonical category buckets defined
 * in ``LIST_CATEGORY_ORDER``. Items keep their original order within
 * each bucket (creation-order today — matches how the list was built).
 * Items whose ``category`` is ``null`` fall into ``autres``.
 *
 * Empty buckets are filtered out so the screen only renders sections
 * that actually carry rows.
 */
function groupItemsByCategory(
  items: ShoppingListItem[],
): { key: ListCategoryKey; items: ShoppingListItem[] }[] {
  const buckets = new Map<ListCategoryKey, ShoppingListItem[]>();
  for (const it of items) {
    const key: ListCategoryKey = it.category ?? LIST_CATEGORY_FALLBACK;
    const arr = buckets.get(key) ?? [];
    arr.push(it);
    buckets.set(key, arr);
  }
  return LIST_CATEGORY_ORDER.map((key) => ({
    key,
    items: buckets.get(key) ?? [],
  })).filter((g) => g.items.length > 0);
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bg },
  content: { padding: 14, paddingBottom: 100, gap: 12 },
  hdrIcon: { fontSize: 14, color: '#fff' },
  hdrBtn: {
    width: 32,
    height: 32,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  },
  optimizeRow: {
    flexDirection: 'row',
    gap: 8,
  },
  optimizeBtn: {
    flex: 1,
    backgroundColor: Colors.terracotta,
    borderWidth: 1,
    borderColor: Colors.terracottaLo,
    borderRadius: 14,
    paddingVertical: 12,
    paddingHorizontal: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  optimizeBtnDim: {
    opacity: 0.55,
  },
  optimizeBtnIcon: {
    fontSize: 16,
  },
  optimizeBtnTxt: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '900',
    letterSpacing: 0.2,
  },
  scanBtn: {
    width: 44,
    height: 44,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: 'rgba(218,119,86,0.45)',
    backgroundColor: 'rgba(218,119,86,0.10)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  scanBtnIcon: {
    fontSize: 18,
  },
  loader: { paddingVertical: 30, alignItems: 'center' },
  dimmed: { opacity: 0.55 },
  // Single-block items container — owns the outer rounded corners and clips
  // each row's flat edges via `overflow: 'hidden'`. Matches handoff iso :
  // rows render flush (no margin) with internal 1px hairline dividers (cf
  // `ListItemRow` + `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 60-70).
  itemsBlock: {
    borderRadius: 14,
    overflow: 'hidden',
  },
  // Wave 12 — parent of all category sections + the market.svg
  // watermark. Relative positioning so the absolutely-placed bg layer
  // anchors here.
  itemsGroupedWrap: {
    position: 'relative',
    gap: 14,
  },
  marketBgWrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    height: 180,
    overflow: 'hidden',
    borderRadius: 14,
    // Behind the items — zIndex 0 keeps the rows on top without
    // pulling them out of the natural flex flow.
    zIndex: 0,
  },
  // One category bucket — header + collés items block. Sits on top of
  // the market bg via zIndex 1 so taps land on the actual rows.
  section: {
    zIndex: 1,
    gap: 6,
  },
  sectionHeader: {
    paddingHorizontal: 4,
    color: 'rgba(218,119,86,0.85)',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  emptyCard: {
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 14,
    padding: 20,
    alignItems: 'center',
    gap: 10,
  },
  emptyTitle: {
    color: '#fff',
    fontSize: 15,
    fontWeight: '800',
    textAlign: 'center',
  },
  emptyHint: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 12,
    textAlign: 'center',
    lineHeight: 18,
  },
  computingCard: {
    backgroundColor: 'rgba(167,139,250,0.14)',
    borderWidth: 1,
    borderColor: 'rgba(167,139,250,0.3)',
    borderRadius: 14,
    padding: 24,
    alignItems: 'center',
    gap: 10,
  },
  computingTxt: { color: Colors.violet, fontSize: 13, fontWeight: '700' },
  startCta: {
    backgroundColor: Colors.terracotta,
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Colors.terracottaLo,
  },
  startCtaTxt: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '900',
    letterSpacing: 0.3,
  },
  toast: {
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
  },
  toastSuccess: {
    backgroundColor: 'rgba(77,212,179,0.14)',
    borderColor: 'rgba(77,212,179,0.45)',
  },
  toastError: {
    backgroundColor: 'rgba(248,113,113,0.14)',
    borderColor: 'rgba(248,113,113,0.45)',
  },
  toastTxt: {
    color: '#fff',
    fontSize: 12.5,
    fontWeight: '700',
    lineHeight: 17,
  },
});
