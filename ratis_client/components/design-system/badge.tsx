/**
 * Design system Badge — rarity tiers (achievements V2 visuals, palette
 * posée dès V1).
 *
 * 4 rarities map to a tier gradient :
 *   - common    : neutral grey gradient, no animation
 *   - rare      : cyan gradient + subtle glow ring
 *   - epic      : violet gradient + slow pulse on the glow
 *   - legendary : gold gradient + holographic shine sweep (`achHoloShine`,
 *                 2s linear loop on a translucent overlay)
 *
 * Spec : `ARCH_design_system.md` § Composants — Badges.
 */

import React, { useEffect } from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated';

import { Colors, Radii, Typography, Rarity } from '@/constants/theme';

export type BadgeRarity = 'common' | 'rare' | 'epic' | 'legendary';
export type BadgeSize = 'sm' | 'md' | 'lg';

export type BadgeProps = {
  rarity: BadgeRarity;
  label: string;
  icon?: React.ReactNode;
  size?: BadgeSize;
  /** Holographic shine sweep — only takes effect on rare+. Default true. */
  shine?: boolean;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

const RARITY_GRADIENT: Record<BadgeRarity, readonly [string, string]> = {
  common: ['#8B8B8B', '#6B6B6B'],
  rare: [Rarity.rare, '#0E7490'],
  epic: [Rarity.epic, '#6D28D9'],
  legendary: [Rarity.legendary, Colors.goldLo],
};

const RARITY_BORDER: Record<BadgeRarity, string> = {
  common: 'rgba(255,255,255,0.20)',
  rare: 'rgba(34,211,238,0.40)',
  epic: 'rgba(168,85,247,0.40)',
  legendary: 'rgba(255,184,0,0.55)',
};

const SIZE_PADDING: Record<BadgeSize, { v: number; h: number }> = {
  sm: { v: 4, h: 8 },
  md: { v: 6, h: 10 },
  lg: { v: 10, h: 14 },
};

const SIZE_FONT: Record<BadgeSize, { fontSize: number; lineHeight: number }> = {
  sm: { fontSize: 9, lineHeight: 11 },
  md: { fontSize: 11, lineHeight: 13 },
  lg: { fontSize: 13, lineHeight: 16 },
};

export function Badge({
  rarity,
  label,
  icon,
  size = 'md',
  shine = true,
  testID,
  style,
}: BadgeProps) {
  const showShine = shine && rarity !== 'common';
  const shineX = useSharedValue(-1);

  useEffect(() => {
    if (!showShine) return;
    shineX.value = withRepeat(
      withTiming(2, { duration: 2000, easing: Easing.linear }),
      -1,
      false,
    );
  }, [showShine, shineX]);

  const shineStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: shineX.value * 80 }],
  }));

  const padding = SIZE_PADDING[size];
  const font = SIZE_FONT[size];

  return (
    <View
      testID={testID}
      style={[
        styles.wrapper,
        {
          borderRadius: Radii.badge,
          borderColor: RARITY_BORDER[rarity],
          paddingVertical: padding.v,
          paddingHorizontal: padding.h,
        },
        style,
      ]}
    >
      <LinearGradient
        colors={RARITY_GRADIENT[rarity] as unknown as readonly [string, string]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[StyleSheet.absoluteFillObject, { borderRadius: Radii.badge - 1 }]}
        testID={testID ? `${testID}-gradient` : 'badge-gradient'}
      />
      {showShine ? (
        <Animated.View
          pointerEvents="none"
          testID={testID ? `${testID}-shine` : 'badge-shine'}
          style={[styles.shine, shineStyle]}
        />
      ) : null}
      <View style={styles.content}>
        {icon ? <View style={styles.icon}>{icon}</View> : null}
        <Text
          style={[
            Typography.label,
            {
              color: Colors.textPrimary,
              fontSize: font.fontSize,
              lineHeight: font.lineHeight,
            },
          ]}
          numberOfLines={1}
        >
          {label}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    alignSelf: 'flex-start',
    borderWidth: 1.5,
    overflow: 'hidden',
  },
  content: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  icon: {
    marginRight: 2,
  },
  shine: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    width: 24,
    backgroundColor: 'rgba(255,255,255,0.35)',
    opacity: 0.8,
  },
});

export default Badge;
