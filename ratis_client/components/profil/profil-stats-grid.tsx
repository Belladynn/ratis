// ratis_client/components/profil/profil-stats-grid.tsx
//
// V5 Profil stats grid — port of `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 595-600 (the row of 3 `<StatTile>` instances).
//
// Anatomy : 3 equal-width tiles in a horizontal row. Each tile shows :
//   - emoji icon row 1
//   - large bold value row 2 (colour matches the metric : gold / violet / mint)
//   - uppercase label row 3 ("CAB" / "SCANS" / "ÉCONOMIES")
//
// Numeric formatting :
//   - CAB balance     → French NBSP-separated thousands (matches `formatBalanceN`)
//   - Scan count      → plain integer
//   - Savings         → integer euros + `€` suffix
//
// Tokens : the violet `#A78BFA` is `Colors.violet`, the gold `#FFB800` is
// `Colors.gold`, the mint `#4DD4B3` is the V5 cashback green (no token —
// the design system carries `Colors.coral` for danger but not a mint).
// Token derogation per `chunk-3-followups.md` § 10.

import React from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

import { Colors } from '@/constants/theme';

const SAVINGS_GREEN = '#4DD4B3';

/** Format an integer with non-breaking spaces every 3 digits (FR convention). */
function formatBalance(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

export type ProfilStatsGridProps = {
  cabBalance: number;
  scanCount: number;
  /** Whole euros — caller divides cents to euros (Math.round expected). */
  savingsEuros: number;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

type TileProps = {
  icon: string;
  value: string;
  label: string;
  color: string;
  testID: string;
};

function StatTile({ icon, value, label, color, testID }: TileProps) {
  return (
    <View
      testID={testID}
      style={[
        styles.tile,
        {
          borderColor: color + '33',
        },
      ]}
    >
      <Text style={styles.icon}>{icon}</Text>
      <Text style={[styles.value, { color }]}>{value}</Text>
      <Text style={styles.label}>{label}</Text>
    </View>
  );
}

export function ProfilStatsGrid({
  cabBalance,
  scanCount,
  savingsEuros,
  testID = 'profil-stats-grid',
  style,
}: ProfilStatsGridProps) {
  return (
    <View testID={testID} style={[styles.row, style]}>
      <StatTile
        testID="profil-stat-cab"
        icon="🪙"
        value={formatBalance(cabBalance)}
        label="CAB"
        color={Colors.gold}
      />
      <StatTile
        testID="profil-stat-scans"
        icon="📷"
        value={String(scanCount)}
        label="SCANS"
        color={Colors.violet}
      />
      <StatTile
        testID="profil-stat-savings"
        icon="💚"
        value={`${savingsEuros}€`}
        label="ÉCONOMIES"
        color={SAVINGS_GREEN}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: 8,
  },
  tile: {
    flex: 1,
    alignItems: 'center',
    gap: 4,
    paddingVertical: 12,
    paddingHorizontal: 10,
    borderRadius: 18,
    borderWidth: 1.5,
    backgroundColor: 'rgba(255,255,255,0.03)',
    // Single 3D-stacked drop shadow — RN does not composite multiple
    // box-shadows so we keep the heavy dark layer that conveys the V5 weight.
    shadowColor: 'rgba(0,0,0,0.4)',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 4,
  },
  icon: {
    fontSize: 18,
    marginBottom: 2,
    lineHeight: 22,
  },
  value: {
    fontSize: 18,
    fontWeight: '900',
    letterSpacing: -0.4,
    lineHeight: 18,
  },
  label: {
    fontSize: 9,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.55)',
    letterSpacing: 0.6,
  },
});

export default ProfilStatsGrid;
