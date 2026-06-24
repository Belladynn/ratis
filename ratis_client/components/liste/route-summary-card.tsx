/**
 * Liste — RouteSummaryCard (V5 strict iso, Itinéraire tab hero).
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste itineraire.png`.
 * Reference JSX    : `Ratis_handoff/lib/ratis-liste.jsx` lines 242-267.
 *
 * Layout
 * ------
 *   ┌─────────────────────────────────────────────┐
 *   │  TOTAL │ ÉCONOMISÉ │ TRAJET                  │
 *   │ 16,17€ │  -4,35€   │ 4.4 km · 42min          │
 *   └─────────────────────────────────────────────┘
 *
 *  - Same dark jar-pink gradient as the products-tab Total card.
 *  - Three columns separated by vertical dividers.
 *  - The "Trajet" cell is omitted when no distance/duration is available.
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTranslation } from 'react-i18next';

import { Colors } from '@/constants/theme';

export type RouteSummaryCardProps = {
  total: number;
  savings: number;
  distanceKm?: number | null;
  durationMin?: number | null;
  testID?: string;
};

function fmtMoney(amount: number): string {
  return amount.toFixed(2).replace('.', ',') + '€';
}

export function RouteSummaryCard({
  total,
  savings,
  distanceKm,
  durationMin,
  testID,
}: RouteSummaryCardProps) {
  const { t } = useTranslation();
  const showTrip =
    typeof distanceKm === 'number' || typeof durationMin === 'number';

  return (
    <View testID={testID ?? 'route-summary-card'} style={styles.outer}>
      <LinearGradient
        colors={[Colors.jarPinkBg1, Colors.jarPinkBg2]}
        start={{ x: 0, y: 0 }}
        end={{ x: 0.7, y: 1 }}
        style={styles.gradient}
      >
        <View style={styles.cornerGlow} pointerEvents="none" />

        <View style={styles.col}>
          <Text style={styles.label}>{t('liste.itinerary.summary_total')}</Text>
          <Text style={styles.value} testID="route-summary-card-total">
            {fmtMoney(total)}
          </Text>
        </View>

        <View style={styles.divider} />

        <View style={styles.col}>
          <Text style={[styles.label, styles.labelSavings]}>
            {t('liste.itinerary.summary_savings')}
          </Text>
          <Text
            style={[styles.value, styles.valueSavings]}
            testID="route-summary-card-savings"
          >
            -{fmtMoney(savings)}
          </Text>
        </View>

        {showTrip ? (
          <>
            <View style={styles.divider} />
            <View style={styles.col}>
              <Text style={styles.label}>
                {t('liste.itinerary.summary_trip')}
              </Text>
              <Text
                style={styles.valueTrip}
                testID="route-summary-card-trip"
              >
                {t('liste.itinerary.summary_trip_format', {
                  km: (distanceKm ?? 0).toFixed(1),
                  minutes: durationMin ?? 0,
                })}
              </Text>
            </View>
          </>
        ) : null}
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
    gap: 14,
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
    height: 36,
    backgroundColor: 'rgba(255,255,255,0.12)',
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
  valueTrip: {
    fontSize: 14,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: -0.3,
    marginTop: 4,
  },
});

export default RouteSummaryCard;
