// ratis_client/app/shop/index.tsx
//
// Boutique V1 — catalog screen (carrousel of active brands).
//
// UX (cf ARCH_boutique.md § UX flow) :
//   ┌───────────────────────────────────┐
//   │  ←  Boutique                      │
//   │     Solde : 47 500 CAB            │
//   │                                   │
//   │  Cette saison :                   │
//   │  ┌────────┬────────┐              │
//   │  │ Amazon │ Carre…│              │
//   │  │ from … │ from …│              │
//   │  └────────┴────────┘              │
//   │  ...                              │
//   │  Mes cartes cadeaux →            │
//   └───────────────────────────────────┘
//
// Tap sur une marque → écran `[brand_id].tsx` (dénominations).
// Tap sur "Mes cartes cadeaux" → V1 retourne sur Profil tab (la liste
// dédiée arrive en V1.x — cf brief).

import React, { useCallback } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { Card } from '@/components/design-system';
import { Colors, Radii, Spacing } from '@/constants/theme';
import { useShopCatalog, type ShopBrand } from '@/hooks/use-shop-catalog';
import { useCabBalance } from '@/hooks/use-cab-balance';
import { CAB_PER_EUR, V1_DENOMINATIONS_CENTS } from '@/types/shop';

const FROM_CAB = (V1_DENOMINATIONS_CENTS[0] / 100) * CAB_PER_EUR; // 25 000 CAB

export default function ShopCatalogScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const catalog = useShopCatalog();
  const cab = useCabBalance();

  const handleBackToProfil = useCallback(() => {
    router.back();
  }, [router]);

  const handleOpenBrand = useCallback(
    (brand: ShopBrand) => {
      router.push({
        pathname: '/shop/[brand_id]',
        params: { brand_id: brand.id },
      });
    },
    [router],
  );

  const handleOpenMyGiftCards = useCallback(() => {
    // V1 placeholder — there is no dedicated "Mes cartes cadeaux" screen yet.
    // Send the user back to Profil where the future entry will live.
    // V1.x will swap this for `/gift-cards` once the screen ships.
    router.push('/(tabs)/profil');
  }, [router]);

  return (
    <View style={styles.container} testID="shop-screen">
      <SafeAreaView edges={['top']} style={{ flex: 1 }}>
        {/* Header */}
        <View style={styles.header}>
          <Pressable
            testID="shop-back"
            accessibilityRole="button"
            accessibilityLabel={t('shop.back')}
            onPress={handleBackToProfil}
            style={styles.backBtn}
          >
            <Text style={styles.backTxt}>‹</Text>
          </Pressable>
          <Text style={styles.title}>{t('shop.title')}</Text>
          <View style={styles.backBtn} />
        </View>

        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
        >
          {/* Balance card */}
          <View style={styles.balanceCard} testID="shop-balance">
            <Text style={styles.balanceLabel}>{t('shop.balance_label')}</Text>
            <Text style={styles.balanceValue}>
              {formatCab(cab.balance)} CAB
            </Text>
          </View>

          <Text style={styles.subtitle}>{t('shop.season_subtitle')}</Text>

          {catalog.isLoading ? (
            <View style={styles.center} testID="shop-catalog-loading">
              <ActivityIndicator color={Colors.terracotta} />
            </View>
          ) : catalog.isError ? (
            <View style={styles.center} testID="shop-catalog-error">
              <Text style={styles.errorTxt}>{t('shop.catalog_error')}</Text>
              <Pressable
                testID="shop-catalog-retry"
                style={styles.retryBtn}
                onPress={() => void catalog.refetch()}
              >
                <Text style={styles.retryTxt}>{t('shop.retry')}</Text>
              </Pressable>
            </View>
          ) : catalog.data?.brands.length === 0 ? (
            <View style={styles.center} testID="shop-catalog-empty">
              <Text style={styles.emptyTxt}>{t('shop.empty')}</Text>
            </View>
          ) : (
            <View style={styles.grid} testID="shop-catalog-grid">
              {catalog.data?.brands.map((brand) => (
                <BrandTile
                  key={brand.id}
                  brand={brand}
                  fromCab={FROM_CAB}
                  fromLabel={t('shop.from_cab', {
                    cab: formatCab(FROM_CAB),
                  })}
                  onPress={() => handleOpenBrand(brand)}
                />
              ))}
            </View>
          )}

          <Pressable
            testID="shop-my-gift-cards"
            style={styles.footerLink}
            onPress={handleOpenMyGiftCards}
            accessibilityRole="button"
          >
            <Text style={styles.footerLinkTxt}>
              {t('shop.my_gift_cards')} ›
            </Text>
          </Pressable>
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function BrandTile({
  brand,
  fromCab: _fromCab,
  fromLabel,
  onPress,
}: {
  brand: ShopBrand;
  fromCab: number;
  fromLabel: string;
  onPress: () => void;
}) {
  return (
    <View style={styles.tileWrap}>
      <Card
        testID={`shop-brand-${brand.id}`}
        accessibilityLabel={brand.name}
        onPress={onPress}
        padding={Spacing.md}
      >
        <View style={styles.tileInner}>
          <View style={styles.logoWrap}>
            {brand.logo_url ? (
              <Image
                source={{ uri: brand.logo_url }}
                style={styles.logoImg}
                resizeMode="contain"
                accessibilityIgnoresInvertColors
              />
            ) : (
              <Text style={styles.logoFallback}>{brand.name.charAt(0)}</Text>
            )}
          </View>
          <Text style={styles.brandName} numberOfLines={1}>
            {brand.name}
          </Text>
          <Text style={styles.fromLabel}>{fromLabel}</Text>
        </View>
      </Card>
    </View>
  );
}

function formatCab(n: number): string {
  // 25000 → "25 000" (NBSP) — fr-style thin separator.
  return n.toLocaleString('fr-FR').replace(/ /g, ' ');
}

// ─────────────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  backBtn: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
  backTxt: { color: Colors.textPrimary, fontSize: 28, lineHeight: 28 },
  title: {
    fontSize: 17,
    fontWeight: '900',
    color: Colors.textPrimary,
    letterSpacing: -0.3,
  },
  content: { padding: 18, paddingBottom: 60, gap: 16 },
  balanceCard: {
    backgroundColor: 'rgba(255,184,0,0.10)',
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.35)',
    borderRadius: Radii.card,
    padding: Spacing.lg,
    alignItems: 'center',
    gap: 4,
  },
  balanceLabel: {
    fontSize: 11,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.55)',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  balanceValue: {
    fontSize: 24,
    fontWeight: '900',
    color: Colors.gold,
    letterSpacing: -0.4,
  },
  subtitle: {
    fontSize: 11,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.55)',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginTop: 4,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  tileWrap: {
    // 50% width minus the row gap.
    width: '47%',
    flexGrow: 1,
  },
  tileInner: {
    alignItems: 'center',
    gap: 6,
  },
  logoWrap: {
    width: 56,
    height: 56,
    borderRadius: 12,
    backgroundColor: 'rgba(255,255,255,0.06)',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 4,
    overflow: 'hidden',
  },
  logoImg: { width: 48, height: 48 },
  logoFallback: {
    fontSize: 24,
    fontWeight: '900',
    color: Colors.textPrimary,
  },
  brandName: {
    fontSize: 14,
    fontWeight: '800',
    color: Colors.textPrimary,
    textAlign: 'center',
  },
  fromLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: 'rgba(255,255,255,0.55)',
    textAlign: 'center',
  },
  center: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 32,
    gap: 12,
  },
  errorTxt: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.7)',
    textAlign: 'center',
    paddingHorizontal: 24,
  },
  emptyTxt: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.5)',
    textAlign: 'center',
    paddingHorizontal: 24,
  },
  retryBtn: {
    backgroundColor: Colors.terracotta,
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: Radii.btn,
  },
  retryTxt: {
    color: Colors.textPrimary,
    fontWeight: '900',
    fontSize: 13,
  },
  footerLink: {
    marginTop: 8,
    alignItems: 'center',
    paddingVertical: 14,
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderRadius: Radii.btn,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  footerLinkTxt: {
    color: Colors.textPrimary,
    fontSize: 13,
    fontWeight: '700',
  },
});
