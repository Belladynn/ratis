/**
 * Design system Card — Duolingo / Clash Royale pivot.
 *
 * Two variants :
 *   - `standard` — surface `#27293A`, radius 20, soft 3D drop shadow + 1px
 *     inset highlight on top edge.
 *   - `accent`   — same base + 4px border-left in the requested accent color
 *     (jarPink / gold / terracotta / violet / orange / cyan) + a faint tinted
 *     overlay (alpha 0.06) so the body still reads, but sits in a colored
 *     atmosphere.
 *
 * Tappable cards (`onPress` provided) animate to scale 0.98 + opacity 0.95
 * on press via Reanimated 4. Otherwise it renders as a plain `View`.
 *
 * Spec : `ARCH_design_system.md` § Composants — Cards.
 */

import React, { useCallback } from 'react';
import {
  Pressable,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import { Colors, Radii, Shadows, Spacing } from '@/constants/theme';
import { Durations } from '@/constants/animations';

export type CardVariant = 'standard' | 'accent';
export type CardAccentColor =
  | 'jarPink'
  | 'gold'
  | 'terracotta'
  | 'violet'
  | 'orange'
  | 'cyan';

export type CardProps = {
  variant?: CardVariant;
  accentColor?: CardAccentColor;
  padding?: number;
  cornerGlow?: boolean;
  children: React.ReactNode;
  onPress?: () => void;
  testID?: string;
  accessibilityLabel?: string;
  style?: StyleProp<ViewStyle>;
};

const ACCENT_PALETTE: Record<CardAccentColor, string> = {
  jarPink: Colors.jarPink,
  gold: Colors.gold,
  terracotta: Colors.terracotta,
  violet: Colors.violet,
  orange: Colors.orange,
  cyan: Colors.cyan,
};

function resolveAccent(color: CardAccentColor | undefined): string {
  if (color && color in ACCENT_PALETTE) {
    return ACCENT_PALETTE[color];
  }
  // Fallback when the variant=accent prop is set but the color is unknown
  // (or undefined) — terracotta is the canonical action accent.
  return Colors.terracotta;
}

/**
 * Convert `#RRGGBB` (or `#RGB`) to `rgba(r, g, b, a)`. Used for the accent
 * variant's tinted background — kept inline to avoid pulling a color util
 * just for one call site.
 */
function hexToRgba(hex: string, alpha: number): string {
  let h = hex.replace('#', '');
  if (h.length === 3) {
    h = h.split('').map((c) => c + c).join('');
  }
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) {
    // Defensive — bail to the surface color if the parse fails.
    return Colors.surface;
  }
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function Card({
  variant = 'standard',
  accentColor,
  padding = Spacing.lg,
  cornerGlow,
  children,
  onPress,
  testID,
  accessibilityLabel,
  style,
}: CardProps) {
  const pressed = useSharedValue(0);

  const animated = useAnimatedStyle(() => {
    const p = pressed.value;
    return {
      transform: [{ scale: 1 - p * 0.02 }],
      opacity: 1 - p * 0.05,
    };
  });

  const handlePressIn = useCallback(() => {
    pressed.value = withTiming(1, { duration: Durations.instant });
  }, [pressed]);

  const handlePressOut = useCallback(() => {
    pressed.value = withTiming(0, { duration: Durations.instant });
  }, [pressed]);

  const isAccent = variant === 'accent';
  const accent = isAccent ? resolveAccent(accentColor) : null;

  const surfaceStyle: StyleProp<ViewStyle> = [
    styles.base,
    {
      padding,
      backgroundColor: isAccent && accent ? hexToRgba(accent, 0.06) : Colors.surface,
    },
    isAccent && accent
      ? {
          borderLeftWidth: 4,
          borderLeftColor: accent,
          borderColor: hexToRgba(accent, 0.35),
          borderTopWidth: 1.5,
          borderRightWidth: 1.5,
          borderBottomWidth: 1.5,
        }
      : null,
    style,
  ];

  const cornerGlowOverlay =
    cornerGlow && accent ? (
      <View
        pointerEvents="none"
        style={[
          styles.cornerGlow,
          { backgroundColor: hexToRgba(accent, 0.18) },
        ]}
      />
    ) : null;

  if (onPress) {
    return (
      <AnimatedPressable
        onPress={onPress}
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel}
        testID={testID}
        style={[surfaceStyle, animated]}
      >
        {children}
        {cornerGlowOverlay}
      </AnimatedPressable>
    );
  }

  return (
    <View
      accessibilityLabel={accessibilityLabel}
      testID={testID}
      style={surfaceStyle}
    >
      {children}
      {cornerGlowOverlay}
    </View>
  );
}

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

const styles = StyleSheet.create({
  base: {
    borderRadius: Radii.card,
    borderWidth: 1.5,
    borderColor: 'rgba(255,255,255,0.08)',
    // Hard 3D drop shadow — see Shadows.card.hard ; we expose both the hard
    // stripe AND a softer diffuse layer ; RN can't stack natively so we keep
    // the hard one (the diffuse is added by an outer wrapper at call sites
    // that need it — Dashboard PR4).
    shadowColor: Shadows.card.hard.shadowColor,
    shadowOffset: Shadows.card.hard.shadowOffset,
    shadowRadius: Shadows.card.hard.shadowRadius,
    shadowOpacity: Shadows.card.hard.shadowOpacity,
    elevation: Shadows.card.hard.elevation,
  },
  cornerGlow: {
    position: 'absolute',
    top: -20,
    right: -20,
    width: 80,
    height: 80,
    borderRadius: 40,
    opacity: 0.6,
  },
});

export default Card;
