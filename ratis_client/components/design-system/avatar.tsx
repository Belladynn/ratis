/**
 * Design system Avatar — gradient circle with optional ring.
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Profil.png`
 * (avatar 80×80 with emoji 🐀).
 *
 * Spec
 * ----
 *  - 3 fixed sizes : sm 32, md 48, lg 80 (corresponds to: nav avatar / inline
 *    avatar / hero avatar). New sizes are added intentionally — V5 only uses
 *    these three.
 *  - The gradient is rendered via `expo-linear-gradient` 135deg from `[hi,
 *    lo]`. By default we use `Colors.gold` palette for backwards parity with
 *    the V5 jar/profile feel.
 *  - When `ringColor` is provided, we wrap the gradient in a thin ring
 *    (border-equivalent) with a 2-3px gap so the ring reads as a separate
 *    halo, not just a thicker border.
 *  - Children render centered. They are typed as `React.ReactNode` so the
 *    consumer can pass a `<Text>` (emoji or initials) or any custom node.
 */

import React from 'react';
import {
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

import { Colors } from '@/constants/theme';

export type AvatarSize = 'sm' | 'md' | 'lg';

export type AvatarProps = {
  size?: AvatarSize;
  children: React.ReactNode;
  /** Gradient endpoints `[topLeft, bottomRight]`. */
  gradientColors?: readonly [string, string];
  /** When set, draws a 2px ring around the avatar (gold by default in V5). */
  ringColor?: string;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

const DIMENSIONS: Record<AvatarSize, { outer: number; ring: number }> = {
  sm: { outer: 32, ring: 2 },
  md: { outer: 48, ring: 2 },
  lg: { outer: 80, ring: 3 },
};

export function Avatar({
  size = 'md',
  children,
  gradientColors = [Colors.goldHi, Colors.gold],
  ringColor,
  testID,
  style,
}: AvatarProps) {
  const { outer, ring } = DIMENSIONS[size];
  const inner = ringColor ? outer - ring * 2 : outer;

  const radius = outer / 2;
  const innerRadius = inner / 2;

  return (
    <View
      testID={testID}
      style={[
        styles.outer,
        {
          width: outer,
          height: outer,
          borderRadius: radius,
          backgroundColor: ringColor ?? 'transparent',
        },
        style,
      ]}
    >
      <LinearGradient
        colors={gradientColors}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[
          styles.gradient,
          {
            width: inner,
            height: inner,
            borderRadius: innerRadius,
          },
        ]}
      >
        {children}
      </LinearGradient>
    </View>
  );
}

const styles = StyleSheet.create({
  outer: {
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  gradient: {
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
});

export default Avatar;
