/**
 * Design system Modal — bottom sheet (V5 strict iso).
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Mission popup.png`.
 * Reference JSX    : `Ratis_handoff/lib/ratis-real-v4.jsx` lines 805-877
 *                    (`MissionsModal`).
 *
 * Implementation notes
 * --------------------
 *  - Two layers : a backdrop (`rgba(0,0,0,0.65)`) that fades in over 200ms,
 *    and a sheet anchored to the bottom that slides up over 260ms with the
 *    bouncy bezier `(0.2, 0.9, 0.3, 1.2)` from the design pattern.
 *  - The sheet content is wrapped in a vertical gradient
 *    `#1c2730 → #15191c` to match the JSX source.
 *  - A 40×4 drag handle sits centered at the top, purely visual (no real drag
 *    gesture in V1 — close is via tap-on-backdrop or the × button). A pan
 *    gesture handler can be added later without touching consumers.
 *  - `scrollable` (default true) wraps the children in a `ScrollView` so long
 *    content (mission lists) overflows correctly inside the `82%` max-height.
 *  - On `open=false` we keep the component mounted for one frame to play the
 *    exit animation, then `pointerEvents='none'` and width 0 so it never
 *    swallows touches when hidden.
 *
 * Spec : `ARCH_frontend_strict_iso.md` § Mission popup modal.
 */

import React, { useEffect } from 'react';
import {
  Pressable,
  ScrollView,
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
  withTiming,
} from 'react-native-reanimated';

import { Colors, Radii, Spacing, Typography } from '@/constants/theme';
import { Durations, EasingPresets } from '@/constants/animations';

const BACKDROP_DURATION = Durations.fast; // 200ms ease-out
const SHEET_DURATION = 260; // bouncy slideUp

export type ModalProps = {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
  /** When true (default), wraps children in a `ScrollView`. */
  scrollable?: boolean;
  /** Optional uppercase label rendered above `title` (e.g. "Tes missions"). */
  eyebrow?: string;
  testID?: string;
  contentStyle?: StyleProp<ViewStyle>;
};

export function Modal({
  open,
  onClose,
  children,
  title,
  scrollable = true,
  eyebrow,
  testID,
  contentStyle,
}: ModalProps) {
  const backdropOpacity = useSharedValue(0);
  const sheetTranslateY = useSharedValue(1); // 0 = visible, 1 = below screen
  const sheetOpacity = useSharedValue(0);

  useEffect(() => {
    if (open) {
      backdropOpacity.value = withTiming(1, {
        duration: BACKDROP_DURATION,
        easing: Easing.out(Easing.cubic),
      });
      sheetOpacity.value = withTiming(1, {
        duration: BACKDROP_DURATION,
        easing: Easing.out(Easing.cubic),
      });
      sheetTranslateY.value = withTiming(0, {
        duration: SHEET_DURATION,
        easing: Easing.bezier(...EasingPresets.bouncy),
      });
    } else {
      backdropOpacity.value = withTiming(0, { duration: BACKDROP_DURATION });
      sheetOpacity.value = withTiming(0, { duration: BACKDROP_DURATION });
      sheetTranslateY.value = withTiming(1, {
        duration: BACKDROP_DURATION,
        easing: Easing.in(Easing.cubic),
      });
    }
  }, [open, backdropOpacity, sheetOpacity, sheetTranslateY]);

  const backdropStyle = useAnimatedStyle(() => ({
    opacity: backdropOpacity.value,
  }));
  const sheetStyle = useAnimatedStyle(() => ({
    opacity: sheetOpacity.value,
    // 100% travel = full sheet height. We use `translateY: %` via a
    // multiplier ; sheet has max-height 82% of parent, so 110% is enough to
    // fully clear it from view.
    transform: [{ translateY: `${sheetTranslateY.value * 110}%` }],
  }));

  // When fully closed, remove from layout to free pointer events.
  if (!open && backdropOpacity.value === 0 && sheetTranslateY.value === 1) {
    return null;
  }

  const Body = scrollable ? ScrollView : View;

  return (
    <View
      pointerEvents={open ? 'auto' : 'none'}
      style={styles.root}
      testID={testID}
    >
      <Animated.View style={[styles.backdrop, backdropStyle]}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close modal"
          onPress={onClose}
          style={StyleSheet.absoluteFill}
          testID={testID ? `${testID}-backdrop` : undefined}
        />
      </Animated.View>

      <Animated.View
        style={[styles.sheetWrapper, sheetStyle]}
        pointerEvents="box-none"
      >
        <LinearGradient
          colors={['#1c2730', '#15191c']}
          start={{ x: 0, y: 0 }}
          end={{ x: 0, y: 1 }}
          style={[styles.sheet, contentStyle]}
        >
          <View style={styles.handleRow}>
            <View style={styles.handle} />
          </View>

          {(title || eyebrow) && (
            <View style={styles.header}>
              <View style={{ flex: 1 }}>
                {eyebrow ? (
                  <Text
                    style={[Typography.label, { color: Colors.textSecondary }]}
                  >
                    {eyebrow}
                  </Text>
                ) : null}
                {title ? (
                  <Text style={[Typography.hero, styles.title]}>{title}</Text>
                ) : null}
              </View>
              <Pressable
                onPress={onClose}
                accessibilityRole="button"
                accessibilityLabel="Close"
                style={styles.closeBtn}
                testID={testID ? `${testID}-close` : undefined}
                hitSlop={8}
              >
                <Text style={styles.closeIcon}>×</Text>
              </Pressable>
            </View>
          )}

          <Body
            style={styles.body}
            contentContainerStyle={
              scrollable ? styles.bodyContent : undefined
            }
          >
            {children}
          </Body>
        </LinearGradient>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 200,
    justifyContent: 'flex-end',
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.65)',
  },
  sheetWrapper: {
    width: '100%',
    maxHeight: '82%',
  },
  sheet: {
    width: '100%',
    borderTopLeftRadius: Radii.modal,
    borderTopRightRadius: Radii.modal,
    borderWidth: 1,
    borderBottomWidth: 0,
    borderColor: 'rgba(255,255,255,0.08)',
    paddingHorizontal: Spacing.lg,
    paddingTop: Spacing.md,
    paddingBottom: 28,
    gap: Spacing.md,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -10 },
    shadowOpacity: 0.6,
    shadowRadius: 40,
    elevation: 24,
  },
  handleRow: {
    alignItems: 'center',
  },
  handle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: 'rgba(255,255,255,0.18)',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.xs,
    paddingBottom: Spacing.sm,
  },
  title: {
    color: Colors.textPrimary,
    marginTop: 2,
  },
  closeBtn: {
    width: 36,
    height: 36,
    borderRadius: Radii.icon,
    backgroundColor: 'rgba(255,255,255,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeIcon: {
    color: Colors.textPrimary,
    fontSize: 22,
    fontWeight: '700',
    lineHeight: 22,
    marginTop: -2,
  },
  body: {
    width: '100%',
  },
  bodyContent: {
    gap: Spacing.md,
    paddingBottom: Spacing.sm,
  },
});

export default Modal;
