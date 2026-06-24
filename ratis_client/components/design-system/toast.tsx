/**
 * Design system Toast — bottom-positioned non-blocking feedback.
 *
 * Reference visual : `Ratis_handoff/lib/ratis-real-v4.jsx` toast pattern (the
 * thin pill that confirms claim / sync actions).
 *
 * Behaviour
 * ---------
 *  - Renders absolute-positioned at the bottom of the parent, centered.
 *  - Slides up + fades in over 250ms (`Easing.out(cubic)`) on `visible=true`.
 *  - Auto-dismisses after `duration` ms (default 1800) — fires `onDismiss` so
 *    the parent can flip its state. The parent owns visibility ; the toast is
 *    purely presentational.
 *  - Slides back down + fades out on `visible=false`.
 *
 * Accessibility
 * -------------
 *  - `accessibilityLiveRegion="polite"` so screen readers announce the
 *    message without interrupting.
 *  - `pointerEvents="none"` to never swallow taps : the toast is feedback,
 *    not interaction.
 */

import React, { useEffect } from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import { Colors, Radii, Spacing, Typography } from '@/constants/theme';

const ENTRY_DURATION = 250;
const EXIT_DURATION = 200;
const DEFAULT_DURATION = 1800;

export type ToastProps = {
  message: string;
  visible: boolean;
  onDismiss: () => void;
  duration?: number;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function Toast({
  message,
  visible,
  onDismiss,
  duration = DEFAULT_DURATION,
  testID,
  style,
}: ToastProps) {
  const opacity = useSharedValue(0);
  const translateY = useSharedValue(20);

  useEffect(() => {
    if (visible) {
      opacity.value = withTiming(1, {
        duration: ENTRY_DURATION,
        easing: Easing.out(Easing.cubic),
      });
      translateY.value = withTiming(0, {
        duration: ENTRY_DURATION,
        easing: Easing.out(Easing.cubic),
      });

      const t = setTimeout(onDismiss, duration);
      return () => clearTimeout(t);
    }

    opacity.value = withTiming(0, { duration: EXIT_DURATION });
    translateY.value = withTiming(20, {
      duration: EXIT_DURATION,
      easing: Easing.in(Easing.cubic),
    });
    return undefined;
  }, [visible, duration, onDismiss, opacity, translateY]);

  const animated = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ translateY: translateY.value }],
  }));

  return (
    <View pointerEvents="none" style={styles.host} testID={testID}>
      <Animated.View
        accessibilityLiveRegion="polite"
        accessibilityRole="alert"
        style={[styles.pill, animated, style]}
      >
        <Text
          style={styles.text}
          testID={testID ? `${testID}-text` : undefined}
        >
          {message}
        </Text>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  host: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 96, // sits above the tab bar (84px + breathing room)
    alignItems: 'center',
    zIndex: 50,
  },
  pill: {
    maxWidth: '86%',
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.sm,
    borderRadius: Radii.btn,
    backgroundColor: 'rgba(15,20,25,0.95)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.10)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.4,
    shadowRadius: 14,
    elevation: 8,
  },
  text: {
    ...Typography.bodySm,
    color: Colors.textPrimary,
    textAlign: 'center',
  },
});

export default Toast;
