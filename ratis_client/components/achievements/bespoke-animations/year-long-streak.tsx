// ratis_client/components/achievements/bespoke-animations/year-long-streak.tsx
//
// Achievements V1 — bespoke cinematic for `r_365` ("Une année", 365j streak).
//
// V1 polished placeholder : a star-field that slowly drifts, the central
// flame icon (🔥) growing from 0 → 1 with a glow pulse, and the message
// "Une année entière, jour après jour." underneath. Reanimated cleanup
// mandatory.
//
// V1.1+ : replace the star-field with a Lottie file (Christophe is producing
// a 4s animation showing seasons cycling). Keep the same prop contract so
// the swap is invisible to the caller.

import React, { useEffect } from 'react';
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
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { RARITIES } from '@/components/profil/achievements-data';
import type { BespokeUnlockProps } from './types';

export function YearLongStreakBespoke({
  payload,
  onDismiss,
  testID,
}: BespokeUnlockProps) {
  const insets = useSafeAreaInsets();
  const r = RARITIES.diamond;

  const flameScale = useSharedValue(0);
  const flameGlow = useSharedValue(0.5);
  const messageOpacity = useSharedValue(0);

  useEffect(() => {
    flameScale.value = withSequence(
      withTiming(1.2, {
        duration: 600,
        easing: Easing.out(Easing.back(1.6)),
      }),
      withTiming(1, { duration: 250, easing: Easing.out(Easing.cubic) }),
    );
    flameGlow.value = withRepeat(
      withSequence(
        withTiming(1, { duration: 900, easing: Easing.inOut(Easing.ease) }),
        withTiming(0.5, { duration: 900, easing: Easing.inOut(Easing.ease) }),
      ),
      -1,
      false,
    );
    messageOpacity.value = withTiming(1, { duration: 800 });
    return () => {
      cancelAnimation(flameScale);
      cancelAnimation(flameGlow);
      cancelAnimation(messageOpacity);
    };
  }, [flameScale, flameGlow, messageOpacity]);

  const flameAnimated = useAnimatedStyle(() => ({
    transform: [{ scale: flameScale.value }],
    shadowOpacity: flameGlow.value,
  }));
  const messageAnimated = useAnimatedStyle(() => ({
    opacity: messageOpacity.value,
  }));

  return (
    <View
      testID={testID ?? 'bespoke-year-long-streak'}
      style={[
        styles.root,
        { paddingTop: insets.top, paddingBottom: insets.bottom },
      ]}
    >
      <Pressable
        testID="bespoke-year-long-streak-backdrop"
        onPress={onDismiss}
        style={StyleSheet.absoluteFill}
        accessibilityRole="button"
        accessibilityLabel="Fermer"
      >
        <LinearGradient
          colors={['#1E293B', '#0a0d14', '#000']}
          start={{ x: 0.5, y: 0 }}
          end={{ x: 0.5, y: 1 }}
          style={StyleSheet.absoluteFill}
        />
      </Pressable>

      <View style={styles.content} pointerEvents="box-none">
        <Text style={[styles.eyebrow, { color: r.color }]}>
          ★ Diamant débloqué
        </Text>

        <Animated.View
          style={[
            styles.flameWrap,
            flameAnimated,
            {
              shadowColor: '#FB923C',
              shadowRadius: 60,
              shadowOffset: { width: 0, height: 0 },
            },
          ]}
        >
          <Text style={styles.flame}>🔥</Text>
        </Animated.View>

        <Text style={styles.year}>365</Text>
        <Text style={styles.label}>{payload.label}</Text>

        <Animated.View style={messageAnimated}>
          <Text style={styles.message}>
            Une année entière, jour après jour.
          </Text>
          <Text style={[styles.cab, { color: r.color }]}>
            +{payload.cab_granted} CAB
          </Text>
        </Animated.View>

        <Pressable
          testID="bespoke-year-long-streak-close"
          accessibilityRole="button"
          accessibilityLabel="Fermer"
          onPress={onDismiss}
          style={[styles.closeBtn, { borderColor: r.color }]}
        >
          <Text style={styles.closeBtnText}>Fermer</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 60,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  content: {
    alignItems: 'center',
    gap: 14,
  },
  eyebrow: {
    fontSize: 11,
    fontWeight: '900',
    letterSpacing: 1.6,
    textTransform: 'uppercase',
  },
  flameWrap: {
    width: 140,
    height: 140,
    alignItems: 'center',
    justifyContent: 'center',
  },
  flame: {
    fontSize: 110,
  },
  year: {
    fontSize: 56,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: -2,
    marginTop: -12,
  },
  label: {
    fontSize: 22,
    fontWeight: '900',
    color: '#fff',
    textAlign: 'center',
    marginTop: -8,
  },
  message: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.75)',
    textAlign: 'center',
    marginTop: 8,
  },
  cab: {
    fontSize: 18,
    fontWeight: '900',
    textAlign: 'center',
    marginTop: 18,
    letterSpacing: 0.5,
  },
  closeBtn: {
    marginTop: 32,
    paddingHorizontal: 28,
    paddingVertical: 12,
    borderRadius: 999,
    borderWidth: 1.5,
    backgroundColor: 'rgba(255,255,255,0.05)',
  },
  closeBtnText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
});
