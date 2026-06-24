// ratis_client/components/achievements/bespoke-animations/konami.tsx
//
// Achievements V1 — bespoke cinematic for `sec_konami` (Konami code easter
// egg).
//
// V1 polished placeholder : retro-pixel aesthetic. The 10-step sequence
// (↑↑↓↓←→←→BA) is shown as a row of pixel arrows that flash rainbow then
// settle, with a "30 LIVES" CRT-style message underneath. Reanimated cleanup
// mandatory.
//
// V1.1+ : add a CRT scanline overlay + pixel-font (custom font already in
// the design system queue).

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

const KONAMI_SEQUENCE: readonly string[] = [
  '↑',
  '↑',
  '↓',
  '↓',
  '←',
  '→',
  '←',
  '→',
  'B',
  'A',
];

export function KonamiBespoke({
  payload,
  onDismiss,
  testID,
}: BespokeUnlockProps) {
  const insets = useSafeAreaInsets();
  const r = RARITIES.diamond;

  const titleOpacity = useSharedValue(0);
  const sequenceShift = useSharedValue(0);

  useEffect(() => {
    titleOpacity.value = withTiming(1, { duration: 700 });
    sequenceShift.value = withRepeat(
      withSequence(
        withTiming(1, { duration: 1200, easing: Easing.inOut(Easing.ease) }),
        withTiming(0, { duration: 1200, easing: Easing.inOut(Easing.ease) }),
      ),
      -1,
      false,
    );
    return () => {
      cancelAnimation(titleOpacity);
      cancelAnimation(sequenceShift);
    };
  }, [titleOpacity, sequenceShift]);

  const titleAnimated = useAnimatedStyle(() => ({
    opacity: titleOpacity.value,
  }));
  const sequenceAnimated = useAnimatedStyle(() => ({
    opacity: 0.65 + sequenceShift.value * 0.35,
  }));

  return (
    <View
      testID={testID ?? 'bespoke-konami'}
      style={[
        styles.root,
        { paddingTop: insets.top, paddingBottom: insets.bottom },
      ]}
    >
      <Pressable
        testID="bespoke-konami-backdrop"
        onPress={onDismiss}
        style={StyleSheet.absoluteFill}
        accessibilityRole="button"
        accessibilityLabel="Fermer"
      >
        <LinearGradient
          colors={['#180a3b', '#0a0d14', '#000']}
          start={{ x: 0.5, y: 0 }}
          end={{ x: 0.5, y: 1 }}
          style={StyleSheet.absoluteFill}
        />
      </Pressable>

      <View style={styles.content} pointerEvents="box-none">
        <Animated.Text
          style={[styles.eyebrow, { color: r.color }, titleAnimated]}
        >
          ★ Diamant secret débloqué
        </Animated.Text>

        <Animated.View
          style={[styles.sequenceRow, sequenceAnimated]}
          pointerEvents="none"
        >
          {KONAMI_SEQUENCE.map((char, i) => (
            <View key={`${char}-${i}`} style={styles.cell}>
              <Text style={styles.cellChar}>{char}</Text>
            </View>
          ))}
        </Animated.View>

        <Text style={styles.crt}>+{payload.cab_granted} CAB</Text>
        <Text style={styles.label}>{payload.label}</Text>
        <Text style={styles.message}>30 LIVES.</Text>

        <Pressable
          testID="bespoke-konami-close"
          accessibilityRole="button"
          accessibilityLabel="Fermer"
          onPress={onDismiss}
          style={[styles.closeBtn, { borderColor: r.color }]}
        >
          <Text style={styles.closeBtnText}>Continuer</Text>
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
    paddingHorizontal: 18,
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
    marginBottom: 4,
  },
  sequenceRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 6,
    marginVertical: 12,
  },
  cell: {
    width: 30,
    height: 30,
    borderWidth: 1,
    borderColor: '#A78BFA',
    backgroundColor: 'rgba(167,139,250,0.10)',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 4,
  },
  cellChar: {
    color: '#E0E7FF',
    fontSize: 16,
    fontWeight: '900',
  },
  crt: {
    fontFamily: undefined,
    color: '#A5F3FC',
    fontSize: 22,
    fontWeight: '900',
    letterSpacing: 1,
    marginTop: 4,
  },
  label: {
    fontSize: 18,
    fontWeight: '900',
    color: '#fff',
    textAlign: 'center',
    marginTop: 4,
  },
  message: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.6)',
    letterSpacing: 4,
    fontWeight: '900',
    marginTop: 6,
  },
  closeBtn: {
    marginTop: 28,
    paddingHorizontal: 24,
    paddingVertical: 11,
    borderRadius: 4,
    borderWidth: 1.5,
    backgroundColor: 'rgba(255,255,255,0.04)',
  },
  closeBtnText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '900',
    letterSpacing: 1,
    textTransform: 'uppercase',
  },
});
