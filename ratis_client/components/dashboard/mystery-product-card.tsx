// ratis_client/components/dashboard/mystery-product-card.tsx
//
// Compact "Produit du jour mystère" card — port of
// `Ratis_handoff/lib/ratis-real-v4.jsx` `function MysteryProductCard`
// (lines 228-276).
//
// Visual anatomy :
//   - Violet body `#3D2E5A` with semi-transparent purple border
//   - Top-right radial corner glow (purple)
//   - Left "?" icon tile (50×50, glowing rounded square)
//   - Right column : "MYSTÈRE" label + "Produit du jour" title + gold pill
//     "+50 cab" (the reward amount stays hardcoded for V1 — the JSX has it
//     hardcoded too, no hook).
//
// V1 limitations / follow-ups :
//   - The card is non-interactive in V1. The JSX wraps it in a `cursor:
//     pointer` div but doesn't actually navigate anywhere. We expose `onPress`
//     for forward-compat ; the dashboard composition leaves it unwired.
//   - Reward amount + title are hardcoded for V1 — when a hook surfaces the
//     mystery product (V2), pass them as props.

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

export type MysteryProductCardProps = {
  rewardLabel?: string;
  onPress?: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function MysteryProductCard({
  rewardLabel = '+50 cab',
  onPress,
  testID = 'mystery-product-card',
  style,
}: MysteryProductCardProps) {
  const { t } = useTranslation();
  const Body = onPress ? Pressable : View;

  return (
    <Body
      testID={testID}
      onPress={onPress}
      style={[styles.root, style]}
      accessibilityRole={onPress ? 'button' : undefined}
    >
      {/* Corner glow — purple radial (top-right) */}
      <View style={styles.cornerGlow} pointerEvents="none" />
      {/* Question mark tile */}
      <View testID="mystery-question-mark" style={styles.iconTile}>
        <Text style={styles.iconText}>?</Text>
      </View>
      {/* Right column : labels + reward pill */}
      <View style={styles.col}>
        <Text style={styles.miniLabel}>Mystère</Text>
        <Text style={styles.title}>{t('dashboard.mystery.title')}</Text>
        <LinearGradient
          colors={[Colors.goldHi, Colors.gold]}
          start={{ x: 0, y: 0 }}
          end={{ x: 0, y: 1 }}
          style={styles.rewardPill}
        >
          <Text style={styles.rewardText}>{rewardLabel}</Text>
        </LinearGradient>
      </View>
    </Body>
  );
}

const styles = StyleSheet.create({
  root: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    width: '100%',
    flex: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 18,
    backgroundColor: '#3D2E5A',
    borderWidth: 1.5,
    borderColor: 'rgba(168,85,247,0.4)',
    overflow: 'hidden',
    // 3D shadow — hard layer only (RN limitation, see ARCH § 301).
    shadowColor: '#251638',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  cornerGlow: {
    position: 'absolute',
    top: -30,
    right: -30,
    width: 110,
    height: 110,
    borderRadius: 55,
    backgroundColor: 'rgba(168,85,247,0.25)',
    opacity: 0.6,
  },
  iconTile: {
    width: 50,
    height: 50,
    borderRadius: 13,
    backgroundColor: 'rgba(168,85,247,0.22)',
    borderWidth: 1.5,
    borderColor: 'rgba(168,85,247,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconText: {
    fontFamily: 'Inter_900Black',
    fontSize: 26,
    color: 'rgba(255,255,255,0.92)',
    lineHeight: 28,
  },
  col: {
    flex: 1,
    minWidth: 0,
    flexDirection: 'column',
  },
  miniLabel: {
    ...Typography.label,
    fontSize: 8,
    color: 'rgba(255,255,255,0.55)',
    letterSpacing: 0.8,
    marginBottom: 2,
  },
  title: {
    fontFamily: 'Inter_900Black',
    fontSize: 12,
    color: Colors.textPrimary,
    letterSpacing: -0.2,
    lineHeight: 14,
  },
  rewardPill: {
    alignSelf: 'flex-start',
    marginTop: 4,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Colors.goldLo,
  },
  rewardText: {
    fontFamily: 'Inter_900Black',
    fontSize: 9,
    color: '#3A2200',
    letterSpacing: -0.1,
  },
});

export default MysteryProductCard;
