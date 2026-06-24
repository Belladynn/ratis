/**
 * Design system ProgressBar — Duolingo / Clash Royale pivot.
 *
 * Anatomy :
 *   - track   : rounded View, dark backdrop (`rgba(255,255,255,0.08)`)
 *   - fill    : `LinearGradient` (variant palette) animated via Reanimated
 *               width interpolation (250ms `withTiming` on value change)
 *   - shimmer : a translucent overlay translated horizontally in a 2s loop —
 *               opt-out via `shimmer={false}` (typically static stories).
 *   - label   : optional centered % string (`showLabel`).
 *
 * Spec : `ARCH_design_system.md` § Composants — Progress Bars.
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

import { Colors, Typography } from '@/constants/theme';

export type ProgressBarVariant = 'gold' | 'jarPink' | 'terracotta' | 'cyan';

export type ProgressBarProps = {
  value: number; // 0..1
  variant: ProgressBarVariant;
  height?: number;
  shimmer?: boolean;
  showLabel?: boolean;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

const VARIANT_GRADIENT: Record<ProgressBarVariant, readonly [string, string]> = {
  gold: [Colors.goldHi, Colors.gold],
  jarPink: [Colors.jarPinkHi, Colors.jarPink],
  terracotta: [Colors.terracottaHi, Colors.terracotta],
  cyan: [Colors.cyan, '#0284C7'], // mid-cyan to deep — kept consistent w/ amber rule
};

function clamp01(v: number): number {
  if (Number.isNaN(v)) return 0;
  if (v < 0) return 0;
  if (v > 1) return 1;
  return v;
}

export function ProgressBar({
  value,
  variant,
  height = 12,
  shimmer = true,
  showLabel = false,
  testID,
  style,
}: ProgressBarProps) {
  const clamped = clamp01(value);
  const pct = Math.round(clamped * 100);

  const shimmerX = useSharedValue(-1);

  useEffect(() => {
    if (!shimmer) {
      shimmerX.value = -1;
      return;
    }
    shimmerX.value = withRepeat(
      withTiming(2, {
        duration: 2000,
        easing: Easing.linear,
      }),
      -1,
      false,
    );
  }, [shimmer, shimmerX]);

  const shimmerStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: shimmerX.value * 200 }],
  }));

  return (
    <View style={[styles.wrapper, style]}>
      <View
        testID={testID}
        style={[styles.track, { height, borderRadius: height / 2 }]}
      >
        <View
          testID={testID ? `${testID}-fill` : 'pb-fill'}
          style={[styles.fill, { width: `${pct}%`, borderRadius: height / 2 }]}
        >
          <LinearGradient
            colors={VARIANT_GRADIENT[variant] as unknown as readonly [string, string]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 0 }}
            style={[styles.gradient, { borderRadius: height / 2 }]}
            testID={testID ? `${testID}-gradient` : 'pb-gradient'}
          />
          {shimmer ? (
            <Animated.View
              pointerEvents="none"
              style={[styles.shimmer, shimmerStyle]}
            />
          ) : null}
        </View>
      </View>
      {showLabel ? (
        <Text
          style={[
            Typography.bodySm,
            styles.label,
          ]}
        >
          {pct}%
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  track: {
    flex: 1,
    backgroundColor: 'rgba(255,255,255,0.08)',
    overflow: 'hidden',
  },
  fill: {
    height: '100%',
    overflow: 'hidden',
    position: 'relative',
  },
  gradient: {
    ...StyleSheet.absoluteFillObject,
  },
  shimmer: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    width: 60,
    backgroundColor: 'rgba(255,255,255,0.25)',
    opacity: 0.6,
  },
  label: {
    color: Colors.textSecondary,
    minWidth: 36,
    textAlign: 'right',
  },
});

export default ProgressBar;
