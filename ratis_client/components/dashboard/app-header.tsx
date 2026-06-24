// ratis_client/components/dashboard/app-header.tsx
//
// Sticky V5 dashboard header — port of `Ratis_handoff/lib/ratis-real-v4.jsx`
// `function AppHeader` (lines 61-108).
//
// Anatomy (left → right) :
//   1. Season label "SAISON · NIV. {level}" + thin gold XP progress bar
//   2. CAB balance pill (coin icon + thousand-separated balance)
//   3. 3 icon buttons : 🎁 Shop, 🏆 Achievements (+badge), 📅 Calendar (+badge)
//
// Hooks consumed :
//   - `useCabBalance()` for the live balance (already used PR4.1)
//   - `useBattlepass()` for level + XP progression
//
// Badge values for "21 achievements" / "5 calendar items" are V1 hardcoded
// placeholders — the JSX `RatisAchievementsUI.TrophyButton` derived `unlocked`
// from a window global, and the `missionsBadge` came from
// `activeMissionsCount`. We keep that contract by accepting the badge values
// as optional props (defaulted to 0 — no badge) so consumers can wire them
// later without forcing this component to know the count derivation rules.
//
// V1 limitations / follow-ups :
//   - Action handlers (onShop / onAchievements / onCalendar) are placeholders
//     that no-op when undefined ; the dashboard composition will route them
//     to the corresponding modals once chunk 7 lands.
//   - The greeting "Bonjour" + contextual line on the JSX dashboard sits
//     OUTSIDE the header (top of the scroll area). This component intentionally
//     restricts itself to the JSX `AppHeader` scope ; the greeting is rendered
//     by `app/(tabs)/index.tsx` directly.

import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Colors, Typography } from '@/constants/theme';
import { ProgressBar } from '@/components/design-system';
import { useCabBalance } from '@/hooks/use-cab-balance';
import { useBattlepass } from '@/hooks/use-battlepass';

export type AppHeaderProps = {
  achievementsBadge?: number;
  calendarBadge?: number;
  onShop?: () => void;
  onAchievements?: () => void;
  onCalendar?: () => void;
  /**
   * Bug 8 (PO ticket 2026-05-12) — Boutique is alpha-unavailable. When
   * `shopDisabled` is true the 🎁 icon is greyed and non-pressable. The
   * default is `true` so callers must opt-IN to a working shop icon
   * (V2 will flip the default once the Runa KYB lands).
   */
  shopDisabled?: boolean;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

/** Format a number with non-breaking spaces every 3 digits (FR convention). */
export function formatBalanceN(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function CoinIcon({ size = 14 }: { size?: number }) {
  return (
    <View
      style={[
        styles.coinIcon,
        { width: size, height: size, borderRadius: size / 2 },
      ]}
    >
      <Text style={[styles.coinChar, { fontSize: size * 0.6 }]}>€</Text>
    </View>
  );
}

function IconButton({
  emoji,
  badge,
  onPress,
  disabled,
  testID,
}: {
  emoji: string;
  badge?: number;
  onPress?: () => void;
  disabled?: boolean;
  testID?: string;
}) {
  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      disabled={disabled}
      testID={testID}
      accessibilityState={{ disabled: !!disabled }}
      style={({ pressed }) => [
        styles.iconBtn,
        pressed && !disabled && styles.iconBtnPressed,
        disabled && styles.iconBtnDisabled,
      ]}
      accessibilityRole="button"
    >
      <Text style={styles.iconBtnEmoji}>{emoji}</Text>
      {badge && badge > 0 ? (
        <View style={styles.iconBtnBadge}>
          <Text style={styles.iconBtnBadgeText}>{badge}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

export function AppHeader({
  achievementsBadge = 0,
  calendarBadge = 0,
  onShop,
  onAchievements,
  onCalendar,
  shopDisabled = true,
  testID = 'app-header',
  style,
}: AppHeaderProps) {
  const insets = useSafeAreaInsets();
  const { balance } = useCabBalance();
  const battlepass = useBattlepass();

  const level = battlepass.data?.current_level ?? 1;
  const xpCurrent = battlepass.data?.xp_current ?? 0;
  const xpNext = battlepass.data?.xp_next_level ?? 1;
  const seasonProgress = xpNext > 0 ? Math.max(0, Math.min(1, xpCurrent / xpNext)) : 0;

  return (
    <View
      testID={testID}
      style={[styles.root, { paddingTop: insets.top + 10 }, style]}
    >
      <View style={styles.seasonCol}>
        <Text testID="app-header-season-label" style={styles.seasonLabel}>
          SAISON · NIV. {level}
        </Text>
        <ProgressBar
          testID="app-header-season-progress"
          variant="gold"
          value={seasonProgress}
          height={4}
          shimmer={false}
        />
      </View>
      <View style={styles.balancePill} testID="app-header-balance">
        <CoinIcon size={14} />
        <Text style={styles.balanceText}>{formatBalanceN(balance)}</Text>
      </View>
      <IconButton
        testID="app-header-shop"
        emoji="🎁"
        onPress={onShop}
        disabled={shopDisabled}
      />
      <IconButton
        testID="app-header-achievements"
        emoji="🏆"
        badge={achievementsBadge}
        onPress={onAchievements}
      />
      <IconButton
        testID="app-header-calendar"
        emoji="📅"
        badge={calendarBadge}
        onPress={onCalendar}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingBottom: 10,
    backgroundColor: '#162028',
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  seasonCol: {
    flex: 1,
    minWidth: 0,
    flexDirection: 'column',
    gap: 5,
  },
  seasonLabel: {
    ...Typography.label,
    fontSize: 9,
    color: Colors.textSecondary,
    letterSpacing: 1,
  },
  balancePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 4,
  },
  balanceText: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 13,
    color: Colors.textPrimary,
    letterSpacing: -0.26,
  },
  coinIcon: {
    backgroundColor: Colors.gold,
    borderWidth: 1,
    borderColor: Colors.goldLo,
    alignItems: 'center',
    justifyContent: 'center',
  },
  coinChar: {
    fontWeight: '900',
    color: 'rgba(74,52,14,0.8)',
    lineHeight: 10,
  },
  iconBtn: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconBtnPressed: {
    opacity: 0.7,
  },
  iconBtnDisabled: {
    // Bug 8 — disabled icon buttons (currently only the 🎁 shop icon
    // while the V1 Boutique is alpha-unavailable) read as muted so the
    // user knows the feature is acknowledged but not pressable yet.
    opacity: 0.4,
  },
  iconBtnEmoji: {
    fontSize: 15,
  },
  iconBtnBadge: {
    position: 'absolute',
    top: -3,
    right: -3,
    minWidth: 14,
    height: 14,
    borderRadius: 7,
    paddingHorizontal: 3,
    backgroundColor: Colors.amber,
    borderWidth: 2,
    borderColor: '#0B0B10',
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconBtnBadgeText: {
    fontSize: 9,
    fontWeight: '900',
    color: '#0a0a0a',
    lineHeight: 10,
  },
});

export default AppHeader;
