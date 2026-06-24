/**
 * Design system CheckBurst — radial particle burst on Liste check.
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste Courses.png`
 *                    (small burst when ticking off an item).
 * Reference JSX    : `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 6-39 (CSS
 *                    keyframe `listBurst`, 8 particles with `--bx/--by` CSS
 *                    vars).
 *
 * Spec
 * ----
 *  - 8 particles arranged in a radial burst (45° apart).
 *  - Each particle is a 4×4 dot with a soft glow shadow in the burst color
 *    (default gold).
 *  - Per-particle 12ms cascade stagger (matches the JSX `p.delay = i * 12`).
 *  - Animation : opacity 1 → 0, scale 1 → 0, translate from origin to
 *    `(cos·22, sin·22)` over 550ms with bezier `(0.2, 0.7, 0.4, 1)` (matches
 *    the JSX `cubic-bezier(0.2,0.7,0.4,1)`).
 *  - Triggered by flipping `play=true`. The component re-arms whenever
 *    `play` transitions from false → true. To replay while `play` stays
 *    true, the consumer should remount via a `key`.
 *
 * The component renders nothing when `play=false` (and the previous burst
 * has finished) so it never bloats the layer tree.
 */

import React, { useEffect, useState } from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withTiming,
} from 'react-native-reanimated';

const PARTICLE_COUNT = 8;
const RADIUS = 22; // px from origin
const DURATION = 550;
const STAGGER = 12;
const BURST_BEZIER = [0.2, 0.7, 0.4, 1] as const;

export type CheckBurstProps = {
  play: boolean;
  /** Origin in the parent's coordinate system, in px. */
  originX?: number;
  originY?: number;
  color?: string;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function CheckBurst({
  play,
  originX = 0,
  originY = 0,
  color = '#FFB800',
  testID,
  style,
}: CheckBurstProps) {
  const [active, setActive] = useState(false);

  // Re-arm on rising edge (play: false → true).
  useEffect(() => {
    if (play) {
      setActive(true);
      const t = setTimeout(
        () => setActive(false),
        DURATION + PARTICLE_COUNT * STAGGER + 60,
      );
      return () => clearTimeout(t);
    }
    return undefined;
  }, [play]);

  if (!active) return null;

  return (
    <View
      pointerEvents="none"
      testID={testID}
      style={[
        styles.host,
        { left: originX, top: originY },
        style,
      ]}
    >
      {Array.from({ length: PARTICLE_COUNT }).map((_, i) => (
        <Particle
          key={`p-${i}`}
          index={i}
          color={color}
          testID={testID ? `${testID}-p${i}` : undefined}
        />
      ))}
    </View>
  );
}

type ParticleProps = {
  index: number;
  color: string;
  testID?: string;
};

function Particle({ index, color, testID }: ParticleProps) {
  const angle = (index / PARTICLE_COUNT) * Math.PI * 2;
  const targetX = Math.cos(angle) * RADIUS;
  const targetY = Math.sin(angle) * RADIUS;
  const delay = index * STAGGER;

  const tx = useSharedValue(0);
  const ty = useSharedValue(0);
  const scale = useSharedValue(1);
  const opacity = useSharedValue(1);

  useEffect(() => {
    const easing = Easing.bezier(...BURST_BEZIER);
    tx.value = withDelay(
      delay,
      withTiming(targetX, { duration: DURATION, easing }),
    );
    ty.value = withDelay(
      delay,
      withTiming(targetY, { duration: DURATION, easing }),
    );
    scale.value = withDelay(
      delay,
      withTiming(0, { duration: DURATION, easing }),
    );
    opacity.value = withDelay(
      delay,
      withTiming(0, { duration: DURATION, easing }),
    );
  }, [delay, targetX, targetY, tx, ty, scale, opacity]);

  const animated = useAnimatedStyle(() => ({
    transform: [
      { translateX: tx.value },
      { translateY: ty.value },
      { scale: scale.value },
    ],
    opacity: opacity.value,
  }));

  return (
    <Animated.View
      testID={testID}
      style={[
        styles.particle,
        {
          backgroundColor: color,
          shadowColor: color,
        },
        animated,
      ]}
    />
  );
}

const styles = StyleSheet.create({
  host: {
    position: 'absolute',
    width: 0,
    height: 0,
  },
  particle: {
    position: 'absolute',
    left: -2, // center the 4×4 dot on origin
    top: -2,
    width: 4,
    height: 4,
    borderRadius: 2,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 8,
    elevation: 4,
  },
});

export default CheckBurst;
