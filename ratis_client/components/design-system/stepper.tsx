/**
 * Design system Stepper — quantity +/- (Liste tab `ItemRow`).
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste Courses.png`
 * Reference JSX    : `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 117-128
 *                    (`qtyBtnStyle`).
 *
 * Spec
 * ----
 *  - Horizontal layout : `[-]` button | value | `[+]` button.
 *  - Buttons are 22×22 rounded squares (radius 6) with no border, no fill —
 *    the parent container provides the framing (subtle 1px border + dark
 *    bg). Press scales to 0.95 via Reanimated for tactile feedback.
 *  - Value cell is `min-width: 16` centered, font-weight 900 mono numerals.
 *  - `min` (default 0) and `max` (default 99) are inclusive bounds.
 *  - Buttons disable themselves at the bounds (visual `opacity: 0.4`,
 *    `disabled` flag on the underlying Pressable so screen readers announce
 *    the state correctly).
 */

import React, { useCallback } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import { Colors } from '@/constants/theme';
import { Durations } from '@/constants/animations';

const PRESS_SCALE = 0.92;

export type StepperProps = {
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
  disabled?: boolean;
  hapticOnPress?: boolean;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function Stepper({
  value,
  onChange,
  min = 0,
  max = 99,
  disabled = false,
  hapticOnPress = true,
  testID,
  style,
}: StepperProps) {
  const atMin = value <= min;
  const atMax = value >= max;

  const handleDec = useCallback(() => {
    if (atMin || disabled) return;
    if (hapticOnPress) {
      Haptics.selectionAsync().catch(() => undefined);
    }
    onChange(value - 1);
  }, [atMin, disabled, hapticOnPress, onChange, value]);

  const handleInc = useCallback(() => {
    if (atMax || disabled) return;
    if (hapticOnPress) {
      Haptics.selectionAsync().catch(() => undefined);
    }
    onChange(value + 1);
  }, [atMax, disabled, hapticOnPress, onChange, value]);

  return (
    <View
      style={[styles.wrap, disabled && styles.wrapDisabled, style]}
      testID={testID}
    >
      <StepperButton
        sign="-"
        onPress={handleDec}
        disabled={atMin || disabled}
        testID={testID ? `${testID}-dec` : undefined}
        accessibilityLabel="Decrease quantity"
      />
      <View style={styles.valueCell}>
        <Text
          style={styles.value}
          testID={testID ? `${testID}-value` : undefined}
        >
          {value}
        </Text>
      </View>
      <StepperButton
        sign="+"
        onPress={handleInc}
        disabled={atMax || disabled}
        testID={testID ? `${testID}-inc` : undefined}
        accessibilityLabel="Increase quantity"
      />
    </View>
  );
}

type StepperButtonProps = {
  sign: '+' | '-';
  onPress: () => void;
  disabled: boolean;
  testID?: string;
  accessibilityLabel: string;
};

function StepperButton({
  sign,
  onPress,
  disabled,
  testID,
  accessibilityLabel,
}: StepperButtonProps) {
  const pressed = useSharedValue(0);

  const animated = useAnimatedStyle(() => ({
    transform: [
      {
        scale: 1 - pressed.value * (1 - PRESS_SCALE),
      },
    ],
  }));

  return (
    <Pressable
      onPressIn={() => {
        pressed.value = withTiming(1, { duration: Durations.instant });
      }}
      onPressOut={() => {
        pressed.value = withTiming(0, { duration: Durations.instant });
      }}
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ disabled }}
      testID={testID}
      style={styles.btnHit}
      hitSlop={6}
    >
      <Animated.View
        style={[styles.btn, animated, disabled && styles.btnDisabled]}
      >
        <Text style={styles.sign}>{sign === '-' ? '−' : '＋'}</Text>
      </Animated.View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(0,0,0,0.18)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.07)',
    borderRadius: 7,
    padding: 2,
  },
  wrapDisabled: {
    opacity: 0.4,
  },
  btnHit: {
    width: 22,
    height: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btn: {
    width: 22,
    height: 22,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnDisabled: {
    opacity: 0.45,
  },
  sign: {
    color: Colors.textPrimary,
    fontSize: 12,
    fontWeight: '900',
    lineHeight: 14,
  },
  valueCell: {
    minWidth: 16,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 2,
  },
  value: {
    color: Colors.textPrimary,
    fontSize: 11,
    fontWeight: '900',
    fontVariant: ['tabular-nums'],
  },
});

export default Stepper;
