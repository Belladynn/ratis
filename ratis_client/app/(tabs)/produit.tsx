// ratis_client/app/(tabs)/produit.tsx
//
// V5 Produit composition — port of `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 360-484 (`ProduitScreen`). Reads from existing hooks ; no new
// business logic introduced (R31).
//
// Layout :
//   1. ScreenBackground (V5 shared)
//   2. PageTitleBand "Fiche produit" (small) + leftIcon back + heart + share
//   3. Scrollable content :
//      - LocationPermissionBanner (when permission denied)
//      - ProduitHeroCard (emoji 80×80 + brand + name + EAN)
//      - ProductConsensusCard (best price + N stores + radius)
//      - SegmentedTabs : Prix · N | Infos
//      - tab=prices: ProductPriceRow × N (best gold + crown ; others +X%)
//      - tab=info  : ProduitInfoTable
//   4. Sticky bottom CTA "+ Ajouter à ma liste" (full-width, primary terracotta)
//
// Hooks consumed (no signature reinvention) :
//   - `useProductByEan(ean, { lat, lng })`         — product detail + nearby
//   - `useIsFavorite(ean)` / `useToggleFavorite()` — heart icon
//   - `useLocalSearchParams()` from expo-router    — read `?ean=` route param
//
// V1 caveats :
//   - `Add-to-list` CTA shows a confirmation toast for now ; the wire-up to
//     the shopping-list hook lives in chunk 7 (it needs the EAN-resolution
//     UX since the AddBar in Liste opens a sheet rather than POSTing direct).
//   - Backend `ProductInfo` doesn't expose `origin` / `weight` / structured
//     conservation (only `storage_type` enum). The Infos table falls back to
//     "—" rather than fabricating values.
//   - When the user lands on `/produit` without an `?ean=` param we render
//     a neutral placeholder ("Aucun produit sélectionné") instead of forcing
//     a default EAN — the V0 shipped a hardcoded fallback that confused
//     reviewers in alpha.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router, useLocalSearchParams } from 'expo-router';
import * as Location from 'expo-location';
import { useTranslation } from 'react-i18next';

import { ScreenBackground } from '@/components/ui/screen-background';
import { PageTitleBand } from '@/components/ui/page-title-band';
import { LocationPermissionBanner } from '@/components/ui/location-permission-banner';
import { ProduitHeroCard } from '@/components/produit/produit-hero-card';
import { ProductConsensusCard } from '@/components/produit/product-consensus-card';
import { ProductPriceRow } from '@/components/produit/product-price-row';
import {
  ProduitInfoTable,
  type InfoRow,
} from '@/components/produit/produit-info-table';
import { Button, SegmentedTabs, Toast } from '@/components/design-system';
import { Colors } from '@/constants/theme';

import { useIsFavorite, useToggleFavorite } from '@/hooks/use-favorites';
import { useProductByEan } from '@/hooks/use-product-by-ean';
import {
  useProductSearch,
  type ProductSearchHit,
} from '@/hooks/use-product-search';
import { useDefaultSuggestions } from '@/hooks/use-default-suggestions';
import { composeSearchHitSecondary } from '@/utils/product-search-hit';

type DetailTab = 'prices' | 'info';
type LocationStatus = 'loading' | 'granted' | 'denied' | 'error';

export default function ProduitScreen() {
  const { t } = useTranslation();
  // Warm the default-suggestions cache so the search empty-state on the
  // Produit tab (when no ?ean= param) can render its dropdown instantly
  // when the user focuses the search field. Same prefetch story as the
  // Liste tab — RQ dedupes by queryKey.
  useDefaultSuggestions();
  const params = useLocalSearchParams<{ ean?: string }>();
  const ean = params.ean ?? null;

  const [activeTab, setActiveTab] = useState<DetailTab>('prices');
  const [location, setLocation] = useState<{ lat: number; lng: number } | null>(
    null,
  );
  const [locationStatus, setLocationStatus] =
    useState<LocationStatus>('loading');
  const [locationBannerDismissed, setLocationBannerDismissed] = useState(false);
  const [toastVisible, setToastVisible] = useState(false);

  const requestLocation = useCallback(async () => {
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setLocationStatus('denied');
        return;
      }
      const loc = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });
      setLocation({
        lat: loc.coords.latitude,
        lng: loc.coords.longitude,
      });
      setLocationStatus('granted');
      setLocationBannerDismissed(false);
    } catch {
      setLocationStatus('error');
    }
  }, []);

  // Auto-request on mount — graceful degradation if denied.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (cancelled) return;
        if (status !== 'granted') {
          setLocationStatus('denied');
          return;
        }
        const loc = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        if (cancelled) return;
        setLocation({
          lat: loc.coords.latitude,
          lng: loc.coords.longitude,
        });
        setLocationStatus('granted');
      } catch {
        if (!cancelled) setLocationStatus('error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const { data, isLoading, isError, refetch } = useProductByEan(ean, {
    lat: location?.lat,
    lng: location?.lng,
  });

  const isFav = useIsFavorite(ean ?? undefined);
  const toggleFav = useToggleFavorite();

  const product = data?.product;
  const prices = useMemo(() => {
    const list = [...(data?.nearby_prices ?? [])];
    list.sort((a, b) => a.price_cents - b.price_cents);
    return list;
  }, [data?.nearby_prices]);

  // price_cents is already an integer number of cents (int-cents) — consumed
  // directly, no float-euro arithmetic.
  const bestPriceCents = prices.length > 0 ? prices[0].price_cents : null;
  const storesCount = prices.length;

  const infoRows = useMemo<InfoRow[]>(() => {
    if (!product) return [];
    const dash = t('produit.info_table.value_unknown');
    const qty =
      product.product_quantity != null && product.product_quantity_unit
        ? `${product.product_quantity} ${product.product_quantity_unit}`
        : dash;
    return [
      { key: t('produit.info_table.quantity'), value: qty },
      { key: t('produit.info_table.brand'), value: product.brand ?? dash },
      { key: t('produit.info_table.origin'), value: dash },
      { key: t('produit.info_table.weight'), value: qty },
      {
        key: t('produit.info_table.conservation'),
        value: product.storage_type ?? dash,
      },
    ];
  }, [product, t]);

  const handleShare = useCallback(() => {
    if (!product) return;
    Share.share({
      message: t('produit.share_message', { name: product.name }),
    }).catch(() => {});
  }, [product, t]);

  const handleAddToList = useCallback(() => {
    // V1 strict-iso : visual only — wiring vers shopping-list est tracké en
    // chunk 7. Le toast "Ajouté à ta liste" reproduit le feedback V5
    // (`ratis-other-tabs.jsx` → `showToast('Ajouté à la liste')`).
    setToastVisible(true);
  }, []);

  const handleToggleFavorite = useCallback(() => {
    if (!ean) return;
    toggleFav.mutate({ ean, favorited: !isFav });
  }, [ean, isFav, toggleFav]);

  const tabsConfig = useMemo(
    () => [
      {
        id: 'prices',
        label: t('produit.tabs.prices_count', { count: storesCount }),
      },
      { id: 'info', label: t('produit.tabs.info') },
    ],
    [storesCount, t],
  );

  const headerLeftIcon = (
    <Pressable
      testID="btn-back"
      accessibilityRole="button"
      accessibilityLabel={t('produit.back')}
      onPress={() => router.back()}
      hitSlop={8}
      style={styles.hdrBtn}
    >
      <Text style={styles.hdrIcon}>←</Text>
    </Pressable>
  );

  const headerRightIcons = [
    <Pressable
      key="fav"
      testID="btn-favorite"
      accessibilityRole="button"
      accessibilityLabel={t('produit.favorite_a11y')}
      onPress={handleToggleFavorite}
      hitSlop={8}
      style={styles.hdrBtn}
    >
      <Text style={[styles.hdrIcon, isFav ? styles.hdrIconFav : null]}>
        {isFav ? '♥' : '♡'}
      </Text>
    </Pressable>,
    <Pressable
      key="share"
      testID="btn-share"
      accessibilityRole="button"
      accessibilityLabel={t('produit.share_a11y')}
      onPress={handleShare}
      hitSlop={8}
      style={styles.hdrBtn}
    >
      <Text style={styles.hdrIcon}>↗</Text>
    </Pressable>,
  ];

  // -----------------------------------------------------------------------
  // No EAN — surface a text-search input so the PO's flow « ouvrir
  // l'onglet Produit pour chercher quelque chose » works (wave 4 Bug 4).
  // Selecting a hit navigates to the same tab with ``?ean=`` so the
  // detail layout takes over with no double-render.
  // -----------------------------------------------------------------------
  if (!ean) {
    return <ProduitSearchEmptyState headerLeftIcon={headerLeftIcon} />;
  }

  return (
    <View style={styles.container} testID="produit-screen">
      <ScreenBackground />
      <SafeAreaView edges={['top']} style={{ flex: 1 }}>
        <PageTitleBand
          title={t('produit.page_title')}
          titleSize="small"
          leftIcon={headerLeftIcon}
          rightIcons={headerRightIcons}
        />

        {isLoading && !data ? (
          <View style={styles.center} testID="produit-loading">
            <ActivityIndicator color={Colors.violet} />
          </View>
        ) : null}

        {isError ? (
          <View style={styles.center}>
            <Text style={styles.errorTitle}>
              {t('produit.error_not_found')}
            </Text>
            <Pressable
              style={styles.retryBtn}
              onPress={() => refetch()}
              testID="produit-retry"
            >
              <Text style={styles.retryTxt}>{t('errors.retry')}</Text>
            </Pressable>
          </View>
        ) : null}

        {!isLoading && !isError && product ? (
          <>
            <ScrollView contentContainerStyle={styles.content}>
              {locationStatus === 'denied' && !locationBannerDismissed ? (
                <LocationPermissionBanner
                  context="produit"
                  onRequestPermission={requestLocation}
                  onDismiss={() => setLocationBannerDismissed(true)}
                />
              ) : null}

              <ProduitHeroCard
                brand={product.brand}
                name={product.name}
                ean={product.ean}
                photoUrl={product.photo_url}
              />

              <ProductConsensusCard
                priceCents={bestPriceCents}
                storesCount={storesCount}
                locationDenied={locationStatus === 'denied'}
              />

              <SegmentedTabs
                tabs={tabsConfig}
                activeId={activeTab}
                onChange={(id) => setActiveTab(id as DetailTab)}
                testID="produit-tabs"
              />

              {activeTab === 'prices' ? (
                prices.length > 0 ? (
                  <View style={styles.pricesCard}>
                    {prices.map((p, idx) => {
                      const bestCents = bestPriceCents ?? p.price_cents;
                      const cents = p.price_cents;
                      const deltaPct =
                        bestCents > 0
                          ? ((cents - bestCents) / bestCents) * 100
                          : 0;
                      return (
                        <ProductPriceRow
                          key={p.store_id}
                          storeName={p.store_name}
                          distanceKm={p.distance_km}
                          priceCents={cents}
                          isBest={idx === 0}
                          deltaPct={deltaPct}
                          isLast={idx === prices.length - 1}
                        />
                      );
                    })}
                  </View>
                ) : locationStatus === 'denied' ? (
                  <View style={styles.emptyBanner}>
                    <Text style={styles.emptyTxt}>
                      {t('produit.empty_location_denied')}
                    </Text>
                  </View>
                ) : (
                  <View style={styles.emptyBanner}>
                    <Text style={styles.emptyTxt}>
                      {t('produit.empty_no_prices')}
                    </Text>
                  </View>
                )
              ) : null}

              {activeTab === 'info' ? (
                <ProduitInfoTable rows={infoRows} />
              ) : null}
            </ScrollView>

            {/* Sticky add-to-list CTA. */}
            <View pointerEvents="box-none" style={styles.ctaWrap}>
              <Button
                variant="primary"
                size="md"
                fullWidth
                label={t('produit.add_cta')}
                icon={<Text style={styles.ctaIcon}>＋</Text>}
                onPress={handleAddToList}
                testID="btn-add-to-list"
                accessibilityLabel={t('produit.add_cta_a11y')}
              />
            </View>
          </>
        ) : null}
      </SafeAreaView>

      <Toast
        visible={toastVisible}
        message={t('produit.added_toast')}
        onDismiss={() => setToastVisible(false)}
        testID="produit-add-toast"
      />
    </View>
  );
}

/**
 * Wave-4 Bug 4 — text-search empty state. Surfaces a search input with
 * a results dropdown so the user can pick a product without going
 * through Scan first. Tap → ``router.replace`` to the same Produit tab
 * with the resolved ``?ean=`` so the detail layout renders next.
 *
 * Implementation note : we mount the search input + dropdown directly
 * here (rather than reusing ``AddBar``) because the visual treatment is
 * the Produit-tab one (centered card on a dark background) and the
 * Liste AddBar carries the suggestions/templates/voice icons which are
 * out of place on a product-search surface.
 */
function ProduitSearchEmptyState({
  headerLeftIcon,
}: {
  headerLeftIcon: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [focused, setFocused] = useState(false);
  const { data: searchData, isFetching } = useProductSearch(query, {
    limit: 12,
  });
  const hits = searchData?.items ?? [];
  const showDropdown = query.trim().length >= 2;
  const empty = !isFetching && hits.length === 0;

  const handlePickHit = useCallback(
    (hit: ProductSearchHit) => {
      // ``replace`` (not ``push``) so the search empty-state isn't kept
      // in the navigation back-stack — the user would otherwise tap
      // back from the detail screen and land on an empty input.
      router.replace({
        pathname: '/(tabs)/produit',
        params: { ean: hit.ean },
      });
    },
    [],
  );

  return (
    <View style={styles.container} testID="produit-empty">
      <ScreenBackground />
      <SafeAreaView edges={['top']} style={{ flex: 1 }}>
        <PageTitleBand
          title={t('produit.page_title')}
          titleSize="small"
          leftIcon={headerLeftIcon}
        />
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={styles.searchContent}
        >
          <Text style={styles.searchTitle}>{t('produit.search_title')}</Text>
          <Text style={styles.searchHint}>{t('produit.search_hint')}</Text>
          <View
            testID="produit-search-bar"
            style={[
              styles.searchBar,
              focused ? styles.searchBarFocused : null,
            ]}
          >
            <Text style={styles.searchIcon}>🔍</Text>
            <TextInput
              testID="produit-search-input"
              value={query}
              onChangeText={setQuery}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder={t('produit.search_placeholder')}
              placeholderTextColor="rgba(255,255,255,0.35)"
              style={styles.searchInput}
              autoFocus
            />
          </View>
          {showDropdown ? (
            <View
              testID="produit-search-dropdown"
              style={styles.searchDropdown}
            >
              {hits.map((hit) => {
                // Wave 9 — secondary line « brand · qty · 🇫🇷 · 🌱 »
                // so the user can tell apart identical-named hits
                // without opening each one. ``null`` => single-line.
                const secondary = composeSearchHitSecondary(hit);
                return (
                  <Pressable
                    key={hit.ean}
                    testID={`produit-search-hit-${hit.ean}`}
                    accessibilityRole="button"
                    accessibilityLabel={
                      secondary ? `${hit.name} — ${secondary}` : hit.name
                    }
                    onPress={() => handlePickHit(hit)}
                    style={styles.searchHitRow}
                  >
                    <Text style={styles.searchHitName} numberOfLines={1}>
                      {hit.name}
                    </Text>
                    {secondary ? (
                      <Text
                        testID={`produit-search-hit-${hit.ean}-secondary`}
                        style={styles.searchHitSecondary}
                        numberOfLines={1}
                      >
                        {secondary}
                      </Text>
                    ) : null}
                  </Pressable>
                );
              })}
              {empty ? (
                <View
                  testID="produit-search-empty"
                  style={styles.searchHitRow}
                >
                  <Text style={styles.searchEmpty}>
                    {t('produit.search_no_results')}
                  </Text>
                </View>
              ) : null}
            </View>
          ) : (
            <Text style={styles.emptyHint}>
              {t('produit.empty_no_ean_hint')}
            </Text>
          )}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bg },
  content: {
    padding: 14,
    paddingBottom: 140,
    gap: 12,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    gap: 8,
  },
  errorTitle: {
    color: Colors.textPrimary,
    fontSize: 14,
    fontWeight: '800',
    marginBottom: 12,
  },
  emptyTitle: {
    color: Colors.textPrimary,
    fontSize: 14,
    fontWeight: '800',
  },
  emptyHint: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 12,
    fontWeight: '600',
    textAlign: 'center',
  },
  retryBtn: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 14,
    backgroundColor: 'rgba(139,92,246,0.22)',
    borderWidth: 1,
    borderColor: 'rgba(139,92,246,0.4)',
  },
  retryTxt: { color: Colors.violet, fontSize: 12, fontWeight: '700' },
  hdrIcon: {
    fontSize: 16,
    color: Colors.textPrimary,
    fontWeight: '800',
  },
  hdrIconFav: { color: '#FB7185' },
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
  pricesCard: {
    backgroundColor: Colors.surface,
    borderWidth: 1.5,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 18,
    overflow: 'hidden',
    shadowColor: 'rgba(0,0,0,0.35)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  emptyBanner: {
    padding: 24,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 18,
    backgroundColor: 'rgba(255,255,255,0.03)',
  },
  emptyTxt: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 12,
    fontWeight: '600',
  },
  ctaWrap: {
    position: 'absolute',
    left: 14,
    right: 14,
    bottom: 88,
  },
  ctaIcon: {
    fontSize: 14,
    fontWeight: '900',
    color: Colors.textPrimary,
  },
  // ── wave-4 Bug 4 search empty-state ────────────────────────────────
  searchContent: {
    padding: 16,
    gap: 12,
  },
  searchTitle: {
    color: Colors.textPrimary,
    fontSize: 16,
    fontWeight: '800',
    textAlign: 'center',
    marginTop: 20,
  },
  searchHint: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 12,
    textAlign: 'center',
    marginBottom: 4,
  },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: 'rgba(255,255,255,0.08)',
    backgroundColor: 'rgba(255,255,255,0.04)',
  },
  searchBarFocused: {
    borderColor: 'rgba(218,119,86,0.55)',
  },
  searchIcon: {
    fontSize: 14,
  },
  searchInput: {
    flex: 1,
    color: '#fff',
    fontSize: 14,
    fontWeight: '700',
    paddingVertical: 4,
  },
  searchDropdown: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    backgroundColor: 'rgba(20,30,38,0.96)',
    overflow: 'hidden',
  },
  searchHitRow: {
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  searchHitName: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '700',
  },
  searchHitSecondary: {
    // Wave 9 — secondary line composes brand · quantity · 🇫🇷 · 🌱.
    // No uppercase / letter-spacing because the emoji segments lose
    // fidelity (🇫🇷 regional-indicator pair gets clipped) and raw
    // quantities (« 1 kg ») read worst in all-caps.
    color: 'rgba(255,255,255,0.55)',
    fontSize: 11,
    fontWeight: '600',
    marginTop: 2,
  },
  searchEmpty: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 13,
    fontStyle: 'italic',
  },
});
