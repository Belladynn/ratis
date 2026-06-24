// ratis_client/components/achievements/unlock-toast.tsx
//
// Achievements V1 — top-of-screen unlock toast (PR 8/8).
//
// Port of `Ratis_handoff/lib/ratis-achievements-ui.jsx` lines 466-540
// (`AchievementUnlockToast`) — web-style JSX with CSS keyframes adapted to
// React Native + Reanimated. The visual contract is preserved : a metallic
// frame whose colours come from the rarity palette, the achievement icon on
// the left, the "Succès débloqué · {rareté}" eyebrow, the label and the
// description.
//
// Visibility cycle (4500ms total) :
//   - 0-450ms     slide-in from above with a soft overshoot
//   - 450-4050ms  hold (~3.6s)
//   - 4050-4500ms slide-out + fade
//
// Tap anywhere on the toast → immediate dismiss (skips the hold). The toast
// also dismisses if the parent passes a new payload while one is already
// visible (handled at the queue level — see `services/achievement-notification-handler.ts`).
//
// Pitfalls avoided :
//   - Reanimated cleanup : the slide-out timer + auto-dismiss callback are
//     both cancelled on unmount (KP-style discipline used in
//     `achievement-card.tsx`).
//   - `top: insets.top + 12` (not `top: 0`) so the toast doesn't collide with
//     the notch / status bar on iOS.
//   - The web "holo sweep" is approximated with a single horizontal sweep
//     overlay; the conic burst rays from the JSX are dropped (RN doesn't
//     ship conic gradients) — visually we lean on the metallic frame +
//     glow shadow which already deliver the "shiny trophy" feel.

import React, { useEffect, useRef } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Animated, {
  cancelAnimation,
  Easing,
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import {
  RARITIES,
  type RarityKey,
} from '@/components/profil/achievements-data';
import type { AchievementUnlockedPayload } from '@/types/achievements';

const ENTER_MS = 450;
const HOLD_MS = 3600;
const EXIT_MS = 450;
const TOTAL_MS = ENTER_MS + HOLD_MS + EXIT_MS; // 4500

export type AchievementUnlockToastProps = {
  /** Payload from the notification — `null` keeps the toast unmounted. */
  payload: AchievementUnlockedPayload | null;
  /** Called once the toast has finished its exit animation OR was tapped. */
  onDismiss: () => void;
  testID?: string;
};

/**
 * Holographic shine sweep — translucent gradient that travels left→right
 * across the toast surface, repeated twice during the visible window.
 * Reserved to rare+ tiers (`r.holo === true`). Same cancelation discipline
 * as `achievement-card.tsx::HoloShine`.
 */
function HoloSweep({ color }: { color: string }) {
  const progress = useSharedValue(0);
  useEffect(() => {
    progress.value = withDelay(
      400,
      withRepeat(
        withTiming(1, {
          duration: 1500,
          easing: Easing.inOut(Easing.ease),
        }),
        2,
        false,
      ),
    );
    return () => {
      cancelAnimation(progress);
    };
  }, [progress]);
  const animated = useAnimatedStyle(() => ({
    transform: [{ translateX: `${-100 + progress.value * 200}%` }],
  }));
  return (
    <Animated.View pointerEvents="none" style={[styles.holoWrap, animated]}>
      <LinearGradient
        colors={[
          'rgba(255,255,255,0)',
          `${color}40`,
          'rgba(255,255,255,0.30)',
          `${color}40`,
          'rgba(255,255,255,0)',
        ]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 0 }}
        style={StyleSheet.absoluteFill}
      />
    </Animated.View>
  );
}

export function AchievementUnlockToast({
  payload,
  onDismiss,
  testID,
}: AchievementUnlockToastProps) {
  const insets = useSafeAreaInsets();
  const translateY = useSharedValue(-120);
  const opacity = useSharedValue(0);
  // Track whether onDismiss has fired so the auto-timer + tap can't both
  // call it twice.
  const dismissedRef = useRef(false);

  const triggerDismiss = () => {
    if (dismissedRef.current) return;
    dismissedRef.current = true;
    onDismiss();
  };

  useEffect(() => {
    if (!payload) return;
    dismissedRef.current = false;
    translateY.value = -120;
    opacity.value = 0;
    // Slide in with a tiny overshoot (cubic-bezier 0.34, 1.4, 0.64, 1).
    translateY.value = withSequence(
      withTiming(8, {
        duration: ENTER_MS * 0.7,
        easing: Easing.out(Easing.back(1.4)),
      }),
      withTiming(0, {
        duration: ENTER_MS * 0.3,
        easing: Easing.out(Easing.cubic),
      }),
    );
    opacity.value = withTiming(1, { duration: ENTER_MS / 2 });

    // Exit animation queued at HOLD end.
    const exitDelay = ENTER_MS + HOLD_MS;
    translateY.value = withDelay(
      exitDelay,
      withTiming(-120, {
        duration: EXIT_MS,
        easing: Easing.in(Easing.cubic),
      }),
    );
    opacity.value = withDelay(
      exitDelay,
      withTiming(0, { duration: EXIT_MS }, (finished) => {
        if (finished) runOnJS(triggerDismiss)();
      }),
    );

    // Belt-and-braces JS timer in case the worklet callback misses (e.g. test
    // env where Reanimated is mocked out). 4500ms total visibility.
    const t = setTimeout(triggerDismiss, TOTAL_MS);
    return () => {
      clearTimeout(t);
      cancelAnimation(translateY);
      cancelAnimation(opacity);
    };
    // We intentionally re-init the animation on each *new* payload (not on
    // every render). The eslint-react-hooks rule wants `triggerDismiss` /
    // shared values listed but they're stable refs — payload is the real
    // input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  const animated = useAnimatedStyle(() => ({
    transform: [{ translateY: translateY.value }],
    opacity: opacity.value,
  }));

  if (!payload) return null;

  const r = RARITIES[payload.rarity as RarityKey];

  return (
    <Animated.View
      pointerEvents="box-none"
      style={[
        styles.root,
        { top: insets.top + 12 },
        animated,
      ]}
    >
      <Pressable
        testID={testID ?? 'achievement-unlock-toast'}
        accessibilityRole="button"
        accessibilityLabel={`Succès débloqué : ${payload.label}`}
        onPress={triggerDismiss}
      >
        <LinearGradient
          colors={r.metal}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={[
            styles.frame,
            {
              shadowColor: r.color,
              shadowOpacity: 0.6,
              shadowRadius: 20,
              shadowOffset: { width: 0, height: 8 },
            },
          ]}
        >
          <View
            style={[
              styles.body,
              { backgroundColor: '#1A1B26' },
            ]}
          >
            {/* Soft rarity glow — vertical fade, mimics the JSX radial */}
            <LinearGradient
              colors={[r.glow, 'transparent']}
              start={{ x: 0.5, y: 0 }}
              end={{ x: 0.5, y: 1 }}
              style={StyleSheet.absoluteFill}
              pointerEvents="none"
            />

            {r.holo ? <HoloSweep color={r.color} /> : null}

            <View
              style={[
                styles.iconWrap,
                {
                  shadowColor: r.color,
                  shadowOpacity: 0.85,
                  shadowRadius: 14,
                  shadowOffset: { width: 0, height: 0 },
                },
              ]}
            >
              <Text style={styles.icon}>{payload.icon}</Text>
            </View>

            <View style={styles.col}>
              <Text
                numberOfLines={1}
                style={[styles.eyebrow, { color: r.color }]}
              >
                ★ Succès débloqué · {r.label}
              </Text>
              <Text numberOfLines={1} style={styles.label}>
                {payload.label}
              </Text>
              <Text numberOfLines={1} style={styles.description}>
                {payload.description}
              </Text>
            </View>
          </View>
        </LinearGradient>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  root: {
    position: 'absolute',
    left: 0,
    right: 0,
    paddingHorizontal: 16,
    zIndex: 50,
  },
  frame: {
    padding: 2,
    borderRadius: 14,
  },
  body: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    borderRadius: 12,
    overflow: 'hidden',
  },
  iconWrap: {
    width: 56,
    height: 56,
    flexShrink: 0,
    alignItems: 'center',
    justifyContent: 'center',
  },
  icon: {
    fontSize: 38,
  },
  col: {
    flex: 1,
    minWidth: 0,
  },
  eyebrow: {
    fontSize: 9,
    fontWeight: '900',
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    marginBottom: 2,
  },
  label: {
    fontSize: 16,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: -0.3,
  },
  description: {
    fontSize: 11,
    color: 'rgba(255,255,255,0.7)',
    marginTop: 2,
  },
  holoWrap: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: '-100%',
    width: '100%',
  },
});

export default AchievementUnlockToast;
