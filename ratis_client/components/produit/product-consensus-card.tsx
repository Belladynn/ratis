/**
 * Product consensus card — V5 strict iso (`Produit.png`, jar-pink band).
 *
 * Source JSX : `Ratis_handoff/lib/ratis-other-tabs.jsx` lignes 402-435.
 *
 * Anatomy :
 *
 *   ┌────────────────────────────────────────────┐
 *   │ ┌────┐  MEILLEUR PRIX                      │
 *   │ │ 🫙 │  4,20€                              │
 *   │ └────┘  7 magasins · 4 km autour           │
 *   └────────────────────────────────────────────┘
 *
 * Visual contract (immuable, JSX iso) :
 *   - Bg gradient `#2A1A1A → #1F1212` (160deg) — `Colors.jarPinkBg1/2`
 *   - Border : `1.5px rgba(255,107,157,0.45)` jar-pink glow
 *   - Icon tile 56×56, gradient pink `#FF8FB3 → #FF6B9D`, dark border, jar SVG
 *     (rendered inline via `react-native-svg`, no asset port needed — see
 *     chunk 5 brief context)
 *   - Label "MEILLEUR PRIX" : weight 800, size 9, letter-spacing 0.8
 *   - Big price : weight 900, size 28, color jar-pink
 *   - Sub : weight 700, size 11, opacity 0.65
 *
 * Edge cases :
 *   - `priceCents = null` → "—" (no consensus)
 *   - `storesCount = 0` + `locationDenied` → location hint
 *   - `storesCount = 0` + permission OK → "Aucun prix disponible"
 *   - `storesCount = 1` → "1 magasin · {km} km autour" (ou …à proximité)
 *   - `storesCount > 1` → "{n} magasins · {km} km autour" (ou …à proximité)
 *
 * Token derogation : same discipline as chunk 3/4 — JSX numeric values kept
 * as-is, theme tokens (`Colors.jarPink*`) consumed where they map.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Svg, { Path } from 'react-native-svg';
import { useTranslation } from 'react-i18next';

import { Colors } from '@/constants/theme';

export interface ProductConsensusCardProps {
  priceCents: number | null;
  storesCount: number;
  /**
   * Search radius in km used to populate `storesCount`. When provided, the
   * sub-line reads "{n} magasins · {km} km autour" (V5). When omitted,
   * falls back to the legacy "à proximité" copy.
   */
  radiusKm?: number | null;
  locationDenied?: boolean;
  testID?: string;
}

function formatEuros(cents: number): string {
  const euros = cents / 100;
  return `${euros.toFixed(2).replace('.', ',')}€`;
}

export function ProductConsensusCard({
  priceCents,
  storesCount,
  radiusKm,
  locationDenied,
  testID,
}: ProductConsensusCardProps) {
  const { t } = useTranslation();
  const priceLabel = priceCents != null ? formatEuros(priceCents) : '—';

  // V5 prefers the "{n} magasins · {km} km autour" copy. We round the radius
  // to 1 decimal (typical user-facing precision) and only render the variant
  // with the radius when the caller provides it.
  const km =
    radiusKm != null ? Math.max(0, Math.round(radiusKm * 10) / 10) : null;

  let sub: string;
  if (storesCount === 1) {
    sub =
      km != null
        ? t('produit.consensus_sub.one_store', { km })
        : t('produit.consensus_sub.one_store_no_radius');
  } else if (storesCount > 1) {
    sub =
      km != null
        ? t('produit.consensus_sub.many_stores', { count: storesCount, km })
        : t('produit.consensus_sub.many_stores_no_radius', {
            count: storesCount,
          });
  } else if (locationDenied) {
    sub = t('produit.consensus_sub.location_denied');
  } else {
    sub = t('produit.consensus_sub.no_stores');
  }

  return (
    <LinearGradient
      testID={testID}
      colors={[Colors.jarPinkBg1, Colors.jarPinkBg2]}
      start={{ x: 0, y: 0 }}
      end={{ x: 0.85, y: 1 }}
      style={styles.card}
    >
      <LinearGradient
        colors={[Colors.jarPinkHi, Colors.jarPink]}
        start={{ x: 0, y: 0 }}
        end={{ x: 0, y: 1 }}
        style={styles.iconTile}
      >
        <Svg width={26} height={26} viewBox="0 0 24 24" fill="none">
          <Path
            d="M12 2L4 7v10l8 5 8-5V7l-8-5z"
            stroke="#fff"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <Path
            d="M12 22V12M4 7l8 5 8-5"
            stroke="#fff"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </Svg>
      </LinearGradient>
      <View style={styles.body}>
        <Text style={styles.label}>{t('produit.consensus_label')}</Text>
        <Text style={styles.price}>{priceLabel}</Text>
        <Text style={styles.sub} numberOfLines={1}>
          {sub}
        </Text>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingVertical: 16,
    paddingHorizontal: 18,
    borderRadius: 18,
    borderWidth: 1.5,
    borderColor: 'rgba(255,107,157,0.45)',
    overflow: 'hidden',
    shadowColor: 'rgba(80,20,40,0.85)',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  iconTile: {
    width: 56,
    height: 56,
    borderRadius: 14,
    borderWidth: 2,
    borderColor: 'rgba(180,40,80,0.8)',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  body: { flex: 1 },
  label: {
    fontSize: 9,
    fontWeight: '800',
    color: 'rgba(255,107,157,0.9)',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  price: {
    fontSize: 28,
    fontWeight: '900',
    color: Colors.jarPink,
    letterSpacing: -0.8,
    lineHeight: 32,
    marginTop: 2,
  },
  sub: {
    fontSize: 11,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.65)',
    marginTop: 4,
  },
});

export default ProductConsensusCard;
