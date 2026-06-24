// ratis_client/components/dashboard/jack-streak-button.tsx
//
// "Streak Jack" CTA — port of `Ratis_handoff/lib/ratis-real-v4.jsx`
// `function JackStreakButton` (lines 338-422).
//
// Two visual states (driven by `streak.already_fed_today`) :
//   - hungry  : coral/red gradient (`#4A1F1B → #2E1410`), terracotta border,
//               rat emoji + multiplier badge "+{N}%" + big red CTA "{days}
//               JOURS"
//   - fed     : teal gradient (`#0F4D45 → #0A3A34`), teal border, "Rassasié"
//               + "Reviens demain" + same large day pill (slate disabled)
//
// Hook : `useStreak()` is consumed by the dashboard composition (passed as
// a `streak` prop here) so this component stays presentational and easy to
// test deterministically.
//
// V1 limitations / follow-ups :
//   - The "feed Jack" mutation isn't wired in V1 — the JSX dispatches
//     `feedJack()` locally on click. Once the backend mutation lands, expose
//     `onFeed` in this component (already accepted as prop). For now, the
//     pressable just calls the optional handler.
//   - JSX uses `jack-mascot.svg` for the right-hand glyph in some variants ;
//     V1 sticks with the rat emoji 🐀 the JSX falls back to (jack-mascot.svg
//     stays available in `assets/images/` for V2).

import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTranslation } from 'react-i18next';

import { Colors, Typography } from '@/constants/theme';
import type { StreakState } from '@/types/gamification';

export type JackStreakButtonProps = {
  streak: StreakState | null | undefined;
  isLoading?: boolean;
  onFeed?: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

const HUNGRY_GRADIENT = ['#4A1F1B', '#2E1410'] as const;
const FED_GRADIENT = ['#0F4D45', '#0A3A34'] as const;

export function JackStreakButton({
  streak,
  isLoading,
  onFeed,
  testID = 'jack-streak-button',
  style,
}: JackStreakButtonProps) {
  const { t } = useTranslation();
  if (isLoading || !streak) {
    return (
      <View
        testID={`${testID}-skeleton`}
        style={[styles.root, styles.skeleton, style]}
      />
    );
  }

  const fed = streak.already_fed_today;
  const bonusPct = Math.round((streak.multiplier ?? 0) * 100);

  return (
    <View
      testID={testID}
      style={[
        styles.root,
        {
          borderColor: fed ? 'rgba(77,212,179,0.6)' : 'rgba(255,107,53,0.55)',
        },
        style,
      ]}
    >
      <LinearGradient
        colors={(fed ? FED_GRADIENT : HUNGRY_GRADIENT) as unknown as readonly [
          string,
          string,
        ]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      {/* Corner glow */}
      <View
        pointerEvents="none"
        style={[
          styles.cornerGlow,
          {
            backgroundColor: fed
              ? 'rgba(77,212,179,0.32)'
              : 'rgba(255,107,53,0.45)',
          },
        ]}
      />

      {/* Left col — labels */}
      <View style={styles.leftCol}>
        <Text style={styles.miniLabel}>Streak Jack</Text>
        <Text
          style={[
            styles.title,
            { color: fed ? '#4DD4B3' : Colors.textPrimary },
          ]}
        >
          {fed ? 'Rassasié' : 'Nourrir Jack'}
        </Text>
        {fed ? (
          <Text style={styles.subFed}>
            {t('dashboard.jack.streak_sub_fed')}
          </Text>
        ) : bonusPct > 0 ? (
          <View style={styles.bonusPill}>
            <Text style={styles.bonusPillText}>+{bonusPct}%</Text>
          </View>
        ) : null}
      </View>

      {/* Right col — the day-count CTA. Bug 2 wave 3 (PO ticket
          2026-05-12) : when hungry, the label reads "NOURRIR" instead of
          "JOURS" so the button reads as a clear call-to-action, not as a
          passive day counter. The streak day count stays visible as the
          large number (kept the visual rhythm of the V4 handoff design).
          When fed, the label reverts to "JOURS" (no action available). */}
      <Pressable
        testID={fed ? 'jack-streak-fed-cta' : 'jack-streak-feed-cta'}
        accessibilityRole="button"
        accessibilityLabel={
          fed ? `${streak.streak_days} jours` : 'Nourrir Jack'
        }
        accessibilityState={{ disabled: fed }}
        disabled={fed}
        onPress={fed ? undefined : onFeed}
        style={({ pressed }) => [
          styles.ctaBlock,
          {
            backgroundColor: fed ? 'rgba(77,212,179,0.18)' : Colors.coral,
          },
          pressed && !fed && styles.ctaPressed,
        ]}
      >
        <Text
          style={[
            styles.ctaDays,
            { color: fed ? 'rgba(255,255,255,0.5)' : Colors.textPrimary },
          ]}
        >
          {streak.streak_days}
        </Text>
        <Text
          style={[
            styles.ctaLabel,
            { color: fed ? 'rgba(255,255,255,0.5)' : Colors.textPrimary },
          ]}
        >
          {fed ? 'JOURS' : 'NOURRIR'}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 18,
    borderWidth: 2,
    overflow: 'hidden',
    // 3D shadow — single hard layer (RN limitation).
    shadowColor: 'rgba(60,12,8,0.95)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  skeleton: {
    backgroundColor: '#2A1A1A',
    opacity: 0.45,
    borderColor: 'rgba(255,255,255,0.06)',
    minHeight: 70,
  },
  cornerGlow: {
    position: 'absolute',
    top: -30,
    right: -30,
    width: 110,
    height: 110,
    borderRadius: 60,
    opacity: 0.6,
  },
  leftCol: {
    flex: 1,
    minWidth: 0,
    flexDirection: 'column',
    gap: 4,
  },
  miniLabel: {
    ...Typography.label,
    fontSize: 8,
    color: 'rgba(255,255,255,0.55)',
    letterSpacing: 0.8,
  },
  title: {
    fontFamily: 'Inter_900Black',
    fontSize: 12,
    letterSpacing: -0.2,
    lineHeight: 14,
  },
  subFed: {
    fontSize: 9,
    fontWeight: '700',
    color: 'rgba(77,212,179,0.7)',
  },
  bonusPill: {
    alignSelf: 'flex-start',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
    backgroundColor: 'rgba(255,184,0,0.20)',
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.5)',
  },
  bonusPillText: {
    fontFamily: 'Inter_900Black',
    fontSize: 9,
    color: Colors.gold,
    letterSpacing: -0.1,
  },
  ctaBlock: {
    width: 56,
    minHeight: 50,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 4,
  },
  ctaPressed: {
    opacity: 0.85,
    transform: [{ scale: 0.96 }],
  },
  ctaDays: {
    fontFamily: 'Inter_900Black',
    fontSize: 22,
    letterSpacing: -0.8,
    lineHeight: 24,
  },
  ctaLabel: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 7,
    letterSpacing: 0.8,
    marginTop: 2,
    opacity: 0.92,
  },
});

export default JackStreakButton;
