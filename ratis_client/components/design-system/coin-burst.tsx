/**
 * Design system CoinBurst — particle celebration.
 *
 * Triggered on `visible={true}` : renders N gold coins (`count`, default 8)
 * that explode radially from `origin` with a parabolic trajectory, fading +
 * spinning + scaling out. Stagger 50ms across coins for a cascade feel.
 *
 * The animation runs on the Reanimated 4 UI thread (`useSharedValue` +
 * `withSequence` / `withTiming` / `withDelay`). `onComplete` fires once when
 * the last coin has finished — wired via a JS-side `setTimeout` so it's
 * deterministic in tests (no need to assert on worklet completion order).
 *
 * The coin visual is a small `View` with a gold gradient — kept minimal here
 * to avoid pulling SVG just for a 16px circle. Higher-fidelity coin assets
 * land in PR4 (`<JarCoinCascade />`) where we have more screen real estate.
 *
 * Spec : `ARCH_design_system.md` § Composants — CoinBurst (sequence : scale
 * bounce 0.8→1.2→1, rotation 0→360, particles radial explosion).
 */

import React, { useEffect, useRef } from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withSequence,
  withTiming,
} from 'react-native-reanimated';

import { Colors } from '@/constants/theme';
import { Durations } from '@/constants/animations';

export type CoinBurstProps = {
  visible: boolean;
  count?: number;
  origin?: { x: number; y: number };
  onComplete?: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

const COIN_DURATION = Durations.loop.jarCoinFall / 3; // ~1167ms — close to ARCH 1200ms target.
const STAGGER = 50;

type CoinProps = {
  index: number;
  total: number;
  testID: string;
};

function Coin({ index, total, testID }: CoinProps) {
  const tx = useSharedValue(0);
  const ty = useSharedValue(0);
  const rot = useSharedValue(0);
  const scale = useSharedValue(0);
  const opacity = useSharedValue(0);

  useEffect(() => {
    // Random sideways drift in [-60, +60] px ; up-then-down parabolic ; full
    // rotation, with slight randomized direction. We sign the rotation by
    // index parity to avoid every coin spinning the same way.
    const drift = (Math.random() - 0.5) * 120;
    const direction = index % 2 === 0 ? 1 : -1;
    const delay = index * STAGGER;

    tx.value = withDelay(
      delay,
      withTiming(drift, {
        duration: COIN_DURATION,
        easing: Easing.out(Easing.cubic),
      }),
    );
    ty.value = withDelay(
      delay,
      withSequence(
        withTiming(-50, {
          duration: COIN_DURATION * 0.4,
          easing: Easing.out(Easing.cubic),
        }),
        withTiming(80, {
          duration: COIN_DURATION * 0.6,
          easing: Easing.in(Easing.cubic),
        }),
      ),
    );
    rot.value = withDelay(
      delay,
      withTiming(direction * 360, {
        duration: COIN_DURATION,
        easing: Easing.linear,
      }),
    );
    scale.value = withDelay(
      delay,
      withSequence(
        withTiming(1, { duration: COIN_DURATION * 0.2 }),
        withTiming(1, { duration: COIN_DURATION * 0.5 }),
        withTiming(0.6, { duration: COIN_DURATION * 0.3 }),
      ),
    );
    opacity.value = withDelay(
      delay,
      withSequence(
        withTiming(1, { duration: COIN_DURATION * 0.2 }),
        withTiming(1, { duration: COIN_DURATION * 0.5 }),
        withTiming(0, { duration: COIN_DURATION * 0.3 }),
      ),
    );
  }, [index, opacity, rot, scale, total, tx, ty]);

  const animated = useAnimatedStyle(() => ({
    transform: [
      { translateX: tx.value },
      { translateY: ty.value },
      { rotate: `${rot.value}deg` },
      { scale: scale.value },
    ],
    opacity: opacity.value,
  }));

  return (
    <Animated.View testID={testID} style={[styles.coin, animated]}>
      <LinearGradient
        colors={[Colors.goldHi, Colors.gold]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.coinGradient}
      />
    </Animated.View>
  );
}

export function CoinBurst({
  visible,
  count = 8,
  origin,
  onComplete,
  testID,
  style,
}: CoinBurstProps) {
  const completedRef = useRef(false);

  useEffect(() => {
    if (!visible) {
      completedRef.current = false;
      return;
    }
    if (!onComplete) return;
    completedRef.current = false;
    const totalDuration = COIN_DURATION + count * STAGGER;
    const t = setTimeout(() => {
      if (completedRef.current) return;
      completedRef.current = true;
      onComplete();
    }, totalDuration);
    return () => clearTimeout(t);
  }, [visible, count, onComplete]);

  if (!visible) return null;

  const left = origin?.x ?? 0;
  const top = origin?.y ?? 0;

  return (
    <View
      pointerEvents="none"
      testID={testID}
      style={[
        styles.wrapper,
        origin ? { left, top } : { left: 0, top: 0 },
        style,
      ]}
    >
      {Array.from({ length: count }).map((_, i) => (
        <Coin
          key={`coin-${i}`}
          index={i}
          total={count}
          testID={testID ? `${testID}-coin-${i}` : `coin-burst-coin-${i}`}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    position: 'absolute',
    width: 0,
    height: 0,
    alignItems: 'center',
    justifyContent: 'center',
  },
  coin: {
    position: 'absolute',
    width: 16,
    height: 16,
    borderRadius: 8,
    overflow: 'hidden',
    // Soft drop shadow for the relief feel.
    shadowColor: Colors.goldSh,
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 2,
  },
  coinGradient: {
    flex: 1,
  },
});

export default CoinBurst;
