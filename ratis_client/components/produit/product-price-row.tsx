/**
 * Product price row — V5 strict iso (`Produit.png`, list rows under tabs).
 *
 * Source JSX : `Ratis_handoff/lib/ratis-other-tabs.jsx` lignes 321-358.
 *
 * Anatomy :
 *
 *   👑  Auchan Nation                   4,20€
 *       2.8 km                           MEILLEUR
 *
 *       Carrefour Voltaire              4,50€
 *       1.2 km                            +7%
 *
 * Visual contract (immuable, JSX iso) :
 *   - Best row : gold tinted bg `rgba(255,184,0,0.08)` + 👑 emoji medallion
 *     (24×24 gold gradient, dark border, 3D shadow). Store name + price in
 *     gold (`Colors.gold`). Sub label "MEILLEUR" gold uppercase weight 800.
 *   - Other rows : transparent bg, no medallion (24×24 spacer for alignment).
 *     Store name + price in white. Right-side delta `+X%` in coral/red
 *     (`#FB7185`) weight 700 — the JSX uses the lighter coral red here.
 *   - Distance line under store name : opacity 0.5, weight 700, size 10.
 *   - Bottom border `rgba(255,255,255,0.06)` except last row (caller hides
 *     via `isLast`).
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTranslation } from 'react-i18next';

import { Colors } from '@/constants/theme';

export interface ProductPriceRowProps {
  storeName: string;
  distanceKm: number;
  priceCents: number;
  /** Best price in the list (gold tinted row + crown). */
  isBest?: boolean;
  /** % above best, only used when `isBest=false` (rounded with `Math.round`). */
  deltaPct?: number;
  /** Hide the bottom border (last row of the list). */
  isLast?: boolean;
  testID?: string;
}

function formatPrice(cents: number): string {
  return `${(cents / 100).toFixed(2).replace('.', ',')}€`;
}

function formatDistance(km: number): string {
  return `${km.toFixed(1).replace('.', ',')} km`;
}

export function ProductPriceRow({
  storeName,
  distanceKm,
  priceCents,
  isBest,
  deltaPct,
  isLast,
  testID,
}: ProductPriceRowProps) {
  const { t } = useTranslation();
  const priceText = formatPrice(priceCents);
  const distText = formatDistance(distanceKm);

  return (
    <View
      testID={testID}
      style={[
        styles.row,
        isBest ? styles.rowBest : null,
        isLast ? styles.rowNoBorder : null,
      ]}
    >
      {isBest ? (
        <LinearGradient
          colors={['#FFE066', '#FFB800']}
          start={{ x: 0, y: 0 }}
          end={{ x: 0, y: 1 }}
          style={styles.medallion}
          testID="best-medallion"
        >
          <Text style={styles.crown}>👑</Text>
        </LinearGradient>
      ) : (
        <View style={styles.medallionSpacer} />
      )}
      <View style={styles.info}>
        <Text
          style={[styles.name, isBest ? styles.nameBest : null]}
          numberOfLines={1}
        >
          {storeName}
        </Text>
        <Text style={styles.dist}>{distText}</Text>
      </View>
      <View style={styles.priceBlock}>
        <Text
          testID="price-val"
          style={[styles.val, isBest ? styles.valBest : null]}
        >
          {priceText}
        </Text>
        {isBest ? (
          <Text style={styles.bestLabel}>
            {t('produit.price_row.best_label')}
          </Text>
        ) : deltaPct != null && deltaPct > 0 ? (
          <Text style={styles.delta}>+{Math.round(deltaPct)}%</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  rowBest: {
    backgroundColor: 'rgba(255,184,0,0.08)',
  },
  rowNoBorder: {
    borderBottomWidth: 0,
  },
  medallion: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: '#B47800',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  crown: { fontSize: 12 },
  medallionSpacer: { width: 24, height: 24, flexShrink: 0 },
  info: { flex: 1, minWidth: 0 },
  name: {
    fontSize: 13,
    fontWeight: '800',
    color: Colors.textPrimary,
    letterSpacing: -0.2,
  },
  nameBest: { color: Colors.gold },
  dist: {
    fontSize: 10,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.5)',
    marginTop: 2,
  },
  priceBlock: { alignItems: 'flex-end' },
  val: {
    fontSize: 14,
    fontWeight: '900',
    color: Colors.textPrimary,
    letterSpacing: -0.3,
  },
  valBest: { color: Colors.gold },
  bestLabel: {
    fontSize: 9,
    fontWeight: '800',
    color: Colors.gold,
    marginTop: 1,
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  },
  delta: {
    fontSize: 9,
    fontWeight: '700',
    color: '#FB7185',
    marginTop: 1,
  },
});

export default ProductPriceRow;
