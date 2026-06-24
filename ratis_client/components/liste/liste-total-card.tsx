/**
 * Liste — Total card (V5 strict iso).
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste Courses.png`.
 * Reference JSX    : `Ratis_handoff/lib/ratis-liste.jsx` lines 150-185.
 *
 * Layout
 * ------
 *   ┌──────────────────────────────────────────────┐
 *   │  TOTAL ESTIMÉ          │   ÉCONOMIES         │
 *   │  16,17€                │   -4,35€            │
 *   │  1 coché · 1,85€       │   après optim.      │
 *   └──────────────────────────────────────────────┘
 *
 *  - Background : dark "jar pink" gradient (`#2A1A1A → #1F1212`).
 *  - Border : 1.5px `rgba(255,107,157,0.3)`, radius 18.
 *  - Subtle radial corner glow (jarPink) — purely decorative.
 *  - Two columns separated by a 1px vertical divider.
 *  - Savings amount is rendered in `jarPink` and prefixed by a `-`.
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTranslation } from 'react-i18next';

import { Colors } from '@/constants/theme';

export type ListeTotalCardProps = {
  /** Total estimated cost of the list (already in major currency unit). */
  total: number;
  /** Savings amount (positive number) to display as `-X,YY€`. */
  savings: number;
  /** Number of items currently checked. */
  checkedCount: number;
  /** Sum of (est × qty) for the checked items, in major unit. */
  checkedTotal: number;
  /** When true, the savings subtitle "après optimisation" is hidden. */
  routeReady?: boolean;
  testID?: string;
};

function fmt(amount: number): string {
  return amount.toFixed(2).replace('.', ',') + '€';
}

export function ListeTotalCard({
  total,
  savings,
  checkedCount,
  checkedTotal,
  routeReady = false,
  testID,
}: ListeTotalCardProps) {
  const { t } = useTranslation();
  const checkedKey =
    checkedCount > 1
      ? 'liste.total_card.checked_format_plural'
      : 'liste.total_card.checked_format';
  const checkedLine = t(checkedKey, {
    n: checkedCount,
    amount: fmt(checkedTotal),
  });

  return (
    <View testID={testID ?? 'liste-total-card'} style={styles.outer}>
      <LinearGradient
        colors={[Colors.jarPinkBg1, Colors.jarPinkBg2]}
        start={{ x: 0, y: 0 }}
        end={{ x: 0.7, y: 1 }}
        style={styles.gradient}
      >
        <View style={styles.cornerGlow} pointerEvents="none" />
        <View style={styles.col}>
          <Text style={styles.label}>
            {t('liste.total_card.total_label')}
          </Text>
          <Text style={styles.value} testID="liste-total-card-total">
            {fmt(total)}
          </Text>
          {checkedCount > 0 && (
            <Text style={styles.sub} testID="liste-total-card-checked">
              {checkedLine}
            </Text>
          )}
        </View>

        <View style={styles.divider} />

        <View style={styles.col}>
          <Text style={[styles.label, styles.labelSavings]}>
            {t('liste.total_card.savings_label')}
          </Text>
          <Text
            style={[styles.value, styles.valueSavings]}
            testID="liste-total-card-savings"
          >
            -{fmt(savings)}
          </Text>
          {!routeReady && (
            <Text style={styles.sub}>
              {t('liste.total_card.savings_subtitle')}
            </Text>
          )}
        </View>
      </LinearGradient>
    </View>
  );
}

const styles = StyleSheet.create({
  outer: {
    borderRadius: 18,
    overflow: 'hidden',
    borderWidth: 1.5,
    borderColor: 'rgba(255,107,157,0.3)',
  },
  gradient: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    position: 'relative',
  },
  cornerGlow: {
    position: 'absolute',
    top: -30,
    right: -30,
    width: 120,
    height: 120,
    borderRadius: 60,
    backgroundColor: 'rgba(255,107,157,0.12)',
  },
  col: {
    flex: 1,
  },
  divider: {
    width: 1,
    height: 40,
    backgroundColor: 'rgba(255,255,255,0.10)',
  },
  label: {
    fontSize: 9,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.5)',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  labelSavings: {
    color: 'rgba(255,107,157,0.9)',
  },
  value: {
    fontSize: 22,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: -0.6,
    marginTop: 2,
  },
  valueSavings: {
    color: Colors.jarPink,
  },
  sub: {
    fontSize: 10,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.5)',
    marginTop: 2,
  },
});

export default ListeTotalCard;
