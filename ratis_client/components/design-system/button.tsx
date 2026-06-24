/**
 * Design system Button — Duolingo / Clash Royale pivot.
 *
 * 4 variants : primary (terracotta), secondary (outline terracotta), gold
 * (claim / reward), danger (coral). Variant tokens are pulled from
 * `constants/theme.ts` (gradients + outlines + 3D shadows).
 *
 * Animations :
 *   - press → scale 0.96 + translateY(2) via Reanimated 4 worklet
 *     (`pressed` SharedValue), released back to identity.
 *   - haptic Light impact (`expo-haptics`) on press, opt-out via
 *     `hapticOnPress={false}`.
 *
 * Accessibility :
 *   - root is a `Pressable` exposing the `testID` prop ; gradient surface
 *     is exposed as `<testID>-gradient` so tests can introspect the active
 *     palette without leaking implementation details.
 *   - disabled/loading both block onPress and keep the button focusable
 *     (no double-tap risk during async flows).
 *
 * Spec : `ARCH_design_system.md` § Composants — Boutons 3 rôles.
 */

import React, { useCallback } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  type PressableProps,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { LinearGradient } from 'expo-linear-gradient';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import { Colors, Radii, Shadows, Spacing, Typography } from '@/constants/theme';
import { Durations } from '@/constants/animations';

export type ButtonVariant = 'primary' | 'secondary' | 'gold' | 'danger';
export type ButtonSize = 'sm' | 'md';

export type ButtonProps = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  label: string;
  onPress?: () => void;
  disabled?: boolean;
  loading?: boolean;
  icon?: React.ReactNode;
  hapticOnPress?: boolean;
  fullWidth?: boolean;
  testID?: string;
  accessibilityLabel?: string;
  style?: StyleProp<ViewStyle>;
};

type Palette = {
  gradient: readonly [string, string] | null;
  border: string;
  shadowColor: string;
  shadowOffsetY: number;
  insetTop: string;
  text: string;
  radius: number;
};

function paletteFor(variant: ButtonVariant, size: ButtonSize): Palette {
  switch (variant) {
    case 'gold':
      return {
        gradient: [Colors.goldHi, Colors.gold],
        border: Colors.goldLo,
        shadowColor: Colors.goldSh,
        shadowOffsetY: 3,
        insetTop: 'rgba(255,255,255,0.40)',
        text: '#3A2200',
        radius: Radii.btnSm,
      };
    case 'danger':
      return {
        gradient: [Colors.coralText, Colors.coral],
        border: '#B91C1C',
        shadowColor: '#7F1D1D',
        shadowOffsetY: 4,
        insetTop: 'rgba(255,255,255,0.30)',
        text: Colors.textPrimary,
        radius: size === 'sm' ? Radii.btnSm : Radii.btn,
      };
    case 'secondary':
      return {
        gradient: null,
        border: Colors.terracotta,
        shadowColor: 'rgba(100,40,20,0.5)',
        shadowOffsetY: 4,
        insetTop: 'rgba(218,119,86,0.15)',
        text: Colors.terracotta,
        radius: size === 'sm' ? Radii.btnSm : Radii.btn,
      };
    case 'primary':
    default:
      return {
        gradient: [Colors.terracottaHi, Colors.terracotta],
        border: Colors.terracottaLo,
        shadowColor: Colors.terracottaSh,
        shadowOffsetY: 4,
        insetTop: 'rgba(255,255,255,0.35)',
        text: Colors.textPrimary,
        radius: size === 'sm' ? Radii.btnSm : Radii.btn,
      };
  }
}

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

export function Button({
  variant = 'primary',
  size = 'md',
  label,
  onPress,
  disabled,
  loading,
  icon,
  hapticOnPress = true,
  fullWidth,
  testID,
  accessibilityLabel,
  style,
}: ButtonProps) {
  const palette = paletteFor(variant, size);
  const inactive = disabled || loading;

  const pressed = useSharedValue(0);

  const animatedRoot = useAnimatedStyle(() => {
    const p = pressed.value;
    return {
      transform: [
        { scale: 1 - p * 0.04 },
        { translateY: p * 2 },
      ],
    };
  });

  const handlePressIn = useCallback(() => {
    pressed.value = withTiming(1, { duration: Durations.instant });
  }, [pressed]);

  const handlePressOut = useCallback(() => {
    pressed.value = withTiming(0, { duration: Durations.instant });
  }, [pressed]);

  const handlePress = useCallback(() => {
    if (inactive) return;
    if (hapticOnPress) {
      // Fire-and-forget — never block UI on haptic resolution.
      void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    }
    onPress?.();
  }, [inactive, hapticOnPress, onPress]);

  const verticalPad = size === 'sm' ? 8 : 11;
  const horizontalPad = size === 'sm' ? 12 : 16;
  const fontStyle = size === 'sm' ? Typography.bodySm : Typography.itemTitle;

  const rootStyle: StyleProp<ViewStyle> = [
    styles.root,
    {
      borderRadius: palette.radius,
      shadowColor: palette.shadowColor,
      shadowOffset: { width: 0, height: palette.shadowOffsetY },
    },
    fullWidth && styles.fullWidth,
    inactive && styles.inactive,
    style,
  ];

  const surfaceStyle: StyleProp<ViewStyle> = {
    borderRadius: palette.radius,
    borderColor: palette.border,
    borderWidth: 2,
    paddingVertical: verticalPad,
    paddingHorizontal: horizontalPad,
  };

  const content = (
    <View style={styles.content}>
      {loading ? (
        <ActivityIndicator
          testID={testID ? `${testID}-spinner` : 'ds-button-spinner'}
          color={palette.text}
          size="small"
        />
      ) : (
        <>
          {icon ? <View style={styles.icon}>{icon}</View> : null}
          <Text
            style={[
              fontStyle,
              { color: palette.text },
              styles.label,
            ]}
            numberOfLines={1}
          >
            {label}
          </Text>
        </>
      )}
    </View>
  );

  const insetHighlight = (
    <View
      pointerEvents="none"
      style={[
        styles.insetHighlight,
        { borderTopColor: palette.insetTop, borderRadius: palette.radius },
      ]}
    />
  );

  const pressableProps: PressableProps = {
    onPress: handlePress,
    onPressIn: handlePressIn,
    onPressOut: handlePressOut,
    disabled: inactive,
    accessibilityRole: 'button',
    accessibilityState: { disabled: !!inactive, busy: !!loading },
    accessibilityLabel: accessibilityLabel ?? label,
    testID,
    hitSlop: 4,
  };

  if (palette.gradient) {
    return (
      <AnimatedPressable {...pressableProps} style={[rootStyle, animatedRoot]}>
        <LinearGradient
          colors={palette.gradient as unknown as readonly [string, string]}
          start={{ x: 0, y: 0 }}
          end={{ x: 0, y: 1 }}
          style={surfaceStyle}
          testID={testID ? `${testID}-gradient` : 'ds-button-gradient'}
        >
          {content}
        </LinearGradient>
        {insetHighlight}
      </AnimatedPressable>
    );
  }

  return (
    <AnimatedPressable {...pressableProps} style={[rootStyle, animatedRoot]}>
      <View
        style={[surfaceStyle, styles.secondarySurface]}
        testID={testID ? `${testID}-surface` : 'ds-button-surface'}
      >
        {content}
      </View>
      {insetHighlight}
    </AnimatedPressable>
  );
}

const styles = StyleSheet.create({
  root: {
    alignSelf: 'flex-start',
    overflow: 'visible',
    // Hard 3D drop shadow — `Shadows.buttonPrimary` shape, but radius/offset
    // are overridden per-variant on the root style.
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: Shadows.buttonPrimary.hard.elevation,
  },
  fullWidth: {
    alignSelf: 'stretch',
    width: '100%',
  },
  inactive: {
    opacity: 0.5,
  },
  content: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.sm,
  },
  icon: {
    marginRight: Spacing.xs,
  },
  label: {
    textAlign: 'center',
  },
  insetHighlight: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    borderTopWidth: 1,
    borderColor: 'transparent',
  },
  secondarySurface: {
    backgroundColor: 'transparent',
  },
});

export default Button;
